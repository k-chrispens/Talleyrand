"""
The OpenAI-key dependency's 400 reaches the user verbatim: the kickstart modal,
the report modal and the answer view all show a 400's detail as the error text.
These tests pin that the message reads as prose and points at the fix, instead
of naming the HTTP header the browser client sets on its own.
"""

from typing import Annotated

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from talleyrand.core.config import settings
from talleyrand.features.graph.dependencies import get_auxiliary_api_key, get_openai_api_key


def _build_app() -> FastAPI:
    app = FastAPI()

    @app.post("/generate")
    async def generate(api_key: Annotated[str, Depends(get_openai_api_key)]) -> dict:
        return {"key": api_key}

    return app


def test_key_from_the_header_is_passed_through():
    client = TestClient(_build_app())
    response = client.post("/generate", headers={"X-OpenAI-API-Key": "sk-test"})
    assert response.status_code == 200
    assert response.json() == {"key": "sk-test"}


def test_missing_key_is_a_400_the_user_can_act_on():
    client = TestClient(_build_app())
    response = client.post("/generate")
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "Settings" in detail
    assert "header" not in detail.lower()
    assert "x-openai-api-key" not in detail.lower()


def test_blank_key_counts_as_missing():
    client = TestClient(_build_app())
    response = client.post("/generate", headers={"X-OpenAI-API-Key": ""})
    assert response.status_code == 400


def _build_auxiliary_app() -> FastAPI:
    app = FastAPI()

    @app.post("/suggest")
    async def suggest(api_key: Annotated[str, Depends(get_auxiliary_api_key)]) -> dict:
        return {"key": api_key}

    return app


def test_auxiliary_work_needs_the_openai_key_by_default():
    client = TestClient(_build_auxiliary_app())
    assert client.post("/suggest").status_code == 400


def test_a_claude_code_session_runs_auxiliary_work_without_an_openai_key(monkeypatch):
    monkeypatch.setattr(settings, "agent_backend", "claude_code")
    client = TestClient(_build_auxiliary_app())
    response = client.post("/suggest")
    assert response.status_code == 200
    assert response.json() == {"key": ""}
