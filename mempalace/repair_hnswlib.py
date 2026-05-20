"""Rebuild chromadb HNSW segments directly via hnswlib.

Bypass chromadb's queue-based write pipeline. ``repair --mode from-sqlite``
upserts through chromadb's collection API; rows are queued in
``embeddings_queue`` and flushed lazily to HNSW via the segment's compactor.
With the daemon down (as is required during repair) the compactor never
runs to completion, so ~50k rows make it to disk and the remaining 41k
sit in the queue forever (self-ey7).

This module owns one operation: re-embed every document recorded in sqlite,
build a fresh hnswlib persistent index in the chromadb on-disk format, and
update ``max_seq_id`` so chromadb treats the queue as fully processed.
"""

from __future__ import annotations

import logging
import os
import pickle
import shutil
import sqlite3
import time
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class RebuildResult:
    collection_name: str
    vector_segment_id: str
    rows_indexed: int
    target_seq_id: int
    backup_path: Optional[str]
    elapsed_seconds: float


def _vector_segment_id(conn: sqlite3.Connection, collection_name: str) -> str:
    row = conn.execute(
        """
        SELECT s.id FROM segments s
        JOIN collections c ON s.collection = c.id
        WHERE c.name = ? AND s.scope = 'VECTOR'
        """,
        (collection_name,),
    ).fetchone()
    if not row:
        raise RuntimeError(f"No VECTOR segment for collection {collection_name!r}")
    return row[0]


def _metadata_segment_max_seq_id(conn: sqlite3.Connection, collection_name: str) -> int:
    row = conn.execute(
        """
        SELECT m.seq_id FROM segments s
        JOIN collections c ON s.collection = c.id
        LEFT JOIN max_seq_id m ON m.segment_id = s.id
        WHERE c.name = ? AND s.scope = 'METADATA'
        """,
        (collection_name,),
    ).fetchone()
    if not row or row[0] is None:
        return 0
    return int(row[0])


def _read_documents(conn: sqlite3.Connection, collection_name: str):
    """Yield (drawer_id, document_text) for every row in the collection.

    Joins ``embeddings`` (row identity) with ``embedding_metadata``
    (the document text under key='chroma:document'). Ordered by
    ``e.id`` so labels are deterministic across rebuilds — see
    label assignment below.
    """
    cur = conn.execute(
        """
        SELECT e.embedding_id, em.string_value
        FROM embeddings e
        JOIN embedding_metadata em ON em.id = e.id
        JOIN segments s ON e.segment_id = s.id
        JOIN collections c ON s.collection = c.id
        WHERE c.name = ? AND em.key = 'chroma:document'
        ORDER BY e.id ASC
        """,
        (collection_name,),
    )
    for emb_id, doc in cur:
        yield emb_id, doc or ""


def _build_persistent_data(
    dimensionality: int,
    id_to_label: dict,
    label_to_id: dict,
    target_seq_id: int,
):
    """Construct the chromadb PersistentData object that lives at
    ``<segment_dir>/index_metadata.pickle``.

    Importing the class lazily lets the rebuild module load even if
    chromadb's package layout shifts under us — the pickle file is
    consumed by chromadb when the daemon starts, so a mismatch would
    surface there anyway.
    """
    from chromadb.segment.impl.vector.local_persistent_hnsw import PersistentData

    pd = PersistentData(
        dimensionality=dimensionality,
        total_elements_added=len(id_to_label),
        id_to_label=id_to_label,
        label_to_id=label_to_id,
        id_to_seq_id={},
    )
    pd.max_seq_id = target_seq_id
    return pd


