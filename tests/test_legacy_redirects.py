"""Verify the /ui/* → /app/* permanent-redirects added by Phase 6.

The legacy server-rendered UI is gone, but external bookmarks, beads
links, and prior screenshots may still reference /ui/. Every such URL
must 301 to its closest /app/ equivalent so nothing fails on the user.
"""

from __future__ import annotations

import pytest

starlette = pytest.importorskip("starlette")
from starlette.testclient import TestClient  # noqa: E402

from mempalace.http_server import _legacy_to_app, build_app  # noqa: E402


@pytest.fixture
def client():
    with TestClient(build_app()) as c:
        yield c


class TestLegacyMapper:
    def test_root(self):
        assert _legacy_to_app("", "") == "/app/"

    def test_wing(self):
        assert _legacy_to_app("wing/career", "") == "/app/wings/career"

    def test_wing_room(self):
        assert (
            _legacy_to_app("wing/career/negotiations", "")
            == "/app/wings/career/negotiations"
        )

    def test_drawer_with_slashes_in_id(self):
        assert (
            _legacy_to_app("drawer/drawer_proj_a_b_c", "")
            == "/app/drawers/drawer_proj_a_b_c"
        )

    def test_kg_entity_consumes_name_param(self):
        # name= becomes a path segment; nothing else in the query.
        assert _legacy_to_app("kg/entity", "name=Alice") == "/app/kg/Alice"

    def test_kg_entity_preserves_unrelated_params(self):
        out = _legacy_to_app("kg/entity", "name=Alice&direction=both")
        assert out == "/app/kg/Alice?direction=both"

    def test_kg_graph_passes_query_through(self):
        # /app/kg ignores legacy ``hops`` silently; preserving the query
        # is cheaper than parsing+stripping it.
        assert _legacy_to_app("kg/graph", "hops=2") == "/app/kg?hops=2"

    def test_search_keeps_query(self):
        assert (
            _legacy_to_app("search", "q=mempalace&wing=self")
            == "/app/search?q=mempalace&wing=self"
        )

    def test_recent_routes_to_ops(self):
        assert _legacy_to_app("recent", "limit=50") == "/app/ops?limit=50"

    def test_operations_routes_to_ops(self):
        assert (
            _legacy_to_app("operations", "days=7&bucket=hour")
            == "/app/ops?days=7&bucket=hour"
        )

    def test_health(self):
        assert _legacy_to_app("health", "") == "/app/health"

    def test_unknown_falls_back_to_app_root(self):
        assert _legacy_to_app("totally/unknown/leaf", "") == "/app/"


class TestLegacyRoutes:
    def test_slash_redirects_to_app(self, client):
        r = client.get("/", follow_redirects=False)
        assert r.status_code == 307
        assert r.headers["location"] == "/app/"

    def test_ui_root_redirects(self, client):
        r = client.get("/ui/", follow_redirects=False)
        assert r.status_code == 301
        assert r.headers["location"] == "/app/"

    def test_ui_drawer_redirects(self, client):
        r = client.get(
            "/ui/drawer/drawer_proj_backend_aaa", follow_redirects=False
        )
        assert r.status_code == 301
        assert r.headers["location"] == "/app/drawers/drawer_proj_backend_aaa"

    def test_ui_kg_entity_redirects(self, client):
        r = client.get("/ui/kg/entity?name=Alice", follow_redirects=False)
        assert r.status_code == 301
        assert r.headers["location"] == "/app/kg/Alice"
