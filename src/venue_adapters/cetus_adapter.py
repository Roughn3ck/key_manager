"""ColdStack v5.3.20 - Cetus (Sui) CLMM read-only venue adapter.

Fetches Cetus CLMM ``Position`` + ``Pool`` objects from Sui JSON-RPC using the
object-content JSON (``sui_getObject`` with ``showContent``) -- no hand-rolled
BCS layout is required, so it tolerates Cetus package/struct revisions. Decodes
the Q64.64 sqrt-price, tick range, liquidity, holdings, uncollected fees and
USD values; the range % / holdings math mirrors the Orca adapter (same CLMM
family, Q64.64).

Standalone module by design (Kris's architecture rule): the only central-file
touch is venue registration (``venue_adapters/__init__.py``), the Sui chain
entry in ``lp_tab._lp_get_chain_info``/``_lp_resolve_venue_for_position`` and
``LP_PLATFORM_MAP``.

Scope: READ-ONLY. Cetus writes land in Phase 2 (see ``cetus_writer.py``).
"""
from __future__ import annotations

import json
import math
import os
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

from lp_engine import LPPosition, OfflineError, VenueAdapter, register_adapter
from price_engine import PriceEngine
from sui_assets import DEFAULT_SUI_RPC, get_coin_metadata, load_registry, parse_coin_type
from sui_ptb import sui_int, sui_i32

# Cetus Move position type. Verified on-chain 2026-09-26 (sui-rpc.publicnode.com):
# the N1 wallet holds exactly one object of this type. Used as the suix_getOwnedObjects
# StructType filter; the suffix check below stays as a guard for package upgrades.
CETUS_POSITION_TYPE = (
    "0x1eabed72c53feb3805120a081dc15963c204dc8d091542592abaf7a35689b2fb::position::Position"
)
CETUS_POSITION_TYPE_SUFFIX = "::position::Position"
CETUS_POOL_TYPE_SUFFIX = "::pool::Pool"

_MAX_OWNED_PAGES = 10
_OWNED_PAGE_SIZE = 50


def _rpc_urls() -> List[str]:
    """RPC URLs to try in order (env override, then the shared Sui default)."""
    urls = []
    env = os.environ.get("SUI_RPC_URL")
    if env:
        urls.append(env)
    urls.append(DEFAULT_SUI_RPC)
    return list(dict.fromkeys(urls))


