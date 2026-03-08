# fetch_injuries.py
# Fetches injury data from ESPN's public API for all teams with upcoming games.
#
# ESPN exposes injury data through the core API at:
#   sports.core.api.espn.com/v2/sports/basketball/leagues/mens-college-basketball/teams/{id}/injuries
#
# Unlike the NBA, NCAAB injury reporting is inconsistent — not all teams report
# injuries, and "day-to-day" in college often just means "coach hasn't decided."
# We store what's available and surface it in the LLM prompt with appropriate
# caveats. Even incomplete injury data is better than none — a confirmed "out"
# on a starter is a 3-5 point swing that the model should know about.
#
# Usage:
#   python3 data/fetch_injuries.py

import requests
import sqlite3
import os
import time
from datetime import datetime, timezone
from dotenv import load_dotenv
from ncaab_team_ids import TEAM_ID_MAP

load_dotenv()

DB_PATH = os.path.join(os.path.dirname(__file__), "../db/sports.db")

# ESPN core API endpoint for team injuries
ESPN_INJURIES_URL = "https://sports.core.api.espn.com/v2/sports/basketball/leagues/mens-college-basketball/teams/{team_id}/injuries"

# Fallback: the site API team page sometimes includes injuries in the response
ESPN_TEAM_URL = "https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/teams/{team_id}"


# ── DATABASE SETUP ────────────────────────────────────────────────────────────
# We upsert injuries each run — delete stale data and replace with current.
# Injury status changes frequently, so we don't track history here.

def init_db(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS injuries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            espn_team_id TEXT NOT NULL,
            team_name TEXT NOT NULL,
            player_name TEXT NOT NULL,
            position TEXT,
            status TEXT,
            detail TEXT,
            fetched_at TEXT NOT NULL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_injuries_team ON injuries(espn_team_id)")
    conn.commit()


# ── GET TEAMS WITH UPCOMING GAMES ────────────────────────────────────────────

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


# ── FETCH INJURIES FROM ESPN CORE API ─────────────────────────────────────────
# The core API returns a list of injury items with $ref links to athlete details.
# We resolve the athlete name from the inline data when available, or follow
# the $ref link if needed.

def fetch_injuries_core(team_id, team_name):
    """Try the core API injuries endpoint first."""
    url = ESPN_INJURIES_URL.format(team_id=team_id)
    try:
        response = requests.get(url, timeout=10)
        if response.status_code == 404:
            return None  # endpoint doesn't exist for this team/sport
        response.raise_for_status()
        data = response.json()
    except Exception as e:
        return None

    items = data.get("items", [])
    if not items:
        return []

    injuries = []
    for item in items:
        # The item may contain inline data or $ref links
        # Try to extract what we can from the inline response
        athlete = item.get("athlete", {})
        player_name = athlete.get("displayName") or athlete.get("fullName")

        # If athlete is a $ref link, try to resolve it
        if not player_name and "$ref" in athlete:
            player_name = resolve_athlete_name(athlete["$ref"])

        if not player_name:
            player_name = "Unknown"

        position = athlete.get("position", {}).get("abbreviation", "")
        status_obj = item.get("status", "")
        if isinstance(status_obj, dict):
            status = status_obj.get("type", {}).get("description", "Unknown")
        elif isinstance(status_obj, str):
            status = status_obj
        else:
            status = "Unknown"

        # Detail / description of the injury
        detail_obj = item.get("type", {})
        if isinstance(detail_obj, dict):
            detail = detail_obj.get("description", "") or detail_obj.get("name", "")
        else:
            detail = ""

        # Also check for a longComment or shortComment
        if not detail:
            detail = item.get("longComment", "") or item.get("shortComment", "")

        injuries.append({
            "espn_team_id": str(team_id),
            "team_name": team_name,
            "player_name": player_name,
            "position": position,
            "status": status,
            "detail": detail,
        })

    return injuries


def resolve_athlete_name(ref_url):
    """Follow a $ref URL to get an athlete's name."""
    try:
        response = requests.get(ref_url, timeout=5)
        response.raise_for_status()
        data = response.json()
        return data.get("displayName") or data.get("fullName") or None
    except Exception:
        return None


# ── FETCH INJURIES FROM SITE API (FALLBACK) ──────────────────────────────────
# If the core API doesn't work for NCAAB, try the site API team endpoint
# which sometimes includes injury data in the response.

def fetch_injuries_site(team_id, team_name):
    """Fallback: check the site API team page for injury data."""
    url = ESPN_TEAM_URL.format(team_id=team_id)
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()
    except Exception:
        return None

    # The site API nests injuries under team -> injuries or similar
    team_data = data.get("team", {})
    injury_list = team_data.get("injuries", [])

    if not injury_list:
        return None

    injuries = []
    for group in injury_list:
        for item in group.get("items", []):
            athlete = item.get("athlete", {})
            player_name = athlete.get("displayName", "Unknown")
            position = athlete.get("position", {}).get("abbreviation", "")
            status = item.get("status", "Unknown")
            detail = item.get("longComment", "") or item.get("shortComment", "")

            injuries.append({
                "espn_team_id": str(team_id),
                "team_name": team_name,
                "player_name": player_name,
                "position": position,
                "status": status,
                "detail": detail,
            })

    return injuries


# ── STORE ─────────────────────────────────────────────────────────────────────
# Delete existing injuries for this team and replace with current data.

def store_injuries(conn, injuries):
    if not injuries:
        return

    fetched_at = datetime.now(timezone.utc).isoformat()
    team_id = injuries[0]["espn_team_id"]

    conn.execute("DELETE FROM injuries WHERE espn_team_id = ?", (team_id,))
    for inj in injuries:
        conn.execute("""
            INSERT INTO injuries (
                espn_team_id, team_name, player_name, position,
                status, detail, fetched_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            inj["espn_team_id"], inj["team_name"], inj["player_name"],
            inj["position"], inj["status"], inj["detail"], fetched_at,
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

    print(f"Fetching injuries for {len(teams)} teams...\n")

    teams_with_injuries = 0
    total_injuries = 0
    no_espn_id = []
    no_data = 0

    for team_name in sorted(teams):
        team_id = TEAM_ID_MAP.get(team_name)
        if not team_id:
            no_espn_id.append(team_name)
            continue

        # Try core API first, fall back to site API
        injuries = fetch_injuries_core(team_id, team_name)
        if injuries is None:
            injuries = fetch_injuries_site(team_id, team_name)

        if injuries is None or len(injuries) == 0:
            no_data += 1
            continue

        store_injuries(conn, injuries)
        teams_with_injuries += 1
        total_injuries += len(injuries)

        for inj in injuries:
            status_str = inj["status"]
            detail_str = f" ({inj['detail']})" if inj["detail"] else ""
            print(f"  {team_name}: {inj['player_name']} ({inj['position']}) — {status_str}{detail_str}")

        # Be polite to ESPN's servers
        time.sleep(0.3)

    if no_espn_id:
        print(f"\nNo ESPN ID mapping for {len(no_espn_id)} teams (see ncaab_team_ids.py)")

    conn.close()
    print(f"\nDone. Found {total_injuries} injuries across {teams_with_injuries} teams.")
    print(f"({no_data} teams had no injury data reported.)")


if __name__ == "__main__":
    main()