"""Aerodrome Venue Adapter - ColdStack LP Engine v5.2.2

Read-only adapter for Aerodrome SlipStream V3 LP positions on BASE.

Queries Aerodrome SlipStream NonfungiblePositionManager contracts on BASE
directly via JSON-RPC. SlipStream is a Uniswap V3 fork, so all selectors,
math, and decoding logic are identical. No third-party API — all reads are
direct on-chain calls using stdlib urllib.request only.

Read-only. Stateless. Offline by default.

Version: v5.2.2 (August 2026) - Aerodrome SlipStream on BASE
"""
import json
import math
import time
import urllib.request
import urllib.error
from typing import Any, Dict, List, Optional, Tuple

from lp_engine import LPPosition, OfflineError, VenueAdapter, register_adapter
from price_engine import PriceEngine


# ---------------------------------------------------------------------------
# RPC and chain configuration
# ---------------------------------------------------------------------------

BASE_RPC_URL = "https://base.publicnode.com"
BASE_RPC_FALLBACK = "https://1rpc.io/base"
BASE_CHAIN_ID = 8453

# ---------------------------------------------------------------------------
# Aerodrome SlipStream contract addresses on BASE
# ---------------------------------------------------------------------------

# Primary canonical set (Aerodrome security page)
AERO_SLIPSTREAM_POSITION_MANAGER = "0x827922686190790b37229fd06084350E74485b72"
AERO_SLIPSTREAM_POOL_FACTORY = "0x5e7BB104d84c7CB9B682AaC2F3d509f5F406809A"

# Newer/fallback set (builddaily.io)
AERO_SLIPSTREAM_POSITION_MANAGER_2 = "0xe1f8cd9AC4e4A65F54f38a5CdAfCA44f6dD68b53"
AERO_SLIPSTREAM_POOL_FACTORY_2 = "0xf8f2eB4940CFE7d13603DDDD87f123820Fc061Ef"

# Aerodrome Voter contract on BASE (maps pool -> CL gauge)
AERO_VOTER = "0x16613524e02ad97eDfeF371bC883F2F5d6C480A5"

# List of all V3 Position Managers to scan (in order)
V3_POSITION_MANAGERS = [
    AERO_SLIPSTREAM_POSITION_MANAGER,
    AERO_SLIPSTREAM_POSITION_MANAGER_2,
]

# Map each Position Manager to its Factory (for pool resolution)
POSITION_MANAGER_TO_FACTORY = {
    AERO_SLIPSTREAM_POSITION_MANAGER.lower(): AERO_SLIPSTREAM_POOL_FACTORY,
    AERO_SLIPSTREAM_POSITION_MANAGER_2.lower(): AERO_SLIPSTREAM_POOL_FACTORY_2,
}



# ---------------------------------------------------------------------------
# Function selectors (standard Uniswap V3 — identical for Aerodrome SlipStream)
# ---------------------------------------------------------------------------

SELECTOR_SLOT0 = "0x3850c7bd"
SELECTOR_POSITIONS = "0x99fbab88"
SELECTOR_BALANCE_OF = "0x70a08231"
SELECTOR_OWNER_OF = "0x6352211e"
SELECTOR_TOTAL_SUPPLY = "0x18160ddd"
SELECTOR_FEE_GROWTH_GLOBAL0 = "0xf3058399"
SELECTOR_FEE_GROWTH_GLOBAL1 = "0x46141319"
SELECTOR_TOKEN_OF_OWNER_BY_INDEX = "0x2f745c59"
SELECTOR_FEE = "0xddca3f43"
SELECTOR_TOKEN0 = "0x0dfe1681"
SELECTOR_TOKEN1 = "0xd21220a7"
SELECTOR_DECIMALS = "0x313ce567"
SELECTOR_SYMBOL = "0x95d89b41"
SELECTOR_GET_POOL = "0x1698ee82"
# Pool tick state: ticks(int24) returns 10 words (320 bytes + 0x prefix)
SELECTOR_TICKS = "0xf30dba93"
# collect() is used as a read-only eth_call to get exact uncollected fees.
SELECTOR_COLLECT = "0xfc6f7865"  # collect((uint256,address,uint128,uint128))
SELECTOR_DECREASE_LIQUIDITY = "0x0c49ccbe"  # decreaseLiquidity((uint256,uint128,uint256,uint256,uint256))
SELECTOR_INCREASE_LIQUIDITY = "0x7cf9b221"  # increaseLiquidity((uint256,uint128,uint256,uint256,uint256,uint256,uint256))
# Voter selector: pool -> CL gauge address
SELECTOR_GAUGE_FOR_POOL = "0xb9a09fd5"  # gauges(address) -> address
# CL Gauge selectors
SELECTOR_STAKED_TOKEN_IDS = "0x9e713103"  # stakedTokenIds(address) -> uint256[]
SELECTOR_POOL_OF_TOKEN = "0x83966021"  # poolOf(uint256 tokenId) -> address
SELECTOR_EARNED_REWARDS = "0x3e491d47"  # earned(address,uint256 tokenId) -> uint256 (AERO, 18 decimals)
# Gauge getReward selector — claims AERO emissions for a staked position
SELECTOR_GET_REWARD = "0x1c4b774b"  # getReward(uint256 tokenId)

# Common BASE token addresses
WETH_BASE = "0x4200000000000000000000000000000000000006"
USDC_BASE = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
AERO_TOKEN = "0x940181a94A35A4569E4529A3CDfB74e38FD98631"
CBBTC_BASE = "0xcbB45146687557Fd9B6F8cB1E2D51a65F3B1D1c1"

# ---------------------------------------------------------------------------
# Token registries
# ---------------------------------------------------------------------------

TOKEN_DECIMALS = {
    WETH_BASE.lower(): 18,
    USDC_BASE.lower(): 6,
    AERO_TOKEN.lower(): 18,
    CBBTC_BASE.lower(): 8,
}

TOKEN_SYMBOLS = {
    WETH_BASE.lower(): "WETH",
    USDC_BASE.lower(): "USDC",
    AERO_TOKEN.lower(): "AERO",
    CBBTC_BASE.lower(): "cbBTC",
}

CANONICAL_SYMBOLS = {
    "WETH": "ETH",
    "cbETH": "ETH",
    "stETH": "ETH",
    "USDC": "USD",
    "USDT": "USD",
    "AERO": "AERO",
}


# ---------------------------------------------------------------------------
# RPC layer
# ---------------------------------------------------------------------------

def _base_rpc_call(method: str, params: list, request_id: int = 1) -> Optional[Any]:
    """Make a single JSON-RPC call to BASE and return the 'result' field.

    For methods that return a JSON object (e.g., eth_getLogs), returns the
    raw object rather than only the 'result' key, so callers can distinguish
    a successful empty list from a transport failure.
    """
    payload = json.dumps(
        {"jsonrpc": "2.0", "method": method, "params": params, "id": request_id}
    ).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "ColdStack/5.2.2",
    }

    for url in (BASE_RPC_URL, BASE_RPC_FALLBACK):
        try:
            req = urllib.request.Request(url, data=payload, headers=headers)
            with urllib.request.urlopen(req, timeout=15) as response:
                data = json.loads(response.read().decode("utf-8"))
                if method in ("eth_getLogs", "eth_blockNumber"):
                    return data
                return data.get("result")
        except Exception:
            continue
    return None


