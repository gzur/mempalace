"""Mempalace health checks — shared primitive for the doctor CLI, the
``/ui/health`` web pane, and the launchd monitor script.

Every check is fast, read-only, and never opens a chromadb PersistentClient
(chromadb cold-loads are the thing we're checking against). All counts come
from a read-only ``chroma.sqlite3`` connection or pure filesystem reads.

A check returns a :class:`Check` tuple with one of three statuses:

- ``ok``   — measurement is in the expected range
- ``warn`` — anomaly worth noticing but not actionable yet
- ``fail`` — actionable: ship a bead, page someone, restart the daemon

The optional ``signature`` field is the failure_signature used by beads to
dedup repeat alerts (see ``MEMPALACE_INSTRUCTIONS`` in mcp_server.py).
"""

from __future__ import annotations

import json
import os
import socket
import sqlite3
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .config import MempalaceConfig

OK = "ok"
WARN = "warn"
FAIL = "fail"

# Queue lag above this is a sign the HNSW consumer is stuck (#1222 / self-mob
# class). Below this it's normal write traffic catching up.
QUEUE_LAG_WARN = 5_000
QUEUE_LAG_FAIL = 50_000

# launchd successive_crashes above this means KeepAlive is flapping; daemon
# can't stay up across restarts.
LAUNCHD_CRASHES_WARN = 1
LAUNCHD_CRASHES_FAIL = 3

DAEMON_HOST_DEFAULT = "127.0.0.1"
DAEMON_PORT_DEFAULT = 9876
LAUNCHD_LABEL_DEFAULT = "cloud.skyvafnir.mempalace"


@dataclass
class Check:
    name: str
    status: str  # OK | WARN | FAIL
    detail: str
    value: Optional[Any] = None
    signature: Optional[str] = None
    extra: dict = field(default_factory=dict)


# ── individual checks ─────────────────────────────────────────────────────


def check_daemon_port(host: str = DAEMON_HOST_DEFAULT, port: int = DAEMON_PORT_DEFAULT) -> Check:
    """TCP-connect to the daemon's port. Doesn't speak HTTP."""
    try:
        with socket.create_connection((host, port), timeout=2.0):
            return Check(
                name="daemon_port",
                status=OK,
                detail=f"{host}:{port} listening",
                value=port,
            )
    except (OSError, socket.timeout) as e:
        return Check(
            name="daemon_port",
            status=FAIL,
            detail=f"cannot connect to {host}:{port}: {e}",
            signature=f"mempalace daemon_port unreachable {host}:{port}",
        )


def check_daemon_healthz(host: str = DAEMON_HOST_DEFAULT, port: int = DAEMON_PORT_DEFAULT) -> Check:
    """GET /healthz; check ok:true + version present."""
    url = f"http://{host}:{port}/healthz"
    try:
        with urllib.request.urlopen(url, timeout=2.0) as resp:
            body = json.loads(resp.read())
    except (urllib.error.URLError, json.JSONDecodeError, OSError) as e:
        return Check(
            name="daemon_healthz",
            status=FAIL,
            detail=f"healthz failed: {e}",
            signature="mempalace daemon_healthz failed",
        )
    if not body.get("ok"):
        return Check(
            name="daemon_healthz",
            status=FAIL,
            detail=f"healthz returned {body}",
            signature="mempalace daemon_healthz not_ok",
        )
    return Check(
        name="daemon_healthz",
        status=OK,
        detail=f"version {body.get('version', '?')}",
        value=body.get("version"),
    )


