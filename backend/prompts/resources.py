"""Resources prompt: extract all mentioned products, software, websites, and services."""

ROLE_PREAMBLE = "research assistant who identifies referenced tools and services"

JSON_KEYS = [
    {
        "key": "resources",
        "description": 'A JSON array of objects, each with "name" (string), "mention" (a short verbatim source excerpt containing the name), and "type" (one of: "product", "software", "website", "service", "tool", "platform").',
    },
]

RULES = """Rules for "resources":
- Use ONLY the original transcript as evidence, never metadata, descriptions, generated summaries or outside knowledge. Never infer a resource from a generic description, expand a name to an unstated brand, or add alternatives. Omit uncertain names.
- Extract every distinct product, software application, website, online service, tool, or platform that is mentioned by name.
- Include company names only when they are referenced as a service or product (e.g. "Google" as a search engine = yes; mentioned in passing as a company = no).
- Include URLs or domain names when explicitly stated; otherwise omit.
- Do NOT include generic concepts, academic papers, or people's names.
- Each entry must have "mention" (a short exact excerpt from the original transcript containing the name), "name" (the exact name as mentioned) and "type" (best-fit category).
- Deduplicate: if the same resource is mentioned multiple times, include it only once.
- Return an empty array [] if no resources are mentioned.
- Example entry: {"name": "Obsidian", "type": "software", "mention": "I use Obsidian for notes."}"""
