# migrate_add_analysis.py
# One-time migration: adds analysis_text column to the predictions table.
# Safe to run multiple times — checks if column already exists.
#
# Usage:
#   python3 migrate_add_analysis.py
 
import sqlite3
import os
 
DB_PATH = os.path.join(os.path.dirname(__file__), "db", "sports.db")
 
 
def migrate():
    conn = sqlite3.connect(DB_PATH)
 
    # Check if column already exists
    columns = [row[1] for row in conn.execute("PRAGMA table_info(predictions)").fetchall()]
 
    if "analysis_text" in columns:
        print("✓ analysis_text column already exists. Nothing to do.")
    else:
        conn.execute("ALTER TABLE predictions ADD COLUMN analysis_text TEXT")
        conn.commit()
        print("✓ Added analysis_text column to predictions table.")
 
    conn.close()
 
 
if __name__ == "__main__":
    migrate()