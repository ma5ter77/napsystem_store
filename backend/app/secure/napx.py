"""NAP-104 (spec v2.1, M5): the .napx protected package and the signed offline lease.

.napx v1 layout (all integers big-endian):

    b"NAPX" | u8 version = 1 | 3 zero bytes
    u32 manifest length | manifest (canonical JSON, UTF-8)
    u32 signature length | Ed25519 signature of the manifest bytes
    segment 0 | segment 1 | ...           each = AES-256-GCM(plaintext segment) + 16-byte tag

Every segment is encrypted with the content key (CEK) under
    nonce = manifest.nonce_prefix (8 bytes) || u32 segment index
    AAD   = SHA-256(manifest bytes) || u32 segment index || u8 (1 for the last segment, else 0)
so a segment cannot be moved, dropped, swapped between packages or the file cut short without decryption failing,
and a changed manifest breaks every segment even where the signature is not checked.

Envelope encryption: the CEK is random per package, stored only wrapped by the server key (KEK) and handed to a
listener's device only inside a lease, encrypted to that device's P-256 public key (ECDH-ES + HKDF-SHA256 +
AES-256-GCM). The lease itself is a compact JWS (EdDSA) bound to account, device, content and expiry.

Honest limits (spec invariant): this protects files at rest and in transit and makes copies useless on other
devices; it is not absolute copy protection — a player has to decrypt to play, and an offline expiry is checked
against the device clock.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import struct
from dataclasses import dataclass
from typing import BinaryIO, Iterator

from cryptography.exceptions import InvalidSignature, InvalidTag
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

MAGIC = b"NAPX"
VERSION = 1
SEGMENT_SIZE = 1024 * 1024  # 1 MiB of plaintext per segment
TAG = 16
LEASE_INFO = b"nap-lease-v1"


class NapxError(Exception):
    """The package or lease is damaged, altered, for another device, or expired."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def unb64u(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def canonical(obj: dict) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _nonce(prefix: bytes, i: int) -> bytes:
    return prefix + struct.pack(">I", i)


def _aad(manifest_hash: bytes, i: int, last: bool) -> bytes:
    return manifest_hash + struct.pack(">IB", i, 1 if last else 0)


# ------------------------------------------------------------------ package

@dataclass
class Package:
    manifest: dict
    manifest_bytes: bytes
    signature: bytes
    data_offset: int

    @property
    def manifest_sha256(self) -> str:
        return hashlib.sha256(self.manifest_bytes).hexdigest()


