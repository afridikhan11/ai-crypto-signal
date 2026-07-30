"""
app/security/api_key_cipher.py

Encrypted-at-rest storage for third-party API credentials (currently used
for Binance API key/secret pairs, but not Binance-specific in any way).

Kept separate from app.services.binance_account_service so that service
stays focused purely on talking to the Binance API - encryption/storage
concerns live here instead.

The encryption key is derived (via PBKDF2-HMAC-SHA256) from the
application's own SECRET_KEY (app.core.config.Settings.secret_key), so
nothing extra needs to be configured, but it also means credentials
become unrecoverable if SECRET_KEY changes - same as any server-side
secret-derived encryption. Requires the `cryptography` package.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Optional

from loguru import logger

from app.core.config import get_settings

try:
    from cryptography.fernet import Fernet, InvalidToken
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

    _CRYPTO_AVAILABLE = True
except ImportError:  # pragma: no cover - guarded so the module still imports without the dep
    _CRYPTO_AVAILABLE = False


# Fixed, non-secret salt. Secrecy comes entirely from the app SECRET_KEY,
# not from this salt - its only purpose is standard PBKDF2 domain separation.
_PBKDF2_SALT = b"ai-crypto-signal-binance-account-service-v1"
_PBKDF2_ITERATIONS = 390_000


def _require_crypto() -> None:
    if not _CRYPTO_AVAILABLE:
        raise RuntimeError(
            "The 'cryptography' package is required for encrypted API key storage. "
            "Install it with: pip install cryptography"
        )


def _derive_fernet_key(app_secret: str) -> bytes:
    _require_crypto()
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=_PBKDF2_SALT,
        iterations=_PBKDF2_ITERATIONS,
    )
    key = kdf.derive(app_secret.encode("utf-8"))
    return base64.urlsafe_b64encode(key)


class ApiKeyCipher:
    """
    Encrypts/decrypts API credentials at rest.

    Usage:
        cipher = ApiKeyCipher()
        token = cipher.encrypt(api_key, api_secret, testnet=True)   # store `token`
        creds = cipher.decrypt(token)                                # {"api_key", "api_secret", "testnet"}
    """

    def __init__(self, app_secret: Optional[str] = None):
        _require_crypto()
        app_secret = app_secret or get_settings().secret_key
        self._fernet = Fernet(_derive_fernet_key(app_secret))

    def encrypt(self, api_key: str, api_secret: str, testnet: bool = False) -> str:
        """Returns an opaque encrypted token safe to store in a file or DB column."""
        payload = json.dumps(
            {"api_key": api_key, "api_secret": api_secret, "testnet": testnet}
        ).encode("utf-8")
        return self._fernet.encrypt(payload).decode("utf-8")

    def decrypt(self, token: str) -> dict:
        """Returns {"api_key": ..., "api_secret": ..., "testnet": ...}."""
        try:
            payload = self._fernet.decrypt(token.encode("utf-8"))
        except InvalidToken as exc:
            raise ValueError(
                "Could not decrypt stored API credentials "
                "(corrupted token, or SECRET_KEY changed since they were saved)."
            ) from exc
        return json.loads(payload)

    def save_to_file(self, path: str | Path, api_key: str, api_secret: str, testnet: bool = False) -> None:
        token = self.encrypt(api_key, api_secret, testnet)
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(token, encoding="utf-8")
        logger.info(f"Encrypted API credentials saved to {p}")

    def load_from_file(self, path: str | Path) -> dict:
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"No encrypted credentials file at {p}")
        return self.decrypt(p.read_text(encoding="utf-8"))
