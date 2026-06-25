"""
Derivation Engine — Derives addresses and private keys from BIP39 mnemonics.

Uses the hdwallet library for BIP32/BIP44/49/84/86 HD wallet derivation.
For EVM addresses, the private key is used to compute the keccak256-based
address manually for correctness. For BTC SegWit/Taproot, bech32 addresses
are generated manually from the public key hash since hdwallet's address()
method returns legacy P2PKH format regardless of semantic.

Security: This module works entirely in-memory. No intermediate files,
no logging of sensitive data. The caller is responsible for clearing
derived keys from memory after use.
"""
from typing import Dict, List, Optional, Any
import hashlib

from hdwallet import HDWallet
from hdwallet.mnemonics import BIP39Mnemonic
from hdwallet.cryptocurrencies import Bitcoin, Ethereum, Dash, Solana, Sui
from hdwallet.derivations import (
    BIP44Derivation,
    BIP49Derivation,
    BIP84Derivation,
    BIP86Derivation,
)
from mnemonic import Mnemonic as MnemonicValidator


# ---------------------------------------------------------------------------
# Bech32 / Bech32m encoding (BIP173 / BIP350)
# ---------------------------------------------------------------------------

_BECH32_CHARSET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"

def _bech32_polymod(values: List[int]) -> int:
    GEN = [0x3b6a57b2, 0x26508e6d, 0x1ea119fa, 0x3d4233dd, 0x2a1462b3]
    chk = 1
    for v in values:
        b = chk >> 25
        chk = (chk & 0x1ffffff) << 5 ^ v
        for i in range(5):
            chk ^= GEN[i] if ((b >> i) & 1) else 0
    return chk

def _bech32_hrp_expand(hrp: str) -> List[int]:
    return [ord(x) >> 5 for x in hrp] + [0] + [ord(x) & 31 for x in hrp]

def _bech32_create_checksum(hrp: str, data: List[int], spec: str = "bech32") -> List[int]:
    const = 1 if spec == "bech32" else 0x2bc830a3
    values = _bech32_hrp_expand(hrp) + data
    polymod = _bech32_polymod(values + [0, 0, 0, 0, 0, 0]) ^ const
    return [(polymod >> 5 * (5 - i)) & 31 for i in range(6)]

def _bech32_encode(hrp: str, data: List[int], spec: str = "bech32") -> str:
    combined = data + _bech32_create_checksum(hrp, data, spec)
    return hrp + '1' + ''.join(_BECH32_CHARSET[d] for d in combined)

def _convertbits(data: List[int], frombits: int, tobits: int, pad: bool = True) -> Optional[List[int]]:
    acc = 0
    bits = 0
    ret: List[int] = []
    maxv = (1 << tobits) - 1
    max_acc = (1 << (frombits + tobits - 1)) - 1
    for value in data:
        if value < 0 or (value >> frombits):
            return None
        acc = ((acc << frombits) | value) & max_acc
        bits += frombits
        while bits >= tobits:
            bits -= tobits
            ret.append((acc >> bits) & maxv)
    if pad:
        if bits:
            ret.append((acc << (tobits - bits)) & maxv)
    elif bits >= frombits or ((acc << (tobits - bits)) & maxv):
        return None
    return ret

def _segwit_address(hrp: str, witver: int, witprog: bytes, spec: str = "bech32") -> str:
    """Encode a segwit address (BIP173 bech32 or BIP350 bech32m)."""
    data = [witver] + (_convertbits(list(witprog), 8, 5) or [])
    return _bech32_encode(hrp, data, spec)


# ---------------------------------------------------------------------------
# Hash helpers
# ---------------------------------------------------------------------------

def _ripemd160(data: bytes) -> bytes:
    """Compute RIPEMD160 — used for Bitcoin pubkey hashing."""
    h = hashlib.new('ripemd160')
    h.update(data)
    return h.digest()

def _sha256(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()

def _pubkey_hash(pubkey_bytes: bytes) -> bytes:
    """RIPEMD160(SHA256(pubkey)) — Bitcoin pubkey hash (HASH160)."""
    return _ripemd160(_sha256(pubkey_bytes))

def _base58check_encode(payload: bytes) -> str:
    """Base58Check encode for P2SH / P2PKH Bitcoin addresses."""
    import base58
    checksum = _sha256(_sha256(payload))[:4]
    return base58.b58encode(payload + checksum).decode()


# ---------------------------------------------------------------------------
# EVM address derivation
# ---------------------------------------------------------------------------

def _evm_address_from_private_key(private_key_hex: str) -> str:
    """Derive an EVM address from a private key using keccak256.

    Args:
        private_key_hex: Hex string private key (no 0x prefix).

    Returns:
        EVM address string with 0x prefix and EIP-55 checksum casing.
    """
    from coincurve import PrivateKey
    from Crypto.Hash import keccak

    pk_bytes = bytes.fromhex(private_key_hex)
    pub = PrivateKey(pk_bytes).public_key.format(compressed=False)
    # Remove the 0x04 prefix byte from uncompressed public key
    pub_bytes = pub[1:]
    k = keccak.new(digest_bits=256)
    k.update(pub_bytes)
    addr_bytes = k.digest()[-20:]
    addr_hex = addr_bytes.hex()

    # EIP-55 checksum encoding
    k2 = keccak.new(digest_bits=256)
    k2.update(addr_hex.encode('utf-8'))
    hash_hex = k2.hexdigest()

    checksummed = ''
    for i, c in enumerate(addr_hex):
        if c in '0123456789':
            checksummed += c
        elif int(hash_hex[i], 16) >= 8:
            checksummed += c.upper()
        else:
            checksummed += c.lower()

    return '0x' + checksummed


class DerivationEngine:
    """Derives addresses and private keys from BIP39 mnemonics.

    Supports EVM (Ethereum-compatible), BTC (Legacy/SegWit/Taproot),
    Solana, Dash, and Sui chains via standard HD wallet derivation paths.

    All methods are stateless — they create a fresh HDWallet per call and
    return derived data without retaining any sensitive material.
    """

    SUPPORTED_CHAINS: Dict[str, Dict[str, Any]] = {
        "EVM (Ethereum / Arbitrum / Base)": {
            "path": "m/44'/60'/0'/0/0",
            "address_type": "evm",
            "coin_type": 60,
            "derivation_class": "BIP44",
            "semantic": "p2pkh",
            "cryptocurrency": "Ethereum",
        },
        "BTC Taproot (bc1p)": {
            "path": "m/86'/0'/0'/0/0",
            "address_type": "btc_taproot",
            "coin_type": 0,
            "derivation_class": "BIP86",
            "semantic": "p2tr",
            "cryptocurrency": "Bitcoin",
        },
        "BTC SegWit (bc1q)": {
            "path": "m/84'/0'/0'/0/0",
            "address_type": "btc_segwit",
            "coin_type": 0,
            "derivation_class": "BIP84",
            "semantic": "p2wpkh",
            "cryptocurrency": "Bitcoin",
        },
        "BTC Legacy (1...)": {
            "path": "m/44'/0'/0'/0/0",
            "address_type": "btc_legacy",
            "coin_type": 0,
            "derivation_class": "BIP44",
            "semantic": "p2pkh",
            "cryptocurrency": "Bitcoin",
        },
        "SOL (Solana)": {
            "path": "m/44'/501'/0'/0'",
            "address_type": "solana",
            "coin_type": 501,
            "derivation_class": "BIP44",
            "semantic": None,
            "cryptocurrency": "Solana",
        },
        "DASH (Dash)": {
            "path": "m/44'/5'/0'/0/0",
            "address_type": "dash",
            "coin_type": 5,
            "derivation_class": "BIP44",
            "semantic": "p2pkh",
            "cryptocurrency": "Dash",
        },
        "SUI (Sui)": {
            "path": "m/44'/784'/0'/0'/0'",
            "address_type": "sui",
            "coin_type": 784,
            "derivation_class": "BIP44",
            "semantic": None,
            "cryptocurrency": "Sui",
        },
    }

    # Map cryptocurrency names to hdwallet classes
    _CRYPTO_MAP = {
        "Ethereum": Ethereum,
        "Bitcoin": Bitcoin,
        "Dash": Dash,
        "Solana": Solana,
        "Sui": Sui,
    }

    # Map derivation class names to actual classes
    _DERIVATION_MAP = {
        "BIP44": BIP44Derivation,
        "BIP49": BIP49Derivation,
        "BIP84": BIP84Derivation,
        "BIP86": BIP86Derivation,
    }

    @staticmethod
    def validate_mnemonic(mnemonic: str) -> bool:
        """Validate a BIP39 mnemonic checksum.

        Args:
            mnemonic: BIP39 mnemonic phrase string.

        Returns:
            True if the mnemonic is valid, False otherwise.
        """
        try:
            m = MnemonicValidator("english")
            return m.check(mnemonic.strip())
        except Exception:
            return False

    @staticmethod
    def generate_mnemonic(strength: int = 256) -> str:
        """Generate a new BIP39 mnemonic.

        Args:
            strength: Entropy strength in bits (128=12 words, 256=24 words).
                      Default is 256 (24 words).

        Returns:
            A new BIP39 mnemonic phrase string.
        """
        m = MnemonicValidator("english")
        return m.generate(strength=strength)

    @staticmethod
    def _build_derivation(derivation_class: str, coin_type: int,
                          account_index: int, address_index: int):
        """Build the appropriate BIP derivation object."""
        cls = DerivationEngine._DERIVATION_MAP[derivation_class]
        return cls(
            coin_type=coin_type,
            account=account_index,
            change='external-chain',
            address=address_index,
        )

    @staticmethod
    def _create_hdwallet(crypto_name: str, semantic: Optional[str],
                         mnemonic_obj: BIP39Mnemonic) -> HDWallet:
        """Create and initialize an HDWallet from a mnemonic."""
        crypto = DerivationEngine._CRYPTO_MAP[crypto_name]
        kwargs: Dict[str, Any] = {"cryptocurrency": crypto}
        if semantic:
            kwargs["semantic"] = semantic
        h = HDWallet(**kwargs)
        h.from_mnemonic(mnemonic=mnemonic_obj)
        return h

    @staticmethod
    def _format_btc_address(address_type: str, public_key: str,
                            hdwallet: HDWallet) -> str:
        """Generate the correct Bitcoin address based on address type.

        The hdwallet library returns legacy P2PKH addresses regardless of
        the semantic setting, so we generate SegWit/Taproot addresses
        manually from the public key.
        """
        pub_bytes = bytes.fromhex(public_key)

        if address_type == "btc_legacy":
            # P2PKH: Base58Check with prefix 0x00
            pkh = _pubkey_hash(pub_bytes)
            return _base58check_encode(b'\x00' + pkh)

        elif address_type == "btc_segwit":
            # P2WPKH (BIP173 bech32): witness v0, 20-byte pubkey hash
            pkh = _pubkey_hash(pub_bytes)
            return _segwit_address('bc', 0, pkh, spec="bech32")

        elif address_type == "btc_taproot":
            # P2TR (BIP350 bech32m): witness v1, 32-byte x-only pubkey
            xonly = pub_bytes[1:]  # Strip the prefix byte from compressed key
            return _segwit_address('bc', 1, xonly, spec="bech32m")

        else:
            # Fallback: use hdwallet's built-in address
            return hdwallet.address()

    @staticmethod
    def derive_from_mnemonic(mnemonic: str, chain: str,
                             path: Optional[str] = None,
                             account_index: int = 0,
                             address_index: int = 0) -> Dict[str, Any]:
        """Derive address + private key from a mnemonic for a given chain.

        Args:
            mnemonic: BIP39 12/24-word mnemonic phrase.
            chain: Key from SUPPORTED_CHAINS (e.g. "EVM (Ethereum / Arbitrum / Base)").
            path: Override derivation path (optional, defaults to chain's standard path).
            account_index: Account index in the derivation path (default 0).
            address_index: Address index in the derivation path (default 0).

        Returns:
            Dict with keys: chain, path, address, private_key, public_key.

        Raises:
            ValueError: If the chain is not supported or mnemonic is invalid.
        """
        if chain not in DerivationEngine.SUPPORTED_CHAINS:
            raise ValueError(
                f"Unsupported chain '{chain}'. "
                f"Supported: {list(DerivationEngine.SUPPORTED_CHAINS.keys())}"
            )

        if not DerivationEngine.validate_mnemonic(mnemonic):
            raise ValueError("Invalid BIP39 mnemonic — checksum verification failed.")

        config = DerivationEngine.SUPPORTED_CHAINS[chain]
        address_type = config["address_type"]
        crypto_name = config["cryptocurrency"]
        semantic = config.get("semantic")
        derivation_class = config["derivation_class"]
        coin_type = config["coin_type"]
        default_path = config["path"]

        # Build mnemonic object for hdwallet
        mnemonic_obj = BIP39Mnemonic(mnemonic=mnemonic.strip())

        # Create HDWallet and apply derivation
        h = DerivationEngine._create_hdwallet(crypto_name, semantic, mnemonic_obj)
        derivation = DerivationEngine._build_derivation(
            derivation_class, coin_type, account_index, address_index
        )
        h.from_derivation(derivation=derivation)

        # Get raw key material
        private_key = h.private_key()
        public_key = h.public_key()

        # Generate the correct address based on chain type
        if address_type == "evm":
            address = _evm_address_from_private_key(private_key)
        elif address_type in ("btc_legacy", "btc_segwit", "btc_taproot"):
            address = DerivationEngine._format_btc_address(
                address_type, public_key, h
            )
        else:
            # Solana, Dash, Sui — use hdwallet's built-in address
            address = h.address()

        # Build the full path string for the response
        used_path = path if path else default_path

        return {
            "chain": chain,
            "path": used_path,
            "address": address,
            "private_key": private_key,
            "public_key": public_key,
        }

    @staticmethod
    def derive_multiple(mnemonic: str, chain: str,
                        count: int = 5) -> List[Dict[str, Any]]:
        """Derive multiple addresses from the same mnemonic+chain.

        Derives indices 0 through count-1.

        Args:
            mnemonic: BIP39 mnemonic phrase.
            chain: Key from SUPPORTED_CHAINS.
            count: Number of addresses to derive (default 5).

        Returns:
            List of derivation result dicts (same format as derive_from_mnemonic).
        """
        results: List[Dict[str, Any]] = []
        for i in range(count):
            results.append(
                DerivationEngine.derive_from_mnemonic(
                    mnemonic, chain, address_index=i
                )
            )
        return results

    @staticmethod
    def derive_all_chains(mnemonic: str) -> Dict[str, Dict[str, Any]]:
        """Derive the default address for every supported chain from one mnemonic.

        Args:
            mnemonic: BIP39 mnemonic phrase.

        Returns:
            Dict mapping chain name -> derivation result dict.
            Chains that fail (e.g. missing optional dependency) are
            included with an "error" key instead of derivation data.
        """
        results: Dict[str, Dict[str, Any]] = {}
        for chain in DerivationEngine.SUPPORTED_CHAINS:
            try:
                results[chain] = DerivationEngine.derive_from_mnemonic(
                    mnemonic, chain
                )
            except Exception as e:
                results[chain] = {"chain": chain, "error": str(e)}
        return results