def _sui_rpc(url: str, method: str, params: list, timeout: int = 20) -> Any:
    payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode("utf-8")
    req = urllib.request.Request(
        url, data=payload,
        headers={"Content-Type": "application/json", "User-Agent": "ColdStack/5.3.20"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if not isinstance(data, dict) or "error" in data:
        err = data.get("error") if isinstance(data, dict) else data
        raise RuntimeError(f"{method}: {json.dumps(err)[:200]}")
    return data.get("result")


def _get_object_json(object_id: str) -> Tuple[str, Dict[str, Any]]:
    """Fetch an object's (type, fields) via JSON content. Raises on failure."""
    last_err: Optional[Exception] = None
    for url in _rpc_urls():
        try:
            result = _sui_rpc(url, "sui_getObject", [object_id, {"showType": True, "showContent": True}])
            data = (result or {}).get("data") or {}
            content = data.get("content") or {}
            fields = content.get("fields") or {}
            return (data.get("type") or content.get("type") or ""), fields
        except Exception as e:  # try the next endpoint
            last_err = e
            continue
    raise RuntimeError(f"sui_getObject({object_id}) failed: {last_err}")


def _get_owned_objects(owner: str) -> List[Dict[str, Any]]:
    """Return owned Cetus-position objects (type + fields), paged.

    Params are the canonical ``suix_getOwnedObjects(owner, query, cursor, limit)``
    shape with a ``StructType`` filter on the exact Cetus position type. (An
    earlier version put ``limit`` inside the query object and applied no filter,
    which the node rejects -- the scan found nothing.)
    """
    out: List[Dict[str, Any]] = []
    cursor: Optional[str] = None
    for _ in range(_MAX_OWNED_PAGES):
        query: Dict[str, Any] = {
            "filter": {"StructType": CETUS_POSITION_TYPE},
            "options": {"showType": True, "showContent": True},
        }
        params: List[Any] = [owner, query, cursor, _OWNED_PAGE_SIZE]
        result = _sui_rpc(_rpc_urls()[0], "suix_getOwnedObjects", params)
        page = (result or {}).get("data") or []
        for item in page:
            d = item.get("data", item) if isinstance(item, dict) else {}
            if not isinstance(d, dict):
                continue
            content = d.get("content") or {}
            out.append({
                "object_id": d.get("objectId"),
                "type": d.get("type") or content.get("type") or "",
                "fields": content.get("fields") or {},
            })
        if not (result or {}).get("hasNextPage"):
            break
        cursor = (result or {}).get("nextCursor")
        if not cursor:
            break
    return out


def _typename_str(value: Any) -> str:
    """Extract the string from a Sui ``TypeName`` (JSON ``{"name": ...}``)."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        if isinstance(value.get("name"), str):
            return value["name"]
        inner = value.get("fields")
        if isinstance(inner, dict):
            return _typename_str(inner)
        if "name" in value:
            return _typename_str(value["name"])
    return ""


def _symbol_decimals(coin_type: str) -> Tuple[str, int]:
    _, _, parsed = parse_coin_type(coin_type)
    meta = get_coin_metadata(coin_type)
    symbol = meta.get("symbol") or parsed or "?"
    try:
        decimals = int(meta.get("decimals", 9))
    except (TypeError, ValueError):
        decimals = 9
    return symbol, decimals


def _tick_to_price(tick: int, dec_a: int, dec_b: int) -> float:
    """Cetus CLMM tick -> human price (token B per token A). Matches Orca."""
    return (1.0001 ** tick) * (10 ** (dec_a - dec_b))


def _compute_holdings(liquidity: int, sqrt_price_x64: int, tick_lower: int,
                      tick_upper: int, tick_current: int,
                      dec_a: int, dec_b: int) -> Tuple[float, float]:
    """Token A/B holdings from liquidity (Uniswap-V3 math, Q64.64 sqrt price)."""
    if not liquidity:
        return 0.0, 0.0
    sqrt_p = sqrt_price_x64 / (2 ** 64)
    sqrt_lower = 1.0001 ** (tick_lower / 2.0)
    sqrt_upper = 1.0001 ** (tick_upper / 2.0)
    if sqrt_p <= 0 or sqrt_upper <= sqrt_lower:
        return 0.0, 0.0
    if tick_current < tick_lower:
        amount_a = liquidity * (sqrt_upper - sqrt_lower) / (sqrt_lower * sqrt_upper)
        amount_b = 0.0
    elif tick_current >= tick_upper:
        amount_a = 0.0
        amount_b = liquidity * (sqrt_upper - sqrt_lower)
    else:
        amount_a = liquidity * (sqrt_upper - sqrt_p) / (sqrt_p * sqrt_upper)
        amount_b = liquidity * (sqrt_p - sqrt_lower)
    return amount_a / (10 ** dec_a), amount_b / (10 ** dec_b)


def _price_as_symbol(symbol: str, registry: Optional[Dict[str, Any]]) -> str:
    """Return the pricing symbol for ``symbol`` from the Sui token registry.

    The registry's ``price_as`` table maps Sui token symbols to PriceEngine-known
    symbols (e.g. ``LBTC -> BTC``). Defaults to the input symbol.
    """
    if not registry:
        return symbol
    mapping = registry.get("price_as") if isinstance(registry, dict) else None
    if isinstance(mapping, dict):
        return mapping.get(symbol.upper(), symbol)
    return symbol


def _usd(amount: Optional[float], symbol: str, price_engine: Optional[PriceEngine],
         registry: Optional[Dict[str, Any]] = None) -> Optional[float]:
    if amount is None or amount <= 0 or not price_engine:
        return None
    price_symbol = _price_as_symbol(symbol, registry)
    try:
        return price_engine.convert_balance_to_fiat(amount, price_symbol, currency="usd")
    except Exception:
        return None


def _is_cetus_position_type(type_str: str) -> bool:
    return CETUS_POSITION_TYPE_SUFFIX in (type_str or "")


def _is_cetus_pool_type(type_str: str) -> bool:
    return CETUS_POOL_TYPE_SUFFIX in (type_str or "")


def _is_sui_object_id(value: str) -> bool:
    """True for a full Sui object id / address (``0x`` + 64 hex)."""
    s = (value or "").strip()
    if s.startswith("sui:"):
        s = s.split(":", 1)[1]
    if not s.startswith("0x"):
        return False
    h = s[2:]
    return len(h) == 64 and all(c in "0123456789abcdefABCDEF" for c in h)


def _is_numeric(value: str) -> bool:
    """True for a bare decimal integer (e.g. a Cetus registry pool id)."""
    s = (value or "").strip()
    return bool(s) and s.isdigit()


def decode_position(fields: Dict[str, Any]) -> Dict[str, Any]:
    """Decode a Cetus Position object's JSON fields into a normalized dict."""
    pool_id = fields.get("pool")
    if isinstance(pool_id, dict):
        pool_id = _typename_str(pool_id) or pool_id.get("id")
    coin_a = _typename_str(fields.get("coin_type_a"))
    coin_b = _typename_str(fields.get("coin_type_b"))
    return {
        "pool": pool_id,
        "liquidity": sui_int(fields.get("liquidity")) or 0,
        "tick_lower": sui_i32(fields.get("tick_lower_index")),
        "tick_upper": sui_i32(fields.get("tick_upper_index")),
        "coin_type_a": coin_a,
        "coin_type_b": coin_b,
        "fee_owed_a": sui_int(fields.get("fee_owed_a")) or 0,
        "fee_owed_b": sui_int(fields.get("fee_owed_b")) or 0,
    }


def decode_pool(fields: Dict[str, Any]) -> Dict[str, Any]:
    """Decode a Cetus CLPool object's JSON fields into a normalized dict.

    Coin types live in the pool *type* generics, not the fields — symbols come
    from the position's coin_type_a/b; only price/tick geometry is read here.
    """
    tick = fields.get("current_tick_index", fields.get("current_tick"))
    return {
        "current_sqrt_price": sui_int(fields.get("current_sqrt_price")),
        "tick_current": sui_i32(tick),
        "tick_spacing": sui_int(fields.get("tick_spacing")),
    }


@register_adapter
class CetusAdapter(VenueAdapter):
    """Cetus CLMM read-only adapter for LP position discovery on Sui."""

    VENUE_KEY = "cetus"
    CHAINS = ["sui"]

    # -- VenueAdapter interface ---------------------------------------------

    def can_handle(self, address_or_id: str, chain_hint: str = "") -> bool:
        """True for a ``sui:``-prefixed id, a Sui hint, or a full Sui object id."""
        value = (address_or_id or "").strip()
        hint = (chain_hint or "").lower()
        if value.startswith("sui:"):
            return True
        if "sui" in hint or "cetus" in hint:
            return bool(value)
        # A bare 0x+64-hex id/address (EVM addresses are 0x+40, so no clash).
        return _is_sui_object_id(value)

    def fetch_position(
        self,
        address_or_id: str,
        online_mode: bool = False,
        price_engine: Optional[PriceEngine] = None,
        chain_hint: str = "",
        wallet_address: str = "",
    ) -> LPPosition:
        """Fetch a single Cetus position by its Sui position object id."""
        if not online_mode:
            raise OfflineError("Cetus adapter requires online mode.")

        raw_id = (address_or_id or "").strip()
        obj_id = raw_id.split(":", 1)[1] if raw_id.startswith("sui:") else raw_id
        if _is_numeric(obj_id):
            return LPPosition(
                position_id=f"sui:{obj_id}", venue="Cetus", chain="Sui",
                error=(
                    f"{obj_id} is a Cetus registry pool id, not an on-chain object id — "
                    "paste the position or pool object id (0x + 64 hex)."
                ),
            )
        if not _is_sui_object_id(obj_id):
            return LPPosition(
                position_id=f"sui:{obj_id}", venue="Cetus", chain="Sui",
                error="Not a valid Sui object id (expected 0x + 64 hex characters).",
            )
        try:
            type_str, fields = _get_object_json(obj_id)
        except Exception as e:
            return LPPosition(
                position_id=f"sui:{obj_id}", venue="Cetus", chain="Sui",
                error=f"Sui RPC unavailable — retry ({e})",
            )
        if _is_cetus_pool_type(type_str):
            return LPPosition(
                position_id=f"sui:{obj_id}", venue="Cetus", chain="Sui",
                error=(
                    "That's a Cetus POOL object — paste a POSITION object id "
                    "(also 0x + 64 hex; positions are NFT objects in the wallet)."
                ),
            )
        if not _is_cetus_position_type(type_str):
            return LPPosition(
                position_id=f"sui:{obj_id}", venue="Cetus", chain="Sui",
                error=f"Object is not a Cetus position ({type_str or 'unknown type'}).",
            )
        return self._position_from_fields(obj_id, fields, price_engine, wallet_address)

    def fetch_all_positions(
        self,
        wallet_address: str,
        online_mode: bool = False,
        price_engine: Optional[PriceEngine] = None,
    ) -> List[LPPosition]:
        """Scan a Sui wallet for every Cetus position object it owns."""
        if not online_mode:
            raise OfflineError("Cetus adapter requires online mode.")
        wallet = (wallet_address or "").strip()
        if wallet.startswith("sui:"):
            wallet = wallet.split(":", 1)[1]
        if not _is_sui_object_id(wallet):
            return []
        try:
            owned = _get_owned_objects(wallet)
        except Exception as e:
            print(f"[cetus-scan] owned-object fetch failed: {e}")
            return []
        positions: List[LPPosition] = []
        for item in owned:
            if not _is_cetus_position_type(item.get("type", "")):
                continue
            obj_id = item.get("object_id")
            if not obj_id:
                continue
            pos = self._position_from_fields(obj_id, item.get("fields") or {}, price_engine, wallet)
            if pos and not pos.error:
                if pos.raw_data and pos.raw_data.get("liquidity", 0) == 0 and not pos.fees_earned:
                    continue  # skip empty/closed positions on scans
                positions.append(pos)
        print(f"[cetus-scan] found {len(positions)} Cetus position(s) for {wallet[:12]}...")
        return positions

    def fetch_fees_earned(self, position_id: str, online_mode: bool = False) -> Dict[str, float]:
        """Return uncollected fees (amount + token_0/token_1) for a position."""
        if not online_mode:
            raise OfflineError("Cetus adapter requires online mode.")
        obj_id = position_id.split(":", 1)[1] if position_id.startswith("sui:") else position_id
        try:
            type_str, fields = _get_object_json(obj_id)
        except Exception:
            return {}
        if not _is_cetus_position_type(type_str):
            return {}
        pos = decode_position(fields)
        sym_a, dec_a = _symbol_decimals(pos["coin_type_a"])
        sym_b, dec_b = _symbol_decimals(pos["coin_type_b"])
        return {
            sym_a: pos["fee_owed_a"] / (10 ** dec_a),
            sym_b: pos["fee_owed_b"] / (10 ** dec_b),
        }

    # -- Internal ------------------------------------------------------------

    def _position_from_fields(self, obj_id: str, fields: Dict[str, Any],
                              price_engine: Optional[PriceEngine],
                              wallet_address: str = "") -> LPPosition:
        pos = decode_position(fields)
        if not pos.get("coin_type_a") or not pos.get("coin_type_b"):
            return LPPosition(
                position_id=f"sui:{obj_id}", venue="Cetus", chain="Sui",
                error="Could not decode Cetus position coin types.",
            )

        # v5.3.21: load the Sui token registry once per position for price_as mappings.
        registry = load_registry()

        sym_a, dec_a = _symbol_decimals(pos["coin_type_a"])
        sym_b, dec_b = _symbol_decimals(pos["coin_type_b"])
        pair = f"{sym_a}/{sym_b}"

        tick_lower = pos["tick_lower"]
        tick_upper = pos["tick_upper"]

        pool: Dict[str, Any] = {}
        if pos.get("pool"):
            try:
                _ptype, pfields = _get_object_json(pos["pool"])
                pool = decode_pool(pfields)
            except Exception:
                pool = {}

        current_price = None
        range_low = None
        range_high = None
        in_range_pct = None
        deposit_amounts: Dict[str, float] = {}
        fees_earned: Dict[str, float] = {}

        if tick_lower is not None:
            range_low = _tick_to_price(tick_lower, dec_a, dec_b)
        if tick_upper is not None:
            range_high = _tick_to_price(tick_upper, dec_a, dec_b)

        tick_current = pool.get("tick_current")
        sqrt_price = pool.get("current_sqrt_price")
        if tick_current is not None:
            current_price = _tick_to_price(tick_current, dec_a, dec_b)
        if current_price is None and sqrt_price is not None:
            sqrt_p = sqrt_price / (2 ** 64)
            current_price = (sqrt_p ** 2) * (10 ** (dec_a - dec_b))

        # In-range %: linear in SQRT price, matching the sentinel's rangePct
        # (lp-sentinel/kp_monitor.js buildPosition). Decimal-independent.
        if tick_lower is not None and tick_upper is not None and tick_upper != tick_lower:
            sqrt_lo = 1.0001 ** (tick_lower / 2.0)
            sqrt_hi = 1.0001 ** (tick_upper / 2.0)
            sqrt_now = (sqrt_price / (2 ** 64)) if sqrt_price is not None else (
                1.0001 ** (tick_current / 2.0) if tick_current is not None else None)
            if sqrt_now is not None and sqrt_hi > sqrt_lo:
                in_range_pct = max(0.0, min(100.0, (sqrt_now - sqrt_lo) / (sqrt_hi - sqrt_lo) * 100.0))

        if sqrt_price is not None and tick_lower is not None and tick_upper is not None \
                and tick_current is not None:
            amt_a, amt_b = _compute_holdings(
                pos["liquidity"], sqrt_price, tick_lower, tick_upper, tick_current, dec_a, dec_b)
            if amt_a > 0:
                deposit_amounts[sym_a] = amt_a
            if amt_b > 0:
                deposit_amounts[sym_b] = amt_b

        fee_a = pos["fee_owed_a"] / (10 ** dec_a)
        fee_b = pos["fee_owed_b"] / (10 ** dec_b)
        if fee_a > 0:
            fees_earned[sym_a] = fee_a
        if fee_b > 0:
            fees_earned[sym_b] = fee_b

        fees_usd = 0.0
        unpriced_fees: List[str] = []
        for amt, sym in ((fee_a, sym_a), (fee_b, sym_b)):
            val = _usd(amt, sym, price_engine, registry=registry)
            if val is None and amt > 0:
                unpriced_fees.append(sym)
            fees_usd += val or 0.0
        fees_earned_usd = fees_usd if fees_usd > 0 else (0.0 if fees_earned else None)

        current_value_usd = None
        unpriced_value: List[str] = []
        for amt, sym in ((deposit_amounts.get(sym_a, 0.0), sym_a),
                         (deposit_amounts.get(sym_b, 0.0), sym_b)):
            val = _usd(amt, sym, price_engine, registry=registry)
            if val is None and amt > 0:
                unpriced_value.append(sym)
            current_value_usd = (current_value_usd or 0.0) + (val or 0.0)
        if current_value_usd == 0.0:
            current_value_usd = None

        all_unpriced = sorted(set(unpriced_fees + unpriced_value))
        notes = []
        if pool == {} and pos.get("pool"):
            notes.append("pool state unavailable")
        if all_unpriced:
            leg_word = "leg" if len(all_unpriced) == 1 else "legs"
            notes.append(
                f"⚠ unpriced: {', '.join(all_unpriced)} "
                f"({len(all_unpriced)} {leg_word} unpriced; USD totals are partial)"
            )

        return LPPosition(
            position_id=f"sui:{obj_id}",
            pool_id=pos.get("pool"),
            venue="Cetus",
            chain="Sui",
            pair=pair,
            token_0=sym_a,
            token_1=sym_b,
            current_price=current_price,
            range_low=range_low,
            range_high=range_high,
            position_in_range_pct=in_range_pct,
            deposit_amounts=deposit_amounts,
            fees_earned=fees_earned,
            fees_earned_usd=fees_earned_usd,
            fees_note=" · ".join(notes) or None,
            deposit_value_usd=current_value_usd,
            current_value_usd=current_value_usd,
            raw_data={
                "position_object_id": obj_id,
                "pool": pos.get("pool"),
                "liquidity": pos["liquidity"],
                "tick_lower": tick_lower,
                "tick_upper": tick_upper,
                "tick_current": tick_current,
                "current_sqrt_price": sqrt_price,
                "decimals0": dec_a,
                "decimals1": dec_b,
                "coin_type_a": pos["coin_type_a"],
                "coin_type_b": pos["coin_type_b"],
                "fee_owed_a": pos["fee_owed_a"],
                "fee_owed_b": pos["fee_owed_b"],
                "unpriced_symbols": all_unpriced,
                "unpriced_count": len(all_unpriced),
                "price_as_registry": registry.get("price_as", {}),
            },
        )

    # -- Write support -------------------------------------------------------

    def can_write(self) -> bool:
        """v5.3.21: Cetus PTB writer is available."""
        return True

    def get_writer(self):
        """Return a CetusWriter instance."""
        from venue_adapters.cetus_writer import CetusWriter
        return CetusWriter()
