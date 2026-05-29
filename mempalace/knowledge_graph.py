"""
knowledge_graph.py — Temporal Entity-Relationship Graph for MemPalace
=====================================================================

Real knowledge graph with:
  - Entity nodes (people, projects, tools, concepts)
  - Typed relationship edges (daughter_of, does, loves, works_on, etc.)
  - Temporal validity (valid_from → valid_to — knows WHEN facts are true)
  - Closet references (links back to the verbatim memory)

Storage: SQLite (local, no dependencies, no subscriptions)
Query: entity-first traversal with time filtering

This is what competes with Zep's temporal knowledge graph.
Zep uses Neo4j in the cloud ($25/mo+). We use SQLite locally (free).

Usage:
    from mempalace.knowledge_graph import KnowledgeGraph

    kg = KnowledgeGraph()
    kg.add_triple("Max", "child_of", "Alice", valid_from="2015-04-01")
    kg.add_triple("Max", "does", "swimming", valid_from="2025-01-01")
    kg.add_triple("Max", "loves", "chess", valid_from="2025-10-01")

    # Query: everything about Max
    kg.query_entity("Max")

    # Query: what was true about Max in January 2026?
    kg.query_entity("Max", as_of="2026-01-15")

    # Query: who is connected to Alice?
    kg.query_entity("Alice", direction="both")

    # Invalidate: Max's sports injury resolved
    kg.invalidate("Max", "has_issue", "sports_injury", ended="2026-02-15")
"""

import hashlib
import json
import os
import sqlite3
import threading
from datetime import date, datetime
from pathlib import Path
from typing import Optional
from .config import sanitize_iso_temporal


DEFAULT_KG_PATH = os.path.expanduser("~/.mempalace/knowledge_graph.sqlite3")

# A non-FK sentinel id used in ``triples.object`` for text-valued triples.
# The actual prose lives in ``triples.value_text``; this string just keeps
# the NOT NULL constraint happy without referencing a real entity row.
_TEXT_OBJECT_SENTINEL = "__text__"

# When a caller passes a long ``obj`` string to ``add_triple`` without an
# explicit ``value_text``, route it to a text-valued triple instead of
# auto-promoting it to a fake entity. Tuned to catch the descriptive
# phrases users had ('Article 3.5 allows…', ~100 chars) without
# misclassifying real multi-word entity names ('Ministry of Finance',
# 'Gissur Þórhallsson').
_AUTO_TEXT_THRESHOLD = 80


def _is_date_only_temporal(value: str) -> bool:
    return isinstance(value, str) and len(value) == 10 and value[4] == "-" and value[7] == "-"


def _temporal_start_key(value: Optional[str]) -> Optional[str]:
    """Return the comparable instant for a valid_from/as_of value."""

    if value is None:
        return None

    if _is_date_only_temporal(value):
        return f"{value}T00:00:00Z"

    return value


def _temporal_end_key(value: Optional[str]) -> Optional[str]:
    """Return the comparable instant for a valid_to value.

    Date-only valid_to values represent the whole day for backward
    compatibility with existing KG facts.
    """

    if value is None:
        return None

    if _is_date_only_temporal(value):
        return f"{value}T23:59:59Z"

    return value


def _sql_temporal_start_expr(column: str) -> str:
    """SQLite expression for comparing valid_from-style temporal values."""

    return (
        f"CASE WHEN length({column}) = 10 "
        f"AND substr({column}, 5, 1) = '-' "
        f"AND substr({column}, 8, 1) = '-' "
        f"THEN {column} || 'T00:00:00Z' ELSE {column} END"
    )


def _sql_temporal_end_expr(column: str) -> str:
    """SQLite expression for comparing valid_to-style temporal values."""

    return (
        f"CASE WHEN length({column}) = 10 "
        f"AND substr({column}, 5, 1) = '-' "
        f"AND substr({column}, 8, 1) = '-' "
        f"THEN {column} || 'T23:59:59Z' ELSE {column} END"
    )


