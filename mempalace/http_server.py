"""HTTP transport for the mempalace MCP server.

Wraps the existing stdio JSON-RPC dispatcher (``mcp_server.handle_request``) in a
thin Starlette ASGI app so a single daemon process can serve every Claude session
instead of each spawning its own stdio subprocess. One HNSW load, one writer,
one log file.

Dispatch is funneled through a single ``asyncio.Lock`` and offloaded to the
default executor so the module-level caches in ``mcp_server`` (client,
collection, metadata at :99-125,283) keep their single-threaded invariants.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextvars
import logging
from typing import Callable, Optional

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Route

from . import mcp_server, web_ui

logger = logging.getLogger(__name__)

_dispatch_lock = asyncio.Lock()
_session_id_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "session_id", default=None
)

# Pin every tool call to ONE OS thread. ChromaDB's HNSW C++ bindings are not
# thread-safe; calling ``col.count()`` / ``col.get()`` from a thread other
# than the one that loaded the index segfaults the process. Funnelling all
# work through a single-worker pool gives every tool the same thread that
# initialised the collection on first use.
_TOOL_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
    max_workers=1, thread_name_prefix="mempalace-tool"
)


def current_session_id() -> str:
    """Return the session id of the in-flight HTTP request, or 'anonymous'."""
    return _session_id_var.get() or "anonymous"


async def mcp_endpoint(request: Request) -> Response:
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            {"jsonrpc": "2.0", "error": {"code": -32700, "message": "Parse error"}, "id": None},
            status_code=400,
        )

    session_id = request.headers.get("X-Session-Id") or "anonymous"
    token = _session_id_var.set(session_id)
    try:
        # asyncio's ThreadPoolExecutor does not auto-propagate contextvars;
        # capture the context after setting the var so the executor thread
        # sees the session id when handlers call current_session_id().
        ctx = contextvars.copy_context()
        async with _dispatch_lock:
            response = await asyncio.get_running_loop().run_in_executor(
                _TOOL_EXECUTOR, ctx.run, mcp_server.handle_request, body
            )
    finally:
        _session_id_var.reset(token)

    if response is None:
        return Response(status_code=204)
    return JSONResponse(response)


async def healthz(_: Request) -> Response:
    return JSONResponse({"ok": True, "version": mcp_server.__version__})


# ── read-only HTML pane of glass ──────────────────────────────────────────
#
# Routes funnel through the same ``_dispatch_lock`` as ``/mcp`` so the
# module-level caches in ``mcp_server`` stay single-threaded. The rendering
# functions in ``web_ui`` call read-only tool functions only.


async def _render(fn: Callable[[], str]) -> Response:
    ctx = contextvars.copy_context()
    try:
        async with _dispatch_lock:
            body = await asyncio.get_running_loop().run_in_executor(
                _TOOL_EXECUTOR, ctx.run, fn
            )
        return HTMLResponse(body)
    except Exception:
        logger.exception("ui render failed")
        return HTMLResponse(
            "<h1>render error</h1><p>see ~/.mempalace/logs/daemon.log</p>",
            status_code=500,
        )


async def ui_index(_: Request) -> Response:
    return await _render(web_ui.render_index)


async def ui_wing(request: Request) -> Response:
    wing = request.path_params["wing"]
    return await _render(lambda: web_ui.render_wing(wing))


async def ui_room(request: Request) -> Response:
    wing = request.path_params["wing"]
    room = request.path_params["room"]
    try:
        offset = int(request.query_params.get("offset", "0"))
    except ValueError:
        offset = 0
    try:
        limit = int(request.query_params.get("limit", "50"))
    except ValueError:
        limit = 50
    return await _render(lambda: web_ui.render_room(wing, room, offset, limit))


async def ui_drawer(request: Request) -> Response:
    drawer_id = request.path_params["drawer_id"]
    return await _render(lambda: web_ui.render_drawer(drawer_id))


async def ui_search(request: Request) -> Response:
    q = request.query_params.get("q", "")
    wing = request.query_params.get("wing", "") or None
    room = request.query_params.get("room", "") or None
    try:
        limit = int(request.query_params.get("limit", "10"))
    except ValueError:
        limit = 10
    return await _render(lambda: web_ui.render_search(q, wing, room, limit))


async def ui_recent(request: Request) -> Response:
    try:
        limit = int(request.query_params.get("limit", "50"))
    except ValueError:
        limit = 50
    return await _render(lambda: web_ui.render_recent(limit))


async def ui_kg(_: Request) -> Response:
    return await _render(web_ui.render_kg)


async def ui_kg_entity(request: Request) -> Response:
    name = request.query_params.get("name", "")
    return await _render(lambda: web_ui.render_kg_entity(name))


async def ui_health(_: Request) -> Response:
    return await _render(web_ui.render_health)


async def root(_: Request) -> Response:
    return RedirectResponse("/ui/", status_code=307)


def build_app() -> Starlette:
    return Starlette(
        routes=[
            Route("/", root, methods=["GET"]),
            Route("/mcp", mcp_endpoint, methods=["POST"]),
            Route("/healthz", healthz, methods=["GET"]),
            Route("/ui/", ui_index, methods=["GET"]),
            Route("/ui/wing/{wing}", ui_wing, methods=["GET"]),
            Route("/ui/wing/{wing}/{room}", ui_room, methods=["GET"]),
            Route("/ui/drawer/{drawer_id:path}", ui_drawer, methods=["GET"]),
            Route("/ui/search", ui_search, methods=["GET"]),
            Route("/ui/recent", ui_recent, methods=["GET"]),
            Route("/ui/kg", ui_kg, methods=["GET"]),
            Route("/ui/kg/entity", ui_kg_entity, methods=["GET"]),
            Route("/ui/health", ui_health, methods=["GET"]),
        ]
    )
