import os
from dotenv import load_dotenv

load_dotenv()

TOKEN        = os.getenv("DISCORD_TOKEN")
MONGO_URI    = os.getenv("MONGO_URI")
LLM_API_KEY  = os.getenv("GEMINI_API_KEY")
LLM_API_URL  = (
    "https://generativelanguage.googleapis.com/v1beta"
    "/models/gemini-3.1-flash-lite-preview:generateContent"
)

MAP_JSON_PATH  = "erlc_map.json"
MAP_IMAGE_PATH = "fall_postals.jpg"

if not TOKEN or not MONGO_URI or not LLM_API_KEY:
    raise RuntimeError(
        "Missing required environment variables. Check your .env file."
    )
