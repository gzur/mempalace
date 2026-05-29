"""
test_knowledge_graph.py — Tests for the temporal knowledge graph.

Covers: entity CRUD, triple CRUD, temporal queries, invalidation,
timeline, stats, and edge cases (duplicate triples, ID collisions).
"""

import pytest
import sqlite3
from mempalace.knowledge_graph import KnowledgeGraph


class TestEntityOperations:
    def test_add_entity(self, kg):
        eid = kg.add_entity("Alice", entity_type="person")
        assert eid == "alice"

    def test_add_entity_normalizes_id(self, kg):
        eid = kg.add_entity("Dr. Chen", entity_type="person")
        assert eid == "dr._chen"

    def test_add_entity_upsert(self, kg):
        kg.add_entity("Alice", entity_type="person")
        kg.add_entity("Alice", entity_type="engineer")
        # Should not raise — INSERT OR REPLACE
        stats = kg.stats()
        assert stats["entities"] == 1


class TestTripleOperations:
    def test_add_triple_creates_entities(self, kg):
        tid = kg.add_triple("Alice", "knows", "Bob")
        assert tid.startswith("t_alice_knows_bob_")
        stats = kg.stats()
        assert stats["entities"] == 2  # auto-created

    def test_add_triple_with_dates(self, kg):
        tid = kg.add_triple("Max", "does", "swimming", valid_from="2025-01-01")
        assert tid.startswith("t_max_does_swimming_")

    def test_duplicate_triple_returns_existing_id(self, kg):
        tid1 = kg.add_triple("Alice", "knows", "Bob")
        tid2 = kg.add_triple("Alice", "knows", "Bob")
        assert tid1 == tid2

    def test_invalidated_triple_allows_re_add(self, kg):
        tid1 = kg.add_triple("Alice", "works_at", "Acme")
        kg.invalidate("Alice", "works_at", "Acme", ended="2025-01-01")
        tid2 = kg.add_triple("Alice", "works_at", "Acme")
        assert tid1 != tid2  # new triple since old one was closed

    def test_add_triple_rejects_inverted_interval(self, kg):
        # valid_to before valid_from would never satisfy
        # `valid_from <= as_of AND valid_to >= as_of` — silently invisible
        # to every query. Reject at write time instead.
        with pytest.raises(ValueError, match="before valid_from"):
            kg.add_triple(
                "Alice",
                "worked_at",
                "Acme",
                valid_from="2026-03-01",
                valid_to="2026-02-01",
            )

    def test_add_triple_accepts_equal_dates(self, kg):
        # Same-day intervals are valid (point-in-time facts).
        tid = kg.add_triple(
            "Alice",
            "joined",
            "Acme",
            valid_from="2026-03-15",
            valid_to="2026-03-15",
        )
        assert tid.startswith("t_alice_joined_acme_")

    def test_add_triple_allows_only_one_bound(self, kg):
        # The guard only fires when BOTH bounds are set.
        tid1 = kg.add_triple("Alice", "knows", "Bob", valid_from="2026-01-01")
        assert tid1.startswith("t_alice_knows_bob_")
        kg.invalidate("Alice", "knows", "Bob", ended="2026-02-01")
        tid2 = kg.add_triple("Alice", "knew", "Bob", valid_to="2026-03-01")
        assert tid2.startswith("t_alice_knew_bob_")


class TestQueries:
    def test_query_outgoing(self, seeded_kg):
        results = seeded_kg.query_entity("Alice", direction="outgoing")
        predicates = {r["predicate"] for r in results}
        assert "parent_of" in predicates
        assert "works_at" in predicates

    def test_query_incoming(self, seeded_kg):
        results = seeded_kg.query_entity("Max", direction="incoming")
        assert any(r["subject"] == "Alice" and r["predicate"] == "parent_of" for r in results)

    def test_query_both_directions(self, seeded_kg):
        results = seeded_kg.query_entity("Max", direction="both")
        directions = {r["direction"] for r in results}
        assert "outgoing" in directions
        assert "incoming" in directions

    def test_query_as_of_filters_expired(self, seeded_kg):
        results = seeded_kg.query_entity("Alice", as_of="2023-06-01", direction="outgoing")
        employers = [r["object"] for r in results if r["predicate"] == "works_at"]
        assert "Acme Corp" in employers
        assert "NewCo" not in employers

    def test_query_as_of_shows_current(self, seeded_kg):
        results = seeded_kg.query_entity("Alice", as_of="2025-06-01", direction="outgoing")
        employers = [r["object"] for r in results if r["predicate"] == "works_at"]
        assert "NewCo" in employers
        assert "Acme Corp" not in employers

    def test_query_relationship(self, seeded_kg):
        results = seeded_kg.query_relationship("does")
        assert len(results) == 2  # swimming + chess


class TestInvalidation:
    def test_invalidate_sets_valid_to(self, seeded_kg):
        seeded_kg.invalidate("Max", "does", "chess", ended="2026-01-01")
        results = seeded_kg.query_entity("Max", direction="outgoing")
        chess = [r for r in results if r["object"] == "chess"]
        assert len(chess) == 1
        assert chess[0]["valid_to"] == "2026-01-01"
        assert chess[0]["current"] is False


class TestTimeline:
    def test_timeline_all(self, seeded_kg):
        tl = seeded_kg.timeline()
        assert len(tl) >= 4

    def test_timeline_entity(self, seeded_kg):
        tl = seeded_kg.timeline("Max")
        subjects_and_objects = {t["subject"] for t in tl} | {t["object"] for t in tl}
        assert "Max" in subjects_and_objects

    def test_timeline_global_has_limit(self, kg):
        # Add > 100 triples
        for i in range(105):
            kg.add_triple(f"entity_{i}", "relates_to", f"entity_{i + 1}")
        tl = kg.timeline()
        assert len(tl) == 100  # LIMIT 100

    def test_timeline_entity_has_limit(self, kg):
        # Add > 100 triples all connected to a single entity
        for i in range(105):
            kg.add_triple(
                "hub", "connects_to", f"spoke_{i}", valid_from=f"2025-01-{(i % 28) + 1:02d}"
            )
        tl = kg.timeline("hub")
        assert len(tl) == 100  # LIMIT 100 on entity-filtered branch


class TestWALMode:
    def test_wal_mode_enabled(self, kg):
        conn = kg._conn()
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        conn.close()
        assert mode == "wal"


class TestStats:
    def test_stats_empty(self, kg):
        stats = kg.stats()
        assert stats["entities"] == 0
        assert stats["triples"] == 0

    def test_stats_seeded(self, seeded_kg):
        stats = seeded_kg.stats()
        assert stats["entities"] >= 4
        assert stats["triples"] == 5
        assert stats["current_facts"] == 4  # 1 expired (Acme Corp)
        assert stats["expired_facts"] == 1


