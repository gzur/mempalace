"""Read-only SQLite access to ``chroma.sqlite3``.

Bypasses ``chromadb`` entirely. Used by both the web UI and the read-only MCP
tools as a workaround for the chromadb / chroma-hnswlib SIGSEGV documented in
beads ``self-mob``. Safe to call from any thread or process; opens a fresh
read-only connection per call.

Schema notes (chromadb 1.x):
    embeddings(id, segment_id, embedding_id, …)
    embedding_metadata(id, key, string_value | int_value | float_value | bool_value)
    embedding_fulltext_search(rowid, string_value) -- FTS5 trigram index
    segments(id, type, collection)
    collections(id, name, …)

The drawer corpus lives in the metadata segment of collection ``mempalace_drawers``.
Document text is stored as an ``embedding_metadata`` row with ``key='chroma:document'``.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Optional

_DRAWERS_COLLECTION = "mempalace_drawers"
_DOCUMENT_KEY = "chroma:document"


def db_path() -> Path:
    # Resolve through the live mcp_server config so tests can monkeypatch
    # ``mcp_server._config.palace_path`` and we still see the override.
    # Lazy import avoids a circular ``mcp_server`` ↔ ``palace_sql`` cycle.
    from . import mcp_server

    return Path(mcp_server._config.palace_path) / "chroma.sqlite3"


def db_conn() -> sqlite3.Connection:
    """Read-only sqlite connection to the palace DB."""
    conn = sqlite3.connect(f"file:{db_path()}?mode=ro", uri=True, timeout=5.0)
    conn.row_factory = sqlite3.Row
    return conn


def drawer_segment_id(conn: sqlite3.Connection) -> Optional[str]:
    row = conn.execute(
        "SELECT s.id FROM segments s "
        "JOIN collections c ON s.collection = c.id "
        "WHERE c.name = ? AND s.type LIKE '%metadata%' "
        "LIMIT 1",
        (_DRAWERS_COLLECTION,),
    ).fetchone()
    return row[0] if row else None


# ── aggregates ────────────────────────────────────────────────────────────


def palace_summary() -> dict:
    """Drawer total + wing/room counts."""
    with db_conn() as conn:
        seg = drawer_segment_id(conn)
        if not seg:
            return {"total": 0, "wings": {}, "rooms": {}}
        total = conn.execute(
            "SELECT count(*) FROM embeddings WHERE segment_id = ?", (seg,)
        ).fetchone()[0]
        wings = {
            (r["string_value"] or "unknown"): r["c"]
            for r in conn.execute(
                "SELECT em.string_value, count(*) AS c "
                "FROM embedding_metadata em "
                "JOIN embeddings e ON em.id = e.id "
                "WHERE e.segment_id = ? AND em.key = 'wing' "
                "GROUP BY em.string_value",
                (seg,),
            )
        }
        rooms = {
            (r["string_value"] or "unknown"): r["c"]
            for r in conn.execute(
                "SELECT em.string_value, count(*) AS c "
                "FROM embedding_metadata em "
                "JOIN embeddings e ON em.id = e.id "
                "WHERE e.segment_id = ? AND em.key = 'room' "
                "GROUP BY em.string_value",
                (seg,),
            )
        }
        return {"total": total, "wings": wings, "rooms": rooms}


def list_wings() -> dict[str, int]:
    return palace_summary()["wings"]


def list_rooms(wing: Optional[str] = None) -> dict[str, int]:
    if not wing:
        return palace_summary()["rooms"]
    with db_conn() as conn:
        seg = drawer_segment_id(conn)
        if not seg:
            return {}
        rows = conn.execute(
            "SELECT room_em.string_value AS room, count(*) AS c "
            "FROM embedding_metadata wing_em "
            "JOIN embeddings e ON e.id = wing_em.id "
            "JOIN embedding_metadata room_em "
            "    ON room_em.id = e.id AND room_em.key = 'room' "
            "WHERE e.segment_id = ? AND wing_em.key = 'wing' "
            "    AND wing_em.string_value = ? "
            "GROUP BY room_em.string_value",
            (seg, wing),
        ).fetchall()
        return {(r["room"] or "unknown"): r["c"] for r in rows}


# ── browsing ──────────────────────────────────────────────────────────────


def list_drawers(
    wing: Optional[str] = None,
    room: Optional[str] = None,
    offset: int = 0,
    limit: int = 20,
    preview_chars: int = 200,
) -> list[dict]:
    """Pageable drawer list. Optional wing/room filters."""
    with db_conn() as conn:
        seg = drawer_segment_id(conn)
        if not seg:
            return []
        clauses = ["e.segment_id = ?"]
        params: list[Any] = [seg]
        joins = (
            "LEFT JOIN embedding_metadata doc "
            "    ON doc.id = e.id AND doc.key = ? "
            "LEFT JOIN embedding_metadata wing_em "
            "    ON wing_em.id = e.id AND wing_em.key = 'wing' "
            "LEFT JOIN embedding_metadata room_em "
            "    ON room_em.id = e.id AND room_em.key = 'room' "
        )
        params.insert(0, _DOCUMENT_KEY)
        if wing:
            clauses.append("wing_em.string_value = ?")
            params.append(wing)
        if room:
            clauses.append("room_em.string_value = ?")
            params.append(room)
        sql = (
            "SELECT e.embedding_id AS drawer_id, "
            "       wing_em.string_value AS wing, "
            "       room_em.string_value AS room, "
            "       doc.string_value AS document "
            "FROM embeddings e "
            f"{joins}"
            f"WHERE {' AND '.join(clauses)} "
            "ORDER BY e.id "
            "LIMIT ? OFFSET ?"
        )
        params.extend([limit, offset])
        out: list[dict] = []
        for r in conn.execute(sql, params):
            doc = r["document"] or ""
            preview = doc[:preview_chars] + ("…" if len(doc) > preview_chars else "")
            out.append(
                {
                    "drawer_id": r["drawer_id"],
                    "wing": r["wing"] or "",
                    "room": r["room"] or "",
                    "content_preview": preview,
                }
            )
        return out


def get_drawer(drawer_id: str) -> Optional[dict]:
    """Full content + every metadata key for one drawer."""
    with db_conn() as conn:
        seg = drawer_segment_id(conn)
        if not seg:
            return None
        row = conn.execute(
            "SELECT id FROM embeddings WHERE segment_id = ? AND embedding_id = ? LIMIT 1",
            (seg, drawer_id),
        ).fetchone()
        if not row:
            return None
        row_id = row["id"]
        meta: dict[str, Any] = {}
        for r in conn.execute(
            "SELECT key, string_value, int_value, float_value, bool_value "
            "FROM embedding_metadata WHERE id = ?",
            (row_id,),
        ):
            if r["string_value"] is not None:
                meta[r["key"]] = r["string_value"]
            elif r["int_value"] is not None:
                meta[r["key"]] = r["int_value"]
            elif r["float_value"] is not None:
                meta[r["key"]] = r["float_value"]
            elif r["bool_value"] is not None:
                meta[r["key"]] = bool(r["bool_value"])
        content = meta.pop(_DOCUMENT_KEY, "")
        return {
            "drawer_id": drawer_id,
            "content": content,
            "wing": meta.get("wing", "") or "",
            "room": meta.get("room", "") or "",
            "metadata": meta,
        }


# ── search ────────────────────────────────────────────────────────────────


def fts_search(
    query: str,
    wing: Optional[str] = None,
    room: Optional[str] = None,
    limit: int = 10,
    snippet_chars: int = 320,
) -> list[dict]:
    """Trigram FTS substring search over drawer documents.

    Not semantic. Used as a crash-proof fallback while ``self-mob`` is open.
    """
    if not query.strip():
        return []
    with db_conn() as conn:
        seg = drawer_segment_id(conn)
        if not seg:
            return []
        rows = conn.execute(
            "SELECT e.embedding_id AS drawer_id, "
            "       wing_em.string_value AS wing, "
            "       room_em.string_value AS room, "
            "       fts.string_value AS document "
            "FROM embedding_fulltext_search fts "
            "JOIN embedding_metadata em "
            "    ON em.id = fts.rowid AND em.key = ? "
            "JOIN embeddings e ON e.id = fts.rowid "
            "LEFT JOIN embedding_metadata wing_em "
            "    ON wing_em.id = e.id AND wing_em.key = 'wing' "
            "LEFT JOIN embedding_metadata room_em "
            "    ON room_em.id = e.id AND room_em.key = 'room' "
            "WHERE fts.string_value MATCH ? "
            "  AND e.segment_id = ? "
            "LIMIT ?",
            (_DOCUMENT_KEY, query, seg, limit * 5),
        ).fetchall()
        hits: list[dict] = []
        for r in rows:
            w = r["wing"] or ""
            rm = r["room"] or ""
            if wing and w != wing:
                continue
            if room and rm != room:
                continue
            doc = r["document"] or ""
            snippet = doc if len(doc) <= snippet_chars else doc[:snippet_chars] + "…"
            hits.append(
                {
                    "drawer_id": r["drawer_id"],
                    "wing": w,
                    "room": rm,
                    "snippet": snippet,
                    "text": doc,
                }
            )
            if len(hits) >= limit:
                break
        return hits
