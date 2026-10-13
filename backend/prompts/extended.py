"""Extended summary prompt: topic-by-topic summary in the speaker's own voice.

Added when the user checks "Create extended summary".
"""

ROLE_PREAMBLE = "editorial writer"

JSON_KEYS = [
    {
        "key": "extended_summary",
        "description": "A thorough, topic-by-topic summary of the entire video that stays close to the speaker's own words and weaves in exact quotes.",
    },
]

RULES = """Rules for "extended_summary":
- The reader uses this summary as a reference and as raw material for learning and writing, so it must stay faithful to what the speaker said and how they said it.
- Cover every major topic raised in the video in the order it appears.
- Condense redundancy and tangents, but stay close to the speaker's own wording, key terms and framing. Keep their hedges and certainty as stated — do not turn "I think" into a fact.
- Quote the speaker verbatim wherever their own words are sharp, specific and on the section's topic: definitions, core claims, memorable phrasings, concrete advice, numbers. Skip quotes that ramble or drift off topic.
- Quotes must be exact text from the transcript (filler removed is fine), wrapped in typographic quotes “…” and attributed when there are several speakers (e.g. As the guest puts it, “…”).
- Use ## Markdown headings for each topic section.
- Each section should be several paragraphs of prose with quotes woven in.
- Length is determined by the content: aim to be comprehensive without padding.
- Do NOT include timestamps or meta-commentary about the video format.
- This should read as a standalone document — someone who has not watched the video should come away fully informed."""
