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
import os
from pathlib import Path

import aiohttp
import discord
import networkx as nx
from discord.ext import tasks # Import tasks for hourly updates
from discord import app_commands
from discord.ext import commands
from PIL import Image, ImageDraw, ImageFont
import json

from config import ROBLOX_API_KEY
from llm import call_llm
from map_renderer import draw_heatmap_overlay, draw_map_path

BASE_DIR = Path(__file__).parent.resolve()
ARREST_BG_PATH = BASE_DIR / "arrest_background.jpg"
VEHICLE_DB_PATH = BASE_DIR / "erlc_vehicles.json"

# ==========================================
# VEHICLE DATABASE
# ==========================================

def load_vehicle_db():
    with open(VEHICLE_DB_PATH, "r") as f:
        data = json.load(f)
    return data.get("vehicles", [])

def vehicle_label(v: dict) -> str:
    brand = v.get("brand", "")
    model = v.get("model", "")
    real = v.get("real_name", "")
    return f"{brand} {model}/{real}"


VEHICLE_DB = load_vehicle_db()
# Pre-indexed for O(1) lookups
VEHICLE_LOOKUP = {vehicle_label(v): v for v in VEHICLE_DB}

def vehicle_speed_model(vehicle: dict, context: str = "mixed") -> float:
    base = {
        # The base speeds were significantly underestimated relative to the map's pixel distance.
        # A scaling factor of approximately 337.33 (253 minutes / 0.75 minutes) is applied
        # to align the calculated ETA with real-world expectations (30-60 seconds for short drives).
        "highway": 105.0 * 337.33, # ~35419.65
        "city":    45.0 * 337.33,  # ~15179.85
        "mixed":   70.0 * 337.33   # ~23613.1
    }.get(context, 70.0 * 337.33) # Default to scaled mixed speed

    category = vehicle.get("bot_category", "car")

    if category == "supercar":
        base *= 1.25
    elif category == "truck":
        base *= 0.85
    elif category == "jeep":
        base *= 0.92

    hp = vehicle.get("horsepower_normalized")
    if hp is None:
        hp_factor = 1.0
    else:
        hp_factor = 0.75 + (hp / 10.0) * 0.6

    return base * hp_factor


def compute_eta_minutes(distance_cost: float, vehicle: dict, context: str = "mixed") -> int:
    speed = vehicle_speed_model(vehicle, context)
    if speed <= 0:
        return 0
    return int((distance_cost / speed) * 60)


def resolve_vehicle(vehicle_str: str):
    return VEHICLE_LOOKUP.get(vehicle_str)

# ==========================================
# POSTAL NORMALIZATION HELPER
# ==========================================
def normalize_postal(postal: str) -> str:
    postal = postal.strip().upper()

    # Already correct format
    if postal.startswith("N-"):
        return postal

    # Accept "P222" → convert to numeric
    if postal.startswith("P"):
        postal = postal[1:]

    # Pure numeric input → convert to N-XXX
    if postal.isdigit():
        return f"N-{postal}"

    # fallback: extract digits
    digits = "".join(c for c in postal if c.isdigit())
    if digits:
        return f"N-{digits}"

    return postal


# ==========================================
# ROBLOX HELPER
# ==========================================