def check_launchd_status(label: str = LAUNCHD_LABEL_DEFAULT) -> Check:
    """Parse ``launchctl print`` for successive_crashes + last_terminating_signal."""
    uid = os.getuid()
    try:
        out = subprocess.run(
            ["launchctl", "print", f"gui/{uid}/{label}"],
            capture_output=True,
            text=True,
            timeout=3.0,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        return Check(
            name="launchd",
            status=WARN,
            detail=f"launchctl unavailable: {e}",
        )
    if out.returncode != 0:
        return Check(
            name="launchd",
            status=WARN,
            detail=f"launchctl returned rc={out.returncode}; agent not loaded?",
        )
    crashes = 0
    last_signal = ""
    pid: Optional[int] = None
    for line in out.stdout.splitlines():
        line = line.strip()
        if line.startswith("successive crashes"):
            try:
                crashes = int(line.split("=", 1)[1].strip())
            except (ValueError, IndexError):
                pass
        elif line.startswith("last terminating signal"):
            last_signal = line.split("=", 1)[1].strip() if "=" in line else ""
        elif line.startswith("pid"):
            try:
                pid = int(line.split("=", 1)[1].strip())
            except (ValueError, IndexError):
                pass

    extra = {"pid": pid, "successive_crashes": crashes, "last_terminating_signal": last_signal}
    if crashes >= LAUNCHD_CRASHES_FAIL:
        return Check(
            name="launchd",
            status=FAIL,
            detail=f"{crashes} successive crashes; last signal: {last_signal or 'unknown'}",
            value=crashes,
            signature=f"mempalace launchd flapping crashes={crashes}",
            extra=extra,
        )
    if crashes >= LAUNCHD_CRASHES_WARN:
        return Check(
            name="launchd",
            status=WARN,
            detail=f"{crashes} successive crashes; last signal: {last_signal or 'unknown'}",
            value=crashes,
            extra=extra,
        )
    if last_signal:
        return Check(
            name="launchd",
            status=WARN,
            detail=f"clean now (pid={pid}) but last terminating signal was {last_signal}",
            value=crashes,
            extra=extra,
        )
    return Check(
        name="launchd",
        status=OK,
        detail=f"pid={pid}, runs clean",
        value=crashes,
        extra=extra,
    )


def _drawer_segment_ids(conn: sqlite3.Connection) -> dict[str, str]:
    """Return {kind: segment_id} for the drawers collection's two segments."""
    out: dict[str, str] = {}
    rows = conn.execute(
        "SELECT s.id, s.type FROM segments s "
        "JOIN collections c ON s.collection = c.id "
        "WHERE c.name = 'mempalace_drawers'"
    ).fetchall()
    for sid, stype in rows:
        if "metadata" in stype:
            out["metadata"] = sid
        elif "vector" in stype or "hnsw" in stype:
            out["vector"] = sid
    return out


def check_queue_lag(palace_path: str) -> Check:
    """``embeddings_queue`` row count. Large values = consumer stuck."""
    db = Path(palace_path) / "chroma.sqlite3"
    if not db.is_file():
        return Check(name="queue_lag", status=WARN, detail="no palace db")
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=2.0)
        try:
            count = conn.execute("SELECT count(*) FROM embeddings_queue").fetchone()[0]
        finally:
            conn.close()
    except sqlite3.Error as e:
        return Check(name="queue_lag", status=WARN, detail=f"sqlite error: {e}")
    if count >= QUEUE_LAG_FAIL:
        return Check(
            name="queue_lag",
            status=FAIL,
            detail=f"{count:,} entries — consumer appears stuck",
            value=count,
            signature=f"mempalace queue_lag fail count>={QUEUE_LAG_FAIL}",
        )
    if count >= QUEUE_LAG_WARN:
        return Check(
            name="queue_lag",
            status=WARN,
            detail=f"{count:,} entries — elevated lag",
            value=count,
        )
    return Check(name="queue_lag", status=OK, detail=f"{count} pending", value=count)