def write_package(src: BinaryIO, dst: BinaryIO, *, cek: bytes, signer: Ed25519PrivateKey, content_id: str,
                  sha256: str, size: int, content_type: str, key_id: str,
                  segment_size: int = SEGMENT_SIZE) -> Package:
    """Encrypts `size` bytes of `src` into `dst`. `sha256` is the plaintext hash (checked while encrypting)."""
    if size <= 0:
        raise NapxError("empty")
    count = -(-size // segment_size)
    manifest = {"format": "napx", "v": VERSION, "content_id": content_id, "sha256": sha256, "size": size,
                "content_type": content_type, "segment_size": segment_size, "segments": count,
                "cipher": "AES-256-GCM", "nonce_prefix": b64u(os.urandom(8)), "key_id": key_id}
    mb = canonical(manifest)
    sig = signer.sign(mb)
    head = MAGIC + bytes([VERSION, 0, 0, 0]) + struct.pack(">I", len(mb)) + mb + struct.pack(">I", len(sig)) + sig
    dst.write(head)
    mh, prefix, aes, h = hashlib.sha256(mb).digest(), unb64u(manifest["nonce_prefix"]), AESGCM(cek), hashlib.sha256()
    for i in range(count):
        chunk = src.read(min(segment_size, size - i * segment_size))
        if not chunk:
            raise NapxError("short_source")
        h.update(chunk)
        dst.write(aes.encrypt(_nonce(prefix, i), chunk, _aad(mh, i, i == count - 1)))
    if h.hexdigest() != sha256:
        raise NapxError("source_hash_mismatch")
    return Package(manifest, mb, sig, len(head))


def read_header(f: BinaryIO) -> Package:
    head = f.read(8)
    if len(head) != 8 or head[:4] != MAGIC:
        raise NapxError("not_napx")
    if head[4] != VERSION:
        raise NapxError("unsupported_version")
    (n,) = struct.unpack(">I", f.read(4))
    if not 0 < n <= 64 * 1024:
        raise NapxError("bad_manifest")
    mb = f.read(n)
    (sn,) = struct.unpack(">I", f.read(4))
    if sn != 64:
        raise NapxError("bad_signature")
    sig = f.read(sn)
    try:
        manifest = json.loads(mb)
    except ValueError:
        raise NapxError("bad_manifest")
    return Package(manifest, mb, sig, 16 + n + sn)


def verify_manifest(pkg: Package, public: Ed25519PublicKey) -> None:
    try:
        public.verify(pkg.signature, pkg.manifest_bytes)
    except InvalidSignature:
        raise NapxError("bad_signature")


def iter_plaintext(f: BinaryIO, pkg: Package, cek: bytes) -> Iterator[bytes]:
    """Decrypts segment by segment (positioned right after the header). Any change raises NapxError("tampered")."""
    m = pkg.manifest
    size, seg, count = int(m["size"]), int(m["segment_size"]), int(m["segments"])
    mh, prefix, aes = hashlib.sha256(pkg.manifest_bytes).digest(), unb64u(m["nonce_prefix"]), AESGCM(cek)
    for i in range(count):
        plain_len = min(seg, size - i * seg)
        ct = f.read(plain_len + TAG)
        if len(ct) != plain_len + TAG:
            raise NapxError("truncated")
        try:
            yield aes.decrypt(_nonce(prefix, i), ct, _aad(mh, i, i == count - 1))
        except InvalidTag:
            raise NapxError("tampered")
    if f.read(1):
        raise NapxError("trailing_data")


# ------------------------------------------------------------------ keys

def wrap_cek(kek: bytes, cek: bytes, aad: bytes) -> str:
    nonce = os.urandom(12)
    return b64u(nonce + AESGCM(kek).encrypt(nonce, cek, aad))


def unwrap_cek(kek: bytes, wrapped: str, aad: bytes) -> bytes:
    raw = unb64u(wrapped)
    return AESGCM(kek).decrypt(raw[:12], raw[12:], aad)


def load_device_key(spki_b64: str) -> ec.EllipticCurvePublicKey:
    """The listener device's public key: SPKI DER (base64 or base64url), P-256 only — what WebCrypto exports."""
    try:
        raw = base64.b64decode(spki_b64.replace("-", "+").replace("_", "/") + "=" * (-len(spki_b64) % 4))
        key = serialization.load_der_public_key(raw)
    except Exception:
        raise NapxError("bad_device_key")
    if not isinstance(key, ec.EllipticCurvePublicKey) or not isinstance(key.curve, ec.SECP256R1):
        raise NapxError("bad_device_key")
    return key


def _lease_kek(shared: bytes, lease_id: str) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=LEASE_INFO + b"|" + lease_id.encode()).derive(shared)


def seal_for_device(device: ec.EllipticCurvePublicKey, cek: bytes, lease_id: str) -> dict:
    eph = ec.generate_private_key(ec.SECP256R1())
    k = _lease_kek(eph.exchange(ec.ECDH(), device), lease_id)
    iv = os.urandom(12)
    epk = eph.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return {"alg": "ECDH-ES+HKDF-SHA256+A256GCM", "crv": "P-256", "epk": b64u(epk), "iv": b64u(iv),
            "ct": b64u(AESGCM(k).encrypt(iv, cek, lease_id.encode()))}


def open_sealed(device_private: ec.EllipticCurvePrivateKey, sealed: dict, lease_id: str) -> bytes:
    """Reference implementation of what the device does (the browser does the same with WebCrypto)."""
    epk = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), unb64u(sealed["epk"]))
    k = _lease_kek(device_private.exchange(ec.ECDH(), epk), lease_id)
    try:
        return AESGCM(k).decrypt(unb64u(sealed["iv"]), unb64u(sealed["ct"]), lease_id.encode())
    except InvalidTag:
        raise NapxError("wrong_device")


# ------------------------------------------------------------------ lease (compact JWS, EdDSA)

def sign_lease(signer: Ed25519PrivateKey, kid: str, payload: dict) -> str:
    h = b64u(canonical({"alg": "EdDSA", "typ": "nap-lease+jws", "kid": kid}))
    p = b64u(canonical(payload))
    return f"{h}.{p}.{b64u(signer.sign(f'{h}.{p}'.encode()))}"


def verify_lease(public: Ed25519PublicKey, token: str, now: int | None = None) -> dict:
    try:
        h, p, s = token.split(".")
        public.verify(unb64u(s), f"{h}.{p}".encode())
        payload = json.loads(unb64u(p))
    except (ValueError, InvalidSignature):
        raise NapxError("bad_lease")
    if now is not None and now >= int(payload["exp"]):
        raise NapxError("lease_expired")
    return payload
