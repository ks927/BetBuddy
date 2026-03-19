# data/fetch_scores.py
# Pulls live and completed NCAAB scores from ESPN's public scoreboard API.
# No API key required — free and unlimited.
#
# Scores are stored keyed by the Odds API game_id (looked up via fuzzy team
# name matching) so that publish.py and score_predictions.py work unchanged.
#
# Usage:
#   python3 -m data.fetch_scores            # today's games
#   python3 -m data.fetch_scores --days 3   # include past N days

import requests
import sqlite3
import os
import argparse
from datetime import datetime, timedelta, timezone

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "db", "sports.db")
ESPN_URL = "https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/scoreboard"


# ── TEAM NAME NORMALIZATION ────────────────────────────────────────────────────

def normalize_team(name):
    return (
        name.lower().strip()
        .replace("state", "st").replace("saint", "st")
        .replace("'", "").replace(".", "").replace("-", " ")
    )


def teams_match(a, b):
    a, b = normalize_team(a), normalize_team(b)
    return a == b or a in b or b in a


# ── ODDS TABLE LOOKUP ──────────────────────────────────────────────────────────

def build_odds_index(conn, date_strs):
    """Return list of {game_id, home_team, away_team, commence_time} for given dates."""
    placeholders = ",".join("?" * len(date_strs))
    rows = conn.execute(
        f"""
        SELECT DISTINCT game_id, home_team, away_team, commence_time
        FROM odds
        WHERE sport = 'basketball_ncaab'
          AND substr(commence_time, 1, 10) IN ({placeholders})
        """,
        date_strs,
    ).fetchall()
    return [
        {"game_id": r[0], "home_team": r[1], "away_team": r[2], "commence_time": r[3]}
        for r in rows
    ]


def find_odds_game(odds_index, espn_home, espn_away):
    """Return the Odds API game dict matching the ESPN teams, or None."""
    for g in odds_index:
        home_ok = teams_match(g["home_team"], espn_home) or teams_match(g["home_team"], espn_away)
        away_ok = teams_match(g["away_team"], espn_away) or teams_match(g["away_team"], espn_home)
        if home_ok and away_ok:
            return g
    return None


# ── FETCH ──────────────────────────────────────────────────────────────────────

def fetch_scores(days_back=1):
    """Fetch scores from ESPN for today + past days_back days. Returns list of game dicts."""
    today = datetime.now(timezone.utc).date()
    dates = [today - timedelta(days=d) for d in range(days_back + 1)]

    games = []
    for date in dates:
        date_str = date.strftime("%Y%m%d")
        try:
            resp = requests.get(ESPN_URL, params={"dates": date_str}, timeout=15)
            resp.raise_for_status()
        except requests.RequestException as e:
            print(f"✗ ESPN scoreboard error ({date_str}): {e}")
            continue

        for event in resp.json().get("events", []):
            comp = event.get("competitions", [{}])[0]
            competitors = comp.get("competitors", [])
            if len(competitors) < 2:
                continue

            home_comp = next((c for c in competitors if c.get("homeAway") == "home"), competitors[0])
            away_comp = next((c for c in competitors if c.get("homeAway") == "away"), competitors[1])

            home_name = home_comp.get("team", {}).get("displayName", "")
            away_name = away_comp.get("team", {}).get("displayName", "")
            home_score = home_comp.get("score")
            away_score = away_comp.get("score")

            if home_score is None or away_score is None:
                continue  # Game hasn't started

            status = event.get("status", {})
            status_type = status.get("type", {})
            state = status_type.get("state", "pre")  # "pre", "in", "post"
            completed = status_type.get("completed", False)

            if state == "pre":
                continue  # Game hasn't started — no scores yet

            games.append({
                "home_team": home_name,
                "away_team": away_name,
                "home_score": int(home_score),
                "away_score": int(away_score),
                "completed": completed,
                "commence_time": event.get("date", ""),
                "game_date": date.isoformat(),
            })

    return games


# ── STORE ──────────────────────────────────────────────────────────────────────

def store_scores(games):
    """Write fetched scores into the scores table, keyed by Odds API game_id."""
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
            last_update TEXT,
            fetched_at TEXT NOT NULL
        )
    """)

    for col, definition in [
        ("completed", "INTEGER NOT NULL DEFAULT 1"),
        ("last_update", "TEXT"),
    ]:
        try:
            conn.execute(f"ALTER TABLE scores ADD COLUMN {col} {definition}")
            conn.commit()
        except sqlite3.OperationalError:
            pass

    date_strs = list({g["game_date"] for g in games})
    odds_index = build_odds_index(conn, date_strs)

    inserted = 0
    unmatched = 0
    now = datetime.now().isoformat()

    for g in games:
        match = find_odds_game(odds_index, g["home_team"], g["away_team"])
        if not match:
            unmatched += 1
            continue  # Not on today's slate — skip

        conn.execute(
            """
            INSERT OR REPLACE INTO scores
                (id, home_team, away_team, home_score, away_score, completed, commence_time, last_update, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                match["game_id"],
                match["home_team"],
                match["away_team"],
                g["home_score"],
                g["away_score"],
                1 if g["completed"] else 0,
                match["commence_time"],
                now if not g["completed"] else None,  # ISO timestamp for live; NULL for final
                now,
            ),
        )
        inserted += 1

    conn.commit()
    conn.close()

    completed = sum(1 for g in games if g["completed"])
    live = sum(1 for g in games if not g["completed"])
    print(f"✓ ESPN: {completed} final, {live} in-progress  →  matched {inserted} to slate (skipped {unmatched})")


# ── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Fetch NCAAB scores from ESPN")
    parser.add_argument("--days", type=int, default=1, help="Days to look back (default: 1)")
    args = parser.parse_args()

    games = fetch_scores(days_back=args.days)
    if games:
        store_scores(games)
    else:
        print("✓ No scores found")


if __name__ == "__main__":
    main()
