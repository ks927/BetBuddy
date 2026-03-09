DATA_DIR = data

# Fetch fresh odds, stats, and injuries before a session
fetch:
	python3 $(DATA_DIR)/fetch_odds.py && python3 $(DATA_DIR)/fetch_stats.py && python3 $(DATA_DIR)/fetch_injuries.py

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

# Manual pick entry (if auto-parse fails)
log:
	python3 prediction_logger.py

# Wipe the database and start fresh
reset:
	rm -f db/sports.db
	@echo "Database cleared."

.PHONY: fetch odds stats injuries today games query reset