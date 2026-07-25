## v5.1.3 - Swap Module + Wallet Card Redesign (July 2026)

### New Features
- **Swap Dialog**: New `src/swap_dialog.py` module for token swaps on HyperEVM
  - Swap between HYPE, WHYPE, USDC, UBTC via Uniswap V3 SwapRouter
  - Wrap/unwrap WHYPE ↔ HYPE natively
  - Auto pool selection (prefers lowest fee tier)
  - Price quote from pool slot0
  - Slippage protection (default 1%, configurable)
  - Gas estimate display
  - Bridge tab: EVM ↔ HL1 transfers (reuses evm_transfer_dialog logic)
- **Wallet Card Redesign**:
  - Copy moved to inline icon (⧉) next to address text
  - "Delete" renamed to "Remove"
  - "EVM ↔ HL1" button removed from address cards (moved into Swap dialog Bridge tab)
  - New "Swap" button on supported chains
  - Uniform button widths (all 100px)
  - Improved hover states and colors
- **Unwrap WHYPE**: Part of the swap module — swap WHYPE → HYPE calls WHYPE.withdraw()

### New Files
- `src/swap_dialog.py` — Self-contained swap dialog module

### Files Changed
- `src/gui_main_v5.py` — Address card redesign, swap button, removed EVM↔HL1 button
- `build_gui_v5.py` — Version strings v5.1.3, swap_dialog hidden import
- `README.md` — Updated for v5.1.3
- `STATUS.md` — v5.1.3 changelog
- `.clinerules` — Version update

---

## v5.1.2 - Vault Explore + Deposit + EVM Transfer + Balance Conversion (July 2026)

### New Features
- **Vault Explore**: "Explore Vaults" button on HL1 Vaults tab opens https://app.hyperliquid.xyz/vaults in browser for vault research
- **Vault Deposit**: Paste a vault address, deposit USDC from HL1 spot balance via CoreWriter contract (0x333...3333) — signed locally by embedded agent, no browser wallet needed
- **EVM ↔ HL1 Transfer Dialog**: Bridge assets between HyperEVM and HyperCore (HL1) Spot balances. EVM→HL1 via ERC-20 transfer to system address; HL1→EVM via spotSend L1 action. Separate module (evm_transfer_dialog.py)
- **HyperEVM Balance Fiat Conversion**: WHYPE and UBTC now show USD/fiat conversion (pegged to HYPE and BTC respectively)
- **Close Position Auto-Refresh**: After closing an LP position, the position cards automatically refresh after 3 seconds
- **Chain Display Fix**: HyperEVM balances display as "HyperEVM" instead of "Hyperliquid_evm"
- **EIP-712 Typed Data Signing**: New `sign_typed_data` command in key_manager_agent for EIP-712 structured data signing (used by HL1→EVM transfers)

### New Files
- `src/evm_transfer_dialog.py` — Self-contained EVM ↔ HL1 transfer dialog
- `src/vault_deposit_dialog.py` — Self-contained vault deposit dialog

### Files Changed
- `src/gui_main_v5.py` — Close position refresh, EVM↔HL1 button, vault explore + deposit bar, chain display fix
- `src/price_engine.py` — WHYPE→HYPE, UBTC→BTC pegged token mappings
- `src/key_manager_agent.py` — `sign_typed_data` command for EIP-712 signing
- `src/lp_liquidity_manager.py` — Additional liquidity management updates
- `src/venue_adapters/hyperliquid_writer.py` — Writer updates
- `build_gui_v5.py` — Version strings v5.1.2, new hidden imports
- `README.md` — Updated for v5.1.2
- `STATUS.md` — v5.1.2 changelog

### Security
- Vault deposits signed locally by embedded key_manager_agent — higher security than browser wallet
- EVM transfers signed by agent — no private keys exposed
- All new modules are self-contained dialogs — no changes to existing signing paths

### Verification
- All source files pass `python -m py_compile`
- EXE build pending

---

## v5.1.1 - Add/Remove Liquidity + Gas Fix + Fee Tracking (July 2026)

