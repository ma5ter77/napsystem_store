"""NAP-104: server keys of the Secure Player.

* KEK (key-encryption key) wraps every package's content key in the database: NAP_CONTENT_MASTER_KEY.
* Ed25519 key signs package manifests and offline leases: NAP_LICENSE_SIGNING_KEY (32-byte seed).

Both are base64 values of 32 random bytes (`python -m app.tools.secure_keys`). When they are not set, they are
derived from NAP_SECRET_KEY with HKDF, so existing servers keep working after the update; setting them separately
lets the session secret be rotated without making downloaded packages and leases unreadable.
"""
from __future__ import annotations

import base64
import hashlib
from functools import lru_cache

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from ..config import get_settings


def _material(explicit: str | None, info: bytes) -> bytes:
    if explicit:
        raw = base64.b64decode(explicit + "=" * (-len(explicit) % 4))
        if len(raw) != 32:
            raise ValueError(f"{info.decode()}: expected base64 of 32 bytes")
        return raw
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=b"nap-store", info=info).derive(
        get_settings().secret_key.encode())


@lru_cache
def kek() -> tuple[str, bytes]:
    """(key id, key). The id goes next to every wrapped key so a rotated KEK can be told apart."""
    k = _material(get_settings().content_master_key, b"nap-content-kek-v1")
    return "kek-" + hashlib.sha256(k).hexdigest()[:12], k


@lru_cache
def signer() -> tuple[str, Ed25519PrivateKey]:
    key = Ed25519PrivateKey.from_private_bytes(_material(get_settings().license_signing_key, b"nap-license-ed25519-v1"))
    return "ed-" + hashlib.sha256(public_raw(key)).hexdigest()[:12], key


def public_raw(key: Ed25519PrivateKey) -> bytes:
    return key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
