"""
Local mode replaces sign-in with one fixed user, so everything that kept
strangers out now has to come from the request itself:

- the peer must be this machine: a remote client is refused whatever Host it
  claims, however the server happens to be bound;
- the Host must name this machine, which stops a DNS-rebinding page from
  talking to the API as if it were same-origin;
- a state-changing request that a browser marks as coming from another origin
  is refused. A web page can send a POST without a CORS preflight (no
  Content-Type, text/plain, a form), and CORS only hides the response, so
  without this any page the operator visits could spend their subscription or
  write to their cases.
"""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from talleyrand.core.config import settings
from talleyrand.features.auth_jwt.router import router as auth_jwt_router
from talleyrand.main import apply_local_mode

LOOPBACK = ("127.0.0.1", 50000)
FRONTEND = settings.cors_origins[0]


def _local_app() -> FastAPI:
    app = FastAPI()
    app.include_router(auth_jwt_router)

    @app.post("/act")
    async def act() -> dict:
        return {"acted": True}

    apply_local_mode(app, email="me@example.com")
    return app


def _client(base_url: str = "http://localhost:8000", peer: tuple = LOOPBACK) -> TestClient:
    return TestClient(_local_app(), base_url=base_url, client=peer)


def test_the_local_user_is_signed_in_without_a_token():
    response = _client().get("/auth/me")
    assert response.status_code == 200
    assert response.json()["email"] == "me@example.com"


def test_loopback_by_ip_is_accepted():
    assert _client("http://127.0.0.1:8000").get("/auth/me").status_code == 200


def test_ipv6_loopback_peer_is_accepted():
    assert _client(peer=("::1", 50000)).get("/auth/me").status_code == 200


def test_a_request_for_any_other_host_is_refused():
    assert _client("http://attacker.example").get("/auth/me").status_code == 400


def test_a_remote_peer_is_refused_even_when_it_claims_to_be_localhost():
    client = _client(peer=("192.168.1.99", 50000))
    assert client.get("/auth/me").status_code == 403
    assert client.post("/act").status_code == 403


def test_a_cross_site_post_is_refused():
    # What fetch(url, {method: "POST", mode: "no-cors"}) from any page sends.
    response = _client().post("/act", headers={"Origin": "https://evil.example"})
    assert response.status_code == 403


def test_the_local_frontends_own_post_is_accepted():
    response = _client().post("/act", headers={"Origin": FRONTEND})
    assert response.status_code == 200


def test_a_post_with_no_origin_is_accepted():
    # Browsers always send Origin on a cross-origin POST; a request without one
    # comes from a local tool, not a web page.
    assert _client().post("/act").status_code == 200


def test_a_cross_site_read_is_left_to_cors():
    # A GET changes nothing, and CORS already keeps its response from the page.
    response = _client().get("/auth/me", headers={"Origin": "https://evil.example"})
    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers
