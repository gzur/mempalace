"""Tests for the Starlette HTTP transport (mempalace.http_server)."""

import json

import pytest

starlette = pytest.importorskip("starlette")
from starlette.testclient import TestClient  # noqa: E402

from mempalace.http_server import build_app, current_session_id  # noqa: E402


@pytest.fixture
def client():
    with TestClient(build_app()) as c:
        yield c


class TestHealthz:
    def test_healthz_ok(self, client):
        r = client.get("/healthz")
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert "version" in body


class TestMcpDispatch:
    def test_initialize_returns_server_info(self, client):
        r = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["id"] == 1
        assert body["result"]["serverInfo"]["name"] == "mempalace"
        assert "instructions" in body["result"]

    def test_tools_list_returns_all_registered_tools(self, client):
        r = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        )
        assert r.status_code == 200
        names = [t["name"] for t in r.json()["result"]["tools"]]
        assert "mempalace_status" in names
        assert "mempalace_add_drawer" in names
        assert "mempalace_diary_write" in names

    def test_tools_call_status(self, client):
        r = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "mempalace_status", "arguments": {}},
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert body["id"] == 3
        # tools/call wraps the handler result in content[0].text
        content = body["result"]["content"]
        assert isinstance(content, list) and content
        # Result is valid JSON inside the text field
        parsed = json.loads(content[0]["text"])
        assert isinstance(parsed, dict)

    def test_unknown_method_returns_error(self, client):
        r = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 4, "method": "totally_unknown", "params": {}},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["error"]["code"] == -32601

    def test_notification_returns_204(self, client):
        r = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
        )
        assert r.status_code == 204
        assert r.content == b""

    def test_malformed_json_returns_400(self, client):
        r = client.post(
            "/mcp",
            content=b"{not valid json",
            headers={"Content-Type": "application/json"},
        )
        assert r.status_code == 400
        assert r.json()["error"]["code"] == -32700


class TestSessionIdThreading:
    def test_session_id_header_propagates_to_added_by(self, client, monkeypatch):
        """When X-Session-Id is set on the request, _default_added_by() returns mcp-<sid>."""
        from mempalace import mcp_server

        captured = {}

        def fake_handle_request(req):
            # Read the session id from the contextvar while the request is in flight.
            captured["added_by"] = mcp_server._default_added_by()
            captured["sid"] = current_session_id()
            return {"jsonrpc": "2.0", "id": req.get("id"), "result": {"ok": True}}

        monkeypatch.setattr(mcp_server, "handle_request", fake_handle_request)
        r = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 5, "method": "ping", "params": {}},
            headers={"X-Session-Id": "session-abc"},
        )
        assert r.status_code == 200
        assert captured["sid"] == "session-abc"
        assert captured["added_by"] == "mcp-session-abc"

    def test_no_session_header_falls_back_to_anonymous(self, client, monkeypatch):
        from mempalace import mcp_server

        captured = {}

        def fake_handle_request(req):
            captured["sid"] = current_session_id()
            captured["added_by"] = mcp_server._default_added_by()
            return {"jsonrpc": "2.0", "id": req.get("id"), "result": {"ok": True}}

        monkeypatch.setattr(mcp_server, "handle_request", fake_handle_request)
        r = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 6, "method": "ping", "params": {}},
        )
        assert r.status_code == 200
        assert captured["sid"] == "anonymous"
        # anonymous → fall back to plain "mcp" so existing tests/expectations hold
        assert captured["added_by"] == "mcp"


class TestStdioDefaultAddedBy:
    """When no HTTP request is in flight, _default_added_by() returns 'mcp'."""

    def test_outside_http_context(self):
        from mempalace import mcp_server

        assert mcp_server._default_added_by() == "mcp"
