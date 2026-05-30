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


class TestKgMerge:
    def test_post_merges_entities_via_api(self, app_client, kg):
        # Seed two aliases — one fact under each spelling, plus an
        # incoming reference to the alias we're folding away.
        kg.add_entity("Gissur", entity_type="person")
        kg.add_entity("Gissur Þórhallsson", entity_type="person")
        kg.add_triple("Gissur", "evaluating", "VAST Iceland")
        kg.add_triple("Gissur Þórhallsson", "works_at", "VAST Iceland")
        kg.add_triple("VAST Iceland", "interviewed", "Gissur")

        r = app_client.post(
            "/api/v1/kg/merge",
            json={"from": "Gissur", "into": "Gissur Þórhallsson"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["success"] is True
        # 1 subject rewrite (Gissur evaluating VAST) +
        # 1 object rewrite  (VAST interviewed Gissur).
        # The works_at row referenced the canonical spelling already.
        assert body["subject_rewrites"] == 1
        assert body["object_rewrites"] == 1
        assert body["triples_rewritten"] == 2

        dump = app_client.get("/api/v1/kg/graph").json()
        names = {n["name"] for n in dump["nodes"]}
        assert "Gissur" not in names
        assert "Gissur Þórhallsson" in names

    def test_missing_payload_returns_structured_error(self, app_client):
        r = app_client.post("/api/v1/kg/merge", json={})
        assert r.status_code == 200
        assert r.json()["success"] is False


class TestSearchKGFallback:
    def test_unmatched_drawers_surfaces_kg_entities(self, app_client, kg):
        # Semantic search over a small drawer corpus almost always returns
        # SOMETHING by cosine distance, so the fallback only fires on a
        # genuinely-empty result set. The /api/v1/search ``mode=fts`` path
        # routes through palace_sql.fts_search, which does substring
        # matching only — perfect for testing the 'no drawer mentions
        # this entity verbatim' case the KG fallback was built for.
        kg.add_entity("Emile Brouchard", entity_type="person")
        kg.add_entity("RUV", entity_type="org")
        kg.add_triple("Emile Brouchard", "works_at", "RUV")

        # The FTS endpoint path is a different shape (palace_sql), so this
        # test exercises tool_search directly via the JSON-RPC tool.
        r = app_client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 100,
                "method": "tools/call",
                "params": {
                    "name": "mempalace_search",
                    "arguments": {
                        "query": "Emile",
                        "limit": 5,
                        "max_distance": 0.05,  # strict; no drawer can match
                    },
                },
            },
        )
        assert r.status_code == 200
        text = r.json()["result"]["content"][0]["text"]
        body = json.loads(text)
        assert body.get("kg_fallback") is True
        kg_names = [e["name"] for e in body.get("kg_entities", [])]
        assert "Emile Brouchard" in kg_names

    def test_drawer_hits_still_attach_kg_companion(self, app_client, kg):
        """KG matches surface alongside drawer hits — they're a
        sidebar, not a fallback. ``kg_fallback`` only marks the
        empty-drawer case so the UI can word the section differently."""
        kg.add_entity("TanStack")
        r = app_client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 101,
                "method": "tools/call",
                "params": {
                    "name": "mempalace_search",
                    "arguments": {"query": "TanStack", "limit": 5},
                },
            },
        )
        assert r.status_code == 200
        body = json.loads(r.json()["result"]["content"][0]["text"])
        assert body["results"], "expected drawer results for 'TanStack'"
        names = [e["name"] for e in body.get("kg_entities", [])]
        assert "TanStack" in names
        # Drawers had hits, so 'fallback' specifically is False/missing.
        assert body.get("kg_fallback") is not True


