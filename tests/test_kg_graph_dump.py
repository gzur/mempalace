"""Tests for ``KnowledgeGraph.graph_dump`` — the whole-graph snapshot
used by the SPA's force-directed view at ``/app/kg/graph``.

The method is also exposed as ``/api/v1/kg/graph`` by ``http_api``.
"""

from __future__ import annotations

import pytest

from mempalace.knowledge_graph import KnowledgeGraph


@pytest.fixture
def populated_kg(tmp_path):
    kg = KnowledgeGraph(db_path=str(tmp_path / "kg.sqlite3"))
    kg.add_entity("Alice", entity_type="person")
    kg.add_entity("Bob", entity_type="person")
    kg.add_entity("Acme", entity_type="org")
    kg.add_triple("Alice", "knows", "Bob", valid_from="2020-01-01")
    kg.add_triple("Alice", "works_at", "Acme", valid_from="2018-01-01")
    kg.add_triple("Bob", "works_at", "Acme", valid_from="2019-06-01")
    yield kg
    kg.close()


class TestGraphDump:
    def test_returns_all_current_nodes_and_edges(self, populated_kg):
        dump = populated_kg.graph_dump()

        names = {n["name"] for n in dump["nodes"]}
        assert names == {"Alice", "Bob", "Acme"}
        # Three triples, all currently valid.
        assert len(dump["edges"]) == 3
        # truncated flag absent / False on small graphs.
        assert dump["truncated"] is False

    def test_nodes_carry_type_and_degree(self, populated_kg):
        dump = populated_kg.graph_dump()
        by_name = {n["name"]: n for n in dump["nodes"]}
        assert by_name["Alice"]["type"] == "person"
        assert by_name["Acme"]["type"] == "org"
        # Acme is the object of two edges, Alice is the subject of two.
        assert by_name["Acme"]["degree"] == 2
        assert by_name["Alice"]["degree"] == 2
        # Bob is in one outgoing and one incoming.
        assert by_name["Bob"]["degree"] == 2

    def test_edges_have_predicate_and_temporal_columns(self, populated_kg):
        dump = populated_kg.graph_dump()
        edge_preds = sorted(e["p"] for e in dump["edges"])
        assert edge_preds == ["knows", "works_at", "works_at"]
        for e in dump["edges"]:
            # Current facts only by default → no valid_to anywhere.
            assert e["valid_to"] is None
            assert e["current"] is True

    def test_include_expired_brings_back_invalidated_facts(self, populated_kg):
        # Invalidate Alice → Acme; she no longer works there.
        populated_kg.invalidate("Alice", "works_at", "Acme", ended="2024-01-01")

        live = populated_kg.graph_dump(include_expired=False)
        assert all(e["valid_to"] is None for e in live["edges"])
        assert {(e["p"], e["current"]) for e in live["edges"]} == {
            ("knows", True),
            ("works_at", True),
        }

        all_ = populated_kg.graph_dump(include_expired=True)
        assert len(all_["edges"]) == 3
        assert any(e["current"] is False for e in all_["edges"])

    def test_max_nodes_keeps_top_degree_subset(self, populated_kg):
        # Add one isolated low-degree edge so we can ask for a 2-node cap.
        populated_kg.add_entity("Carol", entity_type="person")
        populated_kg.add_entity("Hobby", entity_type="topic")
        populated_kg.add_triple("Carol", "likes", "Hobby")

        dump = populated_kg.graph_dump(max_nodes=2)

        assert dump["truncated"] is True
        assert len(dump["nodes"]) == 2
        kept = {n["name"] for n in dump["nodes"]}
        # The low-degree Carol/Hobby pair (degree 1 each) drops out before
        # Alice/Acme/Bob (degree 2 each).
        assert "Carol" not in kept and "Hobby" not in kept
        # Edges that reference dropped nodes are gone.
        for e in dump["edges"]:
            ids_in_dump = {n["id"] for n in dump["nodes"]}
            assert e["s"] in ids_in_dump
            assert e["o"] in ids_in_dump

    def test_empty_graph_returns_empty_dump(self, tmp_path):
        kg = KnowledgeGraph(db_path=str(tmp_path / "fresh.sqlite3"))
        try:
            dump = kg.graph_dump()
            assert dump == {"nodes": [], "edges": [], "truncated": False}
        finally:
            kg.close()
