import discord
from discord.ext import commands
from discord import app_commands
import networkx as nx
import json
import math
import io
import re
import aiohttp
from motor.motor_asyncio import AsyncIOMotorClient
from PIL import Image, ImageDraw

# ==========================================
# CONFIG
# ==========================================
TOKEN = "<token>"
MONGO_URI = "mongodb+srv://crxticals:<Password>@suspect-info-cluster.8degrme.mongodb.net/?appName=suspect-info-cluster" # Replace with your MongoDB URI
LLM_API_KEY = "AIzaSyDS-VvCGDEXJ7moBOPQSEnA1W-9-ObOFfk" # Placeholder for your Gemma 4 / AI API key
LLM_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent"

MAP_JSON_PATH = "erlc_map.json"
MAP_IMAGE_PATH = "fall_postals.jpg"

# ==========================================
# LOGIC ENGINE (GRAPH MATH)
# ==========================================
class ERLCGraph:
    def __init__(self, json_path):
        self.graph = nx.Graph()
        self.nodes_data = {}
        self._load_data(json_path)

    def _load_data(self, json_path):
        """Loads the JSON map data into a NetworkX Graph."""
        with open(json_path, 'r') as f:
            data = json.load(f)
        
        # Load Nodes
        for postal, info in data["nodes"].items():
            self.nodes_data[postal] = info
            self.graph.add_node(postal, x=info['x'], y=info['y'], poi=info.get('poi'), robable=info.get('robable', False))
            
        # Load Edges (Base weights based on Euclidean distance)
        for edge in data["edges"]:
            node1, node2, road_name = edge
            x1, y1 = self.nodes_data[node1]['x'], self.nodes_data[node1]['y']
            x2, y2 = self.nodes_data[node2]['x'], self.nodes_data[node2]['y']
            dist = math.hypot(x2 - x1, y2 - y1)
            self.graph.add_edge(node1, node2, road_name=road_name, base_weight=dist)

    def get_modified_graph(self, vehicle: str, unWL_units: int):
        """
        Applies dynamic weights based on the vehicle and the 'Chaos Failsafe' (UnWL units).
        This executes 'One-and-Done' in memory for the command execution.
        """
        G_mod = self.graph.copy()
        
        # Normalize vehicle string
        vehicle = vehicle.lower()
        
        # Calculate dynamic weights
        for u, v, data in G_mod.edges(data=True):
            weight = data['base_weight']
            road = data['road_name'].lower()
            
            # 1. Vehicle Modifier
            is_highway = "highway" in road
            is_offroad = "unnamed" in road
            
            if vehicle == "supercar":
                if is_highway: weight *= 0.6  # Supercars excel on highways
                if is_offroad: weight *= 2.5  # Supercars struggle off-road
            elif vehicle == "jeep" or vehicle == "truck":
                if is_offroad: weight *= 0.8  # Jeeps handle off-road well
                if is_highway: weight *= 1.2  # Slower top speed on highways
            elif vehicle == "cruiser":
                pass # Standard baseline
                
            # 2. The unWL protocol
            # Untrained units cause suspects to panic, taking straight lines (highways) 
            # to outrun them, rather than complex alleyways (unnamed roads).
            if unWL_units > 0:
                panic_factor = min(unWL_units * 0.1, 0.5) # Max 50% shift
                if is_highway:
                    weight *= (1.0 - panic_factor) # Highways become more likely/cheaper
                if is_offroad:
                    weight *= (1.0 + panic_factor) # Complex routes become less likely/expensive
                    
            G_mod[u][v]['weight'] = weight
            
        return G_mod

    def get_top_destinations(self, start_postal: str, G_mod: nx.Graph, top_n: int = 7):
        """Runs Dijkstra's to find the closest POIs or Robable locations."""
        if start_postal not in G_mod:
            return None, None
            
        # Calculate shortest paths and lengths from the start_postal to all nodes
        lengths, paths = nx.single_source_dijkstra(G_mod, start_postal, weight='weight')
        
        destinations = []
        for node, distance in lengths.items():
            if node == start_postal: continue
            node_data = self.nodes_data[node]
            # We care about POIs or Robable locations as likely destinations
            if node_data.get('poi') or node_data.get('robable'):
                destinations.append({
                    "postal": node,
                    "poi": node_data.get('poi', 'Unknown POI'),
                    "robable": node_data.get('robable', False),
                    "distance_score": round(distance, 2),
                    "path": paths[node]
                })
                
        # Sort by shortest distance/time
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
        print("Bot is ready and slash commands synced.")

