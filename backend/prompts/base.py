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
        "key": "topics",
        "description": "A JSON array of 5-10 short tags (1-3 words each) describing the main topics discussed.",
    },
    {
        "key": "transcript",
        "description": "The cleaned transcript in Markdown format.",
    },
]

RULES = """Rules for "summary":
- 3-5 sentences maximum.
- Capture the main argument, key takeaways, and conclusion.

Rules for "topics":
- 5-10 tags, each 1-3 words.
- Lowercase, use hyphens for multi-word tags (e.g. "machine-learning", "climate-change").
- Focus on core subject matter — concepts, domains, technologies, people — not peripheral mentions.
- Return as a JSON array of strings.

Rules for "transcript":
- Style: clean verbatim. The reader uses this transcript as a reference and as raw material to quote and learn from, so it must read as the speaker's own words, not as an edited rewrite.
- Remove only what carries no meaning: hesitation sounds ("um", "uh", "er", "hmm"), stutters and doubled words ("the the"), false starts the speaker abandons and then restates, and verbal tics used purely as pauses ("you know", "like", "I mean", "so yeah" while searching for words).
- Keep every word that affects meaning or tone: hedges and qualifiers ("kind of", "sort of", "I think", "probably"), emphasis ("actually", "really", "literally" when they stress a point), "like" when it compares or introduces quoted speech, self-corrections, rhetorical questions, asides, jokes, anecdotes, examples, names and numbers.
- When the speaker states the same point several times in a row in near-identical words, keep the single most complete phrasing. Keep repetition used on purpose for emphasis, and keep a point that comes back later with new detail.
- Keep the speaker's vocabulary, idioms and sentence rhythm. Do not paraphrase, reorder, merge or condense sentences. Fix grammar only where a spoken slip would make the sentence hard to read.
- Examples:
  Spoken: "So, um, I— I think it kind of works, you know, but it's actually slower." → "I think it kind of works, but it's actually slower."
  Spoken: "And he was like, 'we're not shipping that.'" → keep unchanged.
  Spoken: "The key thing is trust. Trust is the key thing here. It's really about trust." → "The key thing is trust. It's really about trust."
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
