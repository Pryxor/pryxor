"""
Pryxor — Symmetric encryption for hold payloads.

The `holds.parameters` column contains redacted values, for display.
The real, unredacted parameters are stored encrypted in a separate
column and only decrypted at execution time.

Without this, an approved HOLD would execute with the redacted copy,
which — with `redaction.tools.<tool>.fields.<field>: HASH` — would send
a hash instead of the real value.
"""
from __future__ import annotations

import json
import os

from cryptography.fernet import Fernet, InvalidToken


class PayloadCipher:
    def __init__(self, key: str | bytes | None = None):
        key = key or os.environ.get("PRYXOR_ENCRYPTION_KEY")
        if not key:
            raise RuntimeError(
                "PRYXOR_ENCRYPTION_KEY is not set. Generate one with:\n"
                "  python -c \"from cryptography.fernet import Fernet; "
                "print(Fernet.generate_key().decode())\"\n"
                "then export it before starting the proxy."
            )
        if isinstance(key, str):
            key = key.encode()
        self._fernet = Fernet(key)

    def encrypt(self, data: dict) -> str:
        return self._fernet.encrypt(
            json.dumps(data, ensure_ascii=False).encode()
        ).decode()

    def decrypt(self, token: str) -> dict:
        try:
            raw = self._fernet.decrypt(token.encode())
        except InvalidToken as e:
            raise RuntimeError(
                "Failed to decrypt hold payload — has PRYXOR_ENCRYPTION_KEY "
                "changed since the hold was created?"
            ) from e
        return json.loads(raw)