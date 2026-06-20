"""R5/E6 — the static frontend mount (single-process demo). Wire-the-point only."""

from __future__ import annotations

from fastapi.testclient import TestClient

from relay_api.app import create_app
from relay_api.runs import RunStore


def test_root_redirects_to_prototype(client: TestClient) -> None:
    """``GET /`` redirects to the served prototype so the demo runs from one origin (E6)."""
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code in (307, 308)
    assert resp.headers["location"].endswith("/app/Relay.dc.html")


def test_static_files_served_with_mime(client: TestClient) -> None:
    """The mounted ``app/`` is served; correct MIME types by extension (R5)."""
    html = client.get("/app/Relay.dc.html")
    assert html.status_code == 200
    assert "text/html" in html.headers["content-type"]

    js = client.get("/app/support.js")
    assert js.status_code == 200
    assert "javascript" in js.headers["content-type"]


def test_root_json_when_no_app_dir(store: RunStore, monkeypatch, tmp_path) -> None:
    """With no frontend dir, ``/`` degrades to a JSON service pointer (still single process)."""
    monkeypatch.setenv("RELAY_APP_DIR", str(tmp_path / "missing"))
    app = create_app(store=store)
    client = TestClient(app)
    body = client.get("/").json()
    assert body["service"] == "relay-api"
