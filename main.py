import asyncio

import discord
from discord.ext import commands
import networkx as nx
import json
import math
import io
import re
import aiohttp
from motor.motor_asyncio import AsyncIOMotorClient
from PIL import Image, ImageDraw

# For .env config
import os
from dotenv import load_dotenv

# ==========================================
# CONFIG
# ==========================================
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
MONGO_URI = os.getenv("MONGO_URI")
LLM_API_KEY = os.getenv("GEMINI_API_KEY")
LLM_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.1-flash-lite-preview:generateContent"

MAP_JSON_PATH = "erlc_map.json"
MAP_IMAGE_PATH = "fall_postals.jpg"

if not TOKEN or not MONGO_URI or not LLM_API_KEY:
    raise RuntimeError("Missing required environment variables. Check your .env file.")

# ==========================================
# LOGIC ENGINE (GRAPH MATH)
# ==========================================
class ERLCGraph:
    def __init__(self, json_path):
        self.graph = nx.DiGraph()
        self.nodes_data = {}
        self.postal_nodes = {}
        self.road_graph = {}
        self.road_geometry = {}
        self.config = {}
        self._load_data(json_path)

    def _load_data(self, json_path):
        """Loads JSON map data into a directed weighted graph."""
        with open(json_path, 'r') as f:
            data = json.load(f)

        # store config (v3 system config)
        self.config = data.get("system_config", {})

        # Load Nodes
        for node_id, info in data["nodes"].items():
            # Skip malformed or non-node entries (e.g. comment keys or placeholders)
            if not isinstance(info, dict):
                continue

            # Ensure required coordinate fields exist
            if "x" not in info or "y" not in info:
                continue

            self.nodes_data[node_id] = info

            self.graph.add_node(
                node_id,
                x=info["x"],
                y=info["y"],
                label=info.get("label"),
                poi=info.get("poi"),
                robable=info.get("robable"),
                type=info.get("type")
            )

        # ================================
        # COMPATIBLE EDGE LOADER (v2)
        # Supports:
        # - missing road fields
        # - missing metadata.postals
        # - bidirectional or implicit edges
        # - simplified JSON structures
        # ================================

        for edge in data.get("edges", []):
            if not isinstance(edge, dict):
                continue

            s = edge.get("source")
            t = edge.get("target")

            if not s or not t:
                continue

            # Ensure nodes exist
            if s not in data.get("nodes", {}) or t not in data.get("nodes", {}):
                continue

            n1 = data["nodes"][s]
            n2 = data["nodes"][t]

            # Safe coordinate extraction
            sx, sy = n1.get("x", 0), n1.get("y", 0)
            tx, ty = n2.get("x", 0), n2.get("y", 0)

            base_cost = math.hypot(tx - sx, ty - sy)

            edge_type = edge.get("type", "local")

            # ROAD COMPATIBILITY FIX:
            # If road missing, generate stable fallback name
            road = edge.get("road")
            if not road:
                road = f"{s}__{t}"

            # Ensure road graph exists
            if road not in self.road_graph:
                self.road_graph[road] = {}

            # Build road adjacency (undirected logical structure)
            self.road_graph[road].setdefault(s, set()).add(t)
            self.road_graph[road].setdefault(t, set()).add(s)

            # Metadata compatibility
            metadata = edge.get("metadata") or {}
            postals = metadata.get("postals") or []

            # Determine directionality (default: bidirectional TRUE)
            bidirectional = edge.get("bidirectional", True)

            def add_edge(u, v):
                self.graph.add_edge(
                    u,
                    v,
                    road=road,
                    type=edge_type,
                    is_one_way=(not bidirectional),
                    postals=postals,
                    base_cost=base_cost,
                    weight=base_cost
                )

                # Attach geometry for rendering
                self.graph[u][v]["geometry"] = [
                    (self.nodes_data[u]["x"], self.nodes_data[u]["y"]),
                    (self.nodes_data[v]["x"], self.nodes_data[v]["y"])
                ]

            # Always add forward edge
            add_edge(s, t)

            # Add reverse edge if bidirectional
            if bidirectional:
                add_edge(t, s)

        self.build_road_geometry()
    def build_road_geometry(self):
        """Build ordered polylines per road using DFS traversal."""

        self.road_geometry = {}

        for road, adjacency in self.road_graph.items():
            visited = set()
            segments = []

            nodes = list(adjacency.keys())
            if not nodes:
                continue

            start = nodes[0]
            stack = [(start, None)]
 
            while stack:
                node, parent = stack.pop()

                if node in visited:
                    continue

                visited.add(node)

                if parent is not None:
                    n1 = self.nodes_data.get(parent)
                    n2 = self.nodes_data.get(node)

                    if n1 and n2:
                        segments.append((n1["x"], n1["y"]))
                        segments.append((n2["x"], n2["y"]))

                for neighbor in adjacency.get(node, []):
                    if neighbor not in visited:
                        stack.append((neighbor, node))

            cleaned = []
            seen = set()

            for p in segments:
                if p not in seen:
                    seen.add(p)
                    cleaned.append(p)

            self.road_geometry[road] = cleaned
    def resolve_poi_to_node(self, poi_name: str):
        if not poi_name:
            return None

        poi_name = poi_name.lower().strip()

        best_match = None

        for node_id, data in self.nodes_data.items():
            poi = str(data.get("poi", "")).lower().strip()

            if poi == poi_name:
                return node_id

            # fallback fuzzy containment
            if poi_name in poi or poi in poi_name:
                best_match = node_id

        return best_match

    def resolve_target(self, raw: str):
        """Universal resolver for nodes, postals, and POIs."""
        if not raw:
            return None

        # already valid node
        if raw in self.graph:
            return raw

        # postal format (postal_XXX)
        if isinstance(raw, str) and raw.startswith("postal_"):
            if raw in self.graph:
                return raw
            return self.postal_nodes.get(raw.replace("postal_", "")) or raw

        # numeric postal ("602")
        if isinstance(raw, str) and raw.isdigit():
            return self.postal_nodes.get(raw) or f"postal_{raw}"

        # POI resolution
        poi_resolved = self.resolve_poi_to_node(raw)
        if poi_resolved:
            return poi_resolved

        # fallback: try direct label match
        for node_id, data in self.nodes_data.items():
            if str(data.get("label", "")).lower() == str(raw).lower():
                return node_id

        return None

    def compute_edge_cost(self, base_cost, edge_type, vehicle, unwl_units):
        vehicle = (vehicle or "").lower()

        multiplier_map = self.config.get("multiplier_map", {})

        cost = base_cost * multiplier_map.get(edge_type, 1.0)

        # vehicle modifiers
        if vehicle == "supercar":
            if edge_type == "highway":
                cost *= 0.8
            if edge_type == "industrial":
                cost *= 1.5

        elif vehicle in ["jeep", "truck"]:
            if edge_type == "industrial":
                cost *= 0.85
            if edge_type == "highway":
                cost *= 1.1

        # unWL behavioural factor
        if unwl_units > 0:
            panic = min(unwl_units * 0.8, 0.5)
            if edge_type == "highway":
                cost *= (1.0 - panic)
            if edge_type in ["local", "industrial"]:
                cost *= (1.0 + panic)

        return cost

    def apply_weights(self, vehicle: str, unwl_units: int):
        """Returns a graph with dynamic edge weights applied."""
        G_mod = self.graph.copy()

        for u, v, data in G_mod.edges(data=True):
            # Ignore postal traversal edges for routing (lookup only layer)
            if data.get("type") == "postal":
                G_mod[u][v]["weight"] = 999999
                continue

            base_cost = data.get("base_cost")

            if base_cost is None:
                # fallback: derive from existing weight or set neutral cost
                base_cost = data.get("weight")

            if base_cost is None:
                base_cost = 1.0

            cost = self.compute_edge_cost(
                base_cost,
                data.get("type", "local"),
                vehicle,
                unwl_units
            )
            G_mod[u][v]["weight"] = cost

        # enforce postal edges as non-routing shortcuts
        for u, v, data in G_mod.edges(data=True):
            if data.get("type") == "postal":
                G_mod[u][v]["weight"] = 999999

        return G_mod

    def get_top_destinations(self, start_postal: str, G_mod: nx.Graph, top_n: int = 7):
        """Runs Dijkstra's to find closest POIs or robable locations."""
        if start_postal not in G_mod:
            start_postal = self.postal_nodes.get(start_postal) or f"postal_{start_postal}"
            if start_postal not in G_mod:
                return [], []

        # ensure routing does not end on postal nodes
        def is_valid_node(n):
            return not str(n).startswith("postal_")

        lengths, paths = nx.single_source_dijkstra(G_mod, start_postal, weight='weight')

        lengths = {k: v for k, v in lengths.items() if is_valid_node(k)}
        paths = {k: p for k, p in paths.items() if is_valid_node(k)}

        destinations = []

        for node, distance in lengths.items():
            if node == start_postal:
                continue

            node_data = G_mod.nodes.get(node) or self.nodes_data.get(node)

            if not node_data:
                continue

            is_interesting = node_data.get('robable') is True

            # loosen filter so system never returns empty results
            if node_data.get("robable") is not True:
                continue

            destinations.append({
                "postal": node,
                "poi": node_data.get('poi', 'Unknown POI'),
                "robable": node_data.get('robable', False),
                "distance_score": round(distance, 2),
                "path": paths.get(node, [])
            })

        if not destinations:
            # fallback: return closest raw nodes
            for node, distance in list(lengths.items())[:10]:
                if node == start_postal:
                    continue
                node_data = G_mod.nodes.get(node) or {}
                destinations.append({
                    "postal": node,
                    "poi": node_data.get('poi', 'Unknown POI'),
                    "robable": node_data.get('robable', False),
                    "distance_score": round(distance, 2),
                    "path": paths.get(node, [])
                })

        destinations.sort(key=lambda x: x['distance_score'])
        return destinations[:top_n]

