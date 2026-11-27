#!/usr/bin/env python3
"""
convo_miner.py — Mine conversations into the palace.

Ingests chat exports (Claude Code, ChatGPT, Slack, plain text transcripts).
Normalizes format, chunks by exchange pair (Q+A = one unit), files to palace.

Same palace as project mining. Different ingest strategy.
"""

import os
import sys
import hashlib
import logging
from pathlib import Path
from datetime import datetime
from collections import defaultdict

from .backends.chroma import refuse_if_diverged
from .normalize import normalize_with_times
from .palace import (
    NORMALIZE_VERSION,
    SKIP_DIRS,
    file_already_mined,
    get_collection,
    mine_lock,
)

logger = logging.getLogger("mempalace_mcp")


# Cached hall keywords — avoids re-reading config per drawer
_HALL_KEYWORDS_CACHE = None


def _detect_hall_cached(content: str) -> str:
    """Route content to a hall using cached keywords. Same logic as miner.detect_hall."""
    global _HALL_KEYWORDS_CACHE
    if _HALL_KEYWORDS_CACHE is None:
        from .config import MempalaceConfig

        _HALL_KEYWORDS_CACHE = MempalaceConfig().hall_keywords
    content_lower = content[:3000].lower()
    scores = {}
    for hall, keywords in _HALL_KEYWORDS_CACHE.items():
        score = sum(1 for kw in keywords if kw in content_lower)
        if score > 0:
            scores[hall] = score
    return max(scores, key=scores.get) if scores else "general"


# File types that might contain conversations
CONVO_EXTENSIONS = {
    ".txt",
    ".md",
    ".json",
    ".jsonl",
}

MIN_CHUNK_SIZE = 30
CHUNK_SIZE = 800  # chars per drawer — align with miner.py
DRAWER_UPSERT_BATCH_SIZE = 1000
MAX_FILE_SIZE = 500 * 1024 * 1024  # 500 MB — skip files larger than this.
# Matches miner.py at 500 MB. Long Claude Code sessions, multi-year
# ChatGPT exports, and lifetime Slack dumps routinely exceed 10 MB; the
# cap at that level silently dropped them with `continue`. Per-drawer
# size is bounded by CHUNK_SIZE, but larger source files still produce
# more drawers and therefore more embedding/storage work — and content
# is normalized and loaded fully into memory before chunking, so memory
# use also scales with source size.


def _registry_id(source_file: str) -> str:
    return f"_reg_{hashlib.sha256(source_file.encode()).hexdigest()[:24]}"


def _register_file(collection, source_file: str, wing: str, agent: str):
    """Write a sentinel so file_already_mined() returns True for 0-chunk files.

    Without this, files that normalize to nothing or produce zero chunks are
    re-read and re-processed on every mine run because nothing was written to
    ChromaDB on the first pass.
    """
    sentinel_id = _registry_id(source_file)
    collection.upsert(
        documents=[f"[registry] {source_file}"],
        ids=[sentinel_id],
        metadatas=[
            {
                "wing": wing,
                "room": "_registry",
                "source_file": source_file,
                "added_by": agent,
                "filed_at": datetime.now().isoformat(),
                "ingest_mode": "registry",
                "normalize_version": NORMALIZE_VERSION,
            }
        ],
    )


# =============================================================================
# INCREMENTAL STATE — what each transcript looked like at its last mine
# =============================================================================
#
# A live transcript grows: Claude Code appends records to the session JSONL.
# The miner keeps, per source file, the inode, the byte size (the offset up to
# which the file is mined) and a hash of the bytes just before that offset. On
# the next mine:
#
#   same inode, same size, same probe      -> skip (only the mtime moved)
#   same inode, larger, same probe at the
#   old offset                             -> grow: file the new chunks only
#   other inode, smaller, or probe changed -> rewrite: mine the file whole
#
# A grown file is still normalized and chunked whole (cheap); the miner then
# compares the chunks with the drawers it holds and embeds only the chunks that
# are new or changed. Drawer ids stay stable because they hash the source path
# and the chunk index, and the room of the file is kept from its first mine.

