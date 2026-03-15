# analysis.py
# Extracted analysis runner — callable from query.py (interactive) and
# slate.py (batch). Handles building context, calling Gemini, and
# returning the full analysis text + parsed picks.
#
# Does NOT handle logging or user prompts — that's the caller's job.
 
import os
from dotenv import load_dotenv
from google import genai
from google.genai import types
from retrieval import build_context
from prompt import build_prompt, SYSTEM_PROMPT
from prediction_logger import parse_all_picks
 
load_dotenv()
 
MODEL = "gemini-2.5-flash"
client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
 
 
def run_analysis(team1_fragment, team2_fragment, stream=False, quiet=False):
    """
    Run a full analysis for a matchup.
 
    Args:
        team1_fragment: First team query string (e.g., "houston cougars")
        team2_fragment: Second team query string (e.g., "byu")
        stream: If True, print tokens as they arrive (for interactive use)
        quiet: If True, suppress all output
 
    Returns:
        dict with keys:
            context: raw context string from retrieval
            section1_text: pre-rendered section 1
            analysis_text: full analysis (section1 + LLM output)
            picks: list of parsed pick dicts [{market, pick, confidence}, ...]
            error: error string if something went wrong, else None
    """
    # Build context
    result = build_context(team1_fragment, team2_fragment)
 
    if isinstance(result, tuple):
        context, section1_text = result
    else:
        context = result
        section1_text = ""
 
    # Check for errors
    error_signals = ["could not find", "no upcoming game", "ambiguous team"]
    if any(sig in context.lower() for sig in error_signals):
        return {
            "context": context,
            "section1_text": "",
            "analysis_text": "",
            "picks": [],
            "error": context,
        }
 
    # Build prompt
    messages = build_prompt(context, section1_text, f"{team1_fragment} vs {team2_fragment}")
 
    # Call Gemini
    contents = []
    for msg in messages:
        contents.append(
            types.Content(
                role=msg["role"],
                parts=[types.Part.from_text(text=msg["content"])],
            )
        )
 
    full_response = []
 
    if stream:
        response = client.models.generate_content_stream(
            model=MODEL,
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                temperature=0.3,
                max_output_tokens=4096,
            ),
        )
        for chunk in response:
            if chunk.text:
                if not quiet:
                    print(chunk.text, end="", flush=True)
                full_response.append(chunk.text)
    else:
        response = client.models.generate_content(
            model=MODEL,
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                temperature=0.3,
                max_output_tokens=4096,
            ),
        )
        if response.text:
            full_response.append(response.text)
 
    raw_text = "".join(full_response)
    full_text = section1_text + "\n\n" + raw_text if section1_text else raw_text
 
    # Parse picks
    picks = parse_all_picks(full_text)
 
    return {
        "context": context,
        "section1_text": section1_text,
        "analysis_text": full_text,
        "picks": picks,
        "error": None,
    }