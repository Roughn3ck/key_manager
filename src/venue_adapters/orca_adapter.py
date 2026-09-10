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
    # v5.3.5: Coinbase wrapped BTC on Solana — was silently pricing at $0
    # (truncated-mint symbol → price engine miss → whole leg vanished).
    "cbbtcf3aa214zXHbiAZQwf4122FBYbraNdFqgw4iMij": {"symbol": "cbBTC", "decimals": 8},
}

# Canonical symbol mapping for USD price lookup
_CANONICAL_SYMBOLS = {
    "WSOL": "SOL",
    "WETH": "ETH",
    "WBTC": "BTC",
    # v5.3.5: adapter-side mapping — price_engine.PEGGED_TOKENS has the
    # mixed-case key "cbBTC", but convert_balance_to_fiat uppercases the
    # symbol ("CBBTC") and would miss it. Mapping here routes cbBTC → BTC.
    "CBBTC": "BTC",
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


def _is_fallback_symbol(symbol: str) -> bool:
    """Return True if a symbol is the truncated-mint fallback (e.g. 'cbbt…iMij').

    v5.3.5: used to decide whether a leg needs the mint-based Jupiter price
    fallback — a truncated mint string will never be priced by the engine.
    """
    return bool(symbol) and "..." in symbol


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
    """Map wrapper / bridged tokens to canonical price symbols.

    Case-insensitive on the first lookup miss: the map is keyed
    case-sensitively, but price_engine uppercases symbols internally, so a
    mixed-case symbol like "cbBTC" must still resolve to its canonical
    form ("BTC") for pricing.
    """
    if symbol in _CANONICAL_SYMBOLS:
        return _CANONICAL_SYMBOLS[symbol]
    return _CANONICAL_SYMBOLS.get(symbol.upper(), symbol)


# ---------------------------------------------------------------------------
# v5.3.5: Jupiter lite mint-price fallback
# When a leg's symbol is the truncated-mint fallback, the price engine can
# never price it. The Jupiter lite API prices by MINT instead, rescuing the
# leg from silent $0. Module-level cache, 60s TTL, 5s timeout, stdlib only.
# ---------------------------------------------------------------------------

JUPITER_LITE_PRICE_URL = "https://lite-api.jup.ag/price/v3"
_JUPITER_PRICE_TTL = 60.0
_jupiter_price_cache: Dict[str, Tuple[float, Optional[float]]] = {}  # mint -> (ts, usd_price)


def get_jupiter_mint_price(mint: str) -> Optional[float]:
    """Fetch a mint's USD price via the Jupiter lite API (public, no key).

    GET {JUPITER_LITE_PRICE_URL}?ids={mint} → {mint: {"usdPrice": <num>, ...}}

    Cached for 60s. Returns None if unpriceable (caller must NOT silently
    zero the leg — surface an "unpriced" marker instead).
    """
    if not mint:
        return None
    now = time.time()
    cached = _jupiter_price_cache.get(mint)
    if cached and (now - cached[0]) < _JUPITER_PRICE_TTL:
        return cached[1]
    url = f"{JUPITER_LITE_PRICE_URL}?ids={mint}"
    price: Optional[float] = None
    try:
        req = urllib.request.Request(
            url, headers={"Content-Type": "application/json", "User-Agent": "ColdStack/5.3.5"}
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if isinstance(data, dict):
            entry = data.get(mint)
            if isinstance(entry, dict):
                raw = entry.get("usdPrice")
                if raw is not None:
                    price = float(raw)
    except Exception:
        price = None
    _jupiter_price_cache[mint] = (now, price)
    return price


def _usd_value(
    amount: Optional[float],
    symbol: str,
    price_engine: Optional[PriceEngine],
    mint: str = "",
) -> Optional[float]:
    """Convert a token amount to USD using the shared price engine.

    v5.3.5: when the symbol is the truncated-mint fallback (unlistable),
    prices the leg by MINT via the Jupiter lite API. Returns None when the
    leg is genuinely unpriceable so callers can mark it "⚠ unpriced"
    instead of silently valuing it at $0.
    """
    if amount is None or amount <= 0 or not price_engine:
        return None
    canonical = _canonical_symbol(symbol)
    value = price_engine.convert_balance_to_fiat(amount, canonical, currency="usd")
    if value is not None:
        return value
    # Fallback: mint-based pricing via Jupiter for unlisted mints
    if mint and _is_fallback_symbol(symbol):
        jup_price = get_jupiter_mint_price(mint)
        if jup_price is not None:
            return amount * jup_price
    return None


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


# ---------------------------------------------------------------------------
# v5.3.5: Tick-array fee math (feeGrowthInside)
# Ported from the HyperEVM adapter's V3 pattern; Orca specifics:
#   - TickArray PDA seeds: [b"tick_array", whirlpool, start_index]
#     (string seed format verified live by orca_writer's working swaps)
#   - TICK_ARRAY_SIZE = 88 ticks per array
#   - Anchor layout: 8B discriminator + 4B start_tick_index + 88 x 113B
#     Tick entries. Entry i = (tick - start) // tick_spacing.
#   - Tick entry: initialized bool(1) + liquidity_net i128(16) +
#     liquidity_gross u128(16) + fee_growth_outside_a u128(16) +
#     fee_growth_outside_b u128(16) + reward_growths_outside 3xu128(48) = 113B
# The layout was verified empirically against mainnet (v5.3.5 gate).
# ---------------------------------------------------------------------------

TICK_ARRAY_SIZE = 88
_TICK_ARRAY_DISC_LEN = 8
_TICK_ARRAY_START_LEN = 4
_TICK_ENTRY_SIZE = 113
_TICK_ENTRY_FIRST_OFFSET = 12  # discriminator(8) + start_tick_index(4)


def _tick_array_start_index_for(tick: int, tick_spacing: int) -> int:
    """Compute the start_tick_index of the TickArray containing `tick`.

    Each TickArray covers TICK_ARRAY_SIZE * tick_spacing ticks. Python's //
    is floor division, which matches whirlpool-math's negative-tick behavior
    (start indices are multiples of the span, floored toward -inf).
    """
    span = TICK_ARRAY_SIZE * tick_spacing
    return (tick // span) * span


def _derive_tick_array_address_adapter(whirlpool: str, start_tick_index: int) -> Optional[str]:
    """Derive the TickArray PDA for a given start_tick_index (adapter-side).

    Seeds: [b"tick_array", whirlpool_bytes, str(start_tick_index).encode()].
    The string-seed format is verified live by orca_writer's swap path.
    """
    try:
        seeds = [
            b"tick_array",
            _b58decode(whirlpool),
            str(start_tick_index).encode("utf-8"),
        ]
        pda, _ = _find_program_address(seeds, _b58decode(WHIRLPOOL_PROGRAM_ID))
        return _b58encode(pda) if pda else None
    except (KeyError, ValueError):
        return None


def _read_tick_fee_growth_outside_solana(
    whirlpool: str,
    tick: int,
    tick_spacing: int,
) -> Optional[Tuple[int, int, bool]]:
    """Read (fee_growth_outside_a, fee_growth_outside_b, initialized) for a tick.

    Reads the Whirlpool TickArray PDA containing `tick` and decodes the tick
    entry at offset 12 + i*113 (i = (tick - start) // tick_spacing).

    Returns None when the tick-array account is missing or undecodable —
    callers fall back to the global approximation (flagged as estimate).
    """
    try:
        start_index = _tick_array_start_index_for(tick, tick_spacing)
        ta_addr = _derive_tick_array_address_adapter(whirlpool, start_index)
        if not ta_addr:
            return None
        data = _get_account_data(ta_addr)
        if not data or len(data) < 12:
            return None
        ta_start = struct.unpack_from("<i", data, 8)[0]
        # Entry offset within this tick array
        i = (tick - ta_start) // tick_spacing
        if i < 0 or i >= TICK_ARRAY_SIZE:
            return None
        offset = _TICK_ENTRY_FIRST_OFFSET + i * _TICK_ENTRY_SIZE
        if offset + _TICK_ENTRY_SIZE > len(data):
            return None
        initialized = data[offset] != 0
        base = offset + 1  # skip the initialized bool
        # Anchor TickInfo layout (verified empirically against mainnet, v5.3.5):
        #   [0] initialized bool(1), [1:17] liquidity_net i128, [17:33] liquidity_gross u128,
        #   [33:49] fee_growth_outside_a u128, [49:65] fee_growth_outside_b u128, [65:113] rewards
        lo, hi = struct.unpack_from("<QQ", data, base + 32)
        fg_out_a = lo + (hi << 64)
        lo2, hi2 = struct.unpack_from("<QQ", data, base + 48)
        fg_out_b = lo2 + (hi2 << 64)
        return fg_out_a, fg_out_b, initialized
    except Exception as e:
        print(f"[orca-fees] tick-array read failed for tick {tick}: {e}")
        return None


def _compute_fees_owed(
    liquidity: int,
    fee_owed_a: int,
    fee_owed_b: int,
    fee_growth_checkpoint_a: int,
    fee_growth_checkpoint_b: int,
    fee_growth_global_a: int,
    fee_growth_global_b: int,
    tick_current: Optional[int] = None,
    tick_lower: Optional[int] = None,
    tick_upper: Optional[int] = None,
    tick_spacing: Optional[int] = None,
    whirlpool: Optional[str] = None,
) -> Tuple[int, int, bool]:
    """Compute total uncollected fees for a position (raw integer amounts).

    v5.3.5: computes feeGrowthInside via tick arrays (exact, mirrors the
    Whirlpool program's get_fee_growth_inside) instead of charging the
    position for all global pool trading while it is out of range. The old
    global-minus-checkpoint approximation is exact only for positions that
    stay in range — it overestimated out-of-range fees by ~70x in the field.

    Growth accumulators are Q64.64: the delta is shifted right by 64 bits
    after multiplying by liquidity — matching the on-chain update math.

    When tick-array data is unavailable (missing account / RPC failure),
    falls back to the global approximation and returns ``estimated=True``
    so callers can flag the value as an estimate. Never raises.

    Args:
        liquidity: Position liquidity (plain u128, not Q64.64).
        fee_owed_a / fee_owed_b: Fees already tracked by the position.
        fee_growth_checkpoint_*: Position's stored inside-growth checkpoints.
        fee_growth_global_*: Pool's global growth accumulators (Q64.64).
        tick_current / tick_lower / tick_upper: Tick indices for inside math.
        tick_spacing: Pool tick spacing (tick-array geometry).
        whirlpool: Pool address (tick-array PDA derivation).

    Returns:
        (fee_a_raw, fee_b_raw, estimated) where estimated is True when the
        tick arrays were unavailable and the global approximation was used.
    """
    MOD = 1 << 128  # accumulators are u128

    def _accrued(checkpoint: int, fg_global: int, inside: Optional[int]) -> Tuple[int, bool]:
        if inside is None:
            # Fallback: global approximation (exact only while in range).
            delta = (fg_global - checkpoint) % MOD
            return (liquidity * delta) >> 64, True
        delta = (inside - checkpoint) % MOD
        return (liquidity * delta) >> 64, False

    inside_a: Optional[int] = None
    inside_b: Optional[int] = None
    context_ok = None not in (tick_current, tick_lower, tick_upper, tick_spacing, whirlpool)
    if context_ok and liquidity > 0:
        lower_out = _read_tick_fee_growth_outside_solana(
            whirlpool, tick_lower, tick_spacing  # type: ignore[arg-type]
        )
        upper_out = _read_tick_fee_growth_outside_solana(
            whirlpool, tick_upper, tick_spacing  # type: ignore[arg-type]
        )
        if lower_out is not None and upper_out is not None:
            out_low_a, out_low_b, low_init = lower_out
            out_up_a, out_up_b, up_init = upper_out
            if low_init and up_init:
                inside_a = _compute_fee_growth_inside_u128(
                    tick_current, tick_lower, tick_upper,  # type: ignore[arg-type]
                    fee_growth_global_a, out_low_a, out_up_a,
                )
                inside_b = _compute_fee_growth_inside_u128(
                    tick_current, tick_lower, tick_upper,  # type: ignore[arg-type]
                    fee_growth_global_b, out_low_b, out_up_b,
                )
            else:
                # Boundary tick not initialized — outside values are
                # meaningless; use the approximation and flag it.
                print(f"[orca-fees] boundary tick uninitialized for {whirlpool[:8]}... — estimate fallback")

    accrued_a, est_a = _accrued(fee_growth_checkpoint_a, fee_growth_global_a, inside_a)
    accrued_b, est_b = _accrued(fee_growth_checkpoint_b, fee_growth_global_b, inside_b)
    return fee_owed_a + accrued_a, fee_owed_b + accrued_b, (est_a or est_b)


def _compute_fee_growth_inside_u128(
    tick_current: int,
    tick_lower: int,
    tick_upper: int,
    fg_global: int,
    fg_out_lower: int,
    fg_out_upper: int,
) -> int:
    """Compute feeGrowthInside for one token (Uniswap V3 / Whirlpool rules).

    All arithmetic mod 2^128 (Orca growth accumulators are u128).
    Per whirlpool-math get_fee_growth_inside:
      - below = outside(lower) if current >= lower, else global - outside(lower)
      - above = outside(upper) if current < upper, else global - outside(upper)
      - inside = global - below - above
    """
    MOD = 1 << 128
    if tick_current >= tick_lower:
        below = fg_out_lower % MOD
    else:
        below = (fg_global - fg_out_lower) % MOD
    if tick_current < tick_upper:
        above = fg_out_upper % MOD
    else:
        above = (fg_global - fg_out_upper) % MOD
    return (fg_global - below - above) % MOD


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

        # v5.3.5: feeGrowthInside-aware fee math (tick arrays), with graceful
        # fallback to the global approximation. Matches _fetch_position_at_address.
        fee_a_raw, fee_b_raw, _estimated = _compute_fees_owed(
            liquidity=position["liquidity"],
            fee_owed_a=position["fee_owed_a"],
            fee_owed_b=position["fee_owed_b"],
            fee_growth_checkpoint_a=position["fee_growth_checkpoint_a"],
            fee_growth_checkpoint_b=position["fee_growth_checkpoint_b"],
            fee_growth_global_a=pool["fee_growth_global_a"],
            fee_growth_global_b=pool["fee_growth_global_b"],
            tick_current=pool["tick_current"],
            tick_lower=position["tick_lower"],
            tick_upper=position["tick_upper"],
            tick_spacing=pool["tick_spacing"],
            whirlpool=position["whirlpool"],
        )
        symbol_a = _get_sol_token_symbol(pool["token_mint_a"])
        symbol_b = _get_sol_token_symbol(pool["token_mint_b"])
        # v5.3.5: for fallback (truncated-mint) symbols, key the dict by the
        # actual mint so downstream USD pricing can route via Jupiter.
        key_a = pool["token_mint_a"] if _is_fallback_symbol(symbol_a) else symbol_a
        key_b = pool["token_mint_b"] if _is_fallback_symbol(symbol_b) else symbol_b
        return {
            key_a: fee_a_raw / (10 ** dec_a),
            key_b: fee_b_raw / (10 ** dec_b),
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
        unpriced_legs: List[str] = []
        fee_estimate_note: str = ""

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

            # v5.3.5: fees via feeGrowthInside (tick arrays) with graceful
            # fallback to the global approximation, flagged as an estimate.
            fee_a_raw, fee_b_raw, fees_estimated = _compute_fees_owed(
                liquidity=position["liquidity"],
                fee_owed_a=position["fee_owed_a"],
                fee_owed_b=position["fee_owed_b"],
                fee_growth_checkpoint_a=position["fee_growth_checkpoint_a"],
                fee_growth_checkpoint_b=position["fee_growth_checkpoint_b"],
                fee_growth_global_a=pool["fee_growth_global_a"],
                fee_growth_global_b=pool["fee_growth_global_b"],
                tick_current=current_tick,
                tick_lower=tick_lower,
                tick_upper=tick_upper,
                tick_spacing=pool["tick_spacing"],
                whirlpool=pool_addr,
            )
            if fees_estimated:
                fee_estimate_note = "⚠ fees estimated (tick arrays unavailable)"
            fee_a_h = fee_a_raw / (10 ** dec_a)
            fee_b_h = fee_b_raw / (10 ** dec_b)
            if fee_a_h > 0:
                fees_earned[symbol_a] = fee_a_h
            if fee_b_h > 0:
                fees_earned[symbol_b] = fee_b_h

            # USD values (v5.3.5: mint-aware + unpriced-leg tracking)
            fees_usd = 0.0
            if price_engine:
                fee_val_a = _usd_value(fee_a_h, symbol_a, price_engine, mint=pool["token_mint_a"])
                fee_val_b = _usd_value(fee_b_h, symbol_b, price_engine, mint=pool["token_mint_b"])
                if fee_val_a is None and fee_a_h > 0:
                    unpriced_legs.append(symbol_a)
                if fee_val_b is None and fee_b_h > 0:
                    unpriced_legs.append(symbol_b)
                fees_usd += (fee_val_a or 0.0) + (fee_val_b or 0.0)
            fees_earned_usd = fees_usd if fees_usd > 0 else (0.0 if fees_earned else None)

            val_a = _usd_value(amt_a_h, symbol_a, price_engine, mint=pool["token_mint_a"])
            val_b = _usd_value(amt_b_h, symbol_b, price_engine, mint=pool["token_mint_b"])
            if val_a is None and amt_a_h > 0:
                unpriced_legs.append(symbol_a)
            if val_b is None and amt_b_h > 0:
                unpriced_legs.append(symbol_b)
            total_val = (val_a or 0.0) + (val_b or 0.0)
            position_value_usd = total_val if total_val > 0 else None

            # v5.3.5: never silently zero an unpriceable leg — surface a marker.
            # fees_note renders on the card (lp_tab) even when USD is available.
            if unpriced_legs:
                unpriced_note = (
                    f"⚠ unpriced: {', '.join(sorted(set(unpriced_legs)))} "
                    f"(amounts shown, no USD price found)"
                )
                fee_estimate_note = (
                    f"{fee_estimate_note} · {unpriced_note}" if fee_estimate_note else unpriced_note
                )

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
            fees_note=fee_estimate_note or None,
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
