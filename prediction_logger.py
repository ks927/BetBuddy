# prediction_logger.py
# Parses the LLM's structured analysis output to extract the pick,
# prompts the user to confirm logging, and saves to the predictions
# table in sports.db.
#
# Called automatically at the end of query.py after streaming finishes.
# Also works as a standalone manual entry tool if auto-parsing fails.
#
# Usage (manual entry):
#   python3 prediction_logger.py

import sqlite3
import json
import re
import os
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(__file__), "db", "sports.db")


# ── ANSI COLOR CODES ──────────────────────────────────────────────────────────

CYAN = "\033[1;36m"
GREEN = "\033[1;32m"
RED = "\033[1;31m"
RESET = "\033[0m"


# ── INTERACTIVE PROMPT ────────────────────────────────────────────────────────

def maybe_log_prediction(response_text, away_team, home_team, game_date, odds_snapshot=None):
    """Prompt the user to log picks. Handles single or multiple recommendations."""
    print()

    picks = parse_all_picks(response_text)

    if not picks:
        # Check for NO EDGE — PASS
        if re.search(r'NO\s+EDGE\s*[—–-]\s*PASS', response_text.upper()):
            picks = [{"market": "pass", "pick": "NO EDGE — PASS", "confidence": "LOW"}]
        else:
            try:
                answer = input(f"{CYAN}Log this pick? (y/n): {RESET}").strip().lower()
            except (EOFError, KeyboardInterrupt):
                print()
                return
            if answer == "y":
                print(f"{RED}✗ Could not parse pick from analysis.{RESET}")
                print("  You can log manually with: python3 prediction_logger.py")
            return

    if len(picks) == 1:
        # Single recommendation — simple y/n
        p = picks[0]
        print(f"  Parsed: {CYAN}{p['pick']}{RESET} ({p['confidence']}) — {p['market']}")
        try:
            answer = input(f"{CYAN}Log this pick? (y/n): {RESET}").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if answer != "y":
            print("Pick not logged.")
            return
        to_log = [p]

    else:
        # Multiple recommendations — let user choose
        print(f"  Found {len(picks)} recommendations:\n")
        for i, p in enumerate(picks, 1):
            print(f"    {CYAN}{i}.{RESET} {p['pick']} ({p['confidence']}) — {p['market']}")
        print()

        try:
            answer = input(
                f"{CYAN}Log which? (1/2/both/n): {RESET}"
            ).strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return

        if answer == "n":
            print("No picks logged.")
            return
        elif answer == "both" or answer == "b":
            to_log = picks
        elif answer in [str(i) for i in range(1, len(picks) + 1)]:
            to_log = [picks[int(answer) - 1]]
        else:
            print("Invalid choice. No picks logged.")
            return

    # Save selected picks
    for p in to_log:
        save_prediction(
            game_date=game_date,
            away_team=away_team,
            home_team=home_team,
            market=p["market"],
            pick=p["pick"],
            confidence=p["confidence"],
            odds_snapshot=odds_snapshot,
        )
        print(f"{GREEN}✓ Logged: {p['pick']} ({p['confidence']}) — {p['market']}{RESET}")


# ── PICK PARSER ───────────────────────────────────────────────────────────────
# Extracts market, pick line, and confidence from the LLM's structured
# output. Tuned for prompt v4's conclusion format (Format A/B/C/D).
#
# If you change the prompt template, you may need to adjust these patterns.

def parse_all_picks(response_text):
    """
    Extract all recommendations from the LLM response.
    Returns a list of dicts, each with keys: market, pick, confidence.
    Returns empty list if nothing could be parsed.
    """
    text = response_text.upper()

    # ── Strategy 1: Split on explicit RECOMMENDATION boundaries ──
    # Handles "RECOMMENDATION 1:", "RECOMMENDATION 2:", "RECOMMENDATION:" etc.
    rec_splits = re.split(r'(?=RECOMMENDATION\s*\d*\s*:)', text)
    rec_chunks = [chunk for chunk in rec_splits if chunk.strip().startswith("RECOMMENDATION")]

    if rec_chunks:
        picks = []
        for chunk in rec_chunks:
            parsed = parse_single_pick(chunk)
            if parsed:
                picks.append(parsed)
        if picks:
            return picks

    # ── Strategy 2: Extract the conclusion section and parse verdict lines ──
    # The model sometimes writes prose with "SPREAD VERDICT" and "TOTAL VERDICT"
    # instead of numbered recommendations. Isolate the conclusion to avoid
    # matching spread numbers from the odds/stats sections earlier in the response.
    conclusion = _extract_conclusion(text)
    if conclusion:
        picks = _parse_verdict_prose(conclusion)
        if picks:
            return picks
        # Try as a single pick from just the conclusion
        single = parse_single_pick(conclusion)
        if single:
            return [single]

    return []