class CrimeHeatmap:
    """Maintains per-postal crime weighting derived from MongoDB logs."""
    def __init__(self):
        self.weights = {}

    def build_from_logs(self, logs):
        """Build simple frequency-based heatmap."""
        self.weights.clear()
        for log in logs:
            crimes = log.get("crimes", "").lower()
            # crude extraction: treat each crime token as influence
            for token in crimes.split():
                self.weights[token] = self.weights.get(token, 0) + 1

    def score_node(self, node_data):
        """Convert POI/robbery relevance into scalar bias."""
        base = 1.0
        poi = str(node_data.get("poi", "")).lower()
        if "bank" in poi:
            base += 0.6 * self.weights.get("robbery", 0)
        if node_data.get("robable"):
            base += 0.3 * sum(self.weights.values())
        return base

# ==========================================
# DISCORD BOT & MONGODB SETUP
# ==========================================
class PredictiveBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=discord.Intents.default())
        self.erlc_graph = ERLCGraph(MAP_JSON_PATH)
        self.mongo_client = AsyncIOMotorClient(MONGO_URI)
        self.db = self.mongo_client["erlc_database"]
        self.suspect_logs = self.db["suspect_logs"]
        self.crime_heatmap = CrimeHeatmap()

    async def setup_hook(self):
        await self.tree.sync()
        print("Bot is ready and slash commands synced")

