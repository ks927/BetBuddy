# data/fetch_scores.py
# Pulls completed NCAAB game scores from The Odds API and stores
# them in sports.db. Used by score_predictions.py to grade picks.
#
# The Odds API scores endpoint is included in the free tier.
# One call covers all completed games for the lookback window.
#
# Usage:
#   python3 -m data.fetch_scores            # default 3-day lookback
#   python3 -m data.fetch_scores --days 5   # 5-day lookback

import requests
import sqlite3
import os
import argparse
from datetime import datetime

API_KEY = os.environ.get("ODDS_API_KEY", "")
SPORT = "basketball_ncaab"
BASE_URL = "https://api.the-odds-api.com/v4/sports"
DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "db", "sports.db")


# ── FETCH ─────────────────────────────────────────────────────────────────────

def fetch_scores(days_back=3):
    """Pull scores from The Odds API (completed and in-progress). Returns list of game dicts."""
    if not API_KEY:
        print("✗ ODDS_API_KEY not set")
        return []

    url = f"{BASE_URL}/{SPORT}/scores/"
    params = {
        "apiKey": API_KEY,
        "daysFrom": days_back,
    }

    try:
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"✗ Scores API error: {e}")
        return []

    data = resp.json()
    games = []
    completed_count = 0
    live_count = 0

    for game in data:
        completed = game.get("completed", False)

        scores = game.get("scores", [])
        if not scores or len(scores) < 2:
            continue  # No scores yet (game hasn't started)

        score_map = {s["name"]: int(s["score"]) for s in scores if s.get("score") is not None}
        home = game.get("home_team", "")
        away = game.get("away_team", "")

        if home in score_map and away in score_map:
            games.append({
                "home_team": home,
                "away_team": away,
                "home_score": score_map[home],
                "away_score": score_map[away],
                "commence_time": game.get("commence_time", ""),
                "game_id": game.get("id", ""),
                "completed": completed,
            })
            if completed:
                completed_count += 1
            else:
                live_count += 1

    print(f"✓ Fetched {completed_count} completed, {live_count} in-progress (last {days_back} days)")
    return games


# ── STORE ─────────────────────────────────────────────────────────────────────

def store_scores(games):
    """Write fetched scores into the scores table."""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS scores (
            id TEXT PRIMARY KEY,
            home_team TEXT NOT NULL,
            away_team TEXT NOT NULL,
            home_score INTEGER NOT NULL,
            away_score INTEGER NOT NULL,
            completed INTEGER NOT NULL DEFAULT 1,
            commence_time TEXT,
            fetched_at TEXT NOT NULL
        )
    """)

    # Migrate existing tables that predate the completed column
    try:
        conn.execute("ALTER TABLE scores ADD COLUMN completed INTEGER NOT NULL DEFAULT 1")
        conn.commit()
    except sqlite3.OperationalError:
        pass  # Column already exists

    inserted = 0
    for g in games:
        conn.execute(
            """
            INSERT OR REPLACE INTO scores
                (id, home_team, away_team, home_score, away_score, completed, commence_time, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                g["game_id"],
                g["home_team"],
                g["away_team"],
                g["home_score"],
                g["away_score"],
                1 if g.get("completed") else 0,
                g["commence_time"],
                datetime.now().isoformat(),
            ),
        )
        inserted += 1

    conn.commit()
    conn.close()
    print(f"✓ Stored {inserted} scores")


# ── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Fetch completed NCAAB scores")
    parser.add_argument("--days", type=int, default=3, help="Days to look back (default: 3)")
    args = parser.parse_args()

    games = fetch_scores(days_back=args.days)
    if games:
        store_scores(games)


if __name__ == "__main__":
    main()