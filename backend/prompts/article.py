"""Article base prompt: summary + knowledge-base metadata for web articles.

Replaces 'base' when source="web". There is no cleaned transcript — the
article is summarized in the model's own words, never reproduced.
"""

ROLE_PREAMBLE = "an expert research librarian who curates a personal knowledge base"

JSON_KEYS = [
    {
        "key": "tldr",
        "description": "One sentence (max 30 words) stating the article's core point.",
    },
    {
        "key": "summary",
        "description": "A 2-4 paragraph summary in your own words covering the argument, evidence and conclusion.",
    },
    {
        "key": "key_points",
        "description": "A JSON array of 4-8 short strings — the most important ideas, findings or recommendations.",
    },
    {
        "key": "topics",
        "description": "A JSON array of 5-10 short tags (1-3 words each) describing the main topics.",
    },
    {
        "key": "content_type",
        "description": 'One of: "tutorial", "opinion", "analysis", "news", "research", "case-study", "reference", "interview", "review", "essay", "other".',
    },
    {
        "key": "useful_for",
        "description": "A JSON array of 1-3 short phrases describing when this article is worth revisiting (e.g. \"planning a pricing strategy\").",
    },
]

RULES = """Rules for "tldr", "summary" and "key_points":
- Paraphrase. Never copy sentences verbatim; short quoted phrases only when the exact wording matters, marked with quotes.
- Use ONLY information in the article text — no outside facts or opinions.
- Preserve concrete specifics that make the note useful later: numbers, names, methods, definitions, dates.
- Write the substance directly — avoid "the article says" / "the author argues" filler.
- Ignore leftover boilerplate (newsletter prompts, cookie notices, author bios, related-post teasers).
- Write in the same language as the article.

Rules for "topics":
- 5-10 tags, each 1-3 words.
- Lowercase, use hyphens for multi-word tags (e.g. "machine-learning", "climate-change").
- Focus on core subject matter — concepts, domains, technologies, people — not peripheral mentions.
- Return as a JSON array of strings.

Rules for "useful_for":
- Concrete situations or questions, not generic statements like "learning more about X"."""