bot = PredictiveBot()

# ==========================================
# HELPER FUNCTIONS
# ==========================================
def draw_map_path(paths_to_draw: list) -> io.BytesIO:
    """Draws predicted paths on the ER:LC map image."""
    
    try:
        img = Image.open(MAP_IMAGE_PATH).convert("RGBA")
    except Exception as e:
        print(f"Failed to load map image: {e}")
        raise RuntimeError(f"Map image failed to load: {e}")

    draw = ImageDraw.Draw(img)
    print("[MAP DEBUG] Drawing map with", len(paths_to_draw), "paths")
    # Draw primary path (Red) and secondary paths (Orange, slightly transparent)
    colors = [
        (255, 0, 0, 255),      # solid red
        (255, 165, 0, 180),    # semi-transparent orange
        (255, 165, 0, 180)
    ]
    
    for idx, path_nodes in enumerate(paths_to_draw[:3]):  # Draw top 3 to avoid clutter
        print(f"[MAP DEBUG] Drawing path {idx}:", path_nodes)
        color = colors[0] if idx == 0 else colors[1]
        line_width = 8 if idx == 0 else 4

        # Draw edge-by-edge using edge geometry if available
        for i in range(len(path_nodes) - 1):
            a = path_nodes[i]
            b = path_nodes[i + 1]
            print(f"[MAP DEBUG] Segment: {a} -> {b}")
            # fetch edge geometry if available (preferred)
            edge_data = bot.erlc_graph.graph.get_edge_data(a, b)

            if edge_data:
                geometry = edge_data.get("geometry")

                if geometry and len(geometry) >= 2:
                    draw.line(
                        geometry,
                        fill=color,
                        width=line_width
                    )
                    continue

            # fallback: straight line between nodes
            node_a = bot.erlc_graph.graph.nodes.get(str(a))
            node_b = bot.erlc_graph.graph.nodes.get(str(b))

            if not node_a or not node_b:
                print(f"[MAP WARN] Missing node data: {a} -> {b}")
                continue

            if node_a.get("x") is None or node_a.get("y") is None:
                continue
            if node_b.get("x") is None or node_b.get("y") is None:
                continue

            draw.line(
                [(node_a["x"], node_a["y"]), (node_b["x"], node_b["y"])],
                fill=color,
                width=line_width
            )
    # Save to buffer
    buffer = io.BytesIO()

    # Preserve transparency for layered paths
    img.save(buffer, format="PNG")
    buffer.seek(0)
    return buffer

