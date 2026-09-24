"""
Running a signed-in agent CLI (Claude Code, Hermes) as one model call.

Shared by core/claude_code.py and core/hermes.py. Each call is one process
that runs on the operator's own subscription instead of an API key, and only
in a local session (settings.local_mode): a hosted deployment never spawns a
CLI, whatever model a request names. What every backend gets from here:

- the prompt over stdin, never through a shell;
- a fixed empty working directory, so no project context attaches;
- an environment of a handful of variables, never the server's own: an
  inherited ANTHROPIC_API_KEY or OPENAI_API_KEY would silently bill the run to
  an API instead of the subscription;
- one concurrency cap across backends, since calls share a usage window;
- a timeout, and on timeout or cancellation (a deleted question, a forced
  retry, shutdown) the whole process group killed before the error propagates.
"""

import asyncio
import contextlib
import json
import os
import signal
from pathlib import Path
from typing import Any

from talleyrand.core.config import settings

# Empty on purpose: nothing in it may attach to a run.
CWD = Path.home() / ".local" / "share" / "talleyrand" / "agent-cwd"

ENV_ALLOWLIST = ("PATH", "HOME", "USER", "LOGNAME", "LANG", "TMPDIR")

KILL_GRACE_SECONDS = 5.0

_slots = asyncio.Semaphore(settings.agent_concurrency)


class AgentError(Exception):
    """An agent CLI call failed; str(exc) is a user-facing message."""

    label = "Agent"
    setup_hint = ""


async def run_cli(
    args: list[str],
    prompt: str,
    *,
    error: type[AgentError],
    env: dict[str, str] | None = None,
) -> tuple[bytes, bytes, int | None]:
    """Run one CLI call to completion; returns (stdout, stderr, exit code)."""
    if not settings.local_mode:
        raise error(
            f"{error.label} is only available in a local session. Start Talleyrand with "
            "./start-local.sh to use it."
        )
    CWD.mkdir(parents=True, exist_ok=True)
    child_env = {name: os.environ[name] for name in ENV_ALLOWLIST if name in os.environ}
    child_env |= env or {}

    async with _slots:
        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=CWD,
                env=child_env,
                start_new_session=True,
            )
        except FileNotFoundError as e:
            raise error(f"`{args[0]}` was not found. {error.setup_hint}") from e
        try:
            # ponytail: communicate() buffers the whole output; fine at answer sizes.
            async with asyncio.timeout(settings.agent_timeout_seconds):
                stdout, stderr = await proc.communicate(prompt.encode())
        except TimeoutError as e:
            await _kill(proc)
            raise error(
                f"{error.label} did not finish within {settings.agent_timeout_seconds:.0f} seconds."
            ) from e
        except BaseException:
            await _kill(proc)
            raise
    return stdout, stderr, proc.returncode


def jsonl_events(stdout: bytes) -> list[dict[str, Any]]:
    """The JSON objects in a JSONL stream; anything else on stdout is skipped."""
    events = []
    for line in stdout.splitlines():
        with contextlib.suppress(json.JSONDecodeError):
            event = json.loads(line)
            if isinstance(event, dict):
                events.append(event)
    return events


def stderr_tail(stderr: bytes, returncode: int | None) -> str:
    return stderr.decode(errors="replace").strip()[-500:] or f"exit code {returncode}"


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
