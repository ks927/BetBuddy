# db/migrate_predictions.py
# Creates the predictions table in sports.db for logging picks.
# Safe to re-run — uses IF NOT EXISTS.
#
# Usage:
#   python3 -m db.migrate_predictions

import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "sports.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS predictions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    -- Game info
    game_date TEXT NOT NULL,
    away_team TEXT NOT NULL,
    home_team TEXT NOT NULL,

    -- Pick details
    market TEXT NOT NULL,
    pick TEXT NOT NULL,
    confidence TEXT NOT NULL,

    -- Odds context at time of pick
    odds_snapshot TEXT,

    -- Timestamps
    predicted_at TEXT NOT NULL,

    -- Result fields (filled later by scorer)
    actual_score_away INTEGER,
    actual_score_home INTEGER,
    result TEXT,
    graded_at TEXT
);
"""


def migrate():
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()
    print("✓ predictions table ready")


if __name__ == "__main__":
    migrate()