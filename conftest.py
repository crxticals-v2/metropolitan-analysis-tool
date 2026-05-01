"""
conftest.py  –  Shared fixtures for the AI Suspect bot test suite.

Sets all required environment variables BEFORE any bot module is imported,
so config.py never raises RuntimeError during testing.
"""

import os
import sys
import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

# ── 1. Inject fake env vars before any bot import ────────────────────────────
os.environ.setdefault("DISCORD_TOKEN",        "test_discord_token")
os.environ.setdefault("MONGO_URI",            "mongodb://localhost:27017")
os.environ.setdefault("GEMINI_API_KEY",       "test_gemini_key")
os.environ.setdefault("WATCHLIST_CHANNEL_ID", "1234567890123456789")

# ── 2. Add project root to sys.path so all bot modules resolve ───────────────
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


# ─────────────────────────────────────────────────────────────────────────────
# DISCORD MOCK HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def make_member(
    user_id: int = 111,
    display_name: str = "TestOfficer",
    mention: str = "<@111>",
    roles: list = None,
) -> MagicMock:
    """Return a realistic discord.Member mock."""
    member                    = MagicMock()
    member.id                 = user_id
    member.display_name       = display_name
    member.mention            = mention
    member.roles              = roles or []
    member.display_avatar.url = "https://cdn.discordapp.com/embed/avatars/0.png"
    return member


def make_role(name: str, position: int = 5) -> MagicMock:
    role          = MagicMock()
    role.name     = name
    role.position = position
    role.mention  = f"<@&{hash(name) % 10**18}>"
    role.members  = []
    return role


def make_channel(channel_id: int = 999, name: str = "test-channel") -> AsyncMock:
    channel      = AsyncMock()
    channel.id   = channel_id
    channel.name = name
    # send() is async and returns a message mock
    sent_message      = MagicMock()
    sent_message.id   = 888777666
    sent_message.add_reaction = AsyncMock()
    channel.send      = AsyncMock(return_value=sent_message)
    channel.add_reaction = AsyncMock()
    return channel


def make_guild(guild_id: int = 777) -> MagicMock:
    guild              = MagicMock()
    guild.id           = guild_id
    guild.name         = "Test Guild"
    guild.member_count = 10
    guild.members      = []
    guild.chunk        = AsyncMock()

    metro_role         = make_role("Metropolitan Division")
    swat_role          = make_role("Special Weapons and Tactics Team")
    training_ping_role = make_role("[𝐌𝐃] Awaiting Training Ping")
    inspector_role     = make_role("Metro Chief Inspector")

    def get_role(name: str):
        role_map = {
            "Metropolitan Division":             metro_role,
            "Special Weapons and Tactics Team":  swat_role,
            "[𝐌𝐃] Awaiting Training Ping":       training_ping_role,
            "Metro Chief Inspector":              inspector_role,
        }
        return role_map.get(name)

    guild.roles = [metro_role, swat_role, training_ping_role, inspector_role]
    # discord.utils.get uses an attribute scan; patch get on the guild
    guild.get_role = get_role
    return guild


def make_interaction(
    user: MagicMock = None,
    guild: MagicMock = None,
    channel: AsyncMock = None,
) -> AsyncMock:
    """Return a fully-mocked discord.Interaction."""
    interaction                       = AsyncMock()
    interaction.user                  = user or make_member()
    interaction.guild                 = guild or make_guild()
    interaction.channel               = channel or make_channel()
    interaction.created_at            = datetime.datetime(2025, 1, 15, 12, 0, 0)
    interaction.response.defer        = AsyncMock()
    interaction.response.send_message = AsyncMock()
    interaction.response.send_modal   = AsyncMock()
    interaction.followup.send         = AsyncMock()
    return interaction


# ─────────────────────────────────────────────────────────────────────────────
# MONGO MOCK
# ─────────────────────────────────────────────────────────────────────────────

def make_mongo_collection(find_results: list = None, aggregate_results: list = None):
    """Return an AsyncMock that behaves like a Motor collection."""
    col = AsyncMock()

    # insert_one
    col.insert_one = AsyncMock(return_value=MagicMock(inserted_id="fake_id"))

    # find() → cursor
    cursor              = AsyncMock()
    cursor.sort         = MagicMock(return_value=cursor)
    cursor.limit        = MagicMock(return_value=cursor)
    cursor.to_list      = AsyncMock(return_value=find_results or [])
    col.find            = MagicMock(return_value=cursor)

    # find_one
    col.find_one        = AsyncMock(return_value=None)

    # aggregate() → cursor
    agg_cursor          = AsyncMock()
    agg_cursor.to_list  = AsyncMock(return_value=aggregate_results or [])
    col.aggregate       = MagicMock(return_value=agg_cursor)

    # update_one / upsert
    col.update_one      = AsyncMock()

    return col


# ─────────────────────────────────────────────────────────────────────────────
# BOT MOCK
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def mock_bot():
    """A minimal MetroBot-shaped mock, suitable for both cogs."""
    from graph import ERLCGraph
    from heatmap import CrimeHeatmap
    from config import MAP_JSON_PATH

    bot                         = MagicMock()
    bot.erlc_graph              = ERLCGraph(MAP_JSON_PATH)
    bot.crime_heatmap           = CrimeHeatmap()
    bot.suspect_logs            = make_mongo_collection()
    bot.bot_state               = make_mongo_collection()
    bot.request_metro_cooldowns = {}
    bot.watchlist_channel_id    = 1234567890123456789

    # mongo_client["erlc_database"]["collection"]
    db_mock                     = MagicMock()
    db_mock.__getitem__         = lambda self, name: make_mongo_collection()
    bot.mongo_client.__getitem__= MagicMock(return_value=db_mock)

    bot.get_channel             = MagicMock(return_value=make_channel())
    bot.get_cog                 = MagicMock(return_value=None)
    bot.user                    = make_member(user_id=1, display_name="MetroBot")
    return bot


@pytest.fixture
def simon_cog(mock_bot):
    """Instantiated Simon cog with mocked bot."""
    from simon import Simon
    return Simon(mock_bot)


@pytest.fixture
def operations_cog(mock_bot):
    """Instantiated Operations cog with mocked bot."""
    from operations import Operations
    cog = Operations(mock_bot)
    # Patch internal mongo collections with controllable mocks
    cog.user_links  = make_mongo_collection()
    cog.metro_cases = make_mongo_collection()
    return cog


@pytest.fixture
def interaction():
    return make_interaction()


# ─────────────────────────────────────────────────────────────────────────────
# RE-EXPORT helpers for use in individual test modules
# ─────────────────────────────────────────────────────────────────────────────
__all__ = [
    "make_member", "make_role", "make_channel", "make_guild",
    "make_interaction", "make_mongo_collection",
]