def _base_rpc_batch(
    method_calls: List[Tuple[str, list]], request_id_base: int = 1
) -> List[Optional[Any]]:
    """Execute batched JSON-RPC calls. BASE public RPCs tolerate small batches."""
    if not method_calls:
        return []
    if len(method_calls) == 1:
        method, params = method_calls[0]
        return [_base_rpc_call(method, params, request_id_base)]

    MAX_BATCH = 5
    results: List[Optional[Any]] = []
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "ColdStack/5.2.2",
    }

    for chunk_start in range(0, len(method_calls), MAX_BATCH):
        chunk = method_calls[chunk_start:chunk_start + MAX_BATCH]
        payload_obj = []
        for idx, (method, params) in enumerate(chunk):
            payload_obj.append(
                {"jsonrpc": "2.0", "id": request_id_base + chunk_start + idx, "method": method, "params": params}
            )

        for url in (BASE_RPC_URL, BASE_RPC_FALLBACK):
            try:
                payload = json.dumps(payload_obj).encode("utf-8")
                req = urllib.request.Request(url, data=payload, headers=headers)
                with urllib.request.urlopen(req, timeout=30) as response:
                    data = json.loads(response.read().decode("utf-8"))
                    if isinstance(data, dict) and data.get("error"):
                        raise RuntimeError(data.get("error", {}).get("message", "RPC error"))
                    if not isinstance(data, list):
                        raise RuntimeError("non-list RPC response")
                    by_id = {item.get("id"): item for item in data}
                    for idx in range(len(chunk)):
                        results.append(by_id.get(request_id_base + chunk_start + idx, {}).get("result"))
                    break
            except Exception:
                if url == BASE_RPC_FALLBACK:
                    for idx, (method, params) in enumerate(chunk):
                        results.append(_base_rpc_call(method, params, request_id_base + chunk_start + idx))
    return results


# ---------------------------------------------------------------------------
# ABI helpers
# ---------------------------------------------------------------------------

def _pad_int_to_64(value: int) -> str:
    """Pad an integer to 32-byte (64 hex char) ABI encoding (two's complement)."""
    if value < 0:
        value = (1 << 256) + value
    return format(int(value), "064x")


def _pad_address(address: str) -> str:
    """Pad a 20-byte EVM address to a 32-byte ABI word."""
    clean = address.lower()
    if clean.startswith("0x"):
        clean = clean[2:]
    return ("0" * 24) + clean


def _decode_uint256(hex_str: str) -> int:
    """Decode a 32-byte hex word to a Python int."""
    return int(hex_str, 16)


def _decode_int24(hex_str: str) -> int:
    """Decode a signed int24 packed in a 32-byte ABI word."""
    raw = int(hex_str, 16)
    if raw >= 2 ** 255:
        raw -= 2 ** 256
    return raw


def _decode_address(hex_str: str) -> str:
    """Extract a 20-byte address from a 32-byte ABI word."""
    return "0x" + hex_str[-40:]


def _get_gauge_for_pool(pool_address: str) -> Optional[str]:
    """Get the CL gauge address for a pool via the Aerodrome Voter contract."""
    data = SELECTOR_GAUGE_FOR_POOL + _pad_address(pool_address)
    result = _base_rpc_call("eth_call", [{"to": AERO_VOTER, "data": data}, "latest"])
    if result and isinstance(result, str) and len(result) >= 66:
        addr = _decode_address(result[2:66])
        if int(addr, 16) != 0:
            return addr.lower()
    return None


def _get_staked_token_ids(gauge_address: str, wallet_address: str) -> List[int]:
    """Get all NFT token IDs staked by a wallet in a CL gauge.

    Uses selector 0x4b937763 which returns an ABI-encoded uint256[] array.

    Args:
        gauge_address: The CL gauge contract address.
        wallet_address: The wallet that staked the NFTs.

    Returns:
        List of token IDs (integers). Empty list if none or on error.
    """
    data = SELECTOR_STAKED_TOKEN_IDS + _pad_address(wallet_address)
    result = _base_rpc_call("eth_call", [{"to": gauge_address, "data": data}, "latest"])
    if not result or not isinstance(result, str) or len(result) < 2 + 128:
        return []

    body = result[2:]
    # ABI encoding for dynamic array:
    # word 0: offset (0x20 = 32 bytes)
    # word 1: array length
    # word 2+: array elements
    try:
        offset = int(body[0:64], 16)
        if offset != 0x20:
            return []
        arr_len = int(body[64:128], 16)
        if arr_len == 0:
            return []
        token_ids = []
        for i in range(arr_len):
            start = 128 + i * 64
            if start + 64 <= len(body):
                tid = int(body[start:start + 64], 16)
                if tid > 0:
                    token_ids.append(tid)
        return token_ids
    except (ValueError, IndexError):
        return []


def _get_earned_aero_rewards(
    gauge_address: str, token_id: int, wallet_address: str = ""
) -> float:
    """Get earned AERO emissions for a staked position.

    The deployed Aerodrome CL gauge uses earned(address account, uint256 tokenId)
    (selector 0x7b0a47ee). The wallet_address is the staker's address, not the
    gauge. If wallet_address is omitted, fall back to the legacy single-argument
    call.
    """
    if wallet_address:
        wallet_padded = "000000000000000000000000" + wallet_address[2:].lower()
        data = SELECTOR_EARNED_REWARDS + wallet_padded + _pad_int_to_64(token_id)
    else:
        # Fallback: single-arg call (lenient, but technically incorrect)
        data = SELECTOR_EARNED_REWARDS + _pad_int_to_64(token_id)
    result = _base_rpc_call("eth_call", [{"to": gauge_address, "data": data}, "latest"])
    if not result or not isinstance(result, str) or len(result) < 66:
        return 0.0
    raw = int(result[2:66], 16)
    return raw / 1e18  # AERO has 18 decimals


def _get_gauge_address_for_position(
    token_id: int, position_manager: str, wallet_address: str
) -> Optional[str]:
    """Find the CL gauge address that holds a staked NFT position.

    Checks if the NFT owner is a gauge by calling poolOf(tokenId) on it.
    Returns the gauge address if staked, None if unstaked.
    """
    owner_result = _base_rpc_call(
        "eth_call",
        [{"to": position_manager, "data": SELECTOR_OWNER_OF + _pad_int_to_64(token_id)}, "latest"],
    )
    if not owner_result or not isinstance(owner_result, str) or len(owner_result) < 66:
        return None
    nft_owner = _decode_address(owner_result[2:66])
    if not nft_owner or int(nft_owner, 16) == 0:
        return None
    if nft_owner.lower() == wallet_address.lower():
        return None  # Not staked — wallet owns the NFT
    # The NFT owner is likely a gauge. Verify by calling poolOf(tokenId).
    data = SELECTOR_POOL_OF_TOKEN + _pad_int_to_64(token_id)
    pool_result = _base_rpc_call(
        "eth_call",
        [{"to": nft_owner, "data": data}, "latest"],
    )
    if pool_result and isinstance(pool_result, str) and len(pool_result) >= 66:
        pool_addr = _decode_address(pool_result[2:66])
        if int(pool_addr, 16) != 0:
            return nft_owner.lower()  # Confirmed gauge
    # Even if poolOf fails, return the owner if it's not the wallet
    # (it might be a gauge with a different interface)
    return nft_owner.lower()


# ---------------------------------------------------------------------------
# V3 math helpers
# ---------------------------------------------------------------------------

def _sqrt_price_x96_to_raw_price(sqrt_price_x96: int) -> float:
    """Convert Uniswap V3 sqrtPriceX96 to raw token1/token0 price."""
    return (sqrt_price_x96 / (2 ** 96)) ** 2


def _raw_price_to_human(raw_price: float, decimals0: int, decimals1: int) -> float:
    """Scale raw price by decimal difference for human-readable units."""
    return raw_price * (10 ** (decimals0 - decimals1))


def _tick_to_price(tick: int, decimals0: int, decimals1: int) -> float:
    """Convert a Uniswap V3 tick to a human-readable price."""
    raw = 1.0001 ** tick
    return _raw_price_to_human(raw, decimals0, decimals1)


def _tick_spacing(fee: int) -> int:
    """Return the tick spacing for a Uniswap V3 fee tier."""
    mapping = {100: 1, 500: 10, 3000: 60, 10000: 200}
    return mapping.get(fee, 60)


# ---------------------------------------------------------------------------
# Token helpers
# ---------------------------------------------------------------------------

