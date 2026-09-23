"""
Local mode replaces sign-in with one fixed user, so what keeps other people
out is the network, not a token: the server binds to loopback, and a request
naming any other host is refused. That last check is what stops a web page the
user happens to visit from reaching the API through DNS rebinding and spending
their Claude subscription or reading their cases.
"""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from talleyrand.features.auth_jwt.router import router as auth_jwt_router
from talleyrand.main import apply_local_mode


def _local_app() -> FastAPI:
    app = FastAPI()
    app.include_router(auth_jwt_router)
    apply_local_mode(app, email="me@example.com")
    return app


def test_the_local_user_is_signed_in_without_a_token():
    client = TestClient(_local_app(), base_url="http://localhost:8000")
    response = client.get("/auth/me")
    assert response.status_code == 200
    assert response.json()["email"] == "me@example.com"


def test_loopback_by_ip_is_accepted():
    client = TestClient(_local_app(), base_url="http://127.0.0.1:8000")
    assert client.get("/auth/me").status_code == 200


def test_a_request_for_any_other_host_is_refused():
    client = TestClient(_local_app(), base_url="http://attacker.example")
    assert client.get("/auth/me").status_code == 400