def _temporal_filter_sql(as_of: str) -> tuple[str, list[str]]:
    """Return SQL and parameters for an as-of temporal filter.

    Date-only KG values are normalized for comparison:

    - valid_from='2026-05-06' compares as '2026-05-06T00:00:00Z'
    - valid_to='2026-05-06' compares as '2026-05-06T23:59:59Z'

    This keeps legacy date-only facts working when callers query with
    canonical UTC datetimes such as '2026-05-06T15:00:00Z'.
    """

    as_of_key = _temporal_start_key(as_of)
    valid_from_expr = _sql_temporal_start_expr("t.valid_from")
    valid_to_expr = _sql_temporal_end_expr("t.valid_to")

    return (
        f" AND (t.valid_from IS NULL OR {valid_from_expr} <= ?) "
        f"AND (t.valid_to IS NULL OR {valid_to_expr} >= ?)",
        [as_of_key, as_of_key],
    )


class KnowledgeGraph:
    def __init__(self, db_path: str = None):
        self.db_path = db_path or DEFAULT_KG_PATH
        db_parent = Path(self.db_path).parent
        db_parent.mkdir(parents=True, exist_ok=True)
        try:
            db_parent.chmod(0o700)
        except (OSError, NotImplementedError):
            pass
        self._connection = None
        self._lock = threading.Lock()
        self._init_db()

    def _init_db(self):
        conn = self._conn()
        conn.executescript("""
            PRAGMA journal_mode=WAL;

            CREATE TABLE IF NOT EXISTS entities (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                type TEXT DEFAULT 'unknown',
                properties TEXT DEFAULT '{}',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS triples (
                id TEXT PRIMARY KEY,
                subject TEXT NOT NULL,
                predicate TEXT NOT NULL,
                object TEXT NOT NULL,
                value_text TEXT,
                value_kind TEXT DEFAULT 'entity',
                valid_from TEXT,
                valid_to TEXT,
                confidence REAL DEFAULT 1.0,
                source_closet TEXT,
                source_file TEXT,
                source_drawer_id TEXT,
                adapter_name TEXT,
                extracted_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (subject) REFERENCES entities(id),
                FOREIGN KEY (object) REFERENCES entities(id)
            );

            CREATE INDEX IF NOT EXISTS idx_triples_subject ON triples(subject);
            CREATE INDEX IF NOT EXISTS idx_triples_object ON triples(object);
            CREATE INDEX IF NOT EXISTS idx_triples_predicate ON triples(predicate);
            CREATE INDEX IF NOT EXISTS idx_triples_valid ON triples(valid_from, valid_to);
        """)
        self._migrate_schema(conn)
        conn.commit()

    def _migrate_schema(self, conn):
        """Backwards-compatible schema migration for older triples tables.

        Fresh palaces get ``source_drawer_id`` / ``adapter_name`` (RFC 002 §5.5)
        directly from the canonical ``CREATE TABLE`` above, so this path is a
        no-op on new installs. It exists for palaces that were created before
        those columns were added: SQLite has no ``ADD COLUMN IF NOT EXISTS``,
        so we introspect the schema and only issue the ALTER when the column
        is missing.
        """
        existing = {row["name"] for row in conn.execute("PRAGMA table_info(triples)")}
        if "source_drawer_id" not in existing:
            conn.execute("ALTER TABLE triples ADD COLUMN source_drawer_id TEXT")
        if "adapter_name" not in existing:
            conn.execute("ALTER TABLE triples ADD COLUMN adapter_name TEXT")
        # Text-valued triple objects: descriptive prose stored inline rather
        # than auto-promoted to a fake entity row. See Phase B of the KG
        # cleanup work.
        if "value_text" not in existing:
            conn.execute("ALTER TABLE triples ADD COLUMN value_text TEXT")
        if "value_kind" not in existing:
            conn.execute(
                "ALTER TABLE triples ADD COLUMN value_kind TEXT DEFAULT 'entity'"
            )

    def _conn(self):
        if self._connection is None:
            self._connection = sqlite3.connect(self.db_path, timeout=10, check_same_thread=False)
            self._connection.execute("PRAGMA journal_mode=WAL")
            self._connection.row_factory = sqlite3.Row
        return self._connection

    def close(self):
        """Close the database connection."""
        with self._lock:
            if self._connection is not None:
                self._connection.close()
                self._connection = None

    def __enter__(self):
        """Allow KnowledgeGraph to be used as a context manager."""
        return self

    def __exit__(self, exc_type, exc, tb):
        """Close the SQLite connection when leaving a context manager block."""
        self.close()
        return False

    def _entity_id(self, name: str) -> str:
        return name.lower().replace(" ", "_").replace("'", "")

    # ── Write operations ──────────────────────────────────────────────────

    def add_entity(self, name: str, entity_type: str = "unknown", properties: dict = None):
        """Add or update an entity node."""
        eid = self._entity_id(name)
        props = json.dumps(properties or {})
        with self._lock:
            conn = self._conn()
            with conn:
                conn.execute(
                    "INSERT OR REPLACE INTO entities (id, name, type, properties) VALUES (?, ?, ?, ?)",
                    (eid, name, entity_type, props),
                )
        return eid

    def add_triple(
        self,
        subject: str,
        predicate: str,
        obj: str,
        valid_from: str = None,
        valid_to: str = None,
        confidence: float = 1.0,
        source_closet: str = None,
        source_file: str = None,
        source_drawer_id: str = None,
        adapter_name: str = None,
        value_text: str = None,
    ):
        """
        Add a relationship triple: subject → predicate → object.

        ``source_drawer_id`` and ``adapter_name`` are RFC 002 §5.5 provenance
        fields populated by adapters that advertise ``supports_kg_triples``;
        they default to ``None`` so every existing caller stays
        source-compatible.

        ``value_text`` (RFC for Phase B of the KG cleanup) lets the caller
        attach a *descriptive* object to the triple — a clause, a quote,
        a status sentence — without promoting that string to a fake
        entity. When set, ``obj`` is ignored for entity-creation
        purposes. The triple gets ``value_kind='text'`` and the prose
        lands in ``value_text``; ``object`` keeps a sentinel id to
        satisfy NOT NULL.

        If ``value_text`` is not set but ``obj`` is suspiciously long
        (> 80 chars), we auto-route it to text storage as a safety net
        against existing kg_add callers that pass clauses as objects.

        Examples:
            add_triple("Max", "child_of", "Alice", valid_from="2015-04-01")
            add_triple("Max", "does", "swimming", valid_from="2025-01-01")
            add_triple("Gissur", "pattern",
                       "disappears when triggered, bursts followed by collapse",
                       value_text="disappears when triggered, bursts followed by collapse")
        """

        valid_from = sanitize_iso_temporal(valid_from, "valid_from")
        valid_to = sanitize_iso_temporal(valid_to, "valid_to")

        # Reject inverted intervals. Use temporal comparison keys rather than
        # raw string comparison so legacy date-only values and canonical UTC
        # datetimes can safely coexist.
        if (
            valid_from is not None
            and valid_to is not None
            and _temporal_end_key(valid_to) < _temporal_start_key(valid_from)
        ):
            raise ValueError(
                f"valid_to={valid_to!r} is before valid_from={valid_from!r}; "
                "an inverted interval would be invisible to every KG query"
            )

        sub_id = self._entity_id(subject)
        pred = predicate.lower().replace(" ", "_")

        # Decide: entity-valued or text-valued object?
        is_text_value = value_text is not None or (
            isinstance(obj, str) and len(obj) > _AUTO_TEXT_THRESHOLD
        )
        if is_text_value:
            text_value = (value_text if value_text is not None else obj) or ""
            obj_id = _TEXT_OBJECT_SENTINEL
            value_kind = "text"
        else:
            text_value = None
            obj_id = self._entity_id(obj)
            value_kind = "entity"

        # Auto-create entities if they don't exist (subject always, object
        # only when this is an entity-valued triple).
        with self._lock:
            conn = self._conn()
            with conn:
                conn.execute(
                    "INSERT OR IGNORE INTO entities (id, name) VALUES (?, ?)",
                    (sub_id, subject),
                )
                if not is_text_value:
                    conn.execute(
                        "INSERT OR IGNORE INTO entities (id, name) VALUES (?, ?)",
                        (obj_id, obj),
                    )

                # Check for existing identical triple. For text-valued
                # triples we also need value_text in the dedup key,
                # since multiple (subject, predicate) pairs may share
                # the __text__ sentinel as object.
                if is_text_value:
                    existing = conn.execute(
                        "SELECT id FROM triples "
                        "WHERE subject=? AND predicate=? AND value_kind='text' "
                        "AND value_text=? AND valid_to IS NULL",
                        (sub_id, pred, text_value),
                    ).fetchone()
                else:
                    existing = conn.execute(
                        "SELECT id FROM triples "
                        "WHERE subject=? AND predicate=? AND object=? "
                        "AND (value_kind IS NULL OR value_kind='entity') "
                        "AND valid_to IS NULL",
                        (sub_id, pred, obj_id),
                    ).fetchone()
                if existing:
                    return existing["id"]  # Already exists and still valid

                triple_id = f"t_{sub_id}_{pred}_{obj_id}_{hashlib.sha256(f'{valid_from}{datetime.now().isoformat()}'.encode()).hexdigest()[:12]}"
                conn.execute(
                    """INSERT INTO triples (
                        id, subject, predicate, object, value_text, value_kind,
                        valid_from, valid_to,
                        confidence, source_closet, source_file,
                        source_drawer_id, adapter_name
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        triple_id,
                        sub_id,
                        pred,
                        obj_id,
                        text_value,
                        value_kind,
                        valid_from,
                        valid_to,
                        confidence,
                        source_closet,
                        source_file,
                        source_drawer_id,
                        adapter_name,
                    ),
                )
                return triple_id

    def invalidate(self, subject: str, predicate: str, obj: str, ended: str = None):
        """Mark a relationship as no longer valid (set valid_to date/time)."""
        sub_id = self._entity_id(subject)
        obj_id = self._entity_id(obj)
        pred = predicate.lower().replace(" ", "_")
        ended = sanitize_iso_temporal(ended or date.today().isoformat(), "ended")

        with self._lock:
            conn = self._conn()
            with conn:
                rows = conn.execute(
                    "SELECT id, valid_from FROM triples "
                    "WHERE subject=? AND predicate=? AND object=? AND valid_to IS NULL",
                    (sub_id, pred, obj_id),
                ).fetchall()

                for row in rows:
                    valid_from = row["valid_from"]
                    if valid_from is not None and _temporal_end_key(ended) < _temporal_start_key(
                        valid_from
                    ):
                        raise ValueError(
                            f"valid_to={ended!r} is before valid_from={valid_from!r}; "
                            "an inverted interval would be invisible to every KG query"
                        )

                conn.execute(
                    "UPDATE triples SET valid_to=? "
                    "WHERE subject=? AND predicate=? AND object=? AND valid_to IS NULL",
                    (ended, sub_id, pred, obj_id),
                )

    # ── Cleanup (Phase C) ────────────────────────────────────────────────

    def prune_phrase_entities(
        self,
        length_threshold: int = 60,
        apply: bool = False,
        limit: Optional[int] = None,
    ) -> dict:
        """Migrate descriptive 'phrase entities' to text-valued triples.

        Background (Phase C of the KG cleanup): before Phase B added
        text-valued triple objects, every object string was promoted to
        an entity row. Callers that passed clauses like
        ``"Article 3.5 allows forced sale at par value..."`` as the
        object of a triple ended up with that clause as a phantom
        entity. This method finds those phantoms and converts the
        triples that reference them to text-valued (``value_kind='text'``,
        prose in ``value_text``), then deletes the phantom entity.

        Heuristic for "phrase entity":
          - ``LENGTH(name) > length_threshold`` (default 60 chars)
          - NEVER appears as the ``subject`` of any triple (i.e. only
            referenced as object — a true sink, not a real actor)

        ``apply=False`` (default) is a dry-run: returns the list of
        candidates without touching the DB. ``apply=True`` commits the
        migration in one transaction.
        """
        with self._lock:
            conn = self._conn()
            sql = (
                "SELECT e.id, e.name, e.type "
                "FROM entities e "
                "WHERE LENGTH(e.name) > ? "
                "  AND e.id NOT IN (SELECT DISTINCT subject FROM triples) "
                "ORDER BY LENGTH(e.name) DESC"
            )
            params: list = [length_threshold]
            if limit is not None:
                sql += " LIMIT ?"
                params.append(limit)
            candidates = conn.execute(sql, params).fetchall()

            plan = []
            total_triples = 0
            for c in candidates:
                n = conn.execute(
                    "SELECT COUNT(*) AS c FROM triples WHERE object = ?",
                    (c["id"],),
                ).fetchone()["c"]
                plan.append(
                    {
                        "entity_id": c["id"],
                        "name": c["name"],
                        "type": c["type"],
                        "incoming_triples": n,
                    }
                )
                total_triples += n

            if not apply:
                return {
                    "dry_run": True,
                    "length_threshold": length_threshold,
                    "candidate_count": len(plan),
                    "triple_rewrites": total_triples,
                    "candidates": plan,
                }

            with conn:
                applied = 0
                for c in candidates:
                    conn.execute(
                        "UPDATE triples "
                        "SET value_text = ?, value_kind = 'text', object = ? "
                        "WHERE object = ?",
                        (c["name"], _TEXT_OBJECT_SENTINEL, c["id"]),
                    )
                    conn.execute("DELETE FROM entities WHERE id = ?", (c["id"],))
                    applied += 1

        return {
            "dry_run": False,
            "length_threshold": length_threshold,
            "entities_pruned": len(plan),
            "triples_rewritten": total_triples,
            "candidates": plan,
        }

    def set_entity_type(self, name: str, entity_type: str) -> dict:
        """Update an existing entity's ``type`` (person / org / topic / …).

        Returns ``{success, name, type, previous_type}``. Raises
        ``ValueError`` if the entity does not exist (use ``add_entity`` to
        create one with a type).
        """
        if not name or not name.strip():
            raise ValueError("name is required")
        if not entity_type or not entity_type.strip():
            raise ValueError("entity_type is required")
        eid = self._entity_id(name)
        with self._lock:
            conn = self._conn()
            row = conn.execute(
                "SELECT type FROM entities WHERE id = ?", (eid,)
            ).fetchone()
            if not row:
                raise ValueError(f"entity {name!r} not found")
            previous = row["type"]
            with conn:
                conn.execute(
                    "UPDATE entities SET type = ? WHERE id = ?",
                    (entity_type, eid),
                )
        return {
            "success": True,
            "name": name,
            "type": entity_type,
            "previous_type": previous,
        }

    # ── Merge ─────────────────────────────────────────────────────────────

    def merge_entities(self, from_name: str, into_name: str) -> dict:
        """Fold ``from_name`` into ``into_name``.

        Every triple where ``subject`` or ``object`` matches the source
        entity is rewritten to reference the target entity, then the source
        entity row is deleted. The operation is wrapped in a single
        transaction so a crash mid-merge leaves no dangling FKs.

        Returns ``{success, from, into, subject_rewrites, object_rewrites,
        triples_rewritten}``. ``triples_rewritten`` counts each updated row
        once; a triple that mentioned the source on both sides
        (self-referential after merge) is counted in both ``subject_`` and
        ``object_rewrites`` but only once in the total.

        Raises ``ValueError`` if either entity name is empty or if the
        source entity does not exist. Merging an entity into itself is a
        no-op (returns ``success=True`` with zero rewrites).
        """
        if not from_name or not from_name.strip():
            raise ValueError("from_name is required")
        if not into_name or not into_name.strip():
            raise ValueError("into_name is required")
        from_id = self._entity_id(from_name)
        into_id = self._entity_id(into_name)
        if from_id == into_id:
            return {
                "success": True,
                "from": from_name,
                "into": into_name,
                "subject_rewrites": 0,
                "object_rewrites": 0,
                "triples_rewritten": 0,
                "note": "from and into resolve to the same entity id",
            }

        with self._lock:
            conn = self._conn()
            from_row = conn.execute(
                "SELECT name FROM entities WHERE id = ?", (from_id,)
            ).fetchone()
            if not from_row:
                raise ValueError(f"entity {from_name!r} not found")
            # Auto-create the target if it doesn't exist yet — protects
            # against case where the canonical spelling has only ever been
            # written via add_triple (object side) and never via add_entity.
            into_row = conn.execute(
                "SELECT name FROM entities WHERE id = ?", (into_id,)
            ).fetchone()

            with conn:
                if not into_row:
                    conn.execute(
                        "INSERT INTO entities (id, name) VALUES (?, ?)",
                        (into_id, into_name),
                    )
                # Count which triples touch both sides so we don't
                # double-count them in the summary total.
                both_sides = conn.execute(
                    "SELECT COUNT(*) FROM triples WHERE subject = ? AND object = ?",
                    (from_id, from_id),
                ).fetchone()[0]
                cur_s = conn.execute(
                    "UPDATE triples SET subject = ? WHERE subject = ?",
                    (into_id, from_id),
                )
                s_rewrites = cur_s.rowcount or 0
                cur_o = conn.execute(
                    "UPDATE triples SET object = ? WHERE object = ?",
                    (into_id, from_id),
                )
                o_rewrites = cur_o.rowcount or 0
                conn.execute("DELETE FROM entities WHERE id = ?", (from_id,))

        return {
            "success": True,
            "from": from_name,
            "into": into_name,
            "subject_rewrites": s_rewrites,
            "object_rewrites": o_rewrites,
            "triples_rewritten": s_rewrites + o_rewrites - both_sides,
        }

    # ── Query operations ──────────────────────────────────────────────────

    def query_entity(self, name: str, as_of: str = None, direction: str = "outgoing"):
        """
        Get all relationships for an entity.

        direction: "outgoing" (entity → ?), "incoming" (? → entity), "both"
        as_of: ISO date or canonical UTC datetime — only return facts valid then
        """
        as_of = sanitize_iso_temporal(as_of, "as_of")
        eid = self._entity_id(name)
        results = []

        temporal_sql = ""
        temporal_params = []
        if as_of:
            temporal_sql, temporal_params = _temporal_filter_sql(as_of)

        with self._lock:
            conn = self._conn()

            if direction in ("outgoing", "both"):
                # LEFT JOIN entities: text-valued triples have an
                # ``__text__`` sentinel as ``object`` which doesn't match
                # any real entity row. ``COALESCE`` falls through to
                # ``value_text`` for those.
                query = (
                    "SELECT t.*, "
                    "       COALESCE(e.name, t.value_text) AS obj_name, "
                    "       COALESCE(t.value_kind, 'entity') AS obj_kind "
                    "FROM triples t "
                    "LEFT JOIN entities e ON t.object = e.id "
                    "WHERE t.subject = ?" + temporal_sql
                )
                params = [eid] + temporal_params

                for row in conn.execute(query, params).fetchall():
                    results.append(
                        {
                            "direction": "outgoing",
                            "subject": name,
                            "predicate": row["predicate"],
                            "object": row["obj_name"],
                            "object_kind": row["obj_kind"],
                            "valid_from": row["valid_from"],
                            "valid_to": row["valid_to"],
                            "confidence": row["confidence"],
                            "source_closet": row["source_closet"],
                            "current": row["valid_to"] is None,
                        }
                    )

            if direction in ("incoming", "both"):
                # Text-valued triples can never be 'incoming' on a real
                # entity — their object is the __text__ sentinel — so
                # an inner JOIN naturally excludes them. Keep it that
                # way; nothing to change here for Phase B.
                query = (
                    "SELECT t.*, e.name as sub_name FROM triples t "
                    "JOIN entities e ON t.subject = e.id WHERE t.object = ?" + temporal_sql
                )
                params = [eid] + temporal_params

                for row in conn.execute(query, params).fetchall():
                    results.append(
                        {
                            "direction": "incoming",
                            "subject": row["sub_name"],
                            "predicate": row["predicate"],
                            "object": name,
                            "object_kind": "entity",
                            "valid_from": row["valid_from"],
                            "valid_to": row["valid_to"],
                            "confidence": row["confidence"],
                            "source_closet": row["source_closet"],
                            "current": row["valid_to"] is None,
                        }
                    )

        return results

    def query_relationship(self, predicate: str, as_of: str = None):
        """Get all triples with a given relationship type."""
        as_of = sanitize_iso_temporal(as_of, "as_of")
        pred = predicate.lower().replace(" ", "_")

        query = """
            SELECT t.*, s.name as sub_name, o.name as obj_name
            FROM triples t
            JOIN entities s ON t.subject = s.id
            JOIN entities o ON t.object = o.id
            WHERE t.predicate = ?
        """
        params = [pred]

        if as_of:
            temporal_sql, temporal_params = _temporal_filter_sql(as_of)
            query += temporal_sql
            params.extend(temporal_params)

        results = []
        with self._lock:
            conn = self._conn()
            for row in conn.execute(query, params).fetchall():
                results.append(
                    {
                        "subject": row["sub_name"],
                        "predicate": pred,
                        "object": row["obj_name"],
                        "valid_from": row["valid_from"],
                        "valid_to": row["valid_to"],
                        "current": row["valid_to"] is None,
                    }
                )
        return results

    def timeline(self, entity_name: str = None):
        """Get all facts in chronological order, optionally filtered by entity.

        Text-valued triples are included; their object surfaces as the
        ``value_text`` content and ``object_kind`` is ``'text'``.
        """
        with self._lock:
            conn = self._conn()
            if entity_name:
                eid = self._entity_id(entity_name)
                rows = conn.execute(
                    """
                    SELECT t.*,
                           s.name as sub_name,
                           COALESCE(o.name, t.value_text) as obj_name,
                           COALESCE(t.value_kind, 'entity') as obj_kind
                    FROM triples t
                    JOIN entities s ON t.subject = s.id
                    LEFT JOIN entities o ON t.object = o.id
                    WHERE (t.subject = ? OR t.object = ?)
                    ORDER BY t.valid_from ASC NULLS LAST
                    LIMIT 100
                """,
                    (eid, eid),
                ).fetchall()
            else:
                rows = conn.execute("""
                    SELECT t.*,
                           s.name as sub_name,
                           COALESCE(o.name, t.value_text) as obj_name,
                           COALESCE(t.value_kind, 'entity') as obj_kind
                    FROM triples t
                    JOIN entities s ON t.subject = s.id
                    LEFT JOIN entities o ON t.object = o.id
                    ORDER BY t.valid_from ASC NULLS LAST
                    LIMIT 100
                """).fetchall()

        return [
            {
                "subject": r["sub_name"],
                "predicate": r["predicate"],
                "object": r["obj_name"],
                "object_kind": r["obj_kind"],
                "valid_from": r["valid_from"],
                "valid_to": r["valid_to"],
                "current": r["valid_to"] is None,
            }
            for r in rows
        ]

    # ── Whole-graph dump (for force-directed UI) ──────────────────────────

    def graph_dump(
        self,
        include_expired: bool = False,
        max_nodes: Optional[int] = None,
        as_of: Optional[str] = None,
    ):
        """One-shot dump of the whole graph for client-side visualization.

        Returns ``{nodes: [{id, name, type, degree}], edges: [{s, p, o,
        valid_from, valid_to, current}], truncated: bool}``. When ``max_nodes``
        is set and the graph has more nodes than that, returns only the
        top-degree subset and sets ``truncated=True``.

        ``include_expired`` and ``as_of`` follow the same conventions as
        ``query_entity``; the default ``include_expired=False`` returns only
        currently-valid facts (``valid_to IS NULL``).
        """
        clauses: list[str] = []
        params: list = []
        # Skip text-valued triples — they're properties of the subject,
        # not edges between entities. The SPA renders them inline on the
        # entity-detail page instead.
        clauses.append("(t.value_kind IS NULL OR t.value_kind = 'entity')")
        if not include_expired:
            clauses.append("(t.valid_to IS NULL)")
        if as_of is not None:
            extra, args = _temporal_filter_sql(as_of)
            clauses.append(extra)
            params.extend(args)
        where = "WHERE " + " AND ".join(clauses)
        with self._lock:
            conn = self._conn()
            triple_rows = conn.execute(
                f"""
                SELECT t.subject, t.predicate, t.object, t.valid_from, t.valid_to,
                       s.name AS sub_name, s.type AS sub_type,
                       o.name AS obj_name, o.type AS obj_type
                FROM triples t
                JOIN entities s ON t.subject = s.id
                JOIN entities o ON t.object = o.id
                {where}
                """,
                params,
            ).fetchall()

        degree: dict[str, int] = {}
        node_meta: dict[str, dict] = {}
        edges: list[dict] = []
        for r in triple_rows:
            s_id = r["subject"]
            o_id = r["object"]
            degree[s_id] = degree.get(s_id, 0) + 1
            degree[o_id] = degree.get(o_id, 0) + 1
            node_meta.setdefault(
                s_id, {"id": s_id, "name": r["sub_name"], "type": r["sub_type"] or "unknown"}
            )
            node_meta.setdefault(
                o_id, {"id": o_id, "name": r["obj_name"], "type": r["obj_type"] or "unknown"}
            )
            edges.append(
                {
                    "s": s_id,
                    "p": r["predicate"],
                    "o": o_id,
                    "valid_from": r["valid_from"],
                    "valid_to": r["valid_to"],
                    "current": r["valid_to"] is None,
                }
            )

        truncated = False
        if max_nodes is not None and len(node_meta) > max_nodes:
            kept = {nid for nid, _ in sorted(degree.items(), key=lambda kv: -kv[1])[:max_nodes]}
            node_meta = {nid: meta for nid, meta in node_meta.items() if nid in kept}
            edges = [e for e in edges if e["s"] in kept and e["o"] in kept]
            truncated = True

        nodes = [{**meta, "degree": degree.get(meta["id"], 0)} for meta in node_meta.values()]
        nodes.sort(key=lambda n: (-n["degree"], n["name"]))
        return {"nodes": nodes, "edges": edges, "truncated": truncated}

    # ── Stats ─────────────────────────────────────────────────────────────

    def stats(self):
        with self._lock:
            conn = self._conn()
            entities = conn.execute("SELECT COUNT(*) as cnt FROM entities").fetchone()["cnt"]
            triples = conn.execute("SELECT COUNT(*) as cnt FROM triples").fetchone()["cnt"]
            current = conn.execute(
                "SELECT COUNT(*) as cnt FROM triples WHERE valid_to IS NULL"
            ).fetchone()["cnt"]
            expired = triples - current
            predicates = [
                r["predicate"]
                for r in conn.execute(
                    "SELECT DISTINCT predicate FROM triples ORDER BY predicate"
                ).fetchall()
            ]
        return {
            "entities": entities,
            "triples": triples,
            "current_facts": current,
            "expired_facts": expired,
            "relationship_types": predicates,
        }

    # ── Seed from known facts ─────────────────────────────────────────────

    def seed_from_entity_facts(self, entity_facts: dict):
        """
        Seed the knowledge graph from fact_checker.py ENTITY_FACTS.
        This bootstraps the graph with known ground truth.
        """
        for key, facts in entity_facts.items():
            name = facts.get("full_name", key.capitalize())
            etype = facts.get("type", "person")
            self.add_entity(
                name,
                etype,
                {
                    "gender": facts.get("gender", ""),
                    "birthday": facts.get("birthday", ""),
                },
            )

            # Relationships
            parent = facts.get("parent")
            if parent:
                self.add_triple(
                    name, "child_of", parent.capitalize(), valid_from=facts.get("birthday")
                )

            partner = facts.get("partner")
            if partner:
                self.add_triple(name, "married_to", partner.capitalize())

            relationship = facts.get("relationship", "")
            if relationship == "daughter":
                self.add_triple(
                    name,
                    "is_child_of",
                    facts.get("parent", "").capitalize() or name,
                    valid_from=facts.get("birthday"),
                )
            elif relationship == "husband":
                self.add_triple(name, "is_partner_of", facts.get("partner", name).capitalize())
            elif relationship == "brother":
                self.add_triple(name, "is_sibling_of", facts.get("sibling", name).capitalize())
            elif relationship == "dog":
                self.add_triple(name, "is_pet_of", facts.get("owner", name).capitalize())
                self.add_entity(name, "animal")

            # Interests
            for interest in facts.get("interests", []):
                self.add_triple(name, "loves", interest.capitalize(), valid_from="2025-01-01")
