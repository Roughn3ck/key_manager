"""
Saved Pools - Persistent pool registry for ColdStack LP Engine v5.1.

Stores public identifiers (wallet address, token ID, venue, pool address, pair)
inside the encrypted vault payload (address_db["saved_pools"]). No private keys.

Used by the LP tab to auto-load saved positions by token ID, avoiding the
expensive wallet scan on every refresh.

Solana/Orca: token_id holds the base58 position mint string (not an integer).

Version: v5.2.5 (August 2026) - Solana string token_id support
"""
import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Union


def load_saved_pools(address_db: Dict[str, Any], wallet_address: Optional[str] = None) -> List[Dict[str, Any]]:
    """Load saved pools from the decrypted vault's address_db.

    Args:
        address_db: The in-memory decrypted vault data dict.
        wallet_address: If provided, filter results to this wallet only.

    Returns:
        List of saved pool entries. Empty list if none found.
    """
    pools = address_db.get("saved_pools", [])
    if not isinstance(pools, list):
        return []
    # Normalize legacy venue names (Krystal → BSC)
    for entry in pools:
        if isinstance(entry, dict) and entry.get("venue") in ("Krystal", "krystal"):
            entry["venue"] = "BSC"
    if wallet_address:
        wallet_lower = wallet_address.lower()
        pools = [
            entry for entry in pools
            if isinstance(entry, dict)
            and entry.get("wallet_address", "").lower() == wallet_lower
        ]
    return pools


def save_pool(
    address_db: Dict[str, Any],
    wallet_address: str,
    token_id: Union[int, str],
    venue: str,
    pool_address: Optional[str],
    pair: str,
) -> bool:
    """Add or update a saved pool entry in the decrypted address_db.

    Deduplicates by ``(venue, token_id)``. Only stores public identifiers.
    The caller must re-encrypt the vault via KeyManager.save_encrypted_data(password)
    after calling this function to persist the change to key_vault.encrypted.

    Returns True on success, False on error.
    """
    try:
        pools = address_db.setdefault("saved_pools", [])
        if not isinstance(pools, list):
            pools = []
            address_db["saved_pools"] = pools
        # Check for duplicate (venue:token_id)
        for entry in pools:
            if (
                isinstance(entry, dict)
                and entry.get("venue") == venue
                and entry.get("token_id") == token_id
            ):
                # Already saved — update wallet/pair in case they changed
                entry["wallet_address"] = wallet_address
                entry["pair"] = pair
                entry["pool_address"] = pool_address or ""
                entry["date_saved"] = datetime.now(timezone.utc).isoformat()
                return True
        pools.append({
            "wallet_address": wallet_address,
            "token_id": token_id,
            "venue": venue,
            "pool_address": pool_address or "",
            "pair": pair,
            "date_saved": datetime.now(timezone.utc).isoformat(),
        })
        return True
    except Exception:
        return False


def remove_saved_pool(address_db: Dict[str, Any], token_id: Union[int, str], venue: str) -> bool:
    """Remove a saved pool entry by venue + token_id.

    The caller must re-encrypt the vault to persist this change.

    Returns True if removed, False if not found or error.
    """
    pools = address_db.get("saved_pools", [])
    if not isinstance(pools, list):
        return False
    original_len = len(pools)
    address_db["saved_pools"] = [
        entry for entry in pools
        if not (
            isinstance(entry, dict)
            and entry.get("venue") == venue
            and entry.get("token_id") == token_id
        )
    ]
    return len(address_db["saved_pools"]) != original_len


def is_pool_saved(address_db: Dict[str, Any], token_id: Union[int, str], venue: str) -> bool:
    """Check if a pool is already saved in the decrypted address_db."""
    pools = address_db.get("saved_pools", [])
    if not isinstance(pools, list):
        return False
    return any(
        isinstance(entry, dict)
        and entry.get("venue") == venue
        and entry.get("token_id") == token_id
        for entry in pools
    )


def _find_pool_entry(address_db: Dict[str, Any], token_id: Union[int, str], venue: str) -> Optional[Dict[str, Any]]:
    """Return the saved pool entry matching venue + token_id, or None."""
    pools = address_db.get("saved_pools", [])
    if not isinstance(pools, list):
        return None
    for entry in pools:
        if (
            isinstance(entry, dict)
            and entry.get("venue") == venue
            and entry.get("token_id") == token_id
        ):
            return entry
    return None


