# BetBuddy

A local-first NCAAB (college basketball) betting analysis system. It pulls live odds and team stats, stores them in a local database, and uses an LLM running on your machine to produce structured betting analysis — identifying when the spread or total is mispriced.

The system evaluates **both the spread and the over/under** for every game and recommends whichever market has the stronger edge. If neither market has an edge, it says so and explains why.

## How It Works

1. **Odds data** is fetched from [The Odds API](https://the-odds-api.com/) and stored locally in SQLite. Every fetch is appended (never overwritten), which gives you a time series of line movement.
2. **Team stats and game logs** are pulled from ESPN's public API for every team with an upcoming game.
3. **Pre-computed analytics** — implied probabilities, sharp vs. recreational line comparisons, scoring matchup analysis, days of rest, and totals gap analysis — are assembled into a context block so the LLM doesn't have to do any math.
4. **A local LLM** (via [Ollama](https://ollama.com/)) analyzes the data using a structured prompt that forces it to argue both sides before reaching a conclusion.

No data leaves your machine. No API costs for the LLM.

---

## Prerequisites

- **Python 3.10+**
- **Ollama** — install from [ollama.com](https://ollama.com/)
- **An Odds API key** — free tier at [the-odds-api.com](https://the-odds-api.com/) (500 requests/month)

### Python Dependencies

```bash
pip install requests python-dotenv ollama
```

### Ollama Setup

```bash
# Install and start Ollama
ollama serve

# Pull a model (8b recommended — good balance of speed and reasoning quality)
ollama pull llama3.1:8b
```

> **Note on model choice:** The system asks the LLM to do structured multi-section reasoning. Smaller models (3B parameters) will produce shallow analysis. `llama3.1:8b` is the minimum recommended size. If your hardware supports it, `llama3.1:70b` will produce significantly better output.

---

## Setup

### 1. Clone and enter the project

```bash
cd BetBuddy
```

### 2. Create your `.env` file

Create a file called `.env` in the project root:

```
ODDS_API_KEY=your_api_key_here
```

### 3. Set your model

Open `query.py` and set the `MODEL` variable to match whichever Ollama model you pulled:

```python
MODEL = "llama3.1:8b"
```

### 4. Fetch initial data

```bash
make fetch
```

This runs `fetch_odds.py` (pulls lines from 5 bookmakers) followed by `fetch_stats.py` (pulls season stats and game logs from ESPN for every team with an upcoming game).

---

## Quick Start

1. `make fetch` — pull the latest odds and stats
2. `make today` — see today's games
3. `make query Q="Duke vs Syracuse"` — get a betting recommendation

That's it. Repeat step 1 before each session (or multiple times on game day for better line movement data).

---

## Usage

### See today's games

```bash
make today
```

Output is grouped by date with tip times in Eastern:

```
Saturday March 8
──────────────────────────────────────────────────
  Temple Owls                    @  Tulsa Golden Hurricane          02:00 PM ET
  Duke Blue Devils               @  Syracuse Orange                 04:00 PM ET
```

### See all upcoming games

```bash
make games
```

### Filter by team

```bash
python3 list_games.py duke
```

### Analyze a matchup

```bash
make query Q="Temple vs Tulsa"
```

Or run the script directly:

```bash
python3 query.py "Duke vs Syracuse"
python3 query.py "UConn @ Marquette"
```

The system will stream a structured analysis covering the spread, the over/under, line movement, sharp vs. public money, and a final recommendation with a confidence rating.

---

## Daily Workflow

A typical session looks like this:

```bash
# 1. Refresh data (do this before each session, or multiple times on game day)
make fetch

# 2. See what's on the board
make today

# 3. Analyze the games you're interested in
make query Q="Duke vs Syracuse"
make query Q="Kansas vs Baylor"
```

**Tip:** Lines move most in the hours before tip-off. Fetching odds 2-3 times on game day gives you richer movement data, which makes the line movement analysis more useful.

---

## Makefile Commands

| Command | What it does |
|---------|-------------|
| `make fetch` | Fetch fresh odds and stats (run both) |
| `make odds` | Fetch odds only |
| `make stats` | Fetch stats only (requires odds to exist first) |
| `make today` | List today's games |
| `make games` | List all upcoming games |
| `make query Q="Team vs Team"` | Run a full analysis on a matchup |
| `make reset` | Wipe the database and start fresh |

---

## Project Structure

```
BetBuddy/
├── query.py              # Entry point — parses input, calls retrieval + prompt, streams LLM output
├── retrieval.py          # Builds the data context block from the database
├── prompt.py             # System prompt and structured analysis template
├── list_games.py         # Lists upcoming games from the database
├── Makefile              # Convenience commands
├── .env                  # Your Odds API key (not committed)
├── data/
│   ├── fetch_odds.py     # Pulls odds from The Odds API into SQLite
│   ├── fetch_stats.py    # Pulls team stats and game logs from ESPN
│   ├── fetch_injuries.py # (placeholder — not yet implemented)
│   └── ncaab_team_ids.py # Maps Odds API team names → ESPN team IDs
└── db/
    └── sports.db         # SQLite database (auto-created on first fetch)
```

---

## What the Analysis Covers

Each query produces an 8-section analysis:

1. **What the lines are saying** — spread, total, and moneyline from each book with pre-calculated implied probabilities
2. **Line movement** — how the spread and total have moved across fetches at both sharp (Pinnacle) and recreational (DraftKings) books
3. **Spread: case for the favorite** — strongest argument for the favorite covering, backed by specific stats
4. **Spread: case against the favorite** — equally rigorous argument for the underdog, identifying where the favorite is vulnerable
5. **Totals: case for the over** — offensive matchups, expected total vs. posted line, pace indicators
6. **Totals: case for the under** — defensive matchups, scoring suppression signals
7. **Situational factors** — home court, days of rest, recent schedule, game log patterns
8. **Conclusion** — evaluates both markets, recommends whichever has the stronger edge (or PASS if neither does)

### Confidence Scale

| Level | Meaning |
|-------|---------|
| LOW | Slight lean — would need a better number |
| MEDIUM | Real edge — worth a standard unit |
| HIGH | Strong edge — multiple signals align |

---

## Bookmakers Tracked

| Book | Type | Why |
|------|------|-----|
| Pinnacle | Sharp | The global reference line — reflects where professional bettors have moved the market |
| DraftKings | Recreational | High-volume US book — reflects public betting patterns |
| FanDuel | Recreational | Second-largest US book |
| BetMGM | Recreational | Rounds out the US market picture |
| William Hill | Recreational | Additional reference point |

When Pinnacle's line disagrees with the recreational books by half a point or more, the system flags it — that's a signal that sharp and public money are on opposite sides.

---

## Adding New Teams

If `make stats` reports teams with no ESPN ID mapping, you need to add them to `data/ncaab_team_ids.py`.

1. Search ESPN for the team (e.g., `espn.com/mens-college-basketball/team/_/id/XXXX/team-name`)
2. The number in the URL is the ESPN team ID
3. Add it to the `TEAM_ID_MAP` dict in `ncaab_team_ids.py`:

```python
"Team Name As It Appears In Odds API": ESPN_ID,
```

4. Run `make stats` again to pull their data.

---

## Troubleshooting

**"No upcoming games found"** — Run `make odds` to refresh. If the season is over or between games, there may genuinely be no upcoming games.

**"Could not find team matching..."** — The team name you typed doesn't partially match anything in the odds database. Run `make today` to see exact team names.

**"Ambiguous team name"** — Your search matched multiple teams (e.g., "Carolina" matches both North Carolina and South Carolina). Be more specific.

**Ollama connection errors** — Make sure Ollama is running (`ollama serve`) and that you've pulled the model (`ollama pull llama3.1:8b`).

**API credit warnings** — The free tier of The Odds API gives 500 requests/month. At 2 fetches per day, that's roughly 250/month, leaving headroom. Check the credit count printed after each fetch.