def _get_token_decimals(token_address: str) -> int:
    """Return known decimals; fall back to on-chain decimals() call."""
    lower = token_address.lower()
    if lower in TOKEN_DECIMALS:
        return TOKEN_DECIMALS[lower]
    try:
        result = _base_rpc_call(
            "eth_call",
            [{"to": lower, "data": SELECTOR_DECIMALS}, "latest"],
        )
        if result and isinstance(result, str) and len(result) >= 66:
            return int(result[2:66], 16)
    except Exception:
        pass
    return 18


def _get_token_symbol(token_address: str) -> str:
    """Return known symbol; fall back to on-chain symbol() call."""
    lower = token_address.lower()
    if lower in TOKEN_SYMBOLS:
        return TOKEN_SYMBOLS[lower]
    try:
        result = _base_rpc_call(
            "eth_call",
            [{"to": lower, "data": SELECTOR_SYMBOL}, "latest"],
        )
        if result and isinstance(result, str) and len(result) >= 66:
            length = int(result[2:66], 16)
            if length:
                offset = 66 + 64
                hex_body = result[2:][offset:offset + length * 2]
                return bytes.fromhex(hex_body).decode("utf-8", errors="ignore").replace("\x00", "").strip()
    except Exception:
        pass
    return lower[-6:].upper()


def _canonical_symbol(symbol: str) -> str:
    """Map wrapper / bridged tokens to canonical price symbols."""
    return CANONICAL_SYMBOLS.get(symbol.upper(), symbol.upper())


def _usd_value(amount: Optional[float], symbol: str, price_engine: Optional[PriceEngine]) -> Optional[float]:
    """Convert a token amount to USD using the shared price engine."""
    if amount is None or amount <= 0 or not price_engine:
        return None
    canonical = _canonical_symbol(symbol)
    return price_engine.convert_balance_to_fiat(amount, canonical, currency="usd")


# ---------------------------------------------------------------------------
# Pool helpers
# ---------------------------------------------------------------------------

def _try_get_pool(factory: str, token0: str, token1: str, fee: int) -> Optional[str]:
    """Try to get a pool address from a factory for a given fee tier."""
    data = SELECTOR_GET_POOL + _pad_address(token0) + _pad_address(token1) + _pad_int_to_64(fee)
    result = _base_rpc_call("eth_call", [{"to": factory, "data": data}, "latest"])
    if result and isinstance(result, str) and len(result) >= 66:
        addr = _decode_address(result[2:66])
        if int(addr, 16) != 0:
            return addr.lower()
    return None


def _pool_for_token_ids(
    token0: str, token1: str, fee: int, position_manager: str
) -> Optional[str]:
    """Resolve pool address via the correct factory for the given position manager.

    Tries the given fee first, then falls back to all common fee tiers.
    """
    factory = POSITION_MANAGER_TO_FACTORY.get(position_manager.lower())
    if not factory:
        return None
    if token1.lower() < token0.lower():
        token0, token1 = token1, token0

    # Try the given fee first
    pool = _try_get_pool(factory, token0, token1, fee)
    if pool:
        return pool

    # Fallback: try all common fee tiers
    COMMON_FEES = [100, 500, 2500, 3000, 10000]
    for fallback_fee in COMMON_FEES:
        if fallback_fee == fee:
            continue
        pool = _try_get_pool(factory, token0, token1, fallback_fee)
        if pool:
            return pool

        return None


def _resolve_pool_aggressive(
    token0: str,
    token1: str,
    fee: int,
    position_manager: str,
    token_id: int,
    nft_owner: str,
) -> Optional[str]:
    """Resolve pool address with multiple fallback strategies.

    Tries in order:
    1. _pool_for_token_ids (standard: factory getPool with known fee + fallback fees)
    2. gauge.poolOf(tokenId) — if nft_owner looks like a gauge
    3. Both factories with ALL common fee tiers (including the original fee)
    """
    # Strategy 1: Standard resolution
    pool = _pool_for_token_ids(token0, token1, fee, position_manager)
    if pool:
        return pool

    # Strategy 2: gauge.poolOf(tokenId)
    if nft_owner and int(nft_owner, 16) != 0:
        data = SELECTOR_POOL_OF_TOKEN + _pad_int_to_64(token_id)
        result = _base_rpc_call("eth_call", [{"to": nft_owner, "data": data}, "latest"])
        if result and isinstance(result, str) and len(result) >= 66:
            resolved = _decode_address(result[2:66])
            if int(resolved, 16) != 0:
                print(f"[pool-resolve] Found pool via gauge.poolOf: {resolved}")
                return resolved.lower()

    # Strategy 3: Try both factories with ALL fee tiers
    factories = list(POSITION_MANAGER_TO_FACTORY.values())
    all_fees = [100, 500, 2500, 3000, 10000]
    # Try the original fee first, then all others
    tried = {fee}
    for try_fee in [fee] + [f for f in all_fees if f != fee]:
        for factory in factories:
            pool = _try_get_pool(factory, token0, token1, try_fee)
            if pool:
                print(f"[pool-resolve] Found pool via factory getPool: fee={try_fee} factory={factory} pool={pool}")
                return pool

    print(f"[pool-resolve] Could not resolve pool for token0={token0} token1={token1} fee={fee}")
    return None


def _fetch_pool_state(
    pool_address: str
) -> Tuple[Optional[float], Optional[int], int, str, str, Optional[int]]:
    """Return (human_price, current_tick, fee, token0, token1, sqrtPriceX96_raw) for a pool."""
    pool_address = pool_address.lower()
    slot0_result = _base_rpc_call(
        "eth_call",
        [{"to": pool_address, "data": SELECTOR_SLOT0}, "latest"],
    )
    fee_result = _base_rpc_call(
        "eth_call",
        [{"to": pool_address, "data": SELECTOR_FEE}, "latest"],
    )
    token0_result = _base_rpc_call(
        "eth_call",
        [{"to": pool_address, "data": SELECTOR_TOKEN0}, "latest"],
    )
    token1_result = _base_rpc_call(
        "eth_call",
        [{"to": pool_address, "data": SELECTOR_TOKEN1}, "latest"],
    )

    fee = 0
    if fee_result and isinstance(fee_result, str) and len(fee_result) >= 66:
        fee = int(fee_result[2:66], 16)

    token0 = ""
    if token0_result and isinstance(token0_result, str) and len(token0_result) >= 66:
        token0 = _decode_address(token0_result[2:66]).lower()

    token1 = ""
    if token1_result and isinstance(token1_result, str) and len(token1_result) >= 66:
        token1 = _decode_address(token1_result[2:66]).lower()

    current_price: Optional[float] = None
    current_tick: Optional[int] = None
    sqrt_price_x96_raw: Optional[int] = None
    if (
        slot0_result
        and isinstance(slot0_result, str)
        and len(slot0_result) >= 2 + 32 * 6
    ):
        try:
            sqrt_price_x96 = int(slot0_result[2:66], 16)
            sqrt_price_x96_raw = sqrt_price_x96
            current_tick = int(slot0_result[66:130], 16)
            if current_tick >= 2 ** 255:
                current_tick -= 2 ** 256
            if token0 and token1:
                decimals0 = _get_token_decimals(token0)
                decimals1 = _get_token_decimals(token1)
                raw = _sqrt_price_x96_to_raw_price(sqrt_price_x96)
                current_price = _raw_price_to_human(raw, decimals0, decimals1)
        except (ValueError, OverflowError):
            pass

    return current_price, current_tick, fee, token0, token1, sqrt_price_x96_raw


# ---------------------------------------------------------------------------
# Position decoding
# ---------------------------------------------------------------------------

