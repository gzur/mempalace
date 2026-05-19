"""Read-only HTML pane of glass for the mempalace HTTP daemon.

Rendering helpers only — no Starlette imports here. ``http_server`` wires the
routes and serializes calls through the shared dispatch lock.

We deliberately bypass ``chromadb`` for the read path because the C++ HNSW
binding has segfaulted under the daemon's threading model in this codebase
(see the 2026-05-11 incident). All drawer browsing reads ``chroma.sqlite3``
directly in read-only mode; only the KG views and WAL read still go through
``mcp_server.tool_*`` because those touch separate, safe SQLite paths.
"""

from __future__ import annotations

import datetime as _dt
import html
import json
from collections import OrderedDict, defaultdict
from pathlib import Path
from typing import Any, Iterable, Optional

from . import mcp_server, palace_sql


_CSS = """
*{box-sizing:border-box}
body{font:14px/1.45 ui-monospace,SFMono-Regular,Menlo,monospace;background:#0d1117;color:#c9d1d9;margin:0}
header{background:#161b22;padding:8px 16px;border-bottom:1px solid #30363d;display:flex;gap:14px;align-items:center}
header a{color:#58a6ff;text-decoration:none}
header a:hover{text-decoration:underline}
header .spacer{flex:1}
header .ver{color:#7d8590;font-size:12px}
main{padding:16px;max-width:1200px;margin:0 auto}
h1{font-size:18px;color:#e6edf3;margin:8px 0 4px}
h2{font-size:14px;color:#e6edf3;margin:24px 0 8px;text-transform:uppercase;letter-spacing:0.05em;color:#7d8590}
.breadcrumb{color:#7d8590;font-size:12px;margin-bottom:4px}
.breadcrumb a{color:#58a6ff;text-decoration:none}
.breadcrumb a:hover{text-decoration:underline}
.meta{color:#7d8590;font-size:12px;margin-bottom:8px}
.small{font-size:12px;color:#7d8590}
.num{text-align:right;font-variant-numeric:tabular-nums}
table{border-collapse:collapse;width:100%;margin:8px 0}
th,td{padding:4px 10px;text-align:left;border-bottom:1px solid #21262d;vertical-align:top}
th{background:#161b22;color:#7d8590;font-weight:normal;font-size:11px;text-transform:uppercase;letter-spacing:0.04em}
tr:hover td{background:#161b22}
a{color:#58a6ff;text-decoration:none}
a:hover{text-decoration:underline}
pre{background:#161b22;padding:12px;border:1px solid #21262d;border-radius:6px;overflow-x:auto;white-space:pre-wrap;word-wrap:break-word;font-size:13px}
code{background:#161b22;padding:1px 5px;border-radius:3px;font-size:12px}
form{margin:8px 0;display:flex;gap:6px;flex-wrap:wrap}
input[type=text],input[type=search]{background:#0d1117;border:1px solid #30363d;color:#c9d1d9;padding:6px 8px;font-family:inherit;font-size:13px;border-radius:6px}
input[type=text]:focus,input[type=search]:focus{outline:none;border-color:#58a6ff}
input[type=submit]{background:#238636;border:0;color:#fff;padding:6px 14px;font-family:inherit;font-size:13px;border-radius:6px;cursor:pointer}
input[type=submit]:hover{background:#2ea043}
.pager{margin:12px 0;display:flex;gap:14px}
.pill{display:inline-block;background:#21262d;color:#c9d1d9;padding:1px 6px;border-radius:10px;font-size:11px;margin-right:4px}
.chart{display:block;width:100%;height:auto;background:#161b22;border:1px solid #21262d;border-radius:6px;padding:8px;margin:8px 0}
.chart text{fill:#7d8590;font-size:10px;font-family:ui-monospace,monospace}
.chart .axis{stroke:#30363d;stroke-width:1}
.chart .baseline{stroke:#30363d;stroke-width:1}
.swatch{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:6px;vertical-align:middle}
.grid3{display:grid;grid-template-columns:1fr 1fr 1fr;gap:16px;margin-top:16px}
"""


# ── helpers ───────────────────────────────────────────────────────────────


def _esc(s: Any) -> str:
    return html.escape("" if s is None else str(s))


def _d(x: Any) -> dict:
    return x if isinstance(x, dict) else {}


