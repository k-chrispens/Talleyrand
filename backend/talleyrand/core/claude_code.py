"""
Model calls through the operator's own signed-in Claude Code (`claude -p`).

Used only in a local session (settings.agent_backend == "claude_code"): each
call is one `claude -p` process that runs on the operator's Claude
subscription instead of an API key. The process is made as close to plain
inference as the CLI allows:

- no tools, no MCP servers, no skills, and none of the operator's own settings,
  hooks, plugins or CLAUDE.md (--safe-mode), so nothing but Talleyrand's prompt
  shapes the answer and nothing in a case can make it act;
- a fixed empty working directory, so no project context attaches;
- an environment of a handful of variables, never the server's own: an
  inherited ANTHROPIC_API_KEY would silently bill the run to the API.

The CLI's first event reports what the run actually got, and a run that got
tools or API-key auth is refused rather than trusted. The CLI's contract was
checked against Claude Code 2.1.280 on 2026-09-23 (see PLAN.md).
"""

import asyncio
import contextlib
import json
import logging
import os
import signal
from pathlib import Path
from typing import Any

from talleyrand.core.config import settings

logger = logging.getLogger(__name__)

# Empty on purpose: nothing in it may attach to a run.
CWD = Path.home() / ".local" / "share" / "talleyrand" / "claude-cwd"

# CLAUDE_CODE_OAUTH_TOKEN is the `claude setup-token` fallback for machines
# where the keychain is unavailable; it is still subscription auth.
ENV_ALLOWLIST = ("PATH", "HOME", "USER", "LOGNAME", "LANG", "TMPDIR", "CLAUDE_CODE_OAUTH_TOKEN")

# The one tool a run may have: Claude Code's own vehicle for --json-schema output.
ALLOWED_TOOLS = {"StructuredOutput"}

KILL_GRACE_SECONDS = 5.0

_slots = asyncio.Semaphore(settings.claude_code_concurrency)


class ClaudeCodeError(Exception):
    """A Claude Code call failed; str(exc) is a user-facing message."""


async def run(*, system_prompt: str, prompt: str, json_schema: dict | None = None) -> dict:
    """
    Run one `claude -p` call and return its terminal `result` event.

    The prompt goes over stdin, never through a shell. Timeouts and
    cancellation (a deleted question, a forced retry, shutdown) kill the whole
    process group before the exception propagates.
    """
    if settings.agent_backend != "claude_code":
        raise ClaudeCodeError(
            "Claude Code is only available in a local session. Start Talleyrand with "
            "./start-local.sh to use it."
        )
    args = [
        settings.claude_code_executable,
        "-p",
        "--safe-mode",
        "--disable-slash-commands",
        "--tools",
        "",
        "--strict-mcp-config",
        "--permission-prompts",
        "none",
        "--no-session-persistence",
        "--output-format",
        "stream-json",
        "--verbose",
        "--model",
        settings.claude_code_model,
        "--system-prompt",
        system_prompt,
    ]
    if json_schema is not None:
        args += ["--json-schema", json.dumps(json_schema)]

    CWD.mkdir(parents=True, exist_ok=True)
    env = {name: os.environ[name] for name in ENV_ALLOWLIST if name in os.environ}

    async with _slots:
        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=CWD,
                env=env,
                start_new_session=True,
            )
        except FileNotFoundError as e:
            raise ClaudeCodeError(
                f"`{settings.claude_code_executable}` was not found. Install Claude Code and "
                "sign in with `claude auth login`."
            ) from e
        try:
            # ponytail: communicate() buffers the whole output (the answer
            # appears twice in stream-json); fine at answer sizes.
            async with asyncio.timeout(settings.claude_code_timeout_seconds):
                stdout, stderr = await proc.communicate(prompt.encode())
        except TimeoutError as e:
            await _kill(proc)
            raise ClaudeCodeError(
                f"Claude Code did not finish within {settings.claude_code_timeout_seconds:.0f} "
                "seconds."
            ) from e
        except BaseException:
            await _kill(proc)
            raise

    return _terminal_result(stdout, stderr, proc.returncode)


def _terminal_result(stdout: bytes, stderr: bytes, returncode: int | None) -> dict[str, Any]:
    """Check the run's init event, then return its result event or raise."""
    init: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "system" and event.get("subtype") == "init":
            init = event
        elif event.get("type") == "result":
            result = event

    if init is not None:
        auth = init.get("apiKeySource")
        if auth != "none":
            raise ClaudeCodeError(
                f"Claude Code authenticated with {auth} instead of your Claude subscription; "
                "refusing to run on API billing. Unset it and sign in with `claude auth login`."
            )
        extra_tools = set(init.get("tools") or []) - ALLOWED_TOOLS
        if extra_tools or init.get("mcp_servers"):
            names = ", ".join(sorted(extra_tools) + [str(s) for s in init.get("mcp_servers", [])])
            raise ClaudeCodeError(f"Claude Code started with tools enabled ({names}); refusing.")

    if result is None:
        detail = stderr.decode(errors="replace").strip()[-500:] or f"exit code {returncode}"
        raise ClaudeCodeError(f"Claude Code exited without an answer: {detail}")
    if result.get("is_error") or result.get("subtype") != "success":
        raise ClaudeCodeError(str(result.get("result") or result.get("subtype") or "failed"))
    return result


async def _kill(proc: asyncio.subprocess.Process) -> None:
    """Terminate the run's process group, escalating to SIGKILL, and reap it."""
    try:
        os.killpg(proc.pid, signal.SIGTERM)
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(proc.wait(), KILL_GRACE_SECONDS)
        # Whatever is still in the group: a stuck CLI, or a child it left behind.
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    await proc.wait()
