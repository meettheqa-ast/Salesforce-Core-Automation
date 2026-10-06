"""Grounds generated/healed navigation steps in the target org's real metadata.

See ``models.py`` for the resolved-target shape, ``resolver.py`` for the
fuzzy-match-against-live-metadata service, and ``extraction.py`` for pulling
candidate target names out of story text.
"""

from __future__ import annotations

from .models import NavCandidate, ResolvedNavTarget, TargetType
from .resolver import NavTargetResolver, format_resolved_targets_block

__all__ = [
    "NavCandidate",
    "ResolvedNavTarget",
    "TargetType",
    "NavTargetResolver",
    "format_resolved_targets_block",
]
