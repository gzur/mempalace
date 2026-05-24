"""Pure WAL-series helpers: load, bucket, fill.

Shared between the legacy server-rendered chart in ``web_ui.render_operations``
and the JSON API at ``/api/v1/ops/series``. No rendering here.

The WAL is the file ``mcp_server.tool_recent_ops`` reads at
``~/.mempalace/wal/write_log.jsonl``.
"""

from __future__ import annotations

import datetime as _dt
import json
from collections import OrderedDict
from pathlib import Path
from typing import Optional

DEFAULT_WAL_PATH = Path("~/.mempalace/wal/write_log.jsonl").expanduser()

OP_TYPES: tuple[str, ...] = (
    "add_drawer",
    "diary_write",
    "update_drawer",
    "kg_add",
    "delete_drawer",
)

OPS_PALETTE: dict[str, str] = {
    "add_drawer": "#58a6ff",
    "diary_write": "#3fb950",
    "update_drawer": "#d29922",
    "kg_add": "#bc8cff",
    "delete_drawer": "#f85149",
}


def load_wal_ops(
    path: Path,
    since_days: int,
    bucket: str = "day",
    now: Optional[_dt.datetime] = None,
) -> list[dict]:
    """Stream the WAL, parse, filter to the last N days. Tolerant of malformed lines.

    The cutoff is aligned to the bucket boundary so the loaded ops match
    exactly the date range the chart renders: midnight for daily buckets,
    top-of-hour for hourly buckets. Without alignment, the total counts ops
    from a partial leading bucket that the chart drops, producing the
    "chart says 3, total says 18" mismatch on short ranges.

    A bad line is skipped, not raised, so a half-written final line during
    a daemon crash doesn't take down the chart.

    ``now`` is overridable for tests; defaults to ``datetime.now()``.
    """
    if not path.is_file():
        return []
    if now is None:
        now = _dt.datetime.now()
    if bucket == "hour":
        cutoff = (now - _dt.timedelta(hours=since_days * 24 - 1)).replace(
            minute=0, second=0, microsecond=0
        )
    else:
        cutoff = (now - _dt.timedelta(days=since_days - 1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
    out: list[dict] = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts = rec.get("timestamp") or ""
                try:
                    dt = _dt.datetime.fromisoformat(ts)
                except (TypeError, ValueError):
                    continue
                if dt < cutoff:
                    continue
                rec["_dt"] = dt
                out.append(rec)
    except OSError:
        return []
    out.sort(key=lambda r: r["_dt"])
    return out


def bucket_key(dt: _dt.datetime, bucket: str) -> str:
    if bucket == "hour":
        return dt.strftime("%Y-%m-%d %H:00")
    return dt.strftime("%Y-%m-%d")


def bucket_ops(ops: list[dict], bucket: str) -> "OrderedDict[str, dict[str, int]]":
    """Group ops by (time-bucket, op_type). Returns ordered dict (oldest first)."""
    out: "OrderedDict[str, dict[str, int]]" = OrderedDict()
    for op in ops:
        key = bucket_key(op["_dt"], bucket)
        if key not in out:
            out[key] = {t: 0 for t in OP_TYPES}
            out[key]["_other"] = 0
        kind = op.get("operation") or "_other"
        if kind in out[key]:
            out[key][kind] += 1
        else:
            out[key]["_other"] += 1
    return out


def fill_bucket_range(
    binned: "OrderedDict[str, dict[str, int]]",
    bucket: str,
    days: int,
    now: Optional[_dt.datetime] = None,
) -> "OrderedDict[str, dict[str, int]]":
    """Insert empty buckets so the x-axis spans the full requested range.

    Hourly: last ``days * 24`` hours. Daily: last ``days`` days. The result
    is a NEW ordered dict; the input is not mutated.

    ``now`` is overridable for tests; defaults to ``datetime.now()``.
    """
    empty = {t: 0 for t in OP_TYPES}
    empty["_other"] = 0
    out: "OrderedDict[str, dict[str, int]]" = OrderedDict()
    if now is None:
        now = _dt.datetime.now()
    if bucket == "hour":
        steps = days * 24
        for i in range(steps - 1, -1, -1):
            t = now - _dt.timedelta(hours=i)
            t = t.replace(minute=0, second=0, microsecond=0)
            key = bucket_key(t, bucket)
            out[key] = dict(binned.get(key, empty))
    else:
        for i in range(days - 1, -1, -1):
            t = now - _dt.timedelta(days=i)
            key = bucket_key(t, bucket)
            out[key] = dict(binned.get(key, empty))
    return out


def auto_bucket(days: int) -> str:
    """Default bucket choice when caller did not specify one."""
    return "hour" if days <= 2 else "day"


def series_for_chart(
    path: Path = DEFAULT_WAL_PATH,
    days: int = 30,
    bucket: Optional[str] = None,
    now: Optional[_dt.datetime] = None,
) -> dict:
    """One-call convenience used by the API endpoint.

    Returns ``{ops, binned, bucket, days, op_types, palette}`` with binned
    as a list of ``{key, counts}`` so JSON encoders don't have to special-case
    OrderedDict.
    """
    days = max(1, min(int(days), 365))
    if bucket not in ("hour", "day"):
        bucket = auto_bucket(days)
    ops = load_wal_ops(path, since_days=days, bucket=bucket, now=now)
    raw = bucket_ops(ops, bucket)
    binned = fill_bucket_range(raw, bucket, days, now=now)
    serial = [{"key": k, "counts": v} for k, v in binned.items()]
    return {
        "days": days,
        "bucket": bucket,
        "total_ops": len(ops),
        "op_types": list(OP_TYPES),
        "palette": dict(OPS_PALETTE),
        "buckets": serial,
    }
