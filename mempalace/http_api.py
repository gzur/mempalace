"""JSON API for the mempalace SPA.

Every endpoint is a thin Starlette handler that calls into existing
``mcp_server.tool_*`` functions, ``palace_sql`` helpers, ``wal_series``,
``health.run_checks``, or the ``KnowledgeGraph`` directly. No business logic
lives here — when a tool's return shape changes, this module needs at most
a key rename.

Routes are mounted by ``http_server.build_app()`` under ``/api/v1/``.

Threading: every handler funnels through ``http_server._render_json`` which
acquires the same ``_dispatch_lock`` and ``_TOOL_EXECUTOR`` as the legacy
``/ui/`` handlers, so the chromadb single-thread invariant holds.
"""

from __future__ import annotations

import dataclasses
import logging
import time
from typing import Any, Callable, Optional

from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from . import mcp_server, palace_sql, wal_series

logger = logging.getLogger(__name__)


def _int_param(request: Request, key: str, default: int) -> int:
    raw = request.query_params.get(key)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _opt_str(request: Request, key: str) -> Optional[str]:
    v = request.query_params.get(key)
    return v if v else None


def _opt_bool(request: Request, key: str, default: bool = False) -> bool:
    v = request.query_params.get(key)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def _json_safe(obj: Any) -> Any:
    """Coerce dataclasses + sqlite Row + other oddities to JSON-friendly types.

    Most ``tool_*`` functions already return dict/list/str/int/float, but
    ``health.run_checks`` returns ``Check`` dataclass instances and a few
    helpers return ``sqlite3.Row``. Centralize that conversion here.
    """
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {k: _json_safe(v) for k, v in dataclasses.asdict(obj).items()}
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    return obj


# ── handlers ──────────────────────────────────────────────────────────────


def _wrap(fn: Callable[[Request], Any]) -> Callable[[Request], Any]:
    """Run a sync handler under the http_server dispatch lock + tool executor.

    Imported lazily so the import cycle (http_server <-> http_api) resolves.
    """

    async def handler(request: Request) -> Response:
        from . import http_server

        return await http_server.render_json(lambda: fn(request))

    return handler


def _wrap_post(fn: Callable[[Request, dict], Any]) -> Callable[[Request], Any]:
    """Sibling of ``_wrap`` for POST endpoints that consume a JSON body.

    Reads + parses the body asynchronously before acquiring the dispatch
    lock so the (single-threaded) executor stays free of network I/O. The
    body falls back to an empty dict on parse failure — handlers should
    pull keys with ``.get(...)`` and return a structured error themselves.
    """

    async def handler(request: Request) -> Response:
        from . import http_server

        try:
            body = await request.json()
        except Exception:
            body = {}
        if not isinstance(body, dict):
            body = {}
        return await http_server.render_json(lambda: fn(request, body))

    return handler


_SUMMARY_CACHE: dict[str, Any] = {"key": None, "value": None, "expires_at": 0.0}
_SUMMARY_TTL = 30.0


def palace_summary(_: Request) -> dict:
    """Drawer / wing / room counts for the dashboard.

    Uses the read-only sqlite path via ``palace_sql.palace_summary`` rather
    than ``mcp_server.tool_status`` because tool_status paginates over the
    whole chromadb collection (~9s on a 91k-drawer palace). The sqlite
    aggregate is ~400ms cold and trivially memoizable.

    A 30s in-process TTL cache hides repeat calls (dashboard polls,
    sidebar reloads) behind a single sqlite roundtrip. The cache key
    incorporates the live palace_path so tests with separate temp
    palaces don't see each other's results.
    """
    now = time.time()
    key = str(getattr(mcp_server._config, "palace_path", ""))
    if (
        _SUMMARY_CACHE["value"] is not None
        and _SUMMARY_CACHE["key"] == key
        and now < _SUMMARY_CACHE["expires_at"]
    ):
        return _SUMMARY_CACHE["value"]
    sql = palace_sql.palace_summary()
    payload = {
        "total_drawers": sql.get("total", 0),
        "wings": sql.get("wings", {}),
        "rooms": sql.get("rooms", {}),
        "protocol": getattr(mcp_server, "PALACE_PROTOCOL", ""),
        "aaak_dialect": getattr(mcp_server, "AAAK_SPEC", ""),
    }
    _SUMMARY_CACHE["key"] = key
    _SUMMARY_CACHE["value"] = payload
    _SUMMARY_CACHE["expires_at"] = now + _SUMMARY_TTL
    return payload


def palace_wings(_: Request) -> dict:
    return mcp_server.tool_list_wings()


def palace_rooms(request: Request) -> dict:
    wing = _opt_str(request, "wing")
    return mcp_server.tool_list_rooms(wing=wing)


def drawers_list(request: Request) -> dict:
    wing = _opt_str(request, "wing")
    room = _opt_str(request, "room")
    limit = _int_param(request, "limit", 20)
    offset = _int_param(request, "offset", 0)
    return mcp_server.tool_list_drawers(wing=wing, room=room, limit=limit, offset=offset)


def drawer_detail(request: Request) -> dict:
    drawer_id = request.path_params["drawer_id"]
    return mcp_server.tool_get_drawer(drawer_id)