def _layout(title: str, body: str) -> str:
    return (
        "<!DOCTYPE html>"
        '<html lang="en"><head>'
        '<meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{_esc(title)} · MemPalace</title>"
        f"<style>{_CSS}</style>"
        "</head><body>"
        "<header>"
        '<a href="/ui/">palace</a>'
        '<a href="/ui/search">search</a>'
        '<a href="/ui/recent">recent</a>'
        '<a href="/ui/operations">operations</a>'
        '<a href="/ui/kg">kg</a>'
        '<a href="/ui/health">health</a>'
        '<span class="spacer"></span>'
        f'<span class="ver">v{_esc(mcp_server.__version__)}</span>'
        "</header>"
        f"<main>{body}</main>"
        "</body></html>"
    )


def _crumb(parts: list[tuple[str, Optional[str]]]) -> str:
    items: list[str] = []
    for label, href in parts:
        if href:
            items.append(f'<a href="{_esc(href)}">{_esc(label)}</a>')
        else:
            items.append(_esc(label))
    return f'<div class="breadcrumb">{" / ".join(items)}</div>'


# All chroma.sqlite3 access lives in palace_sql so the read-only MCP tools
# in mcp_server can reuse the same crash-proof bypass (see bead self-mob).


# ── operations table (shared by /ui/ and /ui/recent) ──────────────────────


def _ops_table(ops: Iterable[dict]) -> str:
    ops_list = list(ops)
    if not ops_list:
        return '<div class="meta">no operations recorded yet</div>'
    rows = ["<table><thead><tr><th>ts</th><th>op</th><th>details</th></tr></thead><tbody>"]
    for op in reversed(ops_list):  # newest first
        ts = op.get("ts") or op.get("timestamp") or op.get("filed_at") or ""
        kind = op.get("op") or op.get("operation") or op.get("kind") or ""
        data = op.get("data") if isinstance(op.get("data"), dict) else op
        bits: list[str] = []
        for k in (
            "drawer_id",
            "wing",
            "room",
            "agent_name",
            "topic",
            "old_wing",
            "new_wing",
            "old_room",
            "new_room",
            "predicate",
            "subject",
            "object",
        ):
            v = data.get(k) if isinstance(data, dict) else None
            if v:
                bits.append(f'<span class="pill">{_esc(k)}={_esc(v)}</span>')
        rows.append(
            f'<tr><td class="small">{_esc(ts)}</td><td>{_esc(kind)}</td><td>{"".join(bits)}</td></tr>'
        )
    rows.append("</tbody></table>")
    return "".join(rows)


# ── pages ─────────────────────────────────────────────────────────────────


def render_index() -> str:
    summary = palace_sql.palace_summary()
    wings = sorted(summary["wings"].items(), key=lambda kv: (-kv[1], kv[0]))
    recent = _d(mcp_server.tool_recent_ops(limit=10)).get("operations", [])

    parts: list[str] = []
    parts.append("<h1>palace</h1>")
    parts.append(
        f'<div class="meta">{summary["total"]:,} drawers · '
        f"<code>{_esc(mcp_server._config.palace_path)}</code></div>"
    )

    parts.append(
        '<form action="/ui/search" method="get">'
        '<input type="search" name="q" placeholder="search…" autofocus style="flex:1;min-width:300px">'
        '<input type="submit" value="search">'
        "</form>"
    )

    parts.append("<h2>wings</h2>")
    parts.append('<table><thead><tr><th>wing</th><th class="num">drawers</th></tr></thead><tbody>')
    for w, c in wings:
        parts.append(
            f'<tr><td><a href="/ui/wing/{_esc(w)}">{_esc(w)}</a></td>'
            f'<td class="num">{c:,}</td></tr>'
        )
    parts.append("</tbody></table>")

    parts.append("<h2>recent ops</h2>")
    parts.append(_ops_table(recent))
    parts.append('<div class="small"><a href="/ui/recent">all recent →</a></div>')

    return _layout("palace", "".join(parts))


def render_wing(wing: str) -> str:
    rooms_map = palace_sql.list_rooms(wing)
    rooms = sorted(rooms_map.items(), key=lambda kv: (-kv[1], kv[0]))
    total = sum(c for _, c in rooms)
    parts: list[str] = [_crumb([("palace", "/ui/"), (wing, None)])]
    parts.append(f"<h1>{_esc(wing)}</h1>")
    parts.append(f'<div class="meta">{total:,} drawers · {len(rooms)} rooms</div>')
    parts.append('<table><thead><tr><th>room</th><th class="num">drawers</th></tr></thead><tbody>')
    for r, c in rooms:
        parts.append(
            f'<tr><td><a href="/ui/wing/{_esc(wing)}/{_esc(r)}">{_esc(r)}</a></td>'
            f'<td class="num">{c:,}</td></tr>'
        )
    parts.append("</tbody></table>")
    return _layout(wing, "".join(parts))


