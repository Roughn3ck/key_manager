# ColdStack - Status Report

## v3.1 - ColdStack Rebrand + Check for Updates (June 2026)

### New Features
- **Brand Rebrand**: GUI-facing strings changed from "Key Manager" to "ColdStack" (window title, login screen, version label, module/class docstrings). Main GUI class renamed `KeyManagerGUI` -> `ColdStackGUI`. `PortableKeyManager` class name unchanged (wraps core `KeyManager`).
- **Check for Updates Button**: Added to the status bar (right side, between session timer and Lock button). User-initiated only - shows a confirm dialog, then makes a single `urllib.request` call to the GitHub releases API in a background `threading.Thread`. Results are marshaled back to Tkinter via `self.root.after(0, ...)`. No background polling. Offline by default.
- **EXE Rename**: GUI EXE output changed from `key_manager_gui.exe` to `coldstack.exe`.
- **CLI EXE Deprecated**: The `key_manager.exe` CLI executable is no longer built or distributed in `USB_DEPLOYMENT/` as of v3.1. CLI source remains for script-mode use (`python src/main.py`).
- **Build Script Fixes**: `copy_to_usb_deployment()` backup name fixed from `key_manager_gui_v2.exe` to `key_manager_gui_v3.exe`. `create_launcher_script()` content updated to ColdStack branding.
- **Vault Path Policy Change**: As of v3.1, the vault (`key_vault.encrypted`) always lives in the root directory where ColdStack runs (project root in script mode, EXE directory in frozen mode) - not in `~/.key_manager/`.
- **No New Dependencies**: Update check uses stdlib `urllib.request` and `json` only.

### Files Changed
- `src/gui_main_v3_1.py` (NEW) - v3.1 GUI with ColdStack rebrand + update check + local vault path
- `build_gui_v3_1.py` (NEW) - v3.1 PyInstaller build script (outputs `coldstack.exe`)
- `key_manager_gui_v3_1.spec` (NEW) - v3.1 PyInstaller spec (name='coldstack')
- `.clinerules` (UPDATED) - v3.1 versioning table, project structure, build/run instructions, CLI EXE deprecation
- `README.md` (UPDATED) - ColdStack branding, v3.1 changelog, download table, CLI EXE deprecation, vault path update
- `STATUS.md` (UPDATED) - v3.1 changelog entry
- Backups created: `backups/gui_main_v3.py`, `backups/build_gui_v3.py`, `backups/key_manager_gui_v3.spec`, `backups/README_v3.md`, `backups/STATUS_v3.md`, `backups/.clinerules_v3`, `backups/key_manager_gui_v3.exe`

### Testing
- Script mode: `python src/gui_main_v3_1.py` - login screen shows "ColdStack" title and v3.1 version label
- Window title bar: "ColdStack - Secure Crypto Key Vault"
- "Check for Updates" button visible in status bar after login
- No stale "Key Manager" brand strings in the GUI source
- EXE built successfully: `USB_DEPLOYMENT/coldstack.exe` (44.48 MB)
- Existing vaults at `.key_manager/` can be manually copied to the project root for use

## v3.0 - BIP39 Mnemonic Derivation Engine (June 2026)

### New Features
- **Derivation Engine** (`src/derivation_engine.py`): Derives addresses and private keys from BIP39 mnemonics using standard HD wallet paths (BIP32/BIP44/BIP49/BIP84/BIP86).
  - Supported chains: EVM (Ethereum/Arbitrum/Base), BTC Taproot (bc1p), BTC SegWit (bc1q), BTC Legacy (1...), SOL (Solana), DASH (Dash), SUI (Sui)
  - Manual bech32/bech32m encoding for BTC SegWit/Taproot addresses
  - EIP-55 checksummed EVM addresses via keccak256
  - Mnemonic validation and generation (12/24 words)
- **GUI Derivation Dialogs**: "Derive Addresses" and "Derive All Chains" buttons in the mnemonic section of the account detail panel.
- **Enhanced Private Key Display**: Shows derivation path, source indicator (manual/derived), and link icon for keys derived from the account's stored mnemonic.
- **CLI Commands**: `derive-address`, `generate-mnemonic`, `validate-mnemonic` added to the CLI.
- **Schema Enhancement**: Private keys and addresses now optionally store `source`, `derivation_path`, `address_index`, and `derived_address` metadata. Fully backward-compatible.



