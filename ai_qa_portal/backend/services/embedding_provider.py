"""Pluggable embedding providers.

Three concrete providers ship:

* ``openai``  -- ``text-embedding-3-small`` by default (1536 dims). Reads
  ``OPENAI_EMBEDDINGS_API_KEY`` first, then falls back to ``OPENAI_API_KEY``
  so existing OpenAI-backed deployments work out of the box.
* ``gemini``  -- ``text-embedding-004`` (768 dims). Reads ``GEMINI_API_KEY``.
* ``ollama``  -- ``nomic-embed-text`` (768 dims). Hits the local Ollama
  REST API at ``http://localhost:11434`` by default; override with
  ``OLLAMA_BASE_URL``.

Selection is by ``EMBEDDING_PROVIDER`` env (default ``openai``). Model id
is overridable via ``EMBEDDING_MODEL``.

The provider object exposes a single method::

    embed_texts(["chunk one", "chunk two", ...]) -> list[list[float]]

so callers can batch ergonomically without caring about HTTP shapes.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Protocol

import httpx

from ai_qa_portal.backend.config import settings

logger = logging.getLogger("ai_qa_portal.embedding_provider")


class EmbeddingError(RuntimeError):
    """Raised when the provider returns a non-recoverable error."""


class EmbeddingProvider(Protocol):
    """Minimal embedding interface every concrete provider implements."""

    name: str
    model: str
    dim: int

    def embed_texts(self, texts: list[str]) -> list[list[float]]: ...


# ---- OpenAI ---------------------------------------------------------------

@dataclass
class OpenAIEmbeddingProvider:
    name: str = "openai"
    model: str = "text-embedding-3-small"
    dim: int = 1536
    base_url: str = "https://api.openai.com/v1"
    timeout_s: float = 30.0

    def __post_init__(self) -> None:
        self._client = httpx.Client(timeout=self.timeout_s)

    def _api_key(self) -> str:
        return (
            settings.openai_embeddings_api_key
            or os.environ.get("OPENAI_EMBEDDINGS_API_KEY", "")
            or os.environ.get("OPENAI_API_KEY", "")
        ).strip()

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        key = self._api_key()
        if not key:
            raise EmbeddingError(
                "OPENAI_API_KEY (or OPENAI_EMBEDDINGS_API_KEY) is required for the OpenAI embedding provider"
            )
        # OpenAI's embeddings endpoint accepts up to ~2048 inputs per call;
        # we cap at 96 to keep individual requests well under any timeout
        # budget and to leave room for retries.
        chunk = 96
        out: list[list[float]] = []
        for i in range(0, len(texts), chunk):
            batch = texts[i : i + chunk]
            resp = self._client.post(
                f"{self.base_url}/embeddings",
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json={"model": self.model, "input": batch},
            )
            if resp.status_code != 200:
                raise EmbeddingError(
                    f"OpenAI embeddings failed {resp.status_code}: {resp.text[:300]}"
                )
            data = resp.json().get("data", [])
            out.extend([d["embedding"] for d in data])
        return out


# ---- Gemini ---------------------------------------------------------------

@dataclass
class GeminiEmbeddingProvider:
    name: str = "gemini"
    model: str = "text-embedding-004"
    dim: int = 768
    base_url: str = "https://generativelanguage.googleapis.com/v1beta"
    timeout_s: float = 30.0

    def __post_init__(self) -> None:
        self._client = httpx.Client(timeout=self.timeout_s)

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        key = os.environ.get("GEMINI_API_KEY", "").strip()
        if not key:
            raise EmbeddingError("GEMINI_API_KEY is required for the Gemini embedding provider")
        # Gemini's batch endpoint accepts one model + a list of contents.
        body = {
            "requests": [
                {
                    "model": f"models/{self.model}",
                    "content": {"parts": [{"text": t}]},
                }
                for t in texts
            ]
        }
        resp = self._client.post(
            f"{self.base_url}/models/{self.model}:batchEmbedContents?key={key}",
            json=body,
            headers={"Content-Type": "application/json"},
        )
        if resp.status_code != 200:
            raise EmbeddingError(f"Gemini embeddings failed {resp.status_code}: {resp.text[:300]}")
        data = resp.json().get("embeddings", [])
        return [item.get("values", []) for item in data]


# ---- Ollama ---------------------------------------------------------------

@dataclass
class OllamaEmbeddingProvider:
    name: str = "ollama"
    model: str = "nomic-embed-text"
    dim: int = 768
    base_url: str = ""
    timeout_s: float = 60.0

    def __post_init__(self) -> None:
        self.base_url = self.base_url or os.environ.get(
            "OLLAMA_BASE_URL", "http://localhost:11434"
        )
        self._client = httpx.Client(timeout=self.timeout_s)

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        # Ollama is one-by-one on the embed endpoint; loop and aggregate.
        out: list[list[float]] = []
        for text in texts:
            resp = self._client.post(
                f"{self.base_url}/api/embeddings",
                json={"model": self.model, "prompt": text},
            )
            if resp.status_code != 200:
                raise EmbeddingError(
                    f"Ollama embeddings failed {resp.status_code}: {resp.text[:300]}"
                )
            out.append(resp.json().get("embedding", []))
        return out


# ---- factory --------------------------------------------------------------

_PROVIDER_BY_NAME: dict[str, type[EmbeddingProvider]] = {
    "openai": OpenAIEmbeddingProvider,
    "gemini": GeminiEmbeddingProvider,
    "ollama": OllamaEmbeddingProvider,
}


def get_provider(name: str | None = None) -> EmbeddingProvider:
    """Return a configured provider. ``name`` defaults to
    ``settings.embedding_provider``; raises ``ValueError`` for unknown
    names.
    """
    chosen = (name or settings.embedding_provider or "openai").strip().lower()
    cls = _PROVIDER_BY_NAME.get(chosen)
    if cls is None:
        raise ValueError(
            f"Unknown embedding provider {chosen!r}; available: {sorted(_PROVIDER_BY_NAME)}"
        )
    provider = cls()  # type: ignore[call-arg]
    # Honour explicit overrides from settings.
    if settings.embedding_model:
        provider.model = settings.embedding_model  # type: ignore[attr-defined]
    if settings.embedding_dim:
        provider.dim = settings.embedding_dim  # type: ignore[attr-defined]
    return provider
