# AGENTS.md â€” ColdStack

Forge's project rules for working on ColdStack. Read this file at the start of every session.

## What This Is

ColdStack is a portable, offline-first cryptocurrency key vault (Windows GUI app, `coldstack.exe`) plus a headless signing agent. It stores BIP39 mnemonics, addresses, and private keys encrypted with AES-256-GCM + Argon2id, and optionally (user-toggled "Go Online") fetches read-only balances/prices and LP positions. Production version is v5.3.15.

Forge runs via OpenRouter using Kimi 2.7 on Ollama. Forge is the builder â€” Slater (CTO) is the architect. Forge executes, Slater reviews. ColdStack production version is v5.3.15.

## Commands

```bash
# Run the GUI from source (primary dev loop)
python src/gui_main_v5.py

# CLI (script mode only â€” CLI EXE was deprecated in v3.1)
python src/main.py <command>            # e.g. derive-address, accounts, import-csv, gen-password

# Headless signing agent (binds localhost:8842)
KEY_MANAGER_PASSWORD="***" python src/key_manager_agent.py --vault key_vault.encrypted --serve --port 8842

# Build the portable EXE â†’ USB_DEPLOYMENT/coldstack.exe (with backup to backups/)
python build_gui_v5.py

# Syntax-check a source file after editing (do this before building)
python -m py_compile src/<file>.py

# Tests
python test_derivation.py               # BIP39 derivation parity across chains
```

## âš ï¸ CRITICAL: Build Protocol

**When to build the EXE:**
- After ALL changes to `src/` files are complete and verified
- Only when the task is the FINAL step in a multi-prompt workflow
- After running `python -m py_compile` on every modified file
- A stale EXE is worse than no EXE â€” never leave `USB_DEPLOYMENT/coldstack.exe` stale

**When NOT to build:**
- During intermediate prompts in a multi-prompt workflow (each prompt will say "do NOT rebuild")
- If the build fails â€” fix the error before considering the task done
- For experimental changes that haven't been verified

**When to push to git:**
- Only after the final prompt completes AND the build succeeds
- Never push broken builds
- After pushing, verify the repo is clean: `git status`

**Pre-ship verification (sidecar):**
- Run `node --check sidecar/src/**/*.js` â€” every sidecar JS file must pass before shipping
- Boot smoke test: `node src/server.js` from the sidecar dir, poll `http://127.0.0.1:8765/health` until `{"status":"ok"}` (â‰¤30s), then kill
- The sidecar only ships if it boots cleanly from a cold start

**Pre-ship verification (dialogs):**
- Any train that touches a dialog must run a headless widget-construction smoke test (stub gui â†’ build dialog â†’ assert no exception + expected widgets). py_compile cannot catch widget-creation-order bugs (v5.3.8 lesson).

**Deploy (every deploy):**
- Sync `rpc_endpoints.json` (repo root) next to the EXE in every live portfolio folder, together with the EXE â€” a stale deployed JSON silently shadows corrected code defaults (v5.3.9 lesson). The loader now self-heals deprecated endpoints, but shipping the current JSON keeps config explicit.

**When NOT to push to git:**
- Intermediate prompts (each will say "do NOT push")
- Failed builds
- Experimental changes that haven't been verified by running the GUI

**GitHub releases:**
- Review the existing release formatting at the repo's releases page before creating a new one
- If replacing an existing version with minor bug fixes: replace the release asset file
- If moving to a new version: create a new release with the new v tag
- After all prompts complete: create a backup of src and related files in `backups/<version>/`

## Versioning Convention

New versions add a `_v<N>` suffix on new/modified GUI files (`gui_main_v4.py`, `gui_main_v5.py`). Previous-version GUI files are preserved untouched as the baseline â€” do not edit or delete them. Shared core modules (`crypto_engine.py`, `derivation_engine.py`, `backup_engine.py`, `main.py`, `key_manager_agent.py`) are extended backward-compatibly rather than versioned. The single shipped EXE in `USB_DEPLOYMENT/` is always overwritten with the latest version build; the previous EXE is backed up first.

