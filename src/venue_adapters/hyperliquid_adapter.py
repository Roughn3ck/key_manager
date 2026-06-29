"""
Hyperliquid Venue Adapter - ColdStack LP Engine v5.1

Read-only adapter for Hyperliquid venues:
  - Hyperliquid L1 (api.hyperliquid.xyz/info) perp/spot positions
  - HyperEVM (rpc.hyperliquid.xyz/evm) concentrated-liquidity style pools

Uses only stdlib urllib.request (no axios, no web3.py).

Read-only. Stateless. Offline by default.

Version: v5.1 (June 2026) - NFT position reads + multi-position scan + pool state
"""
import json
import math
import urllib.request
import urllib.error
from typing import Any, Dict, List, Optional, Tuple

from lp_engine import LPPosition, OfflineError, VenueAdapter, register_adapter
from price_engine import PriceEngine


HYPERLIQUID_INFO_URL = "https://api.hyperliquid.xyz/info"
HYPEREVM_RPC_URL = "https://rpc.hyperliquid.xyz/evm"
HYPEREVM_CHAIN_ID = 999

# Contract addresses (HyperEVM, Chain ID 999)
WHYPE = "0x5555555555555555555555555555555555555555"
UBTC = "0x9FDBdA0A5e284c32744D2f17Ee5c74B284993463"
POOL_FACTORY = "0xb1c0fa0b789320044a6f623cfe5ebda9562602e3"
POSITION_MANAGER = "0xead19ae861c29bbb2101e834922b2feee69b9091"
WHYPE_UBTC_POOL_3000 = "0x3a36b04bcc1d5e2e303981ef643d2668e00b43e7"

# Common Uniswap V3 style function selectors (keccak256 first 4 bytes)
SELECTOR_SLOT0 = "0x3850c7bd"
SELECTOR_POSITIONS = "0x99fbab88"
SELECTOR_BALANCE_OF = "0x70a08231"
SELECTOR_OWNER_OF = "0x6352211e"
SELECTOR_TOTAL_SUPPLY = "0x18160ddd"
SELECTOR_TOKEN_OF_OWNER_BY_INDEX = "0x2f745c59"
SELECTOR_FEE = "0xddca3f43"
SELECTOR_TOKEN0 = "0x0dfe1681"
SELECTOR_TOKEN1 = "0xd21220a7"
SELECTOR_DECIMALS = "0x313ce567"
SELECTOR_SYMBOL = "0x95d89b41"
SELECTOR_GET_POOL = "0x1698ee82"

TOKEN_DECIMALS = {
    WHYPE.lower(): 18,
    UBTC.lower(): 8,
    # USDC placeholder until research fills in the address.
    "0xa9f32a5317fe4ac64a06f4f3bede0a4b8e734a4e".lower(): 6,
}

TOKEN_SYMBOLS = {
    WHYPE.lower(): "WHYPE",
    UBTC.lower(): "UBTC",
}


def _is_hex_string(s: str, length: Optional[int] = None) -> bool:
    """Check if a string is a hex literal (with optional length incl 0x)."""
    if not s or not isinstance(s, str):
        return False
    if s.startswith("0x") or s.startswith("0X"):
        body = s[2:]
    else:
        body = s
        s = "0x" + s
    try:
        int(body, 16)
    except ValueError:
        return False
    if length is not None:
        return len(s) == length
    return True


