"""
cogs/simon.py

The SIMON predictive analysis system.
Covers:
  - /metro_suspect_log
  - /metro_predict
  - /metro_profiler
  - /metro_crime_heatmap
  - /metro_watchlist
"""

import asyncio
import io
import math

import aiohttp
import discord
import networkx as nx
from discord import app_commands
from discord.ext import commands
from PIL import Image, ImageDraw, ImageFont

from llm import call_llm
from map_renderer import draw_heatmap_overlay, draw_map_path


# ==========================================
# ROBLOX HELPER
# ==========================================

async def fetch_roblox_data(session: aiohttp.ClientSession, username: str):
    """
    Resolves a Roblox username → (user_id, display_name, avatar_url).
    Returns (None, None, None) on any failure — callers must handle gracefully.
    """
    try:
        async with session.post(
            "https://users.roblox.com/v1/usernames/users",
            json={"usernames": [username], "excludeBannedUsers": False}
        ) as resp:
            data = await resp.json()

        if not data.get("data"):
            return None, None, None

        user = data["data"][0]
        user_id = user["id"]
        display_name = user.get("displayName") or user.get("name") or username

    except Exception:
        return None, None, None

    try:
        async with session.get(
            "https://thumbnails.roblox.com/v1/users/avatar-headshot",
            params={"userIds": user_id, "size": "420x420", "format": "Png", "isCircular": "false"}
        ) as resp:
            thumb_data = await resp.json()

        avatar_url = None
        if thumb_data and thumb_data.get("data"):
            avatar_url = thumb_data["data"][0].get("imageUrl")

    except Exception:
        avatar_url = None

    return user_id, display_name, avatar_url


# ==========================================
# WATCHLIST: COMPOSITE IMAGE BUILDER
# ==========================================

async def build_watchlist_grid(suspects: list) -> io.BytesIO | None:
    """
    suspects: list of dicts with keys:
        _id   (str)  – suspect name (lowercase)
        count (int)  – number of log entries
    Returns a PNG BytesIO buffer or None on failure.
    """
    COLS       = 2
    CELL_W     = 180
    CELL_H     = 220      # 160 avatar + 60 label area
    AVATAR_SZ  = 160
    PADDING    = 10
    BG_COLOR   = (18, 20, 28)       # dark navy
    CARD_COLOR = (30, 33, 46)       # slightly lighter card
    NAME_COLOR = (230, 230, 230)
    COUNT_COLOR= (220, 80, 80)      # red accent

    rows = math.ceil(len(suspects) / COLS)
    grid_w = COLS * CELL_W + (COLS + 1) * PADDING
    grid_h = rows  * CELL_H + (rows  + 1) * PADDING

    grid = Image.new("RGB", (grid_w, grid_h), BG_COLOR)
    draw = ImageDraw.Draw(grid)

    # ── Font: try to load a small TTF; fall back gracefully ──────
    try:
        font_name  = ImageFont.load_default(size=14)
        font_count = ImageFont.load_default(size=12)
    except Exception:
        font_name  = ImageFont.load_default()
        font_count = ImageFont.load_default()

    async with aiohttp.ClientSession() as session:
        for idx, suspect in enumerate(suspects[:6]):
            col = idx % COLS
            row = idx // COLS

            cell_x = PADDING + col * (CELL_W + PADDING)
            cell_y = PADDING + row * (CELL_H + PADDING)

            # card background
            draw.rounded_rectangle(
                [cell_x, cell_y, cell_x + CELL_W, cell_y + CELL_H],
                radius=10,
                fill=CARD_COLOR
            )

            # ── Avatar ───────────────────────────────────────────
            avatar_x = cell_x + (CELL_W - AVATAR_SZ) // 2
            avatar_y = cell_y + 8

            _, _, avatar_url = await fetch_roblox_data(session, suspect["_id"])

            if avatar_url:
                try:
                    async with session.get(avatar_url) as resp:
                        raw = await resp.read()
                    avatar_img = (
                        Image.open(io.BytesIO(raw))
                        .convert("RGB")
                        .resize((AVATAR_SZ, AVATAR_SZ), Image.LANCZOS)
                    )
                    grid.paste(avatar_img, (avatar_x, avatar_y))
                except Exception:
                    # grey placeholder if download fails
                    draw.rectangle(
                        [avatar_x, avatar_y, avatar_x + AVATAR_SZ, avatar_y + AVATAR_SZ],
                        fill=(60, 60, 70)
                    )
            else:
                draw.rectangle(
                    [avatar_x, avatar_y, avatar_x + AVATAR_SZ, avatar_y + AVATAR_SZ],
                    fill=(60, 60, 70)
                )

            # ── Labels ───────────────────────────────────────────
            label_y_name  = cell_y + AVATAR_SZ + 14
            label_y_count = label_y_name + 18

            display = suspect["_id"].title()
            if len(display) > 18:
                display = display[:16] + "…"

            # centre-align text manually (bbox)
            try:
                name_bbox  = draw.textbbox((0, 0), display, font=font_name)
                count_bbox = draw.textbbox((0, 0), f"{suspect['count']} logs", font=font_count)
                name_x  = cell_x + (CELL_W - (name_bbox[2]  - name_bbox[0]))  // 2
                count_x = cell_x + (CELL_W - (count_bbox[2] - count_bbox[0])) // 2
            except Exception:
                name_x  = cell_x + 10
                count_x = cell_x + 10

            draw.text((name_x,  label_y_name),  display,                  fill=NAME_COLOR,  font=font_name)
            draw.text((count_x, label_y_count), f"{suspect['count']} logs", fill=COUNT_COLOR, font=font_count)

    buf = io.BytesIO()
    grid.save(buf, format="PNG")
    buf.seek(0)
    return buf


