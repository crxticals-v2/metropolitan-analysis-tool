"""
main.py – Metropolitan Services Bot
"""

import asyncio
import certifi
import discord
import random
from discord.ext import commands
from motor.motor_asyncio import AsyncIOMotorClient

from config import MAP_JSON_PATH, MONGO_URI, TOKEN, WATCHLIST_CHANNEL_ID
from graph import ERLCGraph
from heatmap import CrimeHeatmap

# ──────────────────────────────────────────────
# BOT CLASS
# ──────────────────────────────────────────────

class MetroBot(commands.Bot):
    def __init__(self):
        intents         = discord.Intents.default()
        intents.members = True
        intents.guilds  = True
        intents.message_content = True

        super().__init__(command_prefix="!", intents=intents)

        # Core engine
        self.erlc_graph   = ERLCGraph(MAP_JSON_PATH)
        self.crime_heatmap = CrimeHeatmap()

        # MongoDB
        self.mongo_client = AsyncIOMotorClient(
            MONGO_URI,
            tls=True,
            tlsCAFile=certifi.where(),
        )
        db                  = self.mongo_client["erlc_database"]
        self.suspect_logs   = db["suspect_logs"]
        self.bot_state      = db["bot_state"]
        # Caches resolved Roblox username → user_id so GCP never has to hit
        # users.roblox.com directly (Cloudflare blocks datacenter IPs).
        # Populated automatically whenever the bot runs locally.
        self.roblox_id_cache = db["roblox_id_cache"]

        # Cooldown tracking: {guild_id: timestamp}
        self.request_metro_cooldowns: dict = {}

    async def setup_hook(self):
        """Load all cogs and optionally sync slash commands."""
        self.watchlist_channel_id = WATCHLIST_CHANNEL_ID
        await self.load_extension("simon")
        await self.load_extension("operations")
        await self.load_extension("handbook")
        await self.load_extension("raffle")

        await self.tree.sync()
        print("✅ Slash commands synced.")

    async def on_ready(self):
        if not hasattr(self, "_presence_set"):
            await self.change_presence(
                activity=discord.Game(name="🚨| MD – Spying for you |")
            )
            self._presence_set = True

        print(f"✅ Logged in as {self.user} ({self.user.id})")


# ──────────────────────────────────────────────
# ENTRY POINT
# ──────────────────────────────────────────────

async def run_bot_with_backoff():
    """Start the bot, retrying transient Discord login/server failures."""
    attempt = 0

    while True:
        bot = MetroBot()
        try:
            await bot.start(TOKEN, reconnect=True)
        except (discord.DiscordServerError, discord.HTTPException, OSError) as exc:
            attempt += 1
            await bot.close()

            # Authentication/configuration problems should fail loudly; retries only help
            # transient 429/5xx/network cases.
            status = getattr(exc, "status", None)
            if status is not None and status < 500 and status != 429:
                raise

            delay = min(300, (2 ** min(attempt, 8)) + random.uniform(0, 5))
            print(f"⚠️ Discord startup failed ({exc!r}). Retrying in {delay:.1f}s.")
            await asyncio.sleep(delay)
        else:
            return


if __name__ == "__main__":
    asyncio.run(run_bot_with_backoff())
