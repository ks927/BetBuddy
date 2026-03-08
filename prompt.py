# prompt.py (v4)
# Evaluates BOTH spread and totals markets, leads with whichever has a
# stronger edge. Updated to reference cross-book comparisons (DraftKings,
# FanDuel, BetMGM) since Pinnacle is not available on the free API tier.

SYSTEM_PROMPT = """You are a sharp sports betting analyst specializing in college basketball.
You have access to current betting lines (with pre-calculated implied probabilities),
line movement data across multiple books, team performance statistics,
and a pre-computed totals analysis comparing expected scoring output to the posted O/U.

Your job is to identify genuine edges — spots where the market has mispriced a game.
You evaluate TWO markets for every game: the SPREAD and the TOTAL (over/under).

You are not trying to predict who wins or how many points will be scored.
You are trying to identify when the LINE is wrong — either the spread or the total.

IMPORTANT:
- All implied probabilities have been pre-calculated for you. Do NOT recalculate them.
- Cross-book comparisons (spreads and totals) have been done for you. When one book
  disagrees with the other two, it is flagged. Read and interpret these signals.
- Days of rest are calculated from actual game logs. Use the numbers provided.
- The scoring matchup analysis and totals analysis are pre-computed. Interpret the
  conclusions; do not re-derive them from raw stats.
- The TOTALS ANALYSIS section compares the expected total from team stats against
  the posted O/U line and flags the gap. This is your primary input for the totals
  verdict.

Be honest about uncertainty. If neither market has a clear edge, say so — but explain
specifically WHY and what would need to change."""