async def call_llm(prompt: str) -> dict:
    """Calls Gemini API (Google Generative Language API) and forces JSON output."""

    system_instruction = """
You are an expert predictive policing AI for ER:LC.
GAME RULES (CRITICAL - MUST FOLLOW):
- Output ONLY JSON in required schema.
- Only nodes with robable=true are valid targets.
- All other nodes are traversal only; never predict them.
- Do NOT simulate real-world behaviour (medical, retreat, fear, policing delay, negotiation, etc).
- Suspects always continue criminal activity unless arrested/disconnected.
- Ignore injury, damage, and “safe zones”.
- No ethical reasoning, only POI selection.
- Output = ranking of criminal objectives, not travel simulation.
- Never mention chaos factor/lack of unWL units online in your analysis. 
- All targets MUST be returned as node IDs (e.g. N-204), never POI names.
Return ONLY JSON in this exact format:
{
  "prediction": {
    "primary_target": "string",
    "secondary_target": "string",
    "threat_level": "LOW|MEDIUM|HIGH|CRITICAL",
    "behavioral_profile": "string",
    "tactical_recommendation": "string",
    "probability_score": float,
    "reasoning": "string"
  }
}
You are NOT, and I repeat, NOT, allowed to modify the names of the schema fields. If you modify itm, your predictions will fall on deaf ears
"""

    # Gemini API uses contents/parts format (NOT OpenAI messages format)
    payload = {
        "contents": [
            {
                "role": "user",
                "parts": [
                    {
                        "text": system_instruction + "\n\n" + prompt
                    }
                ]
            }
        ],
        "generationConfig": {
            "temperature": 0.2
        }
    }

    url = f"{LLM_API_URL}?key={LLM_API_KEY}"

    headers = {
        "Content-Type": "application/json"
    }

    timeout = aiohttp.ClientTimeout(total=10)

    for attempt in range(4):
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(url, headers=headers, json=payload) as resp:
                    
                    if resp.status == 200:
                        data = await resp.json()
                        print("\n===== RAW LLM RESPONSE JSON =====")
                        print(data)
                        print("=================================\n")

                        text = data["candidates"][0]["content"]["parts"][0]["text"]

                        print("\n===== RAW LLM TEXT OUTPUT =====")
                        print(text)
                        print("================================\n")

                        text = re.sub(r"```json|```", "", text).strip()

                        print("\n===== CLEANED LLM TEXT =====")
                        print(text)
                        print("================================\n")

                        parsed = json.loads(text)

                        print("\n===== PARSED LLM JSON =====")
                        print(parsed)
                        print("================================\n")

                        return parsed

                    elif resp.status == 503:
                        wait_time = 2 ** attempt
                        print(f"503 overload. Retrying in {wait_time}s...")
                        await asyncio.sleep(wait_time)
                        continue

                    else:
                        print(f"Gemini API Error: {resp.status} - {await resp.text()}")
                        return None

        except Exception as e:
            wait_time = 2 ** attempt
            print(f"Request failed ({e}). Retrying in {wait_time}s...")
            await asyncio.sleep(wait_time)

    return None

