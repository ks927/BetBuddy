# query.py
# Entry point for BetBuddy. Takes a natural language query and returns
# a full structured betting analysis.
#
# v3 changes:
#   - Swapped from local Ollama (llama3.1:8b) to Gemini 2.5 Flash API
#   - Uses google-genai SDK with streaming
#   - Reads GEMINI_API_KEY from .env
#   - Section 1 pre-rendered from DB, LLM starts at section 2
#   - Temperature set to 0.3 for consistent number reproduction
#
# Setup:
#   pip install google-genai
#   Add GEMINI_API_KEY=your_key to .env
#   Get a free key at: https://aistudio.google.com/app/apikey
#
# Usage:
#   python3 query.py "Duke vs Syracuse"
#   python3 query.py "Kansas vs Baylor Saturday"
#   python3 query.py "UConn @ Marquette"
 
import sys
import re
import os
import sqlite3
from datetime import date
from dotenv import load_dotenv
from google import genai
from google.genai import types
from retrieval import build_context
from prompt import build_prompt, SYSTEM_PROMPT
from prediction_logger import maybe_log_prediction
 
load_dotenv()
 
MODEL = "gemini-2.5-flash"
 
client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
 
# Words to strip when isolating team name fragments from the query.
NOISE_WORDS = {
    "on", "this", "next", "the", "a", "an",
    "monday", "tuesday", "wednesday", "thursday", "friday",
    "saturday", "sunday", "tonight", "tomorrow",
    "night", "afternoon", "evening", "morning",
    "game", "match", "tip", "tipoff",
}
 
 
# ── ANSI COLOR CODES ──────────────────────────────────────────────────────────
 
class Colors:
    GREEN = "\033[92m"
    RED = "\033[91m"
    ORANGE = "\033[38;5;208m"
    YELLOW = "\033[93m"
    BOLD = "\033[1m"
    RESET = "\033[0m"
 
 
# ── COLORIZE OUTPUT ───────────────────────────────────────────────────────────
 
def colorize(text):
    # Color RECOMMENDATION lines (including the ** markdown bold markers)
    text = re.sub(
        r"(\*{0,2}RECOMMENDATION\s*\d*:?\*{0,2})",
        f"{Colors.GREEN}{Colors.BOLD}\\1{Colors.RESET}",
        text,
        flags=re.IGNORECASE,
    )
 
    # Color NO EDGE — PASS
    text = re.sub(
        r"(NO EDGE\s*[—–-]\s*PASS(?:\s+ON THIS GAME)?)",
        f"{Colors.RED}{Colors.BOLD}\\1{Colors.RESET}",
        text,
        flags=re.IGNORECASE,
    )
 
    # Color confidence levels
    text = re.sub(
        r"\bHIGH\s+CONFIDENCE\b",
        f"{Colors.RED}{Colors.BOLD}HIGH CONFIDENCE{Colors.RESET}",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\bMEDIUM\s+CONFIDENCE\b",
        f"{Colors.ORANGE}{Colors.BOLD}MEDIUM CONFIDENCE{Colors.RESET}",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\bLOW\s+CONFIDENCE\b",
        f"{Colors.YELLOW}{Colors.BOLD}LOW CONFIDENCE{Colors.RESET}",
        text,
        flags=re.IGNORECASE,
    )
 
    # Also color standalone confidence after dashes: "— HIGH", "— MEDIUM"
    text = re.sub(
        r"([—–-]\s*)(HIGH)\b",
        f"\\1{Colors.RED}{Colors.BOLD}\\2{Colors.RESET}",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"([—–-]\s*)(MEDIUM)\b",
        f"\\1{Colors.ORANGE}{Colors.BOLD}\\2{Colors.RESET}",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"([—–-]\s*)(LOW)\b",
        f"\\1{Colors.YELLOW}{Colors.BOLD}\\2{Colors.RESET}",
        text,
        flags=re.IGNORECASE,
    )
 
    return text
 
 
# ── PARSE TEAM FRAGMENTS ──────────────────────────────────────────────────────
 
def parse_teams(query: str):
    q = query.strip().lower()
    parts = re.split(r"\s+(?:vs\.?|versus|@)\s+", q, maxsplit=1)
 
    if len(parts) != 2:
        return None
 
    team1 = clean_fragment(parts[0])
    team2 = clean_fragment(parts[1])
 
    if not team1 or not team2:
        return None
 
    return team1, team2
 
 
def clean_fragment(fragment: str) -> str:
    """Strip noise words from a team fragment, preserve the rest."""
    words = fragment.strip().split()
    cleaned = [w for w in words if w not in NOISE_WORDS]
    return " ".join(cleaned).strip()
 
 
