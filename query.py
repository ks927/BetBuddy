# query.py
# Entry point for BetBuddy. Takes a natural language query and returns
# a full structured betting analysis.
#
# Usage:
#   python3 query.py "Duke vs Syracuse"
#   python3 query.py "Kansas vs Baylor Saturday"
#   python3 query.py "UConn @ Marquette"
#
# Flow:
#   1. Parse two team name fragments from the query
#   2. retrieval.build_context() resolves full team names, finds the game,
#      and assembles odds + stats into a formatted context block
#   3. prompt.build_prompt() wraps that context in the structured 6-section
#      template that forces the model to reason before concluding
#   4. Ollama streams the response locally — no API costs, nothing leaves
#      your machine
#
# Requires Ollama to be running with llama3.2 pulled:
#   ollama serve
#   ollama pull llama3.2

import sys
import re
import ollama
from retrieval import build_context
from prompt import build_prompt, SYSTEM_PROMPT

MODEL = "llama3.1:8b"

# Words to strip when isolating team name fragments from the query.
# "Duke vs Syracuse on Saturday night" -> ["duke", "syracuse"]
NOISE_WORDS = {
    "on", "this", "next", "the", "a", "an",
    "monday", "tuesday", "wednesday", "thursday", "friday",
    "saturday", "sunday", "tonight", "tomorrow",
    "night", "afternoon", "evening", "morning",
    "game", "match", "tip", "tipoff",
}


# ── PARSE TEAM FRAGMENTS ──────────────────────────────────────────────────────
# Split on vs/versus/@ to get two raw fragments, then strip noise words.
# We keep multi-word fragments intact — "Ohio State" should stay as one
# query, not get split further. retrieval.find_team_name() handles the
# actual fuzzy matching against the database.

def parse_teams(query: str):
    q = query.strip().lower()

    # Split on vs / versus / @
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


# ── STREAM FROM OLLAMA ────────────────────────────────────────────────────────
# We stream the response so output appears word-by-word rather than making
# you wait 30+ seconds for the full analysis. The model is thinking out loud
# through 6 sections — streaming makes that feel much more natural.
# We also capture the full response text so we could log it later.

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

    print("\n" + "─" * 70)
    return "".join(full_response)


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

    # Build context — this hits the database and assembles all the data
    context = build_context(team1_fragment, team2_fragment)

    # build_context() returns a plain error string if something went wrong
    # (team not found, game not in DB, etc.) — check for that before
    # sending anything to the model
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
    stream_analysis(messages)


if __name__ == "__main__":
    main()