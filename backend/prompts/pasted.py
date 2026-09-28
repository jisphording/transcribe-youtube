"""Pasted-page prompt: used with 'article' when the user copied the page text by hand.

The text is a raw clipboard dump of the whole page, so besides the article it
contains navigation, tag lists, author bios, comments and recommendations.
This module asks Claude to recover the metadata and mark the article body.
"""

ROLE_PREAMBLE = "an expert at separating an article from the surrounding web page clutter"

JSON_KEYS = [
    {
        "key": "title",
        "description": "The article's headline exactly as written in the text (not a subtitle, site name or teaser).",
    },
    {
        "key": "authors",
        "description": "A JSON array of the article author names. [] if not stated.",
    },
    {
        "key": "published",
        "description": 'The publication date as YYYY-MM-DD if stated near the headline/byline, else "". Use a year only if the text gives one.',
    },
    {
        "key": "body_first_words",
        "description": "The first 6-10 words of the article body, copied VERBATIM (exact spelling, punctuation and case).",
    },
    {
        "key": "body_last_words",
        "description": "The last 6-10 words of the article body, copied VERBATIM (exact spelling, punctuation and case).",
    },
]

RULES = """Rules for pasted page text:
- The article text was copied manually from the whole web page. It contains page clutter: navigation and login links, tag lists, bylines, follower counts, clap/like counts, image captions, newsletter or sign-up prompts, comment sections, "more from this author" and recommended-article teasers, footers.
- Treat ONLY the article itself as the source. Everything in the summary, key points, topics, resources and all other keys must come from the article body — never from teasers of other articles, comments or sidebar content.
- The article body starts after the headline/byline block and ends before tag lists, author bios, responses or recommendations.
- "body_first_words" and "body_last_words" must appear character-for-character in the text so they can be located with a text search. Do not paraphrase or fix typos in them.
- A year-less date (e.g. "Jun 11") from the byline: return "" unless the year is clear from the text."""
