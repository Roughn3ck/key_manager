## v5.1 - Hyperliquid Vaults + LP Fee Fix (July 2026)

### Completed
- [x] `src/vault_tracker.py` — Read-only Hyperliquid vault positions via `/info` API (`userVaultEquities` + `vaultDetails`)
- [x] `src/gui_main_v5.py` — HL1 Vaults tab with vault cards: Vault Name heading, Vault Address, TVL, APR, Vault Age, Deposit Age, Deposited, Current Value, Unrealized P&L, Copy Address
- [x] Saved vaults stored inside encrypted `key_vault.encrypted` (`saved_vaults` top-level list), with Save/Delete buttons and cached snapshots so saved vaults render instantly on reopen
- [x] Wallet selector in HL1 Vaults tab: raw address entry or account dropdown from Wallet tab; mode and selection persisted in encrypted vault config
- [x] `src/venue_adapters/hyperliquid_adapter.py` — LP fee reading fixed: uses static `collect((uint256,address,uint128,uint128))` eth_call (selector `0xfc6f7865`) to return real uncollected fees from the Project X PositionManager; matches Project X UI tooltip
- [x] `saved_pools.py` — Saved pools CRUD (public identifiers only, no private keys), stored in `saved_pools.json` next to vault
- [x] HyperEVM ERC-20 balance support (USDC, WHYPE, UBTC), combined "Hyperliquid (HL1 & HyperEVM)" chain option, stablecoin currency conversion fix, balance dispatcher fix
- [x] README.md, CLAUDE.md, .clinerules updated for v5.1
- [x] py_compile passes for modified source files

### Data Sources
- **APR**: `vaultDetails.apr` (annualized decimal from Hyperliquid API, e.g. `-0.0052` → `-0.5%`)
- **TVL**: `vaultDetails.maxDistributable`; fallback to sum of `followers[*].vaultEquity`
- **Vault Age**: earliest timestamp in `vaultDetails.portfolio` history
- **Deposit Age**: `vaultDetails.followerState.vaultEntryTime` for the queried user

### Known Limitations
- Swap router address on HyperEVM still not confirmed — `swap()` raises `NotImplementedError`; cross-ratio rebalances deferred
- Writer requires `key_manager_agent` running with `--serve` on localhost:8842
- Saved pools still live in a separate `saved_pools.json` (to be refactored into encrypted vault in a future release)

### Next Steps
- [ ] Refactor saved pools storage from `saved_pools.json` into `key_vault.encrypted` (single-database goal)
- [ ] Research/confirm HyperEVM swap router contract address
- [ ] Test writer end-to-end with agent on mainnet
- [ ] Build EXE with `python build_gui_v5.py`
- [ ] Test EXE on clean Windows machine

---

# ColdStack - Status Report

## v4.2 - Customizable RPC Endpoints + Standard/Advanced Mode (July 2026)

### New Features
- **Customizable RPC Endpoints**: User-editable `rpc_endpoints.json` config file (co-located with EXE). Contains ONLY public RPC URLs - no API keys or secrets. Falls back to hardcoded defaults if missing/malformed.
- **Standard/Advanced Mode Toggle**: Settings dialog now has two modes. Standard (default): identical to v4.1 UX. Advanced: reveals RPC endpoint editor and API key fields. Mode persists in encrypted vault config.
- **API Key Management**: API keys (Helius, Infura, Alchemy, Quicknode) stored in encrypted vault `config.api_keys`. Never written to `rpc_endpoints.json`. Injected into URLs at runtime (Helius: hostname swap + `?api-key=KEY`).
- **Fallback URLs**: Each chain can have a fallback URL. Balance engine tries primary URL first, falls back on failure.
- **RPC Config Loader** (`src/rpc_config.py`): New module that loads/validates/saves `rpc_endpoints.json`. Merges with defaults for any missing chains.

### Files Changed
- `rpc_endpoints.json` (NEW) - Default RPC endpoint config (17 chains with url, auth, fallback)
- `src/rpc_config.py` (NEW) - RPC config loader module
- `src/balance_engine.py` (UPDATED) - Refactored for new endpoint format + API key injection + fallback URLs
- `src/gui_main_v4.py` (UPDATED) - Advanced Settings dialog, mode switching, config fields, version strings
- `build_gui_v4.py` (UPDATED) - Added `rpc_endpoints.json` as data file, `rpc_config` hidden import, version strings
- `.clinerules` (UPDATED) - v4.2 versioning table, project structure
- `STATUS.md` (UPDATED) - v4.2 changelog

### Security
- **Security boundary maintained**: `rpc_endpoints.json` contains ONLY public URLs. API keys stored in encrypted vault.
- **API keys cleared on lock**: `self.api_keys` cleared when session is locked
- **Backward compatible**: Old vaults without `schema_version`, `app_mode`, or `api_keys` work perfectly - all default to v4.1 behavior

