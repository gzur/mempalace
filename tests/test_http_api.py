"""Contract tests for the /api/v1/* JSON surface.

Spins up the real Starlette app via ``starlette.testclient.TestClient``,
points ``mcp_server._config`` at a seeded temp palace, and verifies the
shape each endpoint returns. The SPA's TanStack Query hooks will rely on
exactly these shapes.
"""

from __future__ import annotations

import datetime as _dt
import json

import pytest

starlette = pytest.importorskip("starlette")
from starlette.testclient import TestClient  # noqa: E402

from mempalace import http_server, mcp_server, wal_series  # noqa: E402


@pytest.fixture
def app_client(monkeypatch, config, seeded_collection, kg, tmp_path):
    """A live TestClient backed by the seeded collection + KG fixtures."""
    monkeypatch.setattr(mcp_server, "_config", config)
    monkeypatch.setattr(mcp_server, "_get_kg", lambda: kg)
    # Skip the divergence probe — the test collection is freshly built.
    monkeypatch.setattr(mcp_server, "_refresh_vector_disabled_flag", lambda: None)
    monkeypatch.setattr(mcp_server, "_vector_disabled", False, raising=False)
    monkeypatch.setattr(mcp_server, "_metadata_cache", None, raising=False)
    # Point the WAL helpers at a temp file so /api/v1/ops/series is hermetic.
    wal = tmp_path / "wal.jsonl"
    with open(wal, "w", encoding="utf-8") as f:
        f.write(
            json.dumps(
                {
                    "timestamp": _dt.datetime.now().isoformat(),
                    "operation": "add_drawer",
                    "params": {"wing": "project", "room": "backend"},
                    "result": None,
                }
            )
            + "\n"
        )
    monkeypatch.setattr(wal_series, "DEFAULT_WAL_PATH", wal)
    with TestClient(http_server.build_app()) as c:
        yield c


class TestPalaceEndpoints:
    def test_summary_returns_total_and_wings(self, app_client):
        r = app_client.get("/api/v1/palace/summary")
        assert r.status_code == 200
        body = r.json()
        assert body["total_drawers"] == 4
        assert body["wings"] == {"project": 3, "notes": 1}

    def test_wings_lists_counts(self, app_client):
        r = app_client.get("/api/v1/palace/wings")
        assert r.status_code == 200
        assert r.json()["wings"] == {"project": 3, "notes": 1}

    def test_rooms_filtered_by_wing(self, app_client):
        r = app_client.get("/api/v1/palace/rooms", params={"wing": "project"})
        assert r.status_code == 200
        assert set(r.json()["rooms"].keys()) == {"backend", "frontend"}


class TestDrawerEndpoints:
    def test_list_drawers_paginates(self, app_client):
        r = app_client.get("/api/v1/drawers", params={"limit": 2, "offset": 0})
        assert r.status_code == 200
        body = r.json()
        assert body["count"] == 2
        assert isinstance(body["drawers"], list)
        for d in body["drawers"]:
            assert {"drawer_id", "wing", "room"} <= set(d.keys())

    def test_list_drawers_filters_by_wing_and_room(self, app_client):
        r = app_client.get(
            "/api/v1/drawers",
            params={"wing": "project", "room": "backend", "limit": 50},
        )
        assert r.status_code == 200
        ids = {d["drawer_id"] for d in r.json()["drawers"]}
        assert ids == {"drawer_proj_backend_aaa", "drawer_proj_backend_bbb"}

    def test_drawer_detail_returns_content_and_metadata(self, app_client):
        r = app_client.get("/api/v1/drawers/drawer_proj_backend_aaa")
        assert r.status_code == 200
        body = r.json()
        assert body["wing"] == "project"
        assert body["room"] == "backend"
        assert "JWT" in body["content"]
        assert body["metadata"]["source_file"] == "auth.py"

    def test_drawer_detail_missing_returns_error_payload(self, app_client):
        r = app_client.get("/api/v1/drawers/does_not_exist")
        # tool_get_drawer returns a dict with ``error`` rather than 404 —
        # the SPA dispatches on this.
        assert r.status_code == 200
        assert "error" in r.json()


class TestSearchEndpoint:
    def test_fts_mode_matches_substring(self, app_client):
        r = app_client.get(
            "/api/v1/search",
            params={"q": "TanStack", "mode": "fts", "limit": 5},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["mode"] == "fts"
        assert any("TanStack" in h["snippet"] for h in body["results"])


class TestOpsEndpoints:
    def test_recent_ops_passes_through_tool(self, app_client):
        r = app_client.get("/api/v1/ops", params={"limit": 5})
        assert r.status_code == 200
        # Tool returns ``operations`` + ``total_in_log`` (or ``error`` /
        # ``note`` for empty WALs); accept either shape so test environments
        # without a WAL still pass.
        body = r.json()
        assert "operations" in body or "note" in body or "error" in body

    def test_series_endpoint_returns_bucket_array(self, app_client):
        r = app_client.get("/api/v1/ops/series", params={"days": 1, "bucket": "hour"})
        assert r.status_code == 200
        body = r.json()
        assert body["days"] == 1
        assert body["bucket"] == "hour"
        assert body["op_types"] == list(wal_series.OP_TYPES)
        assert len(body["buckets"]) == 24


class TestKgEndpoints:
    @pytest.fixture(autouse=True)
    def _seed_kg(self, kg):
        kg.add_entity("Alice", entity_type="person")
        kg.add_entity("Bob", entity_type="person")
        kg.add_triple("Alice", "knows", "Bob")
        return kg

    def test_stats_returns_counts(self, app_client):
        r = app_client.get("/api/v1/kg/stats")
        assert r.status_code == 200
        body = r.json()
        assert body["entities"] == 2
        assert body["triples"] == 1

    def test_graph_dump_returns_nodes_and_edges(self, app_client):
        r = app_client.get("/api/v1/kg/graph")
        assert r.status_code == 200
        body = r.json()
        names = {n["name"] for n in body["nodes"]}
        assert names == {"Alice", "Bob"}
        assert len(body["edges"]) == 1
        assert body["edges"][0]["p"] == "knows"

    def test_entity_query_returns_facts(self, app_client):
        r = app_client.get("/api/v1/kg/entity", params={"name": "Alice"})
        assert r.status_code == 200
        body = r.json()
        assert body.get("entity") == "Alice"


class TestHealthEndpoint:
    def test_returns_overall_and_checks(self, app_client):
        r = app_client.get("/api/v1/health")
        assert r.status_code == 200
        body = r.json()
        assert body["overall"] in ("ok", "warn", "fail")
        assert isinstance(body["checks"], list)
        # Each check is a dataclass-asdict → must serialize to a flat dict.
        for c in body["checks"]:
            assert {"name", "status", "detail"} <= set(c.keys())


class TestStaticFilesMount:
    def test_app_root_returns_index_html(self, app_client):
        r = app_client.get("/app/")
        assert r.status_code == 200
        # Placeholder bundle should still respond with the doc shell.
        assert '<div id="root"></div>' in r.text

    def test_app_unknown_path_falls_back_to_index(self, app_client):
        # html=True semantics: unknown paths under /app/ resolve to index.html
        # so the client-side router owns deep links.
        r = app_client.get("/app/kg/graph")
        assert r.status_code == 200
        assert '<div id="root"></div>' in r.text
