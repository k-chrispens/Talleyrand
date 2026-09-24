"""
An agent CLI takes text only, so a PDF reaches it as the text document its
text layer makes: fitted with the other documents, never dropped in silence.
"""

from uuid import uuid4

import pytest

from talleyrand.core import llm
from talleyrand.core.test_documents import _blank_pdf, _data_uri, _text_pdf
from talleyrand.features.graph.dtos import DocumentDTO, GraphNoId, NodeContentDTO, NodeDTO
from talleyrand.features.research import kickstart, query_service

NODE = str(uuid4())


def _pdf_doc(name: str, pdf: bytes) -> DocumentDTO:
    return DocumentDTO(id=str(uuid4()), name=name, type="pdf", content=_data_uri(pdf))


def _graph(model: str, documents: list[DocumentDTO]) -> GraphNoId:
    return GraphNoId(
        nodes=[NodeDTO(id=NODE)],
        edges=[],
        node_contents=[
            NodeContentDTO(
                id=NODE,
                query="Does the treaty hold?",
                response="",
                selected_model=model,
                documents=[],
                selection_suggestions=[],
                selections=[],
            )
        ],
        case_documents=documents,
    )


@pytest.fixture
def sent(monkeypatch):
    """What the answer would be asked with, instead of asking."""
    calls: dict = {}

    async def fake_stream_text(**kwargs):
        calls.update(kwargs)

    monkeypatch.setattr(query_service, "stream_text", fake_stream_text)
    return calls


@pytest.mark.asyncio
async def test_an_agent_answer_reads_the_pdf_text_as_a_document(sent):
    treaty = _pdf_doc("treaty", _text_pdf("Signed at Vienna"))

    await query_service.do_research_query(NODE, _graph("claude-code", [treaty]), "", "")

    assert "- treaty.pdf: Signed at Vienna" in sent["cached_prefix"]
    assert sent["pdf_documents"] == []


@pytest.mark.asyncio
async def test_an_unreadable_pdf_is_left_for_the_answer_to_mention(sent):
    scan = _pdf_doc("scan", _blank_pdf())

    await query_service.do_research_query(NODE, _graph("hermes", [scan]), "", "")

    assert [doc.filename for doc in sent["pdf_documents"]] == ["scan.pdf"]


@pytest.mark.asyncio
async def test_an_api_answer_still_gets_the_pdf_itself(sent):
    treaty = _pdf_doc("treaty", _text_pdf("Signed at Vienna"))

    await query_service.do_research_query(
        NODE, _graph("gpt-6-astra-medium", [treaty]), "sk-test", ""
    )

    assert "Signed at Vienna" not in sent["cached_prefix"]
    assert [doc.filename for doc in sent["pdf_documents"]] == ["treaty.pdf"]


@pytest.mark.asyncio
async def test_the_answer_names_the_pdfs_it_could_not_read(monkeypatch):
    async def fake_run(**_kwargs):
        return {"result": "The treaty holds."}

    monkeypatch.setattr(llm.claude_code, "run", fake_run)
    scan = llm.PdfAttachment(filename="scan.pdf", data_uri=_data_uri(_blank_pdf()))

    stream = await llm._stream_agent(
        caller="test",
        provider="claude_code",
        instructions="",
        cached_prefix="",
        user_prompt="",
        pdf_documents=[scan],
        web_search_enabled=False,
        verbosity="low",
    )
    execution, answer = [chunk async for chunk in stream]

    assert execution.notes[0].startswith("scan.pdf could not be read")
    assert answer.text == "The treaty holds."


@pytest.mark.asyncio
async def test_kickstart_on_an_agent_inlines_the_pdf_text(monkeypatch):
    monkeypatch.setattr(kickstart.settings, "agent_backend", "claude_code")
    treaty = _pdf_doc("treaty", _text_pdf("Signed at Vienna"))

    content = await kickstart._user_content(["NOTES:\nx"], [treaty], api_key="", system_prompt="")

    assert isinstance(content, str)
    assert "- treaty.pdf: Signed at Vienna" in content


@pytest.mark.asyncio
async def test_kickstart_on_an_agent_refuses_an_unreadable_pdf(monkeypatch):
    monkeypatch.setattr(kickstart.settings, "agent_backend", "hermes")
    locked = _pdf_doc("locked", _blank_pdf(password="secret"))

    with pytest.raises(llm.StructuredGenerationError, match="locked.pdf .*password"):
        await kickstart._user_content(["NOTES:\nx"], [locked], api_key="", system_prompt="")
