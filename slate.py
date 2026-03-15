# slate.py
# Batch-analyzes today's games. Skips games that already have analysis
# in the predictions table (from manual query.py runs). Saves picks
# and analysis text to the DB without interactive prompts.
#
# Usage:
#   python3 slate.py                # analyze today's un-analyzed games
#   make slate                      # same thing
 
import sqlite3
import re
import os
from datetime import datetime, date
from zoneinfo import ZoneInfo
 
from analysis import run_analysis
from prediction_logger import save_prediction
 
DB_PATH = os.path.join(os.path.dirname(__file__), "db", "sports.db")
ET = ZoneInfo("America/New_York")
 
# ── ANSI COLORS ───────────────────────────────────────────────────────────────
GREEN = "\033[1;32m"
RED = "\033[1;31m"
CYAN = "\033[1;36m"
DIM = "\033[2m"
RESET = "\033[0m"
 
 
def normalize_team(name):
    """Simplify team name for matching."""
    return (
        name.lower()
        .strip()
        .replace("state", "st")
        .replace("saint", "st")
        .replace("'", "")
        .replace(".", "")
        .replace("-", " ")
    )
 
 
def teams_match(name_a, name_b):
    a = normalize_team(name_a)
    b = normalize_team(name_b)
    if a == b:
        return True
    if a in b or b in a:
        return True
    a_words = a.split()
    b_words = b.split()
    if a_words and b_words and a_words[-1] == b_words[-1] and len(a_words[-1]) > 3:
        return True
    return False
 
 
def get_todays_games(conn):
    """Get all unique games scheduled for today from the odds table."""
    today_et = datetime.now(ET).date()
 
    cursor = conn.execute("""
        SELECT DISTINCT game_id, home_team, away_team, commence_time
        FROM odds
        WHERE sport = 'basketball_ncaab'
        AND replace(replace(commence_time, 'T', ' '), 'Z', '') > datetime('now', '-12 hours')
        ORDER BY commence_time ASC
    """)
 
    games = []
    for game_id, home, away, commence in cursor.fetchall():
        try:
            tip_utc = datetime.fromisoformat(commence.replace("Z", "+00:00"))
            tip_date = tip_utc.astimezone(ET).date()
            if tip_date == today_et:
                games.append({
                    "game_id": game_id,
                    "home_team": home,
                    "away_team": away,
                    "commence_time": commence,
                })
        except Exception:
            continue
 
    return games
 
 
def game_already_analyzed(conn, home_team, away_team, game_date):
    """Check if we already have an analysis for this game in predictions."""
    rows = conn.execute(
        """
        SELECT id FROM predictions
        WHERE game_date = ? AND analysis_text IS NOT NULL
        """,
        (game_date,),
    ).fetchall()
 
    # Check if any existing prediction matches these teams
    for row in rows:
        pred = conn.execute(
            "SELECT home_team, away_team FROM predictions WHERE id = ?",
            (row[0],),
        ).fetchone()
        if pred:
            if teams_match(pred[0], home_team) and teams_match(pred[1], away_team):
                return True
            if teams_match(pred[0], away_team) and teams_match(pred[1], home_team):
                return True
    return False
 
 
def extract_team_query(full_name):
    """
    Convert an Odds API team name into a query fragment.
    'Houston Cougars' → 'houston cougars'
    'BYU Cougars' → 'byu cougars'
    """
    return full_name.lower().strip()
 
 
def run_slate():
    conn = sqlite3.connect(DB_PATH)
    today = date.today().isoformat()
 
    games = get_todays_games(conn)
    if not games:
        print("No games scheduled for today.")
        conn.close()
        return
 
    print(f"\n{CYAN}── BetBuddy Slate: {date.today().strftime('%A %B %-d')} ──{RESET}")
    print(f"   {len(games)} games on the schedule\n")
 
    analyzed = 0
    skipped = 0
    errors = 0
 
    for i, game in enumerate(games, 1):
        home = game["home_team"]
        away = game["away_team"]
        short_home = home.split()[0] if home else "?"
        short_away = away.split()[0] if away else "?"
 
        # Check if already analyzed
        if game_already_analyzed(conn, home, away, today):
            print(f"  {DIM}[{i}/{len(games)}] {away} @ {home} — already analyzed, skipping{RESET}")
            skipped += 1
            continue
 
        print(f"  {CYAN}[{i}/{len(games)}]{RESET} Analyzing {away} @ {home}...")
 
        try:
            result = run_analysis(
                extract_team_query(away),
                extract_team_query(home),
                stream=False,
                quiet=True,
            )
 
            if result["error"]:
                print(f"    {RED}✗ {result['error']}{RESET}")
                errors += 1
                continue
 
            picks = result["picks"]
            analysis_text = result["analysis_text"]
 
            if not picks:
                # Check for PASS
                if re.search(r'NO\s+EDGE\s*[—–-]\s*PASS', analysis_text.upper()):
                    picks = [{"market": "pass", "pick": "NO EDGE — PASS", "confidence": "LOW"}]
                else:
                    print(f"    {RED}✗ Could not parse picks{RESET}")
                    # Still save with no picks but with analysis
                    save_prediction(
                        game_date=today,
                        away_team=away,
                        home_team=home,
                        market="unknown",
                        pick="PARSE ERROR",
                        confidence="LOW",
                        analysis_text=analysis_text,
                    )
                    errors += 1
                    continue
 
            # Save each pick
            for p in picks:
                save_prediction(
                    game_date=today,
                    away_team=away,
                    home_team=home,
                    market=p["market"],
                    pick=p["pick"],
                    confidence=p["confidence"],
                    analysis_text=analysis_text,
                )
 
            pick_summary = " | ".join(
                f"{p['pick']} ({p['confidence']})" for p in picks
            )
            print(f"    {GREEN}✓ {pick_summary}{RESET}")
            analyzed += 1
 
        except Exception as e:
            print(f"    {RED}✗ Error: {e}{RESET}")
            errors += 1
 
    conn.close()
 
    print(f"\n  Analyzed: {analyzed} | Skipped: {skipped} | Errors: {errors}")
    print(f"  Total: {len(games)} games\n")
 
 
if __name__ == "__main__":
    run_slate()