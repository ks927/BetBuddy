# fetch_stats.py

import requests
import sqlite3
import os
from datetime import datetime, timezone
from dotenv import load_dotenv
from ncaab_team_ids import TEAM_ID_MAP

load_dotenv()

DB_PATH = os.path.join(os.path.dirname(__file__), "../db/sports.db")

ESPN_TEAM_SCHEDULE = "https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/teams/{team_id}/schedule"


# ── DATABASE SETUP ────────────────────────────────────────────────────────────
# Two tables:
#   team_stats: aggregated season metrics per team (upserted each run)
#   game_results: individual game log (append-only, deduped by team+date+opponent)

def init_db(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS team_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            team_name TEXT NOT NULL,
            espn_team_id TEXT,
            games_played INTEGER,
            wins INTEGER,
            losses INTEGER,
            avg_points_for REAL,
            avg_points_against REAL,
            avg_margin REAL,
            last5_record TEXT,
            last5_avg_margin REAL,
            fetched_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS game_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            espn_team_id TEXT NOT NULL,
            team_name TEXT NOT NULL,
            opponent_name TEXT NOT NULL,
            game_date TEXT NOT NULL,
            team_score INTEGER,
            opponent_score INTEGER,
            margin INTEGER,
            home_away TEXT,
            result TEXT,
            fetched_at TEXT NOT NULL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_espn_team_id ON game_results(espn_team_id)")
    conn.commit()


# ── STEP 1: GET TEAMS WITH UPCOMING GAMES ────────────────────────────────────
# Only fetch stats for teams that have upcoming odds in our database.
# No point pulling data for all 350+ NCAAB programs.

def get_teams_from_odds(conn):
    cursor = conn.execute("""
        SELECT DISTINCT home_team, away_team FROM odds
        WHERE sport = 'basketball_ncaab'
        AND commence_time > datetime('now')
    """)
    teams = set()
    for row in cursor.fetchall():
        teams.add(row[0])
        teams.add(row[1])
    return list(teams)


# ── STEP 2: FETCH GAME-BY-GAME RESULTS FROM ESPN ─────────────────────────────
# Pull the full season schedule for a team and return only completed games.
# We check status.type.completed (boolean) which is more reliable than
# checking the state string.

def fetch_team_games(team_id, team_name):
    url = ESPN_TEAM_SCHEDULE.format(team_id=team_id)
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()
    except Exception as e:
        print(f"  ESPN request failed for {team_name}: {e}")
        return []

    games = []
    for event in data.get("events", []):
        competition = event.get("competitions", [{}])[0]
        status = competition.get("status", {})

        # Use the completed boolean — more reliable than state string
        if not status.get("type", {}).get("completed", False):
            continue

        competitors = competition.get("competitors", [])
        our_team = next((c for c in competitors if c.get("team", {}).get("id") == str(team_id)), None)
        opponent = next((c for c in competitors if c.get("team", {}).get("id") != str(team_id)), None)

        if not our_team or not opponent:
            continue

        try:
            our_score = int(our_team.get("score", {}).get("value", 0))
            opp_score = int(opponent.get("score", {}).get("value", 0))
        except (ValueError, TypeError):
            continue

        games.append({
            "espn_team_id": str(team_id),
            "team_name": team_name,
            "opponent_name": opponent.get("team", {}).get("displayName", "Unknown"),
            "game_date": event.get("date", "")[:10],
            "team_score": our_score,
            "opponent_score": opp_score,
            "margin": our_score - opp_score,
            "home_away": "home" if our_team.get("homeAway") == "home" else "away",
            "result": "W" if our_team.get("winner") else "L",
        })

    return games


# ── STEP 3: CALCULATE SUMMARY STATS ──────────────────────────────────────────
# Aggregate the game log into the metrics we'll put in the LLM prompt.
# Last 5 games weighted separately — recent form matters more in NCAAB
# where teams change significantly over a season.

def calculate_stats(team_name, team_id, games):
    if not games:
        return None

    wins = sum(1 for g in games if g["result"] == "W")
    losses = len(games) - wins
    avg_pf = sum(g["team_score"] for g in games) / len(games)
    avg_pa = sum(g["opponent_score"] for g in games) / len(games)
    avg_margin = sum(g["margin"] for g in games) / len(games)

    recent = sorted(games, key=lambda g: g["game_date"], reverse=True)[:5]
    last5_wins = sum(1 for g in recent if g["result"] == "W")
    last5_margin = sum(g["margin"] for g in recent) / len(recent)

    return {
        "team_name": team_name,
        "espn_team_id": str(team_id),
        "games_played": len(games),
        "wins": wins,
        "losses": losses,
        "avg_points_for": round(avg_pf, 1),
        "avg_points_against": round(avg_pa, 1),
        "avg_margin": round(avg_margin, 1),
        "last5_record": f"{last5_wins}-{5 - last5_wins}",
        "last5_avg_margin": round(last5_margin, 1),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


# ── STEP 4: STORE ─────────────────────────────────────────────────────────────
# Upsert team_stats (delete + reinsert) so each run reflects current season.
# Append game_results but skip duplicates so reruns don't double-count games.

def store_stats(conn, stats, games):
    fetched_at = datetime.now(timezone.utc).isoformat()
    conn.execute("DELETE FROM team_stats WHERE team_name = ?", (stats["team_name"],))
    conn.execute("""
        INSERT INTO team_stats (
            team_name, espn_team_id, games_played, wins, losses,
            avg_points_for, avg_points_against, avg_margin,
            last5_record, last5_avg_margin, fetched_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        stats["team_name"], stats["espn_team_id"], stats["games_played"],
        stats["wins"], stats["losses"], stats["avg_points_for"],
        stats["avg_points_against"], stats["avg_margin"],
        stats["last5_record"], stats["last5_avg_margin"], stats["fetched_at"]
    ))

    for g in games:
        exists = conn.execute("""
            SELECT id FROM game_results
            WHERE espn_team_id = ? AND game_date = ? AND opponent_name = ?
        """, (g["espn_team_id"], g["game_date"], g["opponent_name"])).fetchone()
        if not exists:
            conn.execute("""
                INSERT INTO game_results (
                    espn_team_id, team_name, opponent_name, game_date,
                    team_score, opponent_score, margin, home_away, result, fetched_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                g["espn_team_id"], g["team_name"], g["opponent_name"],
                g["game_date"], g["team_score"], g["opponent_score"],
                g["margin"], g["home_away"], g["result"], fetched_at
            ))
    conn.commit()


# ── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    conn = sqlite3.connect(DB_PATH)
    init_db(conn)

    teams = get_teams_from_odds(conn)
    if not teams:
        print("No upcoming games found in odds table. Run fetch_odds.py first.")
        conn.close()
        return

    print(f"Found {len(teams)} teams with upcoming games. Fetching stats...\n")

    found = 0
    not_in_map = []

    for team_name in sorted(teams):
        team_id = TEAM_ID_MAP.get(team_name)
        if not team_id:
            not_in_map.append(team_name)
            continue

        games = fetch_team_games(team_id, team_name)
        if not games:
            print(f"  {team_name}: no completed games found")
            continue

        stats = calculate_stats(team_name, team_id, games)
        if stats:
            store_stats(conn, stats, games)
            print(f"  {team_name}: {stats['wins']}-{stats['losses']} | margin: {stats['avg_margin']:+.1f} | last 5: {stats['last5_record']} ({stats['last5_avg_margin']:+.1f})")
            found += 1

    if not_in_map:
        print(f"\nNo ESPN ID mapping for {len(not_in_map)} teams:")
        for t in not_in_map:
            print(f"  {t}")

    conn.close()
    print(f"\nDone. Stored stats for {found} teams.")


if __name__ == "__main__":
    main()