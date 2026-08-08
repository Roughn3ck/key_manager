# Forge Prompt: ColdStack v5.2.1 — Rename Krystal → BSC

## Context

The "Krystal" adapter/writer are misnamed. The code makes zero Krystal API calls — it talks directly to BSC RPC and queries both Uniswap V3 and PancakeSwap V3 Position Manager contracts on BNB Chain. Since it supports both DEXs, the correct name is `bsc_adapter.py` / `BSCAdapter` / `bsc_writer.py` / `BSCWriter`.

This is a pure rename — no logic changes. Every reference to "Krystal" or "krystal" in the codebase needs to be updated to "BSC" or "bsc" respectively.

**Do NOT rebuild the EXE, update README.md, update STATUS.md, or push to git.**

---

## Rename Plan

### Files to Rename

1. `src/venue_adapters/krystal_adapter.py` → `src/venue_adapters/bsc_adapter.py`
2. `src/venue_adapters/krystal_writer.py` → `src/venue_adapters/bsc_writer.py`

### Files to Edit

#### `src/venue_adapters/bsc_adapter.py` (renamed from krystal_adapter.py)

- Module docstring: Remove all Krystal references. Update to:
  ```python
  """BSC Venue Adapter - ColdStack LP Engine v5.2

  Read-only adapter for BNB Chain (BSC) V3 LP positions.

  Queries both Uniswap V3 and PancakeSwap V3 Position Manager contracts
  on BSC directly via JSON-RPC. No third-party API — all reads are direct
  on-chain calls using stdlib urllib.request only.

  Read-only. Stateless. Offline by default.

  Version: v5.2.1 (August 2026) - Uniswap V3 + PancakeSwap V3 on BSC
  """
  ```
- Class name: `KrystalAdapter` → `BSCAdapter`
- `VENUE_KEY = "***"` → `VENUE_KEY = "bsc"`
- All docstrings referencing Krystal → BSC
- All print statements: `[krystal-value]` → `[bsc-value]`, `[krystal-scan]` → `[bsc-scan]`
- Comment: `# Krystal wraps multiple DEXes` → `# BSC has multiple V3 DEXes`
- `venue="Krystal"` in LPPosition → `venue="BSC"`
- Error messages referencing Krystal → BSC
- `can_write` docstring: "Writer support is available in v5.2.1" (remove Krystal reference)
- `get_writer` method: import from `venue_adapters.bsc_writer` instead of `venue_adapters.krystal_writer`, return `BSCWriter()`
- Class docstring: `"""BSC adapter for V3 LP position reads on BNB Chain."""`

#### `src/venue_adapters/bsc_writer.py` (renamed from krystal_writer.py)

- Module docstring: Remove Krystal references. Update to:
  ```python
  """BSC Venue Writer - ColdStack LP Engine v5.2.1

  Write operations for BSC V3 LP positions (Uniswap V3 + PancakeSwap V3 on BNB Chain).
  All signing is delegated to the key_manager_agent via HTTP — the writer
  never touches private keys directly.

  Version: v5.2.1 (August 2026) - collect_fees + close_position (minimal)
  """
  ```
- Class name: `KrystalWriter` → `BSCWriter`
- `VENUE_KEY = "***"` → `VENUE_KEY = "bsc"`
- Import: `from venue_adapters.krystal_adapter import` → `from venue_adapters.bsc_adapter import`
- All print statements: `[krystal-writer]` → `[bsc-writer]`
- All docstrings referencing Krystal → BSC

#### `src/venue_adapters/__init__.py`

- `from .krystal_adapter import KrystalAdapter` → `from .bsc_adapter import BSCAdapter`
- Update any version comments mentioning Krystal

#### `src/lp_tab.py`

All references to Krystal/krystal throughout the file:
- `from venue_adapters.krystal_adapter import KrystalAdapter` → `from venue_adapters.bsc_adapter import BSCAdapter`
- `KrystalAdapter()` → `BSCAdapter()`
- `krystal_adapter` variable names → `bsc_adapter`
- `venue in ("Krystal", "krystal")` → `venue in ("BSC", "bsc")`
- `_lp_get_chain_info`: `"krystal"` → `"bsc"` as the venue_key for BSC positions
- `get_position_tracking(..., "Krystal")` → `get_position_tracking(..., "BSC")`
- `is_pool_saved(..., "Krystal")` checks → use `"BSC"` (but NOTE: existing saved pools in the vault may have `venue="Krystal"` — see migration note below)
- Platform label: `"Platform: Krystal (BSC)"` → `"Platform: BSC (BNB Chain)"`
- `adapter_key in ("krystal", "bsc")` → just `"bsc"` (and map `"krystal"` to `"bsc"` for backward compat in saved pools)
- `LP_PLATFORM_MAP_reverse.get(adapter_key, venue_key)` — the reverse map will now map `"bsc"` to `"BSC (BNB Chain)"`

