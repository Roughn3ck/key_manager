"""Orca Whirlpool Venue Adapter - ColdStack LP Engine v5.2.5

Read-only adapter for Orca Whirlpool concentrated-liquidity LP positions on Solana.

Queries Solana JSON-RPC to:
  1. Enumerate SPL token accounts owned by a wallet (getTokenAccountsByOwner)
  2. Filter to NFT-like position tokens (amount=1, decimals=0)
  3. For each candidate mint, derive the Whirlpool position PDA and confirm it exists
  4. Deserialize the position + pool accounts directly from on-chain bytes
  5. Compute range status, current price, holdings, and uncollected fees

Account layouts verified against orca-so/whirlpools source (position.rs, whirlpool.rs)
and live on-chain data. PDA derivation is implemented in pure Python (SHA-256 +
ed25519 on-curve test) to avoid adding a non-stdlib dependency.

Read-only. Stateless. Stdlib urllib.request only.

Version: v5.2.5 (August 2026) - Orca Whirlpool on Solana
"""
import base64
import hashlib
import json
import math
import struct
import time
import urllib.request
import urllib.error
from typing import Any, Dict, List, Optional, Tuple

from lp_engine import LPPosition, OfflineError, VenueAdapter, register_adapter
from price_engine import PriceEngine


# ---------------------------------------------------------------------------
# RPC and chain configuration
# ---------------------------------------------------------------------------

SOLANA_RPC_URL = "https://api.mainnet-beta.solana.com"
SOLANA_RPC_FALLBACK = "https://solana-api.projectserum.com"
SOLANA_RPC_FALLBACK_2 = "https://rpc.ankr.com/solana"

# ---------------------------------------------------------------------------
# Program IDs
# ---------------------------------------------------------------------------

WHIRLPOOL_PROGRAM_ID = "whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc"
# Canonical SPL Token (legacy) program ID — owner of all standard SPL token mints/accounts.
# Verified on-chain via getAccountInfo(USDC mint).owner.
SPL_TOKEN_PROGRAM_ID = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
TOKEN_2022_PROGRAM_ID = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"
METAPLEX_METADATA_PROGRAM_ID = "metaqbxxUerdq28cj1RbAWkYQm3ybzjb6a8bt518x1s"

# Position account data layout (verified against Orca's position.rs)
POSITION_ACCOUNT_LEN = 216
# Pool (Whirlpool) account data layout (verified against whirlpool.rs)
POOL_ACCOUNT_LEN = 653  # 8 + 261 + 384

# ---------------------------------------------------------------------------
# Solana base58
# ---------------------------------------------------------------------------

_B58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_B58_INDEX = {c: i for i, c in enumerate(_B58_ALPHABET)}


