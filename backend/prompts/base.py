"""Base prompt: transcript cleaning + short summary.

This is always included in every prompt composition.
"""

ROLE_PREAMBLE = "an expert transcript editor and summarizer"

JSON_KEYS = [
    {
        "key": "summary",
        "description": "A concise 3-5 sentence summary of the video's main content and key takeaways.",
    },
    {
        "key": "transcript",
        "description": "The cleaned transcript in Markdown format.",
    },
]

RULES = """Rules for "summary":
- 3-5 sentences maximum.
- Capture the main argument, key takeaways, and conclusion.

Rules for "transcript":
- Remove ALL filler words: "um", "uh", "like", "you know", "sort of", "kind of", "basically", "literally", "actually", "right", "okay so", "so yeah", "I mean", etc.
- Keep the content as close to the original wording as possible — do not paraphrase or rewrite sentences.
- If there are clearly multiple speakers (e.g. interview format), format as:
  **Speaker A:** Their text here.
  **Speaker B:** Their response here.
  Use generic labels (Host, Guest, Speaker 1, etc.) unless names are clearly mentioned.
- Break the transcript into logical chapters using ## headings (Markdown h2).
  For short videos (< 10 min): 2-4 chapters.
  For medium videos (10-30 min): 4-8 chapters.
  For long videos (> 30 min): 8-15 chapters.
- Chapter headings should be descriptive and reflect the content of that section.
- Do NOT include timestamps.
- Do NOT add any content that wasn't in the original transcript.
- Preserve paragraph breaks for readability — group related sentences together."""
