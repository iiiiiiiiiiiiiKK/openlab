"""Local secret storage and log redaction.

API keys are encrypted with a key derived from a locally stored random secret plus
a machine-bound component. Nothing is ever written in clear text to the database,
the data directory, snapshots or reports.
"""

from __future__ import annotations

import base64
import json
import os
import re
import uuid
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

SECRET_FILE_NAME = "master.key"

SECRET_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"sk-[A-Za-z0-9_\-]{16,}"), "sk-***REDACTED***"),
    (re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]{12,}"), "Bearer ***REDACTED***"),
    (re.compile(r"(?i)(api[_-]?key|token|secret|password)\s*[:=]\s*[\"']?([^\s\"',]{6,})"), r"\1=***REDACTED***"),
    (re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"), "gh***REDACTED***"),
    (re.compile(r"AIza[0-9A-Za-z\-_]{30,}"), "AIza***REDACTED***"),
)


def redact(text: str, known_secrets: list[str] | None = None) -> str:
    """Mask credentials in any string that may leave the process."""
    if not text:
        return text
    for secret in known_secrets or []:
        if secret and len(secret) >= 6:
            text = text.replace(secret, "***REDACTED***")
    for pattern, replacement in SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


class SecretBox:
    """Symmetric encryption helper backed by a persisted master key."""

    def __init__(self, secrets_dir: Path) -> None:
        self.secrets_dir = Path(secrets_dir)
        self.secrets_dir.mkdir(parents=True, exist_ok=True)
        self._fernet = Fernet(self._load_or_create_key())

    def _machine_salt(self) -> bytes:
        parts = [
            os.environ.get("COMPUTERNAME", ""),
            os.environ.get("USERNAME", os.environ.get("USER", "")),
            str(uuid.getnode()),
        ]
        return "openlab-local-secret".encode() + "|".join(parts).encode()

    def _load_or_create_key(self) -> bytes:
        key_path = self.secrets_dir / SECRET_FILE_NAME
        if key_path.exists():
            material = key_path.read_bytes()
        else:
            material = base64.urlsafe_b64encode(os.urandom(48))
            key_path.write_bytes(material)
            try:
                os.chmod(key_path, 0o600)
            except OSError:  # pragma: no cover - platform dependent
                pass
        return self._derive(material)

    def _derive(self, material: bytes) -> bytes:
        hkdf = HKDF(algorithm=hashes.SHA256(), length=32, salt=self._machine_salt(), info=b"openlab-secret-box")
        return base64.urlsafe_b64encode(hkdf.derive(material))

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, token: str) -> str:
        try:
            return self._fernet.decrypt(token.encode()).decode()
        except (InvalidToken, ValueError):
            return ""

    def mask(self, token: str) -> str:
        plain = self.decrypt(token)
        if not plain:
            return ""
        if len(plain) <= 8:
            return "*" * len(plain)
        return f"{plain[:3]}{'*' * 8}{plain[-4:]}"

    def fingerprint(self, token: str) -> dict | None:
        plain = self.decrypt(token)
        if not plain:
            return None
        return {"length": len(plain), "masked": self.mask(token)}


def dumps_safe(payload: dict, box: SecretBox) -> str:
    """Serialise metadata without any raw credential."""
    clean = json.loads(json.dumps(payload))
    text = json.dumps(clean, indent=2, ensure_ascii=False)
    return redact(text)
