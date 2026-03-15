# publish.py
# Generates a static index.html showing today's full NCAAB schedule.
# Games with analysis show the LLM's recommendation; un-analyzed games
# show as schedule-only rows.
#
# Usage:
#   python3 publish.py              # generate only
#   python3 publish.py --push       # generate and push to gh-pages
#   make publish                    # run slate + generate + push

import sqlite3
import os
import sys
import re
import html
import subprocess
from datetime import datetime, date
from zoneinfo import ZoneInfo

DB_PATH = os.path.join(os.path.dirname(__file__), "db", "sports.db")
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "site")
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "index.html")

ET = ZoneInfo("America/New_York")


def normalize_team(name):
    return (
        name.lower().strip()
        .replace("state", "st").replace("saint", "st")
        .replace("'", "").replace(".", "").replace("-", " ")
    )


def teams_match(name_a, name_b):
    a = normalize_team(name_a)
    b = normalize_team(name_b)
    if a == b:
        return True
    if a in b or b in a:
        return True
    a_w, b_w = a.split(), b.split()
    if a_w and b_w and a_w[-1] == b_w[-1] and len(a_w[-1]) > 3:
        return True
    return False


def get_todays_schedule(conn):
    """Get all unique games from the odds table for today."""
    today_et = datetime.now(ET).date()

    cursor = conn.execute("""
        SELECT DISTINCT game_id, home_team, away_team, commence_time
        FROM odds
        WHERE sport = 'basketball_ncaab'
        AND replace(replace(commence_time, 'T', ' '), 'Z', '') > datetime('now', '-12 hours')
        ORDER BY commence_time ASC
    """)

    games = []
    seen = set()
    for game_id, home, away, commence in cursor.fetchall():
        try:
            tip_utc = datetime.fromisoformat(commence.replace("Z", "+00:00"))
            tip_date = tip_utc.astimezone(ET).date()
            if tip_date == today_et:
                key = (home, away)
                if key not in seen:
                    seen.add(key)
                    tip_str = tip_utc.astimezone(ET).strftime("%-I:%M %p ET")
                    games.append({
                        "game_id": game_id,
                        "home_team": home,
                        "away_team": away,
                        "commence_time": commence,
                        "tip_display": tip_str,
                    })
        except Exception:
            continue
    return games


def get_latest_spread(conn, game_id, team_name):
    """Get the latest spread for a team in a game."""
    row = conn.execute(
        """
        SELECT point FROM odds
        WHERE game_id = ? AND market = 'spreads' AND outcome_name = ?
        ORDER BY fetched_at DESC LIMIT 1
        """,
        (game_id, team_name),
    ).fetchone()
    return row[0] if row else None


def get_latest_total(conn, game_id):
    """Get the latest O/U total for a game."""
    row = conn.execute(
        """
        SELECT point FROM odds
        WHERE game_id = ? AND market = 'totals' AND outcome_name = 'Over'
        ORDER BY fetched_at DESC LIMIT 1
        """,
        (game_id,),
    ).fetchone()
    return row[0] if row else None


def get_predictions_for_game(conn, home_team, away_team, game_date):
    """Get predictions matching this game."""
    all_preds = conn.execute(
        """
        SELECT market, pick, confidence, analysis_text
        FROM predictions
        WHERE game_date = ? AND analysis_text IS NOT NULL
        ORDER BY predicted_at ASC
        """,
        (game_date,),
    ).fetchall()

    matching = []
    analysis_text = None
    for market, pick, confidence, analysis in all_preds:
        # We need to check against the prediction's teams — but predictions
        # store the query fragments, not full names. Check by looking at
        # all predictions for today that have analysis.
        # Since we can't easily match here, we'll match by checking the
        # prediction's own home/away fields.
        pass

    # Better approach: query with team matching
    all_preds = conn.execute(
        """
        SELECT home_team, away_team, market, pick, confidence, analysis_text
        FROM predictions
        WHERE game_date = ? AND analysis_text IS NOT NULL
        ORDER BY predicted_at ASC
        """,
        (game_date,),
    ).fetchall()

    for pred_home, pred_away, market, pick, confidence, analysis in all_preds:
        home_ok = teams_match(pred_home, home_team) or teams_match(pred_home, away_team)
        away_ok = teams_match(pred_away, away_team) or teams_match(pred_away, home_team)
        if home_ok and away_ok:
            matching.append({
                "market": market,
                "pick": pick,
                "confidence": confidence,
            })
            if analysis and not analysis_text:
                analysis_text = analysis

    return matching, analysis_text


def confidence_color(conf):
    return {"HIGH": "#ef4444", "MEDIUM": "#f59e0b", "LOW": "#6b7280"}.get(conf, "#6b7280")


def sanitize_analysis(text):
    """Convert analysis text to safe HTML with basic formatting."""
    if not text:
        return ""

    text = html.escape(text)
    # Bold **text**
    text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
    # Section headers
    text = re.sub(
        r'^(\d+\.\s+.+)$',
        r'<h4 style="color:#e2e8f0;margin:16px 0 8px;font-size:14px">\1</h4>',
        text,
        flags=re.MULTILINE,
    )
    # Paragraphs
    paragraphs = text.split("\n\n")
    formatted = []
    for p in paragraphs:
        p = p.strip()
        if p:
            if not p.startswith("<h4"):
                p = f"<p style='margin:0 0 12px;line-height:1.6'>{p}</p>"
            formatted.append(p)
    return "\n".join(formatted)


def generate_html(games, conn):
    """Generate the full HTML page."""
    today_str = date.today().strftime("%A, %B %-d, %Y")
    today_iso = date.today().isoformat()

    cards_html = ""
    analyzed_count = 0

    for i, game in enumerate(games):
        home = game["home_team"]
        away = game["away_team"]
        tip = game["tip_display"]

        # Get odds
        spread = get_latest_spread(conn, game["game_id"], home)
        total = get_latest_total(conn, game["game_id"])

        odds_parts = []
        if spread is not None:
            short_home = home.split()[0]
            odds_parts.append(f"{short_home} {spread:+.1f}")
        if total is not None:
            odds_parts.append(f"O/U {total}")
        odds_str = html.escape(" · ".join(odds_parts)) if odds_parts else ""

        # Get predictions
        picks, analysis_text = get_predictions_for_game(conn, home, away, today_iso)
        has_analysis = bool(analysis_text)
        if has_analysis:
            analyzed_count += 1

        # Build picks chips
        picks_html = ""
        if picks:
            for p in picks:
                if p["market"] == "pass":
                    picks_html += f"""
                    <div class="pick-chip pass">
                        <span class="pick-label">PASS</span>
                        <span class="pick-conf">No Edge</span>
                    </div>"""
                elif p["market"] == "unknown":
                    continue
                else:
                    conf_col = confidence_color(p["confidence"])
                    picks_html += f"""
                    <div class="pick-chip">
                        <span class="pick-label">{html.escape(p['pick'])}</span>
                        <span class="pick-conf" style="color:{conf_col}">{p['confidence']}</span>
                    </div>"""

        # Build analysis panel
        analysis_panel = ""
        if has_analysis:
            analysis_html = sanitize_analysis(analysis_text)
            analysis_panel = f"""
                <div class="analysis-panel" id="game-{i}">
                    <div class="analysis-content">{analysis_html}</div>
                </div>"""

        # Card class and click behavior
        card_class = "game-card" + (" analyzed" if has_analysis else " pending")
        onclick = f'onclick="toggleAnalysis(\'game-{i}\')"' if has_analysis else ""

        # Status indicator
        if has_analysis:
            status = '<span class="status-dot analyzed"></span>'
        else:
            status = '<span class="status-dot pending"></span>'

        cards_html += f"""
        <div class="{card_class}" {onclick}>
            <div class="game-header">
                <div class="matchup">
                    {status}
                    <span class="team away">{html.escape(away)}</span>
                    <span class="at">@</span>
                    <span class="team home">{html.escape(home)}</span>
                </div>
                <div class="game-meta">
                    <span class="odds-line">{odds_str}</span>
                    <span class="tip-time">{tip}</span>
                    {'<span class="expand-icon" id="icon-game-' + str(i) + '">▸</span>' if has_analysis else ''}
                </div>
            </div>
            {f'<div class="picks-row">{picks_html}</div>' if picks_html else ''}
            {analysis_panel}
        </div>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>BetBuddy — Today's NCAAB Slate</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;700&family=DM+Sans:wght@400;500;600;700&display=swap" rel="stylesheet">
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}

        :root {{
            --bg: #09090b;
            --bg-card: #111113;
            --bg-chip: #18181b;
            --bg-header: #09090b;
            --border: #1c1c22;
            --border-hover: #27272a;
            --text: #e2e8f0;
            --text-heading: #f4f4f5;
            --text-team: #e4e4e7;
            --text-muted: #71717a;
            --text-dim: #52525b;
            --text-faint: #3f3f46;
            --text-footer: #27272a;
            --text-analysis: #a1a1aa;
            --text-analysis-strong: #e4e4e7;
            --accent: #3b82f6;
            --dot-pending: #27272a;
            --chip-border: #27272a;
            --pending-opacity: 0.55;
        }}

        body.light {{
            --bg: #fafafa;
            --bg-card: #ffffff;
            --bg-chip: #f4f4f5;
            --bg-header: #fafafa;
            --border: #e4e4e7;
            --border-hover: #d4d4d8;
            --text: #27272a;
            --text-heading: #09090b;
            --text-team: #18181b;
            --text-muted: #71717a;
            --text-dim: #a1a1aa;
            --text-faint: #d4d4d8;
            --text-footer: #d4d4d8;
            --text-analysis: #52525b;
            --text-analysis-strong: #18181b;
            --accent: #2563eb;
            --dot-pending: #d4d4d8;
            --chip-border: #e4e4e7;
            --pending-opacity: 0.45;
        }}

        body {{
            font-family: 'DM Sans', sans-serif;
            background: var(--bg);
            color: var(--text);
            min-height: 100vh;
            transition: background 0.2s, color 0.2s;
        }}

        .header {{
            background: var(--bg-header);
            border-bottom: 1px solid var(--border);
            padding: 28px 24px 20px;
        }}

        .header-inner {{
            max-width: 720px;
            margin: 0 auto;
            display: flex;
            justify-content: space-between;
            align-items: baseline;
        }}

        .header h1 {{
            font-family: 'JetBrains Mono', monospace;
            font-size: 22px;
            font-weight: 700;
            color: var(--text-heading);
            letter-spacing: -0.5px;
        }}

        .header h1 span {{
            color: var(--accent);
        }}

        .header .date {{
            font-size: 13px;
            color: var(--text-dim);
        }}

        .subheader {{
            max-width: 720px;
            margin: 0 auto;
            padding: 16px 24px 8px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }}

        .game-count {{
            font-size: 13px;
            color: var(--text-muted);
        }}

        .legend {{
            display: flex;
            gap: 16px;
            font-size: 12px;
            color: var(--text-dim);
        }}

        .legend-item {{
            display: flex;
            align-items: center;
            gap: 6px;
        }}

        .container {{
            max-width: 720px;
            margin: 0 auto;
            padding: 8px 16px 48px;
        }}

        .game-card {{
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 10px;
            margin-bottom: 8px;
            overflow: hidden;
            transition: border-color 0.15s, background 0.2s;
        }}

        .game-card.analyzed {{
            cursor: pointer;
        }}

        .game-card.analyzed:hover {{
            border-color: var(--border-hover);
        }}

        .game-card.pending {{
            opacity: var(--pending-opacity);
        }}

        .game-header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 14px 16px;
            gap: 12px;
        }}

        .matchup {{
            display: flex;
            align-items: center;
            gap: 8px;
            flex-wrap: wrap;
            min-width: 0;
        }}

        .status-dot {{
            width: 7px;
            height: 7px;
            border-radius: 50%;
            flex-shrink: 0;
        }}

        .status-dot.analyzed {{
            background: var(--accent);
        }}

        .status-dot.pending {{
            background: var(--dot-pending);
        }}

        .team {{
            font-weight: 600;
            font-size: 14px;
            color: var(--text-team);
        }}

        .at {{
            color: var(--text-faint);
            font-size: 12px;
        }}

        .game-meta {{
            display: flex;
            align-items: center;
            gap: 14px;
            flex-shrink: 0;
        }}

        .odds-line {{
            font-family: 'JetBrains Mono', monospace;
            font-size: 12px;
            color: var(--text-dim);
        }}

        .tip-time {{
            font-family: 'JetBrains Mono', monospace;
            font-size: 12px;
            color: var(--text-faint);
        }}

        .expand-icon {{
            color: var(--text-faint);
            font-size: 13px;
            transition: transform 0.15s;
        }}

        .expand-icon.open {{
            transform: rotate(90deg);
        }}

        .picks-row {{
            display: flex;
            gap: 6px;
            padding: 0 16px 12px;
            flex-wrap: wrap;
        }}

        .pick-chip {{
            display: flex;
            align-items: center;
            gap: 8px;
            background: var(--bg-chip);
            border: 1px solid var(--chip-border);
            border-radius: 6px;
            padding: 6px 12px;
        }}

        .pick-chip.pass {{
            opacity: 0.5;
        }}

        .pick-label {{
            font-family: 'JetBrains Mono', monospace;
            font-size: 12px;
            font-weight: 700;
            color: var(--text-heading);
        }}

        .pick-conf {{
            font-size: 10px;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }}

        .analysis-panel {{
            max-height: 0;
            overflow: hidden;
            transition: max-height 0.3s ease-out;
        }}

        .analysis-panel.open {{
            max-height: 3000px;
            transition: max-height 0.5s ease-in;
        }}

        .analysis-content {{
            padding: 0 16px 16px;
            font-size: 13px;
            color: var(--text-analysis);
            border-top: 1px solid var(--border);
            padding-top: 14px;
            line-height: 1.65;
        }}

        .analysis-content strong {{
            color: var(--text-analysis-strong);
        }}

        .footer {{
            text-align: center;
            padding: 24px;
            font-size: 11px;
            color: var(--text-footer);
            font-family: 'JetBrains Mono', monospace;
        }}

        .theme-toggle {{
            position: fixed;
            bottom: 20px;
            left: 20px;
            width: 40px;
            height: 40px;
            border-radius: 50%;
            border: 1px solid var(--border);
            background: var(--bg-card);
            color: var(--text-muted);
            font-size: 18px;
            cursor: pointer;
            display: flex;
            align-items: center;
            justify-content: center;
            transition: background 0.2s, border-color 0.2s;
            z-index: 100;
        }}

        .theme-toggle:hover {{
            border-color: var(--border-hover);
        }}

        @media (max-width: 480px) {{
            .header-inner {{ flex-direction: column; gap: 4px; }}
            .game-header {{ flex-direction: column; align-items: flex-start; gap: 8px; }}
            .game-meta {{ width: 100%; justify-content: space-between; }}
            .team {{ font-size: 13px; }}
        }}
    </style>
</head>
<body>
    <div class="header">
        <div class="header-inner">
            <h1>Bet<span>Buddy</span></h1>
            <span class="date">{today_str}</span>
        </div>
    </div>

    <div class="subheader">
        <span class="game-count">{len(games)} games · {analyzed_count} analyzed</span>
        <div class="legend">
            <div class="legend-item">
                <span class="status-dot analyzed"></span>
                Analyzed
            </div>
            <div class="legend-item">
                <span class="status-dot pending"></span>
                Pending
            </div>
        </div>
    </div>

    <div class="container">
        {cards_html if cards_html else '<div style="text-align:center;padding:48px;color:var(--text-dim)">No games scheduled today.</div>'}
    </div>

    <div class="footer">
        Updated {datetime.now(ET).strftime("%-I:%M %p ET")} · betbuddy
    </div>

    <button class="theme-toggle" id="theme-toggle" onclick="toggleTheme()" aria-label="Toggle theme">🌙</button>

    <script>
        function toggleAnalysis(id) {{
            const panel = document.getElementById(id);
            const icon = document.getElementById('icon-' + id);
            if (panel) {{
                panel.classList.toggle('open');
            }}
            if (icon) {{
                icon.classList.toggle('open');
            }}
        }}

        function toggleTheme() {{
            const body = document.body;
            const btn = document.getElementById('theme-toggle');
            body.classList.toggle('light');
            const isLight = body.classList.contains('light');
            btn.textContent = isLight ? '☀️' : '🌙';
            localStorage.setItem('betbuddy-theme', isLight ? 'light' : 'dark');
        }}

        // Load saved preference
        (function() {{
            const saved = localStorage.getItem('betbuddy-theme');
            if (saved === 'light') {{
                document.body.classList.add('light');
                document.getElementById('theme-toggle').textContent = '☀️';
            }}
        }})();
    </script>
</body>
</html>"""


def publish():
    conn = sqlite3.connect(DB_PATH)
    games = get_todays_schedule(conn)
    html_content = generate_html(games, conn)
    conn.close()

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    with open(OUTPUT_FILE, "w") as f:
        f.write(html_content)

    print(f"✓ Generated {OUTPUT_FILE}")
    print(f"  Games: {len(games)}")

    if "--push" in sys.argv:
        push_to_gh_pages()


def push_to_gh_pages():
    """Push the generated site to the gh-pages branch."""
    if not os.path.exists(OUTPUT_FILE):
        print("✗ No index.html to push.")
        return

    try:
        # Read the generated HTML BEFORE switching branches
        # (the site/ dir won't exist on gh-pages)
        with open(OUTPUT_FILE, "r") as f:
            html_content = f.read()

        current_branch = subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"], text=True
        ).strip()

        # Stash any uncommitted changes (e.g., sports.db updates from slate)
        subprocess.run(["git", "stash", "--include-untracked"], check=True)

        result = subprocess.run(
            ["git", "rev-parse", "--verify", "gh-pages"],
            capture_output=True, text=True,
        )

        if result.returncode != 0:
            subprocess.run(["git", "checkout", "--orphan", "gh-pages"], check=True)
            subprocess.run(["git", "rm", "-rf", "."], check=True)
        else:
            subprocess.run(["git", "checkout", "gh-pages"], check=True)

        # Write the HTML we read earlier
        with open("index.html", "w") as f:
            f.write(html_content)

        subprocess.run(["git", "add", "index.html"], check=True)

        result = subprocess.run(["git", "diff", "--cached", "--quiet"], capture_output=True)
        if result.returncode != 0:
            timestamp = datetime.now(ET).strftime("%Y-%m-%d %I:%M %p ET")
            subprocess.run(
                ["git", "commit", "-m", f"Update slate — {timestamp}"],
                check=True,
            )
            subprocess.run(["git", "push", "origin", "gh-pages"], check=True)
            print("✓ Pushed to gh-pages")
        else:
            print("  No changes to push.")

        # Switch back and restore stashed changes
        subprocess.run(["git", "checkout", current_branch], check=True)
        subprocess.run(["git", "stash", "pop"], check=False)

    except subprocess.CalledProcessError as e:
        print(f"✗ Git error: {e}")
        try:
            subprocess.run(["git", "checkout", current_branch], check=False)
            subprocess.run(["git", "stash", "pop"], check=False)
        except Exception:
            pass


if __name__ == "__main__":
    publish()