### New Features
- **Add/Remove Liquidity UI**: + / − / ✎ icons on LP position cards next to Value
  - "+" opens Add Liquidity dialog with token inputs, 50%/Max buttons, and auto-balance (Zap In) toggle
  - "−" opens Remove Liquidity dialog with percentage slider and estimated output
  - "✎" opens Edit Position stub (coming in v5.2)
- **Auto-balance (Zap In)**: When enabled, system swaps excess token to match position ratio before depositing
- **New module `lp_liquidity_manager.py`**: All liquidity management dialogs in a separate file
- **Unstubbed `swap()` method**: SwapRouter.exactInputSingle now working for standalone swaps
- **`get_swap_quote()`**: Read-only swap price estimation for UI display

### Bug Fixes
- **Position decode offset fix (Forge-17/18)**: `_read_position_data()` now reads fee/tickLower/tickUpper from correct byte offsets (was reading token0/token1 as fee/ticks → OverflowError)
- **Gas price 6x boost**: Increased from 3x to 6x to clear HyperEVM mempool rejections (eth_gasPrice returns 0.1 Gwei, market is ~0.28 Gwei, 6x = 0.6 Gwei)
- **Fee tracking bug**: Fixed `_evm_rpc_call` called as instance method instead of module function
- **Compound fees flow**: Collect → Swap → IncreaseLiquidity now works end-to-end (IncreaseLiquidity fails only on dust amounts, which is expected contract behavior)
- **Nonce retry logic**: Fresh nonce fetch on "nonce too high" with 3s delay

### Verification
- Compound fees: collect ✅ → swap ✅ → increaseLiquidity ✅ (on meaningful amounts)
- Add/Remove liquidity dialogs open from position cards
- Position decode: tick_lower=-300060, tick_upper=-297120, fee=3000 (correct)
- Fee tracking: PnL, APR, days active display on position cards

---

## v5.1 - Hyperliquid Vaults + LP Fee Fix (July 2026)

### Bug Fixes (v5.1 refresh)
- Fee buttons (Compound, Collect, Close Position) now visible in Standard mode (not just Advanced)
- "Withdraw Fees" button replaced with "Close Position" (decreaseLiquidity + collect)
- Canceling a fee operation confirmation no longer clears the screen
- Switching Standard/Advanced mode re-renders LP cards immediately
- Currency display: Vault and LP tabs now show USD + selected display currency (e.g. AUD, CAD)
- Scan Wallet NFT detection: increased scan window from 2000 to 5000, with fallback extension
- Transaction hash entry: better error message suggesting numeric Position ID
- Pool address entry: shows pool price/token pair without error note
- Fee reporting: live fee data via collect() eth_call with wallet_address context
- Auto-detect + Position ID: user-friendly "Select a Platform" notification
- Fetch Position with empty Position ID: falls through to Scan Wallet
- Scan Wallet button: enabled immediately when wallet address is available

### UI Improvements (v5.1 refresh)
- % In Range: green/bold when in range, red/bold when out of range
- Value: green/bold, positioned after Holdings
- Suggestion: yellow/bold, at the end of Line 3
- Scan Wallet: time warning dialog before starting full scan