def render_room(wing: str, room: str, offset: int, limit: int) -> str:
    offset = max(0, offset)
    limit = max(1, min(200, limit))
    drawers = palace_sql.list_drawers(wing=wing, room=room, offset=offset, limit=limit)
    parts: list[str] = [_crumb([("palace", "/ui/"), (wing, f"/ui/wing/{wing}"), (room, None)])]
    parts.append(f"<h1>{_esc(wing)} / {_esc(room)}</h1>")
    parts.append(
        f'<div class="meta">offset {offset:,} · showing {len(drawers):,} · limit {limit}</div>'
    )
    if drawers:
        parts.append("<table><thead><tr><th>id</th><th>preview</th></tr></thead><tbody>")
        for d in drawers:
            did = d["drawer_id"]
            short_id = did[:48] + ("…" if len(did) > 48 else "")
            parts.append(
                f'<tr><td><a href="/ui/drawer/{_esc(did)}"><code>{_esc(short_id)}</code></a></td>'
                f"<td>{_esc(d['content_preview'])}</td></tr>"
            )
        parts.append("</tbody></table>")
    pager: list[str] = []
    if offset > 0:
        prev_off = max(0, offset - limit)
        pager.append(
            f'<a href="/ui/wing/{_esc(wing)}/{_esc(room)}?offset={prev_off}&limit={limit}">← prev</a>'
        )
    if len(drawers) >= limit:
        pager.append(
            f'<a href="/ui/wing/{_esc(wing)}/{_esc(room)}?offset={offset + limit}&limit={limit}">next →</a>'
        )
    if pager:
        parts.append(f'<div class="pager">{"".join(pager)}</div>')
    return _layout(f"{wing} / {room}", "".join(parts))


def render_drawer(drawer_id: str) -> str:
    detail = palace_sql.get_drawer(drawer_id)
    if detail is None:
        body = _crumb([("palace", "/ui/")]) + (
            f'<h1>drawer not found</h1><div class="meta"><code>{_esc(drawer_id)}</code></div>'
        )
        return _layout("drawer", body)
    wing = detail.get("wing", "") or ""
    room = detail.get("room", "") or ""
    crumb_parts: list[tuple[str, Optional[str]]] = [("palace", "/ui/")]
    if wing:
        crumb_parts.append((wing, f"/ui/wing/{wing}"))
    if wing and room:
        crumb_parts.append((room, f"/ui/wing/{wing}/{room}"))
    crumb_parts.append((drawer_id[:40] + ("…" if len(drawer_id) > 40 else ""), None))
    parts: list[str] = [_crumb(crumb_parts)]
    parts.append("<h1>drawer</h1>")
    parts.append(
        f'<div class="meta"><code>{_esc(drawer_id)}</code> · '
        f"{_esc(wing) or '?'} / {_esc(room) or '?'}</div>"
    )
    parts.append("<h2>content</h2>")
    parts.append(f"<pre>{_esc(detail.get('content', ''))}</pre>")
    parts.append("<h2>metadata</h2>")
    parts.append("<table><tbody>")
    for k, v in sorted((detail.get("metadata") or {}).items()):
        parts.append(f"<tr><th>{_esc(k)}</th><td>{_esc(v)}</td></tr>")
    parts.append("</tbody></table>")
    return _layout("drawer", "".join(parts))


