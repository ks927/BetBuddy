BetBuddy
A local-first NCAAB (college basketball) betting analysis system. It pulls live odds, team stats, and injury reports, stores everything in a local database, and uses an LLM running on your machine to produce structured betting analysis — identifying when the spread or total is mispriced.
BetBuddy evaluates both the spread and the over/under for every game and recommends whichever market has the stronger edge. If neither market has an edge, it says so and explains why.
Every pick can be logged and tracked against actual results, giving you a running record with ROI and confidence breakdowns.
How It Works

Odds data is fetched from The Odds API and stored locally in SQLite. Every fetch is appended (never overwritten), which gives you a time series of line movement.
Team stats and game logs are pulled from ESPN's public API for every team with an upcoming game.
Injury reports are pulled from ESPN and surfaced in the analysis so the model can factor in missing players.
Pre-computed analytics — implied probabilities, cross-book line comparisons, scoring matchup analysis, days of rest, totals gap analysis — are assembled into a context block so the LLM doesn't have to do any math.
A local LLM (via Ollama) analyzes the data using a structured prompt that forces it to argue both sides before reaching a conclusion.
Prediction logging — after each analysis, you're prompted to log the pick. Logged predictions are graded against final scores and tracked over time.

No data leaves your machine. No API costs for the LLM.
Quick Start

make fetch — pull the latest odds, stats, and injuries
make today — see today's games
make query Q="Duke vs Syracuse" — get a betting recommendation
make score — grade your picks against final results
make record — see your tracked record

That's it. Repeat step 1 before each session (or multiple times on game day for better line movement data).
Prerequisites

Python 3.10+
Ollama — install from ollama.com
An Odds API key — free tier at the-odds-api.com (500 requests/month)

Python Dependencies
bashpip install requests python-dotenv ollama
Ollama Setup
bash# Install and start Ollama
ollama serve

# Pull a model (8b recommended — good balance of speed and reasoning quality)
ollama pull llama3.1:8b

Note on model choice: The system asks the LLM to do structured multi-section reasoning. Smaller models (3B parameters) will produce shallow analysis. llama3.1:8b is the minimum recommended size. If your hardware supports it, llama3.1:70b will produce significantly better output.


Setup
1. Clone and enter the project
bashgit clone https://github.com/ks927/BetBuddy.git
cd BetBuddy
2. Create your .env file
Create a file called .env in the project root:
ODDS_API_KEY=your_api_key_here
3. Set your model
Open query.py and set the MODEL variable to match whichever Ollama model you pulled:
pythonMODEL = "llama3.1:8b"
4. Fetch initial data
bashmake fetch
This runs all three data fetchers in sequence: odds (from The Odds API), stats (from ESPN), and injuries (from ESPN).
5. Set up prediction tracking
bashpython3 -m db.migrate_predictions
This creates the predictions table in your database. Only needs to be run once.

Usage
See today's games
bashmake today
Output is grouped by date with tip times in Eastern:
Saturday March 8
──────────────────────────────────────────────────
  Temple Owls                    @  Tulsa Golden Hurricane          02:00 PM ET
  Duke Blue Devils               @  Syracuse Orange                 04:00 PM ET
See all upcoming games
bashmake games
Filter by team
bashpython3 list_games.py duke
Analyze a matchup
bashmake query Q="Temple vs Tulsa"
Or run the script directly:
bashpython3 query.py "Duke vs Syracuse"
python3 query.py "UConn @ Marquette"
BetBuddy will stream a structured analysis covering the spread, the over/under, line movement, cross-book comparisons, injuries, and a final recommendation with a confidence rating.
After the analysis, you'll be prompted:
Log this pick? (y/n): y
✓ Logged: Duke -3.5 (HIGH) — spread
If the parser can't extract the pick from the model's output, you can log it manually:
bashmake log
Grade your picks
After games finish, fetch final scores and grade all ungraded predictions:
bashmake score
  ✓ WIN   Duke -3.5 (HIGH)  — Syracuse 68 @ Duke 75
  ✗ LOSS  OVER 145.5 (MEDIUM)  — Kansas 62 @ Baylor 70
View your record
bashmake record
════════════════════════════════════════════════════════
  📊 BetBuddy Prediction Record
════════════════════════════════════════════════════════

  Overall:  12W-8L-1P  (60.0%)
  ROI:     +4.5%  ($90 on $2,000 risked @ -110 flat)
  Pending: 3 ungraded picks

  By Confidence:
    HIGH    5W-2L-0P  (71.4%)  +20.1% ROI
    MEDIUM  5W-4L-1P  (55.6%)  +0.1% ROI
    LOW     2W-2L-0P  (50.0%)  -4.5% ROI

  By Market:
    Spread    8W-5L-1P  (61.5%)  +7.0% ROI
    Total     4W-3L-0P  (57.1%)  +1.3% ROI

  Last 10 Picks:
    ✓ WIN   2025-03-08  Syracuse @ Duke         → Duke -3.5       HIGH    68-75
    ✗ LOSS  2025-03-08  Kansas @ Baylor         → OVER 145.5      MEDIUM  62-70
    ...
For a full history of every pick:
bashmake record-detail

Daily Workflow
A typical session looks like this:
bash# 1. Refresh data (do this before each session, or multiple times on game day)
make fetch

# 2. See what's on the board
make today

# 3. Analyze the games you're interested in
make query Q="Duke vs Syracuse"
make query Q="Kansas vs Baylor"

# 4. After games finish, grade your picks
make score

# 5. Check your record
make record
Tip: Lines move most in the hours before tip-off. Fetching odds 2-3 times on game day gives you richer movement data, which makes the line movement analysis more useful.