def _decode_positions_response(
    lp_data: str,
    token_id: int,
    price_engine: Optional[PriceEngine],
    wallet_address: str = "",
    position_manager: str = "",
) -> Optional[LPPosition]:
    """Decode an Aerodrome SlipStream positions(uint256) return into an LPPosition."""
    if not lp_data or not isinstance(lp_data, str) or len(lp_data) < 2 + 32 * 13:
        return None

    try:
        body = lp_data[2:]
        nonce = int(body[0:64], 16)
        operator = _decode_address(body[64:128])
        token0 = _decode_address(body[128:192]).lower()
        token1 = _decode_address(body[192:256]).lower()
        fee = int(body[256:320], 16)
        tick_lower = _decode_int24(body[320:384])
        tick_upper = _decode_int24(body[384:448])
        liquidity = int(body[448:512], 16)
        fee_growth_inside0_last_x128 = int(body[512:576], 16)
        fee_growth_inside1_last_x128 = int(body[576:640], 16)
        tokens_owed0 = int(body[640:704], 16)
        tokens_owed1 = int(body[704:768], 16)
    except (ValueError, IndexError):
        return None

    decimals0 = _get_token_decimals(token0)
    decimals1 = _get_token_decimals(token1)
    symbol0 = _get_token_symbol(token0)
    symbol1 = _get_token_symbol(token1)

    if price_engine:
        ds_symbol0 = price_engine.get_token_symbol_by_address(token0, chain="base")
        if ds_symbol0:
            symbol0 = ds_symbol0
        ds_symbol1 = price_engine.get_token_symbol_by_address(token1, chain="base")
        if ds_symbol1:
            symbol1 = ds_symbol1

    # Look up NFT owner early — needed for pool resolution fallback
    nft_owner = wallet_address
    if position_manager:
        owner_result = _base_rpc_call(
            "eth_call",
            [{"to": position_manager, "data": SELECTOR_OWNER_OF + _pad_int_to_64(token_id)}, "latest"],
        )
        if owner_result and isinstance(owner_result, str) and len(owner_result) >= 66:
            nft_owner = _decode_address(owner_result[2:66])

    # Resolve pool address with aggressive fallbacks (factory + gauge.poolOf + all fees)
    pool_address = _resolve_pool_aggressive(
        token0, token1, fee, position_manager, token_id, nft_owner
    )

    current_price: Optional[float] = None
    current_tick: Optional[int] = None
    sqrtPriceX96: Optional[int] = None
    if pool_address:
        current_price, current_tick, _, _, _, sqrtPriceX96 = _fetch_pool_state(pool_address)
        # Retry pool state once if it failed (BASE public RPCs can be flaky)
        if current_tick is None:
            print(f"[pool-state] First fetch failed for {pool_address}, retrying...")
            time.sleep(0.5)
            current_price, current_tick, _, _, _, sqrtPriceX96 = _fetch_pool_state(pool_address)

    range_low = _tick_to_price(tick_lower, decimals0, decimals1)
    range_high = _tick_to_price(tick_upper, decimals0, decimals1)

    position_in_range_pct: Optional[float] = None
    if current_tick is not None and tick_upper != tick_lower:
        position_in_range_pct = (
            (current_tick - tick_lower) / (tick_upper - tick_lower) * 100.0
        )

    owed0_h = tokens_owed0 / (10 ** decimals0)
    owed1_h = tokens_owed1 / (10 ** decimals1)

    fees_note = None

    # Detect staked position (NFT owned by gauge, not wallet)
    is_staked = (
        nft_owner
        and int(nft_owner, 16) != 0
        and nft_owner.lower() != wallet_address.lower()
    )

    # Debug: trace fee computation path for staked positions
    if is_staked:
        print(f"[fees-debug] Staked position token_id={token_id}")
        print(f"[fees-debug] pool_address={pool_address}")
        print(f"[fees-debug] current_tick={current_tick}")
        print(f"[fees-debug] liquidity={liquidity}")
        print(f"[fees-debug] fee_growth_inside0_last={fee_growth_inside0_last_x128}")
        print(f"[fees-debug] fee_growth_inside1_last={fee_growth_inside1_last_x128}")
        print(f"[fees-debug] tokens_owed0={tokens_owed0} tokens_owed1={tokens_owed1}")
        print(f"[fees-debug] decimals0={decimals0} decimals1={decimals1}")
        print(f"[fees-debug] symbol0={symbol0} symbol1={symbol1}")

    # Read uncollected trading fees.
    # For STAKED positions: use the feeGrowthGlobal delta method. The gauge
    # periodically calls collect() to claim fees for voters, resetting
    # tokensOwed to 0. New fees accrue in feeGrowthGlobal but never move to
    # tokensOwed (only burn() does that, and the gauge doesn't call burn()).
    # So collect() simulation returns 0, but the real-time accrued fees are
    # computed via: liquidity * (feeGrowthInside_current - feeGrowthInsideLast) / 2^128
    #
    # For UNSTAKED positions: use the collect() simulation. tokensOwed is
    # up-to-date because the wallet can call burn()/collect() directly.
    fee_status = ""
    if is_staked and pool_address and current_tick is not None:
        # Staked: use feeGrowthGlobal delta method
        real_fee0, real_fee1, fee_status = _compute_realtime_fees(
            pool_address=pool_address,
            tick_lower=tick_lower,
            tick_upper=tick_upper,
            current_tick=current_tick,
            liquidity=liquidity,
            fee_growth_inside0_last=fee_growth_inside0_last_x128,
            fee_growth_inside1_last=fee_growth_inside1_last_x128,
            tokens_owed0=tokens_owed0,
            tokens_owed1=tokens_owed1,
            decimals0=decimals0,
            decimals1=decimals1,
        )
        if fee_status == "ok":
            owed0_h = real_fee0
            owed1_h = real_fee1
        elif fee_status == "zero":
            pass
        else:
            # Delta method failed — fall back to collect()
            real_fee0, real_fee1, fee_status = _estimate_uncollected_fees(
                token_id, nft_owner, decimals0, decimals1, position_manager
            )
            if fee_status == "ok":
                owed0_h = real_fee0
                owed1_h = real_fee1
            else:
                fees_note = "Fee data unavailable"
    elif nft_owner:
        # Unstaked: use collect() simulation
        real_fee0, real_fee1, fee_status = _estimate_uncollected_fees(
            token_id, nft_owner, decimals0, decimals1, position_manager
        )
        if fee_status == "ok":
            owed0_h = real_fee0
            owed1_h = real_fee1
        elif fee_status == "zero":
            pass
        else:
            fees_note = "RPC unreachable"
    else:
        fees_note = "Connect wallet to read fees"

    fees_earned = {symbol0: owed0_h, symbol1: owed1_h}

    fees_earned_usd = 0.0
    if price_engine:
        fee_price0 = price_engine.get_token_price_by_address(token0, chain="base")
        if fee_price0:
            fees_earned_usd += owed0_h * fee_price0
        else:
            fees_earned_usd += _usd_value(owed0_h, symbol0, price_engine) or 0.0

        fee_price1 = price_engine.get_token_price_by_address(token1, chain="base")
        if fee_price1:
            fees_earned_usd += owed1_h * fee_price1
        else:
            fees_earned_usd += _usd_value(owed1_h, symbol1, price_engine) or 0.0

    # For staked positions, the gauge is the NFT owner.
    # Trading fees (token0/token1) are relinquished to veAERO voters per
    # Aerodrome's spec. The staker only earns AERO emissions.
    if is_staked:
        aero_earned = _get_earned_aero_rewards(nft_owner, token_id, wallet_address)
        if aero_earned > 0:
            aero_usd = 0.0
            if price_engine:
                aero_usd = price_engine.get_token_price_by_address(AERO_TOKEN, chain="base") or 0.0
            if not aero_usd:
                aero_usd = _usd_value(aero_earned, "AERO", price_engine) or 0.0
            fees_earned["AERO"] = aero_earned
            fees_earned_usd = fees_earned_usd + (aero_earned * aero_usd)
            # Set fees_note to explain the fee structure
            if fee_status == "zero":
                fees_note = "Staked · trading fees → veAERO voters"
            # else: fees are shown from the fees_earned dict, no note needed
        elif fee_status == "zero":
            fees_note = "Staked (no pending emissions or fees)"

    position_value_usd = fees_earned_usd
    deposit_amounts: Dict[str, float] = {}

    pnl_usd: Optional[float] = None
    pnl_pct: Optional[float] = None
    apy: Optional[float] = None
    days_active: Optional[int] = None

    if liquidity > 0 and current_tick is not None and tick_upper != tick_lower:
        sqrt_lower = 1.0001 ** (tick_lower / 2.0)
        sqrt_upper = 1.0001 ** (tick_upper / 2.0)
        if sqrtPriceX96 and sqrtPriceX96 > 0:
            sqrt_price = sqrtPriceX96 / (2 ** 96)
        elif current_price and current_price > 0:
            raw_price = current_price * (10 ** (decimals1 - decimals0))
            sqrt_price = math.sqrt(raw_price)
        else:
            sqrt_price = None

        if sqrt_price is None:
            print(f"[aerodrome-value] No price data for token {token_id} — pool state fetch may have failed")

        if sqrt_price and sqrt_price > 0:
            if tick_lower <= current_tick <= tick_upper:
                amount0_raw = liquidity * (sqrt_upper - sqrt_price) / (sqrt_price * sqrt_upper)
                amount1_raw = liquidity * (sqrt_price - sqrt_lower)
            elif current_tick < tick_lower:
                amount0_raw = liquidity * (sqrt_upper - sqrt_lower) / (sqrt_price * sqrt_upper)
                amount1_raw = 0.0
            else:
                amount0_raw = 0.0
                amount1_raw = liquidity * (sqrt_upper - sqrt_lower)

            amount0_human = amount0_raw / (10 ** decimals0)
            amount1_human = amount1_raw / (10 ** decimals1)

            val0 = 0.0
            val1 = 0.0
            if price_engine:
                price0 = price_engine.get_token_price_by_address(token0, chain="base")
                if price0:
                    val0 = amount0_human * price0
                price1 = price_engine.get_token_price_by_address(token1, chain="base")
                if price1:
                    val1 = amount1_human * price1

            if val0 == 0.0:
                val0 = _usd_value(amount0_human, symbol0, price_engine) or 0.0
            if val1 == 0.0:
                val1 = _usd_value(amount1_human, symbol1, price_engine) or 0.0

            position_value_usd = val0 + val1

            print(f"[aerodrome-value] token {token_id}: val0={val0} val1={val1} total={position_value_usd}")

            deposit_amounts[symbol0] = amount0_human
            deposit_amounts[symbol1] = amount1_human
        else:
            if liquidity:
                deposit_amounts[symbol0] = liquidity / (10 ** (decimals0 + 3))
                deposit_amounts[symbol1] = liquidity / (10 ** (decimals1 + 3))
    else:
        if liquidity:
            deposit_amounts[symbol0] = liquidity / (10 ** (decimals0 + 3))
            deposit_amounts[symbol1] = liquidity / (10 ** (decimals1 + 3))

    return LPPosition(
        position_id=f"base:{token_id}",
        pool_id=pool_address,
        venue="Aerodrome",
        chain="BASE",
        pair=f"{symbol0}/{symbol1}",
        token_0=symbol0,
        token_1=symbol1,
        current_price=current_price,
        range_low=range_low,
        range_high=range_high,
        position_in_range_pct=position_in_range_pct,
        deposit_amounts=deposit_amounts,
        fees_earned=fees_earned,
        fees_earned_usd=fees_earned_usd,
        fees_note=fees_note,
        current_value_usd=position_value_usd,
        deposit_value_usd=position_value_usd,
        pnl_usd=pnl_usd,
        pnl_pct=pnl_pct,
        apy=apy,
        days_active=days_active,
        raw_data={
            "token_id": token_id,
            "nonce": nonce,
            "operator": operator,
            "token0": token0,
            "token1": token1,
            "fee": fee,
            "tick_lower": tick_lower,
            "tick_upper": tick_upper,
            "liquidity": liquidity,
            "fee_growth_inside0": fee_growth_inside0_last_x128,
            "fee_growth_inside1": fee_growth_inside1_last_x128,
            "pool_address": pool_address,
            "current_tick": current_tick,
            "decimals0": decimals0,
            "decimals1": decimals1,
        },
    )