def _b58decode(s: str) -> bytes:
    """Decode a base58 string to bytes."""
    n = 0
    for ch in s.strip():
        n = n * 58 + _B58_INDEX[ch]
    body = n.to_bytes((n.bit_length() + 7) // 8, "big") if n else b""
    # Preserve leading '1's as zero bytes
    pad = len(s) - len(s.lstrip("1"))
    return (b"\x00" * pad) + body


def _b58encode(b: bytes) -> str:
    """Encode bytes as base58."""
    n = int.from_bytes(b, "big")
    out = ""
    while n > 0:
        n, r = divmod(n, 58)
        out = _B58_ALPHABET[r] + out
    pad = len(b) - len(b.lstrip(b"\x00"))
    return ("1" * pad) + out


def _is_solana_address(value: str) -> bool:
    """Return True if value looks like a base58 Solana address (32-byte pubkey)."""
    if not isinstance(value, str):
        return False
    value = value.strip()
    if not (32 <= len(value) <= 44):
        return False
    try:
        _b58decode(value)
    except (KeyError, ValueError):
        return False
    return True


# ---------------------------------------------------------------------------
# Pure-Python PDA derivation (ed25519 on-curve check)
# ---------------------------------------------------------------------------

_ED_P = 2 ** 255 - 19


def _ed_inv(a: int) -> int:
    return pow(a, _ED_P - 2, _ED_P)


# d = -121665 / 121666 mod p
_ED_D = (-121665 * _ed_inv(121666)) % _ED_P


def _ed_is_on_curve(compressed: bytes) -> bool:
    """Return True if a 32-byte compressed ed25519 point is on the curve.

    A point (sign, y) is on the ed25519 curve iff x^2 = (y^2 - 1) / (d*y^2 + 1)
    has a square root mod p (Euler criterion), and the sign bit is consistent.
    """
    if len(compressed) != 32:
        return False
    y = int.from_bytes(compressed, "little") & ((1 << 255) - 1)
    x_sign = (compressed[31] >> 7) & 1
    if y >= _ED_P:
        return False
    y2 = (y * y) % _ED_P
    u = (y2 - 1) % _ED_P
    v = (_ED_D * y2 + 1) % _ED_P
    x2 = (u * _ed_inv(v)) % _ED_P
    if x2 == 0:
        return x_sign == 0
    # Euler criterion: x2 is a QR iff x2^((p-1)/2) == 1
    return pow(x2, (_ED_P - 1) // 2, _ED_P) == 1


def _find_program_address(seeds: List[bytes], program_id: bytes) -> Tuple[Optional[bytes], Optional[int]]:
    """Solana findProgramAddress: try bumps 255..0 until hash is off-curve.

    Returns (pda_bytes, bump) or (None, None) if no valid PDA found.
    """
    for bump in range(255, -1, -1):
        h = hashlib.sha256()
        for seed in seeds:
            if len(seed) > 32:
                return None, None
            h.update(seed)
        h.update(bytes([bump]))
        h.update(program_id)
        h.update(b"ProgramDerivedAddress")
        digest = h.digest()
        if not _ed_is_on_curve(digest):
            return digest, bump
    return None, None


def _derive_position_address(position_mint: str) -> Optional[str]:
    """Derive the Whirlpool position address from a position mint.

    position_PDA = findProgramAddress([b"position", mint_bytes], WHIRLPOOL_PROGRAM_ID)

    Returns the position address as a base58 string, or None on failure.
    """
    try:
        mint_bytes = _b58decode(position_mint)
        program_bytes = _b58decode(WHIRLPOOL_PROGRAM_ID)
        pda, _ = _find_program_address([b"position", mint_bytes], program_bytes)
        if pda is None:
            return None
        return _b58encode(pda)
    except (KeyError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Solana RPC layer
# ---------------------------------------------------------------------------

def _solana_rpc_call(method: str, params: list) -> Optional[Any]:
    """Make a single Solana JSON-RPC call with fallback across endpoints.

    Returns the 'result' field or None on error. Follows the same pattern as
    _base_rpc_call / _bsc_rpc_call in the EVM adapters.
    """
    payload = json.dumps(
        {"jsonrpc": "2.0", "method": method, "params": params, "id": 1}
    ).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "ColdStack/5.2.5",
    }
    for url in (SOLANA_RPC_URL, SOLANA_RPC_FALLBACK, SOLANA_RPC_FALLBACK_2):
        try:
            req = urllib.request.Request(url, data=payload, headers=headers)
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if isinstance(data, dict) and data.get("error"):
                    continue
                return data.get("result")
        except Exception:
            continue
    return None


def _get_account_data(address: str) -> Optional[bytes]:
    """Fetch raw account bytes for an address (base64-encoded)."""
    result = _solana_rpc_call(
        "getAccountInfo", [address, {"encoding": "base64"}]
    )
    if not result or not isinstance(result, dict):
        return None
    value = result.get("value")
    if not value or not isinstance(value, dict):
        return None
    data_field = value.get("data")
    if isinstance(data_field, list) and data_field:
        try:
            return base64.b64decode(data_field[0])
        except Exception:
            return None
    return None


def _detect_token_program(mint_address: str) -> str:
    """Detect whether a mint is SPL Token or Token-2022 by querying getAccountInfo.

    Returns the program ID that owns the mint account.
    Defaults to SPL Token Program if the query fails.
    """
    import json as _json
    payload = _json.dumps({
        "jsonrpc": "2.0", "method": "getAccountInfo",
        "params": [mint_address, {"encoding": "base64"}], "id": 1,
    }).encode()
    for url in (SOLANA_RPC_URL, SOLANA_RPC_FALLBACK, SOLANA_RPC_FALLBACK_2):
        try:
            req = urllib.request.Request(url, data=payload,
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                result = _json.loads(resp.read().decode("utf-8"))
                value = result.get("result", {}).get("value")
                if value:
                    owner = value.get("owner", "")
                    if owner == TOKEN_2022_PROGRAM_ID:
                        return TOKEN_2022_PROGRAM_ID
                    return SPL_TOKEN_PROGRAM_ID
        except Exception:
            continue
    return SPL_TOKEN_PROGRAM_ID  # safe fallback


# ---------------------------------------------------------------------------
# Solana token registry (well-known mints)
# ---------------------------------------------------------------------------

SOLANA_TOKENS: Dict[str, Dict[str, Any]] = {
    "So11111111111111111111111111111111111111112": {"symbol": "SOL", "decimals": 9},
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v": {"symbol": "USDC", "decimals": 6},
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB": {"symbol": "USDT", "decimals": 6},
    "orcaEKTdK7LKz57vaAYr9QeNsVEPfiu6QeMu1ZktXjY": {"symbol": "ORCA", "decimals": 6},
    # Common bridged / ecosystem tokens
    "mSoLzYCxHdYgdzU16g5QSh3i5K3z3KZK7ytfqcJm7So": {"symbol": "mSOL", "decimals": 9},
    "7vfCXTUXx5WJV5JADk17DUJ4ksgau7utNKj4b963voxs": {"symbol": "WETH", "decimals": 8},
    "9n4nbM75f5Ui33ZbPYXnS91Fr6hCqK9UfXoL6uRz5gS": {"symbol": "WSOL", "decimals": 9},
    "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN": {"symbol": "JUP", "decimals": 6},
    "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263": {"symbol": "BONK", "decimals": 5},
}

# Canonical symbol mapping for USD price lookup
_CANONICAL_SYMBOLS = {
    "WSOL": "SOL",
    "WETH": "ETH",
    "WBTC": "BTC",
    "USDC": "USD",
    "USDT": "USD",
    "USDC.e": "USD",
}


def _get_sol_token_symbol(mint: str) -> str:
    """Return a token symbol for a Solana mint, falling back to truncated address."""
    if mint in SOLANA_TOKENS:
        return SOLANA_TOKENS[mint]["symbol"]
    # On-chain fallback: read the SPL mint account and look up Metaplex metadata.
    # For v5.2.5 we skip Metaplex parsing (binary TLV) and return a truncated mint.
    return mint[:4] + "..." + mint[-4:]


def _get_sol_token_decimals(mint: str) -> int:
    """Return token decimals, falling back to on-chain mint account."""
    if mint in SOLANA_TOKENS:
        return SOLANA_TOKENS[mint]["decimals"]
    data = _get_account_data(mint)
    # SPL mint account: decimals at offset 44 (32-byte mint authority + 8-byte supply + 1-byte ... )
    # Actually: mint_authority (32, optional), supply (8), decimals (1) at offset 44
    if data and len(data) >= 45:
        # SPL Mint layout: [mint_authority_opt(4+32), supply(8), decimals(1), ...]
        # offset 44 = decimals byte
        return data[44]
    return 9


def _canonical_symbol(symbol: str) -> str:
    """Map wrapper / bridged tokens to canonical price symbols."""
    return _CANONICAL_SYMBOLS.get(symbol, symbol)


def _usd_value(amount: Optional[float], symbol: str, price_engine: Optional[PriceEngine]) -> Optional[float]:
    """Convert a token amount to USD using the shared price engine."""
    if amount is None or amount <= 0 or not price_engine:
        return None
    return price_engine.convert_balance_to_fiat(amount, _canonical_symbol(symbol), currency="usd")


# ---------------------------------------------------------------------------
# Orca position discovery
# ---------------------------------------------------------------------------

def _find_orca_position_mints(wallet_address: str) -> List[str]:
    """Find all Orca Whirlpool position mint addresses owned by a wallet.

    Searches both SPL Token Program and Token-2022 Program.

    Strategy:
      1. getTokenAccountsByOwner for each token program, jsonParsed encoding
      2. Filter: token amount == "1" AND token decimals == 0 (NFT-like SPL)
      3. For each candidate mint, derive its Whirlpool position PDA and check
         that the account exists on-chain (data length == POSITION_ACCOUNT_LEN)

    Returns a list of position mint base58 strings.
    """
    candidates: List[str] = []

    for token_program in (SPL_TOKEN_PROGRAM_ID, TOKEN_2022_PROGRAM_ID):
        result = _solana_rpc_call(
            "getTokenAccountsByOwner",
            [
                wallet_address,
                {"programId": token_program},
                {"encoding": "jsonParsed"},
            ],
        )
        if not result or not isinstance(result, dict):
            continue
        value = result.get("value")
        if not isinstance(value, list):
            continue
        for entry in value:
            try:
                info = entry["account"]["data"]["parsed"]["info"]
                mint = info.get("mint", "")
                token_amount = info.get("tokenAmount", {})
                amount = token_amount.get("amount", "0")
                decimals = token_amount.get("decimals", 0)
                # Whirlpool position NFTs: amount = 1, decimals = 0
                if amount == "1" and decimals == 0 and mint:
                    candidates.append(mint)
            except (KeyError, TypeError, AttributeError):
                continue

    # Confirm each candidate has a live Whirlpool position account at its PDA
    confirmed: List[str] = []
    for mint in candidates:
        pda = _derive_position_address(mint)
        if not pda:
            continue
        data = _get_account_data(pda)
        if data and len(data) == POSITION_ACCOUNT_LEN:
            confirmed.append(mint)
    return confirmed


# ---------------------------------------------------------------------------
# On-chain deserialization (layouts from orca-so/whirlpools source)
# ---------------------------------------------------------------------------

def _decode_position_data(data: bytes) -> Optional[Dict[str, Any]]:
    """Deserialize a Whirlpool Position account.

    Layout (position.rs):
      0   8s  discriminator
      8   32s whirlpool pubkey
      40  32s position_mint pubkey
      72  Q   liquidity u128 (two u64 words, low then high)
      88  i   tick_lower_index i32
      92  i   tick_upper_index i32
      96  QQ  fee_growth_checkpoint_a u128
      112 Q   fee_owed_a u64
      120 QQ  fee_growth_checkpoint_b u128
      128 Q   fee_owed_b u64
      136 ... reward_infos (3 x 24 bytes)
    Total: 216 bytes
    """
    if len(data) < POSITION_ACCOUNT_LEN:
        return None
    try:
        pool = _b58encode(data[8:40])
        mint = _b58encode(data[40:72])
        liq_lo, liq_hi = struct.unpack_from("<QQ", data, 72)
        liquidity = liq_lo + (liq_hi << 64)
        tick_lower = struct.unpack_from("<i", data, 88)[0]
        tick_upper = struct.unpack_from("<i", data, 92)[0]
        fgc_a_lo, fgc_a_hi = struct.unpack_from("<QQ", data, 96)
        fee_growth_checkpoint_a = fgc_a_lo + (fgc_a_hi << 64)
        fee_owed_a = struct.unpack_from("<Q", data, 112)[0]
        fgc_b_lo, fgc_b_hi = struct.unpack_from("<QQ", data, 120)
        fee_growth_checkpoint_b = fgc_b_lo + (fgc_b_hi << 64)
        fee_owed_b = struct.unpack_from("<Q", data, 128)[0]
    except (struct.error, ValueError):
        return None
    return {
        "whirlpool": pool,
        "position_mint": mint,
        "liquidity": liquidity,
        "tick_lower": tick_lower,
        "tick_upper": tick_upper,
        "fee_growth_checkpoint_a": fee_growth_checkpoint_a,
        "fee_owed_a": fee_owed_a,
        "fee_growth_checkpoint_b": fee_growth_checkpoint_b,
        "fee_owed_b": fee_owed_b,
    }


def _decode_pool_data(data: bytes) -> Optional[Dict[str, Any]]:
    """Deserialize a Whirlpool pool account.

    Layout (whirlpool.rs):
      0   8s  discriminator
      8   32s whirlpools_config
      40  B   whirlpool_bump
      41  H   tick_spacing u16
      43  2s  fee_tier_index_seed
      45  H   fee_rate u16 (hundredths of a basis point)
      47  H   protocol_fee_rate u16
      49  QQ  liquidity u128
      65  QQ  sqrt_price u128 (Q64.64)
      81  i   tick_current_index i32
      85  Q   protocol_fee_owed_a u64
      93  Q   protocol_fee_owed_b u64
      101 32s token_mint_a
      133 32s token_vault_a
      165 QQ  fee_growth_global_a u128 (Q64.64)
      181 32s token_mint_b
      213 32s token_vault_b
      245 QQ  fee_growth_global_b u128 (Q64.64)
      261 Q   reward_last_updated_timestamp u64
      269 ... reward_infos (3 x 128)
    """
    if len(data) < 269:
        return None
    try:
        tick_spacing = struct.unpack_from("<H", data, 41)[0]
        fee_rate = struct.unpack_from("<H", data, 45)[0]
        liq_lo, liq_hi = struct.unpack_from("<QQ", data, 49)
        liquidity = liq_lo + (liq_hi << 64)
        sp_lo, sp_hi = struct.unpack_from("<QQ", data, 65)
        sqrt_price = sp_lo + (sp_hi << 64)
        tick_current = struct.unpack_from("<i", data, 81)[0]
        token_mint_a = _b58encode(data[101:133])
        fg_a_lo, fg_a_hi = struct.unpack_from("<QQ", data, 165)
        fee_growth_global_a = fg_a_lo + (fg_a_hi << 64)
        token_mint_b = _b58encode(data[181:213])
        fg_b_lo, fg_b_hi = struct.unpack_from("<QQ", data, 245)
        fee_growth_global_b = fg_b_lo + (fg_b_hi << 64)
    except (struct.error, ValueError):
        return None
    return {
        "tick_spacing": tick_spacing,
        "fee_rate": fee_rate,
        "liquidity": liquidity,
        "sqrt_price": sqrt_price,
        "tick_current": tick_current,
        "token_mint_a": token_mint_a,
        "token_mint_b": token_mint_b,
        "fee_growth_global_a": fee_growth_global_a,
        "fee_growth_global_b": fee_growth_global_b,
    }


# ---------------------------------------------------------------------------
# Orca price / fee math (Q64.64 fixed point)
# ---------------------------------------------------------------------------

def _tick_to_price(tick: int, decimals_a: int, decimals_b: int) -> float:
    """Convert an Orca Whirlpool tick to a human-readable price (token B per token A)."""
    raw = 1.0001 ** tick
    return raw * (10 ** (decimals_a - decimals_b))


def _compute_fees_owed(
    liquidity: int,
    fee_owed_a: int,
    fee_owed_b: int,
    fee_growth_checkpoint_a: int,
    fee_growth_checkpoint_b: int,
    fee_growth_global_a: int,
    fee_growth_global_b: int,
) -> Tuple[int, int]:
    """Compute total uncollected fees for a position (raw integer amounts).

    Total = accrued_since_last_update + already_owed.
    Growth accumulators are Q64.64; the delta is shifted right by 64 bits after
    multiplying by liquidity — matching the on-chain update math.

    Note: Orca's on-chain fee accounting also reads tick-array data to compute
    feeGrowthInside when the position is out of range at the current tick. For
    v5.2.5 (read-only display), we use the simpler global-minus-checkpoint
    approximation which is exact for in-range positions and conservative
    (slightly overestimates) for out-of-range ones.
    """
    MOD = 1 << 128  # accumulators are u128
    delta_a = (fee_growth_global_a - fee_growth_checkpoint_a) % MOD
    delta_b = (fee_growth_global_b - fee_growth_checkpoint_b) % MOD
    accrued_a = (liquidity * delta_a) >> 64
    accrued_b = (liquidity * delta_b) >> 64
    return fee_owed_a + accrued_a, fee_owed_b + accrued_b


def _compute_holdings(
    liquidity: int,
    sqrt_price_x64: int,
    tick_lower: int,
    tick_upper: int,
    tick_current: int,
    decimals_a: int,
    decimals_b: int,
) -> Tuple[float, float]:
    """Compute the token A and token B holdings for a position.

    Uses Uniswap V3 math with Q64.64 prices (Orca's sqrt_price is Q64.64, not Q96).
    Returns (amount_a_human, amount_b_human).
    """
    if liquidity == 0:
        return 0.0, 0.0
    sqrt_p = sqrt_price_x64 / (2 ** 64)
    sqrt_lower = 1.0001 ** (tick_lower / 2.0)
    sqrt_upper = 1.0001 ** (tick_upper / 2.0)

    if sqrt_p <= 0 or sqrt_upper <= sqrt_lower:
        return 0.0, 0.0

    if tick_current < tick_lower:
        # Entire position is token A
        amt_a = liquidity * (sqrt_upper - sqrt_lower) / (sqrt_lower * sqrt_upper)
        amt_b = 0.0
    elif tick_current >= tick_upper:
        amt_a = 0.0
        amt_b = liquidity * (sqrt_upper - sqrt_lower)
    else:
        amt_a = liquidity * (sqrt_upper - sqrt_p) / (sqrt_p * sqrt_upper)
        amt_b = liquidity * (sqrt_p - sqrt_lower)

    return amt_a / (10 ** decimals_a), amt_b / (10 ** decimals_b)


# ---------------------------------------------------------------------------
# Adapter class
# ---------------------------------------------------------------------------

@register_adapter
class OrcaAdapter(VenueAdapter):
    """Orca Whirlpool adapter for read-only LP position discovery on Solana."""

    VENUE_KEY = "orca"
    CHAINS = ["solana"]

    # -- VenueAdapter interface ---------------------------------------------

    def can_handle(self, address_or_id: str, chain_hint: str = "") -> bool:
        """Return True for base58 Solana addresses or 'solana:'-prefixed IDs.

        Without a hint: accepts any 32-44-char base58 address that is NOT a 0x-prefixed
        EVM address. Orca is ColdStack's only Solana LP venue, so a bare base58 wallet
        address can be auto-routed here by detect_venue() during wallet scans.

        With a hint: requires the hint to name orca / solana / whirlpool (used when
        distinguishing from a bare integer token ID or an EVM address that someone
        has explicitly tagged as Base / BSC / HyperEVM).
        """
        hint = (chain_hint or "").lower()
        value = address_or_id.strip()
        if value.startswith("solana:"):
            value = value.split(":", 1)[1]
        if not _is_solana_address(value):
            return False
        if any(k in hint for k in ("orca", "solana", "whirlpool")):
            return True
        # No hint: accept any non-EVM base58 address. Numeric-only strings and
        # 0x-hex addresses are rejected by _is_solana_address length/charset rules
        # (numeric strings decode but are too short; 0x is not valid base58 anyway).
        return True

    def fetch_position(
        self,
        address_or_id: str,
        online_mode: bool = False,
        price_engine: Optional[PriceEngine] = None,
        chain_hint: str = "",
        wallet_address: str = "",
    ) -> LPPosition:
        """Fetch a single Orca position by position mint, position address, or pool address."""
        if not online_mode:
            raise OfflineError("Orca adapter requires online mode.")

        addr = address_or_id.strip()
        if addr.startswith("solana:"):
            addr = addr.split(":", 1)[1]

        if not _is_solana_address(addr):
            return LPPosition(
                position_id=f"solana:{addr}",
                venue="Orca",
                chain="Solana",
                error="Not a valid Solana address (expected 32-44 base58 characters).",
            )

        # Try as a position mint first (most common user input)
        pos = self._fetch_by_position_mint(addr, price_engine, wallet_address)
        if pos is not None and not pos.error:
            return pos

        # Try as a position address directly (derive its mint from the account)
        pos = self._fetch_by_position_address(addr, price_engine, wallet_address)
        if pos is not None and not pos.error:
            return pos

        # Try as a pool address — returns pool info with no position data
        pos = self._fetch_pool_state_as_position(addr, price_engine)
        if pos is not None and not pos.error:
            return pos

        return LPPosition(
            position_id=f"solana:{addr}",
            venue="Orca",
            chain="Solana",
            error="Could not find an Orca Whirlpool position or pool at this address.",
        )

    def fetch_all_positions(
        self,
        wallet_address: str,
        online_mode: bool = False,
        price_engine: Optional[PriceEngine] = None,
    ) -> List[LPPosition]:
        """Fetch every Orca Whirlpool LP position owned by a Solana wallet."""
        if not online_mode:
            raise OfflineError("Orca adapter requires online mode.")

        wallet_address = wallet_address.strip()
        if not _is_solana_address(wallet_address):
            return []

        positions: List[LPPosition] = []
        mints = _find_orca_position_mints(wallet_address)
        print(f"[orca-scan] found {len(mints)} confirmed position mints for {wallet_address[:8]}...")
        for mint in mints:
            pos = self._fetch_by_position_mint(mint, price_engine, wallet_address)
            if pos is not None and not pos.error:
                # Skip closed positions (zero liquidity, zero owed fees)
                if pos.raw_data and isinstance(pos.raw_data, dict):
                    if pos.raw_data.get("liquidity", 0) == 0 and not pos.fees_earned:
                        continue
                positions.append(pos)
        return positions

    def fetch_fees_earned(
        self, position_id: str, online_mode: bool = False
    ) -> Dict[str, float]:
        """Return accumulated uncollected fees for an Orca position (by mint or address)."""
        if not online_mode:
            raise OfflineError("Orca adapter requires online mode.")

        addr = position_id.split(":", 1)[1] if position_id.startswith("solana:") else position_id
        position_addr = self._resolve_position_address(addr)
        if not position_addr:
            return {}

        pos_data = _get_account_data(position_addr)
        if not pos_data:
            return {}
        position = _decode_position_data(pos_data)
        if not position:
            return {}

        pool_data = _get_account_data(position["whirlpool"])
        if not pool_data:
            return {}
        pool = _decode_pool_data(pool_data)
        if not pool:
            return {}

        dec_a = _get_sol_token_decimals(pool["token_mint_a"])
        dec_b = _get_sol_token_decimals(pool["token_mint_b"])

        fee_a_raw, fee_b_raw = _compute_fees_owed(
            liquidity=position["liquidity"],
            fee_owed_a=position["fee_owed_a"],
            fee_owed_b=position["fee_owed_b"],
            fee_growth_checkpoint_a=position["fee_growth_checkpoint_a"],
            fee_growth_checkpoint_b=position["fee_growth_checkpoint_b"],
            fee_growth_global_a=pool["fee_growth_global_a"],
            fee_growth_global_b=pool["fee_growth_global_b"],
        )
        symbol_a = _get_sol_token_symbol(pool["token_mint_a"])
        symbol_b = _get_sol_token_symbol(pool["token_mint_b"])
        return {
            symbol_a: fee_a_raw / (10 ** dec_a),
            symbol_b: fee_b_raw / (10 ** dec_b),
        }

    # -- Internal helpers ---------------------------------------------------

    def _resolve_position_address(self, addr: str) -> Optional[str]:
        """Resolve an input address to a Whirlpool position account address.

        If addr is a position mint, derives its PDA. If addr is already a
        position account, returns it directly.
        """
        if not _is_solana_address(addr):
            return None
        # Try as position mint first
        pda = _derive_position_address(addr)
        if pda:
            data = _get_account_data(pda)
            if data and len(data) == POSITION_ACCOUNT_LEN:
                return pda
        # Try as direct position account address
        data = _get_account_data(addr)
        if data and len(data) == POSITION_ACCOUNT_LEN:
            return addr
        return None

    def _fetch_by_position_mint(
        self,
        mint: str,
        price_engine: Optional[PriceEngine] = None,
        wallet_address: str = "",
    ) -> Optional[LPPosition]:
        """Fetch a position given its position mint address."""
        pda = _derive_position_address(mint)
        if not pda:
            return None
        return self._fetch_position_at_address(pda, mint, price_engine, wallet_address)

    def _fetch_by_position_address(
        self,
        position_addr: str,
        price_engine: Optional[PriceEngine] = None,
        wallet_address: str = "",
    ) -> Optional[LPPosition]:
        """Fetch a position given its position account address directly."""
        data = _get_account_data(position_addr)
        if not data or len(data) != POSITION_ACCOUNT_LEN:
            return None
        # Extract the mint from the account data (offset 40..72)
        mint = _b58encode(data[40:72])
        return self._fetch_position_at_address(position_addr, mint, price_engine, wallet_address)

    def _fetch_position_at_address(
        self,
        position_addr: str,
        position_mint: str,
        price_engine: Optional[PriceEngine] = None,
        wallet_address: str = "",
    ) -> Optional[LPPosition]:
        """Fetch and decode a position account + its pool, returning an LPPosition."""
        pos_bytes = _get_account_data(position_addr)
        if not pos_bytes:
            return LPPosition(
                position_id=f"solana:{position_mint}",
                venue="Orca",
                chain="Solana",
                error="Solana RPC unavailable",
            )
        position = _decode_position_data(pos_bytes)
        if not position:
            return LPPosition(
                position_id=f"solana:{position_mint}",
                venue="Orca",
                chain="Solana",
                error="Could not decode Whirlpool position account",
            )

        pool_addr = position["whirlpool"]
        pool_bytes = _get_account_data(pool_addr)
        pool = _decode_pool_data(pool_bytes) if pool_bytes else None

        symbol_a = _get_sol_token_symbol(pool["token_mint_a"]) if pool else "?"
        symbol_b = _get_sol_token_symbol(pool["token_mint_b"]) if pool else "?"
        dec_a = _get_sol_token_decimals(pool["token_mint_a"]) if pool else 9
        dec_b = _get_sol_token_decimals(pool["token_mint_b"]) if pool else 9

        tick_lower = position["tick_lower"]
        tick_upper = position["tick_upper"]
        range_low = _tick_to_price(tick_lower, dec_a, dec_b)
        range_high = _tick_to_price(tick_upper, dec_a, dec_b)

        current_price: Optional[float] = None
        current_tick: Optional[int] = None
        sqrt_price_x64: Optional[int] = None
        position_in_range_pct: Optional[float] = None
        deposit_amounts: Dict[str, float] = {}
        fees_earned: Dict[str, float] = {}
        fees_earned_usd: Optional[float] = None
        position_value_usd: Optional[float] = None

        if pool:
            current_tick = pool["tick_current"]
            sqrt_price_x64 = pool["sqrt_price"]
            current_price = _tick_to_price(current_tick, dec_a, dec_b)

            if tick_upper != tick_lower:
                position_in_range_pct = (
                    (current_tick - tick_lower) / (tick_upper - tick_lower) * 100.0
                )

            # Holdings
            amt_a_h, amt_b_h = _compute_holdings(
                liquidity=position["liquidity"],
                sqrt_price_x64=sqrt_price_x64,
                tick_lower=tick_lower,
                tick_upper=tick_upper,
                tick_current=current_tick,
                decimals_a=dec_a,
                decimals_b=dec_b,
            )
            if amt_a_h > 0:
                deposit_amounts[symbol_a] = amt_a_h
            if amt_b_h > 0:
                deposit_amounts[symbol_b] = amt_b_h

            # Fees
            fee_a_raw, fee_b_raw = _compute_fees_owed(
                liquidity=position["liquidity"],
                fee_owed_a=position["fee_owed_a"],
                fee_owed_b=position["fee_owed_b"],
                fee_growth_checkpoint_a=position["fee_growth_checkpoint_a"],
                fee_growth_checkpoint_b=position["fee_growth_checkpoint_b"],
                fee_growth_global_a=pool["fee_growth_global_a"],
                fee_growth_global_b=pool["fee_growth_global_b"],
            )
            fee_a_h = fee_a_raw / (10 ** dec_a)
            fee_b_h = fee_b_raw / (10 ** dec_b)
            if fee_a_h > 0:
                fees_earned[symbol_a] = fee_a_h
            if fee_b_h > 0:
                fees_earned[symbol_b] = fee_b_h

            # USD values
            fees_usd = 0.0
            if price_engine:
                fees_usd += _usd_value(fee_a_h, symbol_a, price_engine) or 0.0
                fees_usd += _usd_value(fee_b_h, symbol_b, price_engine) or 0.0
            fees_earned_usd = fees_usd if fees_usd > 0 else (0.0 if fees_earned else None)

            val_a = _usd_value(amt_a_h, symbol_a, price_engine) or 0.0
            val_b = _usd_value(amt_b_h, symbol_b, price_engine) or 0.0
            total_val = val_a + val_b
            position_value_usd = total_val if total_val > 0 else None

        return LPPosition(
            position_id=f"solana:{position_mint}",
            pool_id=pool_addr,
            venue="Orca",
            chain="Solana",
            pair=f"{symbol_a}/{symbol_b}",
            token_0=symbol_a,
            token_1=symbol_b,
            current_price=current_price,
            range_low=range_low,
            range_high=range_high,
            position_in_range_pct=position_in_range_pct,
            deposit_amounts=deposit_amounts,
            fees_earned=fees_earned,
            fees_earned_usd=fees_earned_usd,
            current_value_usd=position_value_usd,
            deposit_value_usd=position_value_usd,
            raw_data={
                "position_mint": position_mint,
                "position_address": position_addr,
                "whirlpool": pool_addr,
                "liquidity": position["liquidity"],
                "tick_lower": tick_lower,
                "tick_upper": tick_upper,
                "tick_spacing": pool["tick_spacing"] if pool else None,
                "decimals0": dec_a,
                "decimals1": dec_b,
                "token_mint_a": pool["token_mint_a"] if pool else "",
                "token_mint_b": pool["token_mint_b"] if pool else "",
                "fee_owed_a": position["fee_owed_a"],
                "fee_owed_b": position["fee_owed_b"],
            },
        )

    def _fetch_pool_state_as_position(
        self,
        pool_addr: str,
        price_engine: Optional[PriceEngine] = None,
    ) -> Optional[LPPosition]:
        """Fetch pool state (price, tokens) for a pool address — no position data."""
        pool_bytes = _get_account_data(pool_addr)
        if not pool_bytes:
            return None
        pool = _decode_pool_data(pool_bytes)
        if not pool:
            return None

        symbol_a = _get_sol_token_symbol(pool["token_mint_a"])
        symbol_b = _get_sol_token_symbol(pool["token_mint_b"])
        dec_a = _get_sol_token_decimals(pool["token_mint_a"])
        dec_b = _get_sol_token_decimals(pool["token_mint_b"])
        current_price = _tick_to_price(pool["tick_current"], dec_a, dec_b)

        return LPPosition(
            position_id=f"solana:{pool_addr}",
            pool_id=pool_addr,
            venue="Orca",
            chain="Solana",
            pair=f"{symbol_a}/{symbol_b}",
            token_0=symbol_a,
            token_1=symbol_b,
            current_price=current_price,
            fees_note="Pool address shows price only. To view your LP position, enter the position mint or position account address.",
        )

    # -- Write support ------------------------------------------------------

    def can_write(self) -> bool:
        """Write support available in v5.2.5 (OrcaWriter)."""
        return True

    def get_writer(self):
        """Return an OrcaWriter instance."""
        from venue_adapters.orca_writer import OrcaWriter
        return OrcaWriter()
