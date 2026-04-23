"""Resources prompt: extract all mentioned products, software, websites, and services."""

ROLE_PREAMBLE = "research assistant who identifies referenced tools and services"

JSON_KEYS = [
    {
        "key": "resources",
        "description": 'A JSON array of objects, each with "name" (string) and "type" (one of: "product", "software", "website", "service", "tool", "platform").',
    },
]

RULES = """Rules for "resources":
- Extract every distinct product, software application, website, online service, tool, or platform that is mentioned by name.
- Include company names only when they are referenced as a service or product (e.g. "Google" as a search engine = yes; mentioned in passing as a company = no).
- Include URLs or domain names when explicitly stated; otherwise omit.
- Do NOT include generic concepts, academic papers, or people's names.
- Each entry must have "name" (the exact name as mentioned) and "type" (best-fit category).
- Deduplicate: if the same resource is mentioned multiple times, include it only once.
- Return an empty array [] if no resources are mentioned.
- Example entry: {"name": "Obsidian", "type": "software"}"""