# ---------------------------------------------------------------------------
# Fee estimation
# ---------------------------------------------------------------------------

def _estimate_uncollected_fees(
    token_id: int,
    wallet_address: str,
    decimals0: int,
    decimals1: int,
    position_manager: str = "",
) -> Tuple[float, float, str]:
    """Estimate uncollected fees via a read-only collect() eth_call.

    Returns:
        (fee0, fee1, status) where status is one of:
        - "ok" — RPC call succeeded and fees are > 0
        - "zero" — RPC call succeeded, fees are 0
        - "error" — RPC call failed
    """
    if not wallet_address:
        print(f"[fees] no wallet_address provided for token {token_id} — cannot read fees")
        return (0.0, 0.0, "error")

    if not position_manager:
        position_manager = AERO_SLIPSTREAM_POSITION_MANAGER

    print(f"[fees] token {token_id}: using position_manager={position_manager}")

    uint128_max = (1 << 128) - 1
    data = (
        SELECTOR_COLLECT
        + _pad_int_to_64(token_id)
        + _pad_address(wallet_address)
        + _pad_int_to_64(uint128_max)
        + _pad_int_to_64(uint128_max)
    )

    last_error: Optional[Exception] = None
    for attempt in range(3):
        try:
            result = _base_rpc_call(
                "eth_call",
                [{"to": position_manager, "data": data, "from": wallet_address}, "latest"],
            )
            print(f"[fees] token {token_id}: raw collect() result length={len(result) if result else 0}")
            if not result or not isinstance(result, str) or len(result) < 2 + 64:
                print(f"[fees] collect() eth_call returned no data for token {token_id}")
                result = _base_rpc_call(
                    "eth_call",
                    [{"to": position_manager, "data": data}, "latest"],
                )
                if not result or not isinstance(result, str) or len(result) < 2 + 64:
                    return (0.0, 0.0, "error")

            body = result[2:]
            if len(body) >= 128:
                amount0_raw = int(body[0:64], 16)
                amount1_raw = int(body[64:128], 16)
            elif len(body) >= 64:
                amount0_raw = int(body[0:32], 16)
                amount1_raw = int(body[32:64], 16)
            else:
                print(f"[fees] collect() returned insufficient data: {len(body)} chars")
                return (0.0, 0.0, "error")

            amount0_human = amount0_raw / (10 ** decimals0)
            amount1_human = amount1_raw / (10 ** decimals1)

            print(f"[fees] token {token_id}: raw0={amount0_raw} raw1={amount1_raw} "
                  f"human0={amount0_human:.8f} human1={amount1_human:.8f}")

            if amount0_raw > 0 or amount1_raw > 0:
                return (amount0_human, amount1_human, "ok")
            return (0.0, 0.0, "zero")
        except Exception as e:
            last_error = e
            print(f"[fees] collect() eth_call attempt {attempt + 1} failed for token {token_id}: {e}")
            if attempt < 2:
                time.sleep(1.0)

    print(f"[fees] collect() eth_call failed for token {token_id} after 3 attempts: {last_error}")
    return (0.0, 0.0, "error")