CONVO_STATE_FILE = "convo_mine_state.sqlite3"
_PROBE_BYTES = 4096
# Metadata that moves when a transcript grows but needs no new embedding.
_DATE_KEYS = ("source_date", "source_date_end", "session_end", "date_source")


class _MineState:
    """Per-palace record of (inode, size, probe) for each mined source file."""

    def __init__(self, palace_path: str):
        import sqlite3

        os.makedirs(palace_path, exist_ok=True)
        self._db = sqlite3.connect(os.path.join(palace_path, CONVO_STATE_FILE), timeout=30)
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS files ("
            " source_file TEXT PRIMARY KEY, inode INTEGER, size INTEGER,"
            " probe TEXT, normalize_version INTEGER, mined_at TEXT)"
        )
        self._db.commit()

    def get(self, source_file: str):
        row = self._db.execute(
            "SELECT inode, size, probe, normalize_version FROM files WHERE source_file = ?",
            (source_file,),
        ).fetchone()
        if row is None:
            return None
        return {"inode": row[0], "size": row[1], "probe": row[2], "normalize_version": row[3]}

    def put(self, source_file: str, sig: dict) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO files VALUES (?, ?, ?, ?, ?, ?)",
            (
                source_file,
                sig["inode"],
                sig["size"],
                sig["probe"],
                NORMALIZE_VERSION,
                datetime.now().isoformat(),
            ),
        )
        self._db.commit()

    def close(self) -> None:
        self._db.close()


def _probe(fh, end: int) -> str:
    """Hash of the bytes ``[end - _PROBE_BYTES, end)`` of an open binary file."""
    start = max(0, end - _PROBE_BYTES)
    fh.seek(start)
    return hashlib.sha256(fh.read(end - start)).hexdigest()


def _file_signature(path) -> dict:
    """The (inode, size, probe) of ``path`` now. Raises OSError."""
    st = os.stat(path)
    with open(path, "rb") as fh:
        probe = _probe(fh, st.st_size)
    return {"inode": st.st_ino, "size": st.st_size, "probe": probe}


def _mine_plan(path, prev: dict, sig: dict) -> str:
    """Return ``"skip"``, ``"grow"`` or ``"whole"`` for a file with state ``prev``.

    No state (a palace mined before this record existed) gives ``"grow"``:
    the chunk diff then files whatever the palace lacks and nothing else.
    """
    if prev is None:
        return "grow"
    if (prev.get("normalize_version") or 0) < NORMALIZE_VERSION:
        return "whole"
    if prev["inode"] != sig["inode"] or sig["size"] < prev["size"]:
        return "whole"
    if sig["size"] == prev["size"]:
        return "skip" if sig["probe"] == prev["probe"] else "whole"
    try:
        with open(path, "rb") as fh:
            old = _probe(fh, prev["size"])
    except OSError:
        return "whole"
    return "grow" if old == prev["probe"] else "whole"


# =============================================================================
# CHUNKING — exchange pairs for conversations
# =============================================================================


def chunk_exchanges(content: str, with_lines: bool = False) -> list:
    """
    Chunk by exchange pair: one > turn + AI response = one unit.
    Falls back to paragraph chunking if no > markers.

    With ``with_lines=True``, each exchange chunk also gets ``lines``:
    the ``(first, last)`` indices of the ``content`` lines it holds, so
    the miner can date it from the source records of those lines.
    """
    lines = content.split("\n")
    quote_lines = sum(1 for line in lines if line.strip().startswith(">"))

    if quote_lines >= 3:
        return _chunk_by_exchange(lines, with_lines=with_lines)
    chunks = _chunk_by_paragraph(content)
    if with_lines:
        _locate_lines(content, chunks)
    return chunks


def _locate_lines(content: str, chunks: list) -> None:
    """Set ``lines`` on chunks that are verbatim slices of ``content``, in order."""
    cursor, line_no = 0, 0
    for chunk in chunks:
        text = chunk["content"]
        pos = content.find(text, cursor)
        if pos < 0:
            continue
        line_no += content.count("\n", cursor, pos)
        last = line_no + text.count("\n")
        chunk["lines"] = (line_no, last)
        cursor, line_no = pos, line_no


