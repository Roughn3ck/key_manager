"""Shared Ed25519 math primitives, base58 encoding, and SLIP-0010 derivation.

Extracted from key_manager_agent.py to avoid duplication with
derivation_engine.py.  Contains only the low-level math — no signing
or vault interaction.
"""
import hashlib
import hmac
import struct
from typing import Dict


# ============================================================================
# Ed25519 curve parameters (RFC 8032)
# ============================================================================

ED25519_P = 2**255 - 19
ED25519_L = 2**252 + 27742317777372353535851937790883648493
# Standard Ed25519 base point (twisted Edwards form)
ED25519_BY = 46316835694926478169428394003475163141307993866256225615783033603165251855960
ED25519_BX = 15112221349535400772501151409588531511454012693041857206046113283949847762202
ED25519_G = (ED25519_BX, ED25519_BY)


# ============================================================================
# Ed25519 math primitives
# ============================================================================

def ed25519_edwards_add(P: tuple, Q: tuple) -> tuple:
    """Add two points on the Ed25519 (twisted Edwards) curve.

    Twisted Edwards addition:
      x3 = (x1*y2 + x2*y1) / (1 + d*x1*x2*y1*y2)
      y3 = (y1*y2 + x1*x2) / (1 - d*x1*x2*y1*y2)
    where d = -121665/121666 mod p.
    """
    x1, y1 = P
    x2, y2 = Q
    d = (-121665 * pow(121666, ED25519_P - 2, ED25519_P)) % ED25519_P
    x1x2 = x1 * x2 % ED25519_P
    y1y2 = y1 * y2 % ED25519_P
    dx1x2y1y2 = d * x1x2 * y1y2 % ED25519_P
    x3 = (x1 * y2 + x2 * y1) * pow(1 + dx1x2y1y2, ED25519_P - 2, ED25519_P) % ED25519_P
    y3 = (y1y2 + x1x2) * pow(1 - dx1x2y1y2, ED25519_P - 2, ED25519_P) % ED25519_P
    return (x3, y3)


def ed25519_scalarmult(P: tuple, e: int) -> tuple:
    """Multiply point P by scalar e on the Ed25519 curve (double-and-add)."""
    Q = (0, 1)  # Identity element
    while e > 0:
        if e & 1:
            Q = ed25519_edwards_add(Q, P)
        P = ed25519_edwards_add(P, P)
        e >>= 1
    return Q


def ed25519_point_compress(P: tuple) -> bytes:
    """Compress an Ed25519 point to 32 bytes (little-endian y with x sign bit at 255)."""
    x, y = P
    y_bytes = y.to_bytes(32, "little")
    if x & 1:
        y_bytes = y_bytes[:31] + bytes([y_bytes[31] | 0x80])
    return y_bytes


def ed25519_clamp(scalar: bytes) -> bytes:
    """Clamp a 32-byte scalar for Ed25519 private key use (RFC 8032 §5.1.5)."""
    scalar = bytearray(scalar)
    scalar[0] &= 248
    scalar[31] &= 127
    scalar[31] |= 64
    return bytes(scalar)


def ed25519_privkey_to_pubkey(privkey_bytes: bytes) -> bytes:
    """Derive a 32-byte Ed25519 public key from a 32-byte private key.

    Hashes the private key with SHA-512, clamps, and scalar-multiplies
    per RFC 8032 to produce the compressed public key.
    """
    if len(privkey_bytes) != 32:
        raise ValueError(f"Ed25519 private key must be 32 bytes, got {len(privkey_bytes)}")
    h = hashlib.sha512(privkey_bytes).digest()
    scalar_bytes = ed25519_clamp(h[:32])
    scalar = int.from_bytes(scalar_bytes, "little")
    pubkey_point = ed25519_scalarmult(ED25519_G, scalar)
    return ed25519_point_compress(pubkey_point)


def ed25519_privkey_to_pubkey_hex(privkey_hex: str) -> bytes:
    """Hex-string wrapper around ed25519_privkey_to_pubkey."""
    privkey_bytes = bytes.fromhex(privkey_hex.replace("0x", ""))
    return ed25519_privkey_to_pubkey(privkey_bytes)


# ============================================================================
# Base58 encoding (Bitcoin/Solana alphabet)
# ============================================================================

_B58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def b58encode(data: bytes) -> str:
    """Encode bytes as base58 (Bitcoin/Solana alphabet)."""
    n = int.from_bytes(data, "big")
    out = ""
    while n > 0:
        n, r = divmod(n, 58)
        out = _B58_ALPHABET[r] + out
    pad = len(data) - len(data.lstrip(b"\x00"))
    return ("1" * pad) + out


# ============================================================================
# SLIP-0010 Ed25519 HD derivation (all-hardened paths)
# ============================================================================

def slip10_master_key_from_seed(seed: bytes) -> tuple:
    """Derive the SLIP-0010 Ed25519 master key and chain code from a seed.

    Args:
        seed: 64-byte BIP39 seed (from Mnemonic.to_seed).

    Returns:
        (key: bytes, chain_code: bytes) — each 32 bytes.
    """
    I = hmac.new(b"ed25519 seed", seed, hashlib.sha512).digest()
    return I[:32], I[32:]


def slip10_derive_hardened(key: bytes, chain_code: bytes, index: int) -> tuple:
    """Derive a hardened child key per SLIP-0010.

    Args:
        key: 32-byte parent private key.
        chain_code: 32-byte parent chain code.
        index: Child index (will be hardened: index + 0x80000000).

    Returns:
        (key: bytes, chain_code: bytes) — each 32 bytes.
    """
    hardened = index + 0x80000000
    data = b'\x00' + key + struct.pack(">L", hardened)
    I = hmac.new(chain_code, data, hashlib.sha512).digest()
    return I[:32], I[32:]


def slip10_derive_path(seed: bytes, path: str) -> tuple:
    """Derive a key along a SLIP-0010 hardened path.

    Args:
        seed: 64-byte BIP39 seed.
        path: Space-separated path like "44'/501'/0'/0'" (all hardened).

    Returns:
        (key: bytes, chain_code: bytes) — each 32 bytes.

    Raises:
        ValueError: If any component is not hardened (missing tick).
    """
    key, chain_code = slip10_master_key_from_seed(seed)
    if not path or path == "m":
        return key, chain_code
    # Strip leading "m/" if present
    path = path.strip()
    if path.startswith("m/"):
        path = path[2:]
    for component in path.split("/"):
        component = component.strip()
        if not component:
            continue
        if not component.endswith("'"):
            raise ValueError(
                f"SLIP-0010 requires all-hardened derivation. "
                f"Component '{component}' is not hardened."
            )
        index = int(component[:-1])
        key, chain_code = slip10_derive_hardened(key, chain_code, index)
    return key, chain_code