# ==========================================
# COMMANDS
# ==========================================

@bot.tree.command(name="metro_suspect_log", description="Log a suspect's crime history for future predictive training.")
async def metro_suspect_log(
    interaction: discord.Interaction,
    suspect_name: str,
    crimes_committed: str,
    location: str,
    entry_type: str = "crime"
):
    await interaction.response.defer()

    # ==========================================
    # 1. Build valid node reference list
    # ==========================================
    valid_nodes = "\n".join([
        f"{postal}: {info.get('poi', 'Unknown')}"
        for postal, info in bot.erlc_graph.nodes_data.items()
    ])

    # ==========================================
    # 2. Gemini / LLM structured extraction
    # ==========================================
    extraction_prompt = f"""
You are a strict JSON extractor.
Map the provided location description to the closest valid node in the graph.
You MUST only choose from the provided nodes.

VALID NODES:
{valid_nodes}

USER LOCATION INPUT:
{location}

Return ONLY JSON in this format:
{{
  "postal": "PXXX or closest match",
  "poi": "string",
  "confidence": 0.0
}}
"""

    location_data = await call_llm(extraction_prompt)

    # ensure safe parsing / fallback
    if not isinstance(location_data, dict):
        location_data = {}

    extracted_postal = location_data.get("postal")

    # validate against graph (hard constraint)
    if extracted_postal not in bot.erlc_graph.nodes_data:
        location_data = {
            "postal": None,
            "poi": None,
            "confidence": 0.0
        }
        extracted_postal = None

    # ==========================================
    # 3. LOG BUILDING
    # ==========================================
    log_entry = {
        "suspect_name": suspect_name.lower(),
        "crimes": crimes_committed,
        "location_raw": location,
        "postal": extracted_postal,
        "poi": location_data.get("poi"),
        "confidence": location_data.get("confidence", 0.0),
        "entry_type": entry_type.lower(),
        "timestamp": interaction.created_at.isoformat()
    }

    # ==========================================
    # 4. DATABASE
    # ==========================================
    try:
        await bot.suspect_logs.insert_one(log_entry)
        await interaction.followup.send(
            f"✅ Logged suspect **{suspect_name}** with structured location data.",
            ephemeral=True
        )
    except Exception:
        fallback_log = {
            "suspect_name": suspect_name.lower(),
            "crimes": crimes_committed,
            "location_raw": location,
            "postal": None,
            "poi": None,
            "confidence": 0.0,
            "timestamp": interaction.created_at.isoformat()
        }
        await bot.suspect_logs.insert_one(fallback_log)
        await interaction.followup.send(
            "⚠️ Logged with fallback due to database or parsing issue.",
            ephemeral=True
        )
