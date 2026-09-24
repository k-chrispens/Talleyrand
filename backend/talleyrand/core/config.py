"""
Configuration settings for the Talleyrand backend.
Should be split in the future, so that each app registers its own settings.
"""

import secrets
from typing import Literal, Self

from pydantic import AnyUrl, EmailStr, Field, HttpUrl, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Settings configuration for the Talleyrand backend.

    Configuration is loaded from environment variables and .env file.
    Environment variables take precedence over .env file values.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # JWT Settings
    # Required (at least 32 characters) unless local_mode is on; checked in
    # _check_mode below rather than by a field constraint, so local mode can
    # leave it unset.
    jwt_secret_key: str = Field(
        default="",
        description=(
            "Secret key used for signing JWT tokens. "
            "Must be a unique random value per deployment, generated with `openssl rand -hex 32`."
        ),
    )
    jwt_algorithm: str = Field(
        default="HS256",
        description="Algorithm used for JWT encoding/decoding",
    )
    jwt_access_token_expire_minutes: int = Field(
        default=1440,
        description="Expiration time for JWT access tokens in minutes",
    )
    jwt_refresh_token_expire_days: int = Field(
        default=7,
        description="Expiration time for JWT refresh tokens in days",
    )

    # Google OAuth Settings. Required unless local_mode is on (see _check_mode).
    google_client_id: str = Field(default="", description="Google OAuth client ID")
    google_client_secret: str = Field(default="", description="Google OAuth client secret")
    google_redirect_uri: HttpUrl | None = Field(
        default=None,
        description="Redirect URI for Google OAuth callbacks",
    )
    google_token_url: HttpUrl = Field(
        default=HttpUrl("https://oauth2.googleapis.com/token"),
        description="URL to obtain Google OAuth tokens",
    )
    google_userinfo_url: HttpUrl = Field(
        default=HttpUrl("https://www.googleapis.com/oauth2/v2/userinfo"),
        description="URL to fetch user info from Google",
    )
    auth_failure_redirect: HttpUrl = Field(
        default=HttpUrl("http://localhost:3000/auth/failure"),
        description="Frontend URL to redirect after successful authentication",
    )

    # CORS Settings
    cors_origins: list[str] = Field(
        default_factory=lambda: [
            "http://localhost:3000",
        ],
        description="List of allowed CORS origins",
    )

    email_whitelist_enabled: bool = Field(
        default=False,
        description="Whether to enable email whitelist checking",
    )

    email_whitelist: list[EmailStr] = Field(
        default_factory=lambda: [
            "example@example.com",
        ],
        description="List of allowed emails",
    )

    # Cookie Settings (used for oauth_state cookie only)
    cookie_secure: bool = Field(
        default=True,
        description="Whether cookies should be marked as secure",
    )

    mongodb_url: AnyUrl = Field(
        description="MongoDB connection URL",
        default_factory=lambda: AnyUrl("mongodb://localhost"),
    )
    mongodb_database: str = Field(
        default="talleyrand",
        description="Name of the MongoDB database",
    )
    logging_level: str = Field(
        default="INFO",
        description="Logging level for the application",
    )

    query_chunk_size: int = Field(
        default=100,
        description="Chunk size for buffering the LLM responses before frontend delivery",
    )
    stream_idle_timeout_seconds: float = Field(
        default=1000.0,
        description=(
            "Max seconds a generation may wait for the next streamed token before the job is "
            "treated as stalled and failed. Bounds the idle gap (reset on every token), not the "
            "total duration, so long reasoning/web-search pauses are tolerated but a hung "
            "provider stream cannot freeze the job forever."
        ),
    )

    log_llm_requests: bool = Field(
        default=False,
        description="Log full LLM responses to console",
    )

    # Local mode: one person running Talleyrand on their own machine
    # (./start-local.sh). Sign-in is replaced by one fixed user, so the server
    # must only be reachable from this machine: bind to 127.0.0.1, and main.py
    # refuses requests naming any other host.
    local_mode: bool = Field(
        default=False,
        description="Skip Google sign-in and act as local_user_email. Never enable on a server.",
    )
    local_user_email: EmailStr = Field(
        default="local@example.com",
        description=(
            "Who the local session acts as. Cases belong to an email, so setting this to "
            "the address you signed in with before opens the cases you already have."
        ),
    )
    agent_backend: Literal["claude_code", "hermes"] | None = Field(
        default=None,
        description=(
            "The locally signed-in agent CLI that runs kickstart, suggestions, summaries, "
            "reports and naming instead of API keys. Answers run on whichever agent the "
            "question's model names. Requires local_mode."
        ),
    )
    claude_code_executable: str = Field(default="claude", description="The `claude` CLI to run")
    claude_code_model: str = Field(
        default="sonnet",
        description="Model alias passed to `claude --model` for every Claude Code call",
    )
    hermes_executable: str = Field(default="hermes", description="The `hermes` CLI to run")
    hermes_provider: str = Field(
        default="openai-codex",
        description="Hermes provider every Hermes call is pinned to (openai-codex: a ChatGPT plan)",
    )
    hermes_model: str = Field(
        default="gpt-6-astra",
        description="Model passed to `hermes chat --model` for every Hermes call",
    )
    agent_timeout_seconds: float = Field(
        default=900.0,
        gt=0,
        description="Longest one agent CLI call may run before it is killed",
    )
    agent_concurrency: int = Field(
        default=2,
        ge=1,
        description=(
            "How many agent CLI calls may run at once, across backends. They draw on "
            "subscription usage windows."
        ),
    )

    @model_validator(mode="after")
    def _check_mode(self) -> Self:
        if self.agent_backend and not self.local_mode:
            raise ValueError("agent_backend requires local_mode (start with ./start-local.sh)")
        if self.local_mode:
            # ponytail: a fresh key per process — it only signs the local user's
            # stream tickets, which a restart would drop anyway.
            if not self.jwt_secret_key:
                self.jwt_secret_key = secrets.token_hex(32)
            return self
        missing = [
            name
            for name in ("google_client_id", "google_client_secret", "google_redirect_uri")
            if not getattr(self, name)
        ]
        if missing:
            raise ValueError(f"{', '.join(missing)} must be set unless local_mode is on")
        if len(self.jwt_secret_key) < 32:
            raise ValueError(
                "jwt_secret_key must be at least 32 characters; "
                "generate one with `openssl rand -hex 32`"
            )
        return self


settings = Settings()