class TestTemporalDateTimeCompatibility:
    def test_datetime_query_matches_legacy_date_only_fact(self, kg):
        kg.add_triple(
            "Alice",
            "ate_at",
            "Cafe",
            valid_from="2026-05-06",
            valid_to="2026-05-06",
        )

        result = kg.query_entity("Alice", as_of="2026-05-06T15:00:00Z")

        assert len(result) == 1
        assert result[0]["object"] == "Cafe"

    def test_datetime_query_before_legacy_date_only_fact_does_not_match(self, kg):
        kg.add_triple(
            "Alice",
            "ate_at",
            "Cafe",
            valid_from="2026-05-06",
            valid_to="2026-05-06",
        )

        result = kg.query_entity("Alice", as_of="2026-05-05T23:59:59Z")

        assert result == []

    def test_datetime_query_after_legacy_date_only_fact_does_not_match(self, kg):
        kg.add_triple(
            "Alice",
            "ate_at",
            "Cafe",
            valid_from="2026-05-06",
            valid_to="2026-05-06",
        )

        result = kg.query_entity("Alice", as_of="2026-05-07T00:00:00Z")

        assert result == []

    def test_rejects_timezone_offset_datetime_at_kg_layer(self, kg):
        with pytest.raises(ValueError):
            kg.add_triple(
                "Bob",
                "works_at",
                "Globex",
                valid_from="2026-05-06T20:30:00-05:00",
            )

    def test_rejects_naive_datetime_at_kg_layer(self, kg):
        with pytest.raises(ValueError):
            kg.add_triple(
                "Carol",
                "is_in",
                "NYC",
                valid_from="2026-05-07T01:23:00",
            )

    def test_rejects_space_separated_datetime_at_kg_layer(self, kg):
        with pytest.raises(ValueError):
            kg.add_triple(
                "Eve",
                "is_in",
                "London",
                valid_from="2026-05-06T15:00:00Z",
                valid_to="2026-05-06 20:00:00",
            )

    def test_date_only_valid_to_is_end_of_day_for_interval_check(self, kg):
        kg.add_triple(
            "Eve",
            "is_in",
            "London",
            valid_from="2026-05-06T15:00:00Z",
            valid_to="2026-05-06",
        )

        result = kg.query_entity("Eve", as_of="2026-05-06T20:00:00Z")

        assert len(result) == 1
        assert result[0]["object"] == "London"

    def test_rejects_interval_when_date_only_end_is_before_datetime_start(self, kg):
        with pytest.raises(
            ValueError,
            match=r"valid_to='2026-05-06'.*valid_from='2026-05-07T01:00:00Z'",
        ):
            kg.add_triple(
                "Eve",
                "is_in",
                "London",
                valid_from="2026-05-07T01:00:00Z",
                valid_to="2026-05-06",
            )

    def test_query_relationship_uses_safe_temporal_comparison(self, kg):
        kg.add_triple(
            "Alice",
            "visited",
            "Cafe",
            valid_from="2026-05-06",
            valid_to="2026-05-06",
        )

        result = kg.query_relationship("visited", as_of="2026-05-06T15:00:00Z")

        assert len(result) == 1
        assert result[0]["subject"] == "Alice"
        assert result[0]["object"] == "Cafe"

    def test_invalidate_rejects_timezone_offset_ended(self, kg):
        kg.add_triple(
            "Alice",
            "works_at",
            "Acme",
            valid_from="2026-05-06T14:00:00Z",
        )

        with pytest.raises(ValueError):
            kg.invalidate(
                "Alice",
                "works_at",
                "Acme",
                ended="2026-05-06T20:30:00-05:00",
            )


class TestKnowledgeGraphConnectionCleanup:
    def test_close_closes_connection_and_resets_handle(self, tmp_path):
        kg = KnowledgeGraph(str(tmp_path / "kg.sqlite3"))
        conn = kg._conn()

        kg.close()

        assert kg._connection is None
        with pytest.raises(sqlite3.ProgrammingError):
            conn.execute("SELECT 1")

    def test_context_manager_closes_connection(self, tmp_path):
        with KnowledgeGraph(str(tmp_path / "kg.sqlite3")) as kg:
            conn = kg._conn()
            kg.add_entity("Alice")

        assert kg._connection is None
        with pytest.raises(sqlite3.ProgrammingError):
            conn.execute("SELECT 1")