def drawer_search(request: Request) -> dict:
    q = request.query_params.get("q", "") or ""
    wing = _opt_str(request, "wing")
    room = _opt_str(request, "room")
    limit = _int_param(request, "limit", 10)
    mode = (request.query_params.get("mode") or "semantic").lower()
    if mode == "fts":
        hits = palace_sql.fts_search(q, wing=wing, room=room, limit=limit)
        return {"mode": "fts", "query": q, "count": len(hits), "results": hits}
    return {
        "mode": "semantic",
        **mcp_server.tool_search(query=q, limit=limit, wing=wing, room=room),
    }


def ops_recent(request: Request) -> dict:
    limit = _int_param(request, "limit", 50)
    return mcp_server.tool_recent_ops(limit=limit)


def ops_series(request: Request) -> dict:
    days = _int_param(request, "days", 30)
    bucket = _opt_str(request, "bucket")
    return wal_series.series_for_chart(days=days, bucket=bucket)


def tunnels_list(request: Request) -> dict:
    wing = _opt_str(request, "wing")
    return mcp_server.tool_list_tunnels(wing=wing)


def diary_read(request: Request) -> dict:
    agent_name = request.query_params.get("agent", "") or ""
    last_n = _int_param(request, "last_n", 10)
    wing = request.query_params.get("wing", "") or ""
    return mcp_server.tool_diary_read(agent_name=agent_name, last_n=last_n, wing=wing)


def kg_stats(_: Request) -> dict:
    return mcp_server.tool_kg_stats()


def kg_entity(request: Request) -> dict:
    name = request.query_params.get("name", "") or ""
    direction = request.query_params.get("direction", "both") or "both"
    as_of = _opt_str(request, "as_of")
    return mcp_server.tool_kg_query(entity=name, as_of=as_of, direction=direction)


def kg_timeline(request: Request) -> dict:
    entity = _opt_str(request, "entity")
    return mcp_server.tool_kg_timeline(entity=entity)


def kg_set_entity_type(request: Request, body: dict) -> dict:
    """Update one entity's type. PATCH /api/v1/kg/entity/{name} {type}."""
    return mcp_server.tool_kg_set_entity_type(
        name=request.path_params.get("name", "") or "",
        entity_type=str(body.get("type", "") or ""),
    )


def kg_merge(_request: Request, body: dict) -> dict:
    """Merge one KG entity into another. POST {from, into}."""
    return mcp_server.tool_kg_merge(
        from_name=str(body.get("from", "") or ""),
        into_name=str(body.get("into", "") or ""),
    )


def kg_graph(request: Request) -> dict:
    include_expired = _opt_bool(request, "include_expired", default=False)
    max_nodes_raw = request.query_params.get("max_nodes")
    max_nodes = int(max_nodes_raw) if max_nodes_raw and max_nodes_raw.isdigit() else None
    as_of = _opt_str(request, "as_of")
    kg = mcp_server._get_kg()
    return kg.graph_dump(include_expired=include_expired, max_nodes=max_nodes, as_of=as_of)


def health_endpoint(_: Request) -> dict:
    from . import health

    checks = health.run_checks(palace_path=mcp_server._config.palace_path)
    return {
        "overall": health.overall_status(checks),
        "checks": [_json_safe(c) for c in checks],
    }


# ── route table ───────────────────────────────────────────────────────────


def routes() -> list[Route]:
    """All /api/v1/* routes. Imported by ``http_server.build_app``."""
    return [
        Route("/api/v1/palace/summary", _wrap(palace_summary), methods=["GET"]),
        Route("/api/v1/palace/wings", _wrap(palace_wings), methods=["GET"]),
        Route("/api/v1/palace/rooms", _wrap(palace_rooms), methods=["GET"]),
        Route("/api/v1/drawers", _wrap(drawers_list), methods=["GET"]),
        Route("/api/v1/drawers/{drawer_id:path}", _wrap(drawer_detail), methods=["GET"]),
        Route("/api/v1/search", _wrap(drawer_search), methods=["GET"]),
        Route("/api/v1/ops", _wrap(ops_recent), methods=["GET"]),
        Route("/api/v1/ops/series", _wrap(ops_series), methods=["GET"]),
        Route("/api/v1/tunnels", _wrap(tunnels_list), methods=["GET"]),
        Route("/api/v1/diary", _wrap(diary_read), methods=["GET"]),
        Route("/api/v1/kg/stats", _wrap(kg_stats), methods=["GET"]),
        Route("/api/v1/kg/entity", _wrap(kg_entity), methods=["GET"]),
        Route("/api/v1/kg/timeline", _wrap(kg_timeline), methods=["GET"]),
        Route("/api/v1/kg/graph", _wrap(kg_graph), methods=["GET"]),
        Route("/api/v1/kg/merge", _wrap_post(kg_merge), methods=["POST"]),
        Route(
            "/api/v1/kg/entity/{name:path}",
            _wrap_post(kg_set_entity_type),
            methods=["PATCH"],
        ),
        Route("/api/v1/health", _wrap(health_endpoint), methods=["GET"]),
    ]


# Re-export for tests
__all__ = ["routes", "_json_safe"]