@bot.tree.command(name="metro_promote", description="Issue a promotion to an officer.")
async def metro_promote(
    interaction: discord.Interaction,
    officer: discord.Member,
    previous_rank: str,
    new_rank: str,
    notes: str,
    signed: str
):
    embed = discord.Embed(
        title="🚨 ︱Metropolitan Promotion!",
        color=discord.Color.blue()
    )

    embed.add_field(name="Metro Operative", value=officer.mention, inline=False)
    embed.add_field(name="Old Rank", value=previous_rank, inline=False)
    embed.add_field(name="New Rank", value=new_rank, inline=False)
    embed.add_field(name="Notes", value=notes, inline=False)
    embed.add_field(name="Signed", value=signed, inline=False)

    embed.set_footer(text=f"Issued by {signed}")

    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="metro_infract", description="Issue an infraction to an officer.")
async def metro_infract(
    interaction: discord.Interaction,
    officer: discord.Member,
    punishment: str,
    reason: str,
    appealable: str,
    signed: str
):
    embed = discord.Embed(
        title="⚠️ Metro Infraction",
        color=discord.Color.red()
    )

    embed.add_field(name="Officer", value=officer.mention, inline=False)
    embed.add_field(name="\u200b", value="\u200b", inline=False)
    embed.add_field(name="Punishment", value=punishment, inline=False)
    embed.add_field(name="\u200b", value="\u200b", inline=False)
    embed.add_field(name="Reason", value=reason, inline=False)
    embed.add_field(name="\u200b", value="\u200b", inline=False)
    embed.add_field(name="Appealable", value=appealable, inline=False)
    embed.add_field(name="\u200b", value="\u200b", inline=False)
    embed.add_field(name="Signed", value=signed, inline=False)

    embed.set_footer(text=f"Issued by {interaction.user.display_name}")

    await interaction.response.send_message("Infraction Issued")
    await interaction.followup.send(embed=embed)

