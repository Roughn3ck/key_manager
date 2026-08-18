# ColdStack - Status Report

**Project:** https://github.com/Roughn3ck/key_manager
**Current Version:** v5.2.3 (Railgun Privacy Integration)
**Last Updated:** 2026-08-18

---

## Known Issues (v5.2.3)

### Aerodrome SlipStream Staked Positions — Wallet Scan Workaround

**Status:** Temporary workaround — requires manual NFT ID entry

Aerodrome SlipStream positions staked in a CL gauge cannot be discovered via wallet scan. When a position is staked, the NFT is transferred to the gauge contract, so `balanceOf(wallet)` returns 0. The adapter has no way to enumerate all gauges from the Aerodrome Voter contract (no `poolLength()` or `pools(uint256)` exposed).

**Current workaround:** Enter the NFT token ID manually in the Position ID field. The token ID is visible on the Aerodrome dashboard (aerodrome.finance → Dashboard → look for `#<number>` next to "Deposit"). A popup guides the user to this when an Aerodrome scan returns 0 positions.

**Future fix options (not yet viable):**
- Transfer event log scanning: requires `eth_getLogs` over large block ranges. Public Base RPCs cap at ~10k blocks per query (413 error above). Scanning 6 months of history would need ~800 sequential calls — too slow/unreliable. A paid RPC (Alchemy/QuickNode/Infura) with higher log limits would make this feasible.
- Aerodrome subgraph: the Aerodrome frontend uses a single Multicall3 batch call with 8192 bytes of custom bytecode sent to the wallet address. This likely relies on EIP-7702 (EOA delegation) or a similar mechanism that only works if the wallet has code. No public subgraph endpoint has been identified.
- Caching (current partial solution): once a user enters a token ID manually and saves the pool, future wallet scans find the position via the saved-pools gauge lookup (`_find_staked_positions_via_saved_pools`).

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