Makefile Commands
CommandWhat it doesmake fetchFetch fresh odds, stats, and injuries (run all three)make oddsFetch odds onlymake statsFetch stats only (requires odds to exist first)make injuriesFetch injuries only (requires odds to exist first)make todayList today's gamesmake gamesList all upcoming gamesmake query Q="Team vs Team"Run a full analysis on a matchupmake scoreFetch final scores and grade ungraded predictionsmake recordShow your prediction record and ROImake record-detailShow full prediction historymake logManually log a pick (if auto-parsing fails)make resetWipe the database and start fresh

Project Structure
BetBuddy/
├── query.py                # Entry point — parses input, streams LLM output, prompts to log pick
├── retrieval.py            # Builds the data context block from the database
├── prompt.py               # System prompt and structured analysis template
├── list_games.py           # Lists upcoming games from the database
├── prediction_logger.py    # Parses LLM output for pick details, saves to DB
├── score_predictions.py    # Grades logged predictions against actual results
├── record.py               # Displays tracked record with ROI and breakdowns
├── Makefile                # Convenience commands
├── .env                    # Your Odds API key (not committed)
├── data/
│   ├── fetch_odds.py       # Pulls odds from The Odds API into SQLite
│   ├── fetch_stats.py      # Pulls team stats and game logs from ESPN
│   ├── fetch_injuries.py   # Pulls injury reports from ESPN
│   ├── fetch_scores.py     # Pulls completed game scores from The Odds API
│   └── ncaab_team_ids.py   # Maps Odds API team names → ESPN team IDs
└── db/
    ├── sports.db           # SQLite database (auto-created on first fetch)
    └── migrate_predictions.py  # Creates the predictions table (run once)

What the Analysis Covers
Each query produces an 8-section analysis:

What the lines are saying — spread, total, and moneyline from each book with pre-calculated implied probabilities
Line movement — how the spread and total have moved across fetches at DraftKings and FanDuel
Spread: case for the favorite — strongest argument for the favorite covering, backed by specific stats
Spread: case against the favorite — equally rigorous argument for the underdog, identifying where the favorite is vulnerable
Totals: case for the over — offensive matchups, expected total vs. posted line, pace indicators
Totals: case for the under — defensive matchups, scoring suppression signals
Situational factors — injuries, home court, days of rest, recent schedule, game log patterns
Conclusion — evaluates both markets, recommends whichever has the stronger edge (or PASS if neither does)


Prediction Tracking
Every time you run a query, BetBuddy prompts you to log the pick. Logged predictions are stored in SQLite alongside the game details, the pick, the confidence level, and a snapshot of the odds at the time.
After games complete, make score pulls final scores from The Odds API and grades each prediction as WIN, LOSS, or PUSH. The grading logic:

Spread picks: The picked team's actual margin plus the spread. Positive = WIN, negative = LOSS, zero = PUSH.
Total picks: The combined final score compared to the line in the picked direction (OVER/UNDER).

make record shows your running record broken down by confidence level and market type, with flat-bet ROI calculated at -110 standard juice.
If the auto-parser can't extract the pick from the model's output (it uses regex against the structured conclusion format), you'll see a message and can log manually with make log.

Confidence Scale
LevelMeaningLOWSlight lean — would need a better numberMEDIUMReal edge — worth a standard unitHIGHStrong edge — multiple signals align

Bookmakers Tracked
BookWhyDraftKingsHigh-volume US book — primary reference for line movementFanDuelSecond-largest US book — secondary movement trackingBetMGMThird comparison point for cross-book analysis
When one book's spread disagrees with the other two by half a point or more, BetBuddy flags it — that book may be slow to adjust or seeing different action, and the consensus of the other two is more likely to reflect the true market price.

Injury Integration
BetBuddy pulls injury data from ESPN's API for every team with an upcoming game. In the analysis, players are flagged by status:

OUT — confirmed out, flagged prominently. A missing starter is worth roughly 3-5 points on the spread.
DOUBTFUL / QUESTIONABLE — may or may not play. The model factors in the uncertainty.
Day-to-Day — likely to play but worth monitoring.

NCAAB injury reporting is less consistent than professional leagues. If no injuries are reported for a team, BetBuddy notes that and moves on without fabricating anything.

Adding New Teams
If make stats reports teams with no ESPN ID mapping, you need to add them to data/ncaab_team_ids.py.

Search ESPN for the team (e.g., espn.com/mens-college-basketball/team/_/id/XXXX/team-name)
The number in the URL is the ESPN team ID
Add it to the TEAM_ID_MAP dict in ncaab_team_ids.py:

python"Team Name As It Appears In Odds API": ESPN_ID,

Run make stats again to pull their data.


Troubleshooting

"No upcoming games found" — Run make odds to refresh. If the season is over or between games, there may genuinely be no upcoming games.
"Could not find team matching..." — The team name you typed doesn't partially match anything in the odds database. Run make today to see exact team names.
"Ambiguous team name" — Your search matched multiple teams (e.g., "Carolina" matches both North Carolina and South Carolina). Be more specific.
"Could not parse pick from analysis" — The regex parser couldn't extract the pick from the model's output. Use make log to enter it manually.
Unmatched predictions after make score — Team names in your logged picks may not match The Odds API's names closely enough. The fuzzy matcher handles most cases, but unusual names may need manual grading or an update to score_predictions.py's normalize_team().
Ollama connection errors — Make sure Ollama is running (ollama serve) and that you've pulled the model (ollama pull llama3.1:8b).
API credit warnings — The free tier of The Odds API gives 500 requests/month. The scores endpoint also counts against this. At 2 odds fetches per day plus 1 daily score fetch, that's roughly 90/month, leaving plenty of headroom. Check the credit count printed after each fetch.