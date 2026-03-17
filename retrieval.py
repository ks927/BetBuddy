# retrieval.py (v4)
# Pulls everything the LLM needs to analyze a matchup from the local database.
#
# v4 changes over v3:
#   - Adds identify_favorite(): determines who is favored from the spread data,
#     independent of the API's home/away assignment
#   - Adds detect_neutral_site(): flags tournament/postseason games where
#     The Odds API's home/away is arbitrary and shouldn't be trusted
#   - format_lines() now always presents the FAVORITE first in spread display
#   - build_context() labels teams as FAVORITE/UNDERDOG when neutral site is
#     detected, and suppresses home court advantage claims
#   - All spread references now use the favorite's perspective so the model
#     can't get confused about who is laying points
#
# The philosophy: give the model CONCLUSIONS from the data, not raw numbers
# it has to interpret. Every quantitative insight should be pre-digested.
# v4 adds: the model should never have to figure out who is favored from
# raw spread numbers — we tell it directly.

import sqlite3
import os
from datetime import datetime, timezone, timedelta

DB_PATH = os.path.join(os.path.dirname(__file__), "db/sports.db")

BOOKMAKER_ORDER = ["draftkings", "fanduel", "betmgm"]
BOOKMAKER_LABELS = {
    "draftkings": "DraftKings ",
    "fanduel": "FanDuel    ",
    "betmgm": "BetMGM     ",
}

MOVEMENT_BOOKS = ["draftkings", "fanduel"]
TOTALS_MOVEMENT_BOOKS = ["draftkings", "fanduel"]


# ── IMPLIED PROBABILITY ───────────────────────────────────────────────────────

def american_to_implied(price):
    """Convert American odds to implied probability as a percentage."""
    if price is None:
        return None
    if price < 0:
        return round(abs(price) / (abs(price) + 100) * 100, 1)
    else:
        return round(100 / (price + 100) * 100, 1)


# ── IDENTIFY FAVORITE ─────────────────────────────────────────────────────────
# Determine who is favored from the spread data. This is the source of truth
# for the entire context block — everything else references this.

def identify_favorite(lines, team_a, team_b):
    """
    Returns (favorite, underdog, fav_spread) where fav_spread is negative.
    Uses the first available book's spread. If pick'em, returns team_a as
    nominal favorite with spread 0.
    Returns None if no spread data available.
    """
    for bk in BOOKMAKER_ORDER:
        spreads = lines.get(bk, {}).get("spreads", {})
        a_data = spreads.get(team_a, {})
        b_data = spreads.get(team_b, {})

        if a_data.get("point") is not None and b_data.get("point") is not None:
            a_point = a_data["point"]
            b_point = b_data["point"]

            if a_point < b_point:
                # team_a is favored (negative spread)
                return team_a, team_b, a_point
            elif b_point < a_point:
                # team_b is favored
                return team_b, team_a, b_point
            else:
                # Pick'em
                return team_a, team_b, 0

    return None


# ── NEUTRAL SITE DETECTION ────────────────────────────────────────────────────
# Conference tournaments and the NCAA tournament play at neutral sites, but
# The Odds API still assigns home/away (often arbitrarily). When we detect
# a likely neutral site game, we suppress home court advantage claims and
# label teams by favorite/underdog instead of home/away.
#
# Heuristic: games in March or later are likely tournament games.
# This isn't perfect but catches conference tournaments + NCAA tournament.
# A more robust approach would check if the venue differs from either team's
# home arena, but we don't have venue data.

def detect_neutral_site(commence_time_str):
    """
    Returns True if the game is likely at a neutral site (tournament game).
    """
    try:
        game_date = datetime.fromisoformat(commence_time_str.replace("Z", "+00:00"))
        # Conference tournaments typically start late February / early March
        # NCAA tournament runs through April
        if game_date.month in (3, 4):
            return True
        if game_date.month == 2 and game_date.day >= 25:
            return True
    except (ValueError, TypeError):
        pass
    return False


# ── TEAM MATCHING ─────────────────────────────────────────────────────────────

def find_team_name(conn, query):
    query = query.strip().lower()
    cursor = conn.execute("""
        SELECT DISTINCT home_team FROM odds
        UNION
        SELECT DISTINCT away_team FROM odds
    """)
    all_teams = [row[0] for row in cursor.fetchall()]

    for team in all_teams:
        if team.lower() == query:
            return team

    matches = [t for t in all_teams if query in t.lower()]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        starts = [t for t in matches if t.lower().startswith(query)]
        if len(starts) == 1:
            return starts[0]
        return matches

    return None


# ── FIND THE MATCHUP ──────────────────────────────────────────────────────────

def find_game(conn, team1, team2):
    cursor = conn.execute("""
        SELECT DISTINCT game_id, home_team, away_team, commence_time
        FROM odds
        WHERE ((home_team = ? AND away_team = ?)
            OR (home_team = ? AND away_team = ?))
        AND commence_time > datetime('now')
        ORDER BY commence_time ASC
        LIMIT 1
    """, (team1, team2, team2, team1))
    return cursor.fetchone()


# ── FETCH CURRENT LINES ───────────────────────────────────────────────────────

def fetch_current_lines(conn, game_id):
    latest = conn.execute("""
        SELECT MAX(fetched_at) FROM odds WHERE game_id = ?
    """, (game_id,)).fetchone()[0]

    cursor = conn.execute("""
        SELECT bookmaker, market, outcome_name, price, point
        FROM odds
        WHERE game_id = ? AND fetched_at = ?
        ORDER BY bookmaker, market
    """, (game_id, latest))

    lines = {}
    for bookmaker, market, outcome, price, point in cursor.fetchall():
        if bookmaker not in lines:
            lines[bookmaker] = {"spreads": {}, "h2h": {}, "totals": {}}
        lines[bookmaker][market][outcome] = {
            "price": price,
            "point": point,
            "implied_prob": american_to_implied(price),
        }

    return lines, latest


# ── CROSS-BOOK SPREAD COMPARISON ──────────────────────────────────────────────

def analyze_line_disagreement(lines, fav_team, dog_team):
    """Compare spreads across available books. Flag outliers. Uses favorite perspective."""
    spreads = {}
    for bk in BOOKMAKER_ORDER:
        bk_data = lines.get(bk, {}).get("spreads", {}).get(fav_team, {})
        if bk_data and bk_data.get("point") is not None:
            spreads[bk] = bk_data["point"]

    if len(spreads) < 2:
        return "  Not enough books with spread data to compare."

    fav_short = fav_team.split()[0]
    books = list(spreads.keys())
    points = list(spreads.values())

    if max(points) - min(points) < 0.5:
        consensus = points[0]
        return f"  All books agree within 0.5 pts ({fav_short} {consensus:+.1f}). No cross-book divergence."

    disagreements = []
    for i, bk in enumerate(books):
        others = [p for j, p in enumerate(points) if j != i]
        avg_others = sum(others) / len(others)
        diff = spreads[bk] - avg_others
        if abs(diff) >= 0.5:
            label = BOOKMAKER_LABELS[bk].strip()
            other_labels = ", ".join(BOOKMAKER_LABELS[b].strip() for j, b in enumerate(books) if j != i)
            if diff < 0:
                direction = f"has {fav_short} at a bigger favorite than {other_labels}"
            else:
                direction = f"has {fav_short} at a smaller favorite than {other_labels}"
            disagreements.append(
                f"  *** BOOK DISAGREEMENT: {label} has {fav_short} {spreads[bk]:+.1f} "
                f"while {other_labels} average {avg_others:+.1f} ({abs(diff):.1f} pt gap) ***\n"
                f"  {label} {direction} — this may indicate different action or a slow line adjustment."
            )

    if not disagreements:
        spread_str = ", ".join(f"{BOOKMAKER_LABELS[b].strip()} {s:+.1f}" for b, s in spreads.items())
        return f"  Minor spread differences across books: {spread_str}. No significant outlier."

    return "\n".join(disagreements)


# ── FETCH LINE MOVEMENT (MULTI-BOOK) ──────────────────────────────────────────

