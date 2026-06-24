"""
Key Manager Agent — Headless interface for AI agents to sign transactions
without ever exposing private keys.

Architecture:
  - USB drive (mounted at F:\) contains the encrypted vault (key_vault.encrypted)
  - This agent unlocks the vault with a password (from env var or stdin)
  - Accepts JSON commands via stdin (line-delimited) or HTTP (localhost port)
  - Signs transactions internally using stored private keys
  - Returns signed tx hex, tx hashes, or addresses — NEVER private keys

Usage:
  # Stdin mode (pipe commands in):
  KEY_MANAGER_PASSWORD="..." python3 key_manager_agent.py --vault /mnt/f/key_vault.encrypted

  # HTTP server mode:
  KEY_MANAGER_PASSWORD="..." python3 key_manager_agent.py --vault /mnt/f/key_vault.encrypted --serve --port 8842

Commands (JSON, one per line on stdin):
  {"cmd": "unlock"}
  {"cmd": "status"}
  {"cmd": "list_accounts"}
  {"cmd": "get_address", "account": "G6", "chain": "EVM"}
  {"cmd": "sign_tx", "account": "G6", "chain": "EVM", "to": "0x...", "data": "0x...", "value": "0", "chain_id": 42161, "rpc": "https://arb1.arbitrum.io/rpc"}
  {"cmd": "broadcast_tx", "account": "G6", "chain": "EVM", "to": "0x...", "data": "0x...", "value": "0", "chain_id": 42161, "rpc": "https://arb1.arbitrum.io/rpc"}
  {"cmd": "sign_message", "account": "G6", "chain": "EVM", "message": "0x..."}

Response format:
  {"status": "ok", "result": ...}   or   {"status": "error", "error": "..."}

Security:
  - Private keys are loaded into memory only for signing, never returned
  - No private key ever appears in any response, log, or error message
  - Session auto-locks after configurable timeout (default 10 min)
  - HTTP mode binds to localhost only
"""
import argparse
import json
import os
import sys
import time
import hmac
import hashlib
import struct
from pathlib import Path
from typing import Dict, Any, Optional, List

# Add parent dir to path so we can import crypto_engine
sys.path.insert(0, str(Path(__file__).parent))
from crypto_engine import CryptoEngine


# ============================================================================
# Minimal EVM signer — no external dependencies
# ============================================================================

SECP256K1_N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
SECP256K1_P = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEFFFFFC2F

# Generator point for secp256k1
Gx = 0x79BE667EF9DCBBAC55A06295CE870B07029BFCDB2DCE28D959F2815B16F81798
Gy = 0x483ADA7726A3C4655DA4FBFC0E1108A8FD17B448A68554199C47D08FFB10D4B8


class Point:
    """Elliptic curve point on secp256k1."""
    __slots__ = ('x', 'y', 'inf')

    def __init__(self, x: Optional[int] = None, y: Optional[int] = None, inf: bool = False):
        self.x = x
        self.y = y
        self.inf = inf

    @staticmethod
    def infinity() -> 'Point':
        return Point(None, None, True)

    def is_infinity(self) -> bool:
        return self.inf

    def __eq__(self, other):
        if self.inf and other.inf:
            return True
        if self.inf or other.inf:
            return False
        return self.x == other.x and self.y == other.y

    def __add__(self, other: 'Point') -> 'Point':
        if self.inf:
            return other
        if other.inf:
            return self
        if self.x == other.x:
            if (self.y + other.y) % SECP256K1_P == 0:
                return Point.infinity()
            # Point doubling
            lam = (3 * self.x * self.x) * pow(2 * self.y, -1, SECP256K1_P) % SECP256K1_P
        else:
            lam = (other.y - self.y) * pow(other.x - self.x, -1, SECP256K1_P) % SECP256K1_P
        x3 = (lam * lam - self.x - other.x) % SECP256K1_P
        y3 = (lam * (self.x - x3) - self.y) % SECP256K1_P
        return Point(x3, y3)

    def __mul__(self, k: int) -> 'Point':
        # Scalar multiplication using double-and-add
        result = Point.infinity()
        addend = self
        while k:
            if k & 1:
                result = result + addend
            addend = addend + addend
            k >>= 1
        return result

    def to_bytes(self, compressed: bool = True) -> bytes:
        if self.inf:
            return b'\x00' * 33
        prefix = b'\x02' if self.y % 2 == 0 else b'\x03'
        return prefix + self.x.to_bytes(32, 'big')


G = Point(Gx, Gy)


def privkey_to_pubkey(privkey: int) -> Point:
    """Convert private key integer to public key point."""
    return G * privkey


def _get_keccak():
    """Get keccak256 module, handling both Crypto and Cryptodome import paths."""
    try:
        from Crypto.Hash import keccak
    except ImportError:
        from Cryptodome.Hash import keccak
    return keccak


def pubkey_to_address(pubkey: Point) -> str:
    """Convert public key point to Ethereum address (checksummed)."""
    x_bytes = pubkey.x.to_bytes(32, 'big')
    y_bytes = pubkey.y.to_bytes(32, 'big')
    pub_bytes = x_bytes + y_bytes

    keccak = _get_keccak()
    h = keccak.new(digest_bits=256)
    h.update(pub_bytes)
    addr_bytes = h.digest()[-20:]

    addr = '0x' + addr_bytes.hex()
    # EIP-55 checksum
    addr_hash = keccak.new(digest_bits=256)
    addr_hash.update(addr[2:].lower().encode())
    hashed = addr_hash.hexdigest()
    result = '0x'
    for i, c in enumerate(addr[2:]):
        if c in '0123456789':
            result += c
        elif int(hashed[i], 16) >= 8:
            result += c.upper()
        else:
            result += c.lower()
    return result


def private_key_to_address(privkey_hex: str) -> str:
    """Convert a hex private key to a checksummed Ethereum address."""
    privkey = int(privkey_hex, 16)
    pubkey = privkey_to_pubkey(privkey)
    return pubkey_to_address(pubkey)


# ============================================================================
# RLP encoding for raw transactions
# ============================================================================

