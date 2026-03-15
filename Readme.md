# BetBuddy

An NCAAB betting analysis system that pulls live odds, team stats, ATS records, and injury reports, runs every game through an LLM to produce structured analysis, and publishes a daily picks page.

**Live slate:** [ks927.github.io/BetBuddy](https://ks927.github.io/BetBuddy)

## How It Works

1. **Odds** are fetched from The Odds API (DraftKings, FanDuel, BetMGM) and stored in SQLite. Every fetch is appended, building a time series of line movement.
2. **Team stats**, game logs, and **ATS records** are pulled from ESPN for every team with an upcoming game.
3. **Pre-computed analytics** — implied probabilities, cross-book comparisons, scoring matchups, rest days, totals gap analysis, ATS cover rates — are assembled into a context block so the LLM doesn't do any math.
4. **Gemini 2.5 Flash** analyzes the data using a structured 6-section prompt, evaluating both the spread and the over/under.
5. **Picks are logged** to SQLite with the full analysis text, then graded against final scores.
6. **A static page** is generated and published to GitHub Pages showing today's full slate with expandable analysis for every game.

## Quick Start

```bash
make fetch        # pull latest odds, stats, ATS records, and injuries
make today        # see today's games
make query Q="Duke vs Syracuse"   # analyze a single game
make slate        # batch-analyze all of today's un-analyzed games
make publish      # run slate + generate site + push to GitHub Pages
make score        # grade picks against final scores
make record       # see your tracked record
```

## Prerequisites

- Python 3.10+
- A [Gemini API key](https://aistudio.google.com/app/apikey) (free tier — 250 requests/day)
- An [Odds API key](https://the-odds-api.com) (free tier — 500 requests/month)

### Python Dependencies

```bash
pip3 install google-genai requests python-dotenv
```

## Setup

### 1. Clone and enter the project

```bash
git clone https://github.com/ks927/BetBuddy.git
cd BetBuddy
```

### 2. Create your `.env` file

```
ODDS_API_KEY=your_odds_api_key
GEMINI_API_KEY=your_gemini_api_key
```

### 3. Fetch initial data

```bash
make fetch
```

### 4. Set up prediction tracking

```bash
python3 migrate_add_analysis.py
```

This adds the `analysis_text` column to the predictions table. Only needs to be run once.

### 5. Enable GitHub Pages (optional)

Go to your repo → Settings → Pages → Source: **gh-pages** branch → Save.

## Usage

### See today's games

```bash
make today
```

### Analyze a single game

```bash
make query Q="Houston vs BYU"
```

Streams a structured analysis covering the spread, over/under, line movement, cross-book comparisons, ATS records, injuries, and a final recommendation with confidence rating. After the analysis, you're prompted to log the pick.

### Batch-analyze today's full slate

```bash
make slate
```

Runs every game on today's schedule through the LLM. Skips games you've already analyzed via `make query`. Logs all picks automatically.

### Publish the daily page

```bash
make publish
```

Runs `slate` (fills in un-analyzed games), generates a static HTML page, and pushes it to the `gh-pages` branch. The page shows today's full schedule — analyzed games have expandable analysis cards, pending games appear dimmed.

### Grade your picks

```bash
make score
```

### View your record

```bash
make record
```

## Daily Workflow

```bash
# Morning: refresh data (cron runs this at 8am, 2pm, 6pm)
make fetch

# Before games: publish the slate
make publish

# After games: grade picks
make score
```

## Architecture

```
BetBuddy/
├── query.py                # Interactive single-game analysis (Gemini 2.5 Flash)
├── analysis.py             # Extracted analysis runner (used by query.py and slate.py)
├── slate.py                # Batch-analyzes today's un-analyzed games
├── publish.py              # Generates static HTML + pushes to gh-pages
├── retrieval.py            # Builds data context from DB (favorite/underdog oriented)
├── prompt.py               # System prompt and 6-section analysis template
├── list_games.py           # Lists upcoming games from the database
├── prediction_logger.py    # Parses LLM picks, saves to DB with analysis text
├── score_predictions.py    # Grades predictions against final scores
├── record.py               # Displays tracked record with ROI breakdowns
├── migrate_add_analysis.py # One-time DB migration
├── Makefile                # All commands
├── .env                    # API keys (not committed)
├── site/                   # Generated HTML (not committed)
├── data/
│   ├── fetch_odds.py       # Odds from The Odds API
│   ├── fetch_stats.py      # Stats and game logs from ESPN
│   ├── fetch_injuries.py   # Injury reports from ESPN
│   ├── fetch_scores.py     # Final scores from The Odds API
│   ├── fetch_ats.py        # ATS records from ESPN
│   └── ncaab_team_ids.py   # Odds API team names → ESPN IDs
└── db/
    └── sports.db           # SQLite database (auto-created)
```

## What the Analysis Covers

Each game gets a 6-section analysis:

1. **What the lines are saying** — spread, total, and moneyline from each book with implied probabilities. Pre-rendered from the database so numbers are never hallucinated.
2. **Line movement** — how spreads and totals have shifted across fetches at DraftKings and FanDuel.
3. **Spread analysis** — ATS records, season margins, recent form, home/away splits, scoring matchups, rest advantage. Oriented around favorite vs underdog, not home vs away.
4. **Totals analysis** — expected total vs posted line, pace indicators, offensive/defensive mismatches, O/U records.
5. **Situational factors** — injuries, rest, home court, schedule patterns.
6. **Conclusion** — verdict on both markets with confidence rating and a concrete recommendation.

## Key Design Decisions

- **All math is pre-computed** in `retrieval.py` so the LLM explains rather than calculates.
- **Section 1 is pre-rendered** from the database and printed before the LLM streams — the model never restates line numbers, eliminating hallucinated spreads/totals.
- **Favorite/underdog orientation** — context is built around which team is favored, not API home/away designations. This fixes neutral-site tournament games where "home" is arbitrary.
- **Neutral site detection** for March/April tournament games.
- **ATS records** from ESPN's undocumented odds-records API are integrated into KEY FACTS, context blocks, and the prompt.
- **Date-aware score matching** prevents grading against the wrong game when teams play each other multiple times.
- **Cross-alignment matching** in the scorer handles home/away swaps between the odds and scores API endpoints.

## Prediction Tracking

Picks are stored in SQLite with the game details, confidence level, odds snapshot, and full analysis text. After games complete, `make score` grades each prediction:

- **Spread:** picked team's margin + spread. Positive = WIN.
- **Total:** combined score vs line in the picked direction.

The analysis text is preserved for retrospective review — useful for identifying which signals the model over/under-weights.

## Confidence Scale

| Level | Meaning |
|---|---|
| **HIGH** | Strong edge — multiple independent signals align |
| **MEDIUM** | Real edge — worth a standard unit |
| **LOW** | Slight lean — would need a better number |

An edge requires at least two independent signals pointing the same direction. ATS cover rates below 45% (as favorite) or above 55% (as underdog) count as strong signals.

## Bookmakers Tracked

| Book | Role |
|---|---|
| **DraftKings** | Primary — line movement tracked across fetches |
| **FanDuel** | Secondary — movement tracking + cross-book comparison |
| **BetMGM** | Third reference for spotting outlier lines |

When one book disagrees with the other two by 0.5+ points, it's flagged as a potential edge.

## Troubleshooting

| Problem | Fix |
|---|---|
| "No upcoming games found" | Run `make fetch`. If between seasons, there may be no games. |
| "Could not find team matching..." | Check exact names with `make today`. |
| "Ambiguous team name" | Be more specific (e.g., "arizona wildcats" not "arizona"). |
| "Could not parse pick" | Use `make log` to enter manually. |
| Unmatched predictions after `make score` | Team name mismatch — check `score_predictions.py` normalization. |
| Missing ESPN ID | Add to `data/ncaab_team_ids.py`. Find IDs at `espn.com/mens-college-basketball/team/_/id/XXXX`. |
| `GEMINI_API_KEY` not found | Add it to `.env`. Get a free key at [aistudio.google.com](https://aistudio.google.com/app/apikey). |
