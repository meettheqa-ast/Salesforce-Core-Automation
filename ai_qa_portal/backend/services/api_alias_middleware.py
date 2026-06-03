"""API-prefix alias middleware (Phase 3 IA audit -- foundation).

The audit flagged inconsistent ``/api/*`` prefix usage across routers:

  * Most newer routers have ``prefix='/api/...'`` (prompts, imports,
    runs, generate, integrations slice, etc.).
  * Several legacy routers have bare prefixes (``/user-stories``,
    ``/test-cases``, ``/sprints``, ``/tags``, ``/personas``,
    ``/orgs``).

The plan's Phase 3 endgame is "every route lives under /api/*". The
safe way to get there is in three steps:

  1. **NOW** (this middleware): a client requesting
     ``/api/user-stories`` is invisibly served by the existing
     ``/user-stories`` handler. Both paths work. No router file
     changes; no breaking calls.
  2. **Next release**: migrate `lib/api.ts` to call only the
     ``/api/*`` paths. The bare paths continue to work for any
     external clients or bookmarks.
  3. **Release after that**: flip each legacy router's prefix to
     ``/api/...`` and remove the alias entry from this middleware.
     Clients on the new paths keep working; bookmarks on the bare
     paths get a 404 (acceptable after one full release window).

This middleware is registered in main.py via ``app.middleware('http')``
or as an explicit Starlette middleware -- see ``main.py`` for the
mount point.
"""

from __future__ import annotations

import logging

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger("ai_qa_portal.api_alias")


# Resources currently mounted with bare prefixes. When a request
# arrives at ``/api/<bare>/...``, we rewrite the URL path to
# ``/<bare>/...`` so the existing router handles it.
#
# Adding a new resource to this list is the ONLY change needed to
# make it accessible under both URL families during the migration
# window.
_BARE_PREFIXES: tuple[str, ...] = (
    "user-stories",
    "test-cases",
    "sprints",
    "tags",
    "personas",
    "orgs",
    "run",  # POST /run; SSE /run/.../stream
)


class ApiPrefixAliasMiddleware(BaseHTTPMiddleware):
    """Rewrites ``/api/<bare>/...`` -> ``/<bare>/...`` in-flight so
    both URL families resolve to the same handler. Adds an
    ``X-Aliased-From`` header on the response for observability so
    operators can see which paths are still being hit via the legacy
    alias (helps decide when to retire each bare prefix)."""

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path.startswith("/api/"):
            tail = path[len("/api/") :]
            head = tail.split("/", 1)[0] if "/" in tail else tail
            if head in _BARE_PREFIXES:
                rewritten = "/" + tail
                # Rewrite by replacing the scope path. Starlette mutates
                # the original scope dict in-place, which is the
                # documented way to do path rewriting at this layer.
                request.scope["path"] = rewritten
                request.scope["raw_path"] = rewritten.encode()
                response: Response = await call_next(request)
                response.headers.setdefault("X-Aliased-From", path)
                return response
        return await call_next(request)