# ── GAME DATE LOOKUP ──────────────────────────────────────────────────────────
 
def get_game_date(away_team, home_team):
    try:
        conn = sqlite3.connect("db/sports.db")
        row = conn.execute(
            "SELECT commence_time FROM odds WHERE away_team = ? AND home_team = ? "
            "ORDER BY commence_time ASC LIMIT 1",
            (away_team, home_team),
        ).fetchone()
        conn.close()
        if row:
            return row[0][:10]
    except Exception:
        pass
    return date.today().isoformat()
 
 
# ── STREAM FROM GEMINI ────────────────────────────────────────────────────────
 
def stream_analysis(messages: list[dict]) -> str:
    """Stream response from Gemini 2.5 Flash API."""
    print("\n" + "─" * 70)
 
    # Build contents for Gemini API from our messages format
    # messages is [{"role": "user", "content": "..."}]
    contents = []
    for msg in messages:
        contents.append(
            types.Content(
                role=msg["role"],
                parts=[types.Part.from_text(text=msg["content"])],
            )
        )
 
    full_response = []
 
    stream = client.models.generate_content_stream(
        model=MODEL,
        contents=contents,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            temperature=0.3,
            max_output_tokens=4096,
        ),
    )
 
    for chunk in stream:
        if chunk.text:
            print(chunk.text, end="", flush=True)
            full_response.append(chunk.text)
 
    raw_text = "".join(full_response)
 
    # Print separator and colorized summary
    print("\n" + "─" * 70)
    print(f"\n{Colors.BOLD}── SUMMARY ──{Colors.RESET}\n")
 
    conclusion = extract_conclusion(raw_text)
    if conclusion:
        print(colorize(conclusion))
    else:
        print(colorize(raw_text))
 
    print("─" * 70)
    return raw_text
 
 
def extract_conclusion(text):
    """Extract the conclusion/recommendation section from the full response."""
    patterns = [
        r"(\*{0,2}6\.\s*CONCLUSION\*{0,2}.*)",
        r"(\*{0,2}8\.\s*CONCLUSION\*{0,2}.*)",
        r"(SPREAD\s+VERDICT:.*)",
        r"(RECOMMENDATION.*)",
    ]
 
    for pattern in patterns:
        match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
        if match:
            return match.group(1).strip()
 
    return None
 
 
# ── MAIN ──────────────────────────────────────────────────────────────────────
 
def main():
    if len(sys.argv) < 2:
        print("Usage: python3 query.py \"Team1 vs Team2\"")
        print("Example: python3 query.py \"Duke vs Syracuse\"")
        sys.exit(1)
 
    # Check for API key
    if not os.environ.get("GEMINI_API_KEY"):
        print("✗ GEMINI_API_KEY not found in .env")
        print("  Get a free key at: https://aistudio.google.com/app/apikey")
        print("  Add it to your .env file: GEMINI_API_KEY=your_key_here")
        sys.exit(1)
 
    query = " ".join(sys.argv[1:])
    print(f"\nQuery: {query}")
 
    # Parse team fragments
    parsed = parse_teams(query)
    if not parsed:
        print("\nCouldn't parse two teams from that query.")
        print("Format: \"Team1 vs Team2\" or \"Team1 @ Team2\"")
        sys.exit(1)
 
    team1_fragment, team2_fragment = parsed
    print(f"Looking up: '{team1_fragment}' vs '{team2_fragment}'...")
 
    # Build context — returns (context_string, section1_text)
    result = build_context(team1_fragment, team2_fragment)
 
    # Handle both old (string) and new (tuple) return formats
    if isinstance(result, tuple):
        context, section1_text = result
    else:
        context = result
        section1_text = ""
 
    error_signals = [
        "could not find",
        "no upcoming game",
        "ambiguous team",
    ]
    if any(sig in context.lower() for sig in error_signals):
        print(f"\n{context}")
        sys.exit(1)
 
    # Print section 1 directly from DB data — no LLM involved
    if section1_text:
        print("\n" + "─" * 70)
        print(section1_text)
 
    # Build prompt (LLM starts at section 2) and stream analysis
    messages = build_prompt(context, section1_text, query)
    raw_text = stream_analysis(messages)
 
    # Prepend section 1 to raw_text so the prediction logger can see
    # the full output including the correct spread/total numbers
    full_text = section1_text + "\n\n" + raw_text if section1_text else raw_text
 
    # ── Prediction logging ────────────────────────────────────────────────
    game_date = get_game_date(team1_fragment, team2_fragment)
    maybe_log_prediction(
        response_text=full_text,
        away_team=team1_fragment,
        home_team=team2_fragment,
        game_date=game_date,
        analysis_text=full_text,
    )
 
 
if __name__ == "__main__":
    main()