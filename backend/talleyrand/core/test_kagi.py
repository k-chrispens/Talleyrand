"""
Web search for agent answers: Kagi's results reach the prompt, every result
is reported as a source, and only the ones the answer links to count as cited.
No test here reaches Kagi or an agent CLI.
"""

import json

import httpx
import pytest

from talleyrand.core import kagi, llm
from talleyrand.core.llm import SourceChunk, TextChunk

RESULT = {"url": "https://example.org/vienna", "title": "Congress of Vienna", "snippet": "1815."}


def _kagi(handler) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


@pytest.fixture
def key(monkeypatch):
    monkeypatch.setattr(kagi.settings, "kagi_api_key", "kagi-test-key")


@pytest.mark.asyncio
async def test_each_query_is_one_authenticated_search(key):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, str(request.url), request.headers["authorization"]))
        body = json.loads(request.content)
        return httpx.Response(200, json={"data": {"search": [RESULT | {"title": body["query"]}]}})

    results = await kagi.search(["vienna 1815", "talleyrand"], transport=_kagi(handler))

    assert {s[2] for s in seen} == {"Bearer kagi-test-key"}
    assert {s[:2] for s in seen} == {("POST", kagi.SEARCH_URL)}
    # The same page found twice is one result, from the first query.
    assert [(r.url, r.title) for r in results] == [(RESULT["url"], "vienna 1815")]


@pytest.mark.asyncio
async def test_kagis_error_message_reaches_the_user(key):
    def handler(request):
        return httpx.Response(401, json={"errors": [{"message": "Invalid API key"}]})

    with pytest.raises(kagi.KagiError, match="401: Invalid API key"):
        await kagi.search(["q"], transport=_kagi(handler))


@pytest.mark.asyncio
async def test_an_unreachable_kagi_is_a_kagi_error(key):
    def handler(request):
        raise httpx.ConnectError("offline")

    with pytest.raises(kagi.KagiError, match="could not be reached"):
        await kagi.search(["q"], transport=_kagi(handler))


@pytest.fixture
def agent(monkeypatch):
    """A fake agent: writes one query, then answers citing one result."""
    calls: dict = {"prompts": []}

    async def fake_structured(**kwargs):
        calls["query_prompt"] = kwargs["user_content"]
        return llm.SearchQueries(queries=["congress of vienna", ""])

    async def fake_run(*, system_prompt, prompt, json_schema=None):
        calls["prompts"].append(prompt)
        return {"result": f"It held ([source]({RESULT['url']}))."}

    async def fake_search(queries):
        calls["queries"] = queries
        return [
            kagi.SearchResult(RESULT["url"], RESULT["title"], RESULT["snippet"], "2020-01-01"),
            kagi.SearchResult("https://example.org/other", "Other", "", None),
        ]

    monkeypatch.setattr(llm, "_parse_structured_agent", fake_structured)
    monkeypatch.setattr(llm.claude_code, "run", fake_run)
    monkeypatch.setattr(llm.kagi, "search", fake_search)
    return calls


async def _answer(**overrides):
    kwargs = {
        "caller": "test",
        "provider": "claude_code",
        "instructions": "",
        "cached_prefix": "BRIEF ",
        "user_prompt": "CURRENT QUESTION: did it hold?",
        "pdf_documents": [],
        "web_search_enabled": True,
        "verbosity": "low",
    } | overrides
    return [chunk async for chunk in await llm._stream_agent(**kwargs)]


@pytest.mark.asyncio
async def test_an_answer_searches_and_cites_what_it_links(key, agent):
    chunks = await _answer()

    assert agent["query_prompt"] == "CURRENT QUESTION: did it hold?"
    assert agent["queries"] == ["congress of vienna"]
    prompt = agent["prompts"][0]
    assert prompt.startswith("BRIEF CURRENT QUESTION: did it hold?\n\nWEB SEARCH RESULTS")
    assert (
        "[1] Congress of Vienna (2020-01-01)\n    https://example.org/vienna\n    1815." in prompt
    )
    assert [c.text for c in chunks if isinstance(c, TextChunk)] == [
        "It held ([source](https://example.org/vienna))."
    ]
    sources = [c.source for c in chunks if isinstance(c, SourceChunk)]
    assert [(s.url, s.cited) for s in sources] == [
        ("https://example.org/vienna", True),
        ("https://example.org/other", False),
    ]


@pytest.mark.asyncio
async def test_search_off_searches_nothing(key, agent):
    chunks = await _answer(web_search_enabled=False)

    assert "queries" not in agent and "query_prompt" not in agent
    assert not any(isinstance(c, SourceChunk) for c in chunks)


@pytest.mark.asyncio
async def test_without_a_key_nothing_is_spent_and_the_answer_says_so(agent):
    chunks = await _answer()

    assert "query_prompt" not in agent
    assert chunks[0].text.startswith("*Note: web search did not run (it needs a Kagi API key")


@pytest.mark.asyncio
async def test_a_failed_search_still_answers_and_says_so(key, agent, monkeypatch):
    async def failing_search(queries):
        raise kagi.KagiError("Kagi could not be reached (ConnectError)")

    monkeypatch.setattr(llm.kagi, "search", failing_search)

    chunks = await _answer()

    assert "Kagi could not be reached" in chunks[0].text
    assert chunks[1].text.startswith("It held")
