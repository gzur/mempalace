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
from pathlib import Path
from typing import Any, Callable, Optional

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from . import http_api, mcp_server

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


async def render_json(fn: Callable[[], Any]) -> Response:
    """JSON sibling of ``_render``: same lock + executor, dict-out.

    Exposed (no leading underscore) so ``http_api`` can use it without
    triggering a circular module-private import.
    """
    ctx = contextvars.copy_context()
    try:
        async with _dispatch_lock:
            body = await asyncio.get_running_loop().run_in_executor(_TOOL_EXECUTOR, ctx.run, fn)
        if isinstance(body, Response):
            return body
        return JSONResponse(http_api._json_safe(body))
    except Exception:
        logger.exception("api render failed")
        return JSONResponse(
            {"error": "internal", "detail": "see ~/.mempalace/logs/daemon.log"},
            status_code=500,
        )


async def root(_: Request) -> Response:
    return RedirectResponse("/app/", status_code=307)


# ── /ui/* legacy redirects ────────────────────────────────────────────────
#
# The server-rendered HTML pane was replaced by the SPA at /app/ (the
# "Phase 6 cutover"). The handler below 301s every old URL to its new
# /app/ equivalent so external bookmarks, beads links, and prior
# screenshots keep resolving. Once nothing in the wild references /ui/
# anymore the whole block can disappear.

_LEGACY_LEAVES = {
    "search": "/app/search",
    "recent": "/app/ops",
    "operations": "/app/ops",
    "kg": "/app/kg",
    "health": "/app/health",
}


def _legacy_to_app(path: str, query: str) -> str:
    """Translate a stripped ``/ui/<path>?<query>`` to the new ``/app`` URL.

    Always returns the final URL including query string. The caller does
    not need to append anything.
    """
    if path in ("", "/"):
        return _with_query("/app/", query)
    parts = path.strip("/").split("/")
    head = parts[0]
    if head == "wing" and len(parts) == 2:
        return _with_query(f"/app/wings/{parts[1]}", query)
    if head == "wing" and len(parts) >= 3:
        return _with_query(f"/app/wings/{parts[1]}/{parts[2]}", query)
    if head == "drawer" and len(parts) >= 2:
        return _with_query("/app/drawers/" + "/".join(parts[1:]), query)
    if head == "kg" and len(parts) >= 2 and parts[1] == "entity":
        # ?name=Alice → /app/kg/Alice. Strip the consumed param.
        from urllib.parse import parse_qs, urlencode

        params = parse_qs(query, keep_blank_values=True)
        name_list = params.pop("name", None)
        name = name_list[0] if name_list else ""
        remaining = urlencode(params, doseq=True)
        target = f"/app/kg/{name}" if name else "/app/kg"
        return target + (f"?{remaining}" if remaining else "")
    if head == "kg" and len(parts) >= 2 and parts[1] == "graph":
        return _with_query("/app/kg", query)
    if head in _LEGACY_LEAVES and len(parts) == 1:
        return _with_query(_LEGACY_LEAVES[head], query)
    return "/app/"


def _with_query(target: str, query: str) -> str:
    return f"{target}?{query}" if query else target


async def legacy_ui_redirect(request: Request) -> Response:
    rest = request.path_params.get("rest", "") or ""
    target = _legacy_to_app(rest, request.url.query)
    return RedirectResponse(target, status_code=301)


def _web_dist_dir() -> Path:
    """Location of the built SPA assets shipped inside the wheel."""
    return Path(__file__).parent / "web_dist"


class _SPAStaticFiles(StaticFiles):
    """``StaticFiles`` that falls back to ``index.html`` on 404.

    Required so a deep link like ``/app/kg/graph`` — which has no
    corresponding file on disk because the client router owns it — resolves
    to the SPA shell instead of returning 404. Without this, refreshing the
    browser on any non-root route gives a broken page.

    Starlette's StaticFiles raises ``HTTPException(404)`` when a path is
    not found, rather than returning a Response — catch that, not a 404
    Response.
    """

    async def get_response(self, path: str, scope):
        from starlette.exceptions import HTTPException

        try:
            response = await super().get_response(path, scope)
        except HTTPException as e:
            if e.status_code != 404:
                raise
            return await super().get_response("index.html", scope)
        if response.status_code == 404:
            return await super().get_response("index.html", scope)
        return response


def build_app() -> Starlette:
    routes: list = [
        Route("/", root, methods=["GET"]),
        Route("/mcp", mcp_endpoint, methods=["POST"]),
        Route("/healthz", healthz, methods=["GET"]),
        # Legacy /ui/* permanent-redirects so external bookmarks survive.
        Route("/ui", legacy_ui_redirect, methods=["GET"]),
        Route("/ui/", legacy_ui_redirect, methods=["GET"]),
        Route("/ui/{rest:path}", legacy_ui_redirect, methods=["GET"]),
    ]
    routes.extend(http_api.routes())
    web_dist = _web_dist_dir()
    if web_dist.is_dir():
        # ``html=True`` falls back to index.html for unknown sub-paths so
        # the client-side router owns /app/<anything>.
        routes.append(
            Mount(
                "/app",
                app=_SPAStaticFiles(directory=str(web_dist), html=True),
                name="spa",
            )
        )
    return Starlette(routes=routes)