class TestMergeEntities:
    """``merge_entities`` folds duplicate-by-alias entities into one canonical
    row and rewrites every triple that referenced the duplicate."""

    @pytest.fixture
    def kg(self, tmp_path):
        kg = KnowledgeGraph(str(tmp_path / "kg.sqlite3"))
        # Two spellings of the same person, plus an unrelated entity.
        kg.add_entity("Gissur Þórhallsson", entity_type="person")
        kg.add_entity("Gissur", entity_type="person")
        kg.add_entity("VAST Iceland", entity_type="org")
        # Facts split across both aliases:
        kg.add_triple("Gissur Þórhallsson", "works_at", "VAST Iceland")
        kg.add_triple("Gissur", "evaluating", "VAST Iceland")
        kg.add_triple("VAST Iceland", "interviewed", "Gissur")
        yield kg
        kg.close()

    def test_rewrites_subject_and_object_then_drops_source(self, kg):
        result = kg.merge_entities("Gissur", "Gissur Þórhallsson")

        assert result["success"] is True
        assert result["subject_rewrites"] == 1
        assert result["object_rewrites"] == 1
        assert result["triples_rewritten"] == 2

        # Source entity is gone.
        conn = kg._conn()
        assert (
            conn.execute(
                "SELECT 1 FROM entities WHERE id = ?", (kg._entity_id("Gissur"),)
            ).fetchone()
            is None
        )
        # Target still there, and now owns all three triples.
        target_id = kg._entity_id("Gissur Þórhallsson")
        rows = conn.execute(
            "SELECT predicate, subject, object FROM triples ORDER BY predicate"
        ).fetchall()
        triples = [(r["predicate"], r["subject"], r["object"]) for r in rows]
        assert sorted(triples) == sorted(
            [
                ("works_at", target_id, kg._entity_id("VAST Iceland")),
                ("evaluating", target_id, kg._entity_id("VAST Iceland")),
                ("interviewed", kg._entity_id("VAST Iceland"), target_id),
            ]
        )

    def test_query_entity_sees_merged_facts(self, kg):
        kg.merge_entities("Gissur", "Gissur Þórhallsson")
        facts = kg.query_entity("Gissur Þórhallsson", direction="both")
        assert len(facts) == 3
        preds = sorted(f["predicate"] for f in facts)
        assert preds == ["evaluating", "interviewed", "works_at"]

    def test_merge_self_is_noop(self, kg):
        result = kg.merge_entities("Gissur", "Gissur")
        assert result["success"] is True
        assert result["triples_rewritten"] == 0
        # Both rows still exist.
        conn = kg._conn()
        assert (
            conn.execute(
                "SELECT 1 FROM entities WHERE id = ?", (kg._entity_id("Gissur"),)
            ).fetchone()
            is not None
        )

    def test_missing_source_raises(self, kg):
        with pytest.raises(ValueError, match="not found"):
            kg.merge_entities("Nobody", "Gissur Þórhallsson")

    def test_missing_target_is_created(self, kg):
        result = kg.merge_entities("Gissur", "Gissur Thorhallsson (canonical)")
        assert result["success"] is True
        conn = kg._conn()
        assert (
            conn.execute(
                "SELECT name FROM entities WHERE id = ?",
                (kg._entity_id("Gissur Thorhallsson (canonical)"),),
            ).fetchone()["name"]
            == "Gissur Thorhallsson (canonical)"
        )

    def test_empty_names_rejected(self, kg):
        with pytest.raises(ValueError):
            kg.merge_entities("", "Gissur")
        with pytest.raises(ValueError):
            kg.merge_entities("Gissur", "")

    def test_double_sided_triple_counted_once(self, tmp_path):
        # Edge case: an entity that is both subject and object of one
        # triple should only count once in ``triples_rewritten``.
        kg = KnowledgeGraph(str(tmp_path / "self_loop.sqlite3"))
        try:
            kg.add_entity("Mirror")
            kg.add_entity("Reflection")
            kg.add_triple("Mirror", "is", "Mirror")  # self-loop
            kg.add_triple("Mirror", "becomes", "Reflection")

            result = kg.merge_entities("Mirror", "Reflection")
            assert result["subject_rewrites"] == 2
            assert result["object_rewrites"] == 1
            # Self-loop touched both sides — count it once.
            assert result["triples_rewritten"] == 2
        finally:
            kg.close()