def render_search(q: str, wing: Optional[str], room: Optional[str], limit: int) -> str:
    limit = max(1, min(50, limit))
    parts: list[str] = [_crumb([("palace", "/ui/"), ("search", None)])]
    parts.append("<h1>search</h1>")
    parts.append(
        '<div class="meta">substring search via FTS — not semantic. '
        "For semantic search call <code>mempalace_search</code> via the MCP tool.</div>"
    )
    parts.append(
        '<form action="/ui/search" method="get">'
        f'<input type="search" name="q" value="{_esc(q)}" placeholder="query…" style="flex:1;min-width:240px" autofocus>'
        f'<input type="text" name="limit" value="{limit}" size="3" title="limit">'
        '<input type="submit" value="search">'
        "</form>"
    )
    if not q.strip():
        return _layout("search", "".join(parts))
    try:
        hits = palace_sql.fts_search(q, wing=wing, room=room, limit=limit)
    except Exception as e:
        parts.append(f'<div class="meta">fts error: {_esc(e)}</div>')
        return _layout("search", "".join(parts))
    parts.append(f'<div class="meta">{len(hits)} hits</div>')
    if not hits:
        parts.append('<div class="meta">no hits</div>')
    else:
        parts.append(
            "<table><thead><tr><th>drawer</th><th>wing / room</th><th>snippet</th></tr></thead><tbody>"
        )
        for h in hits:
            did = h.get("drawer_id", "")
            short_id = did[:40] + ("…" if len(did) > 40 else "")
            w = h.get("wing", "")
            r = h.get("room", "")
            parts.append(
                "<tr>"
                f'<td><a href="/ui/drawer/{_esc(did)}"><code>{_esc(short_id)}</code></a></td>'
                f'<td><a href="/ui/wing/{_esc(w)}/{_esc(r)}"><code>{_esc(w)}/{_esc(r)}</code></a></td>'
                f"<td>{_esc(h.get('snippet', ''))}</td>"
                "</tr>"
            )
        parts.append("</tbody></table>")
    return _layout("search", "".join(parts))


def render_recent(limit: int) -> str:
    limit = max(1, min(200, limit))
    resp = _d(mcp_server.tool_recent_ops(limit=limit))
    ops = resp.get("operations", [])
    parts: list[str] = [_crumb([("palace", "/ui/"), ("recent", None)])]
    parts.append(f"<h1>recent ops · last {len(ops)}</h1>")
    parts.append(
        f'<div class="meta">{_esc(resp.get("total_in_log", "?"))} ops in WAL · '
        f'<a href="/ui/recent?limit=50">50</a> · '
        f'<a href="/ui/recent?limit=200">200</a></div>'
    )
    if "error" in resp:
        parts.append(f'<div class="meta">{_esc(resp["error"])}</div>')
    parts.append(_ops_table(ops))
    return _layout("recent", "".join(parts))


def _kg_timeline_table(rows: Iterable[dict]) -> str:
    rows_list = list(rows)
    if not rows_list:
        return '<div class="meta">empty</div>'
    out = [
        "<table><thead><tr>"
        "<th>valid_from</th><th>subject</th><th>predicate</th>"
        "<th>object</th><th>valid_to</th><th>cur</th>"
        "</tr></thead><tbody>"
    ]
    for r in rows_list:
        sub = r.get("subject", "") or ""
        pred = r.get("predicate", "") or ""
        obj = r.get("object", "") or ""
        vf = r.get("valid_from", "")
        vt = r.get("valid_to", "")
        cur = "✓" if r.get("current") else ""
        out.append(
            "<tr>"
            f'<td class="small">{_esc(vf)}</td>'
            f'<td><a href="/ui/kg/entity?name={_esc(sub)}">{_esc(sub)}</a></td>'
            f"<td>{_esc(pred)}</td>"
            f'<td><a href="/ui/kg/entity?name={_esc(obj)}">{_esc(obj)}</a></td>'
            f'<td class="small">{_esc(vt)}</td>'
            f"<td>{cur}</td>"
            "</tr>"
        )
    out.append("</tbody></table>")
    return "".join(out)


def render_kg() -> str:
    stats = _d(mcp_server.tool_kg_stats())
    tl = _d(mcp_server.tool_kg_timeline()).get("timeline", []) or []
    parts: list[str] = [_crumb([("palace", "/ui/"), ("kg", None)])]
    parts.append("<h1>knowledge graph</h1>")
    parts.append("<table><tbody>")
    parts.append(f'<tr><th>entities</th><td class="num">{stats.get("entities", 0):,}</td></tr>')
    parts.append(f'<tr><th>triples</th><td class="num">{stats.get("triples", 0):,}</td></tr>')
    parts.append(
        f'<tr><th>current facts</th><td class="num">{stats.get("current_facts", 0):,}</td></tr>'
    )
    parts.append(
        f'<tr><th>expired facts</th><td class="num">{stats.get("expired_facts", 0):,}</td></tr>'
    )
    parts.append("</tbody></table>")
    parts.append(
        '<form action="/ui/kg/entity" method="get">'
        '<input type="text" name="name" placeholder="entity name…" style="flex:1;min-width:240px" autofocus>'
        '<input type="submit" value="open">'
        "</form>"
    )
    preds = stats.get("relationship_types", []) or []
    if preds:
        parts.append("<h2>predicates</h2>")
        parts.append(
            '<div class="meta">' + ", ".join(f"<code>{_esc(p)}</code>" for p in preds) + "</div>"
        )
    parts.append("<h2>recent timeline</h2>")
    parts.append(_kg_timeline_table(tl))
    return _layout("kg", "".join(parts))


