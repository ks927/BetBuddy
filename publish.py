# publish.py
# Generates a static index.html from today's predictions and pushes
# to the gh-pages branch for GitHub Pages hosting.
#
# Reads from the predictions table (including analysis_text) and the
# odds table for current lines. Outputs a single self-contained HTML
# file with expandable game cards.
#
# Usage:
#   python3 publish.py              # generate only
#   python3 publish.py --push       # generate and push to gh-pages
#   make publish                    # generate and push
 
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
 
 
def get_todays_predictions(conn):
    """Get all predictions for today, grouped by game."""
    today = date.today().isoformat()
    rows = conn.execute(
        """
        SELECT id, game_date, away_team, home_team, market, pick, confidence,
               result, actual_score_away, actual_score_home, analysis_text,
               predicted_at
        FROM predictions
        WHERE game_date = ?
        ORDER BY predicted_at ASC
        """,
        (today,),
    ).fetchall()
    return rows
 
 
def get_record(conn):
    """Get overall W-L-P record."""
    rows = conn.execute(
        """
        SELECT result, COUNT(*) as cnt
        FROM predictions
        WHERE result IS NOT NULL AND market != 'pass'
        GROUP BY result
        """
    ).fetchall()
    record = {"WIN": 0, "LOSS": 0, "PUSH": 0}
    for result, cnt in rows:
        if result in record:
            record[result] = cnt
    return record
 
 