def fetch_line_movement(conn, game_id, home_team, away_team):
    all_movements = {}

    for book in MOVEMENT_BOOKS:
        cursor = conn.execute("""
            SELECT outcome_name, point, fetched_at
            FROM odds
            WHERE game_id = ? AND market = 'spreads' AND bookmaker = ?
            ORDER BY fetched_at ASC
        """, (game_id, book))
        rows = cursor.fetchall()
        if not rows:
            continue

        history = {}
        for name, point, ts in rows:
            if name not in history:
                history[name] = {"first": point, "last": point, "first_ts": ts, "last_ts": ts}
            history[name]["last"] = point
            history[name]["last_ts"] = ts

        movements = []
        for team, data in history.items():
            if data["first"] is not None and data["last"] is not None:
                movement = data["last"] - data["first"]
                movements.append({
                    "team": team,
                    "open": data["first"],
                    "current": data["last"],
                    "movement": movement,
                    "first_seen": data["first_ts"][:16].replace("T", " "),
                    "last_seen": data["last_ts"][:16].replace("T", " "),
                })

        if movements:
            all_movements[book] = movements

    return all_movements


# ── FETCH TEAM STATS ──────────────────────────────────────────────────────────

def fetch_team_stats(conn, team_name):
    stats = conn.execute("""
        SELECT games_played, wins, losses, avg_points_for, avg_points_against,
               avg_margin, last5_record, last5_avg_margin, espn_team_id
        FROM team_stats
        WHERE team_name = ?
    """, (team_name,)).fetchone()

    if not stats:
        return None, []

    gp, w, l, ppg, papg, margin, last5, last5_margin, espn_id = stats

    games = conn.execute("""
        SELECT game_date, opponent_name, result, team_score, opponent_score,
               home_away, margin
        FROM game_results
        WHERE espn_team_id = ?
        ORDER BY game_date DESC
        LIMIT 10
    """, (espn_id,)).fetchall()

    summary = {
        "games_played": gp,
        "wins": w,
        "losses": l,
        "ppg": ppg,
        "papg": papg,
        "margin": margin,
        "last5_record": last5,
        "last5_margin": last5_margin,
    }

    return summary, games


# ── FETCH INJURIES ────────────────────────────────────────────────────────────

def fetch_injuries(conn, team_name):
    cursor = conn.execute("""
        SELECT player_name, position, status, detail
        FROM injuries
        WHERE team_name = ?
        ORDER BY status ASC
    """, (team_name,))
    return cursor.fetchall()


# ── FORMAT INJURIES BLOCK ─────────────────────────────────────────────────────

def format_injuries(injuries, team_name):
    if not injuries:
        return "  No injuries reported."

    lines = []
    for player, position, status, detail in injuries:
        pos_str = f" ({position})" if position else ""
        detail_str = f" — {detail}" if detail else ""
        if status and status.lower() in ("out", "out for season"):
            lines.append(f"  *** OUT: {player}{pos_str}{detail_str} ***")
        elif status and status.lower() in ("doubtful", "questionable"):
            lines.append(f"  {status.upper()}: {player}{pos_str}{detail_str}")
        else:
            lines.append(f"  {status}: {player}{pos_str}{detail_str}")

    return "\n".join(lines)


# ── HOME/AWAY SPLITS ──────────────────────────────────────────────────────────

def home_away_splits(games):
    home = [g for g in games if g[5] == "home"]
    away = [g for g in games if g[5] == "away"]

    def record_and_margin(game_list):
        if not game_list:
            return "N/A", None
        w = sum(1 for g in game_list if g[2] == "W")
        avg_m = sum(g[6] for g in game_list) / len(game_list)
        return f"{w}-{len(game_list) - w}", round(avg_m, 1)

    home_rec, home_margin = record_and_margin(home)
    away_rec, away_margin = record_and_margin(away)
    return home_rec, home_margin, away_rec, away_margin


# ── DAYS OF REST ──────────────────────────────────────────────────────────────

