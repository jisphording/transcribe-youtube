"""Focus topic prompt: targeted deep-dive on a user-specified topic.

Added when the user provides a focus instruction (e.g. "Focus only on marketing advice").
"""

ROLE_PREAMBLE = "expert content curator"

JSON_KEYS = [
    {
        "key": "focused_summary",
        "description": "A detailed, thorough summary covering only content relevant to the user's focus topic (provided in the input as 'Focus instruction'). Must be based exclusively on what was said in the transcript — no outside knowledge.",
    },
]

RULES = """Rules for "focused_summary":
- Read the entire transcript, but extract and synthesize ONLY the parts that relate to the user's focus instruction.
- Ignore all content that is not relevant to the specified topic.
- CRITICAL: Use ONLY information explicitly stated in the transcript. Do NOT add context, facts, explanations, or opinions from outside the video — not even well-known facts. If something was not said in the video, it must not appear in the output.
- Write in clear prose that stays close to the speaker's own wording, key terms and framing. Keep their hedges and certainty as stated.
- Quote the speaker verbatim wherever their own words are sharp, specific and on the focus topic: definitions, core claims, memorable phrasings, concrete advice, numbers. Skip quotes that ramble or drift off topic.
- Quotes must be exact text from the transcript (filler removed is fine), wrapped in typographic quotes “…” and attributed when there are several speakers.
- Use ## Markdown headings to organize the focused content by sub-topic or theme.
- Each section should be several paragraphs of prose with quotes woven in.
- If the topic appears only briefly in the video, note that and include whatever is there.
- Do NOT pad with unrelated content to fill space.
- Do NOT include meta-commentary like "the host discusses..." — write the substance directly."""
