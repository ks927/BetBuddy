# retrieval.py (v2)
# Pulls everything the LLM needs to analyze a matchup from the local database.
#
# v2 changes over v1:
#   - Pre-computes implied probabilities so the model doesn't do math
#   - Tracks line movement across multiple books (Pinnacle + DraftKings)
#   - Flags sharp vs. recreational line disagreements explicitly
#   - Calculates days of rest / schedule density from game log
#   - Pre-computes scoring matchup context (offense vs. defense)
#   - Computes pace indicator (combined scoring environment)
#
# The philosophy: give the model CONCLUSIONS from the data, not raw numbers
# it has to interpret. Every quantitative insight should be pre-digested.

import sqlite3
import os
from datetime import datetime, timezone, timedelta

DB_PATH = os.path.join(os.path.dirname(__file__), "db/sports.db")

# Bookmakers in priority order for display.
# We don't have Pinnacle on the free tier, so we compare across the three
# recreational books instead. When one book disagrees with the other two,
# that's a signal — it means one book is either slow to adjust or is seeing
# different action than the others.
BOOKMAKER_ORDER = ["draftkings", "fanduel", "betmgm"]
BOOKMAKER_LABELS = {
    "draftkings": "DraftKings ",
    "fanduel": "FanDuel    ",
    "betmgm": "BetMGM     ",
}

# Books to track for movement comparison
MOVEMENT_BOOKS = ["draftkings", "fanduel"]

# Books to track for totals movement
TOTALS_MOVEMENT_BOOKS = ["draftkings", "fanduel"]


# ── IMPLIED PROBABILITY ───────────────────────────────────────────────────────
# Convert American odds to implied probability. This is arithmetic the LLM
# should never be asked to do — it will get it wrong.
#
#   Negative odds (e.g. -110): prob = |odds| / (|odds| + 100)
#   Positive odds (e.g. +150): prob = 100 / (odds + 100)

def american_to_implied(price):
    """Convert American odds to implied probability as a percentage."""
    if price is None:
        return None
    if price < 0:
        return round(abs(price) / (abs(price) + 100) * 100, 1)
    else:
        return round(100 / (price + 100) * 100, 1)


# ── TEAM MATCHING ─────────────────────────────────────────────────────────────
# Same fuzzy matching as v1 — no changes needed here.

def find_team_name(conn, query):
    query = query.strip().lower()
    cursor = conn.execute("""
        SELECT DISTINCT home_team FROM odds
        UNION
        SELECT DISTINCT away_team FROM odds
    """)
    all_teams = [row[0] for row in cursor.fetchall()]

    # Exact match first
    for team in all_teams:
        if team.lower() == query:
            return team

    # Partial match
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
# Same as v1 but we also compute implied probabilities for every price.

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
# Without Pinnacle, we compare DraftKings, FanDuel, and BetMGM against each
# other. When one book's spread disagrees with the other two by 0.5+ points,
# it means that book is either slow to move or seeing different action.
# The consensus of two books is more likely to be the "true" number.

def analyze_line_disagreement(lines, home_team, away_team):
    """Compare spreads across available books. Flag outliers."""
    spreads = {}
    for bk in BOOKMAKER_ORDER:
        bk_data = lines.get(bk, {}).get("spreads", {}).get(home_team, {})
        if bk_data and bk_data.get("point") is not None:
            spreads[bk] = bk_data["point"]

    if len(spreads) < 2:
        return "  Not enough books with spread data to compare."

    home_short = home_team.split()[0]
    books = list(spreads.keys())
    points = list(spreads.values())

    # Check if all books agree
    if max(points) - min(points) < 0.5:
        consensus = points[0]
        return f"  All books agree within 0.5 pts ({home_short} {consensus:+.1f}). No cross-book divergence."

    # Find the outlier — the book that disagrees with the others
    disagreements = []
    for i, bk in enumerate(books):
        others = [p for j, p in enumerate(points) if j != i]
        avg_others = sum(others) / len(others)
        diff = spreads[bk] - avg_others
        if abs(diff) >= 0.5:
            label = BOOKMAKER_LABELS[bk].strip()
            other_labels = ", ".join(BOOKMAKER_LABELS[b].strip() for j, b in enumerate(books) if j != i)
            if diff < 0:
                direction = f"has the home team at a tighter spread than {other_labels}"
            else:
                direction = f"has the home team at a wider spread than {other_labels}"
            disagreements.append(
                f"  *** BOOK DISAGREEMENT: {label} has {home_short} {spreads[bk]:+.1f} "
                f"while {other_labels} average {avg_others:+.1f} ({abs(diff):.1f} pt gap) ***\n"
                f"  {label} {direction} — this may indicate different action or a slow line adjustment."
            )

    if not disagreements:
        spread_str = ", ".join(f"{BOOKMAKER_LABELS[b].strip()} {s:+.1f}" for b, s in spreads.items())
        return f"  Minor spread differences across books: {spread_str}. No significant outlier."

    return "\n".join(disagreements)