def _compute_realtime_fees(
    pool_address: str,
    tick_lower: int,
    tick_upper: int,
    current_tick: int,
    liquidity: int,
    fee_growth_inside0_last: int,
    fee_growth_inside1_last: int,
    tokens_owed0: int,
    tokens_owed1: int,
    decimals0: int,
    decimals1: int,
) -> Tuple[float, float, str]:
    """Compute real-time uncollected trading fees using the feeGrowthGlobal delta method.

    This is the same method used by the Uniswap V3 and Aerodrome UIs.
    It reads feeGrowthGlobal from the pool, reads tick state to compute
    feeGrowthInside, and calculates the delta from the position's
    feeGrowthInsideLastX128.

    totalFees = tokensOwed + liquidity * (feeGrowthInside - feeGrowthInsideLast) / 2^128

    Returns:
        (fee0_human, fee1_human, status) where status is "ok", "zero", or "error"
    """
    if not pool_address or liquidity == 0:
        return (0.0, 0.0, "zero")

    pool_address = pool_address.lower()

    # 1. Read feeGrowthGlobal0X128 and feeGrowthGlobal1X128 from the pool
    fg_global0_result = _base_rpc_call(
        "eth_call",
        [{"to": pool_address, "data": SELECTOR_FEE_GROWTH_GLOBAL0}, "latest"],
    )
    fg_global1_result = _base_rpc_call(
        "eth_call",
        [{"to": pool_address, "data": SELECTOR_FEE_GROWTH_GLOBAL1}, "latest"],
    )

    if not fg_global0_result or not fg_global1_result:
        print(f"[fees-delta] Failed to read feeGrowthGlobal for pool {pool_address}")
        return (0.0, 0.0, "error")

    try:
        fg_global0 = int(fg_global0_result[2:66], 16)
        fg_global1 = int(fg_global1_result[2:66], 16)
    except (ValueError, IndexError):
        print(f"[fees-delta] Failed to decode feeGrowthGlobal")
        return (0.0, 0.0, "error")

    # 2. Read tick state for tickLower and tickUpper
    # ticks(int24) returns 10 words (320 hex chars after 0x):
    # word 0: liquidityGross (uint128)
    # word 1: liquidityNet (int128)
    # word 2: stakedLiquidityNet (int128)  [Aerodrome-specific]
    # word 3: feeGrowthOutside0X128 (uint256)
    # word 4: feeGrowthOutside1X128 (uint256)
    # word 5: rewardGrowthOutsideX128 (uint256)  [Aerodrome-specific]
    # word 6: tickCumulativeOutside (int56)
    # word 7: secondsPerLiquidityOutsideX128 (uint160)
    # word 8: secondsOutside (uint32)
    # word 9: initialized (bool)

    def _read_tick_fee_growth_outside(tick: int) -> Tuple[int, int, bool]:
        """Read feeGrowthOutside0X128 and feeGrowthOutside1X128 for a tick.

        Returns (fgOutside0, fgOutside1, initialized).
        """
        # Encode int24: for negative values, use two's complement in 32 bytes
        if tick < 0:
            tick_padded = format((1 << 256) + tick, "064x")
        else:
            tick_padded = format(tick, "064x")
        data = SELECTOR_TICKS + tick_padded
        result = _base_rpc_call("eth_call", [{"to": pool_address, "data": data}, "latest"])
        if not result or not isinstance(result, str) or len(result) < 2 + 64 * 10:
            print(f"[fees-delta] Failed to read tick {tick} for pool {pool_address}")
            return (0, 0, False)
        body = result[2:]
        try:
            # word 3: feeGrowthOutside0X128
            fg_outside0 = int(body[3 * 64:(3 + 1) * 64], 16)
            # word 4: feeGrowthOutside1X128
            fg_outside1 = int(body[4 * 64:(4 + 1) * 64], 16)
            # word 9: initialized (bool) — last word
            initialized_word = int(body[9 * 64:(9 + 1) * 64], 16)
            initialized = initialized_word != 0
            return (fg_outside0, fg_outside1, initialized)
        except (ValueError, IndexError):
            print(f"[fees-delta] Failed to decode tick {tick}")
            return (0, 0, False)

    fg_outside_lower0, fg_outside_lower1, _ = _read_tick_fee_growth_outside(tick_lower)
    fg_outside_upper0, fg_outside_upper1, _ = _read_tick_fee_growth_outside(tick_upper)

    # If ticks are not initialized, feeGrowthOutside is 0.
    # This is correct for Uniswap V3 — uninitialized ticks have 0 fee growth outside.

    # 3. Compute feeGrowthInside using the Uniswap V3 formula
    #
    # if tickCurrent < tickLower:
    #   feeGrowthInside = feeGrowthOutside_lower - feeGrowthOutside_upper
    # elif tickCurrent >= tickUpper:
    #   feeGrowthInside = feeGrowthOutside_upper - feeGrowthOutside_lower
    # else:  # tickLower <= tickCurrent < tickUpper
    #   feeGrowthInside = feeGrowthGlobal - feeGrowthOutside_lower - feeGrowthOutside_upper
    #
    # NOTE: All arithmetic is modular (mod 2^256), so subtraction works
    # even when values wrap around.

    MOD = 1 << 256

    if current_tick < tick_lower:
        fg_inside0 = (fg_outside_lower0 - fg_outside_upper0) % MOD
        fg_inside1 = (fg_outside_lower1 - fg_outside_upper1) % MOD
    elif current_tick >= tick_upper:
        fg_inside0 = (fg_outside_upper0 - fg_outside_lower0) % MOD
        fg_inside1 = (fg_outside_upper1 - fg_outside_lower1) % MOD
    else:
        fg_inside0 = (fg_global0 - fg_outside_lower0 - fg_outside_upper0) % MOD
        fg_inside1 = (fg_global1 - fg_outside_lower1 - fg_outside_upper1) % MOD

    # 4. Calculate fee delta and convert to token amounts
    #
    # feeDelta = feeGrowthInside - feeGrowthInsideLast  (mod 2^256)
    # accruedFees = liquidity * feeDelta / 2^128
    # totalUncollected = tokensOwed + accruedFees

    fee_delta0 = (fg_inside0 - fee_growth_inside0_last) % MOD
    fee_delta1 = (fg_inside1 - fee_growth_inside1_last) % MOD

    accrued0 = (liquidity * fee_delta0) >> 128
    accrued1 = (liquidity * fee_delta1) >> 128

    total_fee0_raw = tokens_owed0 + accrued0
    total_fee1_raw = tokens_owed1 + accrued1

    fee0_human = total_fee0_raw / (10 ** decimals0)
    fee1_human = total_fee1_raw / (10 ** decimals1)

    print(
        f"[fees-delta] pool={pool_address} tick_lower={tick_lower} tick_upper={tick_upper} "
        f"current_tick={current_tick} liquidity={liquidity}"
    )
    print(f"[fees-delta] fg_global0={fg_global0} fg_global1={fg_global1}")
    print(f"[fees-delta] fg_inside0={fg_inside0} fg_inside1={fg_inside1}")
    print(
        f"[fees-delta] fg_inside0_last={fee_growth_inside0_last} "
        f"fg_inside1_last={fee_growth_inside1_last}"
    )
    print(f"[fees-delta] fee_delta0={fee_delta0} fee_delta1={fee_delta1}")
    print(f"[fees-delta] accrued0={accrued0} accrued1={accrued1}")
    print(f"[fees-delta] tokens_owed0={tokens_owed0} tokens_owed1={tokens_owed1}")
    print(
        f"[fees-delta] total_fee0_raw={total_fee0_raw} total_fee1_raw={total_fee1_raw}"
    )
    print(f"[fees-delta] human0={fee0_human:.10f} human1={fee1_human:.10f}")
    status = "ok" if total_fee0_raw > 0 or total_fee1_raw > 0 else "zero"
    print(
        f"[fees-delta-result] fee0_human={fee0_human:.10f} "
        f"fee1_human={fee1_human:.10f} status={status}"
    )

    if total_fee0_raw > 0 or total_fee1_raw > 0:
        return (fee0_human, fee1_human, "ok")
    return (0.0, 0.0, "zero")


# ---------------------------------------------------------------------------
# Adapter class
# ---------------------------------------------------------------------------

def _is_evm_address(value: str) -> bool:
    """Return True if value looks like a 42-character EVM address."""
    if not isinstance(value, str):
        return False
    value = value.strip()
    if not value.startswith("0x"):
        return False
    return len(value) == 42


