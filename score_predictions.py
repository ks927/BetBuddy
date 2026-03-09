# score_predictions.py
# Matches ungraded predictions against completed scores and marks
# them WIN, LOSS, or PUSH. Run after fetching scores.
#
# Spread grading: picked team's margin + spread > 0 = WIN
# Total grading:  combined score vs line in the picked direction
# Push:           exact hit on whole number lines
#
# Usage:
#   python3 score_predictions.py
#   make score   (fetches scores first, then runs this)

import sqlite3
import re
import os
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(__file__), "db", "sports.db")


# ── TEAM NAME MATCHING ────────────────────────────────────────────────────────
# The Odds API and our logged names may differ slightly. These helpers
# do fuzzy matching to bridge the gap.

def normalize_team(name):
    return (
        name.lower()
        .strip()
        .replace("state", "st")
        .replace("saint", "st")
        .replace("'", "")
        .replace(".", "")
        .replace("-", " ")
    )


def teams_match(name_a, name_b):
    a = normalize_team(name_a)
    b = normalize_team(name_b)

    if a == b:
        return True
    if a in b or b in a:
        return True

    # Last-word match (catches short names like "Duke", "Gonzaga")
    a_words = a.split()
    b_words = b.split()
    if a_words and b_words and a_words[-1] == b_words[-1] and len(a_words[-1]) > 3:
        return True

    return False


# ── PICK PARSERS ──────────────────────────────────────────────────────────────

def parse_spread_pick(pick_text):
    """Parse 'Duke -3.5' → ('Duke', -3.5)"""
    match = re.match(r'(.+?)\s*([+-]\d+\.?\d*)$', pick_text.strip())
    if match:
        return match.group(1).strip(), float(match.group(2))
    return None


def parse_total_pick(pick_text):
    """Parse 'OVER 145.5' → ('OVER', 145.5)"""
    match = re.match(r'(OVER|UNDER)\s*(\d+\.?\d*)', pick_text.strip().upper())
    if match:
        return match.group(1), float(match.group(2))
    return None


# ── GRADING LOGIC ─────────────────────────────────────────────────────────────

def grade_spread(pick_team, spread, home_team, away_team, home_score, away_score):
    if teams_match(pick_team, home_team):
        margin = (home_score - away_score) + spread
    elif teams_match(pick_team, away_team):
        margin = (away_score - home_score) + spread
    else:
        return None

    if margin > 0:
        return "WIN"
    elif margin < 0:
        return "LOSS"
    else:
        return "PUSH"


def grade_total(direction, line, home_score, away_score):
    actual_total = home_score + away_score

    if direction == "OVER":
        if actual_total > line:
            return "WIN"
        elif actual_total < line:
            return "LOSS"
        else:
            return "PUSH"
    else:
        if actual_total < line:
            return "WIN"
        elif actual_total > line:
            return "LOSS"
        else:
            return "PUSH"


# ── MAIN ──────────────────────────────────────────────────────────────────────

def score_predictions():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    # Get ungraded predictions
    predictions = conn.execute(
        "SELECT * FROM predictions WHERE result IS NULL ORDER BY game_date"
    ).fetchall()

    if not predictions:
        print("No ungraded predictions found.")
        conn.close()
        return

    # Get all scores
    try:
        scores = conn.execute("SELECT * FROM scores").fetchall()
    except sqlite3.OperationalError:
        print("✗ No scores table found. Run: python3 -m data.fetch_scores")
        conn.close()
        return

    if not scores:
        print("✗ No scores in database. Run: python3 -m data.fetch_scores")
        conn.close()
        return

    graded = 0
    unmatched = 0

    for pred in predictions:
        # Find matching score
        matched_score = None
        for score in scores:
            home_ok = teams_match(pred["home_team"], score["home_team"])
            away_ok = teams_match(pred["away_team"], score["away_team"])
            if home_ok and away_ok:
                matched_score = score
                break

        if not matched_score:
            unmatched += 1
            continue

        # Grade based on market
        result = None

        if pred["market"] == "spread":
            parsed = parse_spread_pick(pred["pick"])
            if parsed:
                team, spread = parsed
                result = grade_spread(
                    team, spread,
                    matched_score["home_team"], matched_score["away_team"],
                    matched_score["home_score"], matched_score["away_score"],
                )

        elif pred["market"] == "total":
            parsed = parse_total_pick(pred["pick"])
            if parsed:
                direction, line = parsed
                result = grade_total(
                    direction, line,
                    matched_score["home_score"], matched_score["away_score"],
                )

        elif pred["market"] == "pass":
            # NO EDGE — PASS picks don't get graded
            continue

        if result:
            conn.execute(
                """
                UPDATE predictions
                SET actual_score_away = ?, actual_score_home = ?, result = ?, graded_at = ?
                WHERE id = ?
                """,
                (
                    matched_score["away_score"],
                    matched_score["home_score"],
                    result,
                    datetime.now().isoformat(),
                    pred["id"],
                ),
            )
            graded += 1
            symbol = {"WIN": "✓", "LOSS": "✗", "PUSH": "—"}[result]
            color = {"WIN": "32", "LOSS": "31", "PUSH": "33"}[result]
            print(
                f"  \033[1;{color}m{symbol} {result}\033[0m  "
                f"{pred['pick']} ({pred['confidence']})  "
                f"— {pred['away_team']} {matched_score['away_score']} @ "
                f"{pred['home_team']} {matched_score['home_score']}"
            )

    conn.commit()
    conn.close()

    print(f"\nGraded: {graded} | Unmatched: {unmatched} | Total pending: {len(predictions)}")


if __name__ == "__main__":
    score_predictions()