bot = PredictiveBot()

# ==========================================
# HELPER FUNCTIONS
# ==========================================
def draw_map_path(paths_to_draw: list) -> io.BytesIO:
    """Draws predicted paths on the ER:LC map image."""
    
    try:
        img = Image.open(MAP_IMAGE_PATH).convert()
    except Exception as e:
        print(f"Failed to load map image: {e}")
        return None

    draw = ImageDraw.Draw(img)
    
    # Draw primary path (Red) and secondary paths (Orange, slightly transparent)
    colors = [
    (255, 0, 0, 255),      # solid red
    (255, 165, 0, 180),    # semi-transparent orange
    (255, 165, 0, 180)
]
    
    for idx, path_nodes in enumerate(paths_to_draw[:3]): # Draw top 3 to avoid clutter
        color = colors[0] if idx == 0 else colors[1]
        line_width = 8 if idx == 0 else 4
        
        coords = []
        for postal in path_nodes:
            node_data = bot.erlc_graph.nodes_data[postal]
            coords.append((node_data['x'], node_data['y']))
            
        if len(coords) > 1:
            draw.line(coords, fill=color, width=line_width, joint="curve")
            
    # Save to buffer
    buffer = io.BytesIO()
    # Convert back to RGB to save as JPEG, or save as PNG for transparency
    img.convert("RGB").save(buffer, format="JPEG")
    buffer.seek(0)
    return buffer