### Completed
- [x] `src/gui_main_v5.py` — HL1 Vaults tab bug fixes (Prompt 1): Save/Delete Vault buttons now toggle in place without clearing the scroll frame and re-fetching; selector defaults to Account mode; saved vaults auto-load on unlock and are shown for all wallets
- [x] `src/vault_tracker.py` — Read-only Hyperliquid vault positions via `/info` API (`userVaultEquities` + `vaultDetails`)
- [x] `src/gui_main_v5.py` — HL1 Vaults tab with vault cards: Vault Name heading, Vault Address, TVL, APR, Vault Age, Deposit Age, Deposited, Current Value, Unrealized P&L, Copy Address
- [x] Saved vaults stored inside encrypted `key_vault.encrypted` (`saved_vaults` top-level list), with Save/Delete buttons and cached snapshots so saved vaults render instantly on reopen
- [x] Wallet selector in HL1 Vaults tab: raw address entry or account dropdown from Wallet tab; mode and selection persisted in encrypted vault config
- [x] `src/venue_adapters/hyperliquid_adapter.py` — LP fee reading fixed: uses static `collect((uint256,address,uint128,uint128))` eth_call (selector `0xfc6f7865`) to return real uncollected fees from the Project X PositionManager; matches Project X UI tooltip
- [x] `src/venue_adapters/hyperliquid_adapter.py` — Wallet scan fixed: removed stale hardcoded `known_hints`, widened scan window to 2000 NFTs, removed duplicate `total_supply` call, added fresh ownership re-verification, and probes 100 newly minted tokens that appear during the scan
- [x] `src/gui_main_v5.py` — LP scan progress messages: status label now shows "Fetching saved positions... Full wallet scan will follow." and "Scanning wallet for new positions — this may take up to 6 minutes." before starting the background scan
- [x] `src/gui_main_v5.py` — Remove Pool UX fixed: removing a saved pool now destroys only that position's card, updates the saved-pools counter, and does not trigger a full wallet rescan; the "Fetch Position" button is re-enabled after every fetch (success or error)
- [x] `src/venue_adapters/hyperliquid_adapter.py` — Fetch Position input parsing fixed: 66-character transaction hashes resolve to the LP position via `eth_getTransactionReceipt` + PositionManager `Transfer` event; known contract addresses (PositionManager, Pool Factory, WHYPE, UBTC) return helpful errors instead of being misidentified as pools
- [x] `src/gui_main_v5.py` — LP Address/Account selector + Save Pool flow fixed: new `_lp_get_current_wallet_address()` helper resolves wallet from either Address entry or Account dropdown; `_lp_do_fetch()` and `_lp_do_fetch_single()` show mode-aware error messages; single-position fetch captures and preserves wallet address for Save Pool so the flow works end-to-end in Account mode
- [x] `src/gui_main_v5.py` — Scan Wallet button enabled after state restore when online and Account mode is selected (`_lp_restore_state()` now re-enables `refresh_btn`/`fetch_pos_btn` if `self.online_mode`)
- [x] `src/venue_adapters/hyperliquid_adapter.py` — Auto-detect now accepts 66-character hex transaction hashes and numeric token IDs in `can_handle()`, so entering a Project X LP transaction hash with “Auto-detect” resolves correctly
- [x] `saved_pools.py` — Saved pools CRUD (public identifiers only, no private keys), stored inside encrypted `key_vault.encrypted` (`address_db["saved_pools"]`); no separate JSON file
- [x] `src/gui_main_v5.py` — Saved pools rendered persistently for ALL wallets on LP tab unlock/restore (`_lp_restore_state()` calls `_lp_render_all_saved_placeholders()`); new placeholder cards show pair, venue, token ID, wallet, and Fetch/Remove Pool buttons
- [x] `src/gui_main_v5.py` — `_lp_render_all_saved_placeholders()` shows every saved pool regardless of wallet with live fields as "Not fetched" until Fetch is clicked
- [x] `src/gui_main_v5.py` — `_lp_fetch_saved_single()` fetches a single saved pool by token ID, pre-filling the wallet address and platform dropdown from the saved snapshot
- [x] `src/gui_main_v5.py` — `_lp_update_saved_pools_count()` now reports total saved pools across all wallets in the status label (avoiding duplicate suffixes)
- [x] `src/gui_main_v5.py` — `_lp_remove_pool()` now accepts an optional `card_frame` argument so placeholder cards are destroyed immediately when removed
- [x] HyperEVM ERC-20 balance support (USDC, WHYPE, UBTC), combined "Hyperliquid (HL1 & HyperEVM)" chain option, stablecoin currency conversion fix, balance dispatcher fix
- [x] `build_gui_v5.py` — PyInstaller hidden imports fixed: added `click`, `rich` (+ submodules), `urllib` (+ `--collect-submodules=urllib`), and full stdlib HTTPS/SSL stack (`ssl`, `_ssl`, `http.client`, `socket`, `_socket`) so the bundled EXE can resolve `https://` URLs for LP, balances, prices, vault tracker, and update checker
- [x] README.md, CLAUDE.md, .clinerules updated for v5.1
- [x] py_compile passes for modified source files
- [x] `src/gui_main_v5.py` — LP position card now shows token holdings (e.g. "Holdings: 13.58 HYPE · 0.00734 UBTC") read from `position.deposit_amounts`
- [x] `src/gui_main_v5.py` — Compound/Collect Fees buttons fixed: no longer incorrectly require `self.current_account` (Wallet tab selection); they now validate the LP tab wallet address and resolve the correct vault account name for the writer
- [x] `src/gui_main_v5.py` — LP position cards reformatted to three compact lines: (1) pair · venue · ID, (2) Range · Current · % In/Out Range, (3) Fees · Value · PnL · Holdings · Suggestion; slider stays between line 2 and line 3
- [x] `src/gui_main_v5.py` — Added "Withdraw Fees" button alongside "Collect Fees" and "Compound Fees" on HyperEVM LP cards (Advanced mode); calls the same `collect()` function as Collect Fees for UX clarity
- [x] `requirements.txt` + `build_gui_v5.py` + `src/gui_main_v5.py` — bundled `certifi` CA certificates and set `SSL_CERT_FILE` at startup so the frozen EXE can verify GitHub/RPC HTTPS TLS certificates (fixes "Check for Updates" and all online features in the portable build)
- [x] `src/venue_adapters/hyperliquid_writer.py` — Compound Fees fix per spec: snapshot balances before collect, wait for TX receipts, track nonces for multi-TX flows, use fee deltas (not total wallet balance), and use `MAX_UINT128` for uint128 `amount0Max`/`amount1Max` fields in `collect()`
- [x] `src/gui_main_v5.py` + `build_gui_v5.py` — Embedded key_manager_agent HTTP server: GUI now starts an internal agent thread on `127.0.0.1:8842` after vault unlock so Collect/Compound Fees work without a separate process; server stops on vault lock
- [x] `src/key_manager_agent.py` — `get_address()` chain matching changed from exact match to substring match so descriptive vault entries like "EVM (Ethereum / Arbitrum / Base)" resolve correctly for the writer
- [x] Collect Fees from a Project X HyperEVM LP position tested and confirmed working end-to-end via the embedded agent
- [x] EXE rebuilt with `python build_gui_v5.py` (45.14 MB, 15/07/2026) — all network features confirmed working (LP positions, perp state, spot balances, price feeds, vault tracker, update checker, collect/withdraw fees)