def rlp_encode(data) -> bytes:
    """RLP encode a single item."""
    if data is None:
        return b'\x80'
    if isinstance(data, int):
        if data == 0:
            return b'\x80'
        b = data.to_bytes((data.bit_length() + 7) // 8, 'big')
        return rlp_encode_bytes(b)
    if isinstance(data, str):
        if data.startswith('0x'):
            b = bytes.fromhex(data[2:])
        else:
            b = data.encode('utf-8')
        return rlp_encode_bytes(b)
    if isinstance(data, (bytes, bytearray)):
        return rlp_encode_bytes(bytes(data))
    if isinstance(data, (list, tuple)):
        encoded_items = b''.join(rlp_encode(item) for item in data)
        return encode_length(len(encoded_items), 0xC0) + encoded_items
    raise TypeError(f"Cannot RLP encode {type(data)}")


def rlp_encode_bytes(b: bytes) -> bytes:
    if len(b) == 1 and b[0] < 0x80:
        return b
    return encode_length(len(b), 0x80) + b


def encode_length(length: int, offset: int) -> bytes:
    if length < 56:
        return bytes([length + offset])
    else:
        len_bytes = length.to_bytes((length.bit_length() + 7) // 8, 'big')
        return bytes([len(len_bytes) + offset + 55]) + len_bytes


# ============================================================================
# Transaction signing (EIP-155 / EIP-1559 / EIP-2930)
# ============================================================================

def sign_legacy_tx(
    to: str,
    data: str,
    value: int,
    gas_limit: int,
    gas_price: int,
    nonce: int,
    chain_id: int,
    privkey_hex: str,
) -> str:
    """Sign a legacy (type 0) EVM transaction with EIP-155."""
    pk = int(privkey_hex, 16)

    to_bytes = bytes.fromhex(to[2:]) if to.startswith('0x') else bytes.fromhex(to)
    data_bytes = bytes.fromhex(data[2:]) if data.startswith('0x') else bytes.fromhex(data)

    # EIP-155 signing: rlp([nonce, gasPrice, gasLimit, to, value, data, chainId, 0, 0])
    unsigned = [
        nonce,
        gas_price,
        gas_limit,
        to_bytes,
        value,
        data_bytes,
        chain_id,
        0,
        0,
    ]
    unsigned_rlp = rlp_encode(unsigned)

    # Hash with keccak256
    keccak = _get_keccak()
    h = keccak.new(digest_bits=256)
    h.update(unsigned_rlp)
    msg_hash = h.digest()

    # Sign
    r, s, v = _ecdsa_sign(msg_hash, pk, chain_id)

    # Signed tx: rlp([nonce, gasPrice, gasLimit, to, value, data, v, r, s])
    signed = [nonce, gas_price, gas_limit, to_bytes, value, data_bytes, v, r, s]
    signed_rlp = rlp_encode(signed)

    return '0x' + signed_rlp.hex()


def sign_eip1559_tx(
    to: str,
    data: str,
    value: int,
    gas_limit: int,
    max_fee_per_gas: int,
    max_priority_fee_per_gas: int,
    nonce: int,
    chain_id: int,
    privkey_hex: str,
) -> str:
    """Sign an EIP-1559 (type 2) transaction."""
    pk = int(privkey_hex, 16)

    to_bytes = bytes.fromhex(to[2:]) if to.startswith('0x') else bytes.fromhex(to)
    data_bytes = bytes.fromhex(data[2:]) if data.startswith('0x') else bytes.fromhex(data)

    # EIP-1559: 0x02 || rlp([chainId, nonce, maxPriorityFeePerGas, maxFeePerGas, gasLimit, to, value, data, accessList])
    unsigned_fields = [
        chain_id,
        nonce,
        max_priority_fee_per_gas,
        max_fee_per_gas,
        gas_limit,
        to_bytes,
        value,
        data_bytes,
        [],  # access list
    ]
    unsigned_rlp = rlp_encode(unsigned_fields)
    typed_payload = b'\x02' + unsigned_rlp

    keccak = _get_keccak()
    h = keccak.new(digest_bits=256)
    h.update(typed_payload)
    msg_hash = h.digest()

    r, s, y_parity = _ecdsa_sign(msg_hash, pk, chain_id, eip1559=True)

    # Signed: 0x02 || rlp([chainId, nonce, maxPriorityFeePerGas, maxFeePerGas, gasLimit, to, value, data, accessList, v, r, s])
    signed_fields = unsigned_fields + [y_parity, r, s]
    signed_rlp = rlp_encode(signed_fields)
    signed_payload = b'\x02' + signed_rlp

    return '0x' + signed_payload.hex()


def _ecdsa_sign(msg_hash: bytes, privkey: int, chain_id: int, eip1559: bool = False):
    """ECDSA sign a message hash with private key. Returns (r, s, v)."""
    keccak = _get_keccak()
    z = int.from_bytes(msg_hash, 'big')

    # Deterministic k (RFC 6979)
    k = _rfc6979_k(privkey, msg_hash)

    # R = k * G
    R = G * k
    r = R.x % SECP256K1_N
    if r == 0:
        raise ValueError("r is zero, try again")

    # s = k^-1 * (z + r * d) mod n
    s = (pow(k, -1, SECP256K1_N) * (z + r * privkey)) % SECP256K1_N
    if s > SECP256K1_N // 2:
        s = SECP256K1_N - s  # low-s

    # Recovery id
    y_parity = R.y % 2  # 0 or 1

    if eip1559:
        v = y_parity  # 0 or 1 for EIP-1559
    else:
        v = y_parity + 27 + chain_id * 2 + 8  # EIP-155 legacy

    return r, s, v


def _rfc6979_k(privkey: int, msg_hash: bytes) -> int:
    """RFC 6979 deterministic k generation."""
    keccak = _get_keccak()
    # Step 1: h1 = H(m) (already done, msg_hash)
    h1 = msg_hash

    # Step 2: V = 0x01 0x01 ... 0x01 (32 bytes)
    V = b'\x01' * 32
    # Step 3: K = 0x00 0x00 ... 0x00 (32 bytes)
    K = b'\x00' * 32

    # Step 4: K = HMAC_K(V || 0x00 || int2octets(d) || bits2octets(h1))
    d_bytes = privkey.to_bytes(32, 'big')
    K = _hmac_sha256(K, V + b'\x00' + d_bytes + _bits2octets(h1))
    # Step 5: V = HMAC_K(V)
    V = _hmac_sha256(K, V)
    # Step 6: K = HMAC_K(V || 0x01 || int2octets(d) || bits2octets(h1))
    K = _hmac_sha256(K, V + b'\x01' + d_bytes + _bits2octets(h1))
    # Step 7: V = HMAC_K(V)
    V = _hmac_sha256(K, V)

    while True:
        # T = empty
        T = b''
        while len(T) < 32:
            V = _hmac_sha256(K, V)
            T += V
        k = int.from_bytes(T, 'big')
        if 1 <= k < SECP256K1_N:
            return k
        K = _hmac_sha256(K, V + b'\x00')
        V = _hmac_sha256(K, V)


def _hmac_sha256(key: bytes, msg: bytes) -> bytes:
    import hmac
    return hmac.new(key, msg, hashlib.sha256).digest()


def _bits2octets(h: bytes) -> bytes:
    z1 = int.from_bytes(h, 'big')
    z2 = z1 % SECP256K1_N
    return z2.to_bytes(32, 'big')


# ============================================================================
# RPC helpers
# ============================================================================

def rpc_call(rpc_url: str, method: str, params: list) -> dict:
    """Make a JSON-RPC call."""
    import urllib.request
    payload = json.dumps({
        "jsonrpc": "2.0",
        "id": 1,
        "method": method,
        "params": params,
    }).encode()
    req = urllib.request.Request(rpc_url, data=payload, headers={
        "Content-Type": "application/json",
        "User-Agent": "key-manager-agent/1.0"
    })
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def get_nonce(rpc_url: str, address: str) -> int:
    """Get transaction count for an address."""
    result = rpc_call(rpc_url, "eth_getTransactionCount", [address, "latest"])
    return int(result["result"], 16)


def get_chain_id(rpc_url: str) -> int:
    """Get chain ID from RPC."""
    result = rpc_call(rpc_url, "eth_chainId", [])
    return int(result["result"], 16)


def estimate_gas(rpc_url: str, tx: dict) -> int:
    """Estimate gas for a transaction."""
    result = rpc_call(rpc_url, "eth_estimateGas", [tx])
    return int(result["result"], 16)


def get_gas_price(rpc_url: str) -> dict:
    """Get gas price info (legacy + EIP-1559)."""
    legacy = rpc_call(rpc_url, "eth_gasPrice", [])
    try:
        max_fee = rpc_call(rpc_url, "eth_maxPriorityFeePerGas", [])
        max_priority = int(max_fee["result"], 16)
    except Exception:
        max_priority = 0
    base = int(legacy["result"], 16)
    return {
        "gasPrice": base,
        "maxFeePerGas": int(base * 1.2) + max_priority,  # 20% buffer for base fee fluctuations
        "maxPriorityFeePerGas": max_priority,
    }


def broadcast_raw_tx(rpc_url: str, signed_tx_hex: str) -> str:
    """Broadcast a signed raw transaction. Returns tx hash."""
    result = rpc_call(rpc_url, "eth_sendRawTransaction", [signed_tx_hex])
    if "error" in result:
        raise Exception(result["error"]["message"])
    return result["result"]


# ============================================================================
# Key Manager Agent
# ============================================================================

class KeyManagerAgent:
    """Headless agent interface for the Key Manager vault."""

    def __init__(self, vault_path: str, password: str, session_timeout: int = 600):
        self.vault_path = Path(vault_path)
        self.password = password
        self.session_timeout = session_timeout  # seconds
        self.last_activity = time.time()
        self.unlocked = False
        self.vault_data: Dict[str, Any] = {}
        self.crypto = CryptoEngine()

        # Auto-unlock on init if password provided
        if self.password:
            self.unlock()

    def _check_session(self):
        """Check if session is still valid."""
        if not self.unlocked:
            raise RuntimeError("Vault is locked. Call unlock first.")
        if time.time() - self.last_activity > self.session_timeout:
            self.unlocked = False
            self.vault_data = {}
            raise RuntimeError("Session expired. Re-unlock the vault.")
        self.last_activity = time.time()

    def unlock(self) -> dict:
        """Unlock the vault with the password."""
        if not self.vault_path.exists():
            return {"status": "error", "error": f"Vault file not found: {self.vault_path}"}
        try:
            with open(self.vault_path, 'r', newline='') as f:
                encrypted_json = f.read()
            self.vault_data = self.crypto.decrypt_json(encrypted_json, self.password)
            self.unlocked = True
            self.last_activity = time.time()
            return {"status": "ok", "result": "Vault unlocked"}
        except Exception as e:
            return {"status": "error", "error": f"Failed to unlock: {str(e)}"}

    def status(self) -> dict:
        """Return vault status."""
        if not self.unlocked:
            return {"status": "ok", "result": {"unlocked": False, "vault": str(self.vault_path)}}
        self._check_session()
        accounts = self.vault_data.get("accounts", {})
        pools = self.vault_data.get("pools", {})
        return {
            "status": "ok",
            "result": {
                "unlocked": True,
                "vault": str(self.vault_path),
                "accounts": list(accounts.keys()),
                "pools": list(pools.keys()),
                "account_count": len(accounts),
                "address_count": sum(len(a.get("addresses", [])) for a in accounts.values()),
            }
        }

    def list_accounts(self) -> dict:
        """List all accounts and their addresses (no private keys)."""
        self._check_session()
        accounts = self.vault_data.get("accounts", {})
        result = {}
        for name, data in accounts.items():
            result[name] = {
                "addresses": data.get("addresses", []),
                "notes": data.get("notes", ""),
            }
        return {"status": "ok", "result": result}

    def get_address(self, account: str, chain: str = None) -> dict:
        """Get addresses for an account, optionally filtered by chain."""
        self._check_session()
        accounts = self.vault_data.get("accounts", {})
        if account not in accounts:
            return {"status": "error", "error": f"Account '{account}' not found"}
        addresses = accounts[account].get("addresses", [])
        if chain:
            # Filter by chain match (case-insensitive)
            filtered = [a for a in addresses if a.get("chain", "").lower() == chain.lower()
                        or a.get("coin", "").lower() == chain.lower()]
            if not filtered:
                return {"status": "error", "error": f"No {chain} address found for account '{account}'"}
            return {"status": "ok", "result": filtered}
        return {"status": "ok", "result": addresses}

    def _get_private_key(self, account: str, chain: str = "EVM") -> str:
        """Get private key for an account/chain. Internal only — never returned to caller."""
        pk_store = self.vault_data.get("private_keys", {})
        keys = pk_store.get(account)
        if keys is None:
            raise ValueError(f"No private keys found for account '{account}'")
        # Handle legacy single-string format
        if isinstance(keys, str):
            return keys
        # List of {chain, key} dicts
        for entry in keys:
            entry_chain = entry.get("chain", "").upper()
            if chain.upper() in entry_chain or entry_chain in chain.upper():
                return entry["key"]
        # If no chain match, return first key
        if keys:
            return keys[0]["key"]
        raise ValueError(f"No private key found for account '{account}' chain '{chain}'")

    def sign_tx(self, account: str, to: str, data: str, value: str = "0",
                chain_id: int = None, rpc: str = None, chain: str = "EVM",
                gas_limit: int = None, gas_price: int = None,
                max_fee_per_gas: int = None, max_priority_fee_per_gas: int = None,
                nonce: int = None) -> dict:
        """Sign a transaction. Returns signed tx hex (not broadcast)."""
        self._check_session()
        try:
            privkey = self._get_private_key(account, chain)

            # Derive sender address for nonce/chain_id fetch
            sender = private_key_to_address(privkey)

            # Get chain_id from RPC if not provided
            if chain_id is None and rpc:
                chain_id = get_chain_id(rpc)
            elif chain_id is None:
                return {"status": "error", "error": "chain_id or rpc required"}

            # Get nonce from RPC if not provided
            if nonce is None and rpc:
                nonce = get_nonce(rpc, sender)
            elif nonce is None:
                return {"status": "error", "error": "nonce or rpc required"}

            # Convert value
            val = int(value, 16) if isinstance(value, str) and value.startswith("0x") else int(value)

            # Convert data
            data_hex = data if data.startswith("0x") else "0x" + data

            # Estimate gas if not provided and RPC is available
            if gas_limit is None and rpc:
                tx_for_estimate = {
                    "from": sender,
                    "to": to,
                    "data": data_hex,
                    "value": hex(val),
                }
                try:
                    gas_limit = estimate_gas(rpc, tx_for_estimate)
                    # Add 20% buffer
                    gas_limit = int(gas_limit * 1.2)
                except Exception:
                    gas_limit = 200000  # fallback

            if gas_limit is None:
                gas_limit = 200000

            # Get gas price if not provided and RPC is available
            if rpc and gas_price is None and max_fee_per_gas is None:
                gas_info = get_gas_price(rpc)
                max_fee_per_gas = gas_info["maxFeePerGas"]
                max_priority_fee_per_gas = gas_info["maxPriorityFeePerGas"]

            # Try EIP-1559 if we have fee info, otherwise legacy
            if max_fee_per_gas is not None:
                signed_hex = sign_eip1559_tx(
                    to=to,
                    data=data_hex,
                    value=val,
                    gas_limit=gas_limit,
                    max_fee_per_gas=max_fee_per_gas,
                    max_priority_fee_per_gas=max_priority_fee_per_gas if max_priority_fee_per_gas else 0,
                    nonce=nonce,
                    chain_id=chain_id,
                    privkey_hex=privkey,
                )
            elif gas_price is not None:
                signed_hex = sign_legacy_tx(
                    to=to,
                    data=data_hex,
                    value=val,
                    gas_limit=gas_limit,
                    gas_price=gas_price,
                    nonce=nonce,
                    chain_id=chain_id,
                    privkey_hex=privkey,
                )
            else:
                return {"status": "error", "error": "Need gas_price or max_fee_per_gas or rpc"}

            return {
                "status": "ok",
                "result": {
                    "signed_tx": signed_hex,
                    "from": sender,
                    "to": to,
                    "nonce": nonce,
                    "chain_id": chain_id,
                    "gas_limit": gas_limit,
                }
            }
        except Exception as e:
            return {"status": "error", "error": str(e)}

    def broadcast_tx(self, account: str, to: str, data: str, value: str = "0",
                      chain_id: int = None, rpc: str = None, chain: str = "EVM",
                      gas_limit: int = None, gas_price: int = None,
                      max_fee_per_gas: int = None, max_priority_fee_per_gas: int = None,
                      nonce: int = None) -> dict:
        """Sign and broadcast a transaction. Returns tx hash."""
        self._check_session()
        try:
            # Sign first
            sign_result = self.sign_tx(
                account=account, to=to, data=data, value=value,
                chain_id=chain_id, rpc=rpc, chain=chain,
                gas_limit=gas_limit, gas_price=gas_price,
                max_fee_per_gas=max_fee_per_gas,
                max_priority_fee_per_gas=max_priority_fee_per_gas,
                nonce=nonce,
            )
            if sign_result["status"] != "ok":
                return sign_result

            signed_tx = sign_result["result"]["signed_tx"]

            # Broadcast
            if not rpc:
                return {"status": "error", "error": "rpc URL required for broadcast"}

            tx_hash = broadcast_raw_tx(rpc, signed_tx)
            return {
                "status": "ok",
                "result": {
                    "tx_hash": tx_hash,
                    "signed_tx": signed_tx,
                    "from": sign_result["result"]["from"],
                    "nonce": sign_result["result"]["nonce"],
                }
            }
        except Exception as e:
            return {"status": "error", "error": str(e)}

    def sign_message(self, account: str, message: str, chain: str = "EVM") -> dict:
        """Sign an arbitrary message. Returns signature (r, s, v)."""
        self._check_session()
        try:
            privkey = self._get_private_key(account, chain)
            # Ethereum personal_sign prefix
            if message.startswith("0x"):
                msg_bytes = bytes.fromhex(message[2:])
            else:
                msg_bytes = message.encode('utf-8')

            prefix = b'\x19Ethereum Signed Message:\n' + str(len(msg_bytes)).encode()
            keccak = _get_keccak()
            h = keccak.new(digest_bits=256)
            h.update(prefix + msg_bytes)
            msg_hash = h.digest()

            r, s, v = _ecdsa_sign(msg_hash, int(privkey, 16), chain_id=1, eip1559=True)
            sig = '0x' + r.to_bytes(32, 'big').hex() + s.to_bytes(32, 'big').hex() + bytes([v + 27]).hex()

            return {"status": "ok", "result": {"signature": sig, "r": hex(r), "s": hex(s), "v": v + 27}}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    def handle_command(self, cmd: dict) -> dict:
        """Route a command dict to the appropriate handler."""
        action = cmd.get("cmd")
        if action == "unlock":
            return self.unlock()
        elif action == "status":
            return self.status()
        elif action == "list_accounts":
            return self.list_accounts()
        elif action == "get_address":
            return self.get_address(cmd.get("account"), cmd.get("chain"))
        elif action == "sign_tx":
            return self.sign_tx(
                account=cmd["account"],
                to=cmd["to"],
                data=cmd.get("data", "0x"),
                value=cmd.get("value", "0"),
                chain_id=cmd.get("chain_id"),
                rpc=cmd.get("rpc"),
                chain=cmd.get("chain", "EVM"),
                gas_limit=cmd.get("gas_limit"),
                gas_price=cmd.get("gas_price"),
                max_fee_per_gas=cmd.get("max_fee_per_gas"),
                max_priority_fee_per_gas=cmd.get("max_priority_fee_per_gas"),
                nonce=cmd.get("nonce"),
            )
        elif action == "broadcast_tx":
            return self.broadcast_tx(
                account=cmd["account"],
                to=cmd["to"],
                data=cmd.get("data", "0x"),
                value=cmd.get("value", "0"),
                chain_id=cmd.get("chain_id"),
                rpc=cmd.get("rpc"),
                chain=cmd.get("chain", "EVM"),
                gas_limit=cmd.get("gas_limit"),
                gas_price=cmd.get("gas_price"),
                max_fee_per_gas=cmd.get("max_fee_per_gas"),
                max_priority_fee_per_gas=cmd.get("max_priority_fee_per_gas"),
                nonce=cmd.get("nonce"),
            )
        elif action == "sign_message":
            return self.sign_message(cmd["account"], cmd["message"], cmd.get("chain", "EVM"))
        elif action == "lock":
            self.unlocked = False
            self.vault_data = {}
            return {"status": "ok", "result": "Vault locked"}
        else:
            return {"status": "error", "error": f"Unknown command: {action}"}


def run_stdin(agent: KeyManagerAgent):
    """Run in stdin mode — read JSON commands line by line."""
    print(json.dumps({"status": "ok", "result": "Key Manager Agent ready. Send JSON commands on stdin."}))
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        if line.lower() in ('exit', 'quit', 'q'):
            break
        try:
            cmd = json.loads(line)
            result = agent.handle_command(cmd)
        except json.JSONDecodeError as e:
            result = {"status": "error", "error": f"Invalid JSON: {e}"}
        print(json.dumps(result))
        sys.stdout.flush()


def run_http(agent: KeyManagerAgent, port: int):
    """Run in HTTP server mode on localhost."""
    from http.server import HTTPServer, BaseHTTPRequestHandler

    class Handler(BaseHTTPRequestHandler):
        def _send(self, code, data):
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(data).encode())

        def do_POST(self):
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length).decode()
                cmd = json.loads(body)
                result = agent.handle_command(cmd)
            except Exception as e:
                result = {"status": "error", "error": str(e)}
            self._send(200, result)

        def do_GET(self):
            if self.path == "/status":
                self._send(200, agent.status())
            else:
                self._send(404, {"status": "error", "error": "Use POST with JSON command"})

        def log_message(self, format, *args):
            pass  # Suppress logging

    server = HTTPServer(("127.0.0.1", port), Handler)
    print(json.dumps({"status": "ok", "result": f"Key Manager Agent HTTP server on http://127.0.0.1:{port}"}))
    server.serve_forever()