def check_seq_id_gap(palace_path: str) -> Check:
    """Gap between drawers metadata segment's seq_id and drawers HNSW vector
    segment's seq_id. A persistent gap means the HNSW consumer is behind
    even after the queue itself has drained (rare; happens after the kind
    of two-writer race that triggered self-mob)."""
    db = Path(palace_path) / "chroma.sqlite3"
    if not db.is_file():
        return Check(name="seq_id_gap", status=WARN, detail="no palace db")
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=2.0)
        try:
            segs = _drawer_segment_ids(conn)
            if "metadata" not in segs or "vector" not in segs:
                return Check(
                    name="seq_id_gap",
                    status=WARN,
                    detail="drawers segments not found",
                )
            rows = {
                sid: row[0]
                for sid, row in (
                    (sid, conn.execute(
                        "SELECT seq_id FROM max_seq_id WHERE segment_id = ?", (sid,)
                    ).fetchone())
                    for sid in (segs["metadata"], segs["vector"])
                )
                if row is not None
            }
        finally:
            conn.close()
    except sqlite3.Error as e:
        return Check(name="seq_id_gap", status=WARN, detail=f"sqlite error: {e}")
    meta_seq = rows.get(segs["metadata"])
    vec_seq = rows.get(segs["vector"])
    if meta_seq is None or vec_seq is None:
        return Check(
            name="seq_id_gap",
            status=FAIL,
            detail=f"missing max_seq_id row (meta={meta_seq}, vec={vec_seq})",
            signature="mempalace max_seq_id missing row",
        )
    gap = meta_seq - vec_seq
    if gap >= 50_000:
        return Check(
            name="seq_id_gap",
            status=FAIL,
            detail=f"HNSW vector segment is {gap:,} entries behind metadata",
            value=gap,
            signature=f"mempalace seq_id_gap fail >={gap}",
        )
    if gap >= 1_000:
        return Check(
            name="seq_id_gap",
            status=WARN,
            detail=f"HNSW vector segment is {gap:,} entries behind metadata",
            value=gap,
        )
    return Check(name="seq_id_gap", status=OK, detail=f"gap={gap}", value=gap)


def check_hnsw_divergence(palace_path: str) -> Check:
    """Use chroma backend's read-only probe — same data the merged
    ``_refresh_vector_disabled_flag()`` uses."""
    try:
        from .backends.chroma import hnsw_capacity_status
    except Exception as e:
        return Check(name="hnsw_divergence", status=WARN, detail=f"import failed: {e}")
    try:
        info = hnsw_capacity_status(palace_path)
    except Exception as e:
        return Check(
            name="hnsw_divergence",
            status=WARN,
            detail=f"probe failed: {e}",
        )
    if info.get("diverged"):
        gap = info.get("divergence", 0)
        return Check(
            name="hnsw_divergence",
            status=FAIL,
            detail=info.get("message", "diverged"),
            value=gap,
            signature="mempalace hnsw_divergence",
            extra=info,
        )
    sql_count = info.get("sqlite_count")
    hnsw_count = info.get("hnsw_count")
    sql_str = f"{sql_count:,}" if isinstance(sql_count, int) else "?"
    hnsw_str = f"{hnsw_count:,}" if isinstance(hnsw_count, int) else "?"
    return Check(
        name="hnsw_divergence",
        status=OK,
        detail=f"sqlite={sql_str} hnsw={hnsw_str}",
        value=info.get("divergence", 0),
        extra=info,
    )


def check_wal() -> Check:
    """WAL must be writable for verify-after-write to work."""
    wal_dir = Path.home() / ".mempalace" / "wal"
    wal = wal_dir / "write_log.jsonl"
    if not wal.is_file():
        return Check(name="wal", status=WARN, detail=f"no wal at {wal}")
    if not os.access(wal, os.W_OK):
        return Check(
            name="wal",
            status=FAIL,
            detail=f"wal not writable: {wal}",
            signature="mempalace wal not_writable",
        )
    size = wal.stat().st_size
    return Check(name="wal", status=OK, detail=f"{size:,} bytes", value=size)


# ── runner ────────────────────────────────────────────────────────────────


def run_checks(palace_path: Optional[str] = None, daemon_port: int = DAEMON_PORT_DEFAULT) -> list[Check]:
    """Run every check in the canonical order. Order is stable so monitors
    can diff status across runs without re-sorting."""
    resolved = palace_path or MempalaceConfig().palace_path or ""
    return [
        check_daemon_port(port=daemon_port),
        check_daemon_healthz(port=daemon_port),
        check_launchd_status(),
        check_queue_lag(resolved),
        check_seq_id_gap(resolved),
        check_hnsw_divergence(resolved),
        check_wal(),
    ]


def overall_status(checks: list[Check]) -> str:
    """``fail`` if any FAIL, ``warn`` if any WARN, ``ok`` otherwise."""
    statuses = {c.status for c in checks}
    if FAIL in statuses:
        return FAIL
    if WARN in statuses:
        return WARN
    return OK