@register_adapter
class AerodromeAdapter(VenueAdapter):
    """Aerodrome adapter for SlipStream V3 LP position reads on BASE."""

    VENUE_KEY = "aerodrome"
    CHAINS = ["base"]

    def can_handle(self, address_or_id: str, chain_hint: str = "") -> bool:
        """Return True for 42-char EVM addresses or numeric IDs with Aerodrome/BASE hint."""
        if _is_evm_address(address_or_id):
            hint = (chain_hint or "").lower()
            return any(k in hint for k in ("aerodrome", "base", "slipstream"))
        if address_or_id.isdigit():
            hint = (chain_hint or "").lower()
            return any(k in hint for k in ("aerodrome", "base", "slipstream"))
        return False

    def _fetch_position_by_token_id(
        self,
        token_id: int,
        price_engine: Optional[PriceEngine] = None,
        wallet_address: str = "",
    ) -> LPPosition:
        """Fetch and decode a specific SlipStream position by NFT token ID.

        Tries all registered V3 Position Managers on BASE.
        """
        for pm in V3_POSITION_MANAGERS:
            data = SELECTOR_POSITIONS + _pad_int_to_64(token_id)
            result = _base_rpc_call(
                "eth_call",
                [{"to": pm, "data": data}, "latest"],
            )
            if not result or not isinstance(result, str) or len(result) < 2 + 32 * 13:
                continue

            body = result[2:]
            nonce = int(body[0:64], 16)
            if nonce == 0 and int(body[448:512], 16) == 0:
                pos = _decode_positions_response(result, token_id, price_engine, wallet_address, pm)
                if pos:
                    return pos
                continue

            pos = _decode_positions_response(result, token_id, price_engine, wallet_address, pm)
            if pos is None:
                return LPPosition(
                    position_id=f"base:{token_id}",
                    venue="Aerodrome",
                    chain="BASE",
                    error=f"Could not decode positions({token_id}) response.",
                )
            return pos

        return LPPosition(
            position_id=f"base:{token_id}",
            venue="Aerodrome",
            chain="BASE",
            error=f"Token ID {token_id} not found on any SlipStream Position Manager on BASE.",
        )

    def _fetch_pool_state_as_position(
        self,
        pool_address: str,
        price_engine: Optional[PriceEngine] = None,
    ) -> LPPosition:
        """Fetch pool state (price, tokens) for a pool address — no position data."""
        pool_address = pool_address.lower()
        current_price, current_tick, fee, token0, token1, _ = _fetch_pool_state(pool_address)

        if not token0 or not token1:
            return LPPosition(
                position_id=pool_address,
                venue="Aerodrome",
                chain="BASE",
                error="Could not read pool state. The BASE RPC may be unavailable or this is not a valid Aerodrome SlipStream pool.",
            )

        symbol0 = _get_token_symbol(token0)
        symbol1 = _get_token_symbol(token1)

        return LPPosition(
            position_id=pool_address,
            pool_id=pool_address,
            venue="Aerodrome",
            chain="BASE",
            pair=f"{symbol0}/{symbol1}",
            token_0=symbol0,
            token_1=symbol1,
            current_price=current_price,
            fees_note="Pool address shows price only. To view your LP position, enter your numeric NFT Position ID (found on Aerodrome → Liquidity → My Positions)."
        )

    def fetch_position(
        self,
        address_or_id: str,
        online_mode: bool = False,
        price_engine: Optional[PriceEngine] = None,
        chain_hint: str = "",
        wallet_address: str = "",
    ) -> LPPosition:
        """Fetch a single Aerodrome SlipStream LP position by NFT token ID or pool address."""
        if not online_mode:
            raise OfflineError("Aerodrome adapter requires online mode.")

        address_or_id = address_or_id.strip()

        lowered = address_or_id.lower()
        for pm_name, pm_addr in [("Aerodrome SlipStream", AERO_SLIPSTREAM_POSITION_MANAGER), ("Aerodrome SlipStream (alt)", AERO_SLIPSTREAM_POSITION_MANAGER_2)]:
            if lowered == pm_addr.lower():
                return LPPosition(
                    position_id=address_or_id,
                    venue="Aerodrome",
                    chain="BASE",
                    error=f"This is the {pm_name} Position Manager contract, not a position. Enter the numeric NFT Position ID (e.g. 123456) or a pool address.",
                )
        for fac_name, fac_addr in [("Aerodrome SlipStream", AERO_SLIPSTREAM_POOL_FACTORY), ("Aerodrome SlipStream (alt)", AERO_SLIPSTREAM_POOL_FACTORY_2)]:
            if lowered == fac_addr.lower():
                return LPPosition(
                    position_id=address_or_id,
                    venue="Aerodrome",
                    chain="BASE",
                    error=f"This is the {fac_name} Factory contract, not a pool. Enter the numeric NFT Position ID or a pool address.",
                )

        try:
            token_id = int(address_or_id)
            return self._fetch_position_by_token_id(token_id, price_engine, wallet_address)
        except (ValueError, TypeError):
            pass

        if _is_evm_address(address_or_id):
            return self._fetch_pool_state_as_position(address_or_id, price_engine)

        return LPPosition(
            position_id=address_or_id,
            venue="Aerodrome",
            chain="BASE",
            error="Unrecognized input. Enter a numeric NFT Position ID or a 42-character pool address (0x...).",
        )

    def fetch_all_positions(
        self,
        wallet_address: str,
        online_mode: bool = False,
        price_engine: Optional[PriceEngine] = None,
    ) -> List[LPPosition]:
        """Fetch all SlipStream LP positions owned by a wallet on BASE."""
        if not online_mode:
            raise OfflineError("Aerodrome adapter requires online mode.")

        wallet_address = wallet_address.lower().strip()
        positions: List[LPPosition] = []

        for pm in V3_POSITION_MANAGERS:
            pm_name = "Aerodrome SlipStream" if pm == AERO_SLIPSTREAM_POSITION_MANAGER else "Aerodrome SlipStream (alt)"

            balance_result = _base_rpc_call(
                "eth_call",
                [{"to": pm, "data": SELECTOR_BALANCE_OF + _pad_address(wallet_address)}, "latest"],
            )
            balance = 0
            if balance_result and isinstance(balance_result, str):
                try:
                    balance = int(balance_result[2:66], 16)
                except (ValueError, IndexError):
                    balance = 0

            print(f"[aerodrome-scan] {pm_name} balanceOf={balance} for {wallet_address}")

            if balance == 0:
                continue

            owned_ids: List[int] = []
            for idx in range(balance):
                data = SELECTOR_TOKEN_OF_OWNER_BY_INDEX + _pad_address(wallet_address) + _pad_int_to_64(idx)
                result = _base_rpc_call(
                    "eth_call",
                    [{"to": pm, "data": data}, "latest"],
                )
                if result and isinstance(result, str) and len(result) >= 66:
                    try:
                        token_id = int(result[2:66], 16)
                        owned_ids.append(token_id)
                        print(f"[aerodrome-scan] {pm_name} tokenOfOwnerByIndex({idx}) = {token_id}")
                    except (ValueError, IndexError):
                        pass

            print(f"[aerodrome-scan] {pm_name} found {len(owned_ids)} token IDs: {owned_ids}")

            for token_id in owned_ids:
                pos = self._fetch_position_by_token_id(token_id, price_engine, wallet_address)
                if pos and not pos.error:
                    positions.append(pos)

        # If we found any unstaked Aerodrome positions, use their pool addresses
        # to discover gauges and check for additional staked positions in those
        # pools. This catches staked NFTs that balanceOf() cannot see.
        existing_ids = {p.position_id for p in positions}
        if positions:
            discovered_pools = []
            for pos in positions:
                if pos.pool_id and pos.venue == "Aerodrome":
                    discovered_pools.append({
                        "venue": "Aerodrome",
                        "pool_address": pos.pool_id,
                    })
            if discovered_pools:
                staked = self._find_staked_positions_via_saved_pools(
                    wallet_address, price_engine, discovered_pools
                )
                for staked_pos in staked:
                    if staked_pos.position_id not in existing_ids:
                        if staked_pos.raw_data:
                            staked_pos.raw_data["gauge_address"] = _get_gauge_for_pool(
                                staked_pos.pool_id
                            ) if staked_pos.pool_id else None
                        positions.append(staked_pos)
                        existing_ids.add(staked_pos.position_id)

        # v5.2.2: Also discover staked positions via Transfer event logs.
        # This catches positions staked in gauges we haven't saved or discovered
        # from unstaked NFTs.
        log_staked = self._find_staked_positions_via_transfer_logs(
            wallet_address, price_engine, existing_ids
        )
        positions.extend(log_staked)

        return positions

    def _find_staked_positions_via_saved_pools(
        self,
        wallet_address: str,
        price_engine: Optional[PriceEngine],
        saved_pools: List[Dict[str, Any]],
    ) -> List[LPPosition]:
        """Find staked NFT positions by checking gauges for saved Aerodrome pools.

        For each saved pool with venue='Aerodrome', resolve its CL gauge via the
        Voter contract and check if the wallet has staked token IDs in that gauge.
        This avoids the expensive full-NFT enumeration that fails because
        SlipStream token IDs are not sequential.
        """
        positions: List[LPPosition] = []
        seen_gauges: set = set()

        for entry in saved_pools:
            venue = entry.get("venue", "")
            if venue not in ("Aerodrome", "aerodrome"):
                continue

            pool_address = entry.get("pool_address", "")
            if not pool_address:
                continue

            gauge = _get_gauge_for_pool(pool_address)
            if not gauge or gauge in seen_gauges:
                continue
            seen_gauges.add(gauge)

            staked_ids = _get_staked_token_ids(gauge, wallet_address)
            if not staked_ids:
                continue

            for staked_tid in staked_ids:
                pos = self._fetch_position_by_token_id(
                    staked_tid, price_engine, wallet_address=wallet_address
                )
                if pos and not pos.error:
                    if pos.raw_data:
                        pos.raw_data["gauge_address"] = gauge
                    positions.append(pos)

        return positions

    def _find_staked_positions_via_transfer_logs(
        self,
        wallet_address: str,
        price_engine: Optional[PriceEngine],
        existing_position_ids: set,
    ) -> List[LPPosition]:
        """Discover staked Aerodrome positions by scanning Transfer events.

        When a user stakes an NFT, the Position Manager emits a Transfer event
        from the wallet to the gauge. We scan these logs to discover gauge
        addresses, then enumerate all staked token IDs per gauge.

        Best-effort: if the RPC can't serve old logs, returns an empty list
        and the caller falls back to saved pools.
        """
        positions: List[LPPosition] = []
        wallet_lower = wallet_address.lower().strip()
        if not wallet_lower.startswith("0x") or len(wallet_lower) != 42:
            return positions
        wallet_padded = "0x" + "00" * 12 + wallet_lower[2:]
        transfer_topic = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"

        result = _base_rpc_call("eth_blockNumber", [])
        if not result or not isinstance(result, dict):
            return positions
        latest_hex = result.get("result")
        if not latest_hex:
            return positions
        try:
            latest_block = int(latest_hex, 16)
        except (ValueError, TypeError):
            return positions

        discovered_gauges: Dict[str, None] = {}
        chunk_size = 50000
        max_blocks = 2_000_000  # ~4 months on Base
        start_block = max(0, latest_block - max_blocks)

        for from_block in range(start_block, latest_block, chunk_size):
            to_block = min(from_block + chunk_size - 1, latest_block)
            for pm in V3_POSITION_MANAGERS:
                params = {
                    "fromBlock": hex(from_block),
                    "toBlock": hex(to_block),
                    "address": pm,
                    "topics": [transfer_topic, wallet_padded],  # Transfer FROM wallet
                }
                try:
                    chunk_result = _base_rpc_call("eth_getLogs", [params])
                    if not chunk_result or not isinstance(chunk_result, dict):
                        continue
                    logs = chunk_result.get("result")
                    if not isinstance(logs, list):
                        continue
                    for log in logs:
                        topics = log.get("topics", [])
                        if len(topics) < 4:
                            continue
                        to_addr = "0x" + topics[2][26:66]
                        if int(to_addr, 16) != 0:
                            discovered_gauges[to_addr.lower()] = None
                except Exception:
                    continue  # skip this chunk/PM on error

        for gauge_addr in discovered_gauges:
            staked_ids = _get_staked_token_ids(gauge_addr, wallet_address)
            for staked_tid in staked_ids:
                pos_id = f"base:{staked_tid}"
                if pos_id in existing_position_ids:
                    continue
                pos = self._fetch_position_by_token_id(
                    staked_tid, price_engine, wallet_address=wallet_address
                )
                if pos and not pos.error:
                    if pos.raw_data:
                        pos.raw_data["gauge_address"] = gauge_addr
                    positions.append(pos)
                    existing_position_ids.add(pos_id)

        return positions

    def fetch_fees_earned(
        self, position_id: str, online_mode: bool = False
    ) -> Dict[str, float]:
        """Return accumulated uncollected fees for a SlipStream position on BASE."""
        if not online_mode:
            raise OfflineError("Aerodrome adapter requires online mode.")

        if position_id.startswith("base:"):
            try:
                token_id = int(position_id.split(":", 1)[1])
            except ValueError:
                return {}

            for pm in V3_POSITION_MANAGERS:
                data = SELECTOR_POSITIONS + _pad_int_to_64(token_id)
                result = _base_rpc_call(
                    "eth_call",
                    [{"to": pm, "data": data}, "latest"],
                )
                if not result or not isinstance(result, str) or len(result) < 2 + 32 * 13:
                    continue

                body = result[2:]
                nonce = int(body[0:64], 16)
                if nonce == 0 and int(body[448:512], 16) == 0:
                    continue

                token0 = _decode_address(body[128:192]).lower()
                token1 = _decode_address(body[192:256]).lower()
                fee_tier = int(body[256:320], 16)
                tick_lower = _decode_int24(body[320:384])
                tick_upper = _decode_int24(body[384:448])
                liquidity = int(body[448:512], 16)
                fee_growth_inside0_last = int(body[512:576], 16)
                fee_growth_inside1_last = int(body[576:640], 16)
                tokens_owed0 = int(body[640:704], 16)
                tokens_owed1 = int(body[704:768], 16)
                decimals0 = _get_token_decimals(token0)
                decimals1 = _get_token_decimals(token1)

                owner_result = _base_rpc_call(
                    "eth_call",
                    [{"to": pm, "data": SELECTOR_OWNER_OF + _pad_int_to_64(token_id)}, "latest"],
                )
                nft_owner = ""
                if owner_result and isinstance(owner_result, str) and len(owner_result) >= 66:
                    nft_owner = _decode_address(owner_result[2:66])

                # Check if staked (gauge owns the NFT)
                is_staked_pos = (
                    nft_owner
                    and int(nft_owner, 16) != 0
                )

                # Read pool state for delta method
                pool_addr = _pool_for_token_ids(token0, token1, fee_tier, pm)
                _, cur_tick, _, _, _, _ = _fetch_pool_state(pool_addr) if pool_addr else (None, None, 0, "", "", None)

                if is_staked_pos and pool_addr and cur_tick is not None:
                    fee0, fee1, _ = _compute_realtime_fees(
                        pool_address=pool_addr,
                        tick_lower=tick_lower,
                        tick_upper=tick_upper,
                        current_tick=cur_tick,
                        liquidity=liquidity,
                        fee_growth_inside0_last=fee_growth_inside0_last,
                        fee_growth_inside1_last=fee_growth_inside1_last,
                        tokens_owed0=tokens_owed0,
                        tokens_owed1=tokens_owed1,
                        decimals0=decimals0,
                        decimals1=decimals1,
                    )
                else:
                    fee0, fee1, _ = _estimate_uncollected_fees(token_id, nft_owner, decimals0, decimals1, pm)

                symbol0 = _get_token_symbol(token0)
                symbol1 = _get_token_symbol(token1)
                return {symbol0: fee0, symbol1: fee1}

        return {}

    def can_write(self) -> bool:
        """Writer support is available in v5.2.2."""
        return True

    def get_writer(self):
        """Return an AerodromeWriter instance."""
        from venue_adapters.aerodrome_writer import AerodromeWriter
        return AerodromeWriter()

    def referral_code(self) -> Optional[str]:
        """Optional Aerodrome referral code — not configured yet."""
        return None

    def referral_url(self) -> Optional[str]:
        """Optional Aerodrome referral URL — not configured yet."""
        return None