def update_saved_pool_wallet(
    address_db: Dict[str, Any],
    token_id: Union[int, str],
    venue: str,
    new_wallet_address: str,
) -> bool:
    """Update the wallet_address for a saved pool entry.

    Used when a pool was saved with the wrong wallet address (e.g. saved
    while a different account was selected in the LP tab).

    The caller must re-encrypt the vault to persist this change.

    Returns True on success, False if pool not found or error.
    """
    try:
        entry = _find_pool_entry(address_db, token_id, venue)
        if entry is None:
            return False
        entry["wallet_address"] = new_wallet_address
        return True
    except Exception:
        return False


def update_position_tracking(
    address_db: Dict[str, Any],
    token_id: Union[int, str],
    venue: str,
    current_value_usd: Optional[float] = None,
    fees_collected_usd: Optional[float] = None,
    fee_event: Optional[Dict[str, Any]] = None,
) -> bool:
    """Update tracking fields for a saved pool.

    On first call (no existing tracking data), sets first_seen_date and
    initial_deposit_usd to the current value.

    On subsequent calls, accumulates fees_collected_usd and appends fee_event
    to fee_history.

    The caller must re-encrypt the vault to persist changes.

    Returns True on success, False on error or if no matching pool exists.
    """
    try:
        entry = _find_pool_entry(address_db, token_id, venue)
        if entry is None:
            return False

        now_iso = datetime.now(timezone.utc).isoformat()

        if "first_seen_date" not in entry or entry.get("initial_deposit_usd") is None:
            entry["first_seen_date"] = now_iso
            entry["initial_deposit_usd"] = current_value_usd or 0.0
            entry["total_fees_collected_usd"] = 0.0
            entry["last_fee_collect_date"] = None
            entry["fee_history"] = []

        if fees_collected_usd and fees_collected_usd > 0:
            entry["total_fees_collected_usd"] = entry.get("total_fees_collected_usd", 0.0) + fees_collected_usd
            entry["last_fee_collect_date"] = now_iso

        if fee_event and isinstance(fee_event, dict):
            history = entry.setdefault("fee_history", [])
            if isinstance(history, list):
                history.append(fee_event)

        return True
    except Exception:
        return False


def get_position_tracking(
    address_db: Dict[str, Any],
    token_id: Union[int, str],
    venue: str,
) -> Dict[str, Any]:
    """Get tracking data for a saved pool. Returns empty dict if not found."""
    entry = _find_pool_entry(address_db, token_id, venue)
    if entry is None:
        return {}
    return {
        k: entry[k]
        for k in (
            "first_seen_date",
            "initial_deposit_usd",
            "total_fees_collected_usd",
            "last_fee_collect_date",
            "fee_history",
        )
        if k in entry
    }


def migrate_saved_pools_json(address_db: Dict[str, Any], base_dir: str) -> bool:
    """One-time migration: import saved_pools.json into the encrypted vault.

    If saved_pools.json exists next to the vault AND address_db does not
    already have a "saved_pools" key, import the JSON entries into
    address_db["saved_pools"] and rename the JSON file to
    saved_pools.json.migrated so it's not imported again.

    Returns True if migration occurred (caller should re-save the vault),
    False otherwise.
    """
    json_path = os.path.join(base_dir, "saved_pools.json")
    if not os.path.exists(json_path):
        return False
    # Only migrate if address_db doesn't already have saved_pools
    if address_db.get("saved_pools") is not None:
        # Already has saved pools (even if empty list) — just rename the JSON file
        try:
            os.rename(json_path, json_path + ".migrated")
        except OSError:
            pass
        return False
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list) and data:
            address_db["saved_pools"] = data
            # Rename the JSON file so it's not imported again
            try:
                os.rename(json_path, json_path + ".migrated")
            except OSError:
                pass
            return True
        else:
            # Empty or invalid — just rename
            try:
                os.rename(json_path, json_path + ".migrated")
            except OSError:
                pass
    except (json.JSONDecodeError, OSError):
        pass
    return False