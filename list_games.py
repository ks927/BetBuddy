# list_games.py
# Prints all upcoming NCAAB games currently in the odds database,
# sorted by tip time. Run this before query.py to see what's available.
#
# Usage:
#   python3 list_games.py           # all upcoming games
#   python3 list_games.py today     # only today's games
#   python3 list_games.py duke      # games involving a specific team
#
# Note on timestamps: The Odds API stores commence_time as ISO 8601 with a T
# and Z suffix (e.g. '2026-03-07T02:30:00Z'). SQLite's datetime('now') uses
# a space separator and no Z, so direct comparison fails. We normalize by
# replacing T with a space and stripping Z before comparing.
 
import sqlite3
import sys
import os
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
 
DB_PATH = os.path.join(os.path.dirname(__file__), "db/sports.db")
 
ET = ZoneInfo("America/New_York")
 
 
def main():
    filter_arg = sys.argv[1].lower().strip() if len(sys.argv) > 1 else None
 
    conn = sqlite3.connect(DB_PATH)
 
    # Normalize the stored timestamp to match SQLite's datetime() format
    # before comparing — replace the T separator and strip the Z suffix
    cursor = conn.execute("""
        SELECT DISTINCT game_id, home_team, away_team, commence_time
        FROM odds
        WHERE sport = 'basketball_ncaab'
        AND replace(replace(commence_time, 'T', ' '), 'Z', '') > datetime('now')
        ORDER BY commence_time ASC
    """)
    games = cursor.fetchall()
    conn.close()
 
    if not games:
        print("No upcoming games found. Run fetch_odds.py to refresh.")
        return
 
    # Apply filter if provided
    if filter_arg == "today":
        today_et = datetime.now(ET).date()
        def game_date_et(tip_raw):
            tip_utc = datetime.fromisoformat(tip_raw.replace("Z", "+00:00"))
            return tip_utc.astimezone(ET).date()
        games = [g for g in games if game_date_et(g[3]) == today_et]
    elif filter_arg:
        games = [g for g in games
                 if filter_arg in g[1].lower() or filter_arg in g[2].lower()]
 
    if not games:
        print(f"No upcoming games matching '{filter_arg}'.")
        return
 
    # Group by date for readability
    current_date = None
    for game_id, home, away, tip_raw in games:
        try:
            tip_utc = datetime.fromisoformat(tip_raw.replace("Z", "+00:00"))
            tip = tip_utc.astimezone(ET)
            tip_local = tip.strftime("%I:%M %p ET")
            date_label = tip.strftime("%A %B %-d")
        except Exception:
            tip_local = tip_raw
            date_label = tip_raw[:10]
 
        if date_label != current_date:
            print(f"\n{date_label}")
            print("─" * 50)
            current_date = date_label
 
        print(f"  {away:<30} @  {home:<30}  {tip_local}")
 
    print(f"\n{len(games)} game(s) found.")
 
 
if __name__ == "__main__":
    main()