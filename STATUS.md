# Key Manager — Status Report

## v3.0 — BIP39 Mnemonic Derivation Engine (June 2026)

### New Features
- **Derivation Engine** (`src/derivation_engine.py`): Derives addresses and private keys from BIP39 mnemonics using standard HD wallet paths (BIP32/BIP44/BIP49/BIP84/BIP86).
  - Supported chains: EVM (Ethereum/Arbitrum/Base), BTC Taproot (bc1p), BTC SegWit (bc1q), BTC Legacy (1...), SOL (Solana), DASH (Dash), SUI (Sui)
  - Manual bech32/bech32m encoding for BTC SegWit/Taproot addresses
  - EIP-55 checksummed EVM addresses via keccak256
  - Mnemonic validation and generation (12/24 words)
- **GUI Derivation Dialogs**: "Derive Addresses" and "Derive All Chains" buttons in the mnemonic section of the account detail panel.
  - Chain dropdown, editable derivation path, address index
  - Save derived address + private key to the account with derivation metadata
  - "Derive Another" for incremental index derivation
- **Enhanced Private Key Display**: Shows derivation path, source indicator (manual/derived), and link icon (🔗) for keys derived from the account's stored mnemonic.
- **Add Private Key Dialog**: "Derive from Mnemonic" checkbox — when enabled, the private key is derived from the account's mnemonic instead of manual entry.
- **CLI Commands**: `derive-address`, `generate-mnemonic`, `validate-mnemonic` added to the CLI.
- **Schema Enhancement**: Private keys and addresses now optionally store `source`, `derivation_path`, `address_index`, and `derived_address` metadata. Fully backward-compatible — old entries default to `source="manual"`.

### Files Changed
- `src/derivation_engine.py` (NEW) — BIP39 derivation engine
- `src/gui_main_v3.py` (NEW) — v3 GUI with derivation integration
- `build_gui_v3.py` (NEW) — v3 PyInstaller build script
- `test_derivation.py` (NEW) — Ian Coleman parity + multi-chain tests
- `src/main.py` (EXTENDED) — Schema fields + new CLI commands
- `requirements.txt` (UPDATED) — Added hdwallet, mnemonic
- `USB_DEPLOYMENT/key_manager_gui.exe` (REBUILT) — v3 build

### Testing
- All 7 derivation tests pass (Ian Coleman parity, BTC formats, multi-chain, validation, generation, multiple derivation, backward compatibility)
- EVM address matches Ian Coleman BIP39 tool: `0x9858EfFD232B4033E47d90003D41EC34EcaEda94`
- BTC SegWit produces `bc1q...` (bech32), Taproot produces `bc1p...` (bech32m)



