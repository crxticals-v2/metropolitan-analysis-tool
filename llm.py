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
- Do NOT include any preambles, postambles, conversational filler, or introductory text.
- Only nodes with robable=true are valid targets.
- All other nodes are traversal only; never predict them.
- Do NOT simulate real-world behaviour (medical, retreat, fear, policing delay, negotiation, etc).
- Suspects always continue criminal activity unless arrested/disconnected.
- Ignore injury, damage, and "safe zones".
- No ethical reasoning, only POI selection.
- Output = ranking of criminal objectives, not travel simulation.
- Never mention chaos factor/lack of unWL units online in your analysis.
- All targets MUST be returned as node IDs (e.g. N-204), never POI names.
Return ONLY the JSON object in this exact format (do not add any surrounding text):
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

# Pre-compile for micro-optimization during parsing
JSON_BLOCK_RE = re.compile(r"```json|```")

async def call_llm(prompt: str) -> dict | None:
    """
    Call the Gemini API and return a parsed JSON dict.
    Retries up to 6 times with exponential back-off on 503 / network errors.
    Returns None on unrecoverable failure.
    """
    full_prompt = f"{_SYSTEM_INSTRUCTION}\n\n{prompt}"
    
    payload = {
        "contents": [
            {
                "role": "user",
                "parts": [{"text": full_prompt}],
            }
        ],
        "generationConfig": {
            "temperature": 0.2,
            "response_mime_type": "application/json"
        },
    }

    url     = f"{LLM_API_URL}?key={LLM_API_KEY}"
    headers = {"Content-Type": "application/json"}
    timeout = aiohttp.ClientTimeout(total=30)
    connector = aiohttp.TCPConnector(ssl=False)

    # Create session ONCE outside the loop to utilize connection pooling
    async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
        for attempt in range(6):
            try:
                async with session.post(url, headers=headers, json=payload) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        
                        # Extract text from the response
                        parts = data.get("candidates", [{}])[0].get("content", {}).get("parts", [])
                        text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
                        
                        # Robust JSON extraction: Find the first '{' and last '}' to strip preambles/postambles
                        start_idx = text.find('{')
                        end_idx = text.rfind('}')
                        
                        json_content = text[start_idx:end_idx+1] if start_idx != -1 and end_idx != -1 else ""
                        if not json_content:
                            print("[LLM ERROR] LLM returned empty content for JSON parsing.")
                            return None

                        parsed = json.loads(json_content)
                        if not isinstance(parsed, dict):
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
