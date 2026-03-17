# fetch_barttorvik.py
#
# Fetches advanced efficiency metrics from Barttorvik (T-Rank).
# Provides KenPom-style schedule-adjusted efficiency data for free.
#
# Key metrics stored:
#   AdjOE   — Adjusted Offensive Efficiency (pts per 100 possessions vs avg D1 opponent)
#   AdjDE   — Adjusted Defensive Efficiency (lower is better for defense)
#   AdjEM   — AdjOE - AdjDE (efficiency margin, the single best team quality number)
#   AdjT    — Adjusted Tempo (possessions per 40 min; higher = faster pace)
#   Barthag — Power rating: estimated probability of beating an average D1 team (0–1)
#   Rank    — T-Rank (derived by sorting Barthag descending, 1 = best)
#
# Barttorvik CSV column layout (no header row):
#   [0]  team name
#   [1]  AdjOE
#   [2]  AdjDE
#   [3]  Barthag
#   [4]  record (W-L string)
#   [15] AdjT (adjusted tempo)
#
# Name matching: Barttorvik uses short names ("Duke", "Michigan St.") while the
# Odds API uses full names ("Duke Blue Devils", "Michigan St Spartans"). Name
# resolution is handled in retrieval.py at query time.

import requests
import sqlite3
import csv
import io
import os
from datetime import datetime, timezone

DB_PATH = os.path.join(os.path.dirname(__file__), "../db/sports.db")
YEAR = 2026
URL = f"https://barttorvik.com/trank.php?year={YEAR}&conlimit=All&csv=1"

# Barttorvik serves a JS verification challenge on GET; the POST bypass
# (sending js_test_submitted=1 to the same URL) returns the actual CSV.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}


# ── DB SETUP ──────────────────────────────────────────────────────────────────

def init_db(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS barttorvik_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            team_name TEXT NOT NULL,
            rank INTEGER NOT NULL,
            adj_oe REAL,
            adj_de REAL,
            adj_em REAL,
            adj_t REAL,
            barthag REAL,
            fetched_at TEXT NOT NULL
        )
    """)
    conn.commit()


# ── FETCH ─────────────────────────────────────────────────────────────────────

def fetch_csv():
    s = requests.Session()
    s.headers.update(HEADERS)
    # Trigger the JS challenge cookie (response is the challenge page)
    s.get(URL, timeout=15)
    # POST with the form field that bypasses the JS check
    resp = s.post(URL, data={"js_test_submitted": "1"}, timeout=15)
    resp.raise_for_status()
    return resp.text


# ── PARSE ─────────────────────────────────────────────────────────────────────

def parse_teams(csv_text):
    reader = csv.reader(io.StringIO(csv_text.strip()))
    raw = []
    for row in reader:
        if len(row) < 4 or not row[0].strip():
            continue
        # Skip any accidental header row
        if row[0].strip().lower() in ("teamname", "team", "team name"):
            continue
        try:
            adj_oe  = float(row[1])
            adj_de  = float(row[2])
            barthag = float(row[3])
            adj_t   = float(row[15]) if len(row) > 15 and row[15].strip() else None
        except (ValueError, IndexError):
            continue

        raw.append({
            "team_name": row[0].strip(),
            "adj_oe":    round(adj_oe, 3),
            "adj_de":    round(adj_de, 3),
            "adj_em":    round(adj_oe - adj_de, 3),
            "adj_t":     round(adj_t, 2) if adj_t is not None else None,
            "barthag":   round(barthag, 6),
        })

    # Derive T-Rank by sorting on Barthag descending
    raw.sort(key=lambda t: t["barthag"], reverse=True)
    for rank, team in enumerate(raw, start=1):
        team["rank"] = rank

    return raw


# ── STORE ─────────────────────────────────────────────────────────────────────

def store_teams(conn, teams):
    fetched_at = datetime.now(timezone.utc).isoformat()
    conn.execute("DELETE FROM barttorvik_stats")
    for t in teams:
        conn.execute("""
            INSERT INTO barttorvik_stats
                (team_name, rank, adj_oe, adj_de, adj_em, adj_t, barthag, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            t["team_name"], t["rank"],
            t["adj_oe"], t["adj_de"], t["adj_em"],
            t["adj_t"], t["barthag"], fetched_at,
        ))
    conn.commit()


# ── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    conn = sqlite3.connect(DB_PATH)
    init_db(conn)

    print("Fetching Barttorvik T-Rank efficiency data...")
    try:
        csv_text = fetch_csv()
    except Exception as e:
        print(f"  Failed to fetch: {e}")
        conn.close()
        return

    teams = parse_teams(csv_text)
    if not teams:
        print("  No teams parsed — CSV format may have changed.")
        conn.close()
        return

    store_teams(conn, teams)
    conn.close()

    print(f"  Stored {len(teams)} teams. Top 10 by T-Rank:")
    for t in teams[:10]:
        em  = f"{t['adj_em']:+.2f}" if t["adj_em"] is not None else "N/A"
        oe  = f"{t['adj_oe']:.1f}"  if t["adj_oe"] is not None else "N/A"
        de  = f"{t['adj_de']:.1f}"  if t["adj_de"] is not None else "N/A"
        tmp = f"{t['adj_t']:.1f}"   if t["adj_t"]  is not None else "N/A"
        print(f"    #{t['rank']:3d}  {t['team_name']:<25}  AdjEM {em:>7}  (O {oe} / D {de})  Tempo {tmp}")


if __name__ == "__main__":
    main()
