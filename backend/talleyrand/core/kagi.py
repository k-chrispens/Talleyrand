"""
Web search for the agent CLIs, through the Kagi Search API.

The agents run with no tools, so they cannot search; Talleyrand searches on
their behalf and puts the results in the prompt (core/llm.py's _stream_agent).
The key is the operator's own (settings.kagi_api_key, billed per query by
Kagi) and never reaches an agent's environment. API: POST /api/v1/search,
Bearer auth, results under data.search; checked against Kagi's published
OpenAPI client on 2026-09-24.
"""

import asyncio
from dataclasses import dataclass

import httpx

from talleyrand.core.config import settings

SEARCH_URL = "https://kagi.com/api/v1/search"
TIMEOUT_SECONDS = 20.0
RESULTS_PER_QUERY = 5


class KagiError(Exception):
    """A search failed; str(exc) is a user-facing message."""


@dataclass(frozen=True)
class SearchResult:
    url: str
    title: str
    snippet: str
    published: str | None


async def search(
    queries: list[str], *, transport: httpx.AsyncBaseTransport | None = None
) -> list[SearchResult]:
    """Every query's top results, in query order, each URL once."""
    async with httpx.AsyncClient(
        headers={"Authorization": f"Bearer {settings.kagi_api_key}"},
        timeout=TIMEOUT_SECONDS,
        transport=transport,
    ) as client:
        batches = await asyncio.gather(*(_search_one(client, query) for query in queries))
    seen: set[str] = set()
    results = []
    for result in (r for batch in batches for r in batch):
        if result.url not in seen:
            seen.add(result.url)
            results.append(result)
    return results


async def _search_one(client: httpx.AsyncClient, query: str) -> list[SearchResult]:
    try:
        response = await client.post(SEARCH_URL, json={"query": query, "limit": RESULTS_PER_QUERY})
    except httpx.HTTPError as e:
        raise KagiError(f"Kagi could not be reached ({type(e).__name__})") from e
    try:
        body = response.json()
    except ValueError:
        body = {}
    if response.status_code != 200:
        messages = "; ".join(e.get("message", "") for e in body.get("errors") or [])
        raise KagiError(
            f"Kagi refused the search ({response.status_code}: {messages or 'no detail'})"
        )
    return [
        SearchResult(
            url=item["url"],
            title=item.get("title") or item["url"],
            snippet=item.get("snippet") or "",
            published=item.get("time"),
        )
        for item in (body.get("data") or {}).get("search") or []
        if item.get("url")
    ]