@bot.tree.command(name="metro_predict", description="Run a predictive policing algorithm on a suspect.")
async def metro_predict(
    interaction: discord.Interaction, 
    postal: str, 
    vehicle: str, 
    suspect_name: str,
    optional_tags: str = None, 
    unwl_units: int = 0,
    live_context: str = None
):
    await interaction.response.defer()
    
    if postal not in bot.erlc_graph.nodes_data and postal not in bot.erlc_graph.postal_nodes:
        await interaction.followup.send(f"❌ Error: Postal **{postal}** not found in database.", ephemeral=True)
        return

    # 1. Fetch suspect history + build heatmap
    history_text = "No prior history provided."
    crime_logs = []

    if suspect_name:
        try:
            cursor = bot.suspect_logs.find(
                {"suspect_name": suspect_name.lower()}
            ).sort("timestamp", -1).limit(20)

            crime_logs = await cursor.to_list(length=20)

        except Exception as e:
            print(f"[MONGO ERROR] Failed to fetch suspect logs: {e}")
            crime_logs = []

    history_text = "No prior history available."
    print(f"[DEBUG] Retrieved {len(crime_logs)} logs for suspect: {suspect_name}")

    if crime_logs:
        crime_texts = []
        sighting_texts = []

        for h in crime_logs:
            if h.get("entry_type") == "sighting":
                sighting_texts.append(h.get("location_raw", ""))
            else:
                crime_texts.append(h.get("crimes", ""))

        history_text = "; ".join(crime_texts + sighting_texts)

    # build global crime heatmap
    bot.crime_heatmap.build_from_logs(crime_logs)

    # 2. Graph Math Execution
    modified_graph = bot.erlc_graph.apply_weights(vehicle, unwl_units)

    resolved_postal = postal

    if postal in bot.erlc_graph.postal_nodes:
        resolved_postal = bot.erlc_graph.postal_nodes[postal]
    elif f"postal_{postal}" in bot.erlc_graph.graph:
        resolved_postal = f"postal_{postal}"
    if resolved_postal not in modified_graph:
        await interaction.followup.send(
            f"❌ Invalid start node after resolution: {resolved_postal}",
            ephemeral=True
        )
        return
    raw_dests = bot.erlc_graph.get_top_destinations(resolved_postal, modified_graph, top_n=15)

    if not raw_dests:
        await interaction.followup.send("❌ Error: Could not calculate routes.", ephemeral=True)
        
        return

    # apply behavioural scoring layer
    scored_dests = []
    for d in raw_dests:
        node_data = bot.erlc_graph.nodes_data.get(d["postal"])
        if not node_data:
            continue
        heat = bot.crime_heatmap.score_node(node_data)

        d["heat_score"] = heat
        d["final_score"] = d["distance_score"] / heat
        scored_dests.append(d)

    scored_dests.sort(key=lambda x: x["final_score"])

    top_dests = scored_dests[:7]

    # 3. The Psychological Layer (Prepare Prompt)

    dest_lines = []
    for d in top_dests:
        dest_lines.append(
            f"- {d.get('postal')} | POI: {d.get('poi')} | dist={d.get('distance_score')} | heat={d.get('heat_score')} | final={d.get('final_score')}"
        )

    dest_summary = "DIJKSTRA + BEHAVIOURAL MODEL TOP RESULTS:\n" + "\n".join(dest_lines)
    llm_prompt = f"""
    CURRENT SITUATION:
    - Last Known Postal: {postal}
    - Suspect Vehicle: {vehicle}
    - Suspect History: {history_text}
    - Un-Whitelisted (unWL) Units active: {unwl_units} (Creates 'Chaos/Flush Factor')
    - Optional Tags: {optional_tags or "None"}
    - Live Incident Context: {live_context or "None"}

    {dest_summary}

    Based on this data, provide the predictive analysis.
    """

    prediction_data = await call_llm(llm_prompt)

    # Fallback if API fails
    if not prediction_data:
        prediction_data = {
            "prediction": {
                "primary_target": top_dests[0]["postal"] if top_dests else None,
                "secondary_target": None,
                "threat_level": "MEDIUM",
                "behavioral_profile": "",
                "tactical_recommendation": "Maintain visual.",
                "probability_score": 0.0,
                "reasoning": "Fallback due to LLM failure."
            }
        }

    # ==========================================
    # NORMALISE TO LEGACY OUTPUT SCHEMA (for embed compatibility)
    # ==========================================
    if isinstance(prediction_data, dict) and "prediction" in prediction_data:
        p = prediction_data["prediction"]

        prediction_data = {
            "primary_destination": p.get("primary_target"),
            "secondary_destination": p.get("secondary_target"),
            "probability": f"{round((p.get('probability_score') or 0) * 100)}%",
            "confidence_score": p.get("probability_score", 0.0),
            "eta_window": "Unknown",
            "intercept_postals": [p.get("secondary_target")] if p.get("secondary_target") else [],
            "tactical_analysis": p.get("reasoning") or p.get("tactical_recommendation"),
            "risk_level": ("CRITICAL" if p.get("threat_level") == "CRITICAL" else p.get("threat_level", "Medium")),
            "interference_risk": "High" if unwl_units > 0 else "None",
            "failsafe_suggestion": p.get("tactical_recommendation")
        }

    # ==============================
    # DEBUG PATH RESOLUTION LAYER
    # ==============================

    print("\n===== PATH DEBUG START =====")
    print("Resolved start postal:", resolved_postal)
    print("Primary target raw:", prediction_data.get("primary_destination"))
    print("Intercept targets raw:", prediction_data.get("intercept_postals"))

    def resolve_node(n):
        if not n:
            return None

        if n in modified_graph:
            return n

        if isinstance(n, str):
            # already postal format
            if n.startswith("postal_") or n.startswith("N-"):
                return n

            # try POI resolution (THIS IS THE MISSING PIECE)
            poi_resolved = bot.erlc_graph.resolve_poi_to_node(n)
            if poi_resolved:
                return poi_resolved

        return None

    paths_to_draw = []

    primary_raw = prediction_data.get("primary_destination")
    secondary_raw = prediction_data.get("intercept_postals", [])

    primary_target = resolve_node(primary_raw)

    secondary_target = None
    if isinstance(secondary_raw, list) and secondary_raw:
        secondary_target = resolve_node(secondary_raw[0])

    intercepts = prediction_data.get("intercept_postals")
    if isinstance(intercepts, list) and intercepts:
        secondary_target = resolve_node(intercepts[0])

    print("Primary target resolved:", primary_target)
    print("Secondary target resolved:", secondary_target)

    for target in [primary_target, secondary_target]:
        if not target:
            continue

        try:
            print(f"Computing path {resolved_postal} -> {target}")

            path = nx.shortest_path(
                modified_graph,
                resolved_postal,
                target,
                weight="weight"
            )

            path = [resolve_node(p) for p in path]

            print("Path found:", path)

            paths_to_draw.append(path)

        except Exception as e:
            print(f"Path FAILED {resolved_postal} -> {target}: {e}")

    print("Final paths_to_draw:", paths_to_draw)
    print("===== PATH DEBUG END =====\n")

    # 4. (removed duplicate LLM call and normalization block)

    # 5. Generate Map Image
    map_image_buffer = draw_map_path(paths_to_draw)
    file = discord.File(fp=map_image_buffer, filename="predictive_map.png") if map_image_buffer else discord.utils.MISSING

    # 6. Build Stylized Discord Embed
    color_map = {"Low": discord.Color.green(), "Med": discord.Color.orange(), "Medium": discord.Color.orange(), "High": discord.Color.red()}
    embed_color = color_map.get(prediction_data.get("risk_level", "Medium"), discord.Color.blue())

    embed = discord.Embed(
        title="🚨 Metro Predictive Engine",
        description=f"**Target Analysis:** LKL `{postal}` | Vehicle: `{vehicle}`",
        color=embed_color
    )
    
    embed.add_field(name="Predicted Destination", value=f"**{prediction_data.get('primary_destination', 'Unknown')}**", inline=True)
    embed.add_field(name="Probability", value=f"{prediction_data.get('probability', 'N/A')}", inline=True)
    embed.add_field(name="ETA Window", value=prediction_data.get('eta_window', 'N/A'), inline=True)
    
    intercepts = ", ".join(prediction_data.get("intercept_postals", []))
    embed.add_field(name="Secondary Predicted Destination", value=f"`{intercepts}`" if intercepts else "None viable", inline=False)
    
    embed.add_field(name="Tactical Analysis", value=prediction_data.get('tactical_analysis', 'N/A'), inline=False)
    
    embed.add_field(name="Risk Level", value=prediction_data.get('risk_level', 'Unknown'), inline=True)
    embed.add_field(name="unWL Interference Risk", value=prediction_data.get('interference_risk', 'Unknown'), inline=True)
    
    if unwl_units > 0:
         embed.add_field(name="Failsafe Directive", value=prediction_data.get('failsafe_suggestion', 'N/A'), inline=False)

    if map_image_buffer:
        embed.set_image(url="attachment://predictive_map.png")

    embed.set_footer(text="Simon - Metropolitan Predictive Analysis Program.")

    # 7. Send Response
    if map_image_buffer:
        await interaction.followup.send(embed=embed, file=file)
    else:
        await interaction.followup.send(embed=embed)

# ==========================================
# RUN BOT
# ==========================================
if __name__ == "__main__":
    bot.run(TOKEN)