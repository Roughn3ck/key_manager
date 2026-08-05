# ColdStack v5.2.1 — Krystal Skeleton + Light/Dark Mode

## Highlights

- **Krystal venue adapter skeleton** — first step toward multi-chain LP aggregation. Registers as venue key `krystal`, supports BSC/Ethereum/Arbitrum/Solana, and appears in the LP platform dropdown as "Krystal (BSC)". Full RPC reads coming in the next v5.2 prompt.
- **Light / Dark / System appearance mode** — user-selectable in Settings, persisted in the encrypted vault, live-switching across the entire UI.
- **BNB Chain RPC defaults** — `bsc-dataseed.binance.org` primary + Ankr fallback.

## Files Added

- `src/venue_adapters/krystal_adapter.py` — Krystal skeleton adapter
- `v5.2.1-krystal-platform-map.md` — Prompt artifact
- `release_notes_v5.2.1.md` — This release notes file

## Files Changed

- `src/gui_main_v5.py` — version string v5.2.1, `LP_PLATFORM_MAP` includes Krystal, appearance mode load/save
- `src/settings_dialog.py` — mode-aware combobox styling, Appearance section, save appearance mode
- `src/rpc_config.py` — BSC endpoint + fallback
- `src/venue_adapters/__init__.py` — Krystal adapter import
- `build_gui_v5.py` — version strings + backup tag updated to v5.2.1
- `AGENTS.md`, `README.md`, `STATUS.md` — version + feature updates
- `USB_DEPLOYMENT/README.md`, `USB_DEPLOYMENT/launch.bat` — version strings

## Verification

- `python -m py_compile src/gui_main_v5.py src/settings_dialog.py src/venue_adapters/krystal_adapter.py src/rpc_config.py build_gui_v5.py` passes
- Adapter discovery: `['hyperliquid', 'krystal']`
- GUI imports cleanly; unlock screen shows v5.2.1
- EXE rebuilt: `USB_DEPLOYMENT/coldstack.exe` (45.25 MB)

## Security

- No new network behavior enabled by default — Krystal adapter read methods raise `NotImplementedError` until Prompt 2.
- Appearance mode is a UI-only preference stored in the encrypted vault.
- No private keys are touched by any v5.2.1 change.