**Project Location:** `B:\Github\key_manager\`
**Last Updated:** 2026-06-25
**Assigned Agent:** Vault (headless agent), Cline (GUI)
**Current Version:** v3.1 (GUI ColdStack rebrand + update check)

## Versioning

| Version | Description | Key Files |
|---------|-------------|-----------|
| v1 | Original GUI - display-only (addresses, mnemonics, private keys) | `src/gui_main.py`, `build_gui.py` |
| v2 | GUI Add functionality - create accounts, add addresses, add mnemonics via dialogs | `src/gui_main_v2.py`, `build_gui_v2.py` |
| v2.4 | Headless agent for AI-driven signing | `src/key_manager_agent.py` |
| v3 | GUI BIP39 derivation - derive addresses/keys from mnemonics | `src/gui_main_v3.py`, `build_gui_v3.py`, `src/derivation_engine.py` |
| v3.1 | ColdStack rebrand + Check for Updates button | `src/gui_main_v3_1.py`, `build_gui_v3_1.py`, `key_manager_gui_v3_1.spec` |

### Versioning Rules

1. **New versions** introduce a `_v<N>` suffix to all new/modified working files
2. **v1 files are preserved** - the originals remain untouched as the v1 baseline
3. **Core v1 files are backed up** in `backups/` with a `_v1` suffix
4. **Shared infrastructure** (`crypto_engine.py`, `backup_engine.py`, `main.py`) is extended backward-compatibly
5. **EXE outputs** in `USB_DEPLOYMENT/` are overwritten with the latest version build
6. **Documentation** is updated with each version release

## Architecture

```
key_manager/
├── src/
│   ├── gui_main_v3_1.py       - v3.1 GUI: ColdStack rebrand + Check for Updates
│   ├── gui_main_v3.py         - v3 GUI: BIP39 derivation dialogs (preserved)
│   ├── key_manager_agent.py   - v2.4 Headless agent: HTTP signing server (port 8842)
│   ├── gui_main_v2.py         - v2 GUI: Add dialogs (preserved)
│   ├── gui_main.py            - v1 GUI: Display-only (preserved)
│   ├── crypto_engine.py       - AES-256-GCM + Argon2id key derivation (shared)
│   ├── derivation_engine.py   - BIP39 mnemonic-to-address derivation (v3)
│   ├── backup_engine.py       - Auto timestamped encrypted backups (shared)
│   └── main.py                - CLI interface + KeyManager class (shared, script-mode only)
├── build_gui_v3_1.py          - v3.1 PyInstaller build script (-> coldstack.exe)
├── build_gui_v3.py            - v3 PyInstaller build script (preserved)
├── key_manager_gui_v3_1.spec  - v3.1 PyInstaller spec (name='coldstack')
├── backups/                   - Backup files for restoration
├── USB_DEPLOYMENT/            - Compiled EXE for portable USB use
│   └── coldstack.exe          - v3.1 GUI build (ColdStack branded)
│   (key_manager.exe CLI EXE deprecated and removed as of v3.1)
└── README.md
```

## Component Status

| Component | Status | Notes |
|-----------|--------|-------|
| Crypto Engine | Working | AES-256-GCM + Argon2id |
| CLI (script mode) | Working | `python src/main.py` (CLI EXE deprecated as of v3.1) |
| GUI v3.1 Source | Working | ColdStack rebrand + update check + derivation |
| GUI EXE | Working | v3.1 build -> `coldstack.exe` via `python build_gui_v3_1.py` |
| Derivation Engine | Working | 7 chains, Ian Coleman parity verified |
| Headless Agent | Working | HTTP signing server on localhost:8842 |
| Backup Engine | Working | Timestamped encrypted backups |
| Vault Init | Working | Creates encrypted vault on first run |

## Security Design

- **Master Password -> Argon2id (3 iterations, 64MB memory) -> AES-256-GCM Key**
- Salt (16 bytes) + Nonce (12 bytes) per encryption
- Data stored as: `salt|nonce|ciphertext|tag` (base64 encoded)
- 5-minute auto-lock timeout (GUI); `--timeout` for agent
- Password re-entry required to view mnemonics (GUI)
- Automatic encrypted backups on every session
- **Agent binds to `127.0.0.1` only** - no external access
- **Update check**: User-initiated only, single GitHub API call, no telemetry

## GUI Fix History (June 2026)

Three bugs were identified and fixed in the GUI:

### Bug 1: `FreeConsole()` crashing script mode
- **Fix:** `FreeConsole()` now only runs when `getattr(sys, 'frozen', False)` is `True` (EXE mode only).

### Bug 2: Session timer started before status bar existed
- **Fix:** `start_session_timer()` is now called after `create_status_bar()`.

### Bug 3: `update_session_timer()` lacked defensive guards
- **Fix:** `update_session_timer()` checks `hasattr(self, 'session_timer_label')` before updating.

## Dependencies

```
customtkinter       - Modern Tkinter theme (GUI)
cryptography        - AES-256-GCM + Argon2id (shared)
argon2-cffi         - Argon2id password hashing (GUI)
pyperclip           - Clipboard functionality (GUI)
Pillow (PIL)        - Image support for CustomTkinter (GUI)
PyInstaller         - Build tool for EXE creation (build)
pycryptodomex       - keccak256 for EVM signing (agent)
hdwallet            - BIP32/BIP44/49/84/86 HD wallet derivation (v3)
mnemonic            - BIP39 mnemonic validation/generation (v3)
```

## Future Integration Path

### Phase 1: GUI Working - COMPLETE
### Phase 1.5: v2 - GUI Add Functionality - COMPLETE
### Phase 2: Headless Agent - COMPLETE (v2.4)
### Phase 2.5: v3 - BIP39 Derivation Engine - COMPLETE (v3.0)
### Phase 2.6: v3.1 - ColdStack Rebrand + Update Check - COMPLETE (v3.1)

### Phase 3: Multi-Chain Signing
- [ ] Solana signing support
- [ ] SUI signing support (native, not EVM)
- [ ] DASH signing support

### Phase 4: Pool Management
- [ ] Vault holds all pool wallet keys
- [ ] Sentinel detects rebalancing needs
- [ ] Vault executes transfers via agent
- [ ] Privacy rails (ZEC/XMR) for movements

---
*Status report updated June 25, 2026. v3.1 released - ColdStack rebrand + Check for Updates. CLI EXE deprecated.*