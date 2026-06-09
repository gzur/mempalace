#!/usr/bin/env python
"""Targeted top-up: re-upsert only the drawers missing from the HNSW and
FORCE a persist before exit.

Context (2026-06-06 incident): rebuild_drawers_hnsw.py rebuilt the drawers
vector segment but persisted only up to the last hnsw:sync_threshold boundary
(96,050) — the final sub-threshold tail (~803 rows, seq_id > 543642) stayed in
embeddings_queue and the daemon's consumer would not backfill it. This script
re-adds just those drawers and calls System.stop() so chromadb flushes the
in-memory HNSW completely (the missing step the rebuild relied on atexit for).

MUST run with the daemon DOWN — a second writer would race the daemon's
in-memory HNSW flush onto disk.
"""

from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

import chromadb
from chromadb.config import Settings

SCRIPTS_DIR = str(Path(__file__).resolve().parent)
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)
from rebuild_drawers_hnsw import COLLECTION, _iter_drawers  # noqa: E402

# The vector segment's persisted offset at the time of the incident. Rows in
# embeddings_queue above this were never flushed into the on-disk HNSW.
VECTOR_SEG_OFFSET = 543642
OP_UPSERT = 2


def main() -> None:
    palace = Path(os.path.expanduser("~/.mempalace/palace"))
    db_path = palace / "chroma.sqlite3"

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=10.0)
    conn.row_factory = sqlite3.Row

    pending = {
        r[0]
        for r in conn.execute(
            "SELECT DISTINCT id FROM embeddings_queue WHERE seq_id > ? AND operation = ?",
            (VECTOR_SEG_OFFSET, OP_UPSERT),
        )
    }
    print(f"pending upsert ids in queue: {len(pending)}")

    client = chromadb.PersistentClient(
        path=str(palace), settings=Settings(anonymized_telemetry=False)
    )
    col = client.get_collection(COLLECTION)
    print(f"chromadb pre-topup count: {col.count()}")

    # Reconstruct docs from sqlite metadata; only re-upsert the missing ids.
    # Deleted drawers (e.g. the test drawer) are absent from the metadata
    # segment, so they are naturally skipped here.
    upserted = 0
    for ids, docs, metas in _iter_drawers(conn, 500):
        sel = [(i, d, m) for i, d, m in zip(ids, docs, metas) if i in pending]
        if sel:
            col.upsert(
                ids=[x[0] for x in sel],
                documents=[x[1] for x in sel],
                metadatas=[x[2] for x in sel],
            )
            upserted += len(sel)
            print(f"  upserted {upserted}/{len(pending)}", flush=True)

    print(f"upserted missing drawers: {upserted}")
    print(f"chromadb post-topup count: {col.count()}")

    # Force a full HNSW persist. Plain process exit only flushed to the last
    # sync_threshold boundary last time; stopping the system persists segments.
    client._system.stop()
    print("system stopped — HNSW flush forced")


if __name__ == "__main__":
    main()