The vault schema is additive-only and versioned via a `schema_version` field. Old vaults open without migration and default to prior-version behavior for any missing field. Never break old vaults.

## Architecture

### Vault: the single source of truth
`key_vault.encrypted` (AES-256-GCM, Argon2id KDF: 64MB/3 iterations/4 lanes) holds everything. It is co-located with the app: project root in script mode, `os.path.dirname(sys.executable)` when frozen (`PortableKeyManager` resolves this via `base_dir`). After unlock, `KeyManager.address_db` holds the decrypted in-memory tree `{ version, pools, accounts, mnemonics, private_keys }`. The master password lives in memory only during the active session â€” never written to disk, cleared on lock/auto-lock (5-min timeout, optional "Do not autolock" checkbox).

### Module layout
- `gui_main_v5.py` â€” CustomTkinter dark-theme GUI. Primary interface. Core wallet view only (~2,600 lines). CTkTabview with Wallet / HL1 Vaults / LP Positions tabs; tab bodies live in dedicated modules below.
- `settings_dialog.py` (v5.1.4 NEW) â€” Settings dialog: Go Online toggle, display currency, Standard/Advanced mode switching, RPC endpoint editor, API keys. Carved out of `gui_main_v5.py` to keep the main GUI focused.
- `account_dialogs.py` (v5.1.4 NEW) â€” Account management dialogs: add/delete account, add address/mnemonic/private key, derivation, derive-all-chains, import CSV/Excel, initialize vault, change password, confirm address removal.
- `vault_tab.py` (v5.1.4 NEW) â€” `VaultTab` class containing the HL1 Vaults tab UI, fetch/render, saved vault CRUD, and deposit/explore actions.
- `lp_tab.py` (v5.1.4 NEW) â€” `LPTab` class containing the LP Positions tab UI, scan/fetch, position card rendering, save/remove pool, and fee/collect/compound/close dialogs.
- `chain_options.py` (v5.1.4 NEW) â€” Shared `CHAIN_OPTIONS` and `DERIVATION_CHAINS` constants used by the GUI and account dialogs.
- `crypto_engine.py` â€” AES-256-GCM + Argon2id (shared, unchanged since v1).
- `derivation_engine.py` â€” BIP39 â†’ address/key derivation for 7 chains (EVM, BTC Taproot/SegWit/Legacy, SOL, DASH, SUI).
- `main.py` â€” Click-based CLI (script mode only).
- `key_manager_agent.py` â€” Headless HTTP/JSON signing server (localhost only). This is the only path that signs/broadcasts transactions; the GUI and LP writer never hold private keys â€” they call the agent.
- `balance_engine.py` â€” Read-only wallet balances via public RPC (stdlib `urllib.request`, no new deps). 17 chains, ERC-20 support, HyperEVM (chain 999) + Hyperliquid L1 spot. Dispatcher routes chain types â†’ fetch functions.
- `price_engine.py` â€” CoinGecko prices, 60s in-memory cache, never persisted.
- `rpc_config.py` + `rpc_endpoints.json` â€” User-editable public RPC endpoints (fallback to hardcoded defaults if missing/malformed). API keys are NOT in this JSON â€” they live in the encrypted vault `config.api_keys` and are injected at runtime.
- `lp_engine.py` â€” LP position aggregation facade (`LPPosition` dataclass, `VenueAdapter` ABC, `StrategyEngine`, `LPEngine`). Read-only.
  - `venue_adapters/` â€” One adapter per venue, auto-registered on import (`__init__.py`). `hyperliquid_adapter.py` (read: HyperEVM NFT Position Manager + L1 perp/spot), `hyperliquid_writer.py` (write: wrap/unwrap/approve/open/increase/decrease/collect/close/rebalance, signs via agent on :8842), `bsc_adapter.py` (read: BSC Uniswap V3 + PancakeSwap V3 via direct RPC reads), `bsc_writer.py` (write: collect/close for BSC V3 positions, signs via agent on :8842), `venue_writer.py` (writer ABC).
