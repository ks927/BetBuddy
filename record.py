# record.py
# Displays your prediction record with breakdowns by confidence,
# market, and recent picks. ROI calculated on flat $100 bets at -110.
#
# Usage:
#   python3 record.py             # summary + last 10 picks
#   python3 record.py --detail    # summary + all picks

import sqlite3
import os
import argparse

DB_PATH = os.path.join(os.path.dirname(__file__), "db", "sports.db")


# ── ANSI COLOR CODES ──────────────────────────────────────────────────────────

GREEN = "\033[1;32m"
RED = "\033[1;31m"
YELLOW = "\033[1;33m"
ORANGE = "\033[38;5;208m"
CYAN = "\033[1;36m"
BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"


# ── HELPERS ───────────────────────────────────────────────────────────────────

def get_record(conn, where="1=1", params=()):
    rows = conn.execute(
        f"""
        SELECT result, COUNT(*) as cnt
        FROM predictions
        WHERE result IS NOT NULL AND ({where})
        GROUP BY result
        """,
        params,
    ).fetchall()

    record = {"WIN": 0, "LOSS": 0, "PUSH": 0}
    for row in rows:
        record[row[0]] = row[1]
    return record


def format_record(rec):
    w, l, p = rec["WIN"], rec["LOSS"], rec["PUSH"]
    total = w + l
    pct = (w / total * 100) if total > 0 else 0

    pct_color = GREEN if pct >= 55 else (YELLOW if pct >= 50 else RED)
    record_str = f"{GREEN}{w}W{RESET}-{RED}{l}L{RESET}-{YELLOW}{p}P{RESET}"
    pct_str = f"{pct_color}{pct:.1f}%{RESET}"

    return record_str, pct_str, total


def calc_roi(conn, where="1=1", params=()):
    """Flat-bet ROI: $100 per pick at -110 standard juice."""
    rec = get_record(conn, where, params)
    w, l = rec["WIN"], rec["LOSS"]
    total_bets = w + l + rec["PUSH"]

    if total_bets == 0:
        return 0, 0, 0

    profit = (w * 90.91) - (l * 100)
    risked = (w + l) * 100
    roi = (profit / risked * 100) if risked > 0 else 0

    return profit, risked, roi


# ── DISPLAY ───────────────────────────────────────────────────────────────────

def show_record(detail=False):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    try:
        total = conn.execute("SELECT COUNT(*) FROM predictions").fetchone()[0]
        graded = conn.execute(
            "SELECT COUNT(*) FROM predictions WHERE result IS NOT NULL"
        ).fetchone()[0]
        pending = total - graded
    except sqlite3.OperationalError:
        print("No predictions table found. Run: python3 -m db.migrate_predictions")
        return

    if total == 0:
        print("No predictions logged yet.")
        return

    print(f"\n{BOLD}{'═' * 52}{RESET}")
    print(f"{BOLD}  📊 BetBuddy Prediction Record{RESET}")
    print(f"{BOLD}{'═' * 52}{RESET}\n")

    # Overall
    overall = get_record(conn)
    rec_str, pct_str, _ = format_record(overall)
    profit, risked, roi = calc_roi(conn)
    roi_color = GREEN if roi >= 0 else RED

    print(f"  {BOLD}Overall:{RESET}  {rec_str}  ({pct_str})")
    print(f"  {BOLD}ROI:{RESET}     {roi_color}{roi:+.1f}%{RESET}  "
          f"{DIM}(${profit:+,.0f} on ${risked:,.0f} risked @ -110 flat){RESET}")
    print(f"  {DIM}Pending: {pending} ungraded picks{RESET}")

    # By confidence
    print(f"\n  {BOLD}By Confidence:{RESET}")
    for conf, color in [("HIGH", RED), ("MEDIUM", ORANGE), ("LOW", DIM)]:
        rec = get_record(conn, "confidence = ?", (conf,))
        if rec["WIN"] + rec["LOSS"] + rec["PUSH"] == 0:
            continue
        rec_str, pct_str, count = format_record(rec)
        _, _, roi = calc_roi(conn, "confidence = ?", (conf,))
        roi_color = GREEN if roi >= 0 else RED
        print(f"    {color}{conf:6s}{RESET}  {rec_str}  ({pct_str})  {roi_color}{roi:+.1f}% ROI{RESET}")

    # By market
    print(f"\n  {BOLD}By Market:{RESET}")
    for market in ["spread", "total"]:
        rec = get_record(conn, "market = ?", (market,))
        if rec["WIN"] + rec["LOSS"] + rec["PUSH"] == 0:
            continue
        rec_str, pct_str, _ = format_record(rec)
        _, _, roi = calc_roi(conn, "market = ?", (market,))
        roi_color = GREEN if roi >= 0 else RED
        print(f"    {market.capitalize():8s}  {rec_str}  ({pct_str})  {roi_color}{roi:+.1f}% ROI{RESET}")

    # Recent / all picks
    if detail:
        print(f"\n  {BOLD}All Picks:{RESET}")
        picks = conn.execute(
            "SELECT * FROM predictions ORDER BY game_date DESC, predicted_at DESC"
        ).fetchall()
    else:
        print(f"\n  {BOLD}Last 10 Picks:{RESET}")
        picks = conn.execute(
            "SELECT * FROM predictions ORDER BY game_date DESC, predicted_at DESC LIMIT 10"
        ).fetchall()

    for p in picks:
        if p["result"]:
            symbol = {"WIN": f"{GREEN}✓", "LOSS": f"{RED}✗", "PUSH": f"{YELLOW}—"}[p["result"]]
            result_str = f"{symbol} {p['result']:4s}{RESET}"
            score_str = f"{DIM}{p['actual_score_away']}-{p['actual_score_home']}{RESET}"
        else:
            result_str = f"{DIM}  ···  {RESET}"
            score_str = f"{DIM}pending{RESET}"

        conf_color = {"HIGH": RED, "MEDIUM": ORANGE, "LOW": DIM}.get(p["confidence"], "")
        print(
            f"    {result_str}  {p['game_date']}  "
            f"{p['away_team']} @ {p['home_team']}  "
            f"→ {CYAN}{p['pick']}{RESET}  "
            f"{conf_color}{p['confidence']}{RESET}  {score_str}"
        )

    print(f"\n{BOLD}{'═' * 52}{RESET}\n")
    conn.close()


# ── MAIN ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Show BetBuddy prediction record")
    parser.add_argument("--detail", action="store_true", help="Show all picks")
    args = parser.parse_args()
    show_record(detail=args.detail)