def _span_lines(pieces: list, start: int, end: int) -> tuple:
    """Return the first and last line index of ``pieces`` that overlap ``[start, end)``."""
    hit = [idx for idx, a, b in pieces if a < end and b > start]
    return (hit[0], hit[-1]) if hit else (pieces[0][0], pieces[0][0])


def _chunk_by_exchange(lines: list, with_lines: bool = False) -> list:
    """One user turn (>) + the AI response that follows = one or more chunks.

    The full AI response is preserved verbatim.  When the combined
    user-turn + response exceeds CHUNK_SIZE the response is split across
    consecutive drawers so nothing is silently discarded.
    """
    chunks = []
    i = 0

    def _add(text, pieces, start):
        chunk = {"content": text, "chunk_index": len(chunks)}
        if with_lines:
            chunk["lines"] = _span_lines(pieces, start, start + len(text))
        chunks.append(chunk)

    while i < len(lines):
        line = lines[i]
        if line.strip().startswith(">"):
            user_turn = line.strip()
            # (line index, start, end) of each piece inside ``content``
            pieces = [(i, 0, len(user_turn))]
            pos = len(user_turn) + 1
            i += 1

            ai_lines = []
            while i < len(lines):
                next_line = lines[i]
                if next_line.strip().startswith(">") or next_line.strip().startswith("---"):
                    break
                if next_line.strip():
                    piece = next_line.strip()
                    ai_lines.append(piece)
                    pieces.append((i, pos, pos + len(piece)))
                    pos += len(piece) + 1
                i += 1

            ai_response = " ".join(ai_lines)
            content = f"{user_turn}\n{ai_response}" if ai_response else user_turn

            # Split into multiple drawers when the exchange exceeds CHUNK_SIZE
            if len(content) > CHUNK_SIZE:
                # First chunk: user turn + as much response as fits
                first_part = content[:CHUNK_SIZE]
                if len(first_part.strip()) > MIN_CHUNK_SIZE:
                    _add(first_part, pieces, 0)
                # Remaining response in CHUNK_SIZE-sized continuation drawers
                offset = CHUNK_SIZE
                remainder = content[CHUNK_SIZE:]
                while remainder:
                    part = remainder[:CHUNK_SIZE]
                    remainder = remainder[CHUNK_SIZE:]
                    if len(part.strip()) > MIN_CHUNK_SIZE:
                        _add(part, pieces, offset)
                    offset += CHUNK_SIZE
            elif len(content.strip()) > MIN_CHUNK_SIZE:
                _add(content, pieces, 0)
        else:
            i += 1

    return chunks


def _chunk_by_paragraph(content: str) -> list:
    """Fallback: chunk by paragraph breaks."""
    chunks = []
    paragraphs = [p.strip() for p in content.split("\n\n") if p.strip()]

    # If no paragraph breaks and long content, chunk by line groups
    if len(paragraphs) <= 1 and content.count("\n") > 20:
        lines = content.split("\n")
        for i in range(0, len(lines), 25):
            group = "\n".join(lines[i : i + 25]).strip()
            if len(group) > MIN_CHUNK_SIZE:
                chunks.append({"content": group, "chunk_index": len(chunks)})
        return chunks

    for para in paragraphs:
        if len(para) <= MIN_CHUNK_SIZE:
            continue
        # Hard size cap. Single-paragraph blobs (SQL dumps, JSON
        # responses, base64 attachments) used to land as one drawer of
        # arbitrary size — splitting them keeps the index usable and
        # bounds HNSW/sqlite cost per source file.
        if len(para) > CHUNK_SIZE:
            for start in range(0, len(para), CHUNK_SIZE):
                part = para[start : start + CHUNK_SIZE]
                if len(part.strip()) > MIN_CHUNK_SIZE:
                    chunks.append({"content": part, "chunk_index": len(chunks)})
            continue
        chunks.append({"content": para, "chunk_index": len(chunks)})

    return chunks


# =============================================================================
# ROOM DETECTION — topic-based for conversations
# =============================================================================

