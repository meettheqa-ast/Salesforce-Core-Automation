"""Provider registry.

Callers ask `get_provider("jira")` and get back either the configured
provider or None. Providers register themselves at app startup, so the
single source of truth for "is integration X turned on?" is whether a
key is present in `_PROVIDERS`.

Today no providers register. A future Jira PR will:

    from .jira import JiraProvider
    _PROVIDERS["jira"] = JiraProvider(
        base_url=settings.jira_base_url,
        email=settings.jira_email,
        api_token=settings.jira_api_token,
    )

That's the entire wiring change beyond implementing the methods. The
API surface (`get_provider`, `register_provider`) lets callers stay
provider-agnostic."""

from __future__ import annotations

from .base import ExternalIssueProvider

_PROVIDERS: dict[str, ExternalIssueProvider] = {}


def register_provider(provider: ExternalIssueProvider) -> None:
    """Add a provider to the registry. Last writer wins -- a future PR
    that swaps the Jira client implementation can re-register without
    a restart."""
    _PROVIDERS[provider.source_name] = provider


def get_provider(source: str) -> ExternalIssueProvider | None:
    """Lookup by canonical source name. Returns None when no provider
    is registered, so callers can branch on integration availability
    without try/except."""
    return _PROVIDERS.get(source)


def list_sources() -> list[str]:
    """Sources currently configured. Empty when no integrations are
    wired (the default state today)."""
    return sorted(_PROVIDERS.keys())
