"""
Local mode lets one person run Talleyrand on their own machine with no Google
Cloud project and no sign-in. These tests pin that it drops exactly those
requirements and nothing else: a hosted deployment still refuses to start
without them, and the Claude Code backend can only be switched on locally.
"""

import pytest
from pydantic import ValidationError

from talleyrand.core.config import Settings

HOSTED_ONLY = ("JWT_SECRET_KEY", "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REDIRECT_URI")


@pytest.fixture
def bare_env(monkeypatch):
    for name in HOSTED_ONLY:
        monkeypatch.delenv(name, raising=False)


def test_local_mode_needs_no_google_project_or_signing_key(bare_env):
    settings = Settings(_env_file=None, local_mode=True)
    # Stream tickets are still signed, with a key made up for this process.
    assert len(settings.jwt_secret_key) >= 32


def test_a_hosted_deployment_still_requires_them(bare_env):
    with pytest.raises(ValidationError, match="google_client_id"):
        Settings(_env_file=None)


def test_a_hosted_deployment_still_rejects_a_short_signing_key(bare_env, monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "secret")
    monkeypatch.setenv("GOOGLE_REDIRECT_URI", "http://localhost:8000/auth/google/callback")
    monkeypatch.setenv("JWT_SECRET_KEY", "too-short")
    with pytest.raises(ValidationError, match="jwt_secret_key"):
        Settings(_env_file=None)


def test_claude_code_backend_requires_local_mode():
    with pytest.raises(ValidationError, match="local_mode"):
        Settings(_env_file=None, agent_backend="claude_code")
    assert Settings(_env_file=None, local_mode=True, agent_backend="claude_code").agent_backend
