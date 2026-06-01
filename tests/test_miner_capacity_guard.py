"""Subprocess-writer capacity-divergence guard (bead self-rob).

When sqlite has accumulated thousands of embeddings while HNSW
``max_elements`` froze far below, ``col.upsert`` segfaults inside the
Rust core and leaves orphan sqlite rows. The HTTP daemon refuses these
writes via ``_refuse_write_if_diverged``; the CLI ``mempalace mine`` and
``mempalace mine`` (convos) used to bypass that guard by opening their
own ``PersistentClient``. They now share ``refuse_if_diverged`` from
``backends.chroma`` — these tests pin that behaviour so a regression
won't silently reintroduce the segfault.
"""

from __future__ import annotations

import os
import pickle
import sqlite3

import pytest

from mempalace.backends.chroma import refuse_if_diverged


COLLECTION = "mempalace_drawers"


# ── reuse the on-disk shape helpers from test_hnsw_capacity ───────────


def _seed_chroma_db(palace: str, sqlite_count: int, segment_id: str) -> None:
    db_path = os.path.join(palace, "chroma.sqlite3")
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(
            """
            CREATE TABLE collections (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL
            );
            CREATE TABLE collection_metadata (
                collection_id TEXT,
                key TEXT NOT NULL,
                str_value TEXT,
                int_value INTEGER,
                float_value REAL,
                bool_value INTEGER,
                PRIMARY KEY (collection_id, key)
            );
            CREATE TABLE segments (
                id TEXT PRIMARY KEY,
                collection TEXT NOT NULL,
                scope TEXT NOT NULL
            );
            CREATE TABLE embeddings (
                id INTEGER PRIMARY KEY,
                segment_id TEXT NOT NULL,
                embedding_id TEXT NOT NULL,
                seq_id BLOB NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        col_id = "col-test"
        conn.execute("INSERT INTO collections (id, name) VALUES (?, ?)", (col_id, COLLECTION))
        conn.execute(
            "INSERT INTO segments (id, collection, scope) VALUES (?, ?, 'VECTOR')",
            (segment_id, col_id),
        )
        for i in range(sqlite_count):
            conn.execute(
                """INSERT INTO embeddings (id, segment_id, embedding_id, seq_id)
                   VALUES (?, ?, ?, ?)""",
                (i + 1, segment_id, f"d-{i}", b"\x00" * 8),
            )
        conn.commit()
    finally:
        conn.close()


def _write_pickle(palace: str, segment_id: str, hnsw_count: int) -> None:
    seg_dir = os.path.join(palace, segment_id)
    os.makedirs(seg_dir, exist_ok=True)
    state = {
        "dimensionality": 384,
        "total_elements_added": hnsw_count,
        "max_seq_id": None,
        "id_to_label": {f"d-{i}": i for i in range(hnsw_count)},
        "label_to_id": {i: f"d-{i}" for i in range(hnsw_count)},
        "id_to_seq_id": {},
    }
    with open(os.path.join(seg_dir, "index_metadata.pickle"), "wb") as f:
        pickle.dump(state, f, pickle.HIGHEST_PROTOCOL)


# ── refuse_if_diverged ────────────────────────────────────────────────


def test_refuse_if_diverged_returns_none_when_balanced(tmp_path):
    _seed_chroma_db(str(tmp_path), sqlite_count=1000, segment_id="seg-ok")
    _write_pickle(str(tmp_path), "seg-ok", hnsw_count=950)
    assert refuse_if_diverged(str(tmp_path), COLLECTION, "mine") is None


def test_refuse_if_diverged_returns_payload_when_diverged(tmp_path):
    _seed_chroma_db(str(tmp_path), sqlite_count=20_000, segment_id="seg-bad")
    _write_pickle(str(tmp_path), "seg-bad", hnsw_count=2_000)

    payload = refuse_if_diverged(str(tmp_path), COLLECTION, "mine")
    assert payload is not None
    assert payload["success"] is False
    assert payload["vector_disabled"] is True
    assert "mine refused" in payload["error"]
    assert "mempalace repair" in payload["error"]
    # The probe info is forwarded so callers can render the divergence delta.
    assert payload["capacity_status"]["diverged"] is True


def test_refuse_if_diverged_logs_to_wal_when_diverged(tmp_path):
    _seed_chroma_db(str(tmp_path), sqlite_count=20_000, segment_id="seg-wal")
    _write_pickle(str(tmp_path), "seg-wal", hnsw_count=2_000)

    calls: list[tuple] = []

    def fake_wal(op, params, result):
        calls.append((op, params, result))

    refuse_if_diverged(str(tmp_path), COLLECTION, "mine_convos", wal_log=fake_wal)

    assert len(calls) == 1
    op, params, result = calls[0]
    assert op == "mine_convos_refused"
    assert "reason" in params
    assert result == {"success": False, "vector_disabled": True}


def test_refuse_if_diverged_swallows_wal_failures(tmp_path):
    _seed_chroma_db(str(tmp_path), sqlite_count=20_000, segment_id="seg-wal-err")
    _write_pickle(str(tmp_path), "seg-wal-err", hnsw_count=2_000)

    def broken_wal(op, params, result):
        raise RuntimeError("disk full")

    # A broken WAL must not convert a refusal into a crash.
    payload = refuse_if_diverged(str(tmp_path), COLLECTION, "mine", wal_log=broken_wal)
    assert payload is not None
    assert payload["vector_disabled"] is True


def test_refuse_if_diverged_skips_wal_when_balanced(tmp_path):
    _seed_chroma_db(str(tmp_path), sqlite_count=1000, segment_id="seg-clean")
    _write_pickle(str(tmp_path), "seg-clean", hnsw_count=950)

    calls: list = []
    result = refuse_if_diverged(
        str(tmp_path), COLLECTION, "mine", wal_log=lambda *a: calls.append(a)
    )
    assert result is None
    assert calls == []


# ── miner.mine refuses early on divergence ────────────────────────────


def test_mine_refuses_when_hnsw_diverged(tmp_path, monkeypatch, capsys):
    """``mempalace mine`` must not open the collection when the index is
    capacity-divergent — opening triggers chromadb's Rust-core segfault."""
    from mempalace import miner

    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    palace_dir = tmp_path / "palace"
    palace_dir.mkdir()

    # Synthesize a diverged palace.
    _seed_chroma_db(str(palace_dir), sqlite_count=20_000, segment_id="seg-mine-bad")
    _write_pickle(str(palace_dir), "seg-mine-bad", hnsw_count=2_000)

    # If the guard misfires, get_collection would be called — explode.
    def _explode(*_a, **_kw):
        raise AssertionError("get_collection must not be called when diverged")

    monkeypatch.setattr(miner, "get_collection", _explode)
    monkeypatch.setattr(miner, "get_closets_collection", _explode)
    # Minimal config so load_config doesn't choke on a bare directory.
    monkeypatch.setattr(
        miner,
        "load_config",
        lambda _p: {"wing": "test", "rooms": [{"name": "general", "description": ""}]},
    )
    monkeypatch.setattr(miner, "scan_project", lambda *_a, **_kw: [])
    # Lock context is harmless on tmp_path but we replace it to avoid
    # touching real lock state.
    monkeypatch.setattr(miner, "mine_palace_lock", _NoOpLock)

    miner.mine(str(project_dir), str(palace_dir), dry_run=False)

    captured = capsys.readouterr()
    assert "Mine refused" in captured.err
    assert "vector index is in capacity-divergence state" in captured.err


def test_mine_proceeds_when_capacity_ok(tmp_path, monkeypatch):
    """Balanced palace → guard returns None → mine continues to open the
    collection. Verifies the early-return is gated on actual divergence."""
    from mempalace import miner

    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    palace_dir = tmp_path / "palace"
    palace_dir.mkdir()

    _seed_chroma_db(str(palace_dir), sqlite_count=1000, segment_id="seg-mine-ok")
    _write_pickle(str(palace_dir), "seg-mine-ok", hnsw_count=950)

    opened: list[str] = []

    def _open(palace_path):
        opened.append(palace_path)
        return _NoOpCollection()

    monkeypatch.setattr(miner, "get_collection", _open)
    monkeypatch.setattr(miner, "get_closets_collection", _open)
    monkeypatch.setattr(
        miner,
        "load_config",
        lambda _p: {"wing": "test", "rooms": [{"name": "general", "description": ""}]},
    )
    monkeypatch.setattr(miner, "scan_project", lambda *_a, **_kw: [])
    monkeypatch.setattr(miner, "mine_palace_lock", _NoOpLock)

    miner.mine(str(project_dir), str(palace_dir), dry_run=False)

    assert opened, "guard short-circuited a healthy palace — false positive"


# ── convo_miner.mine_convos refuses early on divergence ───────────────


def test_mine_convos_refuses_when_hnsw_diverged(tmp_path, monkeypatch, capsys):
    from mempalace import convo_miner

    convo_dir = tmp_path / "convos"
    convo_dir.mkdir()
    palace_dir = tmp_path / "palace"
    palace_dir.mkdir()

    _seed_chroma_db(str(palace_dir), sqlite_count=20_000, segment_id="seg-conv-bad")
    _write_pickle(str(palace_dir), "seg-conv-bad", hnsw_count=2_000)

    def _explode(*_a, **_kw):
        raise AssertionError("get_collection must not be called when diverged")

    monkeypatch.setattr(convo_miner, "get_collection", _explode)
    monkeypatch.setattr(convo_miner, "scan_convos", lambda *_a, **_kw: [])

    convo_miner.mine_convos(str(convo_dir), str(palace_dir))

    captured = capsys.readouterr()
    assert "Mine refused" in captured.err
    assert "mine_convos" in captured.err


# ── helpers ───────────────────────────────────────────────────────────


class _NoOpLock:
    def __init__(self, *_a, **_kw):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False


class _NoOpCollection:
    """Stub matching the minimal surface mine() walks when files=[]."""

    def upsert(self, *_a, **_kw):
        return None

    def delete(self, *_a, **_kw):
        return None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
