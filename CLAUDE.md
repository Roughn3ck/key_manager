# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

ColdStack is a portable, offline-first cryptocurrency key vault (Windows GUI app, `coldstack.exe`) plus a headless signing agent. It stores BIP39 mnemonics, addresses, and private keys encrypted with AES-256-GCM + Argon2id, and optionally (user-toggled "Go Online") fetches read-only balances/prices and LP positions. Production version is v5.1.

## Commands

```bash
# Run the GUI from source (primary dev loop)
python src/gui_main_v5.py

# CLI (script mode only — CLI EXE was deprecated in v3.1)
python src/main.py <command>            # e.g. derive-address, accounts, import-csv, gen-password

# Headless signing agent (binds localhost:8842)
KEY_MANAGER_PASSWORD="..." python src/key_manager_agent.py --vault key_vault.encrypted --serve --port 8842

# Build the portable EXE → USB_DEPLOYMENT/coldstack.exe (with backup to backups/)
python build_gui_v5.py

# Syntax-check a source file after editing (do this before building)
python -m py_compile src/<file>.py

# Tests
python test_derivation.py               # BIP39 derivation parity across chains
```

**Critical build rule:** after ANY change to `src/`, rebuild the EXE with `python build_gui_v5.py`. `USB_DEPLOYMENT/coldstack.exe` must never be left stale — a stale EXE is worse than no EXE. The build script cleans `build/`/`dist/`, runs PyInstaller with all hidden imports, backs up the prior EXE to `backups/`, and copies the new one into `USB_DEPLOYMENT/`. If the build fails, fix it before considering the task done.

## Versioning convention

New versions add a `_v<N>` suffix on new/modified GUI files (`gui_main_v4.py`, `gui_main_v5.py`). Previous-version GUI files are preserved untouched as the baseline — do not edit or delete them. Shared core modules (`crypto_engine.py`, `derivation_engine.py`, `backup_engine.py`, `main.py`, `key_manager_agent.py`) are extended backward-compatibly rather than versioned. The single shipped EXE in `USB_DEPLOYMENT/` is always overwritten with the latest version build; the previous EXE is backed up first.

The vault schema is additive-only and versioned via a `schema_version` field. Old vaults open without migration and default to prior-version behavior for any missing field. Never break old vaults.

## Architecture

### Vault: the single source of truth
`key_vault.encrypted` (AES-256-GCM, Argon2id KDF: 64MB/3 iterations/4 lanes) holds everything. It is co-located with the app: project root in script mode, `os.path.dirname(sys.executable)` when frozen (`PortableKeyManager` resolves this via `base_dir`). After unlock, `KeyManager.address_db` holds the decrypted in-memory tree `{ version, pools, accounts, mnemonics, private_keys }`. The master password lives in memory only during the active session — never written to disk, cleared on lock/auto-lock (5-min timeout, optional "Do not autolock" checkbox).

### Module layout
- `gui_main_v5.py` — CustomTkinter dark-theme GUI. Primary interface. CTkTabview with Wallet / HL1 Vaults / LP Positions tabs.
- `crypto_engine.py` — AES-256-GCM + Argon2id (shared, unchanged since v1).
- `derivation_engine.py` — BIP39 → address/key derivation for 7 chains (EVM, BTC Taproot/SegWit/Legacy, SOL, DASH, SUI).
- `main.py` — Click-based CLI (script mode only).
- `key_manager_agent.py` — Headless HTTP/JSON signing server (localhost only). This is the only path that signs/broadcasts transactions; the GUI and LP writer never hold private keys — they call the agent.
- `balance_engine.py` — Read-only wallet balances via public RPC (stdlib `urllib.request`, no new deps). 17 chains, ERC-20 support, HyperEVM (chain 999) + Hyperliquid L1 spot. Dispatcher routes chain types → fetch functions.
- `price_engine.py` — CoinGecko prices, 60s in-memory cache, never persisted.
- `rpc_config.py` + `rpc_endpoints.json` — User-editable public RPC endpoints (fallback to hardcoded defaults if missing/malformed). API keys are NOT in this JSON — they live in the encrypted vault `config.api_keys` and are injected at runtime.
- `lp_engine.py` — LP position aggregation facade (`LPPosition` dataclass, `VenueAdapter` ABC, `StrategyEngine`, `LPEngine`). Read-only.
- `venue_adapters/` — One adapter per venue, auto-registered on import (`__init__.py`). `hyperliquid_adapter.py` (read: HyperEVM NFT Position Manager + L1 perp/spot), `hyperliquid_writer.py` (write: wrap/unwrap/approve/open/increase/decrease/collect/close/rebalance, signs via agent on :8842), `venue_writer.py` (writer ABC).
- `vault_tracker.py` — Read-only Hyperliquid vault positions via `/info` API.
- `saved_pools.py` — `saved_pools.json` next to the vault; stores public identifiers (address, token ID, venue, pool, pair) only — no private keys. Lets LP scans skip the expensive token-ID scan.
- `build_gui_v5.py` — PyInstaller build script (entry point + all hidden imports; every new src module that the GUI imports must be added here as a `--hidden-import` or the frozen EXE will fail to import it at runtime).

### Online/offline boundary
Offline by default — zero network requests unless the user enables "Go Online" in Settings. Balance/price/LP engines raise `OfflineError` when offline and the GUI greys out their controls. Only public addresses are queried; private keys and mnemonics never leave the vault. Prices are in-memory only (never written to disk). The LP engine is read-only; the writer is a tool requiring explicit per-operation GUI confirmation, never autonomous.

### LP fee caveat (v5.1)
Project X / HyperEVM uncollected-fee estimation from RPC tick storage does not produce correct values and is disabled behind `PROJECT_X_FEE_ESTIMATION_ENABLED = False` in `hyperliquid_adapter.py`. HyperEVM positions instead carry a `fees_note` ("Collect fees to report on fee income") shown in the GUI. Collection-based fee tracking is the planned replacement. Don't "fix" the fee math by re-enabling that flag — the helper functions are retained intentionally.

## Conventions

- Type hints on all signatures; docstrings on public methods; `pathlib.Path` for filesystem ops.
- Catch specific exceptions, never bare `except:`.
- GUI: store all widget refs as `self.*`; guard them with `hasattr()` in callbacks that may fire before widgets exist; don't start timers until referenced widgets are created.
- Never log/print decrypted mnemonics, private keys, or addresses.
- Never call `FreeConsole()` in script mode — only when frozen (EXE).
- When adding a GUI version: create `gui_main_v<N>.py` (copy forward), a matching `build_gui_v<N>.py`, add any new `src/` modules to the build's hidden imports, and update `.clinerules` / `README.md` / `STATUS.md`.

## Project state tracking

`.clinerules` holds the Cline-oriented project rules and version table; `STATUS.md` tracks per-version changelogs, known issues, and next steps. Both are updated with each release — keep them in sync with code changes.

## Dependencies

`requirements.txt` lists runtime deps (`cryptography`, `argon2-cffi`, `customtkinter`, `hdwallet`, `mnemonic`, `click`, `rich`, `pyyaml`, `openpyxl`, `pyperclip`). The headless agent additionally needs `pycryptodomex`/`coincurve` for signing. The build needs `PyInstaller`. Network modules use only stdlib `urllib.request` — do not add new network dependencies.