class TestTextValuedTriples:
    """Phase B: descriptive prose objects stored inline in value_text,
    rather than auto-promoted to fake entity rows."""

    @pytest.fixture
    def kg(self, tmp_path):
        kg = KnowledgeGraph(str(tmp_path / "kg.sqlite3"))
        yield kg
        kg.close()

    def test_value_text_creates_text_valued_triple(self, kg):
        kg.add_triple(
            "Gissur",
            "pattern",
            "disappears when triggered",
            value_text="disappears when triggered, bursts followed by collapse",
        )

        # No phantom entity for the long phrase — only Gissur exists.
        conn = kg._conn()
        names = {
            r["name"]
            for r in conn.execute("SELECT name FROM entities").fetchall()
        }
        assert names == {"Gissur"}

        # The triple is marked text-valued and stores the prose in value_text.
        rows = conn.execute(
            "SELECT object, value_text, value_kind FROM triples"
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["value_kind"] == "text"
        assert rows[0]["value_text"] == (
            "disappears when triggered, bursts followed by collapse"
        )
        assert rows[0]["object"] == "__text__"

    def test_long_object_auto_routes_to_text(self, kg):
        # > 80 chars, no explicit value_text → auto-routed.
        prose = "Article 3.5 allows forced sale at par value (270k) if shareholder leaves -- Gissur must not resign"
        assert len(prose) > 80
        kg.add_triple("Gissur", "constraint", prose)

        conn = kg._conn()
        rows = conn.execute(
            "SELECT object, value_kind, value_text FROM triples"
        ).fetchall()
        assert rows[0]["value_kind"] == "text"
        assert rows[0]["value_text"] == prose
        # Article 3.5 phrase did NOT get a phantom entity row.
        names = {r["name"] for r in conn.execute("SELECT name FROM entities").fetchall()}
        assert names == {"Gissur"}

    def test_short_object_stays_entity_valued(self, kg):
        kg.add_triple("Gissur", "works_at", "VAST Iceland")

        conn = kg._conn()
        rows = conn.execute("SELECT value_kind, object FROM triples").fetchall()
        assert rows[0]["value_kind"] == "entity"
        assert rows[0]["object"] == kg._entity_id("VAST Iceland")
        names = {r["name"] for r in conn.execute("SELECT name FROM entities").fetchall()}
        assert names == {"Gissur", "VAST Iceland"}

    def test_query_entity_surfaces_text_values(self, kg):
        kg.add_triple("Gissur", "works_at", "VAST Iceland")
        kg.add_triple(
            "Gissur",
            "pattern",
            "x",
            value_text="disappears when triggered, bursts followed by collapse",
        )

        facts = kg.query_entity("Gissur", direction="outgoing")
        assert len(facts) == 2
        by_pred = {f["predicate"]: f for f in facts}
        assert by_pred["works_at"]["object"] == "VAST Iceland"
        assert by_pred["works_at"]["object_kind"] == "entity"
        assert by_pred["pattern"]["object_kind"] == "text"
        assert "disappears when triggered" in by_pred["pattern"]["object"]

    def test_graph_dump_excludes_text_valued(self, kg):
        kg.add_entity("Gissur", entity_type="person")
        kg.add_entity("VAST Iceland", entity_type="org")
        kg.add_triple("Gissur", "works_at", "VAST Iceland")
        kg.add_triple(
            "Gissur",
            "pattern",
            "x",
            value_text="disappears when triggered, bursts followed by collapse",
        )

        dump = kg.graph_dump()
        # Only the entity-valued triple shows up as an edge.
        assert len(dump["edges"]) == 1
        assert dump["edges"][0]["p"] == "works_at"
        # Both nodes — Gissur and VAST — appear; no __text__ sentinel.
        names = {n["name"] for n in dump["nodes"]}
        assert "Gissur" in names
        assert "VAST Iceland" in names
        assert "__text__" not in names

    def test_dedup_works_for_text_valued(self, kg):
        prose = "disappears when triggered, bursts followed by collapse"
        a = kg.add_triple("Gissur", "pattern", "x", value_text=prose)
        b = kg.add_triple("Gissur", "pattern", "x", value_text=prose)
        assert a == b
        rows = kg._conn().execute("SELECT COUNT(*) AS c FROM triples").fetchone()
        assert rows["c"] == 1


class TestSetEntityType:
    """``set_entity_type`` mutates the ``type`` column in place. Most
    entities land in the KG as 'unknown' (auto-created via add_triple),
    and the user wants to relabel them after the fact."""

    @pytest.fixture
    def kg(self, tmp_path):
        kg = KnowledgeGraph(str(tmp_path / "kg.sqlite3"))
        kg.add_entity("RUV")  # default type=unknown
        yield kg
        kg.close()

    def test_updates_type(self, kg):
        result = kg.set_entity_type("RUV", "org")
        assert result["success"] is True
        assert result["type"] == "org"
        assert result["previous_type"] == "unknown"

        conn = kg._conn()
        row = conn.execute(
            "SELECT type FROM entities WHERE id = ?",
            (kg._entity_id("RUV"),),
        ).fetchone()
        assert row["type"] == "org"

    def test_missing_entity_raises(self, kg):
        with pytest.raises(ValueError, match="not found"):
            kg.set_entity_type("NoSuch", "person")

    def test_empty_args_rejected(self, kg):
        with pytest.raises(ValueError):
            kg.set_entity_type("", "org")
        with pytest.raises(ValueError):
            kg.set_entity_type("RUV", "")