async def call_llm(prompt: str) -> dict:
    """Calls Gemini API (Google Generative Language API) and forces JSON output."""

    system_instruction = """
You are an expert predictive policing AI for ER:LC.
Return ONLY valid JSON. No markdown, no explanation.
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

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=payload) as resp:
                if resp.status == 200:
                    data = await resp.json()

                    # Gemini response extraction
                    try:
                        text = data["candidates"][0]["content"]["parts"][0]["text"]
                    except Exception:
                        return None

                    # clean possible formatting
                    text = re.sub(r"```json|```", "", text).strip()

                    try:
                        return json.loads(text)
                    except Exception:
                        return None

                else:
                    print(f"Gemini API Error: {resp.status} - {await resp.text()}")
                    return None

    except Exception as e:
        print(f"LLM Request failed: {e}")
        return None

# ==========================================
# COMMANDS
# ==========================================

@bot.tree.command(name="metro_suspect_log", description="Log a suspect's crime history for future predictive training.")
async def metro_suspect_log(
    interaction: discord.Interaction,
    suspect_name: str,
    crimes_committed: str,
    location: str
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


@bot.tree.command(name="metro_predict", description="Run a predictive policing algorithm on a fleeing suspect.")
async def metro_predict(
    interaction: discord.Interaction, 
    postal: str, 
    vehicle: str, 
    suspect_name: str = None,
    optional_tags: str = None, 
    unWL_units: int = 0
):
    await interaction.response.defer()
    
    if postal not in bot.erlc_graph.nodes_data:
        await interaction.followup.send(f"❌ Error: Postal **{postal}** not found in database.", ephemeral=True)
        return

    # 1. Fetch suspect history + build heatmap
    history_text = "No prior history provided."
    crime_logs = []

    if suspect_name:
        cursor = bot.suspect_logs.find({"suspect_name": suspect_name.lower()}).sort("timestamp", -1).limit(20)
        crime_logs = await cursor.to_list(length=20)

        if crime_logs:
            history_text = "; ".join([h.get("crimes", "") for h in crime_logs])

    # build global crime heatmap
    bot.crime_heatmap.build_from_logs(crime_logs)

    # 2. Graph Math Execution
    modified_graph = bot.erlc_graph.get_modified_graph(vehicle, unWL_units)

    raw_dests = bot.erlc_graph.get_top_destinations(postal, modified_graph, top_n=15)

    if not raw_dests:
        await interaction.followup.send("❌ Error: Could not calculate routes.", ephemeral=True)
        return

    # apply behavioural scoring layer
    scored_dests = []
    for d in raw_dests:
        node_data = bot.erlc_graph.nodes_data[d["postal"]]
        heat = bot.crime_heatmap.score_node(node_data)

        d["heat_score"] = heat
        d["final_score"] = d["distance_score"] / heat
        scored_dests.append(d)

    scored_dests.sort(key=lambda x: x["final_score"])

    top_dests = scored_dests[:7]

    # Extract paths for drawing
    paths_to_draw = [dest['path'] for dest in top_dests]

    # 3. The Psychological Layer (Prepare Prompt)
    dest_summary = f"DIJKSTRA + BEHAVIOURAL MODEL TOP RESULTS:\n{chr(10).join([f'- {d['postal']} | POI: {d['poi']} | dist={d['distance_score']} | heat={d['heat_score']} | final={d['final_score']}' for d in top_dests])}"
    
    llm_prompt = f"""
    CURRENT SITUATION:
    - Last Known Postal: {postal}
    - Suspect Vehicle: {vehicle}
    - Suspect History: {history_text}
    - Un-Whitelisted (unWL) Units active: {unWL_units} (Creates 'Chaos/Flush Factor')
    - Optional Tags: {optional_tags or "None"}
    
    {dest_summary}
    
    Based on this data, provide the predictive analysis.
    """

    # 4. Call LLM
    prediction_data = await call_llm(llm_prompt)
    
    # Fallback if API fails
    if not prediction_data:
        prediction_data = {
            "primary_destination": top_dests[0]["postal"],
            "probability": "Error%",
            "confidence_score": 0.0,
            "eta_window": "Unknown",
            "intercept_postals": top_dests[0]["path"][1:3] if top_dests else [],
            "tactical_analysis": "AI Service Unreachable. Falling back to raw graph math.",
            "risk_level": "Med",
            "interference_risk": "High" if unWL_units > 0 else "None",
            "failsafe_suggestion": "Maintain visual."
        }

    # 5. Generate Map Image
    map_image_buffer = draw_map_path(paths_to_draw)
    file = discord.File(fp=map_image_buffer, filename="predictive_map.jpg") if map_image_buffer else discord.utils.MISSING

    # 6. Build Stylized Discord Embed
    color_map = {"Low": discord.Color.green(), "Med": discord.Color.orange(), "Medium": discord.Color.orange(), "High": discord.Color.red()}
    embed_color = color_map.get(prediction_data.get("risk_level", "Medium"), discord.Color.blue())

    embed = discord.Embed(
        title="🚨 Metro Predictive Policing Engine",
        description=f"**Target Analysis:** LKA `{postal}` | Vehicle: `{vehicle}`",
        color=embed_color
    )
    
    embed.add_field(name="Predicted Destination", value=f"**{prediction_data.get('primary_destination', 'Unknown')}**", inline=True)
    embed.add_field(name="Probability", value=f"{prediction_data.get('probability', 'N/A')} (Conf: {prediction_data.get('confidence_score', 0.0)})", inline=True)
    embed.add_field(name="ETA Window", value=prediction_data.get('eta_window', 'N/A'), inline=True)
    
    intercepts = ", ".join(prediction_data.get("intercept_postals", []))
    embed.add_field(name="Recommended Intercepts", value=f"`{intercepts}`" if intercepts else "None viable", inline=False)
    
    embed.add_field(name="Tactical Analysis", value=prediction_data.get('tactical_analysis', 'N/A'), inline=False)
    
    embed.add_field(name="Risk Level", value=prediction_data.get('risk_level', 'Unknown'), inline=True)
    embed.add_field(name="unWL Interference Risk", value=prediction_data.get('interference_risk', 'Unknown'), inline=True)
    
    if unWL_units > 0:
         embed.add_field(name="Failsafe Directive", value=prediction_data.get('failsafe_suggestion', 'N/A'), inline=False)

    if map_image_buffer:
        embed.set_image(url="attachment://predictive_map.jpg")

    embed.set_footer(text="Powered by Gemma 4 & Dijkstra Core.")

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