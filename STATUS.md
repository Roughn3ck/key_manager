# ColdStack - Status Report

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
**Last Updated:** 2026-06-25
**Current Version:** v4.1 (Price Feeds + Wallet Balances + Go Online Toggle)

## Versioning

| Version | Description | Key Files |
|---------|-------------|-----------|
| v1 | Original GUI - display-only | `src/gui_main.py`, `build_gui.py` |
| v2 | GUI Add functionality | `src/gui_main_v2.py`, `build_gui_v2.py` |
| v3 | GUI BIP39 derivation | `src/gui_main_v3.py`, `build_gui_v3.py`, `src/derivation_engine.py` |
| v3.1 | ColdStack rebrand + Check for Updates | `src/gui_main_v3_1.py`, `build_gui_v3_1.py` |
| v4.1 | Price Feeds + Wallet Balances + Go Online | `src/gui_main_v4.py`, `build_gui_v4.py`, `src/balance_engine.py`, `src/price_engine.py` |

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
*Status report updated June 25, 2026. v4.1 released - Price Feeds + Wallet Balances + Go Online Toggle.*