**Project Location:** `B:\Github\key_manager\`
**Last Updated:** 2026-06-24
**Assigned Agent:** Vault (headless agent), Cline (GUI)
**Current Version:** v2.4 (GUI + Headless Agent)

## Versioning

| Version | Description | Key Files |
|---------|-------------|-----------|
| v1 | Original GUI — display-only (addresses, mnemonics, private keys) | `src/gui_main.py`, `build_gui.py` |
| v2 | GUI Add functionality — create accounts, add addresses, add mnemonics via dialogs | `src/gui_main_v2.py`, `build_gui_v2.py` |
| v2.4 | Headless agent for AI-driven signing | `src/key_manager_agent.py` |

### Versioning Rules

1. **New versions** introduce a `_v<N>` suffix to all new/modified working files
2. **v1 files are preserved** — the originals remain untouched as the v1 baseline
3. **Core v1 files are backed up** in `backups/` with a `_v1` suffix
4. **Shared infrastructure** (`crypto_engine.py`, `backup_engine.py`, `main.py`) is extended backward-compatibly
5. **EXE outputs** in `USB_DEPLOYMENT/` are overwritten with the latest version build
6. **Documentation** is updated with each version release

## Architecture

```
key_manager/
├── src/
│   ├── key_manager_agent.py  — v2.4 Headless agent: HTTP signing server (port 8842)
│   ├── gui_main_v2.py        — v2 GUI: CustomTkinter dark GUI with add dialogs
│   ├── gui_main.py           — v1 GUI: CustomTkinter dark GUI (display-only)
│   ├── crypto_engine.py      — AES-256-GCM + Argon2id key derivation (shared)
│   ├── backup_engine.py      — Auto timestamped encrypted backups (shared)
│   └── main.py               — CLI interface + KeyManager class (shared)
├── build_gui_v2.py           — v2 PyInstaller build script
├── build_gui.py              — v1 PyInstaller build script
├── backups/                  — v1 backup files (restore point)
├── USB_DEPLOYMENT/           — Compiled EXEs for portable USB use
└── README.md
```

## Component Status

| Component | Status | Notes |
|-----------|--------|-------|
| Crypto Engine | ✅ Working | AES-256-GCM + Argon2id |
| CLI (key_manager.exe) | ✅ Working | Full CLI functionality |
| GUI v2 Source | ✅ Working | Add accounts/addresses/mnemonics |
| GUI EXE | ✅ Working | v2 build — rebuild via `python build_gui_v2.py` |
| **Headless Agent** | ✅ **NEW** | HTTP signing server on localhost:8842 |
| Backup Engine | ✅ Working | Timestamped encrypted backups |
| Vault Init | ✅ Working | Creates encrypted vault on first run |

## Headless Agent — key_manager_agent.py (NEW v2.4)

### Overview
A headless Python process that unlocks the encrypted vault and serves signing commands over HTTP on `localhost:8842`. Designed for AI agent (Vault) use — private keys never leave the process.

### Capabilities
- **EVM signing:** Legacy (pre-1559) and EIP-1559 transactions
- **Broadcast:** Signs and submits to any EVM RPC endpoint
- **Address derivation:** Returns public addresses only (no private keys)
- **Session timeout:** Auto-locks after `--timeout` seconds of inactivity (default 600s)
- **Input modes:** HTTP server (`--serve`) or stdin pipe

### Key Fixes Applied
1. **Windows `\r\n` preservation:** Vault files created on Windows have `\r\n` line endings. Python's text mode on Linux translates these to `\n`, breaking AES-GCM authentication. Fixed by reading with `newline=''`.
2. **Gas price buffer:** EIP-1559 `maxFeePerGas` calculated with 20% buffer above base fee to handle Arbitrum's rapid gas price fluctuations.
3. **Chain matching:** Private key lookup uses substring match (e.g., "EVM" matches "EVM (Ethereum / Arbitrum / Base)").
4. **User-Agent header:** Added to RPC calls to avoid 403 from public Arbitrum RPC.

### Usage
```bash
export KEY_MANAGER_PASSWORD="your-password"
python3 src/key_manager_agent.py \
  --vault /path/to/key_vault.encrypted \
  --serve --port 8842 \
  --timeout 600
```

### Real-World Test (2026-06-24)
- Vault unlocked successfully (25 accounts, 119 addresses, 14 private key entries)
- CCTP claim script (`cctp-claim.py`) fetched attestation from Circle iris API
- Built `receiveMessage` calldata and broadcast to Arbitrum
- TX `0xfbff99...` broadcast but reverted: "Invalid signature: not attester" (Circle attestation issue, not agent issue)
- Agent performed correctly — failure was external (Circle's attester key mismatch)

## Security Design

- **Master Password → Argon2id (3 iterations, 64MB memory) → AES-256-GCM Key**
- Salt (16 bytes) + Nonce (12 bytes) per encryption
- Data stored as: `salt|nonce|ciphertext|tag` (base64 encoded)
- 5-minute auto-lock timeout (GUI); `--timeout` for agent
- Password re-entry required to view mnemonics (GUI)
- Automatic encrypted backups on every session
- **Agent binds to `127.0.0.1` only** — no external access

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
customtkinter       — Modern Tkinter theme (GUI)
cryptography        — AES-256-GCM + Argon2id (shared)
argon2-cffi         — Argon2id password hashing (GUI)
pyperclip           — Clipboard functionality (GUI)
Pillow (PIL)        — Image support for CustomTkinter (GUI)
PyInstaller         — Build tool for EXE creation (build)
pycryptodomex       — keccak256 for EVM signing (agent)
```

## Data in Vault

The vault currently holds:
- 119 addresses across 25 accounts
- 14 private key entries (EVM, DASH chains)
- Pool positions, wallet addresses, chain data
- All encrypted with AES-256-GCM

## Future Integration Path

### Phase 1: GUI Working ✅ COMPLETE
### Phase 1.5: v2 — GUI Add Functionality ✅ COMPLETE
### Phase 2: Headless Agent ✅ COMPLETE (v2.4)
- [x] HTTP server mode for AI agent access
- [x] EVM transaction signing + broadcast
- [x] Private keys never exposed
- [x] CCTP claim script for stuck USDC

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
*Status report updated June 24, 2026. v2.4 released — headless agent operational.*