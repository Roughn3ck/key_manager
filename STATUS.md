## v5.0 - LP Engine + Hyperliquid Writer (July 2026)

### Completed
- [x] `src/venue_adapters/hyperliquid_writer.py` - Full VenueWriter for HyperEVM (wrap, unwrap, approve, open, increase, decrease, collect, close, rebalance)
- [x] `src/gui_main_v5.py` - Copy of v4 + CTkTabview (Vault + LP Positions tabs) + LP tab with card rendering, threaded fetch, Collect Fees write operation
- [x] `build_gui_v5.py` - PyInstaller build script for v5.0 (new hidden imports for lp_engine, venue_adapters, venue_writer, hyperliquid_writer)
- [x] README.md updated with v5.0 changelog and features
- [x] .clinerules updated with v5.0 version table and project structure
- [x] py_compile passes for all new files

### Known Limitations
- Swap router address on HyperEVM not yet confirmed - `swap()` method raises `NotImplementedError`
- Rebalance with cross-ratio swaps deferred until swap router is available
- Writer requires key_manager_agent running with `--serve` on localhost:8842
- Increase/decrease liquidity uses hardcoded WHYPE/UBTC token pair (generalization pending)

### Next Steps
- [ ] Research HyperEVM swap router contract address
- [ ] Test writer end-to-end with agent on testnet
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
| Balance Engine | NEW v4.1 | EVM (6 chains), BTC, SOL, DASH, SUI |
| Price Engine | NEW v4.1 | CoinGecko API, 60s cache |
| GUI v4.1 Source | Working | Settings + inline balances + Go Online |
| GUI EXE | Working | v4.1 build -> `coldstack.exe` (44.50 MB) |
| CLI (script mode) | Working | `python src/main.py` (CLI EXE deprecated) |
| Headless Agent | Working | HTTP signing server on localhost:8842 |

---
*Status report updated June 25, 2026. v4.2 released - Customizable RPC Endpoints + Standard/Advanced Mode.*

## v5.1 LP Fee Issue (Updated July 7, 2026)
### Known Issues (v5.1 LP Fees)
- **LP fee calculation still shows checkpointed values** ($0.04, 6.7e-07 UBTC) instead of real uncollected fees (~$49.83)
- The `collect()` eth_call approach (selector `0x4f1c2879`) was added but the contract may not support this function selector
- The feeGrowthGlobal delta approach was tried but produced ~100x overcount because feeGrowthOutside at tick boundaries is non-zero
- **Confirmed from Project X UI**: Real uncollected fees for token 496329 are 0.3443 HYPE + 0.0000397 UBTC (~$49.83)
- The `tokens_owed` values (0 and 67 raw) are stale checkpointed values from the last position touch
- **Root cause**: Need to determine the correct `collect()` function signature for the Project X PositionManager contract, or find another way to read uncollected fees
- Position value calculation (V3 liquidity math) works correctly (~$4,496)
- Range slider works correctly (green marker, in range)

### Next Steps (v5.2)
- [ ] Collection-based fee tracking — record fee income when user clicks "Collect Fees" instead of estimating from RPC
- [ ] LP position range slider with position marker (green/red) — DONE in v5.1
- [ ] Fees displayed in user's configured currency
- [ ] Fee breakdown display (WHYPE amount + slider + UBTC amount)

## v5.1 Update (July 7, 2026)

### Changes
- **Project X fee estimation disabled**: The manual feeGrowthInside calculation from RPC tick storage was returning either $0.04 or more than the pool value — never the correct fees earned. Fee estimation is now disabled behind `PROJECT_X_FEE_ESTIMATION_ENABLED = False`. Helper functions (`_keccak256`, `_read_tick_fee_growth_outside`, `_compute_fee_growth_inside`) are retained for potential reuse with other venues. Collection-based fee tracking is planned for a future release.
- **`fees_note` field added to LPPosition**: HyperEVM/Project X positions now carry `fees_note="Collect fees to report on fee income"`. The GUI displays this note (in yellow, bold) instead of a misleading fee number.
- **Saved Pools feature added**: New `src/saved_pools.py` module stores public identifiers (wallet address, token ID, venue, pool address, pair) in `saved_pools.json` next to the vault. No private keys stored. "Save Pool" button on each HyperEVM LP card. Auto-loads saved pools on wallet scan (fast token-ID lookup, avoids expensive 6-minute scan). Deduplicates by `venue:position_id`.

### Files Changed
- `src/lp_engine.py` — Added `fees_note: Optional[str] = None` field to `LPPosition`
- `src/venue_adapters/hyperliquid_adapter.py` — Disabled fee estimation behind `PROJECT_X_FEE_ESTIMATION_ENABLED` flag, set `fees_note` on returned positions
- `src/saved_pools.py` (NEW) — Saved pools JSON CRUD module
- `src/gui_main_v5.py` — Added `saved_pools` import, fee note display, Save Pool button, auto-load saved pools on scan, position deduplication
- `build_gui_v5.py` — Added `saved_pools` hidden import
