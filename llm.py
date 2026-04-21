import asyncio
import json
import re

import aiohttp

from config import LLM_API_KEY, LLM_API_URL

# ------------------------------------------------------------------
# SYSTEM INSTRUCTION  (defines output schema + game rules)
# ------------------------------------------------------------------

_SYSTEM_INSTRUCTION = """
You are an expert predictive policing AI for ER:LC.
GAME RULES (CRITICAL - MUST FOLLOW):
- Output ONLY JSON in required schema.
- Only nodes with robable=true are valid targets.
- All other nodes are traversal only; never predict them.
- Do NOT simulate real-world behaviour (medical, retreat, fear, policing delay, negotiation, etc).
- Suspects always continue criminal activity unless arrested/disconnected.
- Ignore injury, damage, and "safe zones".
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
You are NOT, and I repeat, NOT, allowed to modify the names of the schema fields.
"""


async def call_llm(prompt: str) -> dict | None:
    """
    Call the Gemini API and return a parsed JSON dict.
    Retries up to 6 times with exponential back-off on 503 / network errors.
    Returns None on unrecoverable failure.
    """
    payload = {
        "contents": [
            {
                "role": "user",
                "parts": [{"text": _SYSTEM_INSTRUCTION + "\n\n" + prompt}],
            }
        ],
        "generationConfig": {"temperature": 0.2},
    }

    url     = f"{LLM_API_URL}?key={LLM_API_KEY}"
    headers = {"Content-Type": "application/json"}
    timeout = aiohttp.ClientTimeout(total=30)
    connector = aiohttp.TCPConnector(ssl=False)

    for attempt in range(6):
        try:
            async with aiohttp.ClientSession(
                timeout=timeout, connector=connector
            ) as session:
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

                        parsed = json.loads(text)
                        print("\n===== PARSED LLM JSON =====")
                        print(parsed)
                        print("===========================\n")

                        if not isinstance(parsed, dict) or "prediction" not in parsed:
                            print("[LLM ERROR] Invalid schema returned from model")
                            return None

                        return parsed

                    elif resp.status == 503:
                        wait = 2 ** attempt
                        print(f"503 overload. Retrying in {wait}s…")
                        await asyncio.sleep(wait)

                    else:
                        print(f"Gemini API Error: {resp.status} – {await resp.text()}")
                        return None

        except Exception as e:
            wait = min(2 ** attempt, 10)
            print(
                f"[LLM ERROR] {type(e).__name__}: {repr(e)} | "
                f"Retrying in {wait}s"
            )
            await asyncio.sleep(wait)

    return None