def days_of_rest(games, game_date_str):
    if not games:
        return None

    try:
        upcoming = datetime.strptime(game_date_str[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None

    for g in games:
        try:
            last_played = datetime.strptime(g[0], "%Y-%m-%d").date()
            delta = (upcoming - last_played).days
            if delta > 0:
                return delta
        except (ValueError, TypeError):
            continue

    return None


# ── SCORING MATCHUP ANALYSIS ─────────────────────────────────────────────────

def scoring_matchup(fav_stats, dog_stats, fav_team, dog_team):
    """Generate pre-computed matchup insights. Uses favorite/underdog framing."""
    if not fav_stats or not dog_stats:
        return "  Insufficient stats to compute matchup analysis."

    lines = []
    fav_short = fav_team.split()[0]
    dog_short = dog_team.split()[0]

    combined_ppg = fav_stats["ppg"] + dog_stats["ppg"]
    combined_papg = fav_stats["papg"] + dog_stats["papg"]
    expected_total = round((combined_ppg + combined_papg) / 4, 1)
    lines.append(f"  Combined scoring pace: {fav_stats['ppg']:.1f} + {dog_stats['ppg']:.1f} = {combined_ppg:.1f} ppg combined")
    lines.append(f"  Combined defensive yield: {fav_stats['papg']:.1f} + {dog_stats['papg']:.1f} = {combined_papg:.1f} papg combined")
    lines.append(f"  Rough expected total (avg of offense + defense): {expected_total}")

    fav_off_vs_dog_def = fav_stats["ppg"] - dog_stats["papg"]
    dog_off_vs_fav_def = dog_stats["ppg"] - fav_stats["papg"]

    if fav_off_vs_dog_def > 3:
        lines.append(f"  {fav_short} offense ({fav_stats['ppg']:.1f} ppg) vs {dog_short} defense ({dog_stats['papg']:.1f} papg): +{fav_off_vs_dog_def:.1f} mismatch favoring {fav_short}")
    elif fav_off_vs_dog_def < -3:
        lines.append(f"  {fav_short} offense ({fav_stats['ppg']:.1f} ppg) vs {dog_short} defense ({dog_stats['papg']:.1f} papg): {fav_off_vs_dog_def:.1f} — {dog_short} defense should limit {fav_short}")
    else:
        lines.append(f"  {fav_short} offense ({fav_stats['ppg']:.1f} ppg) vs {dog_short} defense ({dog_stats['papg']:.1f} papg): even matchup ({fav_off_vs_dog_def:+.1f})")

    if dog_off_vs_fav_def > 3:
        lines.append(f"  {dog_short} offense ({dog_stats['ppg']:.1f} ppg) vs {fav_short} defense ({fav_stats['papg']:.1f} papg): +{dog_off_vs_fav_def:.1f} mismatch favoring {dog_short}")
    elif dog_off_vs_fav_def < -3:
        lines.append(f"  {dog_short} offense ({dog_stats['ppg']:.1f} ppg) vs {fav_short} defense ({fav_stats['papg']:.1f} papg): {dog_off_vs_fav_def:.1f} — {fav_short} defense should limit {dog_short}")
    else:
        lines.append(f"  {dog_short} offense ({dog_stats['ppg']:.1f} ppg) vs {fav_short} defense ({fav_stats['papg']:.1f} papg): even matchup ({dog_off_vs_fav_def:+.1f})")

    margin_diff = fav_stats["margin"] - dog_stats["margin"]
    lines.append(f"  Season margin comparison: {fav_short} {fav_stats['margin']:+.1f} vs {dog_short} {dog_stats['margin']:+.1f} (diff: {margin_diff:+.1f})")

    return "\n".join(lines)


# ── TOTALS LINE MOVEMENT ─────────────────────────────────────────────────────

def fetch_totals_movement(conn, game_id):
    all_movements = {}

    for book in TOTALS_MOVEMENT_BOOKS:
        cursor = conn.execute("""
            SELECT outcome_name, point, fetched_at
            FROM odds
            WHERE game_id = ? AND market = 'totals' AND bookmaker = ?
            AND outcome_name = 'Over'
            ORDER BY fetched_at ASC
        """, (game_id, book))
        rows = cursor.fetchall()
        if not rows:
            continue

        first_point = rows[0][1]
        first_ts = rows[0][2]
        last_point = rows[-1][1]
        last_ts = rows[-1][2]

        if first_point is not None and last_point is not None:
            movement = last_point - first_point
            all_movements[book] = {
                "open": first_point,
                "current": last_point,
                "movement": movement,
                "first_seen": first_ts[:16].replace("T", " "),
                "last_seen": last_ts[:16].replace("T", " "),
            }

    return all_movements


# ── CROSS-BOOK TOTALS COMPARISON ──────────────────────────────────────────────

def analyze_totals_disagreement(lines):
    totals = {}
    for bk in BOOKMAKER_ORDER:
        bk_over = lines.get(bk, {}).get("totals", {}).get("Over", {})
        if bk_over and bk_over.get("point") is not None:
            totals[bk] = bk_over["point"]

    if len(totals) < 2:
        return "  Not enough books with totals data to compare."

    books = list(totals.keys())
    points = list(totals.values())

    if max(points) - min(points) < 1.0:
        consensus = sum(points) / len(points)
        return f"  All books agree on the total within 1 point (consensus ~{consensus:.1f}). No cross-book divergence."

    disagreements = []
    for i, bk in enumerate(books):
        others = [p for j, p in enumerate(points) if j != i]
        avg_others = sum(others) / len(others)
        diff = totals[bk] - avg_others
        if abs(diff) >= 1.0:
            label = BOOKMAKER_LABELS[bk].strip()
            other_labels = ", ".join(BOOKMAKER_LABELS[b].strip() for j, b in enumerate(books) if j != i)
            direction = "higher" if diff > 0 else "lower"
            disagreements.append(
                f"  *** TOTALS DISAGREEMENT: {label} has O/U {totals[bk]:.1f} "
                f"while {other_labels} average {avg_others:.1f} ({abs(diff):.1f} pts {direction}) ***"
            )

    if not disagreements:
        totals_str = ", ".join(f"{BOOKMAKER_LABELS[b].strip()} {t:.1f}" for b, t in totals.items())
        return f"  Minor totals differences across books: {totals_str}. No significant outlier."

    return "\n".join(disagreements)


# ── TOTALS ANALYSIS (pre-computed) ────────────────────────────────────────────

def totals_analysis(lines, fav_stats, dog_stats, fav_team, dog_team, totals_movements):
    if not fav_stats or not dog_stats:
        return "  Insufficient stats to analyze totals."

    result_lines = []

    posted_total = None
    source_book = None
    for bk in ["draftkings", "fanduel", "betmgm"]:
        over = lines.get(bk, {}).get("totals", {}).get("Over", {})
        if over and over.get("point") is not None:
            posted_total = over["point"]
            source_book = bk
            break

    if posted_total is None:
        return "  No totals line available."

    fav_expected = (fav_stats["ppg"] + dog_stats["papg"]) / 2
    dog_expected = (dog_stats["ppg"] + fav_stats["papg"]) / 2
    expected_total = round(fav_expected + dog_expected, 1)
    gap = round(expected_total - posted_total, 1)

    fav_short = fav_team.split()[0]
    dog_short = dog_team.split()[0]

    result_lines.append(f"  Posted O/U: {posted_total} (from {BOOKMAKER_LABELS.get(source_book, source_book).strip()})")
    result_lines.append(f"  Expected {fav_short} output: ({fav_stats['ppg']:.1f} ppg + {dog_stats['papg']:.1f} papg) / 2 = {fav_expected:.1f}")
    result_lines.append(f"  Expected {dog_short} output: ({dog_stats['ppg']:.1f} ppg + {fav_stats['papg']:.1f} papg) / 2 = {dog_expected:.1f}")
    result_lines.append(f"  Expected total: {expected_total}")
    result_lines.append(f"  Gap vs posted line: {gap:+.1f} points")
    result_lines.append(f"  NOTE: This estimate uses raw season averages which tend to run HIGH (blowouts")
    result_lines.append(f"  inflate offensive numbers). The books use more sophisticated models. A gap under")
    result_lines.append(f"  5 points should NOT be treated as a signal on its own — it needs confirmation")
    result_lines.append(f"  from line movement or cross-book disagreement to be actionable.")

    if gap > 5:
        result_lines.append(f"  *** OVER SIGNAL: Expected total is {gap:.1f} points ABOVE the posted line. This is a large gap that may indicate a genuine over. ***")
    elif gap < -5:
        result_lines.append(f"  *** UNDER SIGNAL: Expected total is {abs(gap):.1f} points BELOW the posted line. This is a large gap that may indicate a genuine under. ***")
    elif gap > 3:
        result_lines.append(f"  Slight lean OVER: expected total is {gap:.1f} above the line. This alone is NOT enough to bet — needs confirming signals (line movement or book disagreement).")
    elif gap < -3:
        result_lines.append(f"  Slight lean UNDER: expected total is {abs(gap):.1f} below the line. This alone is NOT enough to bet — needs confirming signals (line movement or book disagreement).")
    else:
        result_lines.append(f"  Total is well-priced. Gap of {gap:+.1f} is within noise range. No edge from stats alone.")

    if totals_movements:
        result_lines.append("")
        result_lines.append("  Totals line movement:")
        for book, m in totals_movements.items():
            label = BOOKMAKER_LABELS.get(book, book).strip()
            if m["movement"] > 0:
                result_lines.append(f"    {label}: opened {m['open']:.1f} -> now {m['current']:.1f} (moved UP {m['movement']:.1f} — market is adjusting toward OVER)")
            elif m["movement"] < 0:
                result_lines.append(f"    {label}: opened {m['open']:.1f} -> now {m['current']:.1f} (moved DOWN {abs(m['movement']):.1f} — market is adjusting toward UNDER)")
            else:
                result_lines.append(f"    {label}: opened {m['open']:.1f} -> no movement. Market confident in this number.")

        if len(totals_movements) >= 2:
            books = list(totals_movements.keys())
            m1 = totals_movements[books[0]]["movement"]
            m2 = totals_movements[books[1]]["movement"]
            if m1 != 0 and m2 != 0 and (m1 > 0) != (m2 > 0):
                result_lines.append("    *** DIVERGENT TOTALS MOVEMENT: Books moved the total in opposite directions. ***")

    return "\n".join(result_lines)


# ── SPREAD DIRECTION SUMMARY ─────────────────────────────────────────────────

def spread_direction_summary(lines, fav_stats, dog_stats, fav_team, dog_team,
                              fav_rest, dog_rest, is_neutral):
    """Generate a plain-English summary of what the spread data says."""
    if not fav_stats or not dog_stats:
        return "  Insufficient stats for spread summary."

    fav_short = fav_team.split()[0]
    dog_short = dog_team.split()[0]

    result = []

    # Identify the favorite from the spread
    fav_info = identify_favorite(lines, fav_team, dog_team)
    if fav_info:
        favorite, underdog, fav_spread = fav_info
        spread_abs = abs(fav_spread)
        if fav_spread == 0:
            result.append(f"  PICK'EM: Spread is 0. No favorite.")
        else:
            site_label = "NEUTRAL SITE" if is_neutral else ""
            if site_label:
                result.append(f"  *** NEUTRAL SITE GAME — home/away from the API is arbitrary. Ignore home court advantage. ***")
            result.append(f"  FAVORITE: {favorite} — spread {fav_spread:+.1f}")
            result.append(f"  UNDERDOG: {underdog} — spread +{spread_abs:.1f}")
    else:
        result.append("  No spread data available.")
        return "\n".join(result)

    # Margin comparison
    margin_diff = fav_stats["margin"] - dog_stats["margin"]
    result.append(f"  Season margin: {fav_short} {fav_stats['margin']:+.1f}, {dog_short} {dog_stats['margin']:+.1f} (difference: {margin_diff:+.1f})")

    if abs(margin_diff) < 2:
        result.append(f"  MARGIN VERDICT: Nearly even — supports a close game consistent with a tight spread.")
    elif margin_diff > 0:
        result.append(f"  MARGIN VERDICT: {fav_short} has a {margin_diff:.1f}-point better season margin. Data favors {fav_short}.")
    else:
        result.append(f"  MARGIN VERDICT: {dog_short} has a {abs(margin_diff):.1f}-point better season margin. Data favors {dog_short}.")

    # Last 5 trend
    fav_l5 = fav_stats.get("last5_margin")
    dog_l5 = dog_stats.get("last5_margin")
    if fav_l5 is not None and dog_l5 is not None:
        l5_diff = fav_l5 - dog_l5
        result.append(f"  Last 5 margin: {fav_short} {fav_l5:+.1f}, {dog_short} {dog_l5:+.1f} (difference: {l5_diff:+.1f})")
        if abs(l5_diff) < 2:
            result.append(f"  RECENT FORM VERDICT: Both teams in similar recent form.")
        elif l5_diff > 0:
            result.append(f"  RECENT FORM VERDICT: {fav_short} trending better recently by {l5_diff:.1f} ppg margin.")
        else:
            result.append(f"  RECENT FORM VERDICT: {dog_short} trending better recently by {abs(l5_diff):.1f} ppg margin.")

    # Rest advantage
    if fav_rest is not None and dog_rest is not None:
        rest_diff = fav_rest - dog_rest
        if rest_diff > 1:
            result.append(f"  REST ADVANTAGE: {fav_short} ({fav_rest} days) has {rest_diff} more days rest than {dog_short} ({dog_rest} days).")
        elif rest_diff < -1:
            result.append(f"  REST ADVANTAGE: {dog_short} ({dog_rest} days) has {abs(rest_diff)} more days rest than {fav_short} ({fav_rest} days).")
        elif fav_rest <= 1 and dog_rest <= 1:
            result.append(f"  REST: Both teams on back-to-back ({fav_short}: {fav_rest} day, {dog_short}: {dog_rest} day). Fatigue is a factor for BOTH sides equally.")
        else:
            result.append(f"  REST: Similar rest ({fav_short}: {fav_rest} days, {dog_short}: {dog_rest} days). No significant advantage.")

    return "\n".join(result)


# ── TOTALS DIRECTION SUMMARY ──────────────────────────────────────────────────

def totals_direction_summary(lines, fav_stats, dog_stats, fav_team, dog_team, totals_movements):
    if not fav_stats or not dog_stats:
        return "  Insufficient stats for totals summary."

    result = []

    posted_total = None
    for bk in BOOKMAKER_ORDER:
        over = lines.get(bk, {}).get("totals", {}).get("Over", {})
        if over and over.get("point") is not None:
            posted_total = over["point"]
            break

    if posted_total is None:
        return "  No totals line available."

    fav_expected = (fav_stats["ppg"] + dog_stats["papg"]) / 2
    dog_expected = (dog_stats["ppg"] + fav_stats["papg"]) / 2
    expected_total = round(fav_expected + dog_expected, 1)
    gap = round(expected_total - posted_total, 1)

    result.append(f"  Posted O/U line: {posted_total}")
    result.append(f"  Our expected total: {expected_total}")

    if gap > 0:
        result.append(f"  DIRECTION: Our expected total ({expected_total}) is ABOVE the posted line ({posted_total}) by {gap:.1f} points.")
        result.append(f"  This means raw stats lean OVER — but see threshold analysis below for whether this gap is meaningful.")
    elif gap < 0:
        result.append(f"  DIRECTION: Our expected total ({expected_total}) is BELOW the posted line ({posted_total}) by {abs(gap):.1f} points.")
        result.append(f"  This means raw stats lean UNDER — but see threshold analysis below for whether this gap is meaningful.")
    else:
        result.append(f"  DIRECTION: Expected total exactly matches the posted line. No lean either way.")

    if abs(gap) >= 5:
        direction_word = "OVER" if gap > 0 else "UNDER"
        result.append(f"  THRESHOLD VERDICT: Gap of {abs(gap):.1f} points EXCEEDS the 5-point threshold. This is a meaningful {direction_word} signal from stats.")
    elif abs(gap) >= 3:
        direction_word = "OVER" if gap > 0 else "UNDER"
        result.append(f"  THRESHOLD VERDICT: Gap of {abs(gap):.1f} points is in the 3-5 point range. Slight lean {direction_word} but NOT enough alone — needs confirming signals.")
    else:
        result.append(f"  THRESHOLD VERDICT: Gap of {abs(gap):.1f} points is BELOW the 3-point threshold. No meaningful edge. The line is well-priced.")

    if totals_movements:
        moved_over = 0
        moved_under = 0
        for book, m in totals_movements.items():
            if m["movement"] > 0:
                moved_over += 1
            elif m["movement"] < 0:
                moved_under += 1

        if moved_over > 0 and moved_under == 0:
            result.append(f"  MOVEMENT: Line has moved UP (toward OVER) across books.")
            if gap > 0:
                result.append(f"  CONFIRMATION: Movement agrees with our OVER lean. Stronger signal.")
            else:
                result.append(f"  CONFLICT: Movement is toward OVER but our stats lean UNDER. Mixed signal.")
        elif moved_under > 0 and moved_over == 0:
            result.append(f"  MOVEMENT: Line has moved DOWN (toward UNDER) across books.")
            if gap < 0:
                result.append(f"  CONFIRMATION: Movement agrees with our UNDER lean. Stronger signal.")
            else:
                result.append(f"  CONFLICT: Movement is toward UNDER but our stats lean OVER. Mixed signal.")
        elif moved_over > 0 and moved_under > 0:
            result.append(f"  MOVEMENT: Books moved in OPPOSITE directions on the total. No clear market consensus.")
        else:
            result.append(f"  MOVEMENT: No movement on the total. Market is confident in this number.")

    return "\n".join(result)


# ── KEY FACTS BLOCK ───────────────────────────────────────────────────────────

def build_key_facts(fav_team, dog_team, lines, fav_stats, dog_stats,
                    fav_rest, dog_rest, expected_total, posted_total, is_neutral,
                    fav_ats=None, dog_ats=None, fav_bt=None, dog_bt=None):
    fav_short = fav_team.split()[0]
    dog_short = dog_team.split()[0]
    facts = []

    # Fact 0: Neutral site warning
    if is_neutral:
        facts.append(f"• *** NEUTRAL SITE GAME. The home/away labels from the odds API are ARBITRARY.")
        facts.append(f"  Do NOT give either team home court advantage. Evaluate on team quality only. ***")

    # Fact 1: Who is the favorite (always from spread)
    fav_info = identify_favorite(lines, fav_team, dog_team)
    if fav_info:
        favorite, underdog, fav_spread = fav_info
        if fav_spread == 0:
            facts.append(f"• PICK'EM game. No favorite.")
        else:
            facts.append(f"• {favorite} is the FAVORITE (spread {fav_spread:+.1f}). {underdog} is the UNDERDOG (spread +{abs(fav_spread):.1f}).")

    # Fact 2: Margin comparison
    if fav_stats and dog_stats:
        margin_diff = fav_stats["margin"] - dog_stats["margin"]
        if abs(margin_diff) >= 2:
            better = fav_short if margin_diff > 0 else dog_short
            facts.append(f"• {better} has the better season margin by {abs(margin_diff):.1f} points.")
        else:
            facts.append(f"• Season margins are nearly equal ({fav_short} {fav_stats['margin']:+.1f} vs {dog_short} {dog_stats['margin']:+.1f}).")

    # Fact 3: Totals direction
    if expected_total is not None and posted_total is not None:
        gap = round(expected_total - posted_total, 1)
        if gap > 0:
            facts.append(f"• Expected total ({expected_total}) is ABOVE the line ({posted_total}) by {gap:.1f}. Raw stats lean OVER.")
        elif gap < 0:
            facts.append(f"• Expected total ({expected_total}) is BELOW the line ({posted_total}) by {abs(gap):.1f}. Raw stats lean UNDER.")
        else:
            facts.append(f"• Expected total ({expected_total}) MATCHES the line ({posted_total}). No lean.")

        if abs(gap) < 5:
            facts.append(f"• The {abs(gap):.1f}-point gap is BELOW the 5-point signal threshold. This is NOT a strong totals signal.")
        else:
            direction_word = "OVER" if gap > 0 else "UNDER"
            facts.append(f"• The {abs(gap):.1f}-point gap EXCEEDS the 5-point signal threshold. This IS a meaningful {direction_word} signal.")

    # Fact 4: Rest situation
    if fav_rest is not None and dog_rest is not None:
        if fav_rest <= 1 and dog_rest <= 1:
            facts.append(f"• BOTH teams are on back-to-back rest. Fatigue affects both equally.")
        elif fav_rest <= 1:
            facts.append(f"• {fav_short} is on back-to-back rest ({fav_rest} day). {dog_short} has {dog_rest} days rest. Fatigue disadvantage for {fav_short}.")
        elif dog_rest <= 1:
            facts.append(f"• {dog_short} is on back-to-back rest ({dog_rest} day). {fav_short} has {fav_rest} days rest. Fatigue disadvantage for {dog_short}.")
    
    if fav_ats:
        fw = fav_ats["ats_overall"]["w"]
        fl = fav_ats["ats_overall"]["l"]
        total = fw + fl + fav_ats["ats_overall"]["push"]
        if total > 0:
            pct = round(fw / total * 100, 1)
            facts.append(f"• {fav_short} is {fw}-{fl} ATS this season ({pct}% cover rate).")
            if pct <= 45:
                facts.append(f"• *** {fav_short} is a POOR cover team. The market overvalues them. ***")
    if dog_ats:
        dw = dog_ats["ats_overall"]["w"]
        dl = dog_ats["ats_overall"]["l"]
        total = dw + dl + dog_ats["ats_overall"]["push"]
        if total > 0:
            pct = round(dw / total * 100, 1)
            facts.append(f"• {dog_short} is {dw}-{dl} ATS this season ({pct}% cover rate).")
            if pct >= 55:
                facts.append(f"• *** {dog_short} is a GOOD cover team. The market undervalues them. ***")

    # Efficiency metrics (Barttorvik)
    if fav_bt and dog_bt and fav_bt['adj_em'] is not None and dog_bt['adj_em'] is not None:
        em_gap = round(fav_bt['adj_em'] - dog_bt['adj_em'], 2)
        if em_gap < 0:
            facts.append(f"• *** EFFICIENCY ALERT: {dog_short} (T-Rank #{dog_bt['rank']}, "
                         f"AdjEM {dog_bt['adj_em']:+.2f}) has a BETTER efficiency rating than "
                         f"{fav_short} (#{fav_bt['rank']}, AdjEM {fav_bt['adj_em']:+.2f}). "
                         f"The underdog is the better team by this metric. ***")
        else:
            facts.append(f"• Efficiency edge: {fav_short} #{fav_bt['rank']} (AdjEM {fav_bt['adj_em']:+.2f}) "
                         f"vs {dog_short} #{dog_bt['rank']} (AdjEM {dog_bt['adj_em']:+.2f}) "
                         f"— gap of {em_gap:+.2f}.")

    return "\n".join(facts)


# ── FORMAT LINES BLOCK ────────────────────────────────────────────────────────
# v4: Always presents favorite first in spread display.

def format_lines(lines, fav_team, dog_team):
    lines_text = []
    for bk in BOOKMAKER_ORDER:
        if bk not in lines:
            continue
        label = BOOKMAKER_LABELS[bk]
        data = lines[bk]

        # Spread — always show favorite first
        spread_fav = data["spreads"].get(fav_team, {})
        spread_dog = data["spreads"].get(dog_team, {})
        spread_str = "N/A"
        if spread_fav and spread_dog:
            fp = spread_fav.get("price")
            fpt = spread_fav.get("point")
            f_impl = spread_fav.get("implied_prob")
            dp = spread_dog.get("price")
            dpt = spread_dog.get("point")
            d_impl = spread_dog.get("implied_prob")
            spread_str = (
                f"{fav_team.split()[0]} {fpt:+.1f} ({fp:+d}, implied {f_impl}%) | "
                f"{dog_team.split()[0]} {dpt:+.1f} ({dp:+d}, implied {d_impl}%)"
            )

        # Total
        over = data["totals"].get("Over", {})
        under = data["totals"].get("Under", {})
        total_str = ""
        if over:
            o_impl = over.get("implied_prob")
            u_impl = under.get("implied_prob") if under else None
            total_str = f"O/U {over.get('point', 'N/A')}"
            if o_impl and u_impl:
                total_str += f" (Over implied {o_impl}% | Under implied {u_impl}%)"

        # Moneyline — favorite first
        ml_fav = data["h2h"].get(fav_team, {})
        ml_dog = data["h2h"].get(dog_team, {})
        ml_str = ""
        if ml_fav and ml_dog:
            f_ml_impl = ml_fav.get("implied_prob")
            d_ml_impl = ml_dog.get("implied_prob")
            ml_str = (
                f"ML: {fav_team.split()[0]} {ml_fav.get('price', ''):+d} (win prob ~{f_ml_impl}%) | "
                f"{dog_team.split()[0]} {ml_dog.get('price', ''):+d} (win prob ~{d_ml_impl}%)"
            )

        lines_text.append(f"  {label}")
        lines_text.append(f"    Spread: {spread_str}")
        if total_str:
            lines_text.append(f"    Total:  {total_str}")
        if ml_str:
            lines_text.append(f"    {ml_str}")
        lines_text.append("")

    return "\n".join(lines_text) if lines_text else "  No line data available."

def build_section1_text(lines, fav_team, dog_team, disagreement, totals_disagree):
    """
    Pre-render Section 1 (WHAT THE LINES ARE SAYING) so the LLM copies it
    verbatim instead of restating numbers — prevents hallucinated spreads/totals.
    """
    fav_short = fav_team.split()[0]
 
    section = []
    section.append("**1. WHAT THE LINES ARE SAYING**")
    section.append("The current spread, total, and moneyline from each book are:")
 
    for bk in BOOKMAKER_ORDER:
        if bk not in lines:
            continue
        label = BOOKMAKER_LABELS[bk].strip()
        data = lines[bk]
 
        spread_fav = data["spreads"].get(fav_team, {})
        over = data["totals"].get("Over", {})
        ml_fav = data["h2h"].get(fav_team, {})
 
        parts = []
        if spread_fav and spread_fav.get("point") is not None:
            parts.append(f"{fav_short} {spread_fav['point']:+.1f}")
        if over and over.get("point") is not None:
            parts.append(f"O/U {over['point']}")
        if ml_fav and ml_fav.get("price") is not None:
            parts.append(f"ML {fav_short} {ml_fav['price']:+d}")
 
        if parts:
            section.append(f"- {label}: {', '.join(parts)}")
 
    section.append("")
    if "DISAGREE" in disagreement.upper() or "OUTLIER" in disagreement.upper():
        section.append(f"Cross-book spread comparison: {disagreement.strip()}")
    else:
        section.append("Cross-book spread comparison: Books are in agreement — no significant divergence.")
 
    if "DISAGREE" in totals_disagree.upper() or "OUTLIER" in totals_disagree.upper():
        section.append(f"Cross-book totals comparison: {totals_disagree.strip()}")
    else:
        section.append("Cross-book totals comparison: Books are in agreement — no significant divergence.")
 
    return "\n".join(section)

# ── FORMAT TEAM BLOCK ─────────────────────────────────────────────────────────

def format_team_block(team_name, stats, games, rest_days, role_label):
    """role_label is e.g. 'FAVORITE' or 'UNDERDOG' or 'HOME' or 'AWAY'."""
    if not stats:
        return f"  No stats available for {team_name}."

    home_rec, home_margin, away_rec, away_margin = home_away_splits(games)

    lines = [
        f"  Record: {stats['wins']}-{stats['losses']} ({stats['games_played']} games)",
        f"  Avg margin: {stats['margin']:+.1f} | Pts for: {stats['ppg']:.1f} | Pts against: {stats['papg']:.1f}",
        f"  Last 5: {stats['last5_record']} (avg margin: {stats['last5_margin']:+.1f})",
    ]

    home_str = f"Home: {home_rec}"
    if home_margin is not None:
        home_str += f" (avg margin: {home_margin:+.1f})"
    away_str = f"Away: {away_rec}"
    if away_margin is not None:
        away_str += f" (avg margin: {away_margin:+.1f})"
    lines.append(f"  {home_str} | {away_str}")

    if rest_days is not None:
        if rest_days <= 1:
            lines.append(f"  Days of rest: {rest_days} *** BACK-TO-BACK — fatigue factor ***")
        elif rest_days <= 2:
            lines.append(f"  Days of rest: {rest_days} (short rest)")
        elif rest_days >= 7:
            lines.append(f"  Days of rest: {rest_days} (extended break — possible rust)")
        else:
            lines.append(f"  Days of rest: {rest_days}")
    else:
        lines.append("  Days of rest: unknown")

    if games:
        lines.append("  Recent games:")
        for date, opp, result, ts, os_, ha, margin in games[:5]:
            loc = "vs" if ha == "home" else "@"
            lines.append(f"    {date}  {result}  {loc} {opp}  {ts}-{os_} ({margin:+d})")

    return "\n".join(lines)


# ── FORMAT LINE MOVEMENT BLOCK (MULTI-BOOK) ──────────────────────────────────

def format_movement(all_movements, fav_team):
    """Format movement, interpreting direction relative to the favorite."""
    if not all_movements:
        return "  No movement data (only one fetch recorded so far)."

    fav_short = fav_team.split()[0]
    sections = []
    for book, movements in all_movements.items():
        label = BOOKMAKER_LABELS.get(book, book)
        book_lines = [f"  {label}:"]
        any_moved = False
        for m in movements:
            team_short = m["team"].split()[0]
            direction = "no movement"
            if m["movement"] > 0:
                direction = f"moved +{m['movement']:.1f} (line grew — movement toward underdog)"
                any_moved = True
            elif m["movement"] < 0:
                direction = f"moved {m['movement']:.1f} (line shrunk — movement toward favorite)"
                any_moved = True
            book_lines.append(f"    {team_short}: opened {m['open']:+.1f} → now {m['current']:+.1f} ({direction})")
        book_lines.append(f"    (tracked from {movements[0]['first_seen']} to {movements[0]['last_seen']})")
        if not any_moved:
            book_lines.append("    → Line has not moved. Market is confident in this number.")
        sections.append("\n".join(book_lines))

    if len(all_movements) >= 2:
        books = list(all_movements.keys())
        b1_moved = next((m for m in all_movements[books[0]] if m["movement"] != 0), None)
        b2_moved = next((m for m in all_movements[books[1]] if m["movement"] != 0), None)

        if b1_moved and b2_moved:
            if (b1_moved["movement"] > 0) != (b2_moved["movement"] > 0):
                sections.append("  *** DIVERGENT MOVEMENT: Books moved in opposite directions. This is a significant signal. ***")
            else:
                sections.append("  Both books moved in the same direction — market consensus.")
        elif b1_moved or b2_moved:
            mover = books[0] if b1_moved else books[1]
            sections.append(f"  Only {BOOKMAKER_LABELS.get(mover, mover).strip()} has moved. The other book held steady.")
        else:
            sections.append("  Neither book has moved. Strong market consensus on this number.")

    return "\n\n".join(sections)

# ── FETCH ATS RECORDS ─────────────────────────────────────────────────────────

def fetch_ats_records(conn, team_name):
    """
    Fetch ATS records for a team from the team_ats table.
    Returns a dict with all ATS splits, or None if not available.
    """
    try:
        row = conn.execute("""
            SELECT ats_overall_w, ats_overall_l, ats_overall_push, ats_overall_margin,
                   ats_home_w, ats_home_l, ats_home_push, ats_home_margin,
                   ats_away_w, ats_away_l, ats_away_push, ats_away_margin,
                   ats_fav_w, ats_fav_l, ats_fav_push, ats_fav_margin,
                   ats_dog_w, ats_dog_l, ats_dog_push, ats_dog_margin,
                   ou_over, ou_under
            FROM team_ats
            WHERE team_name = ?
        """, (team_name,)).fetchone()
    except Exception:
        return None

    if not row:
        return None

    return {
        "ats_overall": {"w": row[0], "l": row[1], "push": row[2], "margin": row[3]},
        "ats_home": {"w": row[4], "l": row[5], "push": row[6], "margin": row[7]},
        "ats_away": {"w": row[8], "l": row[9], "push": row[10], "margin": row[11]},
        "ats_fav": {"w": row[12], "l": row[13], "push": row[14], "margin": row[15]},
        "ats_dog": {"w": row[16], "l": row[17], "push": row[18], "margin": row[19]},
        "ou": {"over": row[20], "under": row[21]},
    }


# ── FORMAT ATS BLOCK ──────────────────────────────────────────────────────────

def format_ats_block(team_name, ats, role, is_home, is_neutral):
    """
    Format ATS records for the context block.

    role: "FAVORITE" or "UNDERDOG" (from the spread)
    is_home: whether this team is the API home team
    is_neutral: whether this is a neutral site game
    """
    if not ats:
        return "  No ATS data available."

    short = team_name.split()[0]
    lines = []

    # Overall ATS
    overall = ats["ats_overall"]
    push_str = f"-{overall['push']}" if overall["push"] > 0 else ""
    lines.append(
        f"  Overall ATS: {overall['w']}-{overall['l']}{push_str} "
        f"(ATS margin: {overall['margin']:+.1f})"
    )

    # Determine if team is covering or not
    total_games = overall["w"] + overall["l"] + overall["push"]
    if total_games > 0:
        cover_pct = round(overall["w"] / total_games * 100, 1)
        if cover_pct >= 55:
            lines.append(f"  *** GOOD COVER TEAM: {short} covers {cover_pct}% of the time ***")
        elif cover_pct <= 45:
            lines.append(f"  *** POOR COVER TEAM: {short} only covers {cover_pct}% of the time ***")

    # Home/Away ATS (skip if neutral site since it's not relevant)
    if not is_neutral:
        if is_home:
            home = ats["ats_home"]
            push_str = f"-{home['push']}" if home["push"] > 0 else ""
            lines.append(
                f"  ATS at home: {home['w']}-{home['l']}{push_str} "
                f"(margin: {home['margin']:+.1f})"
            )
        else:
            away = ats["ats_away"]
            push_str = f"-{away['push']}" if away["push"] > 0 else ""
            lines.append(
                f"  ATS on road: {away['w']}-{away['l']}{push_str} "
                f"(margin: {away['margin']:+.1f})"
            )

    # ATS as favorite or underdog (contextual to this game's role)
    if role == "FAVORITE":
        fav = ats["ats_fav"]
        fav_total = fav["w"] + fav["l"] + fav["push"]
        push_str = f"-{fav['push']}" if fav["push"] > 0 else ""
        lines.append(
            f"  ATS as favorite: {fav['w']}-{fav['l']}{push_str} "
            f"(margin: {fav['margin']:+.1f})"
        )
        if fav_total >= 10:
            fav_cover_pct = round(fav["w"] / fav_total * 100, 1)
            if fav_cover_pct <= 45:
                lines.append(
                    f"  *** WARNING: {short} covers only {fav_cover_pct}% as favorite. "
                    f"Market tends to overvalue them. ***"
                )
            elif fav_cover_pct >= 55:
                lines.append(
                    f"  *** {short} covers {fav_cover_pct}% as favorite — "
                    f"market consistently undervalues them. ***"
                )
    else:
        dog = ats["ats_dog"]
        dog_total = dog["w"] + dog["l"] + dog["push"]
        push_str = f"-{dog['push']}" if dog["push"] > 0 else ""
        lines.append(
            f"  ATS as underdog: {dog['w']}-{dog['l']}{push_str} "
            f"(margin: {dog['margin']:+.1f})"
        )
        if dog_total >= 5:
            dog_cover_pct = round(dog["w"] / dog_total * 100, 1)
            if dog_cover_pct >= 55:
                lines.append(
                    f"  *** SCRAPPY UNDERDOG: {short} covers {dog_cover_pct}% as underdog. ***"
                )

    # O/U record
    ou = ats["ou"]
    ou_total = ou["over"] + ou["under"]
    if ou_total > 0:
        lines.append(f"  O/U record: {ou['over']}-{ou['under']} (Over-Under)")
        over_pct = round(ou["over"] / ou_total * 100, 1)
        if over_pct >= 58:
            lines.append(f"  *** HIGH OVER TEAM: {short} goes Over {over_pct}% of games ***")
        elif over_pct <= 42:
            lines.append(f"  *** HIGH UNDER TEAM: {short} goes Under {round(100 - over_pct, 1)}% of games ***")

    return "\n".join(lines)

# ── FETCH BARTTORVIK (EFFICIENCY) STATS ───────────────────────────────────────

def fetch_barttorvik_stats(conn, odds_team_name):
    """
    Match an Odds API team name to a Barttorvik row.
    Barttorvik uses short names ('Duke', 'Michigan St.') while the Odds API
    uses full names ('Duke Blue Devils', 'Michigan St Spartans').

    Strategy 1: find all barttorvik names where the odds name starts with the
    barttorvik name (after stripping periods). Take the longest match to
    correctly distinguish 'Michigan' from 'Michigan St.'.

    Strategy 2 (fallback): barttorvik name is a substring of the odds name.
    """
    try:
        rows = conn.execute(
            "SELECT team_name, rank, adj_oe, adj_de, adj_em, adj_t, barthag FROM barttorvik_stats"
        ).fetchall()
    except Exception:
        return None

    if not rows:
        return None

    def normalize(name):
        return name.lower().replace('.', '').strip()

    odds_lower = normalize(odds_team_name)

    # Strategy 1: odds name starts with barttorvik name — take longest match
    prefix_matches = []
    for row in rows:
        bt_norm = normalize(row[0])
        if odds_lower == bt_norm or odds_lower.startswith(bt_norm + ' '):
            prefix_matches.append((len(bt_norm), row))

    if prefix_matches:
        best = max(prefix_matches, key=lambda x: x[0])[1]
        return {"team_name": best[0], "rank": best[1], "adj_oe": best[2],
                "adj_de": best[3], "adj_em": best[4], "adj_t": best[5], "barthag": best[6]}

    # Strategy 2: barttorvik name contained in odds name
    for row in rows:
        bt_norm = normalize(row[0])
        if len(bt_norm) >= 4 and bt_norm in odds_lower:
            return {"team_name": row[0], "rank": row[1], "adj_oe": row[2],
                    "adj_de": row[3], "adj_em": row[4], "adj_t": row[5], "barthag": row[6]}

    return None


# ── FORMAT EFFICIENCY BLOCK ───────────────────────────────────────────────────

def format_efficiency_block(fav_team, dog_team, fav_bt, dog_bt):
    """
    Format Barttorvik efficiency metrics for the LLM context.
    AdjOE/AdjDE are schedule-adjusted points per 100 possessions.
    AdjEM = AdjOE - AdjDE (higher = better team overall).
    Tempo = adjusted possessions per 40 min (higher = faster pace).
    """
    fav_short = fav_team.split()[0]
    dog_short = dog_team.split()[0]
    lines = []

    def team_line(short, bt):
        if not bt:
            return f"  {short}: not found in Barttorvik data"
        em  = f"{bt['adj_em']:+.2f}" if bt['adj_em']  is not None else "N/A"
        oe  = f"{bt['adj_oe']:.1f}"  if bt['adj_oe']  is not None else "N/A"
        de  = f"{bt['adj_de']:.1f}"  if bt['adj_de']  is not None else "N/A"
        t   = f"{bt['adj_t']:.1f}"   if bt['adj_t']   is not None else "N/A"
        bar = f"{bt['barthag']:.3f}" if bt['barthag'] is not None else "N/A"
        return (f"  {short}: T-Rank #{bt['rank']}  AdjEM {em}  "
                f"(AdjO {oe} | AdjD {de})  Tempo {t}  Barthag {bar}")

    lines.append(team_line(fav_short, fav_bt))
    lines.append(team_line(dog_short, dog_bt))

    if fav_bt and dog_bt and fav_bt['adj_em'] is not None and dog_bt['adj_em'] is not None:
        em_gap = round(fav_bt['adj_em'] - dog_bt['adj_em'], 2)
        rank_gap = dog_bt['rank'] - fav_bt['rank']
        lines.append("")
        lines.append(f"  AdjEM gap: {fav_short} leads by {em_gap:+.2f} efficiency points "
                     f"(#{fav_bt['rank']} vs #{dog_bt['rank']}, a {abs(rank_gap)}-rank gap)")

        if em_gap < 0:
            lines.append(f"  *** EFFICIENCY REVERSAL: {dog_short} has the BETTER efficiency "
                         f"rating despite being the underdog. The market may be mispricing this game. ***")
        elif em_gap > 20:
            lines.append(f"  Dominant efficiency edge for {fav_short}. The spread may understate their true advantage.")
        elif em_gap > 10:
            lines.append(f"  Meaningful efficiency edge for {fav_short}.")
        elif em_gap < 5:
            lines.append(f"  Small efficiency gap — closer game than the spread might suggest.")

        # Tempo mismatch — signal for totals
        if fav_bt['adj_t'] is not None and dog_bt['adj_t'] is not None:
            tempo_gap = abs(fav_bt['adj_t'] - dog_bt['adj_t'])
            faster = fav_short if fav_bt['adj_t'] > dog_bt['adj_t'] else dog_short
            slower = dog_short if fav_bt['adj_t'] > dog_bt['adj_t'] else fav_short
            lines.append("")
            if tempo_gap >= 4:
                lines.append(f"  *** PACE MISMATCH: {faster} ({max(fav_bt['adj_t'], dog_bt['adj_t']):.1f}) "
                             f"plays significantly faster than {slower} ({min(fav_bt['adj_t'], dog_bt['adj_t']):.1f}). "
                             f"Pace conflict is a totals signal — game will likely play at a compromise tempo. ***")
            elif tempo_gap >= 2:
                lines.append(f"  Moderate pace difference: {faster} is faster ({max(fav_bt['adj_t'], dog_bt['adj_t']):.1f} "
                             f"vs {min(fav_bt['adj_t'], dog_bt['adj_t']):.1f}). Mild totals consideration.")
            else:
                avg_t = (fav_bt['adj_t'] + dog_bt['adj_t']) / 2
                lines.append(f"  Similar pace ({fav_short} {fav_bt['adj_t']:.1f} vs {dog_short} {dog_bt['adj_t']:.1f} — avg ~{avg_t:.1f}). No pace mismatch.")

    elif not fav_bt and not dog_bt:
        return "  Barttorvik data not available for either team."

    return "\n".join(lines)


# ── MAIN ENTRY POINT ──────────────────────────────────────────────────────────

def build_context(team1_query, team2_query):
    conn = sqlite3.connect(DB_PATH)

    # Resolve team names
    team1 = find_team_name(conn, team1_query)
    team2 = find_team_name(conn, team2_query)

    if isinstance(team1, list):
        conn.close()
        return f"Ambiguous team name '{team1_query}'...", ""
    if isinstance(team2, list):
        conn.close()
        return f"Ambiguous team name '{team2_query}'...", ""
    if not team1:
        conn.close()
        return f"Could not find team matching '{team1_query}'...", ""
    if not team2:
        conn.close()
        return f"Could not find team matching '{team2_query}'...", ""

    # Find the game
    game = find_game(conn, team1, team2)
    if not game:
        conn.close()
        return f"No upcoming game found between {team1} and {team2}.", ""

    game_id, api_home, api_away, commence_time = game

    # Parse tip time
    try:
        tip = datetime.fromisoformat(commence_time.replace("Z", "+00:00"))
        tip_str = tip.strftime("%A %B %-d, %Y  %I:%M %p ET")
        game_date_str = commence_time[:10]
    except Exception:
        tip_str = commence_time
        game_date_str = commence_time[:10] if len(commence_time) >= 10 else None

    # Detect neutral site
    is_neutral = detect_neutral_site(commence_time)

    # Fetch all data
    lines, latest_fetch = fetch_current_lines(conn, game_id)

    # ── v4: Identify the favorite and orient everything around that ──
    # This is the key change. Instead of using the API's home/away to frame
    # the analysis, we use the spread to determine who is favored and build
    # the entire context from that perspective. This prevents the model from
    # getting confused when the API's home/away assignment is wrong or
    # arbitrary (tournament games, neutral sites).
    fav_info = identify_favorite(lines, api_home, api_away)
    if fav_info:
        fav_team, dog_team, fav_spread = fav_info
    else:
        # No spread data — fall back to API home/away
        fav_team, dog_team = api_home, api_away

    movements = fetch_line_movement(conn, game_id, api_home, api_away)
    totals_movements = fetch_totals_movement(conn, game_id)
    fav_stats, fav_games = fetch_team_stats(conn, fav_team)
    dog_stats, dog_games = fetch_team_stats(conn, dog_team)

    # Fetch injuries
    fav_injuries = fetch_injuries(conn, fav_team)
    dog_injuries = fetch_injuries(conn, dog_team)

    # Pre-compute rest days
    fav_rest = days_of_rest(fav_games, game_date_str) if game_date_str else None
    dog_rest = days_of_rest(dog_games, game_date_str) if game_date_str else None

    fav_ats = fetch_ats_records(conn, fav_team)
    dog_ats = fetch_ats_records(conn, dog_team)

    fav_bt = fetch_barttorvik_stats(conn, fav_team)
    dog_bt = fetch_barttorvik_stats(conn, dog_team)

    conn.close()

    fav_is_home = (fav_team == api_home)
    dog_is_home = (dog_team == api_home)
    fav_ats_str = format_ats_block(fav_team, fav_ats, "FAVORITE", fav_is_home, is_neutral)
    dog_ats_str = format_ats_block(dog_team, dog_ats, "UNDERDOG", dog_is_home, is_neutral)

    # Line disagreement analysis (spreads) — now uses favorite perspective
    disagreement = analyze_line_disagreement(lines, fav_team, dog_team)

    # Totals disagreement analysis
    totals_disagree = analyze_totals_disagreement(lines)

    section1_text = build_section1_text(lines, fav_team, dog_team, disagreement, totals_disagree)

    # Scoring matchup analysis — favorite/underdog framing
    matchup = scoring_matchup(fav_stats, dog_stats, fav_team, dog_team)

    # Totals edge analysis
    totals_edge = totals_analysis(lines, fav_stats, dog_stats, fav_team, dog_team, totals_movements)

    # Format injuries
    fav_injuries_str = format_injuries(fav_injuries, fav_team)
    dog_injuries_str = format_injuries(dog_injuries, dog_team)

    # Efficiency block (Barttorvik)
    efficiency_block = format_efficiency_block(fav_team, dog_team, fav_bt, dog_bt)

    # Pre-compute directional summaries
    spread_summary = spread_direction_summary(lines, fav_stats, dog_stats,
                                               fav_team, dog_team,
                                               fav_rest, dog_rest, is_neutral)
    totals_summary = totals_direction_summary(lines, fav_stats, dog_stats,
                                               fav_team, dog_team,
                                               totals_movements)

    # Compute expected total for KEY FACTS block
    posted_total = None
    for bk in BOOKMAKER_ORDER:
        over = lines.get(bk, {}).get("totals", {}).get("Over", {})
        if over and over.get("point") is not None:
            posted_total = over["point"]
            break

    expected_total = None
    if fav_stats and dog_stats:
        fav_expected = (fav_stats["ppg"] + dog_stats["papg"]) / 2
        dog_expected = (dog_stats["ppg"] + fav_stats["papg"]) / 2
        expected_total = round(fav_expected + dog_expected, 1)

    key_facts = build_key_facts(fav_team, dog_team, lines, fav_stats,
                                 dog_stats, fav_rest, dog_rest,
                                 expected_total, posted_total, is_neutral,
                                 fav_ats, dog_ats, fav_bt, dog_bt)

    # ── v4: Context labels ──
    # For neutral site games: label as FAVORITE / UNDERDOG
    # For regular season games: label as FAVORITE (HOME) / UNDERDOG (AWAY) etc.
    if is_neutral:
        matchup_line = f"MATCHUP: {fav_team} vs {dog_team} (NEUTRAL SITE)"
        fav_label = "FAVORITE"
        dog_label = "UNDERDOG"
        venue_note = "VENUE: Neutral site (tournament game). Home court advantage does NOT apply."
    else:
        # Determine if favorite is home or away
        if fav_team == api_home:
            fav_ha = "HOME"
            dog_ha = "AWAY"
        else:
            fav_ha = "AWAY"
            dog_ha = "HOME"
        matchup_line = f"MATCHUP: {dog_team} @ {fav_team}" if fav_team == api_home else f"MATCHUP: {fav_team} @ {dog_team}"
        fav_label = f"FAVORITE ({fav_ha})"
        dog_label = f"UNDERDOG ({dog_ha})"
        venue_note = f"VENUE: {api_home} is the home team."

    # Assemble context block
    context = f"""{matchup_line}
GAME TIME: {tip_str}
{venue_note}
DATA AS OF: {latest_fetch[:16].replace("T", " ")} UTC

══════════════════════════════════════════════════════════════════════
KEY FACTS — DO NOT CONTRADICT THESE IN YOUR ANALYSIS
These are pre-computed from the data. Your job is to EXPLAIN them,
not re-derive or reverse them.
══════════════════════════════════════════════════════════════════════
{key_facts}

══════════════════════════════════════════════════════════════════════
SPREAD DIRECTION SUMMARY (pre-computed — use this as your north star)
══════════════════════════════════════════════════════════════════════
{spread_summary}

══════════════════════════════════════════════════════════════════════
TOTALS DIRECTION SUMMARY (pre-computed — use this as your north star)
══════════════════════════════════════════════════════════════════════
{totals_summary}

══════════════════════════════════════════════════════════════════════
PRE-RENDERED SECTION 1 — COPY THIS VERBATIM INTO YOUR RESPONSE
Do not reword, do not change any numbers, do not add commentary.
══════════════════════════════════════════════════════════════════════
{section1_text}

══════════════════════════════════════════════════════════════════════
CURRENT LINES (implied probabilities pre-calculated from American odds)
══════════════════════════════════════════════════════════════════════
{format_lines(lines, fav_team, dog_team)}

══════════════════════════════════════════════════════════════════════
CROSS-BOOK SPREAD COMPARISON
══════════════════════════════════════════════════════════════════════
{disagreement}

══════════════════════════════════════════════════════════════════════
CROSS-BOOK TOTALS COMPARISON
══════════════════════════════════════════════════════════════════════
{totals_disagree}

══════════════════════════════════════════════════════════════════════
LINE MOVEMENT — SPREADS (tracked across fetches)
══════════════════════════════════════════════════════════════════════
{format_movement(movements, fav_team)}

══════════════════════════════════════════════════════════════════════
ATS RECORDS — {fav_team.upper()} ({fav_label})
══════════════════════════════════════════════════════════════════════
{fav_ats_str}

══════════════════════════════════════════════════════════════════════
ATS RECORDS — {dog_team.upper()} ({dog_label})
══════════════════════════════════════════════════════════════════════
{dog_ats_str}

══════════════════════════════════════════════════════════════════════
SCORING MATCHUP ANALYSIS (pre-computed)
══════════════════════════════════════════════════════════════════════
{matchup}

══════════════════════════════════════════════════════════════════════
TOTALS ANALYSIS (pre-computed — expected total vs posted O/U)
══════════════════════════════════════════════════════════════════════
{totals_edge}

══════════════════════════════════════════════════════════════════════
EFFICIENCY METRICS — Barttorvik T-Rank (schedule-adjusted)
AdjOE/AdjDE = pts per 100 possessions vs avg D1 opponent
AdjEM = AdjOE − AdjDE (higher = better team)
Tempo = adjusted possessions per 40 min (higher = faster)
Barthag = estimated win probability vs avg D1 team (0–1)
══════════════════════════════════════════════════════════════════════
{efficiency_block}

══════════════════════════════════════════════════════════════════════
INJURIES — {fav_team.upper()} ({fav_label})
══════════════════════════════════════════════════════════════════════
{fav_injuries_str}

══════════════════════════════════════════════════════════════════════
INJURIES — {dog_team.upper()} ({dog_label})
══════════════════════════════════════════════════════════════════════
{dog_injuries_str}

══════════════════════════════════════════════════════════════════════
{fav_team.upper()} ({fav_label})
══════════════════════════════════════════════════════════════════════
{format_team_block(fav_team, fav_stats, fav_games, fav_rest, fav_label)}

══════════════════════════════════════════════════════════════════════
{dog_team.upper()} ({dog_label})
══════════════════════════════════════════════════════════════════════
{format_team_block(dog_team, dog_stats, dog_games, dog_rest, dog_label)}
"""

    return context.strip(), section1_text