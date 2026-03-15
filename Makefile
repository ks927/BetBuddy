DATA_DIR = data

# Load .env file if it exists
ifneq (,$(wildcard ./.env))
    include .env
    export
endif

# Fetch fresh odds, stats, and injuries before a session
fetch:
	python3 $(DATA_DIR)/fetch_odds.py && python3 $(DATA_DIR)/fetch_stats.py && python3 $(DATA_DIR)/fetch_injuries.py && python3 -m data.fetch_ats && make publish

# Fetch odds only
odds:
	python3 $(DATA_DIR)/fetch_odds.py

# Fetch stats only (requires odds to have run first)
stats:
	python3 $(DATA_DIR)/fetch_stats.py

# Fetch injuries only (requires odds to have run first)
injuries:
	python3 $(DATA_DIR)/fetch_injuries.py

# List today's games
today:
	python3 list_games.py today

# List all upcoming games
games:
	python3 list_games.py

# Query the system — usage: make query Q="Duke vs Syracuse"
query:
	python3 query.py "$(Q)"

# Grade predictions against actual results
score:
	python3 -m data.fetch_scores && python3 score_predictions.py

# View your record
record:
	python3 record.py

# View full record with all picks
record-detail:
	python3 record.py --detail

# View ungraded (pending) predictions
pending:
	@echo ""
	@echo "── Pending Predictions ──"
	@echo ""
	@sqlite3 -header -column db/sports.db \
		"SELECT game_date AS date, away_team AS away, home_team AS home, market, pick, confidence AS conf FROM predictions WHERE result IS NULL ORDER BY game_date"
	@echo ""

# Remove a pending prediction by ID
unpick:
	@sqlite3 -header -column db/sports.db \
		"SELECT id, game_date AS date, away_team AS away, home_team AS home, market, pick, confidence AS conf FROM predictions WHERE result IS NULL ORDER BY game_date"
	@echo ""
	@read -p "Enter ID to delete (or 'n' to cancel): " id; \
	if [ "$$id" != "n" ]; then \
		sqlite3 db/sports.db "DELETE FROM predictions WHERE id = $$id AND result IS NULL"; \
		echo "✓ Deleted prediction $$id"; \
	else \
		echo "Cancelled."; \
	fi

ats:
	python3 -m data.fetch_ats

slate:
	python3 slate.py

# Manual pick entry (if auto-parse fails)
log:
	python3 prediction_logger.py

publish:
	python3 publish.py --push

publish-preview:
	python3 publish.py

# Wipe the database and start fresh
reset:
	rm -f db/sports.db
	@echo "Database cleared."

.PHONY: fetch odds stats injuries today games query reset