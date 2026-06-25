"""
Hyperliquid Venue Adapter - ColdStack LP Engine v5.0

Read-only adapter for Hyperliquid venues:
  - Hyperliquid L1 (api.hyperliquid.xyz/info) perp/spot positions
  - HyperEVM (rpc.hyperliquid.xyz/evm) concentrated-liquidity style pools

Uses only stdlib urllib.request (no axios, no web3.py).

Read-only. Stateless. Offline by default.

Version: v5.0 (June 2026)
"""
import json
import urllib.request
import urllib.error
from typing import Any, Dict, List, Optional

from lp_engine import LPPosition, OfflineError, VenueAdapter, register_adapter
from price_engine import PriceEngine


HYPERLIQUID_INFO_URL = "https://api.hyperliquid.xyz/info"
HYPEREVM_RPC_URL = "https://rpc.hyperliquid.xyz/evm"

# Common Uniswap V3 style function selectors (keccak256 first 4 bytes)
# These are used as a best-effort decoder for HyperEVM CLMM contracts.
SELECTOR_SLOT0 = "0x3850c7bd"
SELECTOR_POSITIONS = "0x99fbab88"


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
            "User-Agent": "ColdStack/5.0",
        },
    )
    with urllib.request.urlopen(req, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def _evm_rpc_call(method: str, params: list, request_id: int = 1) -> Optional[Any]:
    """Make a JSON-RPC call to HyperEVM and return the 'result' field."""
    payload = json.dumps(
        {"jsonrpc": "2.0", "method": method, "params": params, "id": request_id}
    ).encode("utf-8")
    req = urllib.request.Request(
        HYPEREVM_RPC_URL,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "ColdStack/5.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            data = json.loads(response.read().decode("utf-8"))
            return data.get("result")
    except (urllib.error.URLError, json.JSONDecodeError, KeyError, Exception):
        return None


def _pad_int_to_64(value: int) -> str:
    """Pad an integer to 32-byte (64 hex char) ABI encoding."""
    return format(value, "064x")


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
        - The adapter does NOT handle Chainflip vault IDs (cFHs...) or Sui-style
          64-byte addresses; those belong to future adapters.
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

        # 2. Hyperliquid L1 spot balances (treat large spot positions as LP-like holdings)
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

        return positions

    def fetch_fees_earned(
        self, position_id: str, online_mode: bool = False
    ) -> Dict[str, float]:
        if not online_mode:
            raise OfflineError("Hyperliquid adapter requires online mode.")

        # position_id is expected to be "<address>:<asset>" for perp positions.
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
                # Funding paid/received approximates fees for perp LP-like exposure.
                if entry.get("coin") == asset:
                    try:
                        total += float(entry.get("fundingPaid", 0))
                    except (TypeError, ValueError):
                        pass
        return {asset: total}

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

            side = "Long" if size > 0 else "Short"
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
        """Best-effort read of a HyperEVM concentrated-liquidity pool.

        Uses slot0 to get current price. A full position decode requires a
        tokenId + positions() call; this first version reports the pool price
        and leaves range/fees as unknown unless a tokenId is provided later.
        """
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
                # Uniswap V3 price ratio = (sqrtPriceX96 / 2**96)**2
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
    print("can_handle demo:", adapter.can_handle(demo_addr))
    print("can_handle G5 pool:", adapter.can_handle("0x35dc8b9bc6f3b49bb7578951134e032ccb5dcf2dee72ce34f9a1fa34c46d8742"))

    print("\nFetching demo L1 positions...")
    positions = adapter.fetch_all_positions(demo_addr, online_mode=True)
    for p in positions:
        print(" -", p.position_id, p.pair, p.current_price, p.current_value_usd, p.error)

    print("\nFetching demo EVM pool price...")
    # Use a known Uniswap V3 pool address on Ethereum as a decode sanity check.
    eth_pool = "0x8ad599c3A0ff1De082011EFDDc58f1908eb6e6D8"  # USDC/ETH 0.05%
    print("can_handle eth pool:", adapter.can_handle(eth_pool))
    evm_pos = adapter.fetch_position(eth_pool, online_mode=True, chain_hint="hyperevm")
    print("EVM pool price:", evm_pos.current_price, evm_pos.error)
