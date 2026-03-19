# BetBuddy — Claude Guidelines

## What this project is
NCAA basketball betting analysis tool. Fetches live odds, team stats, ATS records, and efficiency ratings (Barttorvik), then uses Gemini to analyze each matchup and produce structured picks. Results publish to GitHub Pages as a daily static site.

## Common commands
```bash
make fetch            # Full morning refresh: odds + stats + injuries + ATS + Barttorvik + publish
make publish          # Run slate analysis + regenerate HTML + push to gh-pages
make live             # Refresh scores only + push (saves API quota, no re-analysis)
make publish-preview  # Regenerate HTML locally, no push
make slate            # Batch-analyze today's un-analyzed games only (no push)
make score            # Fetch latest scores + grade pending picks
make record           # Show W-L-P record and ROI
make query Q="Duke vs UNC"  # Analyze a single matchup interactively
make fetch            # Full data refresh
make reset            # Wipe sports.db and start fresh
```

## Architecture
```
data/fetch_*.py  →  sports.db  →  retrieval.py (context)  →  prompt.py  →  Gemini API
                                                                              ↓
                                                              prediction_logger.py (parse + save)
                                                                              ↓
                                                              publish.py  →  gh-pages
```

**Key scripts:**
- `slate.py` — batch analysis runner; skips already-analyzed games; has retry logic for Gemini 503s
- `analysis.py` — shared analysis runner used by both `slate.py` and `query.py`; do not duplicate here
- `query.py` — interactive single-game analysis with real-time streaming output
- `retrieval.py` — builds context dict from DB; all math (implied probabilities, margins, etc.) lives here
- `prompt.py` — system prompt and 6-section analysis template; this is the most sensitive file for output quality
- `prediction_logger.py` — parses picks from LLM output (4 fallback strategies); saves to DB
- `publish.py` — generates `site/index.html` and `site/scores.json`; pushes to gh-pages via git stash/branch swap

## Database (SQLite — db/sports.db)
Key tables: `odds`, `predictions`, `team_stats`, `game_results`, `injuries`, `scores`, `team_ats`, `barttorvik_stats`

- `predictions.predicted_at` — ISO datetime, used for "Last analysis" timestamp in UI
- `odds.commence_time` — ISO 8601 with Z suffix; normalize with `.replace("Z", "+00:00")` before parsing
- Always use ET timezone for "today" logic (e.g. determining today's slate)
- Team names vary between APIs (Odds API vs ESPN vs Barttorvik); use fuzzy matching via `normalize_team()` in each module

## Code conventions
- Constants: `SCREAMING_SNAKE_CASE` (`DB_PATH`, `MAX_RETRIES`, `MODEL`)
- Functions: `snake_case`
- Section headers in files: `# ── SECTION NAME ──`
- ANSI colors defined at module level: `GREEN`, `RED`, `CYAN`, `DIM`, `RESET` — used for terminal output only, never in HTML
- Docstrings: single-line comment above function; inline `# —` comments for algorithm steps
- No requirements.txt — dependencies listed in Readme.md (google-genai, requests, python-dotenv)

## Publishing flow
`publish.py --push` does: stash working changes → checkout gh-pages → commit site/ → push → checkout main → pop stash. Don't interrupt or this can leave the repo in a detached state.

## Important gotchas
- **Python version:** `python3` resolves to Xcode's system Python 3.9 (x86_64). Packages must be installed with `python3 -m pip install` and must be arm64 wheels. If you see `incompatible architecture` import errors, run `python3 -m pip install --force-reinstall <package>` to get the correct wheel.
- **Section 1 is pre-rendered:** `query.py` prints the odds/lines section from DB *before* calling the LLM. This is intentional — prevents hallucinated spreads and totals.
- **Neutral-site logic:** Analysis uses favorite/underdog framing instead of home/away for NCAA tournament games. Don't change this.
- **Retry logic exists in two places:** `analysis.py` (MAX_RETRIES=4, exponential backoff) and `slate.py` (same). Don't add a third layer.
- **scores.json:** Updated separately from index.html so live scores can be refreshed (`make live`) without re-running the full analysis pipeline.
- **`site/` is gitignored on main** — it only lives on the gh-pages branch.

## Environment
API keys live in `.env` (never commit this):
- `GEMINI_API_KEY` — from aistudio.google.com
- `ODDS_API_KEY` — from the-odds-api.com (500 req/month free tier — use sparingly)