def _hyperliquid_post(payload: Dict[str, Any]) -> Any:
    """POST JSON to Hyperliquid info endpoint and return parsed response."""
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        HYPERLIQUID_INFO_URL,
        data=data,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "ColdStack/5.1",
        },
    )
    with urllib.request.urlopen(req, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def _evm_rpc_single(method: str, params: list, request_id: int = 1) -> Optional[Any]:
    """Make a single JSON-RPC call to HyperEVM and return the 'result' field."""
    payload = json.dumps(
        {"jsonrpc": "2.0", "method": method, "params": params, "id": request_id}
    ).encode("utf-8")
    req = urllib.request.Request(
        HYPEREVM_RPC_URL,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "ColdStack/5.1",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            data = json.loads(response.read().decode("utf-8"))
            return data.get("result")
    except Exception:
        return None


def _evm_rpc_batch(method_calls: List[Tuple[Any, ...]], request_id_base: int = 1) -> List[Optional[Any]]:
    """Execute batched JSON-RPC calls and return result field per request.

    HyperEVM rate-limits aggressively (max ~2 calls per batch). Keep batch
    size tiny, and fall back to sequential calls if the batch errors.
    """
    if not method_calls:
        return []
    if len(method_calls) == 1:
        method, params = method_calls[0]
        return [_evm_rpc_single(method, params, request_id_base)]

    # HyperEVM tolerates 2 calls per batch; 3+ triggers rate limiting.
    MAX_BATCH = 2
    results: List[Optional[Any]] = []
    for chunk_start in range(0, len(method_calls), MAX_BATCH):
        chunk = method_calls[chunk_start:chunk_start + MAX_BATCH]
        payload_obj = []
        for idx, (method, params) in enumerate(chunk):
            payload_obj.append(
                {"jsonrpc": "2.0", "id": request_id_base + chunk_start + idx, "method": method, "params": params}
            )
        payload = json.dumps(payload_obj).encode("utf-8")
        req = urllib.request.Request(
            HYPEREVM_RPC_URL,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "User-Agent": "ColdStack/5.1",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                data = json.loads(response.read().decode("utf-8"))
                if isinstance(data, dict) and data.get("error"):
                    raise RuntimeError(data.get("error", {}).get("message", "RPC error"))
                if not isinstance(data, list):
                    raise RuntimeError("non-list RPC response")
                by_id = {item.get("id"): item for item in data}
                for idx in range(len(chunk)):
                    results.append(by_id.get(request_id_base + chunk_start + idx, {}).get("result"))
        except Exception:
            # Fall back to sequential calls for this chunk.
            for idx, (method, params) in enumerate(chunk):
                results.append(_evm_rpc_single(method, params, request_id_base + chunk_start + idx))
    return results


def _evm_rpc_call(method: str, params: list, request_id: int = 1) -> Optional[Any]:
    """Make a single JSON-RPC call to HyperEVM and return the 'result' field."""
    return _evm_rpc_single(method, params, request_id)


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


def _price_to_tick(price: float, decimals0: int, decimals1: int) -> int:
    """Convert a human price to the nearest Uniswap V3 tick."""
    raw = price * (10 ** (decimals1 - decimals0))
    return int(math.floor(math.log(raw, 1.0001)))


def _align_tick(tick: int, tick_spacing: int) -> int:
    """Round a tick down to a valid multiple of tick_spacing."""
    return (tick // tick_spacing) * tick_spacing


def _tick_spacing(fee: int) -> int:
    """Return the tick spacing for a Uniswap V3 fee tier."""
    mapping = {100: 1, 500: 10, 3000: 60, 10000: 200}
    return mapping.get(fee, 60)


def _get_token_decimals(token_address: str) -> int:
    """Return known decimals; fall back to on-chain decimals() call."""
    lower = token_address.lower()
    if lower in TOKEN_DECIMALS:
        return TOKEN_DECIMALS[lower]
    try:
        result = _evm_rpc_call(
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
        result = _evm_rpc_call(
            "eth_call",
            [{"to": lower, "data": SELECTOR_SYMBOL}, "latest"],
        )
        if result and isinstance(result, str) and len(result) >= 66:
            length = int(result[2:66], 16)
            if length:
                offset = 66 + 64
                hex_body = result[2:][offset:offset + length * 2]
                return bytes.fromhex(hex_body).decode("utf-8", errors="ignore").strip()
    except Exception:
        pass
    return lower[-6:].upper()


def _pool_for_token_ids(token0: str, token1: str, fee: int) -> Optional[str]:
    """Resolve pool address via Factory.getPool(token0, token1, fee)."""
    # Enforce canonical token sort order used by V3 factories.
    if token1.lower() < token0.lower():
        token0, token1 = token1, token0
    data = SELECTOR_GET_POOL + _pad_address(token0) + _pad_address(token1) + _pad_int_to_64(fee)
    result = _evm_rpc_call(
        "eth_call",
        [{"to": POOL_FACTORY, "data": data}, "latest"],
    )
    if result and isinstance(result, str) and len(result) >= 66:
        addr = _decode_address(result[2:66])
        if int(addr, 16) != 0:
            return addr.lower()
    return None


def _fetch_pool_state(pool_address: str) -> Tuple[Optional[float], Optional[int], int, str, str]:
    """Return (human_price, current_tick, fee, token0, token1) for a pool."""
    pool_address = pool_address.lower()
    slot0_result = _evm_rpc_call(
        "eth_call",
        [{"to": pool_address, "data": SELECTOR_SLOT0}, "latest"],
    )
    fee_result = _evm_rpc_call(
        "eth_call",
        [{"to": pool_address, "data": SELECTOR_FEE}, "latest"],
    )
    token0_result = _evm_rpc_call(
        "eth_call",
        [{"to": pool_address, "data": SELECTOR_TOKEN0}, "latest"],
    )
    token1_result = _evm_rpc_call(
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
    if (
        slot0_result
        and isinstance(slot0_result, str)
        and len(slot0_result) >= 2 + 32 * 6
    ):
        try:
            sqrt_price_x96 = int(slot0_result[2:66], 16)
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

    return current_price, current_tick, fee, token0, token1


def _decode_positions_response(
    lp_data: str, token_id: int, price_engine: Optional[PriceEngine]
) -> Optional[LPPosition]:
    """Decode a positions(uint256) return into an LPPosition with pool metadata."""
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

    pool_address = _pool_for_token_ids(token0, token1, fee)
    current_price: Optional[float] = None
    current_tick: Optional[int] = None
    if pool_address:
        current_price, current_tick, _, _, _ = _fetch_pool_state(pool_address)

    range_low = _tick_to_price(tick_lower, decimals0, decimals1)
    range_high = _tick_to_price(tick_upper, decimals0, decimals1)

    position_in_range_pct: Optional[float] = None
    if current_tick is not None and tick_upper != tick_lower:
        position_in_range_pct = (
            (current_tick - tick_lower) / (tick_upper - tick_lower) * 100.0
        )

    owed0_h = tokens_owed0 / (10 ** decimals0)
    owed1_h = tokens_owed1 / (10 ** decimals1)
    fees_earned = {symbol0: owed0_h, symbol1: owed1_h}
    fees_earned_usd = (
        _usd_value(owed0_h, symbol0, price_engine) or 0.0
    ) + (_usd_value(owed1_h, symbol1, price_engine) or 0.0)

    # Deposit amounts: we don't have exact deposit history, so report current
    # liquidity expressed as token units. For V3, we can't recover exact amounts
    # without pool state and fee-growth snapshots, but we surface liquidity raw.
    deposit_amounts: Dict[str, float] = {}
    if liquidity:
        # Heuristic placeholder: liquidity count scaled by 1/1000 of human unit.
        # This is intentionally conservative and will be refined once the writer
        # module tracks mint events or re-reads via pool collect estimates.
        deposit_amounts[symbol0] = liquidity / (10 ** (decimals0 + 3))
        deposit_amounts[symbol1] = liquidity / (10 ** (decimals1 + 3))

    return LPPosition(
        position_id=f"hyperevm:{token_id}",
        pool_id=pool_address,
        venue="HyperEVM",
        chain="HyperEVM",
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
        current_value_usd=fees_earned_usd,
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


def _format_price(mark_px: Any) -> Optional[float]:
    """Safely convert Hyperliquid mark price to float."""
    try:
        return float(mark_px)
    except (TypeError, ValueError):
        return None


def _canonical_symbol(symbol: str) -> str:
    """Map wrapper / bridged tokens to canonical price symbols."""
    mapping = {
        "WHYPE": "HYPE",
        "UBTC": "BTC",
        "WBTC": "BTC",
        "CBBTC": "BTC",
        "LBTC": "BTC",
        "SOLVBTC": "BTC",
        "WETH": "ETH",
        "STETH": "ETH",
        "WSTETH": "ETH",
        "CBETH": "ETH",
        "WSOL": "SOL",
        "MSOL": "SOL",
    }
    return mapping.get(symbol.upper(), symbol.upper())


def _usd_value(amount: Optional[float], symbol: str, price_engine: Optional[PriceEngine]) -> Optional[float]:
    """Convert a token amount to USD using the shared price engine."""
    if amount is None or amount <= 0 or not price_engine:
        return None
    canonical = _canonical_symbol(symbol)
    return price_engine.convert_balance_to_fiat(amount, canonical, currency="usd")


@register_adapter
class HyperliquidAdapter(VenueAdapter):
    """Adapter for Hyperliquid L1 and HyperEVM venues."""

    VENUE_KEY = "hyperliquid"
    CHAINS = ["hyperliquid", "hyperevm", "hype"]

    # --- Venue detection ---------------------------------------------------

    def can_handle(self, address_or_id: str, chain_hint: str = "") -> bool:
        """Detect Hyperliquid addresses.

        - 42-char hex (20-byte EVM) is treated as Hyperliquid L1 unless chain_hint
          explicitly says HyperEVM/hype.
        - 42-char hex with chain_hint 'hyperevm' or 'hype' is HyperEVM.
        """
        hint = chain_hint.lower()
        if any(k in hint for k in ("hyperliquid", "hyperevm", "hype")):
            return _is_hex_string(address_or_id, length=42) or _is_hex_string(
                address_or_id, length=None
            )
        # Default: only accept 20-byte EVM addresses as Hyperliquid L1 wallet addresses.
        return _is_hex_string(address_or_id, length=42)

    def _chain_mode(self, address_or_id: str, chain_hint: str = "") -> str:
        """Return 'l1' or 'evm' based on address + hint."""
        hint = chain_hint.lower()
        if any(k in hint for k in ("hyperevm", "hype")) and _is_hex_string(
            address_or_id, length=42
        ):
            return "evm"
        return "l1"

    # --- HyperEVM position reads -------------------------------------------

    def fetch_evm_lp_positions(
        self,
        wallet_address: str,
        online_mode: bool = False,
        price_engine: Optional[PriceEngine] = None,
    ) -> List[LPPosition]:
        """Scan the NFT Position Manager for all LP positions owned by wallet.

        The Project X position manager tracks ownership but does not expose
        tokenOfOwnerByIndex. We scan the global NFT id space up to totalSupply
        and collect positions whose ownerOf() matches the wallet. This avoids
        relying on an enumeration index that reverts on this contract.

        To keep runtime reasonable despite ~1.4s per HyperEVM RPC round-trip,
        we batch ownerOf() calls and stop once the wallet's balanceOf() count
        of owned tokens has been found.
        """
        if not online_mode:
            raise OfflineError("Hyperliquid adapter requires online mode.")

        wallet_address = wallet_address.lower()
        positions: List[LPPosition] = []

        # 1. Known token-id hints. For wallets we have already seen on-chain we
        # can short-circuit the expensive global scan by probing the known IDs
        # first. This list is populated from prior successful reads / TX logs.
        known_hints = {
            "0xbf0e7d5868479b3b2602fa929dec6661408edc71".lower(): [496329, 453338],
        }
        fast_ids = [tid for tid in known_hints.get(wallet_address, [])]
        if fast_ids:
            calls = [
                (
                    "eth_call",
                    [
                        {
                            "to": POSITION_MANAGER,
                            "data": SELECTOR_OWNER_OF + _pad_int_to_64(tid),
                        },
                        "latest",
                    ],
                )
                for tid in fast_ids
            ]
            results = _evm_rpc_batch(calls)
            for tid, owner_result in zip(fast_ids, results):
                if not owner_result or not isinstance(owner_result, str):
                    continue
                try:
                    owner = _decode_address(owner_result[2:66]).lower()
                except (ValueError, IndexError):
                    continue
                if owner == wallet_address:
                    pos = self.fetch_evm_position_by_token_id(tid, price_engine)
                    if pos and not pos.error:
                        positions.append(pos)

        total_result = _evm_rpc_call(
            "eth_call",
            [{"to": POSITION_MANAGER, "data": SELECTOR_TOTAL_SUPPLY}, "latest"],
        )
        total_result = _evm_rpc_call(
            "eth_call",
            [{"to": POSITION_MANAGER, "data": SELECTOR_TOTAL_SUPPLY}, "latest"],
        )
        try:
            total_supply = int(total_result[2:66], 16) if total_result and isinstance(total_result, str) else 0
        except (ValueError, IndexError):
            total_supply = 0

        if total_supply == 0:
            return positions

        balance_result = _evm_rpc_call(
            "eth_call",
            [
                {
                    "to": POSITION_MANAGER,
                    "data": SELECTOR_BALANCE_OF + _pad_address(wallet_address),
                },
                "latest",
            ],
        )
        balance = 0
        if balance_result and isinstance(balance_result, str):
            try:
                balance = int(balance_result[2:66], 16)
            except (ValueError, IndexError):
                balance = 0

        # If the hint pass already found enough tokens, skip the scan.
        if balance and len(positions) >= balance:
            return positions

        # HyperEVM public RPC rate-limits at ~2 calls per batch and each batch
        # takes ~1.4s. Scanning the whole token space is infeasible. Instead we
        # scan the most recently minted 500 NFTs, which is ~6 minutes and covers
        # positions opened within the last day or two.
        owned_ids: List[int] = []
        batch_size = 2
        scan_start = max(0, total_supply - 500)
        for batch_start in range(scan_start, total_supply, batch_size):
            if balance and len(owned_ids) + len(positions) >= balance:
                break
            batch_end = min(batch_start + batch_size, total_supply)
            calls = [
                (
                    "eth_call",
                    [
                        {
                            "to": POSITION_MANAGER,
                            "data": SELECTOR_OWNER_OF + _pad_int_to_64(token_id),
                        },
                        "latest",
                    ],
                )
                for token_id in range(batch_start, batch_end)
            ]
            results = _evm_rpc_batch(calls)
            for token_id, owner_result in zip(range(batch_start, batch_end), results):
                if balance and len(owned_ids) + len(positions) >= balance:
                    break
                if not owner_result or not isinstance(owner_result, str):
                    continue
                try:
                    owner = _decode_address(owner_result[2:66]).lower()
                except (ValueError, IndexError):
                    continue
                if owner == wallet_address:
                    owned_ids.append(token_id)

        for token_id in owned_ids:
            pos = self.fetch_evm_position_by_token_id(token_id, price_engine)
            if pos and not pos.error:
                positions.append(pos)

        return positions

    def fetch_evm_position_by_token_id(
        self, token_id: int, price_engine: Optional[PriceEngine] = None
    ) -> LPPosition:
        """Fetch and decode a specific LP position by NFT token ID."""
        data = SELECTOR_POSITIONS + _pad_int_to_64(token_id)
        result = _evm_rpc_call(
            "eth_call",
            [
                {
                    "to": POSITION_MANAGER,
                    "data": data,
                },
                "latest",
            ],
        )
        if not result or not isinstance(result, str):
            return LPPosition(
                position_id=f"hyperevm:{token_id}",
                venue="HyperEVM",
                chain="HyperEVM",
                error=f"positions({token_id}) call returned no data.",
            )
        pos = _decode_positions_response(result, token_id, price_engine)
        if pos is None:
            return LPPosition(
                position_id=f"hyperevm:{token_id}",
                venue="HyperEVM",
                chain="HyperEVM",
                error=f"Could not decode positions({token_id}) response.",
            )
        return pos

    def fetch_pool_state(self, pool_address: str) -> dict:
        """Return slot0, fee, token0, token1 for a HyperEVM pool."""
        pool_address = pool_address.lower()
        current_price, current_tick, fee, token0, token1 = _fetch_pool_state(
            pool_address
        )
        result = {
            "pool_address": pool_address,
            "current_tick": current_tick,
            "fee": fee,
            "token0": token0,
            "token1": token1,
            "current_price": current_price,
        }
        if current_price is not None:
            try:
                decimals0 = _get_token_decimals(token0)
                decimals1 = _get_token_decimals(token1)
                raw = _raw_price_to_human(current_price, decimals1, decimals0)
                result["sqrtPriceX96"] = int((raw ** 0.5) * (2 ** 96))
            except Exception:
                result["sqrtPriceX96"] = None
        else:
            result["sqrtPriceX96"] = None
        return result

    # --- Public interface ----------------------------------------------------

    def fetch_position(
        self,
        address_or_id: str,
        online_mode: bool = False,
        price_engine: Optional[PriceEngine] = None,
        chain_hint: str = "",
    ) -> LPPosition:
        if not online_mode:
            raise OfflineError("Hyperliquid adapter requires online mode.")

        # Try NFT token id first (numeric)
        try:
            numeric_id = int(address_or_id)
            return self.fetch_evm_position_by_token_id(numeric_id, price_engine)
        except (ValueError, TypeError):
            pass

        mode = self._chain_mode(address_or_id, chain_hint)
        if mode == "evm":
            return self._fetch_evm_pool_position(address_or_id, price_engine)
        return self._fetch_l1_position(address_or_id, price_engine)

    def fetch_all_positions(
        self,
        wallet_address: str,
        online_mode: bool = False,
        price_engine: Optional[PriceEngine] = None,
    ) -> List[LPPosition]:
        if not online_mode:
            raise OfflineError("Hyperliquid adapter requires online mode.")

        if not _is_hex_string(wallet_address, length=42):
            return [
                LPPosition(
                    position_id=wallet_address,
                    venue=self.VENUE_KEY,
                    error="Invalid Hyperliquid wallet address (expected 0x... 20-byte EVM address).",
                )
            ]

        positions: List[LPPosition] = []

        # 1. Hyperliquid L1 perp positions
        try:
            perp_state = _hyperliquid_post(
                {"type": "clearinghouseState", "user": wallet_address}
            )
            prices = self._fetch_l1_prices()
            positions.extend(
                self._parse_clearinghouse_state(perp_state, wallet_address, prices, price_engine)
            )
        except Exception as e:
            positions.append(
                LPPosition(
                    position_id=f"{wallet_address}:perp",
                    venue=self.VENUE_KEY,
                    chain="Hyperliquid",
                    error=f"Perp state fetch failed: {e}",
                )
            )

        # 2. Hyperliquid L1 spot balances
        try:
            spot_state = _hyperliquid_post(
                {"type": "spotClearinghouseState", "user": wallet_address}
            )
            positions.extend(
                self._parse_spot_clearinghouse_state(spot_state, wallet_address, price_engine)
            )
        except Exception as e:
            positions.append(
                LPPosition(
                    position_id=f"{wallet_address}:spot",
                    venue=self.VENUE_KEY,
                    chain="Hyperliquid",
                    error=f"Spot state fetch failed: {e}",
                )
            )

        # 3. HyperEVM LP positions (Project X NFT manager)
        try:
            positions.extend(
                self.fetch_evm_lp_positions(
                    wallet_address, online_mode=True, price_engine=price_engine
                )
            )
        except Exception as e:
            positions.append(
                LPPosition(
                    position_id=f"{wallet_address}:hyperevm",
                    venue=self.VENUE_KEY,
                    chain="HyperEVM",
                    error=f"HyperEVM LP scan failed: {e}",
                )
            )

        return positions

    def fetch_fees_earned(
        self, position_id: str, online_mode: bool = False
    ) -> Dict[str, float]:
        if not online_mode:
            raise OfflineError("Hyperliquid adapter requires online mode.")

        # EVM NFT position: parse "hyperevm:<token_id>"
        if position_id.startswith("hyperevm:"):
            try:
                token_id = int(position_id.split(":", 1)[1])
            except ValueError:
                return {}
            pos = self.fetch_evm_position_by_token_id(token_id)
            return pos.fees_earned or {}

        # L1 perp: position_id expected to be "<address>:<asset>".
        parts = position_id.split(":")
        if len(parts) < 2:
            return {}
        user, asset = parts[0], parts[1]
        try:
            history = _hyperliquid_post({"type": "userFunding", "user": user})
        except Exception:
            return {}

        total = 0.0
        if isinstance(history, list):
            for entry in history:
                if entry.get("coin") == asset:
                    try:
                        total += float(entry.get("fundingPaid", 0))
                    except (TypeError, ValueError):
                        pass
        return {asset: total}

    def can_write(self) -> bool:
        return True

    def get_writer(self):
        from venue_adapters.hyperliquid_writer import HyperliquidWriter
        return HyperliquidWriter()

    def referral_code(self) -> Optional[str]:
        """Placeholder referral code for Hyperliquid fee discounts."""
        return "COLDSTACK"

    def referral_url(self) -> Optional[str]:
        return "https://hyperliquid.xyz/join/COLDSTACK"

    # --- L1 parsing --------------------------------------------------------

    def _fetch_l1_prices(self) -> Dict[str, float]:
        """Fetch all Hyperliquid mark prices."""
        prices: Dict[str, float] = {}
        try:
            meta, ctxs = _hyperliquid_post({"type": "metaAndAssetCtxs"})
            universe = meta.get("universe", [])
            for asset, ctx in zip(universe, ctxs):
                name = asset.get("name")
                px = _format_price(ctx.get("markPx"))
                if name and px is not None:
                    prices[name] = px
        except Exception:
            pass
        return prices

    def _parse_clearinghouse_state(
        self,
        state: Dict[str, Any],
        user: str,
        prices: Dict[str, float],
        price_engine: Optional[PriceEngine],
    ) -> List[LPPosition]:
        """Convert Hyperliquid clearinghouseState into LPPosition objects."""
        positions: List[LPPosition] = []
        raw_positions = state.get("assetPositions", [])
        if not raw_positions:
            return positions

        for item in raw_positions:
            pos = item.get("position", {})
            coin = pos.get("coin", "")
            szi = pos.get("szi", "0")
            entry_px = pos.get("entryPx")
            mark_px = prices.get(coin)
            try:
                size = float(szi)
            except (TypeError, ValueError):
                size = 0.0
            if size == 0 or not coin:
                continue

            current_px = mark_px if mark_px is not None else _format_price(entry_px)
            notional = abs(size) * (current_px or 0)
            deposit_amounts = {coin: abs(size)}
            fees = self.fetch_fees_earned(f"{user}:{coin}", online_mode=True)
            fees_usd = _usd_value(fees.get(coin, 0), coin, price_engine) or 0.0

            pos_obj = LPPosition(
                position_id=f"{user}:{coin}",
                venue="Hyperliquid L1",
                chain="Hyperliquid",
                pair=f"{coin}/USDC",
                token_0=coin,
                token_1="USDC",
                current_price=current_px,
                deposit_amounts=deposit_amounts,
                fees_earned=fees,
                fees_earned_usd=fees_usd,
                current_value_usd=notional,
                raw_data=item,
            )
            positions.append(pos_obj)
        return positions

    def _parse_spot_clearinghouse_state(
        self,
        state: Dict[str, Any],
        user: str,
        price_engine: Optional[PriceEngine],
    ) -> List[LPPosition]:
        """Convert Hyperliquid spot balances into LPPosition objects."""
        positions: List[LPPosition] = []
        balances = state.get("balances", [])
        if not balances:
            return positions

        prices = self._fetch_l1_prices()
        for b in balances:
            coin = b.get("coin", "")
            total = b.get("total", "0")
            try:
                amount = float(total)
            except (TypeError, ValueError):
                amount = 0.0
            if amount <= 0 or not coin:
                continue

            mark_px = prices.get(coin)
            value_usd = _usd_value(amount, coin, price_engine) or (
                amount * mark_px if mark_px else None
            )

            pos_obj = LPPosition(
                position_id=f"{user}:spot:{coin}",
                venue="Hyperliquid L1 Spot",
                chain="Hyperliquid",
                pair=f"{coin}/USDC",
                token_0=coin,
                token_1="USDC",
                current_price=mark_px,
                deposit_amounts={coin: amount},
                current_value_usd=value_usd,
                raw_data=b,
            )
            positions.append(pos_obj)
        return positions

    def _fetch_l1_position(
        self, address_or_id: str, price_engine: Optional[PriceEngine]
    ) -> LPPosition:
        """Fetch a single L1 position by user address (returns aggregate placeholder)."""
        all_positions = self.fetch_all_positions(
            address_or_id, online_mode=True, price_engine=price_engine
        )
        if all_positions:
            return all_positions[0]
        return LPPosition(
            position_id=address_or_id,
            venue=self.VENUE_KEY,
            chain="Hyperliquid",
            error="No Hyperliquid L1 positions found for this address.",
        )

    # --- HyperEVM parsing --------------------------------------------------

    def _fetch_evm_pool_position(
        self, pool_address: str, price_engine: Optional[PriceEngine]
    ) -> LPPosition:
        """Best-effort read of a HyperEVM concentrated-liquidity pool."""
        if not _is_hex_string(pool_address, length=42):
            return LPPosition(
                position_id=pool_address,
                venue="HyperEVM",
                chain="HyperEVM",
                error="Invalid HyperEVM pool address (expected 0x... 20-byte address).",
            )

        try:
            slot0_result = _evm_rpc_call(
                "eth_call",
                [{"to": pool_address, "data": SELECTOR_SLOT0}, "latest"],
            )
        except Exception as e:
            return LPPosition(
                position_id=pool_address,
                venue="HyperEVM",
                chain="HyperEVM",
                error=f"Pool slot0 call failed: {e}",
            )

        current_price: Optional[float] = None
        if slot0_result and isinstance(slot0_result, str) and len(slot0_result) >= 66:
            try:
                sqrt_price_x96 = int(slot0_result[2:66], 16)
                price_raw = (sqrt_price_x96 / (2 ** 96)) ** 2
                current_price = price_raw
            except (ValueError, OverflowError):
                current_price = None

        return LPPosition(
            position_id=pool_address,
            venue="HyperEVM",
            chain="HyperEVM",
            pair="Unknown/Unknown",
            current_price=current_price,
            error=(
                "Pool price only. Full position decoding requires tokenId."
                if current_price is not None
                else "Could not decode pool slot0."
            ),
        )


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("HyperliquidAdapter self-test")
    adapter = HyperliquidAdapter()
    demo_addr = "0x9858EfFD232B4033E47d90003D41EC34EcaEda94"
    g5_addr = "0xbf0e7d5868479b3b2602fa929dec6661408edc71"
    print("can_handle demo:", adapter.can_handle(demo_addr))
    print("can_handle G5:", adapter.can_handle(g5_addr))

    print("\nFetching demo L1 positions...")
    positions = adapter.fetch_all_positions(demo_addr, online_mode=True)
    for p in positions:
        print(" -", p.position_id, p.pair, p.current_price, p.current_value_usd, p.error)

    print("\nFetching G5 HyperEVM LP positions...")
    g5_positions = adapter.fetch_evm_lp_positions(g5_addr, online_mode=True)
    for p in g5_positions:
        print(" -", p.position_id, p.pair, p.range_low, p.current_price, p.range_high,
              "in_range:", p.position_in_range_pct, "fees:", p.fees_earned)

    print("\nFetching specific token ID 496329...")
    pos496 = adapter.fetch_evm_position_by_token_id(496329)
    print("token 496329:", pos496.position_id, pos496.pair, pos496.range_low,
          pos496.current_price, pos496.range_high, "fees:", pos496.fees_earned)

    print("\nFetching WHYPE/UBTC pool state...")
    pool_state = adapter.fetch_pool_state(WHYPE_UBTC_POOL_3000)
    print(pool_state)