TOPIC_KEYWORDS = {
    "technical": [
        "code",
        "python",
        "function",
        "bug",
        "error",
        "api",
        "database",
        "server",
        "deploy",
        "git",
        "test",
        "debug",
        "refactor",
    ],
    "architecture": [
        "architecture",
        "design",
        "pattern",
        "structure",
        "schema",
        "interface",
        "module",
        "component",
        "service",
        "layer",
    ],
    "planning": [
        "plan",
        "roadmap",
        "milestone",
        "deadline",
        "priority",
        "sprint",
        "backlog",
        "scope",
        "requirement",
        "spec",
    ],
    "decisions": [
        "decided",
        "chose",
        "picked",
        "switched",
        "migrated",
        "replaced",
        "trade-off",
        "alternative",
        "option",
        "approach",
    ],
    "problems": [
        "problem",
        "issue",
        "broken",
        "failed",
        "crash",
        "stuck",
        "workaround",
        "fix",
        "solved",
        "resolved",
    ],
}


def detect_convo_room(content: str) -> str:
    """Score conversation content against topic keywords."""
    content_lower = content[:3000].lower()
    scores = {}
    for room, keywords in TOPIC_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in content_lower)
        if score > 0:
            scores[room] = score
    if scores:
        return max(scores, key=scores.get)
    return "general"


# =============================================================================
# PALACE OPERATIONS
# =============================================================================


# =============================================================================
# SCAN FOR CONVERSATION FILES
# =============================================================================


def scan_convos(convo_dir: str) -> list:
    """Find all potential conversation files."""
    convo_path = Path(convo_dir).expanduser().resolve()
    files = []
    for root, dirs, filenames in os.walk(convo_path):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for filename in filenames:
            if filename.endswith(".meta.json"):
                continue
            filepath = Path(root) / filename
            if filepath.suffix.lower() in CONVO_EXTENSIONS:
                # Skip symlinks and oversized files
                if filepath.is_symlink():
                    continue
                try:
                    if filepath.stat().st_size > MAX_FILE_SIZE:
                        continue
                except OSError:
                    continue
                files.append(filepath)
    return files


# =============================================================================
# MINE CONVERSATIONS
# =============================================================================


_TS_RE = None


