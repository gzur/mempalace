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
import contextvars
import logging
from typing import Optional

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from . import mcp_server

logger = logging.getLogger(__name__)

_dispatch_lock = asyncio.Lock()
_session_id_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "session_id", default=None
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
                None, ctx.run, mcp_server.handle_request, body
            )
    finally:
        _session_id_var.reset(token)

    if response is None:
        return Response(status_code=204)
    return JSONResponse(response)


async def healthz(_: Request) -> Response:
    return JSONResponse({"ok": True, "version": mcp_server.__version__})


def build_app() -> Starlette:
    return Starlette(
        routes=[
            Route("/mcp", mcp_endpoint, methods=["POST"]),
            Route("/healthz", healthz, methods=["GET"]),
        ]
    )
