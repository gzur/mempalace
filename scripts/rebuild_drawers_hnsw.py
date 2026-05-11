#!/usr/bin/env python
"""Rebuild the drawers HNSW index by re-upserting every sqlite-resident drawer.

Background: bead self-mob. The drawers HNSW segment files (2 May) drifted out
of sync with the sqlite metadata segment (66805 rows post-9gb-fix). chromadb
1.5.8's Rust core SIGSEGV-ed on count mismatch. Mitigation: moved the stale
HNSW dir aside. After that move chromadb cold-loads cleanly, but HNSW is
effectively empty for the historical corpus.

This script restores HNSW recall by streaming every drawer from sqlite and
upserting it back. chromadb re-embeds via its configured embedding function
(default: all-MiniLM-L6-v2 via sentence-transformers) and rebuilds HNSW
incrementally through the queue consumer.

Safe to run while the HTTP daemon is up — chromadb serializes writes via
sqlite, and the daemon's read tools bypass chromadb (mitigation #2 in
mempalace.palace_sql).
"""

from __future__ import annotations

import argparse
import sqlite3
import time
from pathlib import Path
from typing import Iterator

import chromadb
from chromadb.config import Settings

# Hardcoded for this palace; if the segment id ever changes we'd need to
# look it up via segments JOIN collections. Cheap, but adding another query
# obscures the script's intent — keep the constant inline.
DRAWERS_METADATA_SEGMENT = "5ad1fb38-c100-4c1c-8476-29ecbe9d37c7"
COLLECTION = "mempalace_drawers"
DOC_KEY = "chroma:document"


def _iter_drawers(
    conn: sqlite3.Connection, batch_size: int
) -> Iterator[tuple[list[str], list[str], list[dict]]]:
    """Yield (ids, documents, metadatas) batches in stable row-id order."""
    offset = 0
    while True:
        rows = conn.execute(
            "SELECT e.id AS row_id, e.embedding_id AS drawer_id "
            "FROM embeddings e "
            "WHERE e.segment_id = ? "
            "ORDER BY e.id "
            "LIMIT ? OFFSET ?",
            (DRAWERS_METADATA_SEGMENT, batch_size, offset),
        ).fetchall()
        if not rows:
            return

        ids: list[str] = []
        docs: list[str] = []
        metas: list[dict] = []
        for r in rows:
            meta: dict = {}
            doc = ""
            for mr in conn.execute(
                "SELECT key, string_value, int_value, float_value, bool_value "
                "FROM embedding_metadata WHERE id = ?",
                (r["row_id"],),
            ):
                key = mr["key"]
                if mr["string_value"] is not None:
                    if key == DOC_KEY:
                        doc = mr["string_value"]
                    else:
                        meta[key] = mr["string_value"]
                elif mr["int_value"] is not None:
                    meta[key] = mr["int_value"]
                elif mr["float_value"] is not None:
                    meta[key] = mr["float_value"]
                elif mr["bool_value"] is not None:
                    meta[key] = bool(mr["bool_value"])
            if not doc:
                # Drawer with no document content is unindexable; skip it.
                continue
            ids.append(r["drawer_id"])
            docs.append(doc)
            metas.append(meta)

        if ids:
            yield ids, docs, metas
        offset += batch_size


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--palace",
        default=str(Path.home() / ".mempalace" / "palace"),
        help="Palace directory (default: ~/.mempalace/palace)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=500,
        help="Drawers per upsert batch (default 500)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Stop after N drawers (debug only)",
    )
    args = parser.parse_args()

    palace = Path(args.palace)
    db_path = palace / "chroma.sqlite3"

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=10.0)
    conn.row_factory = sqlite3.Row
    total = conn.execute(
        "SELECT count(*) FROM embeddings WHERE segment_id = ?",
        (DRAWERS_METADATA_SEGMENT,),
    ).fetchone()[0]
    print(f"sqlite drawers: {total}")

    client = chromadb.PersistentClient(
        path=str(palace), settings=Settings(anonymized_telemetry=False)
    )
    col = client.get_collection(COLLECTION)
    pre = col.count()
    print(f"chromadb pre-rebuild count: {pre}")

    start = time.time()
    processed = 0
    upserted = 0
    skipped_no_doc = 0
    for ids, docs, metas in _iter_drawers(conn, args.batch_size):
        col.upsert(ids=ids, documents=docs, metadatas=metas)
        upserted += len(ids)
        processed += args.batch_size  # approximate; final batch may be short
        elapsed = time.time() - start
        rate = upserted / elapsed if elapsed > 0 else 0.0
        remaining = max(0, total - upserted)
        eta_s = remaining / rate if rate > 0 else 0.0
        print(
            f"  upserted {upserted}/{total} "
            f"({rate:.0f}/s, ETA {eta_s / 60:.1f}min)",
            flush=True,
        )
        if args.limit and upserted >= args.limit:
            print(f"  --limit={args.limit} reached, stopping")
            break

    skipped_no_doc = total - upserted if not args.limit else 0
    elapsed = time.time() - start
    post = col.count()
    print(f"done in {elapsed:.0f}s")
    print(f"upserted: {upserted}")
    print(f"skipped (no document body): {max(0, skipped_no_doc)}")
    print(f"chromadb post-rebuild count: {post}")


if __name__ == "__main__":
    main()