async def fetch_roblox_data(session: aiohttp.ClientSession, username: str):
    """
    Resolves a Roblox username → (user_id, display_name, avatar_url).
    Returns (None, None, None) on any failure — callers must handle gracefully.
    """
    if not ARREST_BG_PATH.exists():
        print(f"[SYSTEM] Warning: Background image not found at {ARREST_BG_PATH}")
    if not VEHICLE_DB_PATH.exists():
        print(f"[SYSTEM] Warning: Vehicle DB not found at {VEHICLE_DB_PATH}")

    headers = {
        "x-api-key": ROBLOX_API_KEY,
        "Content-Type": "application/json",
        "User-Agent": "Metropolitan-SIMON/2.1 (Google-Cloud-VM)"
    }
    
    # Explicit timeout to prevent hanging on IPv6/DNS resolution
    timeout = aiohttp.ClientTimeout(total=15, connect=5)

    try:
        # Open Cloud v2: Resolve Username to User ID
        async with session.get(
            "https://apis.roblox.com/cloud/v2/users",
            params={"filter": f'username == "{username}"'},
            headers=headers,
            timeout=timeout
        ) as resp:
            if resp.status != 200:
                print(f"[ROBLOX OPEN CLOUD] User Lookup Status {resp.status} for {username}")
                return None, None, None
            data = await resp.json()

        if not data.get("users"):
            return None, None, None

        user = data["users"][0]
        user_id = user["id"]
        display_name = user.get("displayName") or username

    except Exception as e:
        print(f"[ROBLOX API] Exception resolving username '{username}': {repr(e)}")
        return None, None, None

    try:
        # Open Cloud v2: Fetch Headshot Thumbnail
        async with session.get(
            f"https://apis.roblox.com/cloud/v2/users/{user_id}/thumbnail",
            params={"size": "Size420x420", "format": "Png", "isCircular": "false"},
            headers=headers,
            timeout=timeout
        ) as resp:
            if resp.status != 200:
                error_text = await resp.text()
                print(f"[ROBLOX OPEN CLOUD] Avatar Status {resp.status} for {user_id}: {error_text[:100]}")
                return None, None, None
            thumb_data = await resp.json()

        avatar_url = thumb_data.get("thumbnailUrl")

    except Exception as e:
        print(f"[ROBLOX API] Exception during avatar fetch for {username}: {repr(e)}")
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
    COLS       = 3
    CELL_W     = 220
    CELL_H     = 245      # 160 avatar + 85 label area
    AVATAR_SZ  = 160
    PADDING    = 20
    BG_COLOR   = (18, 20, 28)       # dark navy
    CARD_COLOR = (30, 33, 46)       # slightly lighter card
    NAME_COLOR = (230, 230, 230)
    COUNT_COLOR= (220, 80, 80)      # red accent

    rows = math.ceil(len(suspects) / COLS)
    grid_w = COLS * CELL_W + (COLS + 1) * PADDING
    grid_h = rows  * CELL_H + (rows  + 1) * PADDING

    grid = Image.new("RGB", (grid_w, grid_h), BG_COLOR)
    draw = ImageDraw.Draw(grid)

    print(f"[WATCHLIST] Starting grid build for {len(suspects)} suspects...")

    # ── Font: try to load a small TTF; fall back gracefully ────── 
    try:
        font_name  = ImageFont.load_default(size=14)
        font_count = ImageFont.load_default(size=12)
    except Exception as e:
        print(f"[WATCHLIST] Font loading warning: {e}")
        font_name  = ImageFont.load_default()
        font_count = ImageFont.load_default()

    if not ARREST_BG_PATH.exists():
        print(f"[WATCHLIST] CRITICAL: Background {ARREST_BG_PATH} missing.")
    # Pre-load background to avoid repeated disk I/O in the loop
    with Image.open(ARREST_BG_PATH) as bg_file:
        bg_template = bg_file.convert("RGBA").resize((AVATAR_SZ, AVATAR_SZ))

    # Use a standard timeout for the whole session on the VM
    timeout = aiohttp.ClientTimeout(total=30)
    
    async with aiohttp.ClientSession(timeout=timeout) as session:
        # Optimization: Fetch all suspect metadata concurrently
        tasks = [fetch_roblox_data(session, s["_id"]) for s in suspects[:6]]
        roblox_results = await asyncio.gather(*tasks)
        
        # Map results back to suspects
        metadata_map = {
            suspects[i]["_id"]: roblox_results[i] 
            for i in range(len(roblox_results))
        }

        for idx, suspect in enumerate(suspects[:6]):
            col = idx % COLS
            row = idx // COLS

            cell_x = PADDING + col * (CELL_W + PADDING)
            cell_y = PADDING + row * (CELL_H + PADDING)
            
            _, _, avatar_url = metadata_map.get(suspect["_id"], (None, None, None))
            
            # card background
            draw.rounded_rectangle(
                [cell_x, cell_y, cell_x + CELL_W, cell_y + CELL_H],
                radius=10,
                fill=CARD_COLOR
            )

            # ── Avatar ───────────────────────────────────────────
            avatar_x = cell_x + (CELL_W - AVATAR_SZ) // 2
            avatar_y = cell_y + 12

            if avatar_url:
                try:
                    async with session.get(avatar_url) as resp:
                        if resp.status != 200:
                            print(f"[WATCHLIST] Failed to download avatar image: HTTP {resp.status}")
                        raw = await resp.read()
                    avatar_img = Image.open(io.BytesIO(raw)).convert("RGBA").resize((AVATAR_SZ, AVATAR_SZ), Image.LANCZOS)
                    
                    # Create composite using pre-loaded template
                    composite = bg_template.copy()
                    # Composite transparent avatar onto the arrest background
                    composite.paste(avatar_img, (0, 0), avatar_img)
                    grid.paste(composite.convert("RGB"), (avatar_x, avatar_y))
                        
                except Exception as e:
                    print(f"[WATCHLIST] Exception processing image for {suspect['_id']}: {repr(e)}")
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
            label_y_name  = cell_y + AVATAR_SZ + 22
            label_y_count = label_y_name + 20

            display = suspect["_id"].title()
            if len(display) > 20:
                display = display[:18] + "…"

            # centre-align text manually (bbox)
            try:
                name_bbox  = draw.textbbox((0, 0), display, font=font_name)
                count_bbox = draw.textbbox((0, 0), f"{suspect['count']} crimes committed.", font=font_count)
                name_x  = cell_x + (CELL_W - (name_bbox[2]  - name_bbox[0]))  // 2
                count_x = cell_x + (CELL_W - (count_bbox[2] - count_bbox[0])) // 2
            except Exception:
                name_x  = cell_x + 10
                count_x = cell_x + 10

            draw.text((name_x,  label_y_name),  display,                  fill=NAME_COLOR,  font=font_name)
            draw.text((count_x, label_y_count), f"{suspect['count']} crimes committed.", fill=COUNT_COLOR, font=font_count)

    print("[WATCHLIST] Grid build complete.")
    buf = io.BytesIO()
    grid.save(buf, format="PNG")
    buf.seek(0)
    return buf


