# prediction_logger.py
# Parses the LLM's structured analysis output to extract the pick,
# prompts the user to confirm logging, and saves to the predictions
# table in sports.db.
#
# Called automatically at the end of query.py after streaming finishes.
# Also works as a standalone manual entry tool if auto-parsing fails.
#
# v3 changes:
#   - save_prediction() and maybe_log_prediction() now accept analysis_text
#   - Full LLM analysis is stored alongside the pick for retrospective grading
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
 
def maybe_log_prediction(response_text, away_team, home_team, game_date, odds_snapshot=None, analysis_text=None):
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
            analysis_text=analysis_text,
        )
        print(f"{GREEN}✓ Logged: {p['pick']} ({p['confidence']}) — {p['market']}{RESET}")
 
 
# ── PICK PARSER ───────────────────────────────────────────────────────────────
# Extracts market, pick line, and confidence from the LLM's structured
# output. Tuned for prompt v5/v6's conclusion format.
#
# Parse order:
#   1. Explicit RECOMMENDATION lines (cleanest)
#   2. VERDICT lines with LEAN (fallback when model narrates instead of recommending)
#   3. Prose parsing from conclusion section (last resort)
 
def parse_all_picks(response_text):
    """
    Extract all recommendations from the LLM response.
    Returns a list of dicts, each with keys: market, pick, confidence.
    Returns empty list if nothing could be parsed.
 
    Strategy:
      1. Parse explicit RECOMMENDATION lines (cleanest)
      2. Parse VERDICT lines (fallback)
      3. MERGE: if recommendations found fewer picks than verdicts suggest,
         fill in the missing ones from verdict parsing
      4. Prose parsing (last resort)
    """
    text = response_text.upper()
    conclusion = _extract_conclusion(text)
    source = conclusion if conclusion else text
 
    # ── Strategy 1: Split on explicit RECOMMENDATION boundaries ──
    rec_picks = []
    rec_splits = re.split(r'(?=RECOMMENDATION\s*\d*\s*:)', text)
    rec_chunks = [chunk for chunk in rec_splits if chunk.strip().startswith("RECOMMENDATION")]
 
    if rec_chunks:
        for chunk in rec_chunks:
            parsed = parse_single_pick(chunk)
            if parsed:
                rec_picks.append(parsed)
 
    # ── Strategy 2: Parse VERDICT lines ──
    verdict_picks = _parse_verdict_lines(source, text)
 
    # ── Strategy 3: Merge ──
    # If recommendations found some picks but verdicts found more,
    # add the missing ones. This handles the case where the model
    # writes "SPREAD VERDICT: Siena at MEDIUM / TOTALS VERDICT: OVER at HIGH"
    # but then only produces one RECOMMENDATION line.
    if rec_picks and verdict_picks:
        rec_markets = {p["market"] for p in rec_picks}
        for vp in verdict_picks:
            if vp["market"] not in rec_markets:
                rec_picks.append(vp)
        return _dedupe_picks(rec_picks)
 
    if rec_picks:
        return _dedupe_picks(rec_picks)
 
    if verdict_picks:
        return _dedupe_picks(verdict_picks)
 
    # ── Strategy 4: Prose parsing from conclusion (last resort) ──
    if conclusion:
        picks = _parse_verdict_prose(conclusion)
        if picks:
            return _dedupe_picks(picks)
        single = parse_single_pick(conclusion)
        if single:
            return [single]
 
    return []
 
 
def _dedupe_picks(picks):
    """Remove duplicate picks (same market + pick). Keep first occurrence."""
    seen = set()
    result = []
    for p in picks:
        key = (p["market"], p["pick"])
        if key not in seen:
            seen.add(key)
            result.append(p)
    return result
 
 