def rebuild_hnsw_direct(
    palace_path: str,
    collection_name: str = "mempalace_drawers",
    headroom_factor: float = 2.0,
    embedding_batch: int = 256,
    progress_every: int = 1000,
    embedding_device: Optional[str] = None,
) -> RebuildResult:
    """Re-embed every document in ``collection_name`` and rewrite its HNSW segment.

    PRECONDITION: the mempalace daemon must be stopped. Concurrent writes
    against the rebuilt segment will lose data — see ``hnsw_two_writer_race``.

    The existing segment directory is renamed to ``<segment_dir>.pre-hnswlib-<ts>``
    so a botched rebuild can be rolled back with ``mv``. ``max_seq_id`` for
    the VECTOR segment is updated to match the METADATA segment, and any
    ``embeddings_queue`` rows with ``seq_id <= target_seq_id`` are trimmed
    so chromadb does not attempt to re-process them on next boot.
    """
    import hnswlib
    import numpy as np

    from .embedding import get_embedding_function

    started = time.time()
    db_path = os.path.join(palace_path, "chroma.sqlite3")
    conn = sqlite3.connect(db_path)
    try:
        vec_seg_id = _vector_segment_id(conn, collection_name)
        target_seq_id = _metadata_segment_max_seq_id(conn, collection_name)

        rows = list(_read_documents(conn, collection_name))
        n = len(rows)
        if n == 0:
            raise RuntimeError(
                f"No documents found in collection {collection_name!r} — nothing to rebuild"
            )

        ids = [r[0] for r in rows]
        documents = [r[1] for r in rows]
    finally:
        conn.close()

    logger.info("Rebuilding %s: %d documents", collection_name, n)

    ef = get_embedding_function(device=embedding_device)
    embeddings = np.empty((n, 0), dtype=np.float32)
    dim = 0
    for i in range(0, n, embedding_batch):
        batch = documents[i : i + embedding_batch]
        vecs = np.asarray(ef(batch), dtype=np.float32)
        if dim == 0:
            dim = vecs.shape[1]
            embeddings = np.empty((n, dim), dtype=np.float32)
        embeddings[i : i + len(batch)] = vecs
        done = i + len(batch)
        if done % progress_every < embedding_batch:
            logger.info("  embedded %d/%d", done, n)

    headroom = max(int(n * headroom_factor), n + 10_000)

    segment_dir = os.path.join(palace_path, vec_seg_id)
    backup_dir: Optional[str] = None
    if os.path.isdir(segment_dir):
        backup_dir = f"{segment_dir}.pre-hnswlib-{time.strftime('%Y%m%d-%H%M%S')}"
        shutil.move(segment_dir, backup_dir)
        logger.info("Archived existing segment to %s", backup_dir)
    os.makedirs(segment_dir, exist_ok=True)

    index = hnswlib.Index(space="l2", dim=dim)
    index.init_index(
        max_elements=headroom,
        ef_construction=100,
        M=16,
        is_persistent_index=True,
        persistence_location=segment_dir,
    )
    index.set_ef(100)

    labels = list(range(1, n + 1))
    id_to_label = dict(zip(ids, labels))
    label_to_id = {label: emb_id for emb_id, label in id_to_label.items()}

    index.add_items(embeddings, labels)
    index.persist_dirty()

    persist = _build_persistent_data(
        dimensionality=dim,
        id_to_label=id_to_label,
        label_to_id=label_to_id,
        target_seq_id=target_seq_id,
    )
    with open(os.path.join(segment_dir, "index_metadata.pickle"), "wb") as f:
        pickle.dump(persist, f, pickle.HIGHEST_PROTOCOL)

    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT OR REPLACE INTO max_seq_id (segment_id, seq_id) VALUES (?, ?)",
            (vec_seg_id, target_seq_id),
        )
        conn.execute(
            "DELETE FROM embeddings_queue WHERE seq_id <= ?",
            (target_seq_id,),
        )
        conn.commit()
    finally:
        conn.close()

    elapsed = time.time() - started
    logger.info(
        "Rebuilt %s: %d rows indexed in %.1fs (dim=%d, seq_id=%d)",
        collection_name,
        n,
        elapsed,
        dim,
        target_seq_id,
    )

    return RebuildResult(
        collection_name=collection_name,
        vector_segment_id=vec_seg_id,
        rows_indexed=n,
        target_seq_id=target_seq_id,
        backup_path=backup_dir,
        elapsed_seconds=elapsed,
    )
