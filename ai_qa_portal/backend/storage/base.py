from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class StorageBackend(ABC):
    @abstractmethod
    def read(self, key: str) -> dict[str, Any]:
        """Read data for the given key. Returns empty dict if not found."""

    @abstractmethod
    def write(self, key: str, data: dict[str, Any]) -> None:
        """Persist data under the given key."""