#### `src/gui_main_v5.py`

- `LP_PLATFORM_MAP`:
  ```python
  LP_PLATFORM_MAP = {
      "HyperEVM (Project X)": "hyperliquid",
      "BSC (BNB Chain)": "bsc",
  }
  ```
  Change `"Krystal (BSC)": "krystal"` to `"BSC (BNB Chain)": "bsc"`
- Status bar text: `"v5.2.1 - ColdStack | Krystal Skeleton + Light/Dark Mode"` → `"v5.2.1 - ColdStack | BSC V3 + Light/Dark Mode"`

#### `src/lp_engine.py`

- No direct Krystal references, but verify `discover_adapters()` still works after the rename (it imports `venue_adapters` package which will now import `bsc_adapter`)

#### `src/build_gui_v5.py`

- `--hidden-import=venue_adapters.krystal_writer` → `--hidden-import=venue_adapters.bsc_writer`
- `--hidden-import=venue_adapters.krystal_adapter` (if present) → `--hidden-import=venue_adapters.bsc_adapter`
- Version string: `"Krystal Skeleton + Light/Dark Mode"` → `"BSC V3 + Light/Dark Mode"`
- Print statement: `"Krystal Skeleton + Light/Dark Mode"` → `"BSC V3 + Light/Dark Mode"`

#### `AGENTS.md` (project root)

- `krystal_adapter.py (read skeleton: BSC / PancakeSwap V3 via direct RPC reads, multichain)` → `bsc_adapter.py (read: BSC Uniswap V3 + PancakeSwap V3 via direct RPC reads)`
- Add `bsc_writer.py (write: collect/close for BSC V3 positions, signs via agent on :8842)` to the module list

---

## Saved Pools Migration (Backward Compatibility)

Existing saved pools in the encrypted vault may have `venue="Krystal"` stored from before the rename. The code needs to handle this gracefully:

**In `src/lp_tab.py`**, wherever saved pools are loaded and the venue is checked, add a normalization:

```python
# Normalize legacy venue names
if venue in ("Krystal", "krystal"):
    venue = "BSC"
```

This should be applied in:
- `_lp_fetch_saved_only` — when reading `entry.get("venue", ...)`
- `_lp_do_full_scan` / `_lp_do_filtered_full_scan` — in the saved pool merge loop
- `_lp_auto_fetch_all_saved` — when reading venue from saved entries
- `_lp_render_all_saved_placeholders` — when determining the prefix
- `_lp_render_saved_placeholders` — same
- `_lp_save_pool` — when checking `is_pool_saved` (normalize the venue before lookup)
- `_lp_remove_pool` — same
- `_lp_fetch_saved_single` — when mapping venue to adapter key
- `_lp_initialize_tracking` — when calling `is_pool_saved` and `get_position_tracking`

**In `src/saved_pools.py`**, add a normalization in `load_saved_pools`:

```python
def load_saved_pools(address_db, wallet_address=None):
    pools = address_db.get("saved_pools", [])
    if not isinstance(pools, list):
        return []
    # Normalize legacy venue names (Krystal → BSC)
    for entry in pools:
        if isinstance(entry, dict) and entry.get("venue") in ("Krystal", "krystal"):
            entry["venue"] = "BSC"
    if wallet_address:
        wallet_lower = wallet_address.lower()
        pools = [entry for entry in pools if ...]
    return pools
```

This ensures old saved pools with `venue="Krystal"` are automatically treated as `venue="BSC"` without requiring a vault migration.

---

## Summary

| Old | New |
|-----|-----|
| `krystal_adapter.py` | `bsc_adapter.py` |
| `krystal_writer.py` | `bsc_writer.py` |
| `KrystalAdapter` | `BSCAdapter` |
| `KrystalWriter` | `BSCWriter` |
| `VENUE_KEY = "krystal"` | `VENUE_KEY = "bsc"` |
| `venue = "Krystal"` | `venue = "BSC"` |
| `"Krystal (BSC)"` in dropdown | `"BSC (BNB Chain)"` |
| All `[krystal-*]` print logs | `[bsc-*]` |
| `get_writer("krystal")` calls | `get_writer("bsc")` |

## Verification

1. `python -m py_compile src/venue_adapters/bsc_adapter.py`
2. `python -m py_compile src/venue_adapters/bsc_writer.py`
3. `python -m py_compile src/venue_adapters/__init__.py`
4. `python -m py_compile src/lp_tab.py`
5. `python -m py_compile src/gui_main_v5.py`
6. `python -m py_compile src/build_gui_v5.py`
7. `python -m py_compile src/saved_pools.py`

Verify the adapter still registers:
```python
python3 -c "
import sys; sys.path.insert(0, 'src')
from lp_engine import list_venues
print(list_venues())
"
```
Expected: `['bsc', 'hyperliquid']` (sorted)

Do NOT rebuild the EXE. Do NOT push to git. Do NOT update README.md or STATUS.md.