def render_kg_entity(name: str) -> str:
    parts: list[str] = [_crumb([("palace", "/ui/"), ("kg", "/ui/kg"), (name or "?", None)])]
    parts.append(f"<h1>kg · {_esc(name) or 'entity'}</h1>")
    if not name.strip():
        parts.append('<div class="meta">no entity name supplied</div>')
        return _layout("kg entity", "".join(parts))
    q = _d(mcp_server.tool_kg_query(entity=name, direction="both"))
    if "error" in q:
        parts.append(f'<div class="meta">{_esc(q["error"])}</div>')
        return _layout("kg entity", "".join(parts))
    facts = q.get("facts", []) or []
    parts.append(f'<div class="meta">{len(facts)} facts</div>')
    if facts:
        parts.append(
            "<table><thead><tr>"
            "<th>dir</th><th>subject</th><th>predicate</th>"
            "<th>object</th><th>valid_from</th><th>valid_to</th>"
            "</tr></thead><tbody>"
        )
        for f in facts:
            sub = f.get("subject", "") or ""
            obj = f.get("object", "") or ""
            parts.append(
                "<tr>"
                f'<td class="small">{_esc(f.get("direction", ""))}</td>'
                f'<td><a href="/ui/kg/entity?name={_esc(sub)}">{_esc(sub)}</a></td>'
                f"<td>{_esc(f.get('predicate', ''))}</td>"
                f'<td><a href="/ui/kg/entity?name={_esc(obj)}">{_esc(obj)}</a></td>'
                f'<td class="small">{_esc(f.get("valid_from", ""))}</td>'
                f'<td class="small">{_esc(f.get("valid_to", ""))}</td>'
                "</tr>"
            )
        parts.append("</tbody></table>")
    parts.append("<h2>timeline</h2>")
    tl = _d(mcp_server.tool_kg_timeline(entity=name)).get("timeline", []) or []
    parts.append(_kg_timeline_table(tl))
    return _layout(f"kg · {name}", "".join(parts))


# ── health dashboard ──────────────────────────────────────────────────────


_STATUS_COLORS = {
    "ok": "#3fb950",
    "warn": "#d29922",
    "fail": "#f85149",
}


def render_health() -> str:
    from . import health

    checks = health.run_checks()
    overall = health.overall_status(checks)
    parts: list[str] = [_crumb([("palace", "/ui/"), ("health", None)])]
    parts.append("<h1>health</h1>")
    overall_color = _STATUS_COLORS.get(overall, "#7d8590")
    badge_text = {"ok": "ALL GOOD", "warn": "WARNINGS", "fail": "FAILURES"}[overall]
    parts.append(
        f'<div class="meta" style="font-size:14px">'
        f'<span style="display:inline-block;background:{overall_color};color:#0d1117;'
        f'padding:2px 10px;border-radius:10px;font-weight:600">{badge_text}</span>'
        f"</div>"
    )
    parts.append(
        "<table><thead><tr><th>check</th><th>status</th><th>detail</th></tr></thead><tbody>"
    )
    for c in checks:
        color = _STATUS_COLORS.get(c.status, "#7d8590")
        parts.append(
            "<tr>"
            f"<td><code>{_esc(c.name)}</code></td>"
            f'<td><span style="display:inline-block;background:{color};color:#0d1117;'
            f'padding:1px 6px;border-radius:8px;font-size:11px;text-transform:uppercase">'
            f"{_esc(c.status)}</span></td>"
            f"<td>{_esc(c.detail)}</td>"
            "</tr>"
        )
    parts.append("</tbody></table>")
    parts.append(
        '<div class="small">'
        "Checks live in <code>mempalace.health</code>. "
        "Run from the shell with <code>mempalace doctor</code> (exit code 0=ok, 1=warn, 2=fail). "
        "The same checks back the launchd watcher at "
        "<code>scripts/cloud.skyvafnir.mempalace.monitor.plist</code>."
        "</div>"
    )
    return _layout("health", "".join(parts))