### Backward Compatibility
- Old vaults (no `schema_version`) -> treated as v1, all features work
- New field `app_mode` defaults to "standard" if missing
- New field `api_keys` defaults to empty dict if missing
- `rpc_endpoints.json` missing -> fall back to hardcoded defaults (current behavior)
- `rpc_endpoints.json` malformed -> log warning, use hardcoded defaults
- NEVER break old vaults. Additive changes only.

### Testing
- Syntax checks pass for all files (rpc_config.py, balance_engine.py, gui_main_v4.py, build_gui_v4.py)
- EXE build includes `rpc_endpoints.json` as bundled data file
- Settings persistence verified: API keys and app_mode persist in encrypted vault between sessions
- EVM chain progress display: label updates in real-time as each chain is queried
- HYPE (Hyperliquid) optimized: only queries HyperEVM + L1 (2 calls) instead of all EVM chains (14+)
- Standard/Advanced mode toggle works: mode persists, Advanced shows RPC editor + API key fields
- Save button properly saves to vault and closes Settings dialog
- Backward compatibility: old v4.1 vaults open without migration, all defaults work

### Chain Routing
- `EVM (Ethereum / Arbitrum / Base)` -> fetch_all_evm_balances (all 7 EVM chains + ERC-20)
- `HYPE (Hyperliquid)` -> fetch_hype_balances (HyperEVM gas + L1 spot only, 2 calls)
- `Hyperliquid L1 (Spot)` -> fetch_hyperliquid_l1_balances (L1 spot only, 1 call)

## v4.1 - Price Feeds + Wallet Balances + Go Online Toggle (June 2026)

### New Features
- **Go Online Toggle (Settings)**: New Settings dialog with "Go Online" switch (OFF by default). When OFF: 100% offline, identical to v3.1. When ON: enables read-only balance fetching and price feeds. Clear warning dialog when toggling ON. Toggle state persists in vault config (encrypted). Account view re-renders immediately when online mode changes (button states update).
- **Inline Balance Display**: Balances shown inline on existing address cards (not a separate tab). Shows native balance (e.g., 0.5 ETH, 0.001 BTC) next to each address. Balance label hidden by default, only shown after a fetch is triggered.
- **Check Balance Button**: Per-card "Check Balance" button on each address card (between Copy and Delete). Greyed out when offline or unsupported chain. Active when online. Fetches balance for that single address only, displays inline.
- **Compact Address Card Layout**: Reduced vertical space - coin + chain + "derived" tag all on ONE line (separated by middle dot), address below, balance only when fetched, notes only when present. Card padding reduced from 5/10 to 3/6.
- **Currency Toggle**: "Set Default Currency" option in Settings - USD, AUD, CAD, EUR, CHF, or None. When set: shows fiat equivalent alongside native balance (e.g., 0.5 ETH ($1,234.56 USD)). Setting persists in vault config.
- **Balance Engine** (`src/balance_engine.py`): Fetches wallet balances via public RPC endpoints. Supports EVM (6 chains), BTC, SOL, DASH, SUI. Uses stdlib `urllib.request` - no new dependencies. All fetches run in background threads.
- **Price Engine** (`src/price_engine.py`): Fetches crypto prices via CoinGecko API (free tier, no API key). 60-second in-memory cache. No persistent storage of prices.
- **Online/Offline Indicator**: Status bar shows green "Online" or grey "Offline" indicator. Last refresh timestamp shown subtly.
- **Manual Refresh Only**: No auto-refresh or background polling. User clicks "Check Balance" per-card.

### Files Changed
- `src/balance_engine.py` (NEW) - Balance fetching engine (EVM, BTC, SOL, DASH, SUI)
- `src/price_engine.py` (NEW) - CoinGecko price feed with 60s cache
- `src/gui_main_v4.py` (NEW) - v4.0 GUI with Settings dialog, inline balances, Go Online toggle
- `build_gui_v4.py` (NEW) - v4.0 PyInstaller build script
- `key_manager_gui_v4.spec` (NEW) - v4.0 PyInstaller spec
- `.clinerules` (UPDATED) - v4.0 versioning table, new files in structure
- `README.md` (UPDATED) - v4.0 features, changelog
- `STATUS.md` (UPDATED) - v4.0 changelog
- Backups created: `backups/gui_main_v3_1.py`, `backups/build_gui_v3_1.py`, `backups/coldstack_v3_1.exe`, etc.

