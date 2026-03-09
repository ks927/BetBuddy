# query.py
# Entry point for BetBuddy. Takes a natural language query and returns
# a full structured betting analysis. After streaming, prompts to log
# the pick to the predictions table for result tracking.
#
# Usage:
#   python3 query.py "Duke vs Syracuse"
#   python3 query.py "Kansas vs Baylor Saturday"
#   python3 query.py "UConn @ Marquette"

import sys
import re
import sqlite3
import ollama
from datetime import date
from retrieval import build_context
from prompt import build_prompt, SYSTEM_PROMPT
from prediction_logger import maybe_log_prediction

MODEL = "llama3.1:8b"

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
# Post-process the full response to add terminal colors for:
#   - RECOMMENDATION lines → green + bold
#   - HIGH CONFIDENCE → red
#   - MEDIUM CONFIDENCE → orange
#   - LOW CONFIDENCE → yellow
#   - NO EDGE — PASS → red

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

    # Color confidence levels — case-insensitive to catch all variations
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
# Pull the game date from the odds table so we can log it with the pick.

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


# ── STREAM FROM OLLAMA ────────────────────────────────────────────────────────
# Stream the response token by token for real-time feel, then colorize
# the full output and reprint it.

def stream_analysis(messages: list[dict]) -> str:
    print("\n" + "─" * 70)

    full_response = []
    all_messages = [{"role": "system", "content": SYSTEM_PROMPT}] + messages
    stream = ollama.chat(
        model=MODEL,
        messages=all_messages,
        stream=True,
    )

    for chunk in stream:
        token = chunk["message"]["content"]
        print(token, end="", flush=True)
        full_response.append(token)

    raw_text = "".join(full_response)

    # Clear the raw output and reprint with colors
    # Move cursor up by the number of lines we printed, then overwrite
    # This is tricky in all terminals, so instead we just print a separator
    # and the colorized version below
    print("\n" + "─" * 70)
    print(f"\n{Colors.BOLD}── SUMMARY ──{Colors.RESET}\n")

    # Extract and colorize just the conclusion section
    conclusion = extract_conclusion(raw_text)
    if conclusion:
        print(colorize(conclusion))
    else:
        # If we can't extract the conclusion, colorize the whole thing
        print(colorize(raw_text))

    print("─" * 70)
    return raw_text


def extract_conclusion(text):
    """Extract the conclusion/recommendation section from the full response."""
    # Look for section 8 or the RECOMMENDATION keyword
    patterns = [
        r"(\*{0,2}8\.\s*CONCLUSION\*{0,2}.*)",
        r"(SPREAD VERDICT:.*)",
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

    # Build context
    context = build_context(team1_fragment, team2_fragment)

    error_signals = [
        "could not find",
        "no upcoming game",
        "ambiguous team",
    ]
    if any(sig in context.lower() for sig in error_signals):
        print(f"\n{context}")
        sys.exit(1)

    # Build prompt and stream analysis
    messages = build_prompt(context, query)
    raw_text = stream_analysis(messages)

    # ── Prediction logging ────────────────────────────────────────────────
    # Resolve the full team names from the context block for clean logging.
    # build_context returns a text block that starts with the matched names;
    # we pass the fragments here and let the logger use what it can.
    # You may want to have build_context also return the matched team names
    # as a tuple — for now we use the fragments as-is.
    game_date = get_game_date(team1_fragment, team2_fragment)
    maybe_log_prediction(
        response_text=raw_text,
        away_team=team1_fragment,
        home_team=team2_fragment,
        game_date=game_date,
    )


if __name__ == "__main__":
    main()