def _extract_conclusion(text):
    """Pull out the conclusion section from the full response."""
    # Look for section 8 header or CONCLUSION keyword
    patterns = [
        r'(\*{0,2}8\.?\s*CONCLUSION\*{0,2}.*)',
        r'(CONCLUSION\s*\*{0,2}\s*\n.*)',
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return None


def _parse_verdict_prose(conclusion_text):
    """
    Parse spread and total verdicts from prose-style conclusions.
    Handles output like:
        'SPREAD VERDICT IS LEAN MONMOUTH -1.5 AT MEDIUM CONFIDENCE.'
        'TOTAL VERDICT IS LEAN OVER 152.5 AT MEDIUM CONFIDENCE.'
    """
    picks = []

    # Split into sentences — but don't break on decimal points in numbers.
    # Replace decimal points temporarily, split on real sentence endings, restore.
    protected = re.sub(r'(\d)\.(\d)', r'\1<DOT>\2', conclusion_text)
    sentences = re.split(r'[.\n]', protected)
    sentences = [s.replace('<DOT>', '.').strip() for s in sentences if s.strip()]

    spread_pick = None
    total_pick = None

    for sentence in sentences:
        s = sentence.strip()
        if not s:
            continue

        # Look for spread verdict
        if re.search(r'SPREAD\s+VERDICT', s) and not spread_pick:
            parsed = parse_single_pick(s)
            if parsed:
                parsed["market"] = "spread"
                spread_pick = parsed

        # Look for total verdict
        elif re.search(r'TOTAL\s+VERDICT', s) and not total_pick:
            # Check for "NO EDGE" on totals
            if re.search(r'NO\s+EDGE', s):
                continue
            parsed = parse_single_pick(s)
            if parsed:
                parsed["market"] = "total"
                total_pick = parsed

    if spread_pick:
        picks.append(spread_pick)
    if total_pick:
        picks.append(total_pick)

    return picks


# Words that appear before team names but aren't part of the name.
_SPREAD_NOISE = {
    "BET", "TAKE", "PLAY", "LEAN", "LEANING", "PICK", "RECOMMEND",
    "LIKE", "IS", "TO", "THE", "ON", "AT", "RECOMMENDATION", "FORMAT",
    "SPREAD", "VERDICT", "VERDICT:", "FINAL", "COVER",
    "DRAFTKINGS", "FANDUEL", "BETMGM", "CURRENT",
    "A", "B", "C", "D", "1:", "2:", "3:", "1", "2", "3",
}


def _extract_spread(text):
    """
    Find a spread pick like 'CAMPBELL +1.5' by locating the +/- number
    and looking backwards for the team name (1-4 words). Strips action
    words and prompt formatting from the front.

    Returns (team_name, spread) or None.
    """
    for m in re.finditer(r'([+-]\d+\.?\d*)', text):
        spread = m.group(1)
        before = text[:m.start()].rstrip()
        # Strip markdown bold markers and split into words
        before = before.replace("*", "")
        words = before.split()
        candidate = words[-4:] if len(words) >= 4 else words[:]
        while candidate and (candidate[0] in _SPREAD_NOISE or candidate[0].rstrip(":") in _SPREAD_NOISE):
            candidate.pop(0)
        if candidate:
            return " ".join(candidate), spread
    return None


def parse_single_pick(text):
    """
    Parse a single recommendation section. Text should already be uppercased.
    Returns dict with keys: market, pick, confidence — or None on failure.
    """
    # ── Confidence ──
    # Handles multiple formats the model produces:
    #   "at MEDIUM CONFIDENCE"   → (HIGH|MEDIUM|LOW) CONFIDENCE
    #   "CONFIDENCE: MEDIUM"     → CONFIDENCE[:\s]*(HIGH|MEDIUM|LOW)
    #   "— MEDIUM" or "- HIGH"  → [—–-]\s*(HIGH|MEDIUM|LOW)
    #   "(MEDIUM)" or "(HIGH)"  → \(\s*(HIGH|MEDIUM|LOW)\s*\)
    confidence = None
    conf_match = re.search(r'(HIGH|MEDIUM|LOW)\s+CONFIDENCE', text)
    if not conf_match:
        conf_match = re.search(r'CONFIDENCE[:\s]*(HIGH|MEDIUM|LOW)', text)
    if not conf_match:
        conf_match = re.search(r'[—–\-]\s*(HIGH|MEDIUM|LOW)\b', text)
    if not conf_match:
        conf_match = re.search(r'\(\s*(HIGH|MEDIUM|LOW)\s*\)', text)
    if conf_match:
        confidence = conf_match.group(1)

    # ── Market + pick ──
    market = None
    pick = None

    tot_match = re.search(r'(OVER|UNDER)\s*(\d+\.?\d*)', text)
    spr_result = _extract_spread(text)

    if tot_match and spr_result:
        # Both present — first one mentioned is the primary pick
        spr_pos = text.find(spr_result[1])
        if tot_match.start() < spr_pos:
            market = "total"
            pick = f"{tot_match.group(1)} {tot_match.group(2)}"
        else:
            market = "spread"
            pick = f"{spr_result[0]} {spr_result[1]}"
    elif tot_match:
        market = "total"
        pick = f"{tot_match.group(1)} {tot_match.group(2)}"
    elif spr_result:
        market = "spread"
        pick = f"{spr_result[0]} {spr_result[1]}"

    if not market or not pick or not confidence:
        return None

    # Clean up — title case team names for spread picks
    if market == "spread":
        parts = pick.rsplit(" ", 1)
        if len(parts) == 2:
            pick = f"{parts[0].title()} {parts[1]}"
    else:
        pick = pick.upper()

    return {
        "market": market,
        "pick": pick,
        "confidence": confidence,
    }


def parse_pick(response_text):
    """Parse just the first recommendation. Used for backwards compatibility."""
    picks = parse_all_picks(response_text)
    return picks[0] if picks else None


# ── DATABASE ──────────────────────────────────────────────────────────────────

def save_prediction(game_date, away_team, home_team, market, pick, confidence, odds_snapshot=None):
    """Insert a prediction row into the database."""
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        INSERT INTO predictions
            (game_date, away_team, home_team, market, pick, confidence, odds_snapshot, predicted_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            game_date,
            away_team,
            home_team,
            market,
            pick,
            confidence,
            json.dumps(odds_snapshot) if odds_snapshot else None,
            datetime.now().isoformat(),
        ),
    )
    conn.commit()
    conn.close()


# ── MANUAL ENTRY ──────────────────────────────────────────────────────────────
# Fallback for when auto-parsing fails. Run directly to log a pick by hand.

def manual_log():
    print("\n── Manual Pick Logger ──\n")
    away = input("Away team: ").strip()
    home = input("Home team: ").strip()
    date = input("Game date (YYYY-MM-DD): ").strip()
    market = input("Market (spread/total): ").strip().lower()
    pick = input("Pick (e.g. 'Duke -3.5' or 'OVER 145.5'): ").strip()
    confidence = input("Confidence (HIGH/MEDIUM/LOW): ").strip().upper()

    if market not in ("spread", "total"):
        print("Invalid market.")
        return
    if confidence not in ("HIGH", "MEDIUM", "LOW"):
        print("Invalid confidence.")
        return

    save_prediction(date, away, home, market, pick, confidence)
    print(f"{GREEN}✓ Logged: {pick} ({confidence}) — {market}{RESET}")


if __name__ == "__main__":
    manual_log()