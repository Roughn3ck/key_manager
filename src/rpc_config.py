"""
RPC Config Loader - Reads and manages customizable RPC endpoint configuration.

Loads rpc_endpoints.json from the application's runtime directory (next to the
EXE or in the project root). Falls back to hardcoded defaults if the file is
missing or malformed.

The JSON file contains ONLY public RPC URLs - no API keys or secrets.
API keys are stored in the encrypted vault and injected at runtime.

Version: v4.2 (July 2026)
"""
import json
import os
import sys
from datetime import date
from typing import Dict, Optional, Any

# v5.3.9: self-heal map for deprecated endpoints. Evidence-based only —
# one live-verified dead URL per entry. When a loaded config (user file,
# bundled file, or the fallback path) contains a deprecated URL, the loader
# substitutes the replacement, warns, and rewrites the source file in place
# so the fix persists. See _selfheal_deprecated_endpoints.
DEPRECATED_ENDPOINTS: Dict[str, str] = {
    # Official Sui fullnodes: JSON-RPC deprecated (live-verified 2026-09-11)
    "https://fullnode.mainnet.sui.io": "https://sui-rpc.publicnode.com",
}


# Hardcoded default endpoints - used when rpc_endpoints.json is missing/malformed.
# This mirrors the content of rpc_endpoints.json exactly.
DEFAULT_ENDPOINTS: Dict[str, Dict[str, Any]] = {
    "ethereum": {
        "url": "https://ethereum-rpc.publicnode.com",
        "auth": None,
        "fallback": "https://eth.drpc.org",
    },
    "arbitrum": {
        "url": "https://arb1.arbitrum.io/rpc",
        "auth": None,
        "fallback": "https://arbitrum-one-rpc.publicnode.com",
    },
    "base": {
        "url": "https://mainnet.base.org",
        "auth": None,
        "fallback": "https://base-rpc.publicnode.com",
    },
    "bsc": {
        "url": "https://bsc-dataseed.binance.org/",
        "auth": None,
        "fallback": "https://bsc-dataseed1.binance.org",
    },
    "polygon": {
        "url": "https://polygon-bor-rpc.publicnode.com",
        "auth": None,
        "fallback": "https://polygon.drpc.org",
    },
    "optimism": {
        "url": "https://mainnet.optimism.io",
        "auth": None,
        "fallback": "https://optimism-rpc.publicnode.com",
    },
    "hyperliquid_evm": {
        "url": "https://rpc.hyperliquid.xyz/evm",
        "auth": None,
        "fallback": None,
    },
    "bitcoin": {
        "url": "https://blockstream.info/api/address/{address}",
        "auth": None,
        "fallback": "https://mempool.space/api/address/{address}",
    },
    "solana": {
        "url": "https://api.mainnet-beta.solana.com",
        "auth": "helius",
        "fallback": "https://solana-api.projectserum.com",
    },
    "dash": {
        "url": "https://insight.dash.org/insight-api/addr/{address}",
        "auth": None,
        "fallback": None,
    },
    "sui": {
        "url": "https://sui-rpc.publicnode.com",
        "auth": None,
        "fallback": "https://sui.blockpi.network/v1/rpc/public",
    },
    "hyperliquid_l1": {
        "url": "https://api.hyperliquid.xyz/info",
        "auth": None,
        "fallback": None,
    },
    "zcash": {
        "url": "https://api.blockchair.com/zcash/dashboards/address/{address}",
        "auth": None,
        "fallback": None,
    },
    "ripple": {
        "url": "https://s1.ripple.com:51234",
        "auth": None,
        "fallback": "https://s2.ripple.com:51234",
    },
    "cardano": {
        "url": "https://api.koios.rest/api/v1/address_info",
        "auth": None,
        "fallback": None,
    },
    "cosmos": {
        "url": "https://rest.lavenderfive.com:443/cosmoshub/cosmos/bank/v1beta1/balances/{address}",
        "auth": None,
        "fallback": None,
    },
    "secret": {
        "url": "https://rest.lavenderfive.com:443/secretnetwork/cosmos/bank/v1beta1/balances/{address}",
        "auth": None,
        "fallback": None,
    },
    "thorchain": {
        "url": "https://thornode.thorchain.ninja/cosmos/bank/v1beta1/balances/{address}",
        "auth": None,
        "fallback": None,
    },
}