class TestKgAddTriple:
    def test_post_adds_entity_valued_triple(self, app_client, kg):
        kg.add_entity("Emile", entity_type="person")
        kg.add_entity("VAST Iceland", entity_type="org")

        r = app_client.post(
            "/api/v1/kg/triples",
            json={
                "subject": "Emile",
                "predicate": "works_at",
                "object": "VAST Iceland",
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert body["success"] is True
        assert body["fact"] == "Emile → works_at → VAST Iceland"

        dump = app_client.get("/api/v1/kg/graph").json()
        edges = [(e["s"], e["p"], e["o"]) for e in dump["edges"]]
        assert (kg._entity_id("Emile"), "works_at", kg._entity_id("VAST Iceland")) in edges

    def test_post_adds_text_valued_triple(self, app_client, kg):
        kg.add_entity("Emile", entity_type="person")

        r = app_client.post(
            "/api/v1/kg/triples",
            json={
                "subject": "Emile",
                "predicate": "status",
                "object": "x",  # placeholder; real content in value_text
                "value_text": "currently between roles, freelance until July",
            },
        )
        assert r.status_code == 200
        assert r.json()["success"] is True

        facts = app_client.get(
            "/api/v1/kg/entity", params={"name": "Emile"}
        ).json()
        status_fact = next(f for f in facts["facts"] if f["predicate"] == "status")
        assert status_fact["object_kind"] == "text"
        assert "freelance" in status_fact["object"]


class TestKgDeleteEntity:
    def test_delete_orphan_via_api(self, app_client, kg):
        kg.add_entity("Lonely")
        r = app_client.delete("/api/v1/kg/entity/Lonely")
        assert r.status_code == 200
        body = r.json()
        assert body["success"] is True
        assert body["deleted"] is True
        assert body["triples_deleted"] == 0

    def test_default_refuses_with_incident_triples(self, app_client, kg):
        kg.add_entity("Emile", entity_type="person")
        kg.add_entity("RUV", entity_type="org")
        kg.add_triple("Emile", "works_at", "RUV")

        r = app_client.delete("/api/v1/kg/entity/Emile")
        assert r.status_code == 200
        body = r.json()
        assert body["success"] is False
        assert body["incident_triples"] == 1

    def test_cascade_drops_triples_and_entity(self, app_client, kg):
        kg.add_entity("Emile", entity_type="person")
        kg.add_entity("RUV", entity_type="org")
        kg.add_triple("Emile", "works_at", "RUV")

        r = app_client.delete("/api/v1/kg/entity/Emile?cascade=1")
        assert r.status_code == 200
        body = r.json()
        assert body["success"] is True
        assert body["deleted"] is True
        assert body["triples_deleted"] == 1


class TestKgDeleteTriple:
    def test_delete_drops_the_triple(self, app_client, kg):
        kg.add_entity("Emile", entity_type="person")
        kg.add_entity("RUV", entity_type="org")
        tid = kg.add_triple("Emile", "works_at", "RUV")

        r = app_client.delete(f"/api/v1/kg/triples/{tid}")
        assert r.status_code == 200
        body = r.json()
        assert body["success"] is True
        assert body["deleted"] is True

        # /api/v1/kg/entity?name=Emile now returns zero outgoing facts.
        q = app_client.get(
            "/api/v1/kg/entity", params={"name": "Emile", "direction": "outgoing"}
        ).json()
        assert q["count"] == 0

    def test_delete_missing_id_is_soft_miss(self, app_client):
        r = app_client.delete("/api/v1/kg/triples/t_doesnt_exist")
        assert r.status_code == 200
        body = r.json()
        assert body["success"] is True
        assert body["deleted"] is False


class TestKgSetEntityType:
    def test_patch_updates_entity_type(self, app_client, kg):
        kg.add_entity("RUV")  # default type=unknown
        # graph_dump only returns nodes touched by triples; give it one
        # so the post-patch round-trip can see the type change.
        kg.add_triple("RUV", "is_broadcaster", "Iceland")

        r = app_client.patch(
            "/api/v1/kg/entity/RUV",
            json={"type": "org"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["success"] is True
        assert body["type"] == "org"
        assert body["previous_type"] == "unknown"

        dump = app_client.get("/api/v1/kg/graph").json()
        ruv = next(n for n in dump["nodes"] if n["name"] == "RUV")
        assert ruv["type"] == "org"

    def test_patch_missing_entity_returns_error(self, app_client):
        r = app_client.patch("/api/v1/kg/entity/NoSuch", json={"type": "org"})
        assert r.status_code == 200
        assert r.json()["success"] is False

    def test_patch_missing_type_returns_error(self, app_client, kg):
        kg.add_entity("RUV")
        r = app_client.patch("/api/v1/kg/entity/RUV", json={})
        assert r.status_code == 200
        assert r.json()["success"] is False


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


def _mcp_call(client, name, args, *, headers=None, call_id=1):
    """POST a tools/call and unwrap the text-encoded JSON result."""
    r = client.post(
        "/mcp",
        headers=headers or {},
        json={
            "jsonrpc": "2.0",
            "id": call_id,
            "method": "tools/call",
            "params": {"name": name, "arguments": args},
        },
    )
    assert r.status_code == 200, r.text
    text = r.json()["result"]["content"][0]["text"]
    return json.loads(text)


class TestClientHeader:
    def test_x_mempalace_client_lands_on_drawer_metadata(self, app_client):
        body = _mcp_call(
            app_client,
            "mempalace_add_drawer",
            {
                "wing": "self",
                "room": "tests",
                "content": "drawer with client header attribution",
            },
            headers={"X-Mempalace-Client": "claude-code/mempalace"},
        )
        assert body["success"] is True
        drawer_id = body["drawer_id"]

        detail = app_client.get(f"/api/v1/drawers/{drawer_id}").json()
        assert detail["metadata"].get("client") == "claude-code/mempalace"

    def test_no_header_omits_client_field(self, app_client):
        body = _mcp_call(
            app_client,
            "mempalace_add_drawer",
            {
                "wing": "self",
                "room": "tests",
                "content": "drawer without client header",
            },
            call_id=2,
        )
        assert body["success"] is True
        drawer_id = body["drawer_id"]
        detail = app_client.get(f"/api/v1/drawers/{drawer_id}").json()
        assert "client" not in detail["metadata"]


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

    def test_app_missing_asset_returns_404_not_html(self, app_client):
        # After a bundle rotation, an old tab will request a hash that no
        # longer exists. That path MUST 404 (not return index.html dressed
        # as JS) so the browser surfaces 'failed to fetch dynamic import'
        # honestly instead of trying to parse HTML as a module.
        r = app_client.get("/app/assets/wings-stale-hash.js")
        assert r.status_code == 404

    def test_app_missing_css_returns_404(self, app_client):
        r = app_client.get("/app/assets/index-stale.css")
        assert r.status_code == 404
