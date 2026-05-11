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

import html
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
        '<a href="/ui/kg">kg</a>'
        '<a href="/healthz">health</a>'
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
    rows = ['<table><thead><tr><th>ts</th><th>op</th><th>details</th></tr></thead><tbody>']
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
        f'<code>{_esc(mcp_server._config.palace_path)}</code></div>'
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
    parts.append(
        '<table><thead><tr><th>room</th><th class="num">drawers</th></tr></thead><tbody>'
    )
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
    parts: list[str] = [
        _crumb([("palace", "/ui/"), (wing, f"/ui/wing/{wing}"), (room, None)])
    ]
    parts.append(f"<h1>{_esc(wing)} / {_esc(room)}</h1>")
    parts.append(
        f'<div class="meta">offset {offset:,} · showing {len(drawers):,} · limit {limit}</div>'
    )
    if drawers:
        parts.append(
            '<table><thead><tr><th>id</th><th>preview</th></tr></thead><tbody>'
        )
        for d in drawers:
            did = d["drawer_id"]
            short_id = did[:48] + ("…" if len(did) > 48 else "")
            parts.append(
                f'<tr><td><a href="/ui/drawer/{_esc(did)}"><code>{_esc(short_id)}</code></a></td>'
                f'<td>{_esc(d["content_preview"])}</td></tr>'
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
            "<h1>drawer not found</h1>"
            f'<div class="meta"><code>{_esc(drawer_id)}</code></div>'
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
            '<table><thead><tr><th>drawer</th><th>wing / room</th><th>snippet</th></tr></thead><tbody>'
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
                f'<td>{_esc(h.get("snippet", ""))}</td>'
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
    parts.append(
        f'<tr><th>entities</th><td class="num">{stats.get("entities", 0):,}</td></tr>'
    )
    parts.append(
        f'<tr><th>triples</th><td class="num">{stats.get("triples", 0):,}</td></tr>'
    )
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
            '<div class="meta">'
            + ", ".join(f"<code>{_esc(p)}</code>" for p in preds)
            + "</div>"
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
                f'<td>{_esc(f.get("predicate", ""))}</td>'
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