def main():
    parser = argparse.ArgumentParser(description="Key Manager Agent — headless signing interface")
    parser.add_argument("--vault", required=True, help="Path to key_vault.encrypted")
    parser.add_argument("--password", default=None, help="Vault password (or use KEY_MANAGER_PASSWORD env var)")
    parser.add_argument("--serve", action="store_true", help="Run as HTTP server")
    parser.add_argument("--port", type=int, default=8842, help="HTTP server port (default 8842)")
    parser.add_argument("--timeout", type=int, default=600, help="Session timeout in seconds (default 600)")
    args = parser.parse_args()

    password = args.password or os.environ.get("KEY_MANAGER_PASSWORD")
    if not password:
        # Read from stdin (for secure piping)
        password = sys.stdin.readline().strip()

    if not password:
        print(json.dumps({"status": "error", "error": "No password provided. Use --password, KEY_MANAGER_PASSWORD env var, or pipe via stdin."}))
        sys.exit(1)

    agent = KeyManagerAgent(
        vault_path=args.vault,
        password=password,
        session_timeout=args.timeout,
    )

    if not agent.unlocked:
        print(json.dumps(agent.unlock()))
        if not agent.unlocked:
            sys.exit(1)

    if args.serve:
        run_http(agent, args.port)
    else:
        run_stdin(agent)


if __name__ == '__main__':
    main()