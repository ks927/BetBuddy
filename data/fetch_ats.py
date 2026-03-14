# data/fetch_ats.py
# Pulls ATS (against the spread) and O/U records from ESPN's undocumented
# odds-records API and stores them in sports.db.
#
# ESPN provides these record types per team:
#   - spreadOverall, spreadHome, spreadAway, spreadFavorite, spreadUnderdog
#   - moneyLineOverall, moneyLineHome, moneyLineAway, etc.
#
# Each record includes W/L/Push counts and an ATS margin.
# We also extract Over/Under totals from the moneyLine records (ESPN
# bundles O/U stats into the moneyLine record type).
#
# This uses the same ESPN team IDs already in ncaab_team_ids.py.
#
# Usage:
#   python3 -m data.fetch_ats           # fetch for all teams with upcoming games
#   python3 -m data.fetch_ats --all     # fetch for every team in the ID map

import requests
import sqlite3
import os
import sys
import time
import argparse
from datetime import datetime

# Add parent dir to path so we can import ncaab_team_ids
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "db", "sports.db")
BASE_URL = "https://sports.core.api.espn.com/v2/sports/basketball/leagues/mens-college-basketball"
SEASON = 2026
SEASON_TYPE = 2  # regular season


# ── FETCH FROM ESPN ───────────────────────────────────────────────────────────

def fetch_odds_records(espn_team_id):
    """Fetch odds records for a single team from ESPN API."""
    url = f"{BASE_URL}/seasons/{SEASON}/types/{SEASON_TYPE}/teams/{espn_team_id}/odds-records?limit=100"
    try:
        resp = requests.get(url, timeout=15)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as e:
        print(f"  ✗ Error fetching team {espn_team_id}: {e}")
        return None


def parse_odds_records(data):
    """
    Parse the ESPN odds-records response into a flat dict.

    Returns dict with keys like:
        ats_overall_w, ats_overall_l, ats_overall_push, ats_overall_margin,
        ats_home_w, ats_home_l, ats_away_w, ats_away_l,
        ats_fav_w, ats_fav_l, ats_dog_w, ats_dog_l,
        ou_over, ou_under  (from moneyLine overall record)
    """
    if not data or "items" not in data:
        return None

    result = {}

    type_map = {
        "spreadOverall": "ats_overall",
        "spreadHome": "ats_home",
        "spreadAway": "ats_away",
        "spreadFavorite": "ats_fav",
        "spreadUnderdog": "ats_dog",
        "moneyLineOverall": "ml_overall",
    }

    for item in data["items"]:
        record_type = item.get("type", "")
        prefix = type_map.get(record_type)
        if not prefix:
            continue

        stats = {s["type"]: s["value"] for s in item.get("stats", [])}

        result[f"{prefix}_w"] = int(stats.get("win", 0))
        result[f"{prefix}_l"] = int(stats.get("loss", 0))
        result[f"{prefix}_push"] = int(stats.get("push", 0))
        result[f"{prefix}_margin"] = stats.get("margin", 0.0)

        # Extract O/U from moneyLine overall (ESPN puts it there)
        if prefix == "ml_overall":
            result["ou_over"] = int(stats.get("overTotal", 0))
            result["ou_under"] = int(stats.get("underTotal", 0))

    return result if result else None


# ── DATABASE ──────────────────────────────────────────────────────────────────

def ensure_table(conn):
    """Create the team_ats table if it doesn't exist."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS team_ats (
            team_name TEXT PRIMARY KEY,
            espn_team_id INTEGER,
            ats_overall_w INTEGER,
            ats_overall_l INTEGER,
            ats_overall_push INTEGER,
            ats_overall_margin REAL,
            ats_home_w INTEGER,
            ats_home_l INTEGER,
            ats_home_push INTEGER,
            ats_home_margin REAL,
            ats_away_w INTEGER,
            ats_away_l INTEGER,
            ats_away_push INTEGER,
            ats_away_margin REAL,
            ats_fav_w INTEGER,
            ats_fav_l INTEGER,
            ats_fav_push INTEGER,
            ats_fav_margin REAL,
            ats_dog_w INTEGER,
            ats_dog_l INTEGER,
            ats_dog_push INTEGER,
            ats_dog_margin REAL,
            ou_over INTEGER,
            ou_under INTEGER,
            fetched_at TEXT NOT NULL
        )
    """)