# ── FETCH LINE MOVEMENT (MULTI-BOOK) ──────────────────────────────────────────
# v2: track movement at both Pinnacle and DraftKings. When they move in
# different directions, that's a strong signal.

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
# Pull current injuries from the database for a team.

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
        # Flag key statuses for the model
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
# Calculate how many days since the team's last game. This prevents the model
# from fabricating schedule density claims.

def days_of_rest(games, game_date_str):
    """Given a team's game log and the upcoming game date, return days since last game."""
    if not games:
        return None

    try:
        upcoming = datetime.strptime(game_date_str[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None

    for g in games:  # games are already sorted DESC by date
        try:
            last_played = datetime.strptime(g[0], "%Y-%m-%d").date()
            delta = (upcoming - last_played).days
            if delta > 0:
                return delta
        except (ValueError, TypeError):
            continue

    return None


# ── SCORING MATCHUP ANALYSIS ─────────────────────────────────────────────────
# Pre-digest the offensive vs. defensive matchup so the model gets conclusions
# rather than having to compare raw numbers.

def scoring_matchup(home_stats, away_stats, home_team, away_team):
    """Generate pre-computed matchup insights."""
    if not home_stats or not away_stats:
        return "  Insufficient stats to compute matchup analysis."

    lines = []

    # Pace / scoring environment
    combined_ppg = home_stats["ppg"] + away_stats["ppg"]
    combined_papg = home_stats["papg"] + away_stats["papg"]
    expected_total = round((combined_ppg + combined_papg) / 4, 1)  # rough total estimate
    lines.append(f"  Combined scoring pace: {home_stats['ppg']:.1f} + {away_stats['ppg']:.1f} = {combined_ppg:.1f} ppg combined")
    lines.append(f"  Combined defensive yield: {home_stats['papg']:.1f} + {away_stats['papg']:.1f} = {combined_papg:.1f} papg combined")
    lines.append(f"  Rough expected total (avg of offense + defense): {expected_total}")

    # Offensive vs defensive matchup — each direction
    home_short = home_team.split()[0]
    away_short = away_team.split()[0]

    home_off_vs_away_def = home_stats["ppg"] - away_stats["papg"]
    away_off_vs_home_def = away_stats["ppg"] - home_stats["papg"]

    if home_off_vs_away_def > 3:
        lines.append(f"  {home_short} offense ({home_stats['ppg']:.1f} ppg) vs {away_short} defense ({away_stats['papg']:.1f} papg): +{home_off_vs_away_def:.1f} mismatch favoring {home_short}")
    elif home_off_vs_away_def < -3:
        lines.append(f"  {home_short} offense ({home_stats['ppg']:.1f} ppg) vs {away_short} defense ({away_stats['papg']:.1f} papg): {home_off_vs_away_def:.1f} — {away_short} defense should limit {home_short}")
    else:
        lines.append(f"  {home_short} offense ({home_stats['ppg']:.1f} ppg) vs {away_short} defense ({away_stats['papg']:.1f} papg): even matchup ({home_off_vs_away_def:+.1f})")

    if away_off_vs_home_def > 3:
        lines.append(f"  {away_short} offense ({away_stats['ppg']:.1f} ppg) vs {home_short} defense ({home_stats['papg']:.1f} papg): +{away_off_vs_home_def:.1f} mismatch favoring {away_short}")
    elif away_off_vs_home_def < -3:
        lines.append(f"  {away_short} offense ({away_stats['ppg']:.1f} ppg) vs {home_short} defense ({home_stats['papg']:.1f} papg): {away_off_vs_home_def:.1f} — {home_short} defense should limit {away_short}")
    else:
        lines.append(f"  {away_short} offense ({away_stats['ppg']:.1f} ppg) vs {home_short} defense ({home_stats['papg']:.1f} papg): even matchup ({away_off_vs_home_def:+.1f})")

    # Margin comparison
    margin_diff = home_stats["margin"] - away_stats["margin"]
    lines.append(f"  Season margin comparison: {home_short} {home_stats['margin']:+.1f} vs {away_short} {away_stats['margin']:+.1f} (diff: {margin_diff:+.1f})")

    return "\n".join(lines)


# ── TOTALS LINE MOVEMENT ─────────────────────────────────────────────────────
# Track how the O/U total has moved across fetches, same multi-book approach
# as spread movement.

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
# Same approach as spreads — compare the three books against each other.
# A 1+ point disagreement on the total is meaningful.

def analyze_totals_disagreement(lines):
    """Compare totals across available books. Flag outliers."""
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
# Compare the posted O/U line against our expected total derived from team stats.
# Flag the gap and interpret it so the model doesn't have to.
#
# IMPORTANT: This estimate uses raw season averages, which tend to run HIGH
# because they include blowouts against weak teams. The books use pace-adjusted
# efficiency and strength of schedule — they're smarter than a simple average.
# We need wide thresholds to avoid systematically biasing toward OVER.

def totals_analysis(lines, home_stats, away_stats, home_team, away_team, totals_movements):
    """Pre-compute totals edge analysis."""
    if not home_stats or not away_stats:
        return "  Insufficient stats to analyze totals."

    result_lines = []

    # Get the consensus total from available books
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

    # Expected total from team stats
    # Method: average each team's offensive output with opponent's defensive yield
    home_expected = (home_stats["ppg"] + away_stats["papg"]) / 2
    away_expected = (away_stats["ppg"] + home_stats["papg"]) / 2
    expected_total = round(home_expected + away_expected, 1)
    gap = round(expected_total - posted_total, 1)

    home_short = home_team.split()[0]
    away_short = away_team.split()[0]

    result_lines.append(f"  Posted O/U: {posted_total} (from {BOOKMAKER_LABELS.get(source_book, source_book).strip()})")
    result_lines.append(f"  Expected {home_short} output: ({home_stats['ppg']:.1f} ppg + {away_stats['papg']:.1f} papg) / 2 = {home_expected:.1f}")
    result_lines.append(f"  Expected {away_short} output: ({away_stats['ppg']:.1f} ppg + {home_stats['papg']:.1f} papg) / 2 = {away_expected:.1f}")
    result_lines.append(f"  Expected total: {expected_total}")
    result_lines.append(f"  Gap vs posted line: {gap:+.1f} points")
    result_lines.append(f"  NOTE: This estimate uses raw season averages which tend to run HIGH (blowouts")
    result_lines.append(f"  inflate offensive numbers). The books use more sophisticated models. A gap under")
    result_lines.append(f"  5 points should NOT be treated as a signal on its own — it needs confirmation")
    result_lines.append(f"  from line movement or cross-book disagreement to be actionable.")

    # Thresholds raised to avoid systematic OVER bias:
    #   5+ points  = genuine signal worth flagging
    #   3-5 points = slight lean, needs confirmation
    #   under 3    = noise, market is probably right
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

    # Recent scoring trends — last 5 games combined
    home_last5_ppg = None
    away_last5_ppg = None
    if home_stats.get("last5_margin") is not None:
        # We can approximate recent scoring from margin + papg, but better to
        # just flag the trend direction
        pass

    # Totals movement
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

        # Cross-book comparison for totals
        if len(totals_movements) >= 2:
            books = list(totals_movements.keys())
            m1 = totals_movements[books[0]]["movement"]
            m2 = totals_movements[books[1]]["movement"]
            if m1 != 0 and m2 != 0 and (m1 > 0) != (m2 > 0):
                result_lines.append("    *** DIVERGENT TOTALS MOVEMENT: Books moved the total in opposite directions. ***")

    return "\n".join(result_lines)


# ── FORMAT LINES BLOCK ────────────────────────────────────────────────────────
# v2: includes implied probabilities inline.

def format_lines(lines, home_team, away_team):
    lines_text = []
    for bk in BOOKMAKER_ORDER:
        if bk not in lines:
            continue
        label = BOOKMAKER_LABELS[bk]
        data = lines[bk]

        # Spread with implied probability
        spread_home = data["spreads"].get(home_team, {})
        spread_away = data["spreads"].get(away_team, {})
        spread_str = "N/A"
        if spread_home and spread_away:
            hp = spread_home.get("price")
            hpt = spread_home.get("point")
            h_impl = spread_home.get("implied_prob")
            ap = spread_away.get("price")
            apt = spread_away.get("point")
            a_impl = spread_away.get("implied_prob")
            spread_str = (
                f"{home_team.split()[0]} {hpt:+.1f} ({hp:+d}, implied {h_impl}%) | "
                f"{away_team.split()[0]} {apt:+.1f} ({ap:+d}, implied {a_impl}%)"
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

        # Moneyline with implied probability
        ml_home = data["h2h"].get(home_team, {})
        ml_away = data["h2h"].get(away_team, {})
        ml_str = ""
        if ml_home and ml_away:
            h_ml_impl = ml_home.get("implied_prob")
            a_ml_impl = ml_away.get("implied_prob")
            ml_str = (
                f"ML: {home_team.split()[0]} {ml_home.get('price', ''):+d} (win prob ~{h_ml_impl}%) | "
                f"{away_team.split()[0]} {ml_away.get('price', ''):+d} (win prob ~{a_ml_impl}%)"
            )

        lines_text.append(f"  {label}")
        lines_text.append(f"    Spread: {spread_str}")
        if total_str:
            lines_text.append(f"    Total:  {total_str}")
        if ml_str:
            lines_text.append(f"    {ml_str}")
        lines_text.append("")  # blank line between books

    return "\n".join(lines_text) if lines_text else "  No line data available."


# ── FORMAT TEAM BLOCK ─────────────────────────────────────────────────────────
# v2: includes home/away margins and days of rest.

def format_team_block(team_name, stats, games, rest_days):
    if not stats:
        return f"  No stats available for {team_name}."

    home_rec, home_margin, away_rec, away_margin = home_away_splits(games)

    lines = [
        f"  Record: {stats['wins']}-{stats['losses']} ({stats['games_played']} games)",
        f"  Avg margin: {stats['margin']:+.1f} | Pts for: {stats['ppg']:.1f} | Pts against: {stats['papg']:.1f}",
        f"  Last 5: {stats['last5_record']} (avg margin: {stats['last5_margin']:+.1f})",
    ]

    # Home/away with margins
    home_str = f"Home: {home_rec}"
    if home_margin is not None:
        home_str += f" (avg margin: {home_margin:+.1f})"
    away_str = f"Away: {away_rec}"
    if away_margin is not None:
        away_str += f" (avg margin: {away_margin:+.1f})"
    lines.append(f"  {home_str} | {away_str}")

    # Days of rest
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

def format_movement(all_movements):
    if not all_movements:
        return "  No movement data (only one fetch recorded so far)."

    sections = []
    for book, movements in all_movements.items():
        label = BOOKMAKER_LABELS.get(book, book)
        book_lines = [f"  {label}:"]
        any_moved = False
        for m in movements:
            direction = "no movement"
            if m["movement"] > 0:
                direction = f"moved +{m['movement']:.1f} (line grew — movement toward underdog)"
                any_moved = True
            elif m["movement"] < 0:
                direction = f"moved {m['movement']:.1f} (line shrunk — movement toward favorite)"
                any_moved = True
            book_lines.append(f"    {m['team'].split()[0]}: opened {m['open']:+.1f} → now {m['current']:+.1f} ({direction})")
        book_lines.append(f"    (tracked from {movements[0]['first_seen']} to {movements[0]['last_seen']})")
        if not any_moved:
            book_lines.append("    → Line has not moved. Market is confident in this number.")
        sections.append("\n".join(book_lines))

    # Cross-book movement comparison
    if len(all_movements) >= 2:
        books = list(all_movements.keys())
        # Compare home team movement direction across books
        b1_home = next((m for m in all_movements[books[0]] if m["movement"] != 0), None)
        b2_home = next((m for m in all_movements[books[1]] if m["movement"] != 0), None)

        if b1_home and b2_home:
            if (b1_home["movement"] > 0) != (b2_home["movement"] > 0):
                sections.append("  *** DIVERGENT MOVEMENT: Sharp and recreational books moved in opposite directions. This is a significant signal. ***")
            else:
                sections.append("  Sharp and recreational books moved in the same direction — market consensus.")
        elif b1_home or b2_home:
            mover = books[0] if b1_home else books[1]
            sections.append(f"  Only {BOOKMAKER_LABELS.get(mover, mover).split('(')[0].strip()} has moved. The other book held steady.")
        else:
            sections.append("  Neither book has moved. Strong market consensus on this number.")

    return "\n\n".join(sections)


# ── MAIN ENTRY POINT ──────────────────────────────────────────────────────────

def build_context(team1_query, team2_query):
    conn = sqlite3.connect(DB_PATH)

    # Resolve team names
    team1 = find_team_name(conn, team1_query)
    team2 = find_team_name(conn, team2_query)

    if isinstance(team1, list):
        conn.close()
        return f"Ambiguous team name '{team1_query}'. Did you mean: {', '.join(team1)}?"
    if isinstance(team2, list):
        conn.close()
        return f"Ambiguous team name '{team2_query}'. Did you mean: {', '.join(team2)}?"
    if not team1:
        conn.close()
        return f"Could not find team matching '{team1_query}' in the odds database."
    if not team2:
        conn.close()
        return f"Could not find team matching '{team2_query}' in the odds database."

    # Find the game
    game = find_game(conn, team1, team2)
    if not game:
        conn.close()
        return f"No upcoming game found between {team1} and {team2}."

    game_id, home_team, away_team, commence_time = game

    # Parse tip time
    try:
        tip = datetime.fromisoformat(commence_time.replace("Z", "+00:00"))
        tip_str = tip.strftime("%A %B %-d, %Y  %I:%M %p ET")
        game_date_str = commence_time[:10]
    except Exception:
        tip_str = commence_time
        game_date_str = commence_time[:10] if len(commence_time) >= 10 else None

    # Fetch all data
    lines, latest_fetch = fetch_current_lines(conn, game_id)
    movements = fetch_line_movement(conn, game_id, home_team, away_team)
    totals_movements = fetch_totals_movement(conn, game_id)
    home_stats, home_games = fetch_team_stats(conn, home_team)
    away_stats, away_games = fetch_team_stats(conn, away_team)

    # Fetch injuries
    home_injuries = fetch_injuries(conn, home_team)
    away_injuries = fetch_injuries(conn, away_team)

    # Pre-compute rest days
    home_rest = days_of_rest(home_games, game_date_str) if game_date_str else None
    away_rest = days_of_rest(away_games, game_date_str) if game_date_str else None

    conn.close()

    # Line disagreement analysis (spreads)
    disagreement = analyze_line_disagreement(lines, home_team, away_team)

    # Totals disagreement analysis
    totals_disagree = analyze_totals_disagreement(lines)

    # Scoring matchup analysis
    matchup = scoring_matchup(home_stats, away_stats, home_team, away_team)

    # Totals edge analysis
    totals_edge = totals_analysis(lines, home_stats, away_stats, home_team, away_team, totals_movements)

    # Format injuries
    home_injuries_str = format_injuries(home_injuries, home_team)
    away_injuries_str = format_injuries(away_injuries, away_team)

    # Assemble context block
    context = f"""MATCHUP: {away_team} @ {home_team}
GAME TIME: {tip_str}
DATA AS OF: {latest_fetch[:16].replace("T", " ")} UTC

══════════════════════════════════════════════════════════════════════
CURRENT LINES (implied probabilities pre-calculated from American odds)
══════════════════════════════════════════════════════════════════════
{format_lines(lines, home_team, away_team)}

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
{format_movement(movements)}

══════════════════════════════════════════════════════════════════════
SCORING MATCHUP ANALYSIS (pre-computed)
══════════════════════════════════════════════════════════════════════
{matchup}

══════════════════════════════════════════════════════════════════════
TOTALS ANALYSIS (pre-computed — expected total vs posted O/U)
══════════════════════════════════════════════════════════════════════
{totals_edge}

══════════════════════════════════════════════════════════════════════
INJURIES — {home_team.upper()} (HOME)
══════════════════════════════════════════════════════════════════════
{home_injuries_str}

══════════════════════════════════════════════════════════════════════
INJURIES — {away_team.upper()} (AWAY)
══════════════════════════════════════════════════════════════════════
{away_injuries_str}

══════════════════════════════════════════════════════════════════════
{home_team.upper()} (HOME)
══════════════════════════════════════════════════════════════════════
{format_team_block(home_team, home_stats, home_games, home_rest)}

══════════════════════════════════════════════════════════════════════
{away_team.upper()} (AWAY)
══════════════════════════════════════════════════════════════════════
{format_team_block(away_team, away_stats, away_games, away_rest)}
"""

    return context.strip()