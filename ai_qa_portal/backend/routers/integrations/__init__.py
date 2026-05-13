"""Compatibility shim for the consolidated integrations router.

The repository previously had a package at ``routers/integrations/`` and now
has the actual implementation in ``routers/integrations.py``. ``main.py``
imports ``from .routers import integrations`` (package import), so we re-export
the router from the module implementation here.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_MODULE_PATH = Path(__file__).resolve().parent.parent / "integrations.py"
_SPEC = importlib.util.spec_from_file_location(
    "ai_qa_portal.backend.routers._integrations_impl",
    _MODULE_PATH,
)
if _SPEC is None or _SPEC.loader is None:  # pragma: no cover
    raise RuntimeError(f"Could not resolve integrations router module at {_MODULE_PATH}")
_MOD = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MOD)

router = _MOD.router
