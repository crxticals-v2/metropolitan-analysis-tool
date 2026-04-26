import os
from dotenv import load_dotenv

load_dotenv()

TOKEN        = os.getenv("DISCORD_TOKEN")
MONGO_URI    = os.getenv("MONGO_URI")
LLM_API_KEY  = os.getenv("GEMINI_API_KEY")
ROBLOX_API_KEY = os.getenv("ROBLOX_API_KEY")
LLM_API_URL  = (
    "https://generativelanguage.googleapis.com/v1beta"
    "/models/gemini-3.1-flash-lite-preview:generateContent"
)
WATCHLIST_CHANNEL_ID = int(os.getenv("WATCHLIST_CHANNEL_ID", "1496911809918140426"))

MAP_JSON_PATH  = "erlc_map.json"
MAP_IMAGE_PATH = "fall_postals.jpg"

if not TOKEN or not MONGO_URI or not LLM_API_KEY or not WATCHLIST_CHANNEL_ID:
    raise RuntimeError(
        "Missing required environment variables. Check your .env file."
    )
