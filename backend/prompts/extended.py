"""Extended summary prompt: topic-by-topic editorial rewrite.

Added when the user checks "Create extended summary".
"""

ROLE_PREAMBLE = "editorial writer"

JSON_KEYS = [
    {
        "key": "extended_summary",
        "description": "A thorough, topic-by-topic editorial summary of the entire video.",
    },
]

RULES = """Rules for "extended_summary":
- Cover every major topic raised in the video in the order it appears.
- Consolidate and rewrite ideas for clarity — do not copy-paste from the transcript.
- Write in a serious, editorial tone suitable for a professional audience.
- Use ## Markdown headings for each topic section.
- Each section should be several paragraphs of synthesized prose.
- Length is determined by the content: aim to be comprehensive without padding.
- Do NOT include timestamps or meta-commentary about the video format.
- This should read as a standalone document — someone who has not watched the video should come away fully informed."""
