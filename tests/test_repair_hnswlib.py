"""Tests for ``mempalace.repair_hnswlib.rebuild_hnsw_direct``.

End-to-end exercise of the rebuild on a small palace seeded via the
public ``tool_add_drawer`` path, then verify the new segment files load
through hnswlib directly (chromadb-free) and that ``max_seq_id`` is
updated.
"""

from __future__ import annotations

import os
import pickle

import numpy as np
import pytest

from mempalace import repair_hnswlib


def _patch_mcp_server(monkeypatch, config, kg):
    from mempalace import mcp_server

    monkeypatch.setattr(mcp_server, "_config", config)
    monkeypatch.setattr(mcp_server, "_get_kg", lambda: kg)
    monkeypatch.setattr(mcp_server, "_metadata_cache", None)
    monkeypatch.setattr(mcp_server, "_vector_disabled", False, raising=False)
    monkeypatch.setattr(mcp_server, "_vector_disabled_reason", "", raising=False)
    monkeypatch.setattr(mcp_server, "_refresh_vector_disabled_flag", lambda: None)


def _seed_drawers(monkeypatch, config, kg, n: int = 12):
    """Add ``n`` distinct drawers via the public MCP write path. Returns ids."""
    from mempalace import mcp_server

    _patch_mcp_server(monkeypatch, config, kg)

    ids = []
    for i in range(n):
        result = mcp_server.tool_add_drawer(
            wing="self",
            room="topic",
            content=f"Distinct content for drawer number {i} - keyword_{i} " * 5,
        )
        assert result["success"], result
        ids.append(result["drawer_id"])
    return ids


def _segment_dir(palace_path: str, segment_uuid: str) -> str:
    return os.path.join(palace_path, segment_uuid)


class TestRebuildHnswDirect:
    def test_rebuild_round_trip_through_hnswlib(self, monkeypatch, config, palace_path, kg):
        """Seed a palace, rebuild HNSW, verify the new index supports knn_query."""
        import hnswlib

        ids = _seed_drawers(monkeypatch, config, kg, n=8)

        result = repair_hnswlib.rebuild_hnsw_direct(
            palace_path=palace_path,
            collection_name="mempalace_drawers",
        )

        assert result.rows_indexed == 8
        assert result.collection_name == "mempalace_drawers"
        assert result.vector_segment_id

        seg_dir = _segment_dir(palace_path, result.vector_segment_id)
        for name in ("data_level0.bin", "header.bin", "length.bin", "link_lists.bin"):
            assert os.path.isfile(os.path.join(seg_dir, name)), f"missing {name}"
        assert os.path.isfile(os.path.join(seg_dir, "index_metadata.pickle"))

        with open(os.path.join(seg_dir, "index_metadata.pickle"), "rb") as f:
            persist = pickle.load(f)
        assert persist.total_elements_added == 8
        assert persist.dimensionality == 384
        assert set(persist.id_to_label.keys()) == set(ids)
        assert all(isinstance(v, int) and v > 0 for v in persist.id_to_label.values())

        idx = hnswlib.Index(space="l2", dim=persist.dimensionality)
        idx.load_index(
            seg_dir,
            max_elements=persist.total_elements_added,
            is_persistent_index=True,
        )
        assert idx.get_current_count() == 8
        query = np.random.RandomState(0).rand(persist.dimensionality).astype(np.float32)
        labels, _distances = idx.knn_query(query, k=3)
        for lab in labels[0]:
            assert lab in persist.label_to_id

    def test_rebuild_updates_max_seq_id_and_trims_queue(self, monkeypatch, config, palace_path, kg):
        """``max_seq_id`` for the VECTOR segment matches the METADATA seg
        post-rebuild, and processed rows are cleared from embeddings_queue."""
        import sqlite3

        _seed_drawers(monkeypatch, config, kg, n=4)

        result = repair_hnswlib.rebuild_hnsw_direct(
            palace_path=palace_path,
            collection_name="mempalace_drawers",
        )

        conn = sqlite3.connect(os.path.join(palace_path, "chroma.sqlite3"))
        try:
            vec_seq_row = conn.execute(
                "SELECT seq_id FROM max_seq_id WHERE segment_id = ?",
                (result.vector_segment_id,),
            ).fetchone()
            meta_seq_row = conn.execute(
                """
                SELECT m.seq_id FROM segments s
                JOIN collections c ON s.collection = c.id
                JOIN max_seq_id m ON m.segment_id = s.id
                WHERE c.name = 'mempalace_drawers' AND s.scope = 'METADATA'
                """,
            ).fetchone()
            queue_lag = conn.execute(
                "SELECT COUNT(*) FROM embeddings_queue WHERE seq_id <= ?",
                (result.target_seq_id,),
            ).fetchone()[0]
        finally:
            conn.close()

        assert vec_seq_row is not None, "VECTOR segment max_seq_id row missing"
        assert meta_seq_row is not None
        assert vec_seq_row[0] == meta_seq_row[0], (
            f"VECTOR seq_id {vec_seq_row[0]} != METADATA seq_id {meta_seq_row[0]}"
        )
        assert queue_lag == 0, f"queue has {queue_lag} unprocessed rows below target"

    def test_rebuild_archives_existing_segment(self, monkeypatch, config, palace_path, kg):
        """The previous segment directory is renamed to ``.pre-hnswlib-<ts>``
        so a botched rebuild is reversible by ``mv``."""
        _seed_drawers(monkeypatch, config, kg, n=3)

        result = repair_hnswlib.rebuild_hnsw_direct(palace_path=palace_path)

        assert result.backup_path is not None
        assert os.path.isdir(result.backup_path)
        assert ".pre-hnswlib-" in os.path.basename(result.backup_path)
        assert os.path.isdir(_segment_dir(palace_path, result.vector_segment_id))

    def test_rebuild_raises_when_no_documents(self, monkeypatch, config, palace_path, kg):
        """Empty / unknown collection ⇒ structured error, no segment write."""
        _seed_drawers(monkeypatch, config, kg, n=1)

        with pytest.raises(RuntimeError, match="No VECTOR segment"):
            repair_hnswlib.rebuild_hnsw_direct(
                palace_path=palace_path,
                collection_name="mempalace_nonexistent_collection",
            )
