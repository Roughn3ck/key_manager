"""Krystal Venue Adapter - ColdStack LP Engine v5.2

Read-only adapter for Krystal (https://krystal.app).

Krystal is a multichain LP management aggregator — it is not a DEX itself. It
wraps underlying DEXes such as PancakeSwap V3 on BNB Chain, Uniswap V3 on
Ethereum/Arbitrum, and Orca/Raydium on Solana. This adapter supports both
Uniswap V3 and PancakeSwap V3 on BNB Chain (BSC). Krystal wraps multiple underlying
DEXes, so the adapter scans all registered V3 Position Managers to find the user's
NFT positions regardless of which DEX the pool was created on.

All network calls use stdlib urllib.request only — no web3.py, no requests.

Read-only. Stateless. Offline by default.

Version: v5.2-p2c (August 2026) - Uniswap V3 + PancakeSwap V3 on BSC
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

BSC_RPC_URL = "https://bsc-dataseed.binance.org/"
BSC_CHAIN_ID = 56
BSC_RPC_FALLBACK = "https://rpc.ankr.com/bsc"

# ---------------------------------------------------------------------------
# V3 DEX contract addresses on BSC
# Krystal wraps multiple DEXes — we query whichever Position Manager
# holds the user's NFT positions.
# ---------------------------------------------------------------------------

# Uniswap V3 on BSC (deployed by Uniswap after BSL expiry)
UNISWAP_V3_POSITION_MANAGER = "0x7b8A01B39D58278b5DE7e48c8449c9f4F5170613"
UNISWAP_V3_POOL_FACTORY = "0xdB1d10011AD0Ff90774D0C6Bb92e5C5c8b4461F7"

# PancakeSwap V3 on BSC
PANCAKE_V3_POSITION_MANAGER = "0x46A15B0b27311cedF172AB29E4f4766fbE7F4364"
PANCAKE_V3_POOL_FACTORY = "0x0BFbCF9fa4f9C56B0F40a671Ad40E0805A091865"
PANCAKE_V3_SWAP_ROUTER = "0x1b81D678ffb9C0263b24A97847620C99d213eB14"
PANCAKE_V3_POOL_DEPLOYER = "0x41ff9AA7e16B8B1a8a8dc4f0eFacd93D02d071c9"

# List of all V3 Position Managers to scan (in order)
V3_POSITION_MANAGERS = [
    UNISWAP_V3_POSITION_MANAGER,
    PANCAKE_V3_POSITION_MANAGER,
]

# Map each Position Manager to its Factory (for pool resolution)
POSITION_MANAGER_TO_FACTORY = {
    UNISWAP_V3_POSITION_MANAGER.lower(): UNISWAP_V3_POOL_FACTORY,
    PANCAKE_V3_POSITION_MANAGER.lower(): PANCAKE_V3_POOL_FACTORY,
}

# Common BSC token addresses
WBNB = "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bcF2aE"
USDT_BSC = "0x55d398326f99059fF775485246999027B3197955"

# ---------------------------------------------------------------------------
# Function selectors (standard Uniswap V3 — identical for PancakeSwap V3)
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
# collect() is used as a read-only eth_call to get exact uncollected fees.
SELECTOR_COLLECT = "0xfc6f7865"  # collect((uint256,address,uint128,uint128))
SELECTOR_DECREASE_LIQUIDITY = "0x2c1eaa8e"  # decreaseLiquidity((uint256,uint128,uint256,uint256,uint256))

# ---------------------------------------------------------------------------
# Token registries
# ---------------------------------------------------------------------------

TOKEN_DECIMALS = {
    WBNB.lower(): 18,
    USDT_BSC.lower(): 18,  # BSC USDT is 18 decimals (unlike Ethereum's 6)
    # Add more as needed:
    # BUSD: 0xe9e7CEA3DedcA5984780Bafc599bD69ADd087D56 (18)
    # CAKE: 0x0E09FaBB73Fd3A0fbDA3Ad2C1C5E41b1c0E6a336 (18)
    # LINK: 0xF8A0BF9cF54Bb92F17374d9e9A321A6e4A6d7C43 (18)
}

TOKEN_SYMBOLS = {
    WBNB.lower(): "WBNB",
    USDT_BSC.lower(): "USDT",
}


# ---------------------------------------------------------------------------
# RPC layer
# ---------------------------------------------------------------------------

def _bsc_rpc_call(method: str, params: list, request_id: int = 1) -> Optional[Any]:
    """Make a single JSON-RPC call to BSC and return the 'result' field."""
    payload = json.dumps(
        {"jsonrpc": "2.0", "method": method, "params": params, "id": request_id}
    ).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "ColdStack/5.2",
    }

    for url in (BSC_RPC_URL, BSC_RPC_FALLBACK):
        try:
            req = urllib.request.Request(url, data=payload, headers=headers)
            with urllib.request.urlopen(req, timeout=15) as response:
                data = json.loads(response.read().decode("utf-8"))
                return data.get("result")
        except Exception:
            # Fall back to next URL on any error.
            continue
    return None


def _bsc_rpc_batch(
    method_calls: List[Tuple[str, list]], request_id_base: int = 1
) -> List[Optional[Any]]:
    """Execute batched JSON-RPC calls. BSC supports larger batches than HyperEVM."""
    if not method_calls:
        return []
    if len(method_calls) == 1:
        method, params = method_calls[0]
        return [_bsc_rpc_call(method, params, request_id_base)]

    # BSC public RPCs generally tolerate up to 5 calls per batch.
    MAX_BATCH = 5
    results: List[Optional[Any]] = []
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "ColdStack/5.2",
    }

    for chunk_start in range(0, len(method_calls), MAX_BATCH):
        chunk = method_calls[chunk_start:chunk_start + MAX_BATCH]
        payload_obj = []
        for idx, (method, params) in enumerate(chunk):
            payload_obj.append(
                {"jsonrpc": "2.0", "id": request_id_base + chunk_start + idx, "method": method, "params": params}
            )

        for url in (BSC_RPC_URL, BSC_RPC_FALLBACK):
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
                    break  # successful batch, move to next chunk
            except Exception:
                # On last URL, fall back to sequential calls for this chunk.
                if url == BSC_RPC_FALLBACK:
                    for idx, (method, params) in enumerate(chunk):
                        results.append(_bsc_rpc_call(method, params, request_id_base + chunk_start + idx))
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
    """Decode a signed 24-bit int packed in a 32-byte word."""
    raw = int(hex_str, 16)
    if raw >= 2 ** 127:
        raw -= 2 ** 256
    # int24 only uses 24 bits; sign-extend if needed.
    if raw >= 2 ** 23:
        raw -= 2 ** 24
    elif raw < -(2 ** 23):
        raw += 2 ** 24
    return raw


def _decode_address(hex_str: str) -> str:
    """Extract a 20-byte address from a 32-byte ABI word."""
    return "0x" + hex_str[-40:]


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
        result = _bsc_rpc_call(
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
        result = _bsc_rpc_call(
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
    mapping = {
        "WBNB": "BNB",
        "WBTC": "BTC",
        "WETH": "ETH",
        "BUSD": "USD",
        "USDT": "USD",
        "USDC": "USD",
        "CAKE": "CAKE",
        "LINK": "LINK",
    }
    return mapping.get(symbol.upper(), symbol.upper())


def _usd_value(amount: Optional[float], symbol: str, price_engine: Optional[PriceEngine]) -> Optional[float]:
    """Convert a token amount to USD using the shared price engine."""
    if amount is None or amount <= 0 or not price_engine:
        return None
    canonical = _canonical_symbol(symbol)
    return price_engine.convert_balance_to_fiat(amount, canonical, currency="usd")


# ---------------------------------------------------------------------------
# Pool helpers
# ---------------------------------------------------------------------------

def _pool_for_token_ids(
    token0: str, token1: str, fee: int, position_manager: str
) -> Optional[str]:
    """Resolve pool address via the correct factory for the given position manager."""
    factory = POSITION_MANAGER_TO_FACTORY.get(position_manager.lower())
    if not factory:
        return None
    # Enforce canonical token sort order used by V3 factories.
    if token1.lower() < token0.lower():
        token0, token1 = token1, token0
    data = SELECTOR_GET_POOL + _pad_address(token0) + _pad_address(token1) + _pad_int_to_64(fee)
    result = _bsc_rpc_call("eth_call", [{"to": factory, "data": data}, "latest"])
    if result and isinstance(result, str) and len(result) >= 66:
        addr = _decode_address(result[2:66])
        if int(addr, 16) != 0:
            return addr.lower()
    return None


def _fetch_pool_state(
    pool_address: str
) -> Tuple[Optional[float], Optional[int], int, str, str, Optional[int]]:
    """Return (human_price, current_tick, fee, token0, token1, sqrtPriceX96_raw) for a pool."""
    pool_address = pool_address.lower()
    slot0_result = _bsc_rpc_call(
        "eth_call",
        [{"to": pool_address, "data": SELECTOR_SLOT0}, "latest"],
    )
    fee_result = _bsc_rpc_call(
        "eth_call",
        [{"to": pool_address, "data": SELECTOR_FEE}, "latest"],
    )
    token0_result = _bsc_rpc_call(
        "eth_call",
        [{"to": pool_address, "data": SELECTOR_TOKEN0}, "latest"],
    )
    token1_result = _bsc_rpc_call(
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
    """Decode a PancakeSwap V3 positions(uint256) return into an LPPosition."""
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

    # Override with DexScreener's correct symbol if available
    # (on-chain symbol() can return null-padded or wrong symbols)
    if price_engine:
        ds_symbol0 = price_engine.get_token_symbol_by_address(token0, chain="bsc")
        if ds_symbol0:
            symbol0 = ds_symbol0
        ds_symbol1 = price_engine.get_token_symbol_by_address(token1, chain="bsc")
        if ds_symbol1:
            symbol1 = ds_symbol1

    pool_address = _pool_for_token_ids(token0, token1, fee, position_manager)
    current_price: Optional[float] = None
    current_tick: Optional[int] = None
    sqrtPriceX96: Optional[int] = None
    if pool_address:
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

    # Estimate real uncollected fees via collect() eth_call (read-only).
    fees_note = None
    if not wallet_address and position_manager:
        # Look up the actual owner of this NFT and use it for the collect() call
        owner_result = _bsc_rpc_call(
            "eth_call",
            [{"to": position_manager, "data": SELECTOR_OWNER_OF + _pad_int_to_64(token_id)}, "latest"],
        )
        if owner_result and isinstance(owner_result, str) and len(owner_result) >= 66:
            wallet_address = _decode_address(owner_result[2:66])

    if wallet_address:
        real_fee0, real_fee1, fee_status = _estimate_uncollected_fees(
            token_id, wallet_address, decimals0, decimals1, position_manager
        )
        if fee_status == "ok":
            owed0_h = real_fee0
            owed1_h = real_fee1
        elif fee_status == "zero":
            fees_note = "No uncollected fees"
        else:  # "error"
            fees_note = "RPC unreachable"
    else:
        fees_note = "Connect wallet to read fees"

    fees_earned = {symbol0: owed0_h, symbol1: owed1_h}

    # Compute fees USD value using DexScreener first, CoinGecko fallback
    fees_earned_usd = 0.0
    if price_engine:
        fee_price0 = price_engine.get_token_price_by_address(token0, chain="bsc")
        if fee_price0:
            fees_earned_usd += owed0_h * fee_price0
        else:
            fees_earned_usd += _usd_value(owed0_h, symbol0, price_engine) or 0.0

        fee_price1 = price_engine.get_token_price_by_address(token1, chain="bsc")
        if fee_price1:
            fees_earned_usd += owed1_h * fee_price1
        else:
            fees_earned_usd += _usd_value(owed1_h, symbol1, price_engine) or 0.0

    # Compute position value using V3 liquidity math
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
            # current_price is human-readable (adjusted for decimals).
            # Convert back to raw price for V3 math.
            raw_price = current_price * (10 ** (decimals1 - decimals0))
            sqrt_price = math.sqrt(raw_price)
        else:
            sqrt_price = None

        if sqrt_price is None:
            print(f"[bsc-value] No price data for token {token_id} — pool state fetch may have failed")

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

            # Primary: DexScreener token price by contract address
            val0 = 0.0
            val1 = 0.0
            if price_engine:
                price0 = price_engine.get_token_price_by_address(token0, chain="bsc")
                if price0:
                    val0 = amount0_human * price0
                price1 = price_engine.get_token_price_by_address(token1, chain="bsc")
                if price1:
                    val1 = amount1_human * price1

            # Fallback: CoinGecko by symbol
            if val0 == 0.0:
                val0 = _usd_value(amount0_human, symbol0, price_engine) or 0.0
            if val1 == 0.0:
                val1 = _usd_value(amount1_human, symbol1, price_engine) or 0.0

            position_value_usd = val0 + val1

            print(f"[bsc-value] token {token_id}: val0={val0} val1={val1} total={position_value_usd}")

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
        position_id=f"bsc:{token_id}",
        pool_id=pool_address,
        venue="BSC",
        chain="BSC",
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
        position_manager = PANCAKE_V3_POSITION_MANAGER

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
            result = _bsc_rpc_call(
                "eth_call",
                [{"to": position_manager, "data": data, "from": wallet_address}, "latest"],
            )
            print(f"[fees] token {token_id}: raw collect() result length={len(result) if result else 0}")
            if not result or not isinstance(result, str) or len(result) < 2 + 64:
                print(f"[fees] collect() eth_call returned no data for token {token_id}")
                # Try without the `from` field — some RPCs don't require it
                result = _bsc_rpc_call(
                    "eth_call",
                    [{"to": position_manager, "data": data}, "latest"],
                )
                if not result or not isinstance(result, str) or len(result) < 2 + 64:
                    return (0.0, 0.0, "error")

            body = result[2:]
            # Handle both packed (2 x uint128 in 32 bytes) and padded (2 x uint256) formats
            if len(body) >= 128:
                # Padded format: two 32-byte words
                amount0_raw = int(body[0:64], 16)
                amount1_raw = int(body[64:128], 16)
            elif len(body) >= 64:
                # Packed format: two 16-byte values in 32 bytes
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
class BSCAdapter(VenueAdapter):
    """BSC adapter for V3 LP position reads on BNB Chain."""

    VENUE_KEY = "bsc"
    CHAINS = ["bsc", "ethereum", "arbitrum", "solana"]

    def can_handle(self, address_or_id: str, chain_hint: str = "") -> bool:
        """Return True for 42-char EVM addresses or numeric IDs with BSC hint."""
        if _is_evm_address(address_or_id):
            hint = (chain_hint or "").lower()
            return any(k in hint for k in ("bsc", "pancakeswap"))
        # Numeric token ID with BSC hint
        if address_or_id.isdigit():
            hint = (chain_hint or "").lower()
            return any(k in hint for k in ("bsc", "pancakeswap"))
        return False

    def _fetch_position_by_token_id(
        self,
        token_id: int,
        price_engine: Optional[PriceEngine] = None,
        wallet_address: str = "",
    ) -> LPPosition:
        """Fetch and decode a specific V3 position by NFT token ID.

        Tries all registered V3 Position Managers on BSC (Uniswap V3, PancakeSwap V3).
        """
        for pm in V3_POSITION_MANAGERS:
            data = SELECTOR_POSITIONS + _pad_int_to_64(token_id)
            result = _bsc_rpc_call(
                "eth_call",
                [{"to": pm, "data": data}, "latest"],
            )
            if not result or not isinstance(result, str) or len(result) < 2 + 32 * 13:
                continue

            # Check if this PM actually owns the token (nonce > 0 means position exists)
            body = result[2:]
            nonce = int(body[0:64], 16)
            if nonce == 0 and int(body[448:512], 16) == 0:
                # nonce=0 and liquidity=0 — might be an empty/closed position, try next PM
                # But still decode it — the user may want to see closed positions
                pos = _decode_positions_response(result, token_id, price_engine, wallet_address, pm)
                if pos:
                    return pos
                continue

            pos = _decode_positions_response(result, token_id, price_engine, wallet_address, pm)
            if pos is None:
                return LPPosition(
                    position_id=f"bsc:{token_id}",
                    venue="BSC",
                    chain="BSC",
                    error=f"Could not decode positions({token_id}) response.",
                )
            return pos

        return LPPosition(
            position_id=f"bsc:{token_id}",
            venue="BSC",
            chain="BSC",
            error=f"Token ID {token_id} not found on any V3 Position Manager on BSC.",
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
                venue="BSC",
                chain="BSC",
                error="Could not read pool state. The BSC RPC may be unavailable or this is not a valid PancakeSwap V3 pool.",
            )

        symbol0 = _get_token_symbol(token0)
        symbol1 = _get_token_symbol(token1)

        return LPPosition(
            position_id=pool_address,
            pool_id=pool_address,
            venue="BSC",
            chain="BSC",
            pair=f"{symbol0}/{symbol1}",
            token_0=symbol0,
            token_1=symbol1,
            current_price=current_price,
            fees_note="Pool address shows price only. Enter the numeric NFT Position ID to view a specific position.",
        )

    def fetch_position(
        self,
        address_or_id: str,
        online_mode: bool = False,
        price_engine: Optional[PriceEngine] = None,
        chain_hint: str = "",
        wallet_address: str = "",
    ) -> LPPosition:
        """Fetch a single PancakeSwap V3 LP position by NFT token ID or pool address."""
        if not online_mode:
            raise OfflineError("BSC adapter requires online mode.")

        address_or_id = address_or_id.strip()

        # 1. Known contract addresses — helpful error messages
        lowered = address_or_id.lower()
        for pm_name, pm_addr in [("Uniswap V3", UNISWAP_V3_POSITION_MANAGER), ("PancakeSwap V3", PANCAKE_V3_POSITION_MANAGER)]:
            if lowered == pm_addr.lower():
                return LPPosition(
                    position_id=address_or_id,
                    venue="BSC",
                    chain="BSC",
                    error=f"This is the {pm_name} Position Manager contract, not a position. Enter the numeric NFT Position ID (e.g. 123456) or a pool address.",
                )
        for fac_name, fac_addr in [("Uniswap V3", UNISWAP_V3_POOL_FACTORY), ("PancakeSwap V3", PANCAKE_V3_POOL_FACTORY)]:
            if lowered == fac_addr.lower():
                return LPPosition(
                    position_id=address_or_id,
                    venue="BSC",
                    chain="BSC",
                    error=f"This is the {fac_name} Factory contract, not a pool. Enter the numeric NFT Position ID or a pool address.",
                )

        # 2. Numeric NFT token ID — fetch position directly
        try:
            token_id = int(address_or_id)
            return self._fetch_position_by_token_id(token_id, price_engine, wallet_address)
        except (ValueError, TypeError):
            pass

        # 3. 42-char EVM address — treat as pool address, fetch pool state only
        if _is_evm_address(address_or_id):
            return self._fetch_pool_state_as_position(address_or_id, price_engine)

        # 4. Unknown format
        return LPPosition(
            position_id=address_or_id,
            venue="BSC",
            chain="BSC",
            error="Unrecognized input. Enter a numeric NFT Position ID or a 42-character pool address (0x...).",
        )

    def fetch_all_positions(
        self,
        wallet_address: str,
        online_mode: bool = False,
        price_engine: Optional[PriceEngine] = None,
    ) -> List[LPPosition]:
        """Fetch all V3 LP positions owned by a wallet on BSC.

        Scans all registered V3 Position Managers (Uniswap V3, PancakeSwap V3).
        """
        if not online_mode:
            raise OfflineError("BSC adapter requires online mode.")

        wallet_address = wallet_address.lower().strip()
        positions: List[LPPosition] = []

        for pm in V3_POSITION_MANAGERS:
            pm_name = "Uniswap V3" if pm == UNISWAP_V3_POSITION_MANAGER else "PancakeSwap V3"

            # 1. Get the wallet's NFT balance on this Position Manager
            balance_result = _bsc_rpc_call(
                "eth_call",
                [{"to": pm, "data": SELECTOR_BALANCE_OF + _pad_address(wallet_address)}, "latest"],
            )
            balance = 0
            if balance_result and isinstance(balance_result, str):
                try:
                    balance = int(balance_result[2:66], 16)
                except (ValueError, IndexError):
                    balance = 0

            print(f"[bsc-scan] {pm_name} balanceOf={balance} for {wallet_address}")

            if balance == 0:
                continue

            # 2. Get each token ID via tokenOfOwnerByIndex
            owned_ids: List[int] = []
            for idx in range(balance):
                data = SELECTOR_TOKEN_OF_OWNER_BY_INDEX + _pad_address(wallet_address) + _pad_int_to_64(idx)
                result = _bsc_rpc_call(
                    "eth_call",
                    [{"to": pm, "data": data}, "latest"],
                )
                if result and isinstance(result, str) and len(result) >= 66:
                    try:
                        token_id = int(result[2:66], 16)
                        owned_ids.append(token_id)
                        print(f"[bsc-scan] {pm_name} tokenOfOwnerByIndex({idx}) = {token_id}")
                    except (ValueError, IndexError):
                        pass

            print(f"[bsc-scan] {pm_name} found {len(owned_ids)} token IDs: {owned_ids}")

            # 3. Fetch each position by token ID
            for token_id in owned_ids:
                pos = self._fetch_position_by_token_id(token_id, price_engine, wallet_address)
                if pos and not pos.error:
                    positions.append(pos)

        return positions

    def fetch_fees_earned(
        self, position_id: str, online_mode: bool = False
    ) -> Dict[str, float]:
        """Return accumulated uncollected fees for a V3 position on BSC."""
        if not online_mode:
            raise OfflineError("BSC adapter requires online mode.")

        if position_id.startswith("bsc:"):
            try:
                token_id = int(position_id.split(":", 1)[1])
            except ValueError:
                return {}

            # Try all position managers
            for pm in V3_POSITION_MANAGERS:
                data = SELECTOR_POSITIONS + _pad_int_to_64(token_id)
                result = _bsc_rpc_call(
                    "eth_call",
                    [{"to": pm, "data": data}, "latest"],
                )
                if not result or not isinstance(result, str) or len(result) < 2 + 32 * 13:
                    continue

                body = result[2:]
                nonce = int(body[0:64], 16)
                if nonce == 0 and int(body[448:512], 16) == 0:
                    # Check next PM
                    continue

                token0 = _decode_address(body[128:192]).lower()
                token1 = _decode_address(body[192:256]).lower()
                decimals0 = _get_token_decimals(token0)
                decimals1 = _get_token_decimals(token1)

                # Get owner for collect() call
                owner_result = _bsc_rpc_call(
                    "eth_call",
                    [{"to": pm, "data": SELECTOR_OWNER_OF + _pad_int_to_64(token_id)}, "latest"],
                )
                wallet = ""
                if owner_result and isinstance(owner_result, str) and len(owner_result) >= 66:
                    wallet = _decode_address(owner_result[2:66])

                if not wallet:
                    return {}

                fee0, fee1, _ = _estimate_uncollected_fees(token_id, wallet, decimals0, decimals1, pm)
                symbol0 = _get_token_symbol(token0)
                symbol1 = _get_token_symbol(token1)
                return {symbol0: fee0, symbol1: fee1}

        return {}

    def can_write(self) -> bool:
        """Writer support is available in v5.2.1."""
        return True

    def get_writer(self):
        """Return a BSCWriter instance."""
        from venue_adapters.bsc_writer import BSCWriter
        return BSCWriter()

    def referral_code(self) -> Optional[str]:
        """Optional Krystal referral code — not configured yet."""
        return None

    def referral_url(self) -> Optional[str]:
        """Optional Krystal referral URL — not configured yet."""
        return None