- `vault_tracker.py` â€” Read-only Hyperliquid vault positions via `/info` API.
- `saved_pools.py` â€” Saved pools CRUD inside the encrypted vault (`address_db["saved_pools"]â€); stores public identifiers (address, token ID, venue, pool, pair) only â€” no private keys. Lets LP scans skip the expensive token-ID scan.
- `lp_liquidity_manager.py` (v5.1.1 NEW) â€” Add/Remove/Edit liquidity dialogs. Separated from `gui_main_v5.py` for maintainability. Contains `AddLiquidityDialog` (with auto-balance/Zap In), `RemoveLiquidityDialog` (with percentage slider), and `EditPositionDialog` (stub for v5.2).
- `build_gui_v5.py` â€” PyInstaller build script (entry point + all hidden imports; every new src module that the GUI imports must be added here as a `--hidden-import` or the frozen EXE will fail to import it at runtime). **Critical:** the full HTTPS/SSL stack must be included (`ssl`, `_ssl`, `http.client`, `socket`, `_socket`, `urllib`, `urllib.request`, `urllib.error` + `--collect-submodules=urllib`) or the EXE will fail on all `https://` URLs.

### Online/offline boundary
Offline by default â€” zero network requests unless the user enables "Go Online" in Settings. Balance/price/LP engines raise `OfflineError` when offline and the GUI greys out their controls. Only public addresses are queried; private keys and mnemonics never leave the vault. Prices are in-memory only (never written to disk). The LP engine is read-only; the writer is a tool requiring explicit per-operation GUI confirmation, never autonomous.

### LP fee reading (v5.1)
Real uncollected fees on Project X / HyperEVM are read via a static `eth_call` to `collect((uint256,address,uint128,uint128))` (selector `0xfc6f7865`) on the Project X PositionManager at `0xeaD19AE861c29bBb2101E834922B2FEee69B9091`. This returns the same values shown in the Project X UI tooltip. Do not re-enable the old `feeGrowthGlobal` delta or `PROJECT_X_FEE_ESTIMATION_ENABLED` approach â€” it overcounts. The `fees_note` field on `LPPosition` is retained for backward compatibility but should not be populated by the Hyperliquid adapter.

### Railgun Sidecar (v5.2.3)
- `sidecar/` â€” Node.js Express server wrapping @railgun-community/wallet SDK. Runs on localhost:8765.
- `sidecar/src/routes/` â€” engine, wallet, balances, transfer, poi, cache, health routes
- `sidecar/src/state.js` â€” shared sidecar state (engine, wallets, balances, scan status)
- `sidecar/src/db.js` â€” LevelDOWN database for encrypted wallet storage
- `sidecar/src/artifacts.js` â€” ArtifactStore for proof artifact downloads
- `sidecar/src/networks.js` â€” ColdStack chain name â†’ Railgun NetworkName mapping
- `sidecar/src/callbacks.js` â€” balance and scan progress callbacks
- `sidecar/src/utils.js` â€” transaction helpers (gas, signing, approval)
- `src/railgun_bridge.py` â€” Python HTTP client managing sidecar lifecycle
- `src/railgun_tab.py` â€” CustomTkinter Railgun tab (sidecar status, wallet, balances, transactions)

### Key contract addresses (HyperEVM, Chain 999)
- WHYPE: `0x5555555555555555555555555555555555555555`
- UBTC: `0x9FDBdA0A5e284c32744D2f17Ee5c74B284993463`
- Pool Factory: `0xb1c0fa0b789320044a6f623cfe5ebda9562602e3`
- Position Manager: `0xead19ae861c29bbb2101e834922b2feee69b9091`
- WHYPE/UBTC Pool (3000 bps fee): `0x3a36b04bcc1d5e2e303981ef643d2668e00b43e7`
- Swap Router: `0x1ebdfc75ffe3ba3de61e7138a3e8706ac841af9b` (exactInputSingle now implemented in v5.1.1)
- RPC: `https://rpc.hyperliquid.xyz/evm`
- Hyperliquid Info API: `https://api.hyperliquid.xyz/info`

### App modes
- **Standard mode** (default): Hides RPC endpoint editing and API key fields. Basic functionality visible.
- **Advanced mode**: Unlocks custom RPC endpoints, API key configuration, and additional settings.
- LP fee buttons (Collect/Compound) should show in BOTH modes â€” fee operations are basic LP management, not advanced features.

## ColdTrack Module (v5.3.0+)

ColdTrack is the financial ledger module â€” the monetization layer of ColdStack. It lives as a subpackage at `src/coldtrack/` and appears as a tab in the main CTkTabview.

### What It Does
- Tracks portfolios, accounts, transactions, LP positions, and holdings
- SQLite database (`coldtrack.db`) co-located with the vault
- User-initiated sync from vault â†’ ColdTrack DB (controlled disclosure)
- Multi-currency support (USD, CAD, AUD, EUR)
- Tax-ready schema (cost basis, realized gains, holding periods)

### What It Does NOT Do (Yet)
- Transaction import from on-chain (Phase 2 â€” v5.3.15)
- LP position tracking with P&L (Phase 3 â€” v5.3.4)
- Holdings & cost basis (Phase 4 â€” v5.3.5)
- Tax report generation (Phase 5 â€” v5.3.6)

### Module Layout
- `src/coldtrack/db.py` â€” SQLite schema (Kimi's v3.0 as-built: PORTFOLIOSâ†’ACCOUNTSâ†’LP_POSITIONS/TRANSACTIONS/â€¦, plus `FEE_EVENTS`/`CAPITAL_EVENTS`/`POOL_GROUPS` and the LP_POSITIONS identifier columns â€” additive/idempotent), connection management, CRUD
- `src/coldtrack/importer.py` â€” Bridge: vault address_db â†’ ColdTrack DB
- `src/coldtrack/tab.py` â€” ColdTrack tab UI (CustomTkinter) + "Export Sentinel View" button
- `src/coldtrack/sentinel_export.py` (v5.3.13; emit-fix v5.3.15) â€” **port of `kimi/coldtax/export_strategy_view.py`** (credited): materializes the merged `strategy_view.json` (Pool records from `LP_POSITIONS` under 'Executive Mind', KP positions from the `kitandpaul` account with `entry {usd,date,token0_amt,token1_amt,fees_claimed_usd}` in canonical token0/token1 order via alias-tolerant `_same_token`, plus `nfpm`/`liquidity` from the NOTES config blob, `pool_groups[]` passthrough, `FEE_EVENTS`/`CAPITAL_EVENTS` â†’ snake_case with `position_id` as the sentinel pool-id string). Registry seam = `LP_POSITIONS` + `POOL_GROUPS`. Multi-DB `--db PATH`/`;`-separated, `$ARGUS_DB_PATH` env fallback, default = Pack + K&P DBs. Atomic tmp + `os.replace`; graceful empties; stdlib only; usable as module `main` / `coldtrack export`. Contract v1 reserved keys: `view_version, generated_by, generated_at, source_db, fee_events, capital_events, pool_groups`.
- Tests: `test_sentinel_export.py` (temp-DB fixtures in Kimi's schema shape â†’ assert merged contract; mirrors the sentinel's `coldtrack_view_smoke_test.js`), `test_coldtrack_tab_widgets.py` (headless widget-construction smoke)

### Security
- ColdTrack DB stores financial data, NOT private keys
- No signing capability â€” ColdTrack is read-only
- Bridge is user-initiated, not automatic
- Shielded/GSS transactions are excluded by design
- Private keys and mnemonics NEVER cross the bridge

### Schema
The schema is v3.0, designed by Kimi (CFO). Full SQL in `kimi/briefs/coldtrack-schema-v3.md`. Key principles:
- PORTFOLIOS â†’ ACCOUNTS â†’ (TRANSACTIONS, LP_POSITIONS, etc.)
- Free-text TYPE/CATEGORY/PLATFORM/CHAIN (no CHECK constraints)
- Multi-currency values per row (VALUE_USD, VALUE_CAD, VALUE_AUD, VALUE_EUR)
- IS_PRIVACY_SHIELDED flag on ACCOUNTS for GSS
- TAX_JURISDICTION on PORTFOLIOS (free-text)
- Schema is additive â€” new columns are nullable, old data survives

### Adding New ColdTrack Modules
When adding a new module to `src/coldtrack/`:
1. Create the module file
2. Add it to `build_gui_v5.py` as a `--hidden-import`
3. Update this section

### Module layout (updates)
- `gui_main_v5.py` â€” CTkTabview with Wallet / HL1 Vaults / LP Positions / Railgun / ColdTrack tabs; VERSION constant (single source of truth)
- `coldtrack/` â€” SQLite ledger subpackage (db.py, importer.py, tab.py, sentinel_export.py)
- `ed25519_utils.py` â€” Shared Ed25519 math primitives, base58 encoding, SLIP-0010 HD derivation (used by derivation_engine and key_manager_agent)

## Coding Conventions

- Type hints on all function signatures; docstrings on all public methods.
- `pathlib.Path` for filesystem operations.
- No plain text sensitive data in logs or print statements.
- Error handling: catch specific exceptions, never bare `except:`.
- GUI: All widget references stored as `self.*`. Guard with `hasattr()` in callbacks that might fire before widgets exist. Don't start timers until referenced widgets are created.
- Session timeout: 5 minutes of inactivity triggers auto-lock.
- Never call `FreeConsole()` in script mode â€” only when frozen (EXE).
- When adding a GUI version: create `gui_main_v<N>.py` (copy forward), a matching `build_gui_v<N>.py`, add any new `src/` modules to the build's hidden imports, and update this file + `README.md` + `STATUS.md`.

## Security Rules

- Never log or print decrypted mnemonics, private keys, or addresses in production code.
- Always clear clipboard after copy operations where possible.
- Mnemonics must auto-hide after reveal (5-minute max display).
- Password fields must use `show="*"` masking.
- No password storage â€” password is held in memory only during active session.
- On lock/session expiry: clear `current_password`, `revealed_mnemonics`, and `address_db` references.
- LP Engine is read-only â€” never add signing or transaction execution capabilities to the adapter.
- Go Online is OFF by default â€” never change this default.
- The writer signs via the key_manager_agent (localhost:8842) â€” never load private keys into the GUI or writer directly.

## Dependencies

`requirements.txt` lists runtime deps (`cryptography`, `argon2-cffi`, `customtkinter`, `hdwallet`, `mnemonic`, `click`, `rich`, `pyyaml`, `openpyxl`, `pyperclip`). The headless agent additionally needs `pycryptodomex`/`coincurve` for signing. The build needs `PyInstaller`. Network modules use only stdlib `urllib.request` â€” do not add new network dependencies.

## Known Issues (v5.1)

1. HyperEVM swap router address not yet confirmed â€” `swap()` raises `NotImplementedError`; cross-ratio rebalances and compound-with-swap deferred
2. Saved pools are now stored inside `key_vault.encrypted`, but a one-time migration of any legacy `saved_pools.json` may still be needed on very old deployments
3. G5 `lp_address` in `strategy.json` is 66 chars (not a valid 42-char EVM address) â€” needs clarification for HyperEVM adapter testing
4. Compound/Collect fees may fail with "Agent error (broadcast_transaction) 'result'" if the embedded key_manager_agent is not running or not unlocked â€” ensure vault is unlocked and the agent thread started

## Testing

- `test_derivation.py` â€” BIP39 derivation parity + multi-chain tests
- `python3 -m py_compile src/<file>.py` â€” syntax check after editing any source file
- LP Engine: `python3 -c "from lp_engine import LPEngine; e = LPEngine(online_mode=False); e.fetch_all_positions('0x...')"` â†’ should raise `OfflineError`
- Always run `python3 -m py_compile src/<file>.py` after editing any source file

## Project State Tracking

`STATUS.md` tracks per-version changelogs, known issues, and next steps. `README.md` has the full project documentation. Both are updated with each release â€” keep them in sync with code changes.