# ── /ui/operations ────────────────────────────────────────────────────────
#
# Time-series chart of WAL operations. WAL is ``~/.mempalace/wal/write_log.jsonl``,
# the same file ``mcp_server.tool_recent_ops`` reads at :1140.

_WAL_PATH = Path("~/.mempalace/wal/write_log.jsonl").expanduser()

_OP_TYPES = ("add_drawer", "diary_write", "update_drawer", "kg_add", "delete_drawer")

_OPS_PALETTE = {
    "add_drawer": "#58a6ff",
    "diary_write": "#3fb950",
    "update_drawer": "#d29922",
    "kg_add": "#bc8cff",
    "delete_drawer": "#f85149",
}


def _load_wal_ops(path: Path, since_days: int, bucket: str = "day") -> list[dict]:
    """Stream the WAL, parse, filter to the last N days. Tolerant of malformed lines.

    The cutoff is aligned to the bucket boundary so the loaded ops match
    exactly the date range the chart will render: midnight for daily buckets,
    top-of-hour for hourly buckets. Without alignment the total counts ops
    from a partial leading bucket that the chart drops, producing the
    "chart says 3, total says 18" mismatch on short ranges.

    Mirrors the malformed-line tolerance from ``mcp_server.tool_recent_ops``: a bad
    line is skipped, not raised, so a half-written final line during daemon crash
    doesn't take down the chart.
    """
    if not path.is_file():
        return []
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


def _bucket_key(dt: _dt.datetime, bucket: str) -> str:
    if bucket == "hour":
        return dt.strftime("%Y-%m-%d %H:00")
    return dt.strftime("%Y-%m-%d")


def _bucket_ops(ops: list[dict], bucket: str) -> "OrderedDict[str, dict[str, int]]":
    """Group ops by (time-bucket, op_type). Returns ordered dict (oldest first)."""
    out: "OrderedDict[str, dict[str, int]]" = OrderedDict()
    for op in ops:
        key = _bucket_key(op["_dt"], bucket)
        if key not in out:
            out[key] = {t: 0 for t in _OP_TYPES}
            out[key]["_other"] = 0
        kind = op.get("operation") or "_other"
        if kind in out[key]:
            out[key][kind] += 1
        else:
            out[key]["_other"] += 1
    return out


def _fill_bucket_range(
    binned: "OrderedDict[str, dict[str, int]]",
    bucket: str,
    days: int,
) -> "OrderedDict[str, dict[str, int]]":
    """Insert empty buckets so the x-axis has the full requested range, not just
    the buckets with data. Hourly: last ``days * 24`` hours. Daily: last ``days``
    days. The result is a NEW ordered dict; the input is not mutated.
    """
    empty = {t: 0 for t in _OP_TYPES}
    empty["_other"] = 0
    out: "OrderedDict[str, dict[str, int]]" = OrderedDict()
    now = _dt.datetime.now()
    if bucket == "hour":
        steps = days * 24
        for i in range(steps - 1, -1, -1):
            t = now - _dt.timedelta(hours=i)
            t = t.replace(minute=0, second=0, microsecond=0)
            key = _bucket_key(t, bucket)
            out[key] = dict(binned.get(key, empty))
    else:
        for i in range(days - 1, -1, -1):
            t = now - _dt.timedelta(days=i)
            key = _bucket_key(t, bucket)
            out[key] = dict(binned.get(key, empty))
    return out


def _render_range_form(days: int, bucket: str) -> str:
    days_opts = [1, 7, 30, 90, 365]
    bucket_opts = [("auto", ""), ("hour", "hour"), ("day", "day")]
    days_html = "".join(
        f'<option value="{d}"{" selected" if d == days else ""}>{d}d</option>' for d in days_opts
    )
    bucket_html = "".join(
        f'<option value="{v}"{" selected" if v == bucket else ""}>{label}</option>'
        for label, v in bucket_opts
    )
    return (
        '<form method="get">'
        '<label class="small">range '
        f'<select name="days">{days_html}</select></label> '
        '<label class="small">bucket '
        f'<select name="bucket">{bucket_html}</select></label> '
        '<input type="submit" value="apply">'
        "</form>"
    )


