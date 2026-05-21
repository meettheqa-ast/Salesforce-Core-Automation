"""Prompt compiler -- safely render a template body against a context.

Why Jinja2 SandboxedEnvironment:
  * The existing assembler uses raw Python f-strings which means a
    creative user editing a system prompt could embed ``{__import__}``
    and reach into the runtime. We can't allow that on a per-user
    editable prompt.
  * Jinja's SandboxedEnvironment blocks attribute access on disallowed
    types, blocks ``__class__`` walks, and restricts function calls to
    a whitelist. No filesystem, no subprocess, no introspection.

Why ``StrictUndefined``:
  * A typo in a template body (e.g. ``{{ stroy.title }}``) should
    surface at SAVE time, not silently render to empty string at
    generation time. Strict mode raises ``UndefinedError`` which the
    router translates to a 400 with the exact missing variable.

Why a placeholder allow-list:
  * Each category has a small, declared set of safe placeholders (see
    ``SeedSpec.placeholders_declared``). We refuse to save a body that
    uses an undeclared top-level name. This catches a class of bugs
    where a user copy-pastes a placeholder from a different category
    and gets silent empty rendering.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from jinja2 import StrictUndefined, TemplateSyntaxError, UndefinedError, meta
from jinja2.exceptions import SecurityError
from jinja2.sandbox import SandboxedEnvironment

logger = logging.getLogger("ai_qa_portal.prompt_compiler")


# Single shared sandboxed environment. Stateless + thread-safe per
# Jinja2 docs.
_ENV = SandboxedEnvironment(
    undefined=StrictUndefined,
    autoescape=False,  # We're rendering prompt text for LLMs, not HTML.
    keep_trailing_newline=True,
    trim_blocks=False,
    lstrip_blocks=False,
)




class PromptCompileError(Exception):
    """Raised by compile() on any unsafe / invalid template body.

    The router catches this and returns HTTP 400 with ``.detail`` set
    to a human-readable message that points at the exact placeholder
    / line / safety issue."""


@dataclass
class CompiledPrompt:
    """Result of ``compile()``. The router returns these fields in the
    /preview response; the generation runtime uses ``text`` only."""

    text: str
    placeholders_used: list[str] = field(default_factory=list)
    bytes: int = 0
    # When True, the template referenced no placeholders at all -- the
    # body is a static prompt. We allow this; not every system prompt
    # needs templating.
    is_static: bool = False


# ---------- public API -------------------------------------------


def extract_placeholders(body: str) -> set[str]:
    """Return the set of top-level placeholder roots used by a body.

    Uses Jinja's official ``meta.find_undeclared_variables`` so deeply
    nested expressions like ``{% if story.title %}{{ persona.role }}
    {% endif %}`` correctly report ``{"story", "persona"}``. On a
    syntax error this returns an empty set; the dedicated
    ``validate_body`` path surfaces the real error.
    """
    if not body:
        return set()
    try:
        ast = _ENV.parse(body)
    except TemplateSyntaxError:
        return set()
    return set(meta.find_undeclared_variables(ast))


def validate_body(
    body: str,
    *,
    declared_placeholders: Iterable[str],
    max_bytes: int,
) -> set[str]:
    """Pre-flight a body for SAVE. Raises ``PromptCompileError`` on:
      * empty body
      * body larger than ``max_bytes``
      * Jinja syntax error
      * a top-level placeholder not in ``declared_placeholders``

    Returns the set of placeholders actually USED so the caller can
    persist it next to the version row.
    """
    if not body or not body.strip():
        raise PromptCompileError("Prompt body cannot be empty.")
    encoded = body.encode("utf-8")
    if len(encoded) > max_bytes:
        raise PromptCompileError(
            f"Prompt body is {len(encoded):,} bytes; cap is {max_bytes:,}. "
            "Trim the body or raise PROMPT_MAX_BODY_BYTES.",
        )

    # Compile-only pass surfaces syntax errors without touching context.
    try:
        _ENV.parse(body)
    except TemplateSyntaxError as exc:
        raise PromptCompileError(
            f"Jinja2 syntax error at line {exc.lineno}: {exc.message}",
        ) from exc

    used = extract_placeholders(body)
    declared = set(declared_placeholders or ())
    unknown = used - declared
    if unknown:
        # Sort for stable error messages.
        names = ", ".join(sorted(unknown))
        raise PromptCompileError(
            f"Undeclared placeholder(s): {names}. Declared for this "
            f"category: {sorted(declared) or '(none)'}.",
        )
    return used


def compile(  # noqa: A001 -- intentional, matches the public verb
    body: str,
    context: Mapping[str, Any] | None = None,
    *,
    strict: bool = True,
) -> CompiledPrompt:
    """Render ``body`` against ``context``. Raises ``PromptCompileError``
    on syntax / safety / undefined-variable failures.

    When ``strict=False``, undefined variables render as empty string
    -- useful for /preview when the user hasn't supplied every field.
    """
    if not body:
        return CompiledPrompt(text="", placeholders_used=[], bytes=0, is_static=True)

    if strict:
        env = _ENV
    else:
        # Forgiving env shares the same sandbox restrictions; only the
        # undefined behaviour changes.
        env = SandboxedEnvironment(
            undefined=_LenientUndefined,
            autoescape=False,
            keep_trailing_newline=True,
        )

    try:
        template = env.from_string(body)
        rendered = template.render(**(context or {}))
    except TemplateSyntaxError as exc:
        raise PromptCompileError(
            f"Jinja2 syntax error at line {exc.lineno}: {exc.message}",
        ) from exc
    except UndefinedError as exc:
        raise PromptCompileError(
            f"Undefined placeholder: {exc.message}. Supply the value in "
            "context or set strict=False (preview mode).",
        ) from exc
    except SecurityError as exc:
        raise PromptCompileError(
            f"Unsafe template construct rejected by sandbox: {exc}",
        ) from exc

    used = extract_placeholders(body)
    return CompiledPrompt(
        text=rendered,
        placeholders_used=sorted(used),
        bytes=len(rendered.encode("utf-8")),
        is_static=not used,
    )


# ---------- lenient undefined for preview path -------------------


class _LenientUndefined(StrictUndefined):
    """Renders as ``""`` instead of raising on access. Used only when
    compile() is called with strict=False (the /preview endpoint and
    UI dry-run). Attribute access on an undefined chains into another
    _LenientUndefined so ``{{ story.title }}`` doesn't blow up even
    when ``story`` is missing entirely."""

    def __str__(self) -> str:  # pragma: no cover -- exercised via Jinja
        return ""

    def __bool__(self) -> bool:
        return False

    def __getattr__(self, name: str):  # noqa: D401
        return _LenientUndefined(
            hint=self._undefined_hint,
            obj=self._undefined_obj,
            name=name,
        )

    def __getitem__(self, key):  # noqa: D401
        return _LenientUndefined(
            hint=self._undefined_hint,
            obj=self._undefined_obj,
            name=str(key),
        )