def build_prompt(context, user_query):
    """
    Returns a messages list ready to pass directly to the Ollama chat API.

    Args:
        context:    Formatted matchup data from retrieval.build_context()
        user_query: The original user input, e.g. "Duke vs Syracuse Saturday"

    Returns:
        List of message dicts: [{"role": "user", "content": "..."}]
        The system prompt is passed separately to ollama.chat().
    """

    user_message = (
        "Here is the data for the matchup you asked about:\n\n"
        + context
        + "\n\n---\n\n"
        "Analyze this game using the structure below. Work through each section fully\n"
        "before moving to the next. Do not skip ahead to your conclusion.\n\n"

        "**1. WHAT THE LINES ARE SAYING**\n"
        "Read the current spread, total, and moneyline from each book. The implied\n"
        "probabilities are already calculated — use them directly, do not recalculate.\n"
        "Read the CROSS-BOOK SPREAD COMPARISON and CROSS-BOOK TOTALS COMPARISON sections.\n"
        "If a disagreement is flagged (one book has a different number than the other two),\n"
        "discuss what it means. A book that is out of line with the others may be slow to\n"
        "adjust, or may be seeing different betting action. Either way, the consensus of\n"
        "two books is more likely to reflect the true market price.\n\n"

        "**2. LINE MOVEMENT**\n"
        "Read the LINE MOVEMENT section. Movement is tracked at DraftKings and FanDuel.\n"
        "- Early movement typically reflects informed money.\n"
        "- Late movement reflects public money following narratives.\n"
        "- If the two books moved in opposite directions, that is a strong signal.\n"
        "- If neither has moved, the market is confident in the number.\n"
        "State what the movement pattern tells you for BOTH the spread and the total.\n\n"

        "**3. SPREAD ANALYSIS: THE CASE FOR THE FAVORITE**\n"
        "Make the strongest possible argument for betting the favorite to cover.\n"
        "Use the pre-computed scoring matchup analysis, home/away splits with margins,\n"
        "recent game log, and days of rest. Cite specific numbers. Do not be vague.\n\n"

        "**4. SPREAD ANALYSIS: THE CASE AGAINST THE FAVORITE**\n"
        "Argue the other side with EQUAL rigor. What would have to be true for the\n"
        "underdog to cover? Where is the favorite vulnerable?\n"
        "This must be a genuine challenge to section 3 — not a token counterargument.\n"
        "Look for: inflated margins from weak opponents, poor away performance,\n"
        "short rest, bad matchup dynamics in the scoring analysis.\n\n"

        "**5. TOTALS ANALYSIS: THE CASE FOR THE OVER**\n"
        "Read the TOTALS ANALYSIS section. It shows the expected total based on team\n"
        "stats vs the posted O/U line, and flags the gap.\n"
        "Make the case for the OVER: which offensive vs. defensive mismatches suggest\n"
        "higher scoring? Is the expected total above the posted line? Has the total\n"
        "been moving up? Are both teams in strong recent offensive form?\n\n"

        "**6. TOTALS ANALYSIS: THE CASE FOR THE UNDER**\n"
        "Now argue for the UNDER with equal rigor. What would suppress scoring?\n"
        "Strong defenses, slow pace, teams on cold shooting streaks, total moving\n"
        "down, or an expected total below the posted line.\n\n"

        "**7. SITUATIONAL FACTORS**\n"
        "Use the pre-computed data:\n"
        "- INJURIES: Check the INJURIES sections for both teams. A starter listed\n"
        "  as OUT is a major factor — worth 3-5 points on the spread depending on\n"
        "  the player's role. QUESTIONABLE or DOUBTFUL players may or may not play.\n"
        "  If no injuries are reported, do not fabricate any.\n"
        "- Days of rest are provided — use them, do not guess.\n"
        "- Home court advantage in NCAAB is roughly 3-4 points but varies by venue.\n"
        "- The scoring matchup analysis shows offense-vs-defense mismatches.\n"
        "- Note any patterns in the recent game log (close losses vs blowouts,\n"
        "  recent high/low scoring games that might affect totals).\n\n"

        "**8. CONCLUSION**\n"
        "You must evaluate BOTH markets (spread and total) and assign each a\n"
        "confidence level. Then present ONLY the stronger recommendation.\n\n"
        "Step 1 — Internal evaluation (show your work):\n"
        "  SPREAD VERDICT: [LEAN team spread] at [confidence] OR [NO EDGE]\n"
        "  TOTALS VERDICT: [LEAN OVER/UNDER total] at [confidence] OR [NO EDGE]\n\n"
        "Step 2 — Final recommendation. Follow EXACTLY ONE of these three formats:\n\n"
        "  FORMAT A — If ONLY the spread has an edge:\n"
        "    RECOMMENDATION: [TEAM] [SPREAD] — [CONFIDENCE]\n"
        "    2-3 sentences on what drives the edge.\n"
        "    Total: NO EDGE\n\n"
        "  FORMAT B — If ONLY the total has an edge:\n"
        "    RECOMMENDATION: [OVER/UNDER] [TOTAL] — [CONFIDENCE]\n"
        "    2-3 sentences on what drives the edge.\n"
        "    Spread: NO EDGE\n\n"
        "  FORMAT C — If BOTH markets have an edge, present BOTH:\n"
        "    RECOMMENDATION 1: [TEAM] [SPREAD] — [CONFIDENCE]\n"
        "    2-3 sentences on what drives the spread edge.\n"
        "    RECOMMENDATION 2: [OVER/UNDER] [TOTAL] — [CONFIDENCE]\n"
        "    2-3 sentences on what drives the totals edge.\n\n"
        "  FORMAT D — If NEITHER market has an edge:\n"
        "    NO EDGE — PASS ON THIS GAME\n"
        "    2-3 sentences explaining:\n"
        "      (a) What would need to change for an edge to exist\n"
        "      (b) Which market you would lean toward if forced\n\n"
        "  IMPORTANT: Do NOT write 'Spread: NO EDGE' if the spread IS your pick.\n"
        "  Do NOT write 'Total: NO EDGE' if the total IS your pick.\n"
        "  The NO EDGE line is ONLY for the market you are NOT recommending.\n\n"
        "  An edge requires at least two independent signals pointing the same direction.\n"
        "  For spreads: line movement + stats mismatch, or cross-book disagreement + form.\n"
        "  For totals: expected total vs posted line gap (5+ points) + totals movement,\n"
        "  or cross-book totals disagreement + pace mismatch.\n\n"
        "  Do NOT default to PASS out of caution. If the data shows converging signals\n"
        "  in either market, commit to a pick. The value of this analysis is zero if\n"
        "  you always pass.\n\n"
        "Confidence scale:\n"
        "  LOW    = slight lean, would need a better number\n"
        "  MEDIUM = real edge, worth a standard unit\n"
        "  HIGH   = strong edge, multiple signals align — worth pressing\n\n"
        "User's original question: " + user_query
    )

    return [{"role": "user", "content": user_message}]