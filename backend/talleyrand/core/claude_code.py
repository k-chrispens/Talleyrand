"""
Model calls through the operator's own signed-in Claude Code (`claude -p`).

Each call runs on the operator's Claude subscription (process handling in
core/agent_cli.py), made as close to plain inference as the CLI allows: no
tools, no MCP servers, no skills, and none of the operator's own settings,
hooks, plugins or CLAUDE.md (--safe-mode), so nothing but Talleyrand's prompt
shapes the answer and nothing in a case can make it act.

The CLI's first event reports what the run actually got, and a run that got
tools or API-key auth is refused rather than trusted. The CLI's contract was
checked against Claude Code 2.1.280 on 2026-09-23 (see PLAN.md).
"""

import json
import os
from typing import Any

from talleyrand.core.agent_cli import AgentError, jsonl_events, run_cli, stderr_tail
from talleyrand.core.config import settings

# The `claude setup-token` fallback for machines where the keychain is
# unavailable; it is still subscription auth.
OAUTH_TOKEN_ENV = "CLAUDE_CODE_OAUTH_TOKEN"

# The one tool a run may have: Claude Code's own vehicle for --json-schema output.
ALLOWED_TOOLS = {"StructuredOutput"}


class ClaudeCodeError(AgentError):
    label = "Claude Code"
    setup_hint = "Install Claude Code and sign in with `claude auth login`."


async def run(*, system_prompt: str, prompt: str, json_schema: dict | None = None) -> dict:
    """Run one `claude -p` call and return its terminal `result` event."""
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
    env = {OAUTH_TOKEN_ENV: os.environ[OAUTH_TOKEN_ENV]} if OAUTH_TOKEN_ENV in os.environ else {}
    stdout, stderr, returncode = await run_cli(args, prompt, error=ClaudeCodeError, env=env)
    return _terminal_result(stdout, stderr, returncode)


def _terminal_result(stdout: bytes, stderr: bytes, returncode: int | None) -> dict[str, Any]:
    """Check the run's init event, then return its result event or raise."""
    events = jsonl_events(stdout)
    init = next(
        (e for e in events if e.get("type") == "system" and e.get("subtype") == "init"), None
    )
    result = next((e for e in reversed(events) if e.get("type") == "result"), None)

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
        raise ClaudeCodeError(
            f"Claude Code exited without an answer: {stderr_tail(stderr, returncode)}"
        )
    if result.get("is_error") or result.get("subtype") != "success":
        raise ClaudeCodeError(str(result.get("result") or result.get("subtype") or "failed"))
    return result