def store_ats(conn, team_name, espn_id, records):
    """Insert or replace ATS records for a team."""
    conn.execute("""
        INSERT OR REPLACE INTO team_ats (
            team_name, espn_team_id,
            ats_overall_w, ats_overall_l, ats_overall_push, ats_overall_margin,
            ats_home_w, ats_home_l, ats_home_push, ats_home_margin,
            ats_away_w, ats_away_l, ats_away_push, ats_away_margin,
            ats_fav_w, ats_fav_l, ats_fav_push, ats_fav_margin,
            ats_dog_w, ats_dog_l, ats_dog_push, ats_dog_margin,
            ou_over, ou_under,
            fetched_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        team_name, espn_id,
        records.get("ats_overall_w", 0), records.get("ats_overall_l", 0),
        records.get("ats_overall_push", 0), records.get("ats_overall_margin", 0.0),
        records.get("ats_home_w", 0), records.get("ats_home_l", 0),
        records.get("ats_home_push", 0), records.get("ats_home_margin", 0.0),
        records.get("ats_away_w", 0), records.get("ats_away_l", 0),
        records.get("ats_away_push", 0), records.get("ats_away_margin", 0.0),
        records.get("ats_fav_w", 0), records.get("ats_fav_l", 0),
        records.get("ats_fav_push", 0), records.get("ats_fav_margin", 0.0),
        records.get("ats_dog_w", 0), records.get("ats_dog_l", 0),
        records.get("ats_dog_push", 0), records.get("ats_dog_margin", 0.0),
        records.get("ou_over", 0), records.get("ou_under", 0),
        datetime.now().isoformat(),
    ))


# ── GET TEAMS TO FETCH ────────────────────────────────────────────────────────

def get_upcoming_teams(conn):
    """Get team names that appear in upcoming games (from odds table)."""
    try:
        cursor = conn.execute("""
            SELECT DISTINCT home_team FROM odds
            WHERE commence_time > datetime('now')
            UNION
            SELECT DISTINCT away_team FROM odds
            WHERE commence_time > datetime('now')
        """)
        return [row[0] for row in cursor.fetchall()]
    except Exception:
        return []


def get_team_id_map():
    """Load the Odds API name -> ESPN ID mapping."""
    try:
        from data.ncaab_team_ids import TEAM_ID_MAP
        return TEAM_ID_MAP
    except ImportError:
        try:
            from ncaab_team_ids import TEAM_IDS
            return TEAM_ID_MAP
        except ImportError:
            print("✗ Could not import TEAM_ID_MAP from ncaab_team_ids.py")
            return {}


# ── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Fetch ATS records from ESPN")
    parser.add_argument("--all", action="store_true",
                        help="Fetch all teams (not just upcoming)")
    args = parser.parse_args()

    team_id_map = get_team_id_map()
    if not team_id_map:
        print("✗ No team ID mapping available.")
        return

    conn = sqlite3.connect(DB_PATH)
    ensure_table(conn)

    if args.all:
        teams_to_fetch = list(team_id_map.keys())
        print(f"Fetching ATS records for all {len(teams_to_fetch)} teams...")
    else:
        upcoming = get_upcoming_teams(conn)
        teams_to_fetch = [t for t in upcoming if t in team_id_map]
        if not teams_to_fetch:
            print("No upcoming teams found with ESPN IDs. Use --all to fetch everything.")
            conn.close()
            return
        print(f"Fetching ATS records for {len(teams_to_fetch)} upcoming teams...")

    fetched = 0
    failed = 0

    for team_name in teams_to_fetch:
        espn_id = team_id_map[team_name]
        data = fetch_odds_records(espn_id)
        if data:
            records = parse_odds_records(data)
            if records:
                store_ats(conn, team_name, espn_id, records)
                fetched += 1
            else:
                failed += 1
        else:
            failed += 1

        # Be respectful to ESPN — small delay between requests
        time.sleep(0.3)

    conn.commit()
    conn.close()
    print(f"✓ Fetched ATS records: {fetched} teams | {failed} failed")


if __name__ == "__main__":
    main()