def _render_ops_chart_svg(
    binned: "OrderedDict[str, dict[str, int]]",
    op_types: tuple[str, ...],
    bucket: str,
) -> str:
    if not binned:
        return '<div class="meta">no operations recorded in this range</div>'

    keys = list(binned.keys())
    n = len(keys)
    totals = [sum(binned[k][t] for t in op_types) + binned[k].get("_other", 0) for k in keys]
    y_max = max(totals) if any(totals) else 1

    # SVG layout — keep the plot area at a fixed minimum width regardless of
    # bar count, otherwise short ranges (1d, 7d) produce a tiny viewBox that
    # the browser then stretches to fill the container, blowing up text and
    # single bars to fullscreen size. preserveAspectRatio default keeps text
    # at its declared font-size relative to the viewBox.
    pad_l, pad_r, pad_t, pad_b = 36, 8, 12, 28
    bar_w_max = 24
    bar_w = max(2, min(bar_w_max, (700 - pad_l - pad_r) // max(n, 1)))
    bar_gap = max(1, bar_w // 6)
    plot_w_min = 700
    cluster_w = bar_w * n
    plot_w = max(plot_w_min, cluster_w)
    width = pad_l + plot_w + pad_r
    height = 220
    plot_h = height - pad_t - pad_b
    # Centre the bar cluster within the plot area when it does not fill it
    # (short ranges). For long ranges the cluster equals plot_w, so x_start
    # collapses back to pad_l.
    x_start = pad_l + (plot_w - cluster_w) // 2

    rects: list[str] = []
    overlays: list[str] = []
    for i, k in enumerate(keys):
        x = x_start + i * bar_w + bar_gap // 2
        col_w = bar_w - bar_gap
        running = 0
        col_total = 0
        breakdown_lines: list[str] = []
        for t in op_types:
            count = binned[k].get(t, 0)
            col_total += count
            if count > 0:
                breakdown_lines.append(f"{t}: {count}")
            if count <= 0:
                continue
            h = (count / y_max) * plot_h
            y = pad_t + plot_h - (running + h)
            running += h
            rects.append(
                f'<rect x="{x}" y="{y:.1f}" width="{col_w}" height="{h:.1f}" '
                f'fill="{_OPS_PALETTE[t]}">'
                f"<title>{_esc(k)} · {_esc(t)}: {count}</title>"
                "</rect>"
            )
        # Full-column hover overlay: gives a big hit target so the native SVG
        # tooltip fires even on empty buckets and stacked sub-bars. Browsers
        # only show <title> tooltips after the cursor stops moving over the
        # element, and stacked rects only let the topmost slice receive that
        # hover — the overlay collapses all of it to one column-wide target
        # with a rich, multi-line summary.
        if col_total > 0:
            tip = f"{k} · total {col_total}\n" + "\n".join(breakdown_lines)
        else:
            tip = f"{k} · no ops"
        overlays.append(
            f'<rect x="{x}" y="{pad_t}" width="{col_w}" height="{plot_h}" '
            f'fill="transparent" pointer-events="all">'
            f"<title>{_esc(tip)}</title></rect>"
        )

    # axis + labels
    axis_y0 = pad_t + plot_h
    axes = [
        f'<line class="baseline" x1="{pad_l}" y1="{axis_y0}" x2="{width - pad_r}" y2="{axis_y0}"/>'
    ]
    for frac, label in ((0, 0), (0.5, y_max / 2), (1.0, y_max)):
        y = pad_t + plot_h - frac * plot_h
        axes.append(f'<line class="axis" x1="{pad_l}" y1="{y}" x2="{width - pad_r}" y2="{y}"/>')
        axes.append(f'<text x="{pad_l - 6}" y="{y + 3}" text-anchor="end">{label:g}</text>')

    # tick labels: first, middle, last; skip rest to avoid overlap
    tick_positions = sorted({0, n // 2, n - 1}) if n > 1 else [0]
    for ti in tick_positions:
        cx = x_start + ti * bar_w + bar_w / 2
        text = keys[ti]
        if bucket == "hour":
            text = text[5:]  # "MM-DD HH:00"
        axes.append(
            f'<text x="{cx:.1f}" y="{height - pad_b + 14}" text-anchor="middle">{_esc(text)}</text>'
        )

    return (
        f'<svg class="chart" viewBox="0 0 {width} {height}" '
        f'role="img" aria-label="operations over time">'
        + "".join(axes)
        + "".join(rects)
        + "".join(overlays)
        + "</svg>"
    )


def _wing_for_op(op: dict) -> Optional[str]:
    """Best-effort wing extraction for the wing-breakdown table."""
    params = op.get("params") or {}
    if not isinstance(params, dict):
        return None
    for k in ("wing", "new_wing", "old_wing"):
        v = params.get(k)
        if v:
            return str(v)
    agent = params.get("agent_name")
    if agent:
        return f"agents/{agent}"
    return None


def _render_ops_summary(
    ops: list[dict],
    binned: "OrderedDict[str, dict[str, int]]",
    op_types: tuple[str, ...],
    days: int,
) -> str:
    total = len(ops)
    avg_per_day = total / max(days, 1)
    # busiest bucket
    sums = [(k, sum(v[t] for t in op_types) + v.get("_other", 0)) for k, v in binned.items()]
    sums = [s for s in sums if s[1] > 0]
    busiest_k, busiest_n = max(sums, key=lambda s: s[1]) if sums else ("—", 0)

    # by op type
    per_type: dict[str, int] = {t: 0 for t in op_types}
    other = 0
    for op in ops:
        k = op.get("operation")
        if k in per_type:
            per_type[k] += 1
        else:
            other += 1

    # by wing (top 10)
    wing_counts: dict[str, int] = defaultdict(int)
    for op in ops:
        w = _wing_for_op(op)
        if w:
            wing_counts[w] += 1
    top_wings = sorted(wing_counts.items(), key=lambda kv: (-kv[1], kv[0]))[:10]

    # totals block
    totals_html = (
        "<div>"
        "<h2>totals</h2>"
        "<table><tbody>"
        f"<tr><td>total</td><td class='num'>{total:,}</td></tr>"
        f"<tr><td>avg / day</td><td class='num'>{avg_per_day:.1f}</td></tr>"
        f"<tr><td>busiest</td><td class='num'>{_esc(busiest_k)} ({busiest_n})</td></tr>"
        "</tbody></table>"
        "</div>"
    )

    # by-op block
    op_rows: list[str] = []
    for t in op_types:
        n = per_type[t]
        pct = (100.0 * n / total) if total else 0.0
        swatch = f'<span class="swatch" style="background:{_OPS_PALETTE[t]}"></span>'
        op_rows.append(
            f"<tr><td>{swatch}<code>{_esc(t)}</code></td>"
            f"<td class='num'>{n:,}</td>"
            f"<td class='num small'>{pct:.0f}%</td></tr>"
        )
    if other:
        op_rows.append(
            f"<tr><td><code>other</code></td><td class='num'>{other:,}</td>"
            f"<td class='num small'>{(100.0 * other / total):.0f}%</td></tr>"
        )
    by_op_html = (
        "<div><h2>by operation</h2>"
        "<table><thead><tr><th>op</th><th class='num'>n</th><th class='num'>%</th></tr></thead>"
        f"<tbody>{''.join(op_rows)}</tbody></table></div>"
    )

    # by-wing block
    if top_wings:
        wing_rows = "".join(
            f"<tr><td>{_esc(w)}</td><td class='num'>{n:,}</td></tr>" for w, n in top_wings
        )
        by_wing_html = (
            "<div><h2>top wings</h2>"
            "<table><thead><tr><th>wing</th><th class='num'>n</th></tr></thead>"
            f"<tbody>{wing_rows}</tbody></table></div>"
        )
    else:
        by_wing_html = '<div><h2>top wings</h2><div class="meta">no wing info</div></div>'

    return f'<div class="grid3">{totals_html}{by_op_html}{by_wing_html}</div>'


def render_operations(days: int = 30, bucket: Optional[str] = None) -> str:
    days = max(1, min(int(days), 365))
    if bucket not in ("hour", "day"):
        bucket = "hour" if days <= 2 else "day"

    ops = _load_wal_ops(_WAL_PATH, since_days=days, bucket=bucket)
    raw_binned = _bucket_ops(ops, bucket)
    binned = _fill_bucket_range(raw_binned, bucket, days)

    parts: list[str] = [_crumb([("palace", "/ui/"), ("operations", None)])]
    parts.append("<h1>operations</h1>")
    parts.append(
        f'<div class="meta">{len(ops):,} ops in last {days}d · bucket={bucket} · '
        f"source <code>{_esc(str(_WAL_PATH))}</code></div>"
    )
    parts.append(_render_range_form(days, bucket))
    parts.append(_render_ops_chart_svg(binned, _OP_TYPES, bucket))
    parts.append(_render_ops_summary(ops, binned, _OP_TYPES, days))
    return _layout("operations", "".join(parts))