def _extract_conclusion(text):
    """Pull out the conclusion section from the full response."""
    # Look for section 6 or 8 header, or CONCLUSION keyword
    patterns = [
        r'(\*{0,2}6\.?\s*CONCLUSION\*{0,2}.*)',
        r'(\*{0,2}8\.?\s*CONCLUSION\*{0,2}.*)',
        r'(SPREAD\s+VERDICT:.*)',
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return None
 
 
def _parse_verdict_lines(text, full_text=None):
    """
    Parse SPREAD VERDICT and TOTALS VERDICT lines directly.
 
    Handles patterns like:
        SPREAD VERDICT: LEAN BAYLOR -3.5 AT HIGH CONFIDENCE
        SPREAD VERDICT: BAYLOR AT HIGH              (no spread number)
        TOTALS VERDICT: LEAN UNDER 142.5 AT MEDIUM CONFIDENCE
        TOTALS VERDICT: OVER AT HIGH                (no total number)
        TOTALS VERDICT: NO EDGE
 
    When the verdict line is missing a number (spread or total), we scan
    the full response for RECOMMENDATION lines or posted O/U lines to
    recover the number.
    """
    if full_text is None:
        full_text = text
 
    picks = []
 
    # Find spread verdict
    spread_match = re.search(
        r'SPREAD\s+VERDICT\s*:?\s*(?:LEAN\s+)?(.+?)(?:AT\s+|[—–-]\s*)(HIGH|MEDIUM|LOW)',
        text
    )
    if spread_match:
        raw_pick = spread_match.group(1).strip()
        confidence = spread_match.group(2)
 
        if not re.search(r'NO\s+EDGE', raw_pick):
            spr_result = _extract_spread(raw_pick)
            if spr_result:
                team, spread = spr_result
                pick_str = f"{team.title()} {spread}"
                picks.append({
                    "market": "spread",
                    "pick": pick_str,
                    "confidence": confidence,
                })
            else:
                # Verdict has a team name but no spread number.
                # Try to recover the spread from RECOMMENDATION lines
                # or from the CURRENT LINES section in the full response.
                team_name = raw_pick.strip().rstrip(":").strip()
                # Remove noise words
                for noise in ["LEAN", "LEANING", "SPREAD", "VERDICT"]:
                    team_name = team_name.replace(noise, "").strip()
 
                if team_name:
                    # Look for this team + a spread number anywhere in the response
                    recovered = _extract_spread(team_name)
                    if not recovered:
                        # Search the full text for "TeamName +/-N.N"
                        pattern = re.escape(team_name) + r'\s*([+-]\d+\.?\d*)'
                        rec_match = re.search(pattern, full_text)
                        if rec_match:
                            recovered = (team_name, rec_match.group(1))
 
                    if recovered:
                        team, spread = recovered
                        pick_str = f"{team.title()} {spread}"
                        picks.append({
                            "market": "spread",
                            "pick": pick_str,
                            "confidence": confidence,
                        })
 
    # Find totals verdict
    totals_match = re.search(
        r'TOTAL[S]?\s+VERDICT\s*:?\s*(?:LEAN\s+)?(.+?)(?:AT\s+|[—–-]\s*)(HIGH|MEDIUM|LOW)',
        text
    )
    if totals_match:
        raw_pick = totals_match.group(1).strip()
        confidence = totals_match.group(2)
 
        if not re.search(r'NO\s+EDGE', raw_pick):
            tot_match = re.search(r'(OVER|UNDER)\s*(\d+\.?\d*)', raw_pick)
            if tot_match:
                picks.append({
                    "market": "total",
                    "pick": f"{tot_match.group(1)} {tot_match.group(2)}",
                    "confidence": confidence,
                })
            else:
                # Verdict has OVER or UNDER but no number.
                # Recover the total from the full response.
                direction_match = re.search(r'(OVER|UNDER)', raw_pick)
                if direction_match:
                    direction = direction_match.group(1)
                    # Look for "O/U N" or "OVER/UNDER N" in the full text
                    total_num = None
                    # Try posted O/U line first
                    ou_match = re.search(r'O/U\s+(\d+\.?\d*)', full_text)
                    if ou_match:
                        total_num = ou_match.group(1)
                    # Try "OVER N" or "UNDER N" from a RECOMMENDATION line
                    if not total_num:
                        rec_tot = re.search(r'RECOMMENDATION.*?' + direction + r'\s+(\d+\.?\d*)', full_text)
                        if rec_tot:
                            total_num = rec_tot.group(1)
                    # Try posted total from the lines section
                    if not total_num:
                        posted_match = re.search(r'POSTED\s+O/U[:\s]*(\d+\.?\d*)', full_text)
                        if posted_match:
                            total_num = posted_match.group(1)
                    # Try any "total" followed by a number
                    if not total_num:
                        any_total = re.search(r'TOTAL[:\s]+(\d{3}\.?\d*)', full_text)
                        if any_total:
                            total_num = any_total.group(1)
 
                    if total_num:
                        picks.append({
                            "market": "total",
                            "pick": f"{direction} {total_num}",
                            "confidence": confidence,
                        })
 
    return picks
 
 
def _parse_verdict_prose(conclusion_text):
    """
    Parse spread and total verdicts from prose-style conclusions.
    Handles output like:
        'SPREAD VERDICT IS LEAN MONMOUTH -1.5 AT MEDIUM CONFIDENCE.'
        'TOTAL VERDICT IS LEAN OVER 152.5 AT MEDIUM CONFIDENCE.'
    """
    picks = []
 
    # Split into sentences — but don't break on decimal points in numbers.
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
    confidence = None
    conf_match = re.search(r'(HIGH|MEDIUM|LOW)\s+CONFIDENCE', text)
    if not conf_match:
        conf_match = re.search(r'CONFIDENCE[:\s]*(HIGH|MEDIUM|LOW)', text)
    if not conf_match:
        conf_match = re.search(r'[—–\-]\s*(HIGH|MEDIUM|LOW)\b', text)
    if not conf_match:
        conf_match = re.search(r'\(\s*(HIGH|MEDIUM|LOW)\s*\)', text)
    if not conf_match:
        # Bare confidence word near end of line (last resort)
        conf_match = re.search(r'AT\s+(HIGH|MEDIUM|LOW)\b', text)
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
 
def save_prediction(game_date, away_team, home_team, market, pick, confidence, odds_snapshot=None, analysis_text=None):
    """Insert a prediction row into the database."""
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS predictions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            game_date TEXT NOT NULL,
            away_team TEXT NOT NULL,
            home_team TEXT NOT NULL,
            market TEXT NOT NULL,
            pick TEXT NOT NULL,
            confidence TEXT NOT NULL,
            odds_snapshot TEXT,
            predicted_at TEXT NOT NULL,
            actual_score_away INTEGER,
            actual_score_home INTEGER,
            result TEXT,
            graded_at TEXT,
            analysis_text TEXT
        )
        """,
    )
    conn.execute(
        """
        INSERT INTO predictions
            (game_date, away_team, home_team, market, pick, confidence, odds_snapshot, predicted_at, analysis_text)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            analysis_text,
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