# ==========================================
# UI COMPONENTS
# ==========================================

class MetroProfilerView(discord.ui.View):
    def __init__(self, embeds):
        super().__init__(timeout=180)
        self.embeds = embeds
        self.index = 0
        self.update_buttons()

    def update_buttons(self):
        self.children[0].disabled = self.index <= 0
        self.children[1].disabled = self.index >= len(self.embeds) - 1

    @discord.ui.button(label="◀  Previous", style=discord.ButtonStyle.secondary)
    async def previous(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.index > 0:
            self.index -= 1
        self.update_buttons()
        await interaction.response.edit_message(embed=self.embeds[self.index], view=self)

    @discord.ui.button(label="Next  ▶", style=discord.ButtonStyle.secondary)
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.index < len(self.embeds) - 1:
            self.index += 1
        self.update_buttons()
        await interaction.response.edit_message(embed=self.embeds[self.index], view=self)


class WatchlistButton(discord.ui.Button):
    """Opens an ephemeral profiler panel for this suspect."""
    def __init__(self, cog, suspect_name: str, log_count: int, position: int):
        label = f"{suspect_name.title()[:18]}  •  {log_count}"
        super().__init__(
            label=label,
            style=discord.ButtonStyle.danger,
            row=position // 3          # row 0 for first 3, row 1 for last 3
        )
        self.cog = cog
        self.suspect_name = suspect_name

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        # Call the shared builder on the cog
        pages, map_file = await self.cog.build_profiler_result(self.suspect_name)

        if not pages:
            await interaction.followup.send(
                f"❌ No records found for **{self.suspect_name}**.",
                ephemeral=True
            )
            return

        view = MetroProfilerView(pages)

        if map_file:
            await interaction.followup.send(embed=pages[0], view=view, file=map_file, ephemeral=True)
        else:
            await interaction.followup.send(embed=pages[0], view=view, ephemeral=True)


class WatchlistView(discord.ui.View):
    def __init__(self, cog, suspects: list):
        super().__init__(timeout=300)   # buttons live for 5 minutes
        self.cog = cog
        for idx, suspect in enumerate(suspects[:6]):
            self.add_item(
                WatchlistButton(
                    cog=self.cog,
                    suspect_name=suspect["_id"],
                    log_count=suspect["count"],
                    position=idx
                )
            )


# ==========================================
# COG
# ==========================================

class Simon(commands.Cog):
    """SIMON – Predictive analysis commands."""

    def __init__(self, bot):
        self.bot = bot

    # ------------------------------------------------------------------ #
    # SHARED PROFILER BUILDER                                              #
    # ------------------------------------------------------------------ #
    async def build_profiler_result(self, roblox_username: str):
        """
        Runs the full profiler pipeline for roblox_username.
        Returns:
            pages       : list[discord.Embed]  — paginated crime log embeds
            map_file    : discord.File | None  — map overlay attachment
        """
        async with aiohttp.ClientSession() as session:
            _, display_name, image_url = await fetch_roblox_data(session, roblox_username)

        # ── Crime history ────────────────────────────────────────────
        logs_cursor = (
            self.bot.suspect_logs
            .find({"suspect_name": roblox_username.lower()})
            .sort("timestamp", -1)
            .limit(20)
        )
        logs = await logs_cursor.to_list(length=20)

        if not logs:
            return [], None

        # ── Paginate (5 crimes per page) ─────────────────────────────
        pages = []
        for i in range(0, len(logs), 5):
            chunk = logs[i:i + 5]
            desc = f"## 👤 Metro Profiler: {roblox_username}\n**━━━━━━━━━━━━━━━━━━━━**\n"

            for log in chunk:
                desc += (
                    f"\n**Crime:** {log.get('crimes', 'Unknown')}\n"
                    f"**Location:** {log.get('poi') or log.get('postal') or 'Unknown'}\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                )

            embed = discord.Embed(description=desc, color=discord.Color.dark_red())
            if image_url:
                embed.set_thumbnail(url=image_url)
            pages.append(embed)

        # ── POI frequency → map overlay ──────────────────────────────
        poi_counts = {}
        for log in logs:
            poi = log.get("poi") or log.get("postal")
            if poi:
                poi_counts[poi] = poi_counts.get(poi, 0) + 1

        top_pois = sorted(poi_counts.items(), key=lambda x: x[1], reverse=True)[:5]
        nodes = []
        for poi, _ in top_pois:
            resolved = self.bot.erlc_graph.resolve_target(poi)
            if resolved:
                nodes.append(resolved)

        paths_to_draw = []
        for i in range(len(nodes) - 1):
            try:
                path = nx.shortest_path(
                    self.bot.erlc_graph.graph, nodes[i], nodes[i + 1], weight="weight"
                )
                paths_to_draw.append(path)
            except Exception:
                continue

        loop = asyncio.get_running_loop()
        map_buffer = await loop.run_in_executor(
            None, draw_map_path, self.bot.erlc_graph, paths_to_draw
        )

        if pages and map_buffer:
            pages[0].set_image(url="attachment://profile_map.png")

        map_file = discord.File(fp=map_buffer, filename="profile_map.png") if map_buffer else None

        # ── LLM behavioural analysis (first page only) ───────────────
        prompt = f"""
    You are analysing a suspect profile.
    Username: {roblox_username}
    Recent crimes: {[l.get('crimes') for l in logs[:10]]}
    Frequent locations: {list(poi_counts.keys())}
    Provide behavioural robbery pattern analysis.
    """
        llm_result = await call_llm(prompt)
        analysis = "Unavailable"
        if llm_result and isinstance(llm_result, dict):
            analysis = (
                llm_result.get("prediction", {}).get("reasoning")
                or llm_result.get("analysis")
                or "No analysis generated."
            )

        if pages:
            pages[0].add_field(name="Behavioural Pattern", value=analysis[:1024], inline=False)

        return pages, map_file


    # ------------------------------------------------------------------ #
    # /metro_watchlist                                                     #
    # ------------------------------------------------------------------ #
    @app_commands.command(
        name="metro_watchlist",
        description="Display the 6 most logged suspects on the Metro watchlist."
    )
    async def metro_watchlist(self, interaction: discord.Interaction):
        await interaction.response.defer()

        # ── 1. Aggregate top 6 from MongoDB ──────────────────────────
        pipeline = [
            {
                "$group": {
                    "_id": "$suspect_name",
                    "count": {"$sum": 1},
                    "last_crime":    {"$last": "$crimes"},
                    "last_location": {"$last": "$poi"},
                    "last_seen":     {"$last": "$timestamp"}
                }
            },
            {"$sort":  {"count": -1}},
            {"$limit": 6}
        ]

        cursor = self.bot.suspect_logs.aggregate(pipeline)
        top_suspects = await cursor.to_list(length=6)

        if not top_suspects:
            await interaction.followup.send(
                "❌ No suspect records found in the database.",
                ephemeral=True
            )
            return

        # ── 2. Build 2×3 headshot grid ────────────────────────────────
        grid_buffer = await build_watchlist_grid(top_suspects)

        # ── 3. Compose main embed ─────────────────────────────────────
        embed = discord.Embed(
            title="🚨  Metro Suspect Watchlist",
            description=(
                "Top **6** most-logged suspects ranked by incident frequency.\n"
                "Select a name below to open their full **Metro Profiler** report."
            ),
            color=discord.Color.from_rgb(180, 30, 30)
        )

        for suspect in top_suspects:
            last_seen = suspect.get("last_seen", "—")
            if last_seen and last_seen != "—":
                last_seen = last_seen[:10]

            embed.add_field(
                name=f"👤  {suspect['_id'].title()}",
                value=(
                    f"**Logs:** {suspect['count']}\n"
                    f"**Last Crime:** {suspect.get('last_crime', '—')[:40]}\n"
                    f"**Last Location:** {suspect.get('last_location') or '—'}\n"
                    f"**Last Seen:** {last_seen}"
                ),
                inline=True          
            )

        if grid_buffer:
            embed.set_image(url="attachment://watchlist_grid.png")

        embed.set_footer(
            text="Metro Predictive Policing Engine  •  Data sourced from suspect_logs",
            icon_url=interaction.user.display_avatar.url if interaction.user.display_avatar else None
        )

        # ── 4. Attach view + file ─────────────────────────────────────
        view = WatchlistView(self, top_suspects)
        file = discord.File(fp=grid_buffer, filename="watchlist_grid.png") if grid_buffer else discord.utils.MISSING

        if grid_buffer:
            await interaction.followup.send(embed=embed, file=file, view=view)
        else:
            await interaction.followup.send(embed=embed, view=view)


    # ------------------------------------------------------------------ #
    # /metro_profiler                                                      #
    # ------------------------------------------------------------------ #
    @app_commands.command(
        name="metro_profiler",
        description="Open a detailed suspect profiler from Roblox username."
    )
    async def metro_profiler(self, interaction: discord.Interaction, roblox_username: str):
        await interaction.response.defer()

        pages, map_file = await self.build_profiler_result(roblox_username)

        if not pages:
            await interaction.followup.send(
                "❌ No records found for this suspect.",
                ephemeral=True
            )
            return

        view = MetroProfilerView(pages)

        if map_file:
            await interaction.followup.send(embed=pages[0], view=view, file=map_file)
        else:
            await interaction.followup.send(embed=pages[0], view=view)

            
    # ------------------------------------------------------------------ #
    # /metro_suspect_log                                                 #
    # ------------------------------------------------------------------ #
    @app_commands.command(
        name="metro_suspect_log",
        description="Log a suspect's crime history for future predictive training.",
    )
    async def metro_suspect_log(
        self,
        interaction: discord.Interaction,
        suspect_name: str,
        crimes_committed: str,
        location: str,
        entry_type: str = "crime",
    ):
        await interaction.response.defer()

        valid_nodes = "\n".join(
            f"{nid}: {info.get('poi', 'Unknown')}"
            for nid, info in self.bot.erlc_graph.nodes_data.items()
        )

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

        if not isinstance(location_data, dict):
            location_data = {}

        extracted_postal = location_data.get("postal")

        if extracted_postal not in self.bot.erlc_graph.nodes_data:
            location_data    = {"postal": None, "poi": None, "confidence": 0.0}
            extracted_postal = None

        log_entry = {
            "suspect_name": suspect_name.lower(),
            "crimes":       crimes_committed,
            "location_raw": location,
            "postal":       extracted_postal,
            "poi":          location_data.get("poi"),
            "confidence":   location_data.get("confidence", 0.0),
            "entry_type":   entry_type.lower(),
            "timestamp":    interaction.created_at.isoformat(),
        }

        try:
            await self.bot.suspect_logs.insert_one(log_entry)
            await interaction.followup.send(
                f"✅ Logged suspect **{suspect_name}** with structured location data.",
                ephemeral=True,
            )
        except Exception:
            fallback = {**log_entry, "postal": None, "poi": None, "confidence": 0.0}
            await self.bot.suspect_logs.insert_one(fallback)
            await interaction.followup.send(
                "⚠️ Logged with fallback due to database or parsing issue.",
                ephemeral=True,
            )

            
    # ------------------------------------------------------------------ #
    # /metro_predict                                                     #
    # ------------------------------------------------------------------ #
    @app_commands.command(
        name="metro_predict",
        description="Run a predictive policing algorithm on a suspect.",
    )
    async def metro_predict(
        self,
        interaction: discord.Interaction,
        postal: str,
        vehicle: str,
        suspect_name: str,
        optional_tags: str = None,
        unwl_units: int = 0,
        live_context: str = None,
    ):
        await interaction.response.defer()

        if (
            postal not in self.bot.erlc_graph.nodes_data
            and postal not in self.bot.erlc_graph.postal_nodes
        ):
            await interaction.followup.send(
                f"❌ Error: Postal **{postal}** not found in database.",
                ephemeral=True,
            )
            return

        crime_logs   = []
        history_text = "No prior history available."

        if suspect_name:
            try:
                cursor = (
                    self.bot.suspect_logs.find(
                        {"suspect_name": suspect_name.lower()}
                    )
                    .sort("timestamp", -1)
                    .limit(20)
                )
                crime_logs = await cursor.to_list(length=20)
            except Exception as e:
                print(f"[MONGO ERROR] Failed to fetch suspect logs: {e}")

        if crime_logs:
            crime_texts   = []
            sighting_texts = []
            for h in crime_logs:
                if h.get("entry_type") == "sighting":
                    sighting_texts.append(h.get("location_raw", ""))
                else:
                    crime_texts.append(h.get("crimes", ""))
            history_text = "; ".join(crime_texts + sighting_texts)

        self.bot.crime_heatmap.build_from_logs(crime_logs)

        modified_graph = self.bot.erlc_graph.apply_weights(vehicle, unwl_units)
        resolved_postal = postal

        if postal in self.bot.erlc_graph.postal_nodes:
            resolved_postal = self.bot.erlc_graph.postal_nodes[postal]
        elif f"postal_{postal}" in self.bot.erlc_graph.graph:
            resolved_postal = f"postal_{postal}"

        if resolved_postal not in modified_graph:
            await interaction.followup.send(
                f"❌ Invalid start node after resolution: {resolved_postal}",
                ephemeral=True,
            )
            return

        raw_dests = self.bot.erlc_graph.get_top_destinations(
            resolved_postal, modified_graph, top_n=15
        )
        if not raw_dests:
            await interaction.followup.send(
                "❌ Error: Could not calculate routes.", ephemeral=True
            )
            return

        scored_dests = []
        for d in raw_dests:
            node_data = self.bot.erlc_graph.nodes_data.get(d["postal"])
            if not node_data:
                continue
            heat           = self.bot.crime_heatmap.score_node(node_data)
            d["heat_score"] = heat
            d["final_score"] = d["distance_score"] / heat
            scored_dests.append(d)

        scored_dests.sort(key=lambda x: x["final_score"])
        top_dests = scored_dests[:7]

        dest_lines   = [
            f"- {d['postal']} | POI: {d['poi']} | "
            f"dist={d['distance_score']} | heat={d['heat_score']} | "
            f"final={d['final_score']}"
            for d in top_dests
        ]
        dest_summary = "DIJKSTRA + BEHAVIOURAL MODEL TOP RESULTS:\n" + "\n".join(
            dest_lines
        )
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

        if not prediction_data:
            prediction_data = {
                "prediction": {
                    "primary_target":        top_dests[0]["postal"] if top_dests else None,
                    "secondary_target":      None,
                    "threat_level":          "MEDIUM",
                    "behavioral_profile":    "",
                    "tactical_recommendation": "LLM failure fallback.",
                    "probability_score":     0.0,
                    "reasoning":             "LLM returned None. System fallback activated.",
                }
            }

        if isinstance(prediction_data, dict) and "prediction" in prediction_data:
            p = prediction_data["prediction"]
            prediction_data = {
                "primary_destination":  p.get("primary_target"),
                "secondary_destination": p.get("secondary_target"),
                "probability":          f"{round((p.get('probability_score') or 0) * 100)}%",
                "confidence_score":     p.get("probability_score", 0.0),
                "eta_window":           "Unknown",
                "intercept_postals":    [p.get("secondary_target")] if p.get("secondary_target") else [],
                "tactical_analysis":    p.get("reasoning") or p.get("tactical_recommendation"),
                "risk_level":           p.get("threat_level", "Medium"),
                "interference_risk":    "High" if unwl_units > 0 else "None",
                "failsafe_suggestion":  p.get("tactical_recommendation"),
            }

        def resolve_node(n):
            if not n:
                return None
            if n in modified_graph:
                return n
            if isinstance(n, str) and (
                n.startswith("postal_") or n.startswith("N-")
            ):
                return n
            poi_resolved = self.bot.erlc_graph.resolve_poi_to_node(n)
            return poi_resolved

        paths_to_draw  = []
        primary_target = resolve_node(prediction_data.get("primary_destination"))
        intercepts     = prediction_data.get("intercept_postals")
        secondary_target = None
        if isinstance(intercepts, list) and intercepts:
            secondary_target = resolve_node(intercepts[0])

        for target in [primary_target, secondary_target]:
            if not target:
                continue
            try:
                path = nx.shortest_path(
                    modified_graph, resolved_postal, target, weight="weight"
                )
                path = [resolve_node(p) for p in path]
                paths_to_draw.append(path)
            except Exception:
                pass

        loop             = asyncio.get_running_loop()
        map_image_buffer = await loop.run_in_executor(
            None, draw_map_path, self.bot.erlc_graph, paths_to_draw
        )
        file = (
            discord.File(fp=map_image_buffer, filename="predictive_map.png")
            if map_image_buffer
            else discord.utils.MISSING
        )

        color_map = {
            "Low":    discord.Color.green(),
            "Med":    discord.Color.orange(),
            "Medium": discord.Color.orange(),
            "High":   discord.Color.red(),
        }
        embed_color = color_map.get(
            prediction_data.get("risk_level", "Medium"), discord.Color.blue()
        )

        embed = discord.Embed(
            title="<:LAPD_Metropolitan:1495867271501975552> Metro Predictive Engine",
            description=f"**Target Analysis:** LKL `{postal}` | Vehicle: `{vehicle}`",
            color=embed_color,
        )
        embed.add_field(
            name="Predicted Destination",
            value=f"**{prediction_data.get('primary_destination', 'Unknown')}**",
            inline=True,
        )
        embed.add_field(
            name="Probability",
            value=prediction_data.get("probability", "N/A"),
            inline=True,
        )
        embed.add_field(
            name="ETA Window",
            value=prediction_data.get("eta_window", "N/A"),
            inline=True,
        )

        intercepts_str = ", ".join(prediction_data.get("intercept_postals", []))
        embed.add_field(
            name="Secondary Predicted Destination",
            value=f"`{intercepts_str}`" if intercepts_str else "None viable",
            inline=False,
        )
        embed.add_field(
            name="Tactical Analysis",
            value=prediction_data.get("tactical_analysis", "N/A"),
            inline=False,
        )
        embed.add_field(
            name="Risk Level",
            value=prediction_data.get("risk_level", "Unknown"),
            inline=True,
        )
        embed.add_field(
            name="unWL Interference Risk",
            value=prediction_data.get("interference_risk", "Unknown"),
            inline=True,
        )
        if unwl_units > 0:
            embed.add_field(
                name="Failsafe Directive",
                value=prediction_data.get("failsafe_suggestion", "N/A"),
                inline=False,
            )
        if map_image_buffer:
            embed.set_image(url="attachment://predictive_map.png")
        embed.set_footer(
            text="PAPI – The Metropolitan Predictive Analysis Program Insight."
        )

        if map_image_buffer:
            await interaction.followup.send(embed=embed, file=file)
        else:
            await interaction.followup.send(embed=embed)


    # ------------------------------------------------------------------ #
    # /metro_crime_heatmap                                               #
    # ------------------------------------------------------------------ #
    @app_commands.command(
        name="metro_crime_heatmap",
        description="Generate a visual heatmap of historical crime activity.",
    )
    async def metro_crime_heatmap(self, interaction: discord.Interaction):
        await interaction.response.defer()

        try:
            pipeline = [
                {"$match": {"postal": {"$ne": None}}},
                {"$group": {"_id": "$postal", "count": {"$sum": 1}}},
            ]
            cursor  = self.bot.suspect_logs.aggregate(pipeline)
            results = await cursor.to_list(length=None)

            heatmap_data = {r["_id"]: r["count"] for r in results}

            if not heatmap_data:
                await interaction.followup.send(
                    "No historical crime data found to generate a heatmap."
                )
                return

            loop   = asyncio.get_running_loop()
            buffer = await loop.run_in_executor(
                None, draw_heatmap_overlay, self.bot.erlc_graph, heatmap_data
            )

            file  = discord.File(fp=buffer, filename="heatmap.png")
            embed = discord.Embed(
                title="<:LAPD_Metropolitan:1495867271501975552> Metropolitan Crime Heatmap",
                color=discord.Color.red(),
            )
            embed.set_image(url="attachment://heatmap.png")

            await interaction.followup.send(
                content="✅ Crime heatmap generated successfully.", ephemeral=True
            )
            await interaction.channel.send(embed=embed, file=file)

        except Exception as e:
            print(f"[HEATMAP ERROR] {e}")
            await interaction.followup.send(
                "An error occurred while generating the crime heatmap."
            )

async def setup(bot):
    await bot.add_cog(Simon(bot))