def _get_runtime_dir() -> str:
    """Get the directory where rpc_endpoints.json should be located.

    In frozen (EXE) mode: next to the EXE.
    In script mode: project root (parent of src/).
    """
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    else:
        return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _get_bundled_dir() -> str:
    """Get the directory of the bundled default rpc_endpoints.json.

    In frozen mode: sys._MEIPASS (PyInstaller temp extraction dir).
    In script mode: project root.
    """
    if getattr(sys, 'frozen', False):
        return getattr(sys, '_MEIPASS', os.path.dirname(sys.executable))
    else:
        return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_rpc_config(base_dir: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
    """Load RPC endpoint configuration from rpc_endpoints.json.

    Search order:
    1. User-customized file in runtime dir (next to EXE or project root)
    2. Bundled default (from PyInstaller _MEIPASS or project root)
    3. Hardcoded DEFAULT_ENDPOINTS constant (final fallback)

    v5.3.9: whatever config is chosen is swept for deprecated endpoints
    (see DEPRECATED_ENDPOINTS). File-based configs are rewritten in place
    when repairs occur so the fix persists across restarts.

    Args:
        base_dir: Override the runtime directory. If None, auto-detected.

    Returns:
        Dict of {chain_id: {url, auth, fallback}} for all configured chains.
    """
    runtime_dir = base_dir or _get_runtime_dir()
    bundled_dir = _get_bundled_dir()

    # Try user-customized file first (in runtime directory)
    user_path = os.path.join(runtime_dir, "rpc_endpoints.json")
    config = _try_load_file(user_path, "user")
    if config is not None:
        return _selfheal_deprecated_endpoints(config, user_path)

    # Try bundled default (PyInstaller _MEIPASS or project root)
    bundled_path = os.path.join(bundled_dir, "rpc_endpoints.json")
    if bundled_path != user_path:
        config = _try_load_file(bundled_path, "bundled")
        if config is not None:
            return _selfheal_deprecated_endpoints(config, bundled_path)

    # Final fallback: hardcoded defaults
    print("RPC config: using hardcoded defaults (no rpc_endpoints.json found)")
    return _selfheal_deprecated_endpoints(DEFAULT_ENDPOINTS.copy(), None)


def _selfheal_deprecated_endpoints(
    config: Dict[str, Dict[str, Any]],
    source_path: Optional[str],
) -> Dict[str, Dict[str, Any]]:
    """Replace deprecated endpoints in a loaded config and persist the fix.

    v5.3.9: stale deployed rpc_endpoints.json files silently shadow fixed
    code defaults (a Sep-3 file pointed SUI at the deprecated official
    fullnode, producing "Could not fetch SUI balance" despite corrected
    code defaults). This sweeps every chain's url and fallback against
    DEPRECATED_ENDPOINTS; on any match it substitutes the replacement and,
    when the config came from a file, rewrites ONLY the affected entries
    back to disk (bumping the "updated" field). Never raises — a config
    rewrite failure keeps the in-memory fix.

    Args:
        config: The loaded endpoint dict (mutated in place).
        source_path: The JSON file the config was loaded from, or None for
                     hardcoded defaults (no rewrite possible).

    Returns:
        The (possibly repaired) config dict.
    """
    changed: Dict[str, Dict[str, str]] = {}
    for chain_id, entry in config.items():
        if not isinstance(entry, dict):
            continue
        for field in ("url", "fallback"):
            old = entry.get(field)
            if old and old in DEPRECATED_ENDPOINTS:
                new = DEPRECATED_ENDPOINTS[old]
                entry[field] = new
                changed.setdefault(chain_id, {})[field] = new
                print(f"RPC config: deprecated endpoint for {chain_id} replaced ({old} -> {new})")
    if not changed:
        return config
    if source_path is None:
        print("RPC config: deprecated endpoints repaired in defaults (in-memory; no file to rewrite)")
        return config
    try:
        with open(source_path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        endpoints = raw.get("endpoints", raw)
        for chain_id, fields in changed.items():
            if chain_id in endpoints and isinstance(endpoints[chain_id], dict):
                for field, new in fields.items():
                    endpoints[chain_id][field] = new
        raw["updated"] = str(date.today())
        with open(source_path, "w", encoding="utf-8") as f:
            json.dump(raw, f, indent=2, ensure_ascii=False)
        print(f"RPC config: self-heal written to {source_path}")
    except Exception as e:
        # Never crash over config — the in-memory fix is already applied.
        print(f"RPC config: could not rewrite {source_path} after self-heal ({e}); in-memory fix kept")
    return config


def _try_load_file(path: str, source: str) -> Optional[Dict[str, Dict[str, Any]]]:
    """Attempt to load and validate an rpc_endpoints.json file.

    Args:
        path: File path to load.
        source: Description of source ("user" or "bundled") for logging.

    Returns:
        Endpoint dict if valid, None if file missing or malformed.
    """
    if not os.path.exists(path):
        return None

    try:
        with open(path, 'r', encoding='utf-8') as f:
            raw = json.load(f)

        endpoints = raw.get("endpoints", raw)

        # Validate structure: each entry should have at least a "url" key
        validated: Dict[str, Dict[str, Any]] = {}
        for chain_id, entry in endpoints.items():
            if not isinstance(entry, dict):
                continue
            url = entry.get("url")
            if not url:
                continue
            validated[chain_id] = {
                "url": url,
                "auth": entry.get("auth"),
                "fallback": entry.get("fallback"),
            }

        if not validated:
            print(f"RPC config ({source}): file at {path} has no valid endpoints, using defaults")
            return None

        # Merge with defaults: any chain missing from the file gets the default
        for chain_id, default_entry in DEFAULT_ENDPOINTS.items():
            if chain_id not in validated:
                validated[chain_id] = default_entry.copy()

        print(f"RPC config ({source}): loaded {len(validated)} endpoints from {path}")
        return validated

    except json.JSONDecodeError as e:
        print(f"RPC config ({source}): malformed JSON in {path}: {e}, using defaults")
        return None
    except Exception as e:
        print(f"RPC config ({source}): error loading {path}: {e}, using defaults")
        return None


def save_rpc_config(endpoints: Dict[str, Dict[str, Any]], base_dir: Optional[str] = None) -> bool:
    """Save RPC endpoint configuration to rpc_endpoints.json.

    Only public URLs are written. API keys are never written to this file.

    Args:
        endpoints: Dict of {chain_id: {url, auth, fallback}} to save.
        base_dir: Override the runtime directory. If None, auto-detected.

    Returns:
        True if saved successfully, False on error.
    """
    runtime_dir = base_dir or _get_runtime_dir()
    path = os.path.join(runtime_dir, "rpc_endpoints.json")

    try:
        data = {
            "version": 1,
            "updated": str(date.today()),
            "endpoints": endpoints,
        }
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        print(f"RPC config: saved {len(endpoints)} endpoints to {path}")
        return True
    except Exception as e:
        print(f"RPC config: error saving to {path}: {e}")
        return False


def get_default_endpoints() -> Dict[str, Dict[str, Any]]:
    """Return a copy of the hardcoded default endpoints."""
    return DEFAULT_ENDPOINTS.copy()


def get_default_for_chain(chain_id: str) -> Optional[Dict[str, Any]]:
    """Get the default endpoint config for a single chain.

    Args:
        chain_id: Chain identifier (e.g., "ethereum", "bitcoin").

    Returns:
        Dict with {url, auth, fallback} or None if chain not in defaults.
    """
    entry = DEFAULT_ENDPOINTS.get(chain_id)
    return entry.copy() if entry else None