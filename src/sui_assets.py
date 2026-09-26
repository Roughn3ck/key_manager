"""ColdStack v5.3.20 - Sui asset auto-detection (standalone, read-only).

Sui holds are enumerated EXHAUSTIVELY via ``suix_getAllBalances`` -- every coin
in the address (SUI, LBTC, DEEP, ...), not a registry-driven subset. Symbol and
decimals come from ``suix_getCoinMetadata`` (cached), with a small extendable
registry at ``src/sui_tokens.json`` for overrides (and to fix tokens whose
on-chain metadata is missing). That registry IS the manual "manage tokens" path
for Sui: edit the JSON to add/pin a token.

Integration surface: the balance engine's ``sui`` chain entry calls
``fetch_sui_assets()`` and falls back to the SUI-only ``fetch_sui_balance`` on
failure. This module makes no on-chain writes and never raises out of
``fetch_sui_assets`` (returns ``[]`` on any RPC error).
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

DEFAULT_SUI_RPC = "https://sui-rpc.publicnode.com"
_REGISTRY_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sui_tokens.json")

# symbol/decimals keyed by coin type (metadata cache; 5-minute TTL not needed --
# coin metadata is immutable in practice, so cache for the process lifetime).
_META_CACHE: Dict[str, Dict[str, Any]] = {}


def _rpc(url: str, method: str, params: list, timeout: int = 12) -> Any:
    """POST a Sui JSON-RPC call. Raises RuntimeError on any JSON-RPC error."""
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


def load_registry(path: Optional[str] = None) -> Dict[str, Any]:
    """Load ``src/sui_tokens.json`` (or a caller-supplied path). Never raises."""
    p = path or _REGISTRY_PATH
    try:
        with open(p, "r", encoding="utf-8") as f:
            reg = json.load(f)
        if isinstance(reg, dict) and isinstance(reg.get("coins"), dict):
            return reg
    except Exception:
        pass
    return {"coins": {}}


def parse_coin_type(coin_type: str) -> tuple:
    """``0x<addr>::<module>::<SYMBOL>`` -> ``(address, module, symbol)``.

    Tolerant of short/absent segments (returns "" for missing pieces).
    """
    parts = (coin_type or "").split("::")
    address = parts[0] if len(parts) > 0 else ""
    module = parts[1] if len(parts) > 1 else ""
    symbol = parts[2] if len(parts) > 2 else (module or coin_type or "")
    return address, module, symbol


def get_all_balances(address: str, url: Optional[str] = None) -> List[Dict[str, Any]]:
    """Return ``[{"coin_type", "total_balance"}]`` for every held coin.

    ``suix_getAllBalances`` is exhaustive. Raw integer (MIST / base units).
    Raises on RPC error so the caller can try a fallback URL.
    """
    url = url or DEFAULT_SUI_RPC
    result = _rpc(url, "suix_getAllBalances", [address])
    if not isinstance(result, list):
        return []
    out: List[Dict[str, Any]] = []
    for entry in result:
        if not isinstance(entry, dict):
            continue
        coin_type = entry.get("coinType")
        total = entry.get("totalBalance")
        if coin_type is None or total is None:
            continue
        try:
            out.append({"coin_type": coin_type, "total_balance": int(total)})
        except (TypeError, ValueError):
            continue
    return out


def get_coin_metadata(coin_type: str, url: Optional[str] = None) -> Dict[str, Any]:
    """Return ``{"symbol","decimals","name"}`` for a coin type (cached).

    Returns ``{}`` when the RPC has no metadata (e.g. unregistered coin).
    """
    if coin_type in _META_CACHE:
        return _META_CACHE[coin_type]
    url = url or DEFAULT_SUI_RPC
    meta: Dict[str, Any] = {}
    try:
        result = _rpc(url, "suix_getCoinMetadata", [coin_type])
        if isinstance(result, dict):
            meta = {
                "symbol": result.get("symbol") or "",
                "decimals": int(result.get("decimals", 9)),
                "name": result.get("name") or "",
            }
    except Exception:
        meta = {}
    _META_CACHE[coin_type] = meta
    return meta


def resolve_symbol_decimals(coin_type: str, registry: Optional[Dict[str, Any]] = None,
                            url: Optional[str] = None) -> tuple:
    """Resolve ``(symbol, decimals)`` for a coin type.

    Precedence: registry override > on-chain metadata > parsed coin-type symbol
    > module name. Decimals default to 9 (Sui's native scale) when unknown.
    """
    reg = (registry or {}).get("coins", {})
    entry = reg.get(coin_type) if isinstance(reg, dict) else {}
    entry = entry if isinstance(entry, dict) else {}
    _, module, parsed = parse_coin_type(coin_type)
    meta = {} if entry else get_coin_metadata(coin_type, url=url)
    symbol = entry.get("symbol") or meta.get("symbol") or parsed or module or coin_type
    try:
        decimals = int(entry.get("decimals", meta.get("decimals", 9)))
    except (TypeError, ValueError):
        decimals = 9
    return symbol, decimals


def fetch_sui_assets(address: str, rpc_url: Optional[str] = None,
                     fallback_url: Optional[str] = None,
                     registry: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Return every non-zero held Sui coin as a balance-engine entry.

    Each entry: ``{"chain":"sui","balance":float,"symbol":str,"decimals":int,
    "coin_type":str,"type":"coin"}``. SUI is listed first, then the rest
    alphabetically. Returns ``[]`` (never raises) on any RPC failure so the
    caller can fall back to the native SUI-only balance path.
    """
    address = (address or "").strip()
    if not address:
        return []
    reg = registry if registry is not None else load_registry()

    urls = [u for u in (rpc_url, fallback_url, DEFAULT_SUI_RPC) if u]
    raw: List[Dict[str, Any]] = []
    used_url: Optional[str] = None
    for u in dict.fromkeys(urls):
        try:
            raw = get_all_balances(address, url=u)
            used_url = u
            if raw:
                break
        except Exception:
            continue
    if not raw:
        return []

    assets: List[Dict[str, Any]] = []
    for entry in raw:
        if entry["total_balance"] <= 0:
            continue
        symbol, decimals = resolve_symbol_decimals(entry["coin_type"], registry=reg, url=used_url)
        assets.append({
            "chain": "sui",
            "balance": entry["total_balance"] / (10 ** decimals),
            "symbol": symbol,
            "decimals": decimals,
            "coin_type": entry["coin_type"],
            "type": "coin",
        })
    assets.sort(key=lambda a: (0 if a["symbol"].upper() == "SUI" else 1, a["symbol"].upper()))
    return assets