### Security
- **Offline by default**: Online mode is OFF until user explicitly enables it
- **Only public addresses queried**: Private keys and mnemonics NEVER leave the vault
- **No auto-refresh**: All balance/price fetches are manual (user clicks "Refresh")
- **No price storage**: Prices cached in-memory only (60s TTL), never written to disk
- **Config encrypted**: Online mode and currency settings stored in the encrypted vault

### Testing
- Syntax checks pass for all new files (gui_main_v4.py, balance_engine.py, price_engine.py)
- EXE built successfully: `USB_DEPLOYMENT/coldstack.exe` (44.50 MB) - rebuilt with all fixes
- Offline mode: balance labels hidden (no "-" placeholder), Check Balance buttons greyed out, no network requests, no crashes
- Online mode: Check Balance buttons active, clicking shows "Fetching..." then balance inline
- Compact cards verified: coin + chain + derived on one line, reduced vertical space
- Toggle re-render: switching online/offline immediately updates button states on all cards
- Vault config backward-compatible: old vaults without "config" key default to offline

## v3.1 - ColdStack Rebrand + Check for Updates (June 2026)

- GUI rebranded to "ColdStack" (class `ColdStackGUI`)
- "Check for Updates" button in status bar (user-initiated, threaded)
- GUI EXE renamed to `coldstack.exe`
- CLI EXE deprecated and removed from USB_DEPLOYMENT
- Vault co-located with application (not in `~/.key_manager/`)

## v3.0 - BIP39 Mnemonic Derivation Engine (June 2026)

- DerivationEngine: 7 chains (EVM, BTC Taproot/SegWit/Legacy, Solana, Dash, Sui)
- GUI derivation dialogs, enhanced private key display
- CLI commands: `derive-address`, `generate-mnemonic`, `validate-mnemonic`

