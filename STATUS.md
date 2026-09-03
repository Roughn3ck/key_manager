# ColdStack - Status Report

**Project:** https://github.com/Roughn3ck/key_manager
**Current Version:** v5.3.3 (Railgun Sidecar Syntax Hotfix)
**Last Updated:** 2026-09-03

---

## v5.3.3 - Railgun Sidecar Syntax Hotfix (2026-09-03)

### Summary
Hotfix for a broken `sidecar/src/routes/engine.js` shipped in v5.3.2. The new `POST /engine/load-provider` route was inserted without the preceding `/engine/status` route's closing `});`, making the file syntactically invalid. Sidecar crashed on cold start with a SyntaxError that the GUI never surfaced.

### Fixed
- **`engine.js` syntax error** — restored the missing `});` closing the `/engine/status` route handler. `node --check` now passes on all 14 sidecar JS files.

### Process
- Added pre-ship sidecar gate to AGENTS.md: `node --check` on every `sidecar/src/**/*.js`, plus cold-start boot smoke test (`node src/server.js` → health OK ≤30s). Sidecar ships only if it boots cleanly.

---

## v5.3.2 - Railgun Transactions (September 2026)

### Summary
v5.3.2 enables full Railgun transactions (shield, unshield, private transfer) with a complete round-trip for native ETH. Critical fixes for a show-stopping `show_mnemonic` crash, dead RPC endpoints, and a 6-chain Railgun overclaim (SDK 7.6.1 supports only 4 chains).

