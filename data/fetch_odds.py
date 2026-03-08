# fetch_odds.py

import requests
import sqlite3
import os
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("ODDS_API_KEY")
SPORT = "basketball_ncaab"
REGIONS = "us"
MARKETS = "h2h,spreads,totals"
ODDS_FORMAT = "american"

# Resolve DB path relative to this file so it works regardless of
# which directory you run the script from
DB_PATH = os.path.join(os.path.dirname(__file__), "../db/sports.db")

# We only store lines from sharp, high-volume books.
# Pinnacle is the gold standard for sharp money.
# DraftKings, FanDuel, BetMGM represent the US recreational market.
# Comparing Pinnacle's line to the recreational books reveals where
# the sharp vs. public money diverges — a valuable signal.
BOOKMAKERS_TO_STORE = {"pinnacle", "draftkings", "fanduel", "betmgm", "williamhill_us"}


# ── DATABASE SETUP ────────────────────────────────────────────────────────────
# We store every fetch as a new set of rows — we never overwrite.
# This gives us a time series of line movement, which is more valuable
# than just the current number.
#
# Schema decisions:
#   game_id     — The Odds API's unique ID for a matchup. Stable across fetches.
#   market      — h2h (moneyline), spreads, or totals
#   outcome_name — team name for h2h/spreads, "Over"/"Under" for totals
#   price       — American odds (-110, +145, etc.)
#   point       — The spread or total number (-3.5, 147.5, etc.). NULL for moneyline.
#   fetched_at  — When we pulled this. This is what lets us track movement over time.

def init_db(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS odds (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            game_id TEXT NOT NULL,
            sport TEXT NOT NULL,
            home_team TEXT NOT NULL,
            away_team TEXT NOT NULL,
            commence_time TEXT NOT NULL,
            bookmaker TEXT NOT NULL,
            market TEXT NOT NULL,
            outcome_name TEXT NOT NULL,
            price INTEGER,
            point REAL,
            fetched_at TEXT NOT NULL
        )
    """)
    # Index on game_id so retrieval.py can quickly pull all rows for a game
    conn.execute("CREATE INDEX IF NOT EXISTS idx_game_id ON odds(game_id)")
    # Index on fetched_at so we can efficiently get the most recent snapshot
    conn.execute("CREATE INDEX IF NOT EXISTS idx_fetched_at ON odds(fetched_at)")
    conn.commit()


# ── FETCH FROM THE ODDS API ───────────────────────────────────────────────────
# One API call returns all upcoming games with lines from all bookmakers.
# We log how many credits we used so you can track against your monthly limit.
# The free tier gives 500 requests/month — at 2 fetches/day that's ~250/month,
# leaving headroom for debugging and reruns.

def fetch_odds():
    url = f"https://api.the-odds-api.com/v4/sports/{SPORT}/odds"
    params = {
        "apiKey": API_KEY,
        "regions": REGIONS,
        "markets": MARKETS,
        "oddsFormat": ODDS_FORMAT,
    }

    response = requests.get(url, params=params, timeout=10)
    response.raise_for_status()

    # The API returns credit usage in headers — log it every run
    remaining = response.headers.get("x-requests-remaining", "?")
    used = response.headers.get("x-requests-used", "?")
    print(f"  API credits used: {used} | remaining: {remaining}")

    return response.json()


# ── PARSE AND STORE ───────────────────────────────────────────────────────────
# The API response is deeply nested: game → bookmaker → market → outcome.
# We flatten it into individual rows, one per outcome per market per bookmaker.
# For example, a single spread market produces two rows — one for each team.
#
# We filter to only our whitelisted bookmakers here rather than in SQL later,
# which keeps the database lean and the signal clean.

def store_odds(conn, games):
    fetched_at = datetime.now(timezone.utc).isoformat()
    rows_inserted = 0
    games_stored = 0

    for game in games:
        game_id = game["id"]
        home_team = game["home_team"]
        away_team = game["away_team"]
        commence_time = game["commence_time"]

        # Track whether we stored anything for this game
        game_had_data = False

        for bookmaker in game.get("bookmakers", []):
            bk_key = bookmaker["key"]

            # Skip books we don't care about
            if bk_key not in BOOKMAKERS_TO_STORE:
                continue

            for market in bookmaker.get("markets", []):
                market_key = market["key"]

                for outcome in market.get("outcomes", []):
                    conn.execute("""
                        INSERT INTO odds (
                            game_id, sport, home_team, away_team,
                            commence_time, bookmaker, market,
                            outcome_name, price, point, fetched_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        game_id,
                        SPORT,
                        home_team,
                        away_team,
                        commence_time,
                        bk_key,
                        market_key,
                        outcome["name"],
                        outcome.get("price"),
                        outcome.get("point"),
                        fetched_at,
                    ))
                    rows_inserted += 1
                    game_had_data = True

        if game_had_data:
            games_stored += 1

    conn.commit()
    print(f"  Stored {rows_inserted} rows across {games_stored} games.")
    print(f"  ({len(games) - games_stored} games had no data from our target bookmakers)")


# ── SHOW A QUICK PREVIEW ──────────────────────────────────────────────────────
# After storing, print a sample of what we just pulled so you can sanity
# check the data looks right without opening the database manually.

def preview_odds(conn):
    cursor = conn.execute("""
        SELECT home_team, away_team, commence_time, bookmaker, market, outcome_name, price, point
        FROM odds
        WHERE fetched_at = (SELECT MAX(fetched_at) FROM odds)
        AND market = 'spreads'
        AND bookmaker = 'draftkings'
        LIMIT 5
    """)
    rows = cursor.fetchall()
    if not rows:
        print("\n  No spread data found for DraftKings in latest fetch.")
        return

    print("\n  Sample spreads (DraftKings, latest fetch):")
    print(f"  {'Matchup':<45} {'Team':<25} {'Spread':<8} {'Price'}")
    print("  " + "-" * 90)
    for row in rows:
        home, away, tip, book, market, team, price, point = row
        matchup = f"{away} @ {home}"
        spread = f"{point:+.1f}" if point is not None else "N/A"
        print(f"  {matchup:<45} {team:<25} {spread:<8} {price}")


# ── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    # Ensure the db directory exists before trying to connect
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

    conn = sqlite3.connect(DB_PATH)
    init_db(conn)

    print("Fetching NCAAB odds from The Odds API...")
    games = fetch_odds()

    if not games:
        print("No games returned. The season may be over or your API key may be invalid.")
        conn.close()
        return

    print(f"  Found {len(games)} upcoming games.")
    store_odds(conn, games)
    preview_odds(conn)

    conn.close()
    print("\nDone.")


if __name__ == "__main__":
    main()