**Project Location:** `B:\Github\key_manager\`
**Last Updated:** 2026-07-04
**Current Version:** v4.2 (Price Feeds + Wallet Balances + Go Online Toggle)

## Versioning

| Version | Description | Key Files |
|---------|-------------|-----------|
| v1 | Original GUI - display-only | `src/gui_main.py`, `build_gui.py` |
| v2 | GUI Add functionality | `src/gui_main_v2.py`, `build_gui_v2.py` |
| v3 | GUI BIP39 derivation | `src/gui_main_v3.py`, `build_gui_v3.py`, `src/derivation_engine.py` |
| v3.1 | ColdStack rebrand + Check for Updates | `src/gui_main_v3_1.py`, `build_gui_v3_1.py` |
| v4.1 | Price Feeds + Wallet Balances + Go Online | `src/gui_main_v4.py`, `build_gui_v4.py`, `src/balance_engine.py`, `src/price_engine.py` |
| v4.2 | Customizable RPC + Standard/Advanced Mode | `src/rpc_config.py`, `rpc_endpoints.json`, `src/balance_engine.py` (updated), `src/gui_main_v4.py` (updated) |

## Architecture

```
key_manager/
├── src/
│   ├── gui_main_v4.py         - v4.1 GUI: Settings + inline balances + Go Online
│   ├── gui_main_v3_1.py       - v3.1 GUI (preserved)
│   ├── balance_engine.py      - v4.1: Fetch wallet balances via public RPC
│   ├── price_engine.py        - v4.1: Fetch crypto prices via CoinGecko
│   ├── crypto_engine.py       - AES-256-GCM + Argon2id (shared, unchanged)
│   ├── derivation_engine.py   - BIP39 derivation (shared, unchanged)
│   └── main.py                - CLI interface (shared, script-mode only)
├── build_gui_v4.py            - v4.0 PyInstaller build script
├── key_manager_gui_v4.spec    - v4.0 PyInstaller spec
├── USB_DEPLOYMENT/
│   └── coldstack.exe          - v4.0 GUI build
└── README.md
```

## Component Status

| Component | Status | Notes |
|-----------|--------|-------|
| Crypto Engine | Working | AES-256-GCM + Argon2id (unchanged) |
| Derivation Engine | Working | 7 chains (unchanged) |
| Balance Engine | Working v4.1 | EVM (6+ chains), BTC, SOL, DASH, SUI, HyperEVM ERC-20, Hyperliquid L1 spot |
| Price Engine | Working v4.1 | CoinGecko API, 60s cache |
| Vault Tracker | Working v5.1 | Hyperliquid L1 vault positions; cached saved vaults |
| LP Engine / Adapter | Working v5.1 | HyperEVM + L1 positions; fee reading via static `collect()` eth_call |
| Hyperliquid Writer | Working v5.0 | Signs via agent on localhost:8842 |
| GUI v5.1 Source | Working | Wallet / HL1 Vaults / LP Positions tabs |
| GUI EXE | Needs rebuild | Run `python build_gui_v5.py` |
| CLI (script mode) | Working | `python src/main.py` (CLI EXE deprecated) |
| Headless Agent | Working | HTTP signing server on localhost:8842 |

---
*Status report updated July 8, 2026. v5.1 released - Hyperliquid Vaults polish + LP Fee Fix.*

## v5.1 LP Fee Issue (Resolved July 8, 2026)

### Resolution
- **Root cause identified**: The Project X PositionManager exposes the standard Uniswap V3 `collect((uint256,address,uint128,uint128))` function with selector `0xfc6f7865`.
- A static `eth_call` to this function returns the exact uncollected fee amounts for a token ID, matching the Project X UI tooltip.
- The previous `feeGrowthGlobal` delta approach overcounted ~100× because `feeGrowthOutside` at tick boundaries is non-zero.
- The `PROJECT_X_FEE_ESTIMATION_ENABLED` flag and `fees_note` workaround are replaced by live fee values.

### Verified Example
- Token ID 496329 on the WHYPE/UBTC pool:
  - Static `collect()` eth_call returned ~0.368 HYPE + ~0.000042 UBTC at the time of diagnosis.
  - Project X UI tooltip showed ~0.3443 HYPE + ~0.0000397 UBTC (~$49.83).
  - Values drift with price and accrued fees; the method is correct.

### Files Changed
- `src/venue_adapters/hyperliquid_adapter.py` — fee estimation now uses static `collect()` eth_call (selector `0xfc6f7865`)
- `src/lp_engine.py` — `fees_note` field retained for backward compatibility but no longer populated by Hyperliquid adapter

---

## v5.1 Update (July 8, 2026)

### Changes
- **HL1 Vaults tab redesign**:
  - Vault Name shown as card heading; Vault Address shown smaller.
  - Metrics row: TVL, APR, Vault Age, Deposit Age.
  - Removed "Leader" line.
  - Content moved to top of tab (minimal empty space above header).
- **Saved vaults**:
  - Save/Delete buttons on each vault card.
  - Full position snapshots stored in encrypted vault (`address_db["saved_vaults"]`).
  - Saved vaults render instantly on reopen without requiring a network refresh.
- **Wallet selector**:
  - Two modes: "Address" (raw 0x entry) or "Account" (dropdown of Wallet-tab account names).
  - Auto-resolves the selected account's first EVM/HYPE address.
  - Last mode and selection persisted in encrypted vault config.
- **APR / TVL / Age fixes**:
  - APR from `vaultDetails.apr`.
  - TVL from `maxDistributable` (fallback: sum of follower equities).
  - Vault Age from earliest portfolio history timestamp.
  - Deposit Age from user's `followerState.vaultEntryTime`.
- **LP fee reading fixed**: static `collect()` eth_call on Project X PositionManager.

### Files Changed
- `src/vault_tracker.py` — added full-snapshot serialization, portfolio-history flattening, APR/TVL/deposit-age parsing
- `src/gui_main_v5.py` — HL1 Vaults UI redesign, Save/Delete vault buttons, wallet selector, cached saved-vault rendering
- `src/venue_adapters/hyperliquid_adapter.py` — fee estimation via static `collect()` eth_call
- `README.md`, `STATUS.md`, `CLAUDE.md`, `.clinerules` — updated for v5.1

### Security
- Saved vault snapshots contain only public vault identifiers + cached read-only position data. No private keys.
- All writes to `address_db` are followed by `KeyManager.save_encrypted_data()`.

### Testing
- Syntax checks pass for modified source files.
- HL1 Vaults tab renders correctly with real names, TVL, APR, ages, and saved-vault buttons.
- LP fee values from static `collect()` match Project X UI within expected drift.

---

## v5.1 Update (July 7, 2026)

### Changes
- **Project X fee estimation disabled**: The manual feeGrowthInside calculation from RPC tick storage was returning either $0.04 or more than the pool value — never the correct fees earned. Fee estimation is now disabled behind `PROJECT_X_FEE_ESTIMATION_ENABLED = False`. Helper functions (`_keccak256`, `_read_tick_fee_growth_outside`, `_compute_fee_growth_inside`) are retained for potential reuse with other venues. Collection-based fee tracking is planned for a future release.

### Files Changed
- `src/lp_engine.py` — Added `fees_note: Optional[str] = None` field to `LPPosition`
- `src/venue_adapters/hyperliquid_adapter.py` — Disabled fee estimation behind `PROJECT_X_FEE_ESTIMATION_ENABLED` flag, set `fees_note` on returned positions
- `src/saved_pools.py` (NEW) — Saved pools JSON CRUD module
- `src/gui_main_v5.py` — Added `saved_pools` import, fee note display, Save Pool button, auto-load saved pools on scan, position deduplication
- `build_gui_v5.py` — Added `saved_pools` hidden import