def get_recent_results(conn, limit=10):
    """Get most recent graded picks for streak display."""
    rows = conn.execute(
        """
        SELECT result FROM predictions
        WHERE result IS NOT NULL AND market != 'pass'
        ORDER BY graded_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [r[0] for r in rows]
 
 
def get_current_spread(conn, home_team, away_team):
    """Get the latest consensus spread for a game."""
    row = conn.execute(
        """
        SELECT point FROM odds
        WHERE home_team LIKE ? AND away_team LIKE ?
        AND market = 'spreads'
        ORDER BY fetched_at DESC
        LIMIT 1
        """,
        (f"%{home_team}%", f"%{away_team}%"),
    ).fetchone()
    return row[0] if row else None
 
 
def get_tip_time(conn, home_team, away_team):
    """Get the commence time for a game."""
    row = conn.execute(
        """
        SELECT commence_time FROM odds
        WHERE home_team LIKE ? AND away_team LIKE ?
        ORDER BY fetched_at DESC
        LIMIT 1
        """,
        (f"%{home_team}%", f"%{away_team}%"),
    ).fetchone()
    if row:
        try:
            utc = datetime.fromisoformat(row[0].replace("Z", "+00:00"))
            return utc.astimezone(ET).strftime("%-I:%M %p ET")
        except Exception:
            return ""
    return ""
 
 
def confidence_color(conf):
    """Return CSS color for confidence level."""
    return {
        "HIGH": "#ef4444",
        "MEDIUM": "#f59e0b",
        "LOW": "#6b7280",
    }.get(conf, "#6b7280")
 
 
def result_badge(result):
    """Return styled badge HTML for a result."""
    if not result:
        return ""
    colors = {
        "WIN": ("🟢", "#22c55e"),
        "LOSS": ("🔴", "#ef4444"),
        "PUSH": ("🟡", "#f59e0b"),
    }
    emoji, color = colors.get(result, ("", "#6b7280"))
    return f'<span style="color:{color};font-weight:700">{emoji} {result}</span>'
 
 
def sanitize_analysis(text):
    """Convert analysis text to safe HTML with basic formatting."""
    if not text:
        return "<p style='color:#9ca3af;font-style:italic'>Analysis not available for this pick.</p>"
 
    text = html.escape(text)
    # Bold **text**
    text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
    # Section headers (lines starting with numbered sections)
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
 
 
def group_picks_by_game(predictions):
    """Group prediction rows by game (away_team + home_team)."""
    games = {}
    for row in predictions:
        key = (row[2], row[3])  # (away_team, home_team)
        if key not in games:
            games[key] = {
                "away_team": row[2],
                "home_team": row[3],
                "game_date": row[1],
                "picks": [],
                "analysis_text": row[10],
            }
        games[key]["picks"].append({
            "market": row[4],
            "pick": row[5],
            "confidence": row[6],
            "result": row[7],
        })
    return games
 
 
def generate_html(predictions, record, recent_results, conn):
    """Generate the full HTML page."""
    today_str = date.today().strftime("%A, %B %-d, %Y")
    wins = record["WIN"]
    losses = record["LOSS"]
    pushes = record["PUSH"]
    total = wins + losses + pushes
    win_pct = f"{(wins / total * 100):.1f}" if total > 0 else "0.0"
 
    # Streak display
    streak_html = ""
    for r in recent_results:
        if r == "WIN":
            streak_html += '<span class="streak-dot win">W</span>'
        elif r == "LOSS":
            streak_html += '<span class="streak-dot loss">L</span>'
        else:
            streak_html += '<span class="streak-dot push">P</span>'
 
    games = group_picks_by_game(predictions)
 
    # Build game cards
    cards_html = ""
    if not games:
        cards_html = """
        <div class="no-picks">
            <p>No picks posted yet today.</p>
            <p style="font-size:14px;color:#9ca3af">Check back later — picks are usually posted by game time.</p>
        </div>
        """
    else:
        for i, (key, game) in enumerate(games.items()):
            away = game["away_team"]
            home = game["home_team"]
            tip = get_tip_time(conn, home, away)
 
            picks_html = ""
            for p in game["picks"]:
                if p["market"] == "pass":
                    picks_html += f"""
                    <div class="pick-chip pass">
                        <span class="pick-label">PASS</span>
                        <span class="pick-conf">No Edge</span>
                    </div>"""
                else:
                    conf_col = confidence_color(p["confidence"])
                    badge = result_badge(p["result"]) if p["result"] else ""
                    picks_html += f"""
                    <div class="pick-chip">
                        <span class="pick-label">{html.escape(p['pick'])}</span>
                        <span class="pick-conf" style="color:{conf_col}">{p['confidence']}</span>
                        {badge}
                    </div>"""
 
            analysis_html = sanitize_analysis(game.get("analysis_text"))
 
            cards_html += f"""
            <div class="game-card" onclick="toggleAnalysis('game-{i}')">
                <div class="game-header">
                    <div class="matchup">
                        <span class="team away">{html.escape(away)}</span>
                        <span class="at">@</span>
                        <span class="team home">{html.escape(home)}</span>
                    </div>
                    <div class="game-meta">
                        <span class="tip-time">{tip}</span>
                        <span class="expand-icon" id="icon-game-{i}">▸</span>
                    </div>
                </div>
                <div class="picks-row">{picks_html}</div>
                <div class="analysis-panel" id="game-{i}">
                    <div class="analysis-content">{analysis_html}</div>
                </div>
            </div>"""
 
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>BetBuddy — NCAAB Picks</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;700&family=Space+Grotesk:wght@400;600;700&display=swap" rel="stylesheet">
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
 
        body {{
            font-family: 'Space Grotesk', sans-serif;
            background: #0a0a0a;
            color: #e2e8f0;
            min-height: 100vh;
            padding: 0;
        }}
 
        .header {{
            background: linear-gradient(135deg, #0f172a 0%, #1e293b 100%);
            border-bottom: 1px solid #1e293b;
            padding: 32px 24px;
            text-align: center;
        }}
 
        .header h1 {{
            font-family: 'JetBrains Mono', monospace;
            font-size: 28px;
            font-weight: 700;
            letter-spacing: -0.5px;
            color: #f8fafc;
            margin-bottom: 4px;
        }}
 
        .header .subtitle {{
            font-size: 14px;
            color: #64748b;
            letter-spacing: 2px;
            text-transform: uppercase;
        }}
 
        .record-bar {{
            display: flex;
            justify-content: center;
            align-items: center;
            gap: 32px;
            padding: 20px 24px;
            background: #0f172a;
            border-bottom: 1px solid #1e293b;
            flex-wrap: wrap;
        }}
 
        .record-stat {{
            text-align: center;
        }}
 
        .record-stat .value {{
            font-family: 'JetBrains Mono', monospace;
            font-size: 24px;
            font-weight: 700;
            color: #f8fafc;
        }}
 
        .record-stat .label {{
            font-size: 11px;
            color: #64748b;
            text-transform: uppercase;
            letter-spacing: 1.5px;
            margin-top: 2px;
        }}
 
        .streak-row {{
            display: flex;
            justify-content: center;
            gap: 6px;
            padding: 12px 24px;
            background: #0a0a0a;
        }}
 
        .streak-dot {{
            font-family: 'JetBrains Mono', monospace;
            font-size: 11px;
            font-weight: 700;
            width: 28px;
            height: 28px;
            border-radius: 6px;
            display: flex;
            align-items: center;
            justify-content: center;
        }}
 
        .streak-dot.win {{ background: #052e16; color: #22c55e; }}
        .streak-dot.loss {{ background: #2a0a0a; color: #ef4444; }}
        .streak-dot.push {{ background: #1a1a0a; color: #f59e0b; }}
 
        .date-header {{
            padding: 20px 24px 8px;
            font-size: 13px;
            color: #64748b;
            text-transform: uppercase;
            letter-spacing: 2px;
            max-width: 720px;
            margin: 0 auto;
        }}
 
        .container {{
            max-width: 720px;
            margin: 0 auto;
            padding: 0 16px 48px;
        }}
 
        .game-card {{
            background: #111827;
            border: 1px solid #1e293b;
            border-radius: 12px;
            margin-bottom: 12px;
            cursor: pointer;
            transition: border-color 0.2s;
            overflow: hidden;
        }}
 
        .game-card:hover {{
            border-color: #334155;
        }}
 
        .game-header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 16px 20px;
        }}
 
        .matchup {{
            display: flex;
            align-items: center;
            gap: 8px;
            flex-wrap: wrap;
        }}
 
        .team {{
            font-weight: 600;
            font-size: 15px;
        }}
 
        .at {{
            color: #475569;
            font-size: 13px;
        }}
 
        .game-meta {{
            display: flex;
            align-items: center;
            gap: 12px;
        }}
 
        .tip-time {{
            font-family: 'JetBrains Mono', monospace;
            font-size: 12px;
            color: #64748b;
        }}
 
        .expand-icon {{
            color: #475569;
            font-size: 14px;
            transition: transform 0.2s;
        }}
 
        .expand-icon.open {{
            transform: rotate(90deg);
        }}
 
        .picks-row {{
            display: flex;
            gap: 8px;
            padding: 0 20px 16px;
            flex-wrap: wrap;
        }}
 
        .pick-chip {{
            display: flex;
            align-items: center;
            gap: 8px;
            background: #1e293b;
            border-radius: 8px;
            padding: 8px 14px;
        }}
 
        .pick-chip.pass {{
            opacity: 0.5;
        }}
 
        .pick-label {{
            font-family: 'JetBrains Mono', monospace;
            font-size: 13px;
            font-weight: 700;
            color: #f8fafc;
        }}
 
        .pick-conf {{
            font-size: 11px;
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
            max-height: 2000px;
            transition: max-height 0.5s ease-in;
        }}
 
        .analysis-content {{
            padding: 0 20px 20px;
            font-size: 13px;
            color: #94a3b8;
            border-top: 1px solid #1e293b;
            padding-top: 16px;
        }}
 
        .no-picks {{
            text-align: center;
            padding: 48px 24px;
            color: #64748b;
        }}
 
        .no-picks p:first-child {{
            font-size: 18px;
            color: #94a3b8;
            margin-bottom: 8px;
        }}
 
        .footer {{
            text-align: center;
            padding: 24px;
            font-size: 12px;
            color: #334155;
            font-family: 'JetBrains Mono', monospace;
        }}
 
        @media (max-width: 480px) {{
            .header h1 {{ font-size: 22px; }}
            .record-bar {{ gap: 20px; }}
            .record-stat .value {{ font-size: 20px; }}
            .game-header {{ padding: 12px 16px; }}
            .picks-row {{ padding: 0 16px 12px; }}
            .team {{ font-size: 14px; }}
        }}
    </style>
</head>
<body>
    <div class="header">
        <h1>BetBuddy</h1>
        <div class="subtitle">NCAAB Betting Analysis</div>
    </div>
 
    <div class="record-bar">
        <div class="record-stat">
            <div class="value">{wins}-{losses}{f'-{pushes}' if pushes else ''}</div>
            <div class="label">Record</div>
        </div>
        <div class="record-stat">
            <div class="value">{win_pct}%</div>
            <div class="label">Win Rate</div>
        </div>
        <div class="record-stat">
            <div class="value">{total}</div>
            <div class="label">Total Picks</div>
        </div>
    </div>
 
    {f'<div class="streak-row">{streak_html}</div>' if streak_html else ''}
 
    <div class="date-header">{today_str}</div>
 
    <div class="container">
        {cards_html}
    </div>
 
    <div class="footer">
        Updated {datetime.now(ET).strftime("%-I:%M %p ET")} · Powered by data, not vibes
    </div>
 
    <script>
        function toggleAnalysis(id) {{
            const panel = document.getElementById(id);
            const icon = document.getElementById('icon-' + id);
            panel.classList.toggle('open');
            icon.classList.toggle('open');
        }}
    </script>
</body>
</html>"""
 
 
def publish():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = None  # Use tuple rows
 
    predictions = get_todays_predictions(conn)
    record = get_record(conn)
    recent = get_recent_results(conn)
 
    html_content = generate_html(predictions, record, recent, conn)
    conn.close()
 
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    with open(OUTPUT_FILE, "w") as f:
        f.write(html_content)
 
    print(f"✓ Generated {OUTPUT_FILE}")
    print(f"  Record: {record['WIN']}-{record['LOSS']}-{record['PUSH']}")
    print(f"  Today's picks: {len(predictions)}")
 
    if "--push" in sys.argv:
        push_to_gh_pages()
 
 
def push_to_gh_pages():
    """Push the generated site to the gh-pages branch."""
    site_dir = OUTPUT_DIR
    index_file = OUTPUT_FILE
 
    if not os.path.exists(index_file):
        print("✗ No index.html to push. Run publish first.")
        return
 
    try:
        # Save current branch
        current_branch = subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            text=True,
        ).strip()
 
        # Check if gh-pages exists
        result = subprocess.run(
            ["git", "rev-parse", "--verify", "gh-pages"],
            capture_output=True,
            text=True,
        )
 
        if result.returncode != 0:
            # Create orphan gh-pages branch
            subprocess.run(["git", "checkout", "--orphan", "gh-pages"], check=True)
            subprocess.run(["git", "rm", "-rf", "."], check=True)
        else:
            subprocess.run(["git", "checkout", "gh-pages"], check=True)
 
        # Copy the generated file to root
        import shutil
        shutil.copy(os.path.join(site_dir, "index.html"), "index.html")
 
        # Commit and push
        subprocess.run(["git", "add", "index.html"], check=True)
 
        # Check if there are changes to commit
        result = subprocess.run(
            ["git", "diff", "--cached", "--quiet"],
            capture_output=True,
        )
        if result.returncode != 0:
            timestamp = datetime.now(ET).strftime("%Y-%m-%d %I:%M %p ET")
            subprocess.run(
                ["git", "commit", "-m", f"Update picks — {timestamp}"],
                check=True,
            )
            subprocess.run(["git", "push", "origin", "gh-pages"], check=True)
            print("✓ Pushed to gh-pages")
        else:
            print("  No changes to push.")
 
        # Switch back to original branch
        subprocess.run(["git", "checkout", current_branch], check=True)
 
    except subprocess.CalledProcessError as e:
        print(f"✗ Git error: {e}")
        # Try to get back to the original branch
        try:
            subprocess.run(["git", "checkout", current_branch], check=False)
        except Exception:
            pass
 
 
if __name__ == "__main__":
    publish()