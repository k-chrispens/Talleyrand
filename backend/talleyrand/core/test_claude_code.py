"""
Tests for the Claude Code backend: the `claude -p` subprocess and the routing
in core/llm.py that sends a local session's model calls through it.

These run a REAL subprocess against a fake `claude` executable, because the
process handling is the part that can go wrong: the prompt must reach stdin
byte for byte, the child must never see an API key that would bill the run to
the API instead of the subscription, and a cancelled or stalled run must take
its whole process group down with it rather than leave `claude` orphaned.
The fake's output mirrors what Claude Code 2.1.280 actually prints
(`--output-format stream-json --verbose`), checked with live calls on
2026-09-23.
"""

import asyncio
import json
import os
import sys
import time
from pathlib import Path

import pytest
from pydantic import BaseModel

from talleyrand.core import claude_code, llm
from talleyrand.core.claude_code import ClaudeCodeError
from talleyrand.core.config import settings
from talleyrand.core.llm import (
    PdfAttachment,
    StructuredGenerationError,
    TextChunk,
    parse_structured,
    stream_text,
)
from talleyrand.core.model_settings import get_model_config

FAKE_CLAUDE = """#!{python}
import json, os, pathlib, subprocess, sys, time

here = pathlib.Path(__file__).parent
mode = (here / "mode").read_text().strip()
args = sys.argv[1:]
prompt = sys.stdin.read()
(here / "last_call.json").write_text(json.dumps(
    {{"args": args, "prompt": prompt, "env": sorted(os.environ), "cwd": os.getcwd()}}
))

init = {{
    "type": "system",
    "subtype": "init",
    "tools": ["StructuredOutput"] if "--json-schema" in args else [],
    "mcp_servers": [],
    "apiKeySource": "none",
}}
if mode == "api_key":
    init["apiKeySource"] = "ANTHROPIC_API_KEY"
if mode == "tools":
    init["tools"] = ["Bash"]
print(json.dumps(init), flush=True)

if mode == "hang":
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    (here / "child_pid").write_text(str(child.pid))
    time.sleep(60)
if mode == "limit":
    print(json.dumps({{
        "type": "result", "subtype": "success", "is_error": True,
        "result": "Claude AI usage limit reached",
    }}))
    sys.exit(1)
if mode == "crash":
    print("claude: something broke", file=sys.stderr)
    sys.exit(2)

result = {{"type": "result", "subtype": "success", "is_error": False, "result": "echo:" + prompt}}
if "--json-schema" in args:
    result["structured_output"] = json.loads((here / "structured").read_text())
print(json.dumps(result))
"""


class _FakeClaude:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.executable = directory / "claude"
        self.executable.write_text(FAKE_CLAUDE.format(python=sys.executable))
        self.executable.chmod(0o755)
        self.mode("ok")

    def mode(self, mode: str) -> None:
        (self.directory / "mode").write_text(mode)

    def structured(self, data: object) -> None:
        (self.directory / "structured").write_text(json.dumps(data))

    @property
    def last_call(self) -> dict:
        return json.loads((self.directory / "last_call.json").read_text())

    def child_pid(self) -> int | None:
        path = self.directory / "child_pid"
        return int(path.read_text()) if path.exists() else None


@pytest.fixture
def fake_claude(tmp_path, monkeypatch) -> _FakeClaude:
    fake = _FakeClaude(tmp_path)
    monkeypatch.setattr(settings, "local_mode", True)
    monkeypatch.setattr(settings, "agent_backend", "claude_code")
    monkeypatch.setattr(settings, "claude_code_executable", str(fake.executable))
    monkeypatch.setattr(claude_code, "CWD", tmp_path / "cwd")
    return fake


def _is_dead(pid: int) -> bool:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.05)
    return False


# --- the subprocess ---------------------------------------------------------


@pytest.mark.asyncio
async def test_prompt_reaches_the_cli_byte_for_byte(fake_claude):
    prompt = "Why? $(rm -rf ~) `id` \"quoted\" 'single' \\ \né中 -- --tools Bash"
    result = await claude_code.run(system_prompt="sys", prompt=prompt)
    assert result["result"] == "echo:" + prompt


@pytest.mark.asyncio
async def test_the_cli_runs_with_no_tools_no_config_and_no_persistence(fake_claude):
    await claude_code.run(system_prompt="Be brief.", prompt="hi")
    args = fake_claude.last_call["args"]
    assert args[args.index("--tools") + 1] == ""
    for flag in (
        "-p",
        "--safe-mode",
        "--disable-slash-commands",
        "--strict-mcp-config",
        "--no-session-persistence",
    ):
        assert flag in args
    assert args[args.index("--permission-prompts") + 1] == "none"
    assert args[args.index("--system-prompt") + 1] == "Be brief."
    assert args[args.index("--model") + 1] == settings.claude_code_model
    assert Path(fake_claude.last_call["cwd"]).resolve() == claude_code.CWD.resolve()


@pytest.mark.asyncio
async def test_api_keys_in_the_server_environment_never_reach_the_cli(fake_claude, monkeypatch):
    # Either key would make `claude` bill the API instead of the subscription.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("JWT_SECRET_KEY", "server-secret")
    await claude_code.run(system_prompt="sys", prompt="hi")
    env = set(fake_claude.last_call["env"])
    assert not env & {"ANTHROPIC_API_KEY", "OPENAI_API_KEY", "JWT_SECRET_KEY"}
    assert {"PATH", "HOME"} <= env


@pytest.mark.asyncio
async def test_a_run_billed_to_an_api_key_is_refused(fake_claude):
    fake_claude.mode("api_key")
    with pytest.raises(ClaudeCodeError, match="ANTHROPIC_API_KEY"):
        await claude_code.run(system_prompt="sys", prompt="hi")


@pytest.mark.asyncio
async def test_a_run_that_was_given_tools_is_refused(fake_claude):
    fake_claude.mode("tools")
    with pytest.raises(ClaudeCodeError, match="Bash"):
        await claude_code.run(system_prompt="sys", prompt="hi")


@pytest.mark.asyncio
async def test_the_clis_own_error_message_reaches_the_user(fake_claude):
    fake_claude.mode("limit")
    with pytest.raises(ClaudeCodeError, match="usage limit reached"):
        await claude_code.run(system_prompt="sys", prompt="hi")


@pytest.mark.asyncio
async def test_a_crash_without_a_result_reports_stderr(fake_claude):
    fake_claude.mode("crash")
    with pytest.raises(ClaudeCodeError, match="something broke"):
        await claude_code.run(system_prompt="sys", prompt="hi")


@pytest.mark.asyncio
async def test_a_stalled_run_is_killed_with_its_whole_process_group(fake_claude, monkeypatch):
    monkeypatch.setattr(settings, "claude_code_timeout_seconds", 1.0)
    fake_claude.mode("hang")
    with pytest.raises(ClaudeCodeError, match="did not finish"):
        await claude_code.run(system_prompt="sys", prompt="hi")
    child = fake_claude.child_pid()
    assert child is not None
    assert _is_dead(child)


@pytest.mark.asyncio
async def test_cancelling_a_run_kills_its_whole_process_group(fake_claude):
    fake_claude.mode("hang")
    task = asyncio.create_task(claude_code.run(system_prompt="sys", prompt="hi"))
    for _ in range(100):
        if fake_claude.child_pid() is not None:
            break
        await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    child = fake_claude.child_pid()
    assert child is not None
    assert _is_dead(child)


@pytest.mark.asyncio
async def test_nothing_runs_outside_a_local_agent_session(fake_claude, monkeypatch):
    # A hosted deployment must never spawn `claude`, whatever model id a
    # request names.
    monkeypatch.setattr(settings, "agent_backend", None)
    with pytest.raises(ClaudeCodeError, match="start-local"):
        await claude_code.run(system_prompt="sys", prompt="hi")
    assert not (fake_claude.directory / "last_call.json").exists()


# --- routing in core/llm.py -------------------------------------------------


class NameSchema(BaseModel):
    name: str


@pytest.fixture
def no_openai(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("an OpenAI client was constructed in a Claude Code session")

    monkeypatch.setattr(llm, "AsyncOpenAI", refuse)


@pytest.mark.asyncio
async def test_structured_calls_run_on_claude_code_with_no_openai_key(fake_claude, no_openai):
    fake_claude.structured({"name": "Cryo-EM build or buy"})
    parsed = await parse_structured(
        caller="test",
        model="gpt-6-astra",
        api_key="",
        system_prompt="Name the case.",
        user_content="A case about cryo-EM.",
        schema=NameSchema,
        web_search_enabled=True,
    )
    assert parsed == NameSchema(name="Cryo-EM build or buy")
    args = fake_claude.last_call["args"]
    assert json.loads(args[args.index("--json-schema") + 1]) == NameSchema.model_json_schema()


@pytest.mark.asyncio
async def test_structured_output_that_breaks_the_schema_is_an_error(fake_claude, no_openai):
    fake_claude.structured({"title": "wrong field"})
    with pytest.raises(StructuredGenerationError, match="Claude Code"):
        await parse_structured(
            caller="test",
            model="gpt-6-astra",
            api_key="",
            system_prompt="Name the case.",
            user_content="A case.",
            schema=NameSchema,
        )


@pytest.mark.asyncio
async def test_structured_calls_with_pdfs_say_so_instead_of_dropping_them(fake_claude, no_openai):
    with pytest.raises(StructuredGenerationError, match="PDF"):
        await parse_structured(
            caller="test",
            model="gpt-6-astra",
            api_key="",
            system_prompt="Name the case.",
            user_content=[
                {"type": "input_text", "text": "A case."},
                {"type": "input_file", "filename": "a.pdf", "file_data": "data:,"},
            ],
            schema=NameSchema,
        )
    assert not (fake_claude.directory / "last_call.json").exists()


async def _answer(**overrides) -> list[str]:
    kwargs = {
        "caller": "test",
        "model": get_model_config("claude-code"),
        "api_key": "",
        "instructions": "Answer.",
        "cached_prefix": "BRIEF ",
        "user_prompt": "QUESTION",
        "pdf_documents": [],
        "web_search_enabled": False,
        "verbosity": "low",
    } | overrides
    stream = await stream_text(**kwargs)
    return [chunk.text async for chunk in stream if isinstance(chunk, TextChunk)]


@pytest.mark.asyncio
async def test_an_answer_is_the_whole_prompt_answered_once(fake_claude, no_openai):
    assert await _answer() == ["echo:BRIEF QUESTION"]
    args = fake_claude.last_call["args"]
    assert args[args.index("--system-prompt") + 1].startswith("Answer.")


@pytest.mark.asyncio
async def test_an_answer_says_what_it_could_not_use(fake_claude, no_openai):
    chunks = await _answer(
        web_search_enabled=True,
        pdf_documents=[PdfAttachment(filename="paper.pdf", data_uri="data:,")],
    )
    notice, answer = chunks
    assert "web search" in notice
    assert "1 attached PDF" in notice
    assert answer == "echo:BRIEF QUESTION"


def test_claude_code_needs_no_api_key(fake_claude):
    assert llm.resolve_api_key(get_model_config("claude-code"), "", "") == ""