# ==========================================
# GANG LOGO COMPOSITE IMAGE BUILDER
# ==========================================
async def build_gang_logo_grid(gang_shorthands: list) -> io.BytesIO | None:
    """
    Composites gang logos into a single image.
    Returns a PNG BytesIO buffer or None on failure.
    """
    if not gang_shorthands:
        return None

    logos = []
    for shorthand in gang_shorthands:
        # Linux is case-sensitive. Check multiple variants to be safe.
        potential_paths = [
            BASE_DIR / f"{shorthand.lower()}.png",
            BASE_DIR / f"{shorthand.upper()}.png",
            BASE_DIR / f"{shorthand}.png"
        ]
        
        target_path = next((p for p in potential_paths if p.exists()), None)

        if target_path:
            try:
                logo_img = Image.open(target_path).convert("RGBA")
                logos.append(logo_img)
            except Exception as e:
                print(f"[GANG LOGO] Error loading {target_path}: {e}")
                continue
        else:
            print(f"[GANG LOGO] File missing: {shorthand}. Checked variations in {BASE_DIR}")
    
    if not logos:
        return None

    # Determine grid dimensions (e.g., single row)
    # Assuming all logos are roughly square, let's resize them to a standard size
    LOGO_SIZE = 128 # pixels
    PADDING = 10

    resized_logos = []
    for logo in logos:
        resized_logos.append(logo.resize((LOGO_SIZE, LOGO_SIZE), Image.LANCZOS))

    grid_width = (LOGO_SIZE * len(resized_logos)) + (PADDING * (len(resized_logos) + 1))
    grid_height = LOGO_SIZE + (PADDING * 2) # Top and bottom padding

    # Background matches the individual watchlist grid (18, 20, 28)
    grid = Image.new("RGB", (grid_width, grid_height), (18, 20, 28))

    x_offset = PADDING
    for logo in resized_logos:
        grid.paste(logo, (x_offset, PADDING), logo)
        x_offset += LOGO_SIZE + PADDING

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

class GangIntelButton(discord.ui.Button):
    """Opens a tactical briefing on a specific gang."""
    def __init__(self, cog, label: str, shorthand: str):
        super().__init__(
            label=f"Intel: {label}",
            style=discord.ButtonStyle.danger,
            row=0
        )
        self.cog = cog
        self.shorthand = shorthand

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        pages, files = await self.cog.build_gang_profiler(self.shorthand)
        if not pages:
            await interaction.followup.send("No intelligence found for this gang.", ephemeral=True)
            return
        
        # Using a simple view for gang intel since it's usually a single detailed page
        await interaction.followup.send(embed=pages[0], files=files, ephemeral=True)


class WatchlistButton(discord.ui.Button): 
    """Opens an ephemeral profiler panel for this suspect."""
    def __init__(self, cog, suspect_name: str, log_count: int, position: int):
        label = f"No.{position + 1}: {suspect_name.title()[:15]}"
        super().__init__(
            label=label,
            style=discord.ButtonStyle.secondary,
            row=position // 3  # Aligns with the 3-column visual grid (Row 0: 1-3, Row 1: 4-6)
        )
        self.cog = cog
        self.suspect_name = suspect_name

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        # Call the shared builder on the cog
        pages, files_tuple = await self.cog.build_profiler_result(self.suspect_name)
        map_file, avatar_file = files_tuple

        if not pages:
            await interaction.followup.send(
                f"❌ No records found for **{self.suspect_name}**.",
                ephemeral=True
            )
            return

        view = MetroProfilerView(pages)
        
        files = []
        if map_file: files.append(map_file)
        if avatar_file: files.append(avatar_file)

        await interaction.followup.send(embed=pages[0], view=view, files=files, ephemeral=True)


class SuspectWatchlistView(discord.ui.View):
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

class GangWatchlistView(discord.ui.View):
    def __init__(self, cog):
        super().__init__(timeout=300)
        self.cog = cog
        
        gangs = [
            ("77th Saints", "77th"),
            ("West Coast Cartel", "WCC"),
            ("Noche Silente", "NSH")
        ]
        
        for name, shorthand in gangs:
            self.add_item(
                GangIntelButton(cog=self.cog, label=name, shorthand=shorthand)
            )

# ==========================================
# COG
# ==========================================