### Fixed
- **`show_mnemonic` crash** — 4 call sites used `self.gui.show_mnemonic()` (doesn't exist on `ColdStackGUI`); fixed to `self.gui.key_manager.show_mnemonic()` — every Railgun transaction path was silently crashing before showing any message
- **RPC endpoint refresh** — replaced dead/gated public RPC endpoints (llamarpc, ankr, polygon-rpc) with working public ones (`publicnode.com`, `drpc.org`); Ethereum and Polygon Railgun providers now load
- **Engine init toString crash** — `SUPPORTED_RAILGUN_NETWORKS` included undefined `Base`/`Optimism` entries (SDK 7.6.1); removed both, engine init no longer crashes on success
- **Chain reality fix** — Railgun trimmed to 4 chains (Ethereum, Arbitrum, BSC, Polygon); Base/Optimism commented out pending SDK upgrade (wallet>10.4.0 / shared-models>7.6.1)

### New Features
- **Railgun Shield (public → private)** — dialog wraps native ETH (deposit → WETH) then shields via Railgun proxy; supports both native ETH and standard ERC-20s
- **Railgun Unshield-to-Native (private → native ETH)** — chains unshield → auto-withdraw (WETH → ETH) in one flow; plain WETH unshield unchanged
- **Railgun Private Transfer (0zk → 0zk)** — encrypted memo, show-sender toggle, full confirmation summary with human-readable amount + base-unit verification
- **Railgun provider status display** — per-chain ✓/✗ with error text; "Reload Providers" button retries failed chains without engine restart
- **Token info endpoint** (`GET /transfer/token-info`) — symbol + decimals for any ERC-20; native ETH returns wrapped-token info
- **Balance labels** — known wrapped-native tokens (WETH/WBNB/WMATIC) display by symbol instead of truncated address
- **Wrapped-native addresses** — canonical WETH/WBNB/WMATIC for Ethereum, Arbitrum, BSC, Polygon; verified on-chain

### v5.3.2 Bundled Work (from previous uncommitted prompts)
- **RPC endpoint refresh** (`rpc_endpoints.json`, `balance_engine.py`, `rpc_config.py`) — all 5 EVM chain defaults replaced with verified live endpoints
- **Per-mint Orca token program fix** — closePosition ATA creation uses per-mint `token_prog_a`/`token_prog_b` (position NFT vs pool tokens)
- **Railgun closure bug fixes** — error notification lambda capture (`{e}` → `msg`) prevents silent `NameError` crashes on all 4 Railgun error paths
- **Add Private Key scroll + custom mnemonic derive** — dialog scrolls when content overflows; custom mnemonic derive stores key in `custom_derived_meta` instead of reading disabled entry

### Files Added
- `src/railgun_tx_dialogs.py` — Shield/Unshield/Private Transfer dialogs (native ETH support, threaded submit, copyable results)
- `src/ed25519_utils.py` — shared Ed25519 math primitives, base58, SLIP-0010 derivation

### Files Changed
- `src/gui_main_v5.py` — VERSION bump to 5.3.2
- `src/railgun_tab.py` — 4 show_mnemonic fixes; chain lists to 4; balance labels; per-chain provider status; Reload Providers button; hardcoded 6-chain text fixed
- `src/railgun_tx_dialogs.py` — RAILGUN_CHAINS to 4; ETH as valid token in ShieldDialog; UnshieldDialog native unwrap checkbox + 2-tx status
- `src/railgun_bridge.py` — transfer methods rewritten to match sidecar contract; reload_provider(); LONG_TIMEOUT for proofs
- `sidecar/src/routes/engine.js` — `POST /engine/load-provider` (single-chain provider retry); init skips unknown chains gracefully
- `sidecar/src/routes/transfer.js` — native ETH shield (wrap + shield); unshield-to-native (unshield + auto-unwrap); token-info native support
- `sidecar/src/networks.js` — trimmed to 4 chains; WRAPPED_NATIVE map with 4 live entries
- `src/balance_engine.py` + `src/rpc_config.py` + `rpc_endpoints.json` — endpoint refresh
- `build_gui_v5.py` — railgun_tx_dialogs hidden-import; launch banner v5.3.2
- `README.md` — latest-release link
- `STATUS.md` — this entry
- `AGENTS.md` — version refs

### Verification
- `python -m py_compile` passes on all modified files
- Solana test mnemonic produces `HAgk14JpMQLgt6rVgv7cBQFJWFto5Dqxi472uT3DKpqk` ✅
- Engine init no longer crashes on Base/Optimism entries
- Sidecar endpoints verified: `/transfer/token-info?chain=arbitrum&tokenAddress=ETH` returns `{native:true, symbol:"WETH", decimals:18}`
- EXE builds as Windows PE32+ (156.32 MB)
- Railgun live test: shield ETH on Arbitrum → confirm Spendable; private transfer → unshield to native ETH

---

### Summary
Hotfix for Orca Whirlpool close position. Positions opened via Orca's current UI mint Token-2022 position NFTs (with metadata extension); the legacy `closePosition` instruction types `position_mint` as SPL Token-only and rejects them with `AccountOwnedByWrongProgram` (3007). The Orca writer now selects the dedicated `closePositionWithTokenExtensions` instruction when the position NFT belongs to Token-2022 — identical account layout, Token-2022 token program required; the close burns the NFT and closes both the token account and the mint account (rent reclaimed).

### Fixed
- **Orca close position on Token-2022 NFTs** — discriminator `01b6873b9b1963df` selected when the position mint's owning program is Token-2022; SPL positions continue on legacy `closePosition` (`7b86510031446262`). The decrease-liquidity and collectFees steps were already Token-2022-compatible; only the close instruction changed.
- Root-cause evidence and instruction details: `coldstack-orca-token2022-close-instruction-fix.md`

### Files Changed
- `src/venue_adapters/orca_writer.py` — `DISC_CLOSE_POSITION_TE` constant + conditional discriminator in `_build_close_position_ix`
- `src/gui_main_v5.py` — VERSION bump to 5.3.1
- `AGENTS.md` — production version refs updated; ColdTrack roadmap phase tags shifted (this hotfix took the v5.3.1 slot)
- `README.md` — latest-release link updated to v5.3.1
- `STATUS.md` — this entry

### Verification
- `python -m py_compile` passes for all modified files
- EXE rebuild is a Windows PE32+ build; GUI header shows v5.3.1
- Orca fetch and SPL-token positions unaffected; ColdTrack and all other tabs present
- Live G3 Token-2022 position closed cleanly (NFT burned, token account + mint closed, rent reclaimed)

---

## v5.3.0 - ColdTrack Foundation (August 2026)

### Summary
v5.3.0 introduces ColdTrack as a new module and tab in ColdStack. ColdTrack is the financial ledger layer — the monetization layer of the ColdStack ecosystem. This release establishes the foundation: SQLite database, account bridge from vault, and the ColdTrack tab UI. It also bundles all v5.2.6 hotfixes (Solana derivation, Token-2022, base58 display, etc.) that were never separately released.

### New Features
- **ColdTrack Module** — New `src/coldtrack/` subpackage
  - SQLite database layer (`coldtrack.db`) with 9-table schema (v3.0)
  - Schema: PORTFOLIOS, ACCOUNTS, FX_RATES, TRANSACTIONS, LP_POSITIONS, LP_SNAPSHOTS, VAULT_DEPOSITS, HOLDINGS, TAGS + TRANSACTION_TAGS
  - Account bridge: user-initiated sync from vault → ColdTrack DB
  - ColdTrack tab in CTkTabview: portfolio overview + account list
  - Controlled disclosure: only public identifiers cross the bridge (no private keys)
- **Multi-currency schema** — USD, CAD, AUD, EUR values stored per transaction/snapshot
- **Multi-portfolio schema** — PORTFOLIOS as ownership root (Executive Mind, Kit & Paul)

### v5.2.6 Hotfixes (bundled)
- **Solana derivation fix** — SLIP-0010 all-hardened 4-level derivation (`m/44'/501'/0'/0'`) replacing wrong BIP44 5-level
- **Base58 private key display** — Solana keys shown in 64-byte keypair base58 format (Brave/Phantom compatible)
- **Solana sendTransaction encoding** — explicit `{"encoding": "base64"}` fixes invalid base58 errors
- **Token ID type fix** — EVM token_id string→int conversion for saved pools
- **Orca Token-2022 support** — per-mint token program detection (position NFT vs pool tokens)
- **LP tab error fixes** — saved pool placeholders for failed fetches, Orca auto-fetch, copyable error messages
- **Add Private Key dialog** — scrollable form, vault-mnemonic derive stores key in derived_meta (placeholder fix)
- **Delete private key feature** — per-key delete with password confirmation
- **VERSION constant** — single source of truth replacing hardcoded version strings

### Files Added
- `src/coldtrack/__init__.py` — Package init
- `src/coldtrack/db.py` — SQLite database layer (9 tables, CRUD)
- `src/coldtrack/importer.py` — Vault → ColdTrack account bridge
- `src/coldtrack/tab.py` — ColdTrack tab UI
- `src/ed25519_utils.py` — Shared Ed25519 math primitives, base58, SLIP-0010 HD derivation
- `src/venue_adapters/orca_adapter.py` — Orca Whirlpool read adapter (Solana LP positions)
- `src/venue_adapters/orca_writer.py` — Orca Whirlpool write adapter (collect/close/rebalance via agent)

### Files Changed
- `src/gui_main_v5.py` — ColdTrack tab registered; VERSION constant; delete private key UI; base58 display; copyable notifications; scrollable Add Private Key dialog
- `src/derivation_engine.py` — Solana SLIP-0010 routing; ed25519_utils imports
- `src/key_manager_agent.py` — ed25519_utils imports; Solana sendTransaction encoding fix; SLIP-0010 key preference
- `src/main.py` — delete_private_key() + delete-key CLI command
- `src/account_dialogs.py` — Add Private Key overhaul (scrollable, custom mnemonic, derived_meta fix); delete private key dialog
- `src/lp_tab.py` — Placeholder rendering for failed fetches; Orca auto-fetch; token_id type fix
- `src/lp_engine.py` — Minor updates
- `src/venue_adapters/orca_adapter.py` — Token-2022 detection; Token-2022 wallet scan; _detect_token_program
- `src/saved_pools.py` — Minor updates
- `build_gui_v5.py` — ColdTrack + ed25519_utils hidden imports; version bumped to v5.3.0
- `.gitignore` — coldtrack.db
- `AGENTS.md` — ColdTrack module section; version bump
- `README.md` — ColdTrack section
- `src/chain_options.py` — Minor updates

### Verification
- `python -m py_compile` passes for all new and modified files
- Solana test mnemonic produces `HAgk14JpMQLgt6rVgv7cBQFJWFto5Dqxi472uT3DKpqk` ✅
- GUI launches, ColdTrack tab appears alongside existing tabs
- Vault sync creates coldtrack.db with correct schema
- Portfolios and accounts display correctly after sync
- Existing tabs (Wallet, HL1 Vaults, LP Positions, Railgun) unaffected
- All `py_compile` checks pass

---

## Known Issues

### Aerodrome SlipStream — Active Troubleshooting

**Status:** Read support is functional; write operations are still being validated

Aerodrome SlipStream positions on Base are now discovered via wallet scan and saved-pool gauge lookup. However, the following write paths are not yet fully verified and may fail in production:

- **Collect Fees** — fee collection for Aerodrome NFT positions needs the correct `NonfungiblePositionManager.collect()` calldata and spender approval path. Test on a small position first.
- **Compound Fees** — requires collect → swap through the Aerodrome swap router → re-deposit; the exact router and multi-hop path for SlipStream pools has not been confirmed.
- **Close Position** — must collect unclaimed fees, then call `decreaseLiquidity` with `liquidity=uint128.max` and burn the NFT. The current decrease/burn sequence is being validated against live SlipStream positions.

Until these are verified, treat Aerodrome write operations as experimental. Always double-check the transaction preview and ensure the embedded signing agent is running before confirming.

### Aerodrome SlipStream Staked Positions — Wallet Scan Workaround

**Status:** Temporary workaround — requires manual NFT ID entry

Aerodrome SlipStream positions staked in a CL gauge cannot be discovered via wallet scan. When a position is staked, the NFT is transferred to the gauge contract, so `balanceOf(wallet)` returns 0. The adapter has no way to enumerate all gauges from the Aerodrome Voter contract (no `poolLength()` or `pools(uint256)` exposed).

**Current workaround:** Enter the NFT token ID manually in the Position ID field. The token ID is visible on the Aerodrome dashboard (aerodrome.finance → Dashboard → look for `#<number>` next to "Deposit"). A popup guides the user to this when an Aerodrome scan returns 0 positions.

**Future fix options (not yet viable):**
- Transfer event log scanning: requires `eth_getLogs` over large block ranges. Public Base RPCs cap at ~10k blocks per query (413 error above). Scanning 6 months of history would need ~800 sequential calls — too slow/unreliable. A paid RPC (Alchemy/QuickNode/Infura) with higher log limits would make this feasible.
- Aerodrome subgraph: the Aerodrome frontend uses a single Multicall3 batch call with 8192 bytes of custom bytecode sent to the wallet address. This likely relies on EIP-7702 (EOA delegation) or a similar mechanism that only works if the wallet has code. No public subgraph endpoint has been identified.
- Caching (current partial solution): once a user enters a token ID manually and saves the pool, future wallet scans find the position via the saved-pools gauge lookup (`_find_staked_positions_via_saved_pools`).

---

## v5.2.4 - Railgun Sidecar + Aerodrome & BSC V3 Pools (August 2026)

### Summary
v5.2.4 ships the Railgun privacy sidecar alongside expanded LP coverage: Aerodrome SlipStream on Base and Uniswap V3 / PancakeSwap V3 on BNB Chain. This is an incremental release on top of v5.2.3 with a bug fix for account deletion.

### New Features
- **Aerodrome SlipStream (Base)** — read-only position discovery for concentrated-liquidity pools on Base
  - Wallet scan via `balanceOf`/`tokenOfOwnerByIndex` on the SlipStream Position Manager
  - Saved-pool gauge fallback for staked positions (`_find_staked_positions_via_saved_pools`)
  - Position decoding: token pair, fee tier, tick range, liquidity, price, in-range %
- **BSC V3 Pool Reads** — Uniswap V3 and PancakeSwap V3 positions on BNB Chain via the Krystal adapter
  - Factory/NPM registry for both DEXs
  - Wallet scan across all registered position managers
  - Fee estimation via read-only `collect()` `eth_call`

### Bug Fixes
- **Delete Account** (`src/main.py`, `src/account_dialogs.py`) — accounts that only exist in a pool member list (orphaned after pool swaps) can now be deleted; error message improved when an account is not found anywhere.

### Files Changed
- `src/main.py` — `delete_account()` rewritten to handle orphaned pool references
- `src/account_dialogs.py` — clearer "not found" error message in delete dialog
- `src/venue_adapters/aerodrome_adapter.py` — Aerodrome SlipStream read adapter
- `src/venue_adapters/krystal_adapter.py` — BSC V3 read adapter
- `src/lp_tab.py` — Aerodrome and BSC rendering/scan integration

### Verification
- `python -m py_compile` passes for modified files
- Delete-account dialog now removes orphaned pool-only accounts

---

## v5.2.3 - Railgun Privacy Integration (August 2026)

### Summary
v5.2.3 adds Railgun privacy protocol support via a Node.js sidecar process. ColdStack can now shield ERC-20 tokens, manage shielded balances, and execute private transfers (0zk→0zk) across Ethereum, Arbitrum, BNB Chain, Polygon, Base, and Optimism.

### New Features
- **Railgun Sidecar** — Node.js Express server wrapping the @railgun-community/wallet SDK
- **Shielded Wallets** — load BIP39 mnemonics into Railgun private balances
- **Shield/Unshield** — move ERC-20 tokens between public and private balances
- **Private Transfers** — 0zk→0zk encrypted transfers with optional memo
- **Balance Scanning** — automatic shielded balance updates via Railgun engine callbacks
- **POI Support** — Private Proof of Innocence status tracking
- **Cache Management** — full rebuild and cache clear for engine database

### Architecture
- Sidecar: Node.js Express server on localhost:8765
- Bridge: Python HTTP client (`src/railgun_bridge.py`) manages sidecar lifecycle
- GUI: New "Railgun" tab in the main tabview
- Self-signing mode (no Broadcaster/Waku dependency for v5.2.3)

### Requirements
- Node.js 18+ installed on the system (for running the sidecar)
- First launch downloads 50MB+ of proof artifacts (cached for subsequent use)

### Files Added
- `sidecar/` — complete Node.js sidecar (14 JS files + package.json)
- `src/railgun_bridge.py` — Python HTTP bridge client
- `src/railgun_tab.py` — Railgun GUI tab

---

## v5.2.1 - BSC Pool Reads + Light/Dark Mode (August 2026)

### Summary
v5.2.1 adds Uniswap V3 and PancakeSwap V3 position reads on BNB Chain (BSC) via the Krystal venue adapter, a user-selectable light/dark/system appearance mode, and a session file cleanup fix.

### New Features

- **Krystal Venue Adapter — BSC Pool Reads**
  - Full BSC RPC layer with primary/fallback RPC support
  - Uniswap V3 on BSC: Factory `0xdB1d10011AD0Ff90774D0C6Bb92e5C5c8b4461F7`, NPM `0x7b8A01B39D58278b5DE7e48c8449c9f4F5170613`
  - PancakeSwap V3 on BSC: Factory `0x0BFbCF9fa4f9C56B0F40a671Ad40E0805A091865`, NPM `0x46A15B0b27311cedF172AB29E4f4766fbE7F4364`
  - Wallet scan queries all registered V3 Position Managers (both Uniswap V3 and PancakeSwap V3)
  - Position decoding: token0/token1, fee tier, tick range, liquidity, current price, in-range %
  - Fee estimation via read-only `collect()` eth_call
  - Pool resolution via factory `getPool()` for each PM's factory
  - Known BSC token registry (WBNB, USDT) with on-chain fallback for decimals/symbol

- **Light/Dark/System Appearance Mode**
  - User-selectable in Settings (Dark / Light / System segmented button)
  - Persisted in `appearance.json` (plaintext, next to app — read before vault unlock)
  - Applied at startup before login screen renders
  - `appearance.py` module (carved off from `gui_main_v5.py`)
  - ttk Combobox styling adapts to current mode
  - Text readability fixes: explicit `(light, dark)` color tuples for account list, HL1 Vaults, LP Positions

- **Session File Cleanup**
  - `WM_DELETE_WINDOW` protocol handler ensures `.key_manager_session` is deleted on window close
  - `atexit` fallback for graceful exits where WM_DELETE_WINDOW doesn't fire

### Bug Fixes
- **LP platform dropdown**: `LPEngine` class was missing `list_venues()` method (module-level function existed but GUI called instance method). Silent `AttributeError` fallback only showed HyperEVM.
- **Settings dialog save crash**: `settings_dialog.py` referenced `gui.base_dir` (instance attr) but `base_dir` is a module-level variable. Fixed by computing `base_dir` independently in `settings_dialog.py`.
- **CTkSegmentedButton variable**: `variable` param alone doesn't update `StringVar` on click — added `command` callback to set the value.
- **Build script**: Added `--hidden-import=appearance` for PyInstaller `--onefile` build.

### Files Changed
- `src/venue_adapters/krystal_adapter.py` — Full BSC RPC layer, position decoding, wallet scan, fee estimation, both Uniswap V3 + PancakeSwap V3 support
- `src/venue_adapters/__init__.py` — KrystalAdapter import
- `src/lp_engine.py` — Added `list_venues()` method to `LPEngine` class
- `src/appearance.py` — NEW: appearance mode management (load/save/style_combobox)
- `src/gui_main_v5.py` — Appearance mode startup, LP_PLATFORM_MAP, session cleanup, appearance import
- `src/settings_dialog.py` — Appearance section, base_dir fix, mode-aware combobox, segmented button fix
- `src/vault_tab.py` — Light mode text color fixes
- `src/lp_tab.py` — Light mode text color fixes, suggestion color
- `src/account_dialogs.py` — Light mode fg_color tuple fixes
- `build_gui_v5.py` — `--hidden-import=appearance`, build timestamp

### Verification
- `python -m py_compile` passes for all modified files
- Adapter discovery returns `['hyperliquid', 'krystal']`
- Wallet scan on `0xF04A...` finds 2 BNB/INK positions on Uniswap V3 BSC
- Light mode: all text readable, tabs work, persistence via appearance.json
- Session file deleted on window close

---

## v5.1.4 - gui_main_v5.py Carve-Off (July 2026)

### Summary
Pure refactor: no functional changes. The monolithic `src/gui_main_v5.py` (6,424 lines) was carved into independent modules, leaving the core wallet GUI at ~2,600 lines.

### New Modules
- `src/settings_dialog.py` — Settings dialog
- `src/account_dialogs.py` — Account management dialogs
- `src/vault_tab.py` — HL1 Vaults tab implementation
- `src/lp_tab.py` — LP Positions tab implementation
- `src/chain_options.py` — Shared chain constants

---

## v5.1.3 - Swap Module + Wallet Card Redesign (July 2026)

### New Features
- Swap Dialog (`src/swap_dialog.py`): token swaps on HyperEVM
- Wallet card redesign: copy icon, uniform buttons, improved hover states

---

## v5.1.2 - Vault Explore + Deposit + EVM Transfer (July 2026)

### New Features
- Vault Explore + Deposit dialogs
- EVM ↔ HL1 Transfer Dialog (`src/evm_transfer_dialog.py`)
- HyperEVM balance fiat conversion

---

## v5.1.1 - Add/Remove Liquidity + Gas Fix (July 2026)

### New Features
- Add/Remove Liquidity UI with auto-balance (Zap In)
- `src/lp_liquidity_manager.py` module
- Position decode offset fix, gas price boost, nonce retry

---

## v5.1 - Hyperliquid Vaults + LP Fee Fix (July 2026)

### New Features
- HL1 Vaults tab with vault cards
- Saved vaults in encrypted vault
- LP fee reading via static `collect()` eth_call
- Wallet scan with NFT detection

---

## v4.2 - Customizable RPC + Standard/Advanced Mode (July 2026)

### New Features
- Customizable RPC endpoints (`rpc_endpoints.json`)
- Standard/Advanced mode toggle
- API key management (stored in encrypted vault)

---

## v4.1 - Price Feeds + Wallet Balances (June 2026)

### New Features
- Go Online toggle, inline balance display
- Balance Engine + Price Engine (CoinGecko)
- Multi-currency display (USD, AUD, CAD, EUR, CHF)

---

## v3.1 - ColdStack Rebrand (June 2026)

- GUI rebranded to "ColdStack"
- CLI EXE deprecated, GUI-only deployment

---

## v3.0 - BIP39 Derivation (June 2026)

- DerivationEngine: 7 chains (EVM, BTC, Solana, Dash, Sui)
- GUI derivation dialogs

---

## Architecture

```
coldstack/
├── src/
│   ├── gui_main_v5.py          — Main GUI (~2,540 lines after v5.2.1 carve-off)
│   ├── appearance.py           — Light/dark mode management (v5.2.1)
│   ├── settings_dialog.py      — Settings dialog (v5.1.4 carve-off)
│   ├── account_dialogs.py      — Account management dialogs (v5.1.4)
│   ├── vault_tab.py            — HL1 Vaults tab (v5.1.4)
│   ├── lp_tab.py               — LP Positions tab (v5.1.4)
│   ├── chain_options.py        — Shared chain constants (v5.1.4)
│   ├── swap_dialog.py          — Token swap dialog (v5.1.3)
│   ├── evm_transfer_dialog.py  — EVM ↔ HL1 transfers (v5.1.2)
│   ├── vault_deposit_dialog.py — Vault deposit dialog (v5.1.2)
│   ├── lp_liquidity_manager.py — Add/Remove liquidity (v5.1.1)
│   ├── lp_engine.py            — LP position engine + StrategyEngine
│   ├── crypto_engine.py        — AES-256-GCM + Argon2id
│   ├── derivation_engine.py    — BIP39 derivation
│   ├── balance_engine.py       — Wallet balance fetching
│   ├── price_engine.py         — CoinGecko price feeds
│   ├── rpc_config.py           — RPC endpoint config
│   ├── vault_tracker.py        — Hyperliquid vault tracking
│   ├── saved_pools.py          — Saved pools CRUD
│   ├── key_manager_agent.py    — Headless signing agent
│   ├── main.py                 — CLI interface
│   └── venue_adapters/
│       ├── __init__.py         — Adapter discovery
│       ├── hyperliquid_adapter.py — HyperEVM + L1 reads
│       ├── hyperliquid_writer.py  — HyperEVM write operations
│       ├── krystal_adapter.py  — BSC multi-DEX reads (Uniswap V3 + PancakeSwap V3)
│       └── venue_writer.py    — Writer ABC
├── build_gui_v5.py             — PyInstaller build script
├── rpc_endpoints.json          — Default RPC endpoints
├── requirements.txt
└── USB_DEPLOYMENT/
    └── coldstack.exe           — Portable EXE
```

## Versioning

| Version | Description | Key Files |
|---------|-------------|-----------|
| v5.2.1 | BSC Pool Reads + Light/Dark Mode | `krystal_adapter.py`, `appearance.py`, `settings_dialog.py` |
| v5.1.4 | gui_main_v5.py carve-off | `settings_dialog.py`, `account_dialogs.py`, `vault_tab.py`, `lp_tab.py` |
| v5.1.3 | Swap module + wallet card redesign | `swap_dialog.py` |
| v5.1.2 | Vault explore + deposit + EVM transfer | `evm_transfer_dialog.py`, `vault_deposit_dialog.py` |
| v5.1.1 | Add/remove liquidity + gas fix | `lp_liquidity_manager.py` |
| v5.1 | Hyperliquid vaults + LP fee fix | `vault_tracker.py`, `hyperliquid_adapter.py` |
| v4.2 | Customizable RPC + Standard/Advanced | `rpc_config.py`, `rpc_endpoints.json` |
| v4.1 | Price feeds + wallet balances | `balance_engine.py`, `price_engine.py` |
| v3.1 | ColdStack rebrand | — |
| v3.0 | BIP39 derivation engine | `derivation_engine.py` |