### Data Sources
- **APR**: `vaultDetails.apr` (annualized decimal from Hyperliquid API, e.g. `-0.0052` → `-0.5%`)
- **TVL**: `vaultDetails.maxDistributable`; fallback to sum of `followers[*].vaultEquity`
- **Vault Age**: earliest timestamp in `vaultDetails.portfolio` history
- **Deposit Age**: `vaultDetails.followerState.vaultEntryTime` for the queried user

### Known Limitations (v5.1)
- ~~Swap router address on HyperEVM still not confirmed — `swap()` raises `NotImplementedError`; cross-ratio rebalances deferred~~ Fixed in v5.1.1
- ~~Compound Fees not yet tested live (waiting for fees to accrue)~~ Tested and working in v5.1.1

### Next Steps (v5.1.1)
- [x] Research/confirm HyperEVM swap router contract address
- [x] Collect Fees tested and confirmed working on Project X mainnet
- [x] Test Compound Fees end-to-end with embedded agent on mainnet
- [ ] Build EXE with `python build_gui_v5.py` (in progress)
- [ ] Test EXE on clean Windows machine
- [x] Test Add/Remove Liquidity dialogs from position cards

### v5.1.1 Completed
- [x] `src/lp_liquidity_manager.py` created with Add/Remove/Edit dialogs
- [x] `src/gui_main_v5.py` wired + / − / ✎ icons with status-bar tooltips
- [x] `src/venue_adapters/hyperliquid_writer.py` — unstubbed `swap()` + `get_swap_quote()`
- [x] `build_gui_v5.py` — version strings v5.1.1 + `lp_liquidity_manager` hidden import
- [x] `AGENTS.md`, `.clinerules`, `README.md`, `STATUS.md` updated for v5.1.1

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
**Last Updated:** 2026-07-25
**Current Version:** v5.1.3 (Swap Module + Wallet Card Redesign)

## Versioning

| Version | Description | Key Files |
|---------|-------------|-----------|
| v1 | Original GUI - display-only | `src/gui_main.py`, `build_gui.py` |
| v2 | GUI Add functionality | `src/gui_main_v2.py`, `build_gui_v2.py` |
| v3 | GUI BIP39 derivation | `src/gui_main_v3.py`, `build_gui_v3.py`, `src/derivation_engine.py` |
| v3.1 | ColdStack rebrand + Check for Updates | `src/gui_main_v3_1.py`, `build_gui_v3_1.py` |
| v4.1 | Price Feeds + Wallet Balances + Go Online | `src/gui_main_v4.py`, `build_gui_v4.py`, `src/balance_engine.py`, `src/price_engine.py` |
| v4.2 | Customizable RPC + Standard/Advanced Mode | `src/rpc_config.py`, `rpc_endpoints.json`, `src/balance_engine.py` (updated), `src/gui_main_v4.py` (updated) |
| v5.1.1 | Add/Remove Liquidity + Gas Fix + Fee Tracking | `src/lp_liquidity_manager.py`, `src/gui_main_v5.py`, `src/venue_adapters/hyperliquid_writer.py`, `build_gui_v5.py` |
| v5.1.2 | Vault Explore + Deposit + EVM Transfer + Balance Conversion | `src/evm_transfer_dialog.py`, `src/vault_deposit_dialog.py`, `src/gui_main_v5.py`, `src/price_engine.py`, `src/key_manager_agent.py`, `build_gui_v5.py` |
| v5.1.3 | Swap Module + Wallet Card Redesign | `src/swap_dialog.py`, `src/gui_main_v5.py`, `build_gui_v5.py` |

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
| GUI EXE | Working v5.1.3 | Swap module + wallet card redesign build complete |
| CLI (script mode) | Working | `python src/main.py` (CLI EXE deprecated) |
| Headless Agent | Working | HTTP signing server on localhost:8842 |

---
*Status report updated July 25, 2026. v5.1.3 build refreshed — swap dialog, wallet card redesign, full HTTPS/SSL stack bundled, all network features working.*

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

## v5.1 Update (July 15, 2026) — Prompt 3: Saved Pools Persistent + All Wallets

### Changes
- **All saved pools shown persistently on unlock**: `_lp_restore_state()` now calls `_lp_render_all_saved_placeholders()` immediately after restoring the selector state, so saved pools appear as placeholder cards as soon as the vault is unlocked — no live fetch required.
- **No wallet filter on saved pool display**: `_lp_render_all_saved_placeholders()` loads every saved pool from `address_db["saved_pools"]` regardless of `wallet_address`. Each placeholder card displays pair, venue, token ID, truncated pool address, wallet, and "Not fetched" placeholders for live fields.
- **Fetch a single saved pool**: New `_lp_fetch_saved_single()` method pre-fills the LP tab wallet address, platform dropdown, and position entry from the saved snapshot, then calls `_lp_do_fetch_single()` to pull live data.
- **Remove Pool works from placeholders**: `_lp_remove_pool()` now accepts an optional `card_frame` argument and destroys the placeholder card directly when provided; the existing live-card lookup via `position_cards` is preserved for backward compatibility.
- **Saved-pools counter in status label**: `_lp_update_saved_pools_count()` now updates the status label with the total number of saved pools across all wallets, stripping any previous saved-pools suffix to avoid duplication.

### Files Changed
- `src/gui_main_v5.py` — `_lp_restore_state()`, `_lp_update_saved_pools_count()`, `_lp_remove_pool()`; new methods `_lp_render_all_saved_placeholders()` and `_lp_fetch_saved_single()`
- `STATUS.md` — recorded Prompt 3 fixes

### Testing
- `python -m py_compile src/gui_main_v5.py` passes.
- EXE rebuilt with `python build_gui_v5.py` (45.14 MB, 15/07/2026).

---

## v5.1 Update (July 15, 2026) — Prompt 4: LP Card Reformat

### Changes
- **Three-line compact LP cards**: `_lp_render_card()` now consolidates position information into:
  1. Header line — pair · venue · position ID (all on one line).
  2. Range line — Range · Current Price · % In/Out of Range (slider follows this line).
  3. Details line — Fees earned (with token breakdown in parentheses) · Value · PnL · Holdings · Suggestion.
- **Removed separate labels**: The standalone `ID:`, `% In Range`, `Fees earned`, `Value`, `Holdings`, and `Suggestion` labels are gone; their content is merged into the three lines above.
- **Slider unchanged**: The range slider with the colored marker remains between line 2 and line 3.
- **Error note preserved**: `position.error` still appears on its own line when present.

### Files Changed
- `src/gui_main_v5.py` — `_lp_render_card()` restructured
- `STATUS.md` — recorded Prompt 4 reformat

### Testing
- `python -m py_compile src/gui_main_v5.py` passes.
- EXE rebuilt with `python build_gui_v5.py` (45.14 MB, 15/07/2026).

---

## v5.1 Update (July 15, 2026) — Prompt 5: Withdraw Fees Button

### Changes
- **New "Withdraw Fees" button on HyperEVM LP cards**: Advanced mode now shows three action buttons per HyperEVM LP position: "Compound Fees", "Collect Fees", and "Withdraw Fees".
- **Withdraw == Collect semantically**: "Withdraw Fees" calls the same `_lp_collect_fees_dialog()` / `collect()` flow as "Collect Fees" — the only difference is the button label, which some users find clearer (it emphasizes that fees are transferred to the wallet).
- **Purple styling**: Withdraw Fees uses a distinct purple color (`#6f42c1` / `#5a32a3`) so it is visually distinguishable from Collect Fees (orange) and Compound Fees (green/teal).

### Files Changed
- `src/gui_main_v5.py` — `_lp_render_card()` adds Withdraw Fees button; new `_lp_withdraw_fees_dialog()` method
- `README.md` — updated LP write-operations bullet to mention all three fee buttons
- `STATUS.md` — recorded Prompt 5 addition

### Testing
- `python -m py_compile src/gui_main_v5.py` passes.
- EXE rebuilt with `python build_gui_v5.py` (45.14 MB, 15/07/2026).

---

## v5.1 Update (July 15, 2026)

### Changes
- **LP Scan Wallet button state fixed**: In Account mode, the Scan Wallet button could remain disabled after unlock because `_lp_restore_state()` restored the dropdown without re-enabling the action buttons. The restore method now explicitly enables `refresh_btn` and `fetch_pos_btn` when the app is online.
- **Auto-detect extended to transaction hashes and token IDs**: `HyperliquidAdapter.can_handle()` now accepts 66-character hex strings (32-byte transaction hashes) and numeric strings (NFT token IDs) in addition to 42-character EVM addresses. Entering a Project X LP transaction hash with Platform set to "Auto-detect" now resolves to the position via `eth_getTransactionReceipt` + PositionManager `Transfer` event.

### Files Changed
- `src/gui_main_v5.py` — `_lp_restore_state()` now re-enables LP action buttons when online
- `src/venue_adapters/hyperliquid_adapter.py` — `can_handle()` accepts 42-char, 66-char, and numeric inputs
- `STATUS.md` — recorded fixes and refreshed build metadata

### Testing
- `python -m py_compile src/gui_main_v5.py` passes.
- `python -m py_compile src/venue_adapters/hyperliquid_adapter.py` passes.
- EXE rebuilt with `python build_gui_v5.py` (45.14 MB, 15/07/2026) after all five prompts were applied.

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