class Simon(commands.Cog):
    """SIMON – Predictive analysis commands."""

    def __init__(self, bot):
        self.bot = bot
        self.gang_logos_cache = None
        self._roblox_cache = {}  # Cache for (user_id, display_name, avatar_url)
        self.settings = self.bot.mongo_client["erlc_database"]["settings"]
        # Cache valid nodes string for LLM extraction efficiency
        self._nodes_prompt_cache = "\n".join(
            f"{nid}: {info.get('poi', 'Unknown')}"
            for nid, info in self.bot.erlc_graph.nodes_data.items()
        )

    @commands.Cog.listener()
    async def on_ready(self):
        # Ensure the bot is ready before starting the loop
        
        # Pre-render and cache the gang logos composite image for performance
        print(f"[SIMON] Running in directory: {os.getcwd()}")
        if self.gang_logos_cache is None:
            print("[SIMON] Caching gang logo composite image...")
            gangs = ["77th", "WCC", "NSH"]
            self.gang_logos_cache = await build_gang_logo_grid(gangs)
            print("[SIMON] Gang logo cache initialized.")

        if not self.update_hourly_watchlist.is_running():
            print("[WATCHLIST] Starting hourly update loop...")
            self.update_hourly_watchlist.start()

    async def get_watchlist_channel_id(self):
        """Fetch current watchlist channel for AUTO-SEND from dynamic settings."""
        data = await self.settings.find_one({"_id": "guild_config"})
        if data and "channels" in data:
            return data["channels"].get("watchlist_auto")
        return getattr(self.bot, "watchlist_channel_id", None)

    async def get_intel_command_channel_id(self):
        """Fetch channel where intelligence commands are public (non-ephemeral)."""
        data = await self.settings.find_one({"_id": "guild_config"})
        if data and "channels" in data:
            return data["channels"].get("intelligence_command")
        return None

    async def vehicle_autocomplete(self, interaction: discord.Interaction, current: str):
        current = current.lower()
        results = []
        
        # Optimization: Early exit for empty strings if you want to save processing
        if not current:
            return [app_commands.Choice(name=vehicle_label(v)[:100], value=vehicle_label(v)) for v in VEHICLE_DB[:25]]

        for v in VEHICLE_DB:
            label = vehicle_label(v)
            if current in label.lower():
                results.append(
                    app_commands.Choice(name=label[:100], value=label)
                )
            if len(results) >= 25:
                break

        return results

    # ------------------------------------------------------------------ #
    # SHARED PROFILER BUILDER                                              #
    # ------------------------------------------------------------------ #
    async def build_profiler_result(self, roblox_username: str):
        """
        Runs the full profiler pipeline for roblox_username.
        Returns:
            pages       : list[discord.Embed]  — paginated crime log embeds
            map_file    : discord.File | None  — map overlay attachment
            avatar_file : discord.File | None  — composited profile picture
        """
        async with aiohttp.ClientSession() as session:
            # Use cache if available
            if roblox_username.lower() in self._roblox_cache:
                _, display_name, image_url = self._roblox_cache[roblox_username.lower()]
            else:
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
                return [], (None, None)

            # ── Process Avatar with Background (Reusing outer session) ───
            print(f"[PROFILER] Downloading avatar for {roblox_username}...")
            avatar_file = None
            if image_url:
                try:
                    async with session.get(image_url) as resp:
                        if resp.status != 200:
                            print(f"[PROFILER] Failed to download avatar image: HTTP {resp.status}")
                        raw = await resp.read()

                    avatar_img = Image.open(io.BytesIO(raw)).convert("RGBA").resize((420, 420), Image.LANCZOS)
                    with Image.open(ARREST_BG_PATH) as bg:
                        composite = bg.convert("RGBA").resize((420, 420))
                        composite.paste(avatar_img, (0, 0), avatar_img)

                        buf = io.BytesIO()
                        composite.convert("RGB").save(buf, format="PNG", optimize=True)
                        buf.seek(0)
                        avatar_file = discord.File(fp=buf, filename="profile_avatar.png")
                except Exception as e:
                    print(f"[PROFILER] Exception processing avatar for {roblox_username}: {repr(e)}")

            # ── Paginate (5 crimes per page) ─────────────────────────────
            pages = []
            for i in range(0, len(logs), 5):
                chunk = logs[i:i + 5]
                desc = f"## <:LAPD_Metropolitan:1495867271501975552> | Intelligence Profile: {roblox_username}\n**━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━**\n"

                for log in chunk:
                    desc += (
                        f"\n**Crime:** {log.get('crimes', 'Unknown')}\n"
                    f"**Location:** {log.get('poi') or log.get('postal') or log.get('location_raw') or 'Unknown'}\n"
                        "**━━━━━━━━━━━━━━━━━━━━━━━━━**\n"
                    )

                embed = discord.Embed(description=desc, color=discord.Color.dark_red())
                if avatar_file:
                    embed.set_thumbnail(url="attachment://profile_avatar.png")
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
    Analyze the suspect's spatial behavior and geographical footprint.
    Username: {roblox_username}
    Recent History (Crime + Location): {[{'crime': l.get('crimes'), 'loc': l.get('location_raw')} for l in logs[:10]]}
    Frequented POIs: {list(poi_counts.keys())}

    TASK: Identify physical location patterns. Determine which areas they treat as "start points" versus "targets." 
    Focus on where they typically rob and their habitual origin points (where they come from). 
    Avoid categorizing by robbery "value" or "tier"; prioritize their movement logic and POI clusters.
    Provide ONLY the analysis text. Do NOT include preambles or postambles.
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
                pages[0].add_field(name="🧠 S.I.M.O.N. Behavioural Analysis", value=f"> {analysis[:1000]}", inline=False)

            return pages, (map_file, avatar_file)


    async def build_gang_profiler(self, gang_shorthand: str):
        """Generates a profile for a specific gang based on logs and manual MO."""
        gang_config = await self.settings.find_one({"_id": f"gang_{gang_shorthand}"})
        gang_config = gang_config or {}
        
        # Aggregation for top members
        pipeline = [
            {"$match": {"gang": gang_shorthand}},
            {"$group": {"_id": "$suspect_name", "count": {"$sum": 1}}},
            {"$sort": {"count": -1}},
            {"$limit": 5}
        ]
        cursor = self.bot.suspect_logs.aggregate(pipeline)
        top_members = await cursor.to_list(length=5)
        
        # Load Gang Logo File
        logo_file = None
        potential_paths = [
            BASE_DIR / f"{gang_shorthand.lower()}.png",
            BASE_DIR / f"{gang_shorthand.upper()}.png",
            BASE_DIR / f"{gang_shorthand}.png"
        ]
        target_path = next((p for p in potential_paths if p.exists()), None)
        if target_path:
            logo_file = discord.File(target_path, filename="gang_logo.png")

        mo_text = gang_config.get("mo", "No operational data on file.") if gang_config else "No data."
        vehicles = gang_config.get("vehicles", "Unknown") if gang_config else "Unknown"
        clothing = gang_config.get("clothing", "Unknown") if gang_config else "Unknown"

        # Load Gang Logo File
        # Fetch picture of top rep
        avatar_file = None
        top_rep_name = "Unknown"
        if top_members:
            top_rep_name = top_members[0]["_id"]
            async with aiohttp.ClientSession() as session:
                _, _, avatar_url = await fetch_roblox_data(session, top_rep_name)
                if avatar_url:
                    async with session.get(avatar_url) as resp:
                        raw = await resp.read()
                    avatar_img = Image.open(io.BytesIO(raw)).convert("RGBA").resize((420, 420), Image.LANCZOS)
                    with Image.open(ARREST_BG_PATH) as bg:
                        composite = bg.convert("RGBA").resize((420, 420))
                        composite.paste(avatar_img, (0, 0), avatar_img)
                        buf = io.BytesIO()
                        composite.convert("RGB").save(buf, format="PNG")
                        buf.seek(0)
                        avatar_file = discord.File(fp=buf, filename="gang_top_rep.png")

        # LLM Synthesis of the manual MO
        prompt = f"Summarize this gang MO intelligence for a tactical briefing. Focus on patterns and threats: {mo_text}. Do NOT include any preambles or postambles."
        summary = await call_llm(prompt)
        analysis = summary.get("prediction", {}).get("reasoning") or summary.get("analysis") or mo_text

        desc = (
            f"## ️ TACTICAL INTELLIGENCE BRIEFING\n"
            f"**Faction:** `{gang_shorthand}`\n"
            f"**Status:** `ACTIVE / UNDER SURVEILLANCE`\n"
            f"**━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━**\n\n"
            f"### 📋 Operational Analysis (M.O.)\n"
            f"> {analysis}\n\n"
            f"🚘 **Tactical Vehicles:** {vehicles}\n"
            f"👕 **Uniform/Identifiers:** {clothing}\n\n"
            f"### 👥 High-Value Targets (Affiliates)\n"
        )
        
        for i, m in enumerate(top_members):
            prefix = "👑" if i == 0 else "🔹"
            desc += f"{prefix} **{m['_id'].title()}** — `{m['count']}` documented incidents\n"

        embed = discord.Embed(description=desc, color=discord.Color.gold())
        
        # Set Gang Logo as the primary Faction ID (Thumbnail)
        if logo_file:
            embed.set_thumbnail(url="attachment://gang_logo.png")
            
        # Set Top Suspect as the Priority Affiliate (Author Icon)
        if avatar_file:
            embed.set_author(name=f"Priority Affiliate: {top_rep_name.title()}", icon_url="attachment://gang_top_rep.png")
        
        embed.set_footer(text="SIMON v2.1 • Gang Intelligence Module • DO NOT DISCLOSE", icon_url="https://i.imgur.com/qdvbBqe.png") # This icon_url is hardcoded, should it be dynamic?
        
        files = [f for f in [avatar_file, logo_file] if f]
        return [embed], files


    async def _generate_gang_watchlist_content(self):
        """Aggregates crime data by gang and identifies the top representative for each."""
        print("[WATCHLIST] Compiling gang analytics...")
        gangs = ["77th", "WCC", "NSH"]
        gang_stats = []
        
        for g in gangs:
            # Total crimes for the gang
            total = await self.bot.suspect_logs.count_documents({"gang": g})
            
            # Top suspect in this gang
            pipeline = [
                {"$match": {"gang": g}},
                {"$group": {"_id": "$suspect_name", "count": {"$sum": 1}}},
                {"$sort": {"count": -1}},
                {"$limit": 1}
            ]
            res = await self.bot.suspect_logs.aggregate(pipeline).to_list(1)
            top_sus = res[0] if res else {"_id": "Unknown", "count": 0}
            
            gang_stats.append({
                "gang": g,
                "total": total,
                "top_rep": top_sus["_id"],
                "rep_count": top_sus["count"]
            })
        print(f"[WATCHLIST] Gang stats compiled for: {[g['gang'] for g in gang_stats]}")

        # Use cached gang logo image
        gang_logo_file = discord.utils.MISSING
        if self.gang_logos_cache:
            # Reset pointer before reading from cache
            self.gang_logos_cache.seek(0)
            # We pass a copy of the BytesIO object to avoid issues with concurrent reads
            gang_logo_file = discord.File(fp=self.gang_logos_cache, filename="gang_logos.png")

        embed = discord.Embed(
            title="<:LAPD_Metropolitan:1495867271501975552> Metropolitan Division | Organised Crime Analytics",
            description="## 🏙️ GANG ACTIVITY MONITOR\nTracking the activity of known criminal factions within the city.\n**━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━**",
            color=discord.Color.dark_grey()
        )

        for stat in gang_stats:
            embed.add_field(
                name=f"Organised Crime Group: {stat['gang']}",
                value=(
                    # Removed the text placeholder as the image will be at the bottom
                    f" **Total Crimes:** `{stat['total']}`\n"
                    f"👑 **Most Active:** `{stat['top_rep'].title()}`\n"
                    f"📈 **Individual Share:** `{stat['rep_count']}` logs"
                ),
                inline=True
            )
        
        if gang_logo_file:
            embed.set_image(url="attachment://gang_logos.png")
        embed.set_footer(text="SIMON v2.1 • Gang Intelligence Module")
        view = GangWatchlistView(self)
        return embed, gang_logo_file, view


    async def _generate_suspect_watchlist_content(self):
        """Helper to generate the embed, file, and view for the watchlist."""
        print("[WATCHLIST] Fetching top suspect data from MongoDB...")
        # 1. Aggregate top 6 suspects by total crime count
        top_suspects_pipeline = [
            {
                "$group": {
                    "_id": "$suspect_name",
                    "count": {"$sum": 1},
                    "last_seen":     {"$last": "$timestamp"}
                }
            },
            {"$sort":  {"count": -1}},
            {"$limit": 6}
        ]
        try:
            cursor = self.bot.suspect_logs.aggregate(top_suspects_pipeline)
            top_suspects = await cursor.to_list(length=6)
        except Exception as e:
            print(f"[WATCHLIST] MongoDB Aggregation Error: {e}")
            return None, None, None
            
        print(f"[WATCHLIST] Found {len(top_suspects)} top suspects.")
        if not top_suspects:
            print("[WATCHLIST] Aggregation returned 0 results. Check if suspect_logs collection is empty.")
            return None, None, None

        names = [s["_id"] for s in top_suspects]
        freq_pipeline = [
            {"$match": {"suspect_name": {"$in": names}, "postal": {"$ne": None}}},
            {"$group": {"_id": {"name": "$suspect_name", "postal": "$postal"}, "count": {"$sum": 1}}},
            {"$sort": {"count": -1}},
            {"$group": {"_id": "$_id.name", "top_postal": {"$first": "$_id.postal"}}}
        ]
        freq_results = await self.bot.suspect_logs.aggregate(freq_pipeline).to_list(length=6)
        freq_map = {r["_id"]: r["top_postal"] for r in freq_results}

        for suspect in top_suspects:
            postal_id = freq_map.get(suspect["_id"])
            if postal_id:
                node_info = self.bot.erlc_graph.nodes_data.get(postal_id)
                suspect["most_frequent_location"] = node_info.get("poi") if node_info else postal_id
            else:
                suspect["most_frequent_location"] = "UNK"

        print("[WATCHLIST] Locations resolved. Starting grid generation...")
        # 2. Build 2×3 headshot grid
        print(f"[WATCHLIST] Building visual grid for {len(top_suspects)} suspects...")
        grid_buffer = await build_watchlist_grid(top_suspects)
        if not grid_buffer:
            print("[WATCHLIST] Warning: build_watchlist_grid returned None.")
            # We continue even if the image fails, though the generator might return None later

        # 3. Compose main embed
        embed = discord.Embed(
            title="<:LAPD_Metropolitan:1495867271501975552> Metropolitan Division | Crime Analytics",
            description="## 🚨 ACTIVE WATCHLIST\nThe predictive engine has identified the following high-frequency offenders. Tactical profiling is available via the components below.\n**━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━**",
            color=discord.Color.from_rgb(18, 20, 28)
        )
        for suspect in top_suspects:
            last_seen = suspect.get("last_seen", "—")
            if last_seen and last_seen != "—":
                # Convert ISO timestamp to a clean date or Discord timestamp
                try:
                    last_seen = last_seen.split("T")[0]
                except:
                    pass

            embed.add_field(
                name=f"{suspect['_id']}",
                value=(
                    f" **Incidents:** `{suspect['count']}`\n"
                    f"📍 **Most Freq:** `{suspect.get('most_frequent_location') or 'UNK'}`\n"
                    f"🗓️ **Active:** `{last_seen}`"
                ),
                inline=True          
            )

        if grid_buffer:
            embed.set_image(url="attachment://watchlist_grid.png")

        embed.set_footer(
            text="S.I.M.O.N. v2.1  •  Metropolitan Predictive Analysis",
        )

        view = SuspectWatchlistView(self, top_suspects)
        file = discord.File(fp=grid_buffer, filename="watchlist_grid.png") if grid_buffer else discord.utils.MISSING
        return embed, file, view


    @tasks.loop(hours=1.0)
    async def update_hourly_watchlist(self):
        await self.bot.wait_until_ready() # Ensure bot is fully ready
        print("[WATCHLIST] Triggering hourly update cycle...")

        # Fetch the last watchlist message ID from the database
        state = await self.bot.bot_state.find_one({"_id": "watchlist_state"})
        state = state or {}
        last_suspect_id = state.get("last_suspect_msg_id")
        last_gang_id = state.get("last_gang_msg_id")
        print(f"[WATCHLIST] Clean-up: Previous message IDs identified as {last_suspect_id}, {last_gang_id}")

        channel_id = await self.get_watchlist_channel_id()
        if not channel_id:
            print("[WATCHLIST] Error: No channel configured in settings or config.py")
            return
            
        channel = self.bot.get_channel(channel_id)
        if not channel:
            print(f"[WATCHLIST] Error: Watchlist channel with ID {channel_id} not found.")
            print(f"[WATCHLIST] Available channels: {[c.id for c in self.bot.get_all_channels() if isinstance(c, discord.TextChannel)]}")
            return

        # 1. Delete previous messages
        for msg_id in [last_suspect_id, last_gang_id]:
            if msg_id:
                try:
                    old_message = await channel.fetch_message(msg_id)
                    await old_message.delete()
                except Exception:
                    pass
        print("[WATCHLIST] Previous messages cleared (if any).")

        # 2. Generate and Send Suspect Watchlist
        print("[WATCHLIST] Generating suspect watchlist content...")
        s_embed, s_file, s_view = await self._generate_suspect_watchlist_content()
        if not s_embed:
            print("[WATCHLIST] Early exit: Generator returned no content (s_embed is None).")
            return
        print("[WATCHLIST] Suspect content generated successfully.")
        
        # 3. Generate and Send Gang Watchlist (now returns file too)
        print("[WATCHLIST] Generating gang watchlist content...")
        g_embed, g_file, g_view = await self._generate_gang_watchlist_content()
        print("[WATCHLIST] Gang content generated successfully.")

        try:
            if s_file:
                new_suspect_msg = await channel.send(embed=s_embed, file=s_file, view=s_view)
            else:
                new_suspect_msg = await channel.send(embed=s_embed, view=s_view)
            
            if g_file:
                new_gang_msg = await channel.send(embed=g_embed, file=g_file, view=g_view)
            else:
                new_gang_msg = await channel.send(embed=g_embed, view=g_view)
            
            # 4. Update State
            await self.bot.bot_state.update_one(
                {"_id": "watchlist_state"},
                {"$set": {
                    "last_suspect_msg_id": new_suspect_msg.id,
                    "last_gang_msg_id": new_gang_msg.id
                }},
                upsert=True
            )
            print(f"[WATCHLIST] Hourly updates posted in {channel.name}.")
            
        except discord.Forbidden:
            print(f"[WATCHLIST] Error: Missing permissions to send message in {channel.name}.")
        except Exception as e:
            print(f"[WATCHLIST] Exception sending new watchlist: {repr(e)}")
            import traceback; traceback.print_exc()


    # ------------------------------------------------------------------ #
    # /metro_watchlist                                                     #
    # ------------------------------------------------------------------ #
    @app_commands.command(
        name="metro_watchlist",
        description="Display the 6 most logged suspects on the Metro watchlist."
    )
    async def metro_watchlist(self, interaction: discord.Interaction):
        cmd_channel_id = await self.get_intel_command_channel_id()
        is_ephemeral = (interaction.channel_id != cmd_channel_id)
        await interaction.response.defer(ephemeral=is_ephemeral)

        s_embed, s_file, s_view = await self._generate_suspect_watchlist_content()

        if not s_embed:
            await interaction.followup.send(
                "❌ No suspect records found in the database.",
                ephemeral=True
            )
            return

        g_embed, g_file, g_view = await self._generate_gang_watchlist_content()

        if s_file:
            await interaction.followup.send(embed=s_embed, file=s_file, view=s_view, ephemeral=is_ephemeral)
        else:
            await interaction.followup.send(embed=s_embed, view=s_view, ephemeral=is_ephemeral)
        
        # Follow up with the Gang Monitor as a second message
        if g_file:
            await interaction.followup.send(embed=g_embed, file=g_file, view=g_view, ephemeral=is_ephemeral)
        else:
            await interaction.followup.send(embed=g_embed, view=g_view, ephemeral=is_ephemeral)

    # ------------------------------------------------------------------ #
    # /metro_profiler                                                      #
    # ------------------------------------------------------------------ #
    @app_commands.command(
        name="metro_profiler",
        description="Open a detailed suspect profiler from Roblox username."
    )
    async def metro_profiler(self, interaction: discord.Interaction, roblox_username: str):
        cmd_channel_id = await self.get_intel_command_channel_id()
        is_ephemeral = (interaction.channel_id != cmd_channel_id)
        await interaction.response.defer(ephemeral=is_ephemeral)

        pages, files_tuple = await self.build_profiler_result(roblox_username)
        map_file, avatar_file = files_tuple

        if not pages:
            await interaction.followup.send(
                "❌ No records found for this suspect.",
                ephemeral=True
            )
            return

        view = MetroProfilerView(pages)
        
        files = []
        if map_file: files.append(map_file)
        if avatar_file: files.append(avatar_file)

        await interaction.followup.send(embed=pages[0], view=view, files=files, ephemeral=is_ephemeral)

            
    # ------------------------------------------------------------------ #
    # /metro_suspect_log                                                 #
    # ------------------------------------------------------------------ #
    @app_commands.command(
        name="metro_suspect_log",
        description="Log a suspect's crime history for future predictive training.",
    )
    @app_commands.choices(gang=[
        app_commands.Choice(name="None", value="none"),
        app_commands.Choice(name="77th Saints Gang (77th)", value="77th"),
        app_commands.Choice(name="West Coast Cartel (WCC)", value="WCC"),
        app_commands.Choice(name="Noche Silente Hermanos Gang (NSH)", value="NSH")
    ])
    async def metro_suspect_log(
        self,
        interaction: discord.Interaction,
        suspect_name: str,
        gang: app_commands.Choice[str],
        crimes_committed: str,
        location: str,
        entry_type: str = "crime",
    ):
        now = interaction.created_at # This is already a datetime object
        await interaction.response.defer(ephemeral=True)
        suspect_key = suspect_name.lower()

        valid_nodes = self._nodes_prompt_cache

        extraction_prompt = f"""
You are a strict JSON extractor.
Map the provided location description to the closest valid node ID (e.g., N-205) in the graph.
You MUST only choose from the provided nodes.

VALID NODES (NodeID: POI):
{valid_nodes}

USER LOCATION INPUT:
{location}

Return ONLY JSON in this format:
{{
  "node_id": "The N-XXX ID from the list",
  "poi": "The corresponding POI name",
  "confidence": 0.0
}}
"""
        location_data = await call_llm(extraction_prompt)

        if not isinstance(location_data, dict):
            location_data = {}

        # Use graph resolver to normalize and validate the node ID
        raw_node = location_data.get("node_id") or location_data.get("postal")
        extracted_node = self.bot.erlc_graph.resolve_target(raw_node)

        if extracted_node not in self.bot.erlc_graph.nodes_data:
            extracted_node = None

        log_entry = {
            "suspect_name": suspect_key,
            "gang":         gang.value if gang.value != "none" else None,
            "officer_id":   interaction.user.id,
            "crimes":       crimes_committed,
            "location_raw": location,
            "postal":       extracted_node,
            "poi":          location_data.get("poi"),
            "confidence":   location_data.get("confidence", 0.0),
            "entry_type":   entry_type.lower(),
            "timestamp":    now,
        }

        try:
            await self.bot.suspect_logs.insert_one(log_entry)
            
            # Intel Point Logic
            points = 1 # Default for Crime/Sighting
            reason = "Suspect Log"
            
            # Repeat Offender Bonus (Every 5th log for this suspect)
            suspect_count = await self.bot.suspect_logs.count_documents({"suspect_name": suspect_key})
            if suspect_count % 5 == 0:
                points += 1
                reason = f"Repeat Offender Tracking ({suspect_count} logs)"

            ops_cog = self.bot.get_cog("Operations")
            if ops_cog:
                await ops_cog._award_intel_points(interaction.user.id, points, reason)

            await interaction.followup.send(f"✅ Logged suspect **{suspect_name}**. (+{points} Intel Points)")
        except Exception:
            fallback = {**log_entry, "postal": None, "poi": None, "confidence": 0.0}
            await self.bot.suspect_logs.insert_one(fallback)
            await interaction.followup.send("⚠️ Logged with fallback due to database or parsing issue.")

            
    # ------------------------------------------------------------------ #
    # /metro_predict                                                     #
    # ------------------------------------------------------------------ #
    @app_commands.command(
        name="metro_predict",
        description="Run a predictive policing algorithm on a suspect.",
    )
    @app_commands.autocomplete(vehicle=vehicle_autocomplete)
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
        postal = normalize_postal(postal)

        vehicle_data = resolve_vehicle(vehicle)

        if not vehicle_data:
            await interaction.followup.send(
                "❌ Invalid vehicle selection.",
                ephemeral=True
            )
            return

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

    TASK: Provide predictive analysis focusing on spatial POI patterns. 
    Prioritize the suspect's historical movement corridors and origin points over the specific type of crime. 
    Analyze if the current LKL suggests a sequence progression from a known origin or a return to a specific territory.
    Output strictly follows the system instruction schema with no conversational filler.
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
        def get_destination_display(node_id: str):
            if not node_id:
                return "Unknown"
            node_info = self.bot.erlc_graph.nodes_data.get(node_id)
            if node_info and node_info.get("poi") and node_info["poi"] != "Unknown":
                return f"{node_info['poi']} ({node_id})"
            return node_id

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

        # ETA computation block
        eta_window = "Unknown"

        try:
            if primary_target and resolved_postal:
                path = nx.shortest_path(
                    modified_graph,
                    resolved_postal,
                    primary_target,
                    weight="weight"
                )

                total_cost = 0.0
                for i in range(len(path) - 1):
                    edge_data = modified_graph.get_edge_data(path[i], path[i + 1])
                    if edge_data and "weight" in edge_data:
                        total_cost += edge_data["weight"]

                context = "mixed"
                if total_cost > 120:
                    context = "highway"
                elif total_cost < 50:
                    context = "city"

                minutes = compute_eta_minutes(total_cost, vehicle_data, context)
                eta_window = f"{minutes} min"

        except Exception:
            eta_window = "Unknown"

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
            title="<:LAPD_Metropolitan:1495867271501975552> S.I.M.O.N. Predictive Engine",
            description=f"**Target Analysis:** LKL `{postal}` | Vehicle: `{vehicle}`\n**━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━**",
            
            color=embed_color,
        )
        embed.set_thumbnail(url="https://i.imgur.com/qdvbBqe.png")
        embed.add_field(
            name="📈 Predicted Destination",
            value=f"**{get_destination_display(prediction_data.get('primary_destination'))}**",
            inline=True,
        )
        embed.add_field(
            name="📊 Probability",
            value=prediction_data.get("probability", "N/A"),
            inline=True,
        )
        embed.add_field(
            name="ETA Window",
            value=eta_window,
            inline=True,
        )

        secondary_dest_nodes = prediction_data.get("intercept_postals", [])
        intercepts_display = [get_destination_display(node) for node in secondary_dest_nodes]
        intercepts_str = ", ".join(intercepts_display)
        embed.add_field(
            name="🗺️ Secondary Predicted Destination",
            value=f"`{intercepts_str}`" if intercepts_str else "None viable",
            inline=False,
        )
        embed.add_field(
            name="♟️ Tactical Analysis",
            value=prediction_data.get("tactical_analysis", "N/A"),
            inline=False,
        )
        embed.add_field(
            name="⚠️ Risk Level",
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
            text="S.I.M.O.N v2.1 – Metropolitan Predictive Analysis"
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
        await interaction.response.defer(ephemeral=True)

        try:
            pipeline = [
                {"$match": {"postal": {"$ne": None}}},
                {"$group": {"_id": "$postal", "count": {"$sum": 1}}},
            ]
            cursor  = self.bot.suspect_logs.aggregate(pipeline)
            results = await cursor.to_list(length=1000)

            heatmap_data = {r["_id"]: r["count"] for r in results}

            if not heatmap_data:
                await interaction.followup.send(
                    "❌ No historical crime data with valid coordinates found to generate a heatmap.", ephemeral=True
                )
                return

            loop   = asyncio.get_running_loop()
            buffer = await loop.run_in_executor(
                None, draw_heatmap_overlay, self.bot.erlc_graph, heatmap_data
            )

            file  = discord.File(fp=buffer, filename="heatmap.png")
            embed = discord.Embed(
                title="<:LAPD_Metropolitan:1495867271501975552> Metropolitan Crime Heatmap",
                description="Visual representation of spatial criminal density based on historical intelligence logs.",
                color=discord.Color.red(),
            )
            embed.set_image(url="attachment://heatmap.png")
            embed.set_footer(text="S.I.M.O.N. v2.1 • Spatial Intelligence")

            await interaction.followup.send(embed=embed, file=file)

        except Exception as e:
            print(f"[HEATMAP ERROR] {e}")
            await interaction.followup.send(
                "An error occurred while generating the crime heatmap."
            )

async def setup(bot):
    await bot.add_cog(Simon(bot))