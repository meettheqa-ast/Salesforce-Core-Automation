from __future__ import annotations

import os

from cryptography.fernet import Fernet


class CredentialService:
    def __init__(self, key: str | None = None):
        raw = key or os.environ.get("FERNET_KEY", "")
        if not raw:
            raise RuntimeError(
                "FERNET_KEY not set. Generate one with: "
                'python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'
            )
        self._fernet = Fernet(raw.encode() if isinstance(raw, str) else raw)

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, ciphertext: str) -> str:
        return self._fernet.decrypt(ciphertext.encode()).decode()