def _message_timestamps(filepath) -> list:
    """Return the ISO timestamps of the dated lines of a JSONL transcript.

    Claude Code and Codex JSONL lines carry a top-level ``timestamp``.
    Normalization drops them, so the miner reads them here to date each
    drawer. Returns ``[]`` for files without such lines (plain text,
    exports) or on any read error.
    """
    import json
    import re

    global _TS_RE
    if _TS_RE is None:
        _TS_RE = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d")
    if not str(filepath).endswith(".jsonl"):
        return []
    out = []
    try:
        with open(filepath, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if '"timestamp"' not in line:
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue
                ts = obj.get("timestamp") if isinstance(obj, dict) else None
                if isinstance(ts, str) and _TS_RE.match(ts):
                    out.append(ts[:19])
    except OSError:
        return []
    out.sort()
    return out


def _date_chunks(chunks: list, timestamps: list, line_times: list = None) -> None:
    """Set ``source_date`` on each chunk from the transcript timestamps.

    With ``line_times`` (one source time per content line, from
    :func:`normalize.normalize_with_times`) a chunk that carries ``lines``
    gets the exact time of its first source record as ``source_date``
    and of its last as ``source_date_end``; ``date_source`` is
    ``"record"``. Other chunks fall back to the estimate: chunk ``i`` of
    ``n`` gets the timestamp at the same relative position
    (``date_source`` ``"estimate"``). ``session_end`` is always the last
    timestamp of the file.
    """
    if not chunks:
        return
    end = timestamps[-1] if timestamps else None
    if line_times:
        nonempty = [t for t in line_times if t]
        end = max(nonempty) if nonempty else end
    n, m = len(chunks), len(timestamps)
    for i, chunk in enumerate(chunks):
        span = chunk.pop("lines", None)
        own = []
        if line_times and span:
            own = [t for t in line_times[span[0] : span[1] + 1] if t]
        if own:
            chunk["source_date"] = min(own)
            chunk["source_date_end"] = max(own)
            chunk["date_source"] = "record"
        elif m:
            pos = min(m - 1, int((i + 0.5) * m / n))
            chunk["source_date"] = timestamps[pos]
            chunk["date_source"] = "estimate"
        else:
            continue
        chunk["session_end"] = end or chunk["source_date"]


def _drawer_meta(chunk, source_file, chunk_room, wing, agent, extract_mode, filed_at):
    """The metadata of one convo drawer."""
    return {
        "wing": wing,
        "room": chunk_room,
        "hall": _detect_hall_cached(chunk["content"]),
        "source_file": source_file,
        "chunk_index": chunk["chunk_index"],
        "added_by": agent,
        "filed_at": filed_at,
        "ingest_mode": "convos",
        "extract_mode": extract_mode,
        "normalize_version": NORMALIZE_VERSION,
        **(
            {
                "source_date": chunk["source_date"],
                "session_end": chunk["session_end"],
                "date_source": chunk.get("date_source", "estimate"),
                **(
                    {"source_date_end": chunk["source_date_end"]}
                    if chunk.get("source_date_end")
                    else {}
                ),
            }
            if chunk.get("source_date")
            else {}
        ),
    }


def _drawer_id(wing, chunk_room, source_file, chunk_index):
    digest = hashlib.sha256((source_file + str(chunk_index)).encode()).hexdigest()[:24]
    return f"drawer_{wing}_{chunk_room}_{digest}"


def _existing_drawers(collection, source_file):
    """{chunk_index: (id, document, metadata)} of the convo drawers of a file.

    Returns ``None`` when a drawer has an older ``normalize_version``: the
    file must then be mined whole.
    """
    got = collection.get(where={"source_file": source_file}, include=["documents", "metadatas"])
    out = {}
    for did, doc, meta in zip(
        got.get("ids") or [], got.get("documents") or [], got.get("metadatas") or []
    ):
        meta = meta or {}
        if meta.get("ingest_mode") == "registry" or meta.get("room") == "_registry":
            continue
        if (meta.get("normalize_version") or 1) < NORMALIZE_VERSION:
            return None
        idx = meta.get("chunk_index")
        if isinstance(idx, int):
            out[idx] = (did, doc, meta)
    return out


def _file_chunks_locked(
    collection, source_file, chunks, wing, room, agent, extract_mode, plan="whole"
):
    """Lock the source file and file its chunks.

    ``plan="whole"`` purges every drawer of the file and files all chunks
    (a rewritten file, or a normalize-version rebuild). ``plan="grow"``
    compares the chunks with the drawers the file already has and upserts
    only the new or changed ones; unchanged drawers keep their embedding
    and get only their date metadata refreshed.

    Returns (drawers_added, room_counts_delta, skipped).
    """
    room_counts_delta: dict = defaultdict(int)
    drawers_added = 0
    with mine_lock(source_file):
        existing = _existing_drawers(collection, source_file) if plan == "grow" else None
        if existing is None:
            plan = "whole"
            try:
                collection.delete(where={"source_file": source_file})
            except Exception:
                logger.debug("Stale-drawer purge failed for %s", source_file, exc_info=True)
            existing = {}
        elif existing and extract_mode != "general":
            # Keep the room of the first mine, so the drawer ids stay stable.
            rooms = defaultdict(int)
            for _did, _doc, meta in existing.values():
                rooms[meta.get("room") or room] += 1
            room = max(rooms, key=rooms.get)

        # Keep one filed_at per source file so all transcript drawers share an
        # ingest timestamp.
        filed_at = datetime.now().isoformat()
        new_ids = set()
        to_file = []
        refresh_ids, refresh_metas = [], []
        for chunk in chunks:
            chunk_room = chunk.get("memory_type", room) if extract_mode == "general" else room
            did = _drawer_id(wing, chunk_room, source_file, chunk["chunk_index"])
            new_ids.add(did)
            old = existing.get(chunk["chunk_index"])
            if old and old[0] == did and old[1] == chunk["content"]:
                meta = dict(old[2])
                fresh = _drawer_meta(chunk, source_file, chunk_room, wing, agent, extract_mode, "")
                if any(meta.get(k) != fresh.get(k) for k in _DATE_KEYS):
                    # Chroma merges an update into the stored metadata; a
                    # None value deletes the key that the chunk lost.
                    for k in _DATE_KEYS:
                        meta[k] = fresh.get(k)
                    refresh_ids.append(did)
                    refresh_metas.append(meta)
                continue
            to_file.append((did, chunk_room, chunk))

        stale = [old[0] for old in existing.values() if old[0] not in new_ids]
        if chunks:
            # A file that once had too little content got a registry
            # sentinel. It has real drawers now: the sentinel must go, or
            # its "[registry] PATH" text stays searchable.
            stale.append(_registry_id(source_file))
        if stale:
            collection.delete(ids=stale)
        for start in range(0, len(refresh_ids), DRAWER_UPSERT_BATCH_SIZE):
            collection.update(
                ids=refresh_ids[start : start + DRAWER_UPSERT_BATCH_SIZE],
                metadatas=refresh_metas[start : start + DRAWER_UPSERT_BATCH_SIZE],
            )

        # Batch chunks into bounded upserts so large transcripts keep most of
        # the embedding speedup without one huge Chroma/SQLite request.
        for batch_start in range(0, len(to_file), DRAWER_UPSERT_BATCH_SIZE):
            batch = to_file[batch_start : batch_start + DRAWER_UPSERT_BATCH_SIZE]
            for _did, chunk_room, _chunk in batch:
                if extract_mode == "general":
                    room_counts_delta[chunk_room] += 1
            try:
                collection.upsert(
                    documents=[c["content"] for _d, _r, c in batch],
                    ids=[d for d, _r, _c in batch],
                    metadatas=[
                        _drawer_meta(c, source_file, r, wing, agent, extract_mode, filed_at)
                        for _d, r, c in batch
                    ],
                )
                drawers_added += len(batch)
            except Exception as e:
                if "already exists" not in str(e).lower():
                    raise
    return drawers_added, room_counts_delta, False


def _capacity_refused(palace_path: str, op_name: str) -> bool:
    """Share the HNSW capacity-divergence guard with the HTTP daemon —
    see bead self-rob. Returns True after printing the refusal so callers
    can early-return without writing.
    """
    try:
        from .mcp_server import _wal_log
    except Exception:
        _wal_log = None
    refused = refuse_if_diverged(palace_path, "mempalace_drawers", op_name, wal_log=_wal_log)
    if refused is None:
        return False
    print(f"\n  Mine refused: {refused['error']}", file=sys.stderr)
    return True


def mine_convos(
    convo_dir: str,
    palace_path: str,
    wing: str = None,
    agent: str = "mempalace",
    limit: int = 0,
    dry_run: bool = False,
    extract_mode: str = "exchange",
):
    """Mine a directory of conversation files into the palace.

    extract_mode:
        "exchange" — default exchange-pair chunking (Q+A = one unit)
        "general"  — general extractor: decisions, preferences, milestones, problems, emotions
    """

    convo_path = Path(convo_dir).expanduser().resolve()
    if not wing:
        from .config import normalize_wing_name

        wing = normalize_wing_name(convo_path.name)

    files = scan_convos(convo_dir)
    if limit > 0:
        files = files[:limit]

    print(f"\n{'=' * 55}")
    print("  MemPalace Mine — Conversations")
    print(f"{'=' * 55}")
    print(f"  Wing:    {wing}")
    print(f"  Source:  {convo_path}")
    print(f"  Files:   {len(files)}")
    print(f"  Palace:  {palace_path}")
    if dry_run:
        print("  DRY RUN — nothing will be filed")
    print(f"{'-' * 55}\n")

    if not dry_run and _capacity_refused(palace_path, "mine_convos"):
        return

    collection = get_collection(palace_path) if not dry_run else None
    state = _MineState(palace_path) if not dry_run else None
    try:
        total_drawers, files_skipped, room_counts = _mine_files(
            files, collection, state, wing, agent, dry_run, extract_mode
        )
    finally:
        if state is not None:
            state.close()

    print(f"\n{'=' * 55}")
    print("  Done.")
    print(f"  Files processed: {len(files) - files_skipped}")
    print(f"  Files skipped (already filed): {files_skipped}")
    print(f"  Drawers filed: {total_drawers}")
    if room_counts:
        print("\n  By room:")
        for room, count in sorted(room_counts.items(), key=lambda x: x[1], reverse=True):
            print(f"    {room:20} {count} files")
    print('\n  Next: mempalace search "what you\'re looking for"')
    print(f"{'=' * 55}\n")


def _mine_files(files, collection, state, wing, agent, dry_run, extract_mode):
    """File each convo file. Return (drawers filed, files skipped, room counts)."""
    total_drawers = 0
    files_skipped = 0
    room_counts = defaultdict(int)

    for i, filepath in enumerate(files, 1):
        source_file = str(filepath)

        plan = "whole"
        sig = None
        if not dry_run:
            try:
                sig = _file_signature(filepath)
            except OSError:
                continue
            prev = state.get(source_file)
            plan = _mine_plan(filepath, prev, sig)
            # The state alone cannot see drawers that went away (an erase, a
            # repair) or a version bump stamped on them: check the palace too.
            if plan == "skip":
                if file_already_mined(collection, source_file):
                    files_skipped += 1
                    continue
                plan = "whole"
            elif prev is None and plan == "grow" and not _has_drawers(collection, source_file):
                plan = "whole"

        def _register():
            # A rewritten file that now yields nothing must not keep old drawers.
            if plan == "whole":
                try:
                    collection.delete(where={"source_file": source_file})
                except Exception:
                    logger.debug("Purge failed for %s", source_file, exc_info=True)
            _register_file(collection, source_file, wing, agent)
            state.put(source_file, sig)

        # Normalize format
        try:
            content, line_times = normalize_with_times(str(filepath))
        except (OSError, ValueError):
            if not dry_run:
                _register()
            continue

        if not content or len(content.strip()) < MIN_CHUNK_SIZE:
            if not dry_run:
                _register()
            continue

        # Chunk — either exchange pairs or general extraction
        if extract_mode == "general":
            from .general_extractor import extract_memories

            chunks = extract_memories(content)
            # Each chunk already has memory_type; use it as the room name
        else:
            chunks = chunk_exchanges(content, with_lines=line_times is not None)

        _date_chunks(chunks, _message_timestamps(filepath), line_times)

        if not chunks:
            if not dry_run:
                _register()
            continue

        # Detect room from content (general mode uses memory_type instead)
        if extract_mode != "general":
            room = detect_convo_room(content)
        else:
            room = None  # set per-chunk below

        if dry_run:
            if extract_mode == "general":
                from collections import Counter

                type_counts = Counter(c.get("memory_type", "general") for c in chunks)
                types_str = ", ".join(f"{t}:{n}" for t, n in type_counts.most_common())
                print(f"    [DRY RUN] {filepath.name} → {len(chunks)} memories ({types_str})")
            else:
                print(f"    [DRY RUN] {filepath.name} → room:{room} ({len(chunks)} drawers)")
            total_drawers += len(chunks)
            # Track room counts
            if extract_mode == "general":
                for c in chunks:
                    room_counts[c.get("memory_type", "general")] += 1
            else:
                room_counts[room] += 1
            continue

        if extract_mode != "general":
            room_counts[room] += 1

        # Lock + file the chunks. Lock serializes concurrent agents. A whole
        # plan purges the old drawers first (a rewrite, or a schema bump); a
        # grow plan files only the chunks the palace lacks.
        drawers_added, room_delta, skipped = _file_chunks_locked(
            collection, source_file, chunks, wing, room, agent, extract_mode, plan
        )
        state.put(source_file, sig)
        if skipped:
            files_skipped += 1
            continue
        for r, n in room_delta.items():
            room_counts[r] += n

        total_drawers += drawers_added
        print(f"  + [{i:4}/{len(files)}] {filepath.name[:50]:50} +{drawers_added}")

    return total_drawers, files_skipped, room_counts


def _has_drawers(collection, source_file) -> bool:
    try:
        return bool(collection.get(where={"source_file": source_file}, limit=1).get("ids"))
    except Exception:
        return False


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python convo_miner.py <convo_dir> [--palace PATH] [--limit N] [--dry-run]")
        sys.exit(1)
    from .config import MempalaceConfig

    mine_convos(sys.argv[1], palace_path=MempalaceConfig().palace_path)
