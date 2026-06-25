# ColdStack

*Secure offline crypto key vault with BIP39 derivation engine.*

[![GitHub release (latest by date)](https://img.shields.io/github/v/release/Roughn3ck/key_manager)](https://github.com/Roughn3ck/key_manager/releases) [![License](https://img.shields.io/badge/license-MIT-blue)](LICENSE) [![Platform](https://img.shields.io/badge/platform-Windows-blue)]() [![Status](https://img.shields.io/badge/status-Production%20v3.0-success)]()

## 📥 Download

**Latest release: [ColdStack v3.0 — BIP39 Derivation Engine](https://github.com/Roughn3ck/key_manager/releases/tag/v3.0)**

| File | Size | Description |
|------|------|-------------|
| `key_manager_gui.exe` | ~45MB | Full GUI application with v3 derivation engine |
| `key_manager.exe` | ~35MB | CLI version for headless operations |

> No installation required. Just download, run, and click "Initialize New Vault". Works on any Windows 10/11 machine — no Python needed.

---

## v3.0 Features — BIP39 Mnemonic Derivation (June 2026)

Key Manager v3 adds a full BIP39 mnemonic-to-address derivation engine:

- **Derive addresses and private keys** from stored mnemonics using standard HD wallet paths
- **7 supported chains**: EVM (Ethereum/Arbitrum/Base), BTC Taproot, BTC SegWit, BTC Legacy, Solana, Dash, Sui
- **GUI integration**: "Derive Addresses" and "Derive All Chains" buttons in the mnemonic section
- **Derive from Mnemonic** checkbox in the Add Private Key dialog
- **Enhanced private key display** with derivation path and source indicators
- **CLI commands**: `derive-address`, `generate-mnemonic`, `validate-mnemonic`
- **Backward compatible**: v2 vaults open without migration; old keys display correctly

### Running v3
- **GUI (script mode):** `python src/gui_main_v3.py`
- **Build EXE:** `python build_gui_v3.py` → `USB_DEPLOYMENT/key_manager_gui.exe`
- **CLI:** `python src/main.py derive-address --account "MyAccount" --chain "EVM (Ethereum / Arbitrum / Base)" --index 0`

### Supported Derivation Chains

| Chain | Path | Address Format |
|-------|------|----------------|
| EVM (Ethereum / Arbitrum / Base) | m/44'/60'/0'/0/0 | 0x... (EIP-55 checksum) |
| BTC Taproot (bc1p) | m/86'/0'/0'/0/0 | bc1p... (bech32m) |
| BTC SegWit (bc1q) | m/84'/0'/0'/0/0 | bc1q... (bech32) |
| BTC Legacy (1...) | m/44'/0'/0'/0/0 | 1... (Base58Check P2PKH) |
| SOL (Solana) | m/44'/501'/0'/0' | Base58 Ed25519 |
| DASH (Dash) | m/44'/5'/0'/0/0 | X... (Base58Check P2PKH) |
| SUI (Sui) | m/44'/784'/0'/0'/0' | 0x... (Ed25519) |

> **Verified against Ian Coleman's BIP39 tool**: The standard test vector produces `0x9858EfFD232B4033E47d90003D41EC34EcaEda94` at `m/44'/60'/0'/0/0`.



## Quick Start

1. **Copy** `key_manager_gui.exe` to a USB drive (or any folder)
2. **Run** it — double-click the EXE
3. **Click** "Initialize New Vault" on the login screen
4. **Create** a master password (there is NO recovery if you lose it)
5. **Start adding** your accounts, addresses, mnemonics, and private keys

That's it. No installation. No Python. No dependencies. Just run the EXE.

> **Important:** The EXE is completely self-contained. It runs on **any Windows computer** without Python installed. All libraries (Python runtime, crypto, GUI framework) are bundled inside the ~45MB EXE.

## What is ColdStack?

ColdStack is a secure application for storing and managing your cryptocurrency wallet information. It encrypts your data with **AES-256-GCM** authenticated encryption and **Argon2id** key derivation — the same standards used by password managers and crypto exchanges.

You can store:
- **Wallet addresses** organized by account and chain
- **24-word recovery phrases (mnemonics)** — encrypted, hidden by default, revealed on demand
- **Private keys** — multiple chain-specific keys per account
- **Account notes** for additional context

Everything is encrypted at rest. Your master password is never stored — it exists only in memory during your active session.

## Two Interfaces

### GUI (Windows EXE)
- **File:** `key_manager_gui.exe` (built from `src/gui_main_v3.py`)
- **For:** Human use — view, add, manage accounts and keys
- **Features:** Dark theme, add accounts/addresses/mnemonics, BIP39 derivation, CSV/Excel import, auto-lock

### Headless Agent (Python)
- **File:** `src/key_manager_agent.py`
- **For:** AI agent use — Vault signs and broadcasts transactions via HTTP
- **Port:** 8842 (localhost only — binds to `127.0.0.1`)
- **Protocol:** JSON over HTTP POST

```
┌─────────────┐     ┌──────────────────────────┐     ┌─────────────────┐
│  USB Drive   │     │  key_manager_agent.py    │     │  Vault (AI)     │
│  (F:\)       │     │  (headless process)       │     │  (DeepSeek V3.2)│
│              │     │                           │     │                 │
│ key_vault    │────▶│  unlock(password)         │◀────│  sends JSON     │
│ .encrypted   │     │  decrypt in memory        │     │  commands via   │
│              │     │  sign tx internally       │     │  HTTP to :8842  │
│              │     │  NEVER return privkey     │     │                 │
└─────────────┘     └──────────────────────────┘     └─────────────────┘
```

### Headless Agent Commands

| Command | Purpose | Returns |
|---------|---------|---------|
| `{"cmd": "status"}` | Check vault state | Unlocked, account list |
| `{"cmd": "list_accounts"}` | Full account tree | All accounts + addresses |
| `{"cmd": "get_address", "account": "G6", "chain": "EVM"}` | Public address | Address only (no keys) |
| `{"cmd": "sign_tx", "account": "G6", "to": "0x...", "data": "0x...", "chain_id": 42161, "rpc": "...", "chain": "EVM"}` | Sign tx (no broadcast) | Signed hex, from, nonce |
| `{"cmd": "broadcast_tx", ...}` | Sign + broadcast | TX hash, from, nonce |
| `{"cmd": "lock"}` | Lock vault | Confirmation |

**Starting the agent:**
```bash
# Password from env var (never hardcode)
export KEY_MANAGER_PASSWORD="your-password"
python3 src/key_manager_agent.py --vault /path/to/key_vault.encrypted --serve --port 8842 --timeout 600
```

**Security rules for AI agents:**
- NEVER request or log private keys
- ALWAYS use `sign_tx` or `broadcast_tx` — keys stay internal
- ALWAYS send `{"cmd": "lock"}` when done
- Password comes from `KEY_MANAGER_PASSWORD` env var only

## ColdStack GUI Features (v3.0)

### Core Features
- **Modern Dark Theme** — Easy on the eyes, built with CustomTkinter
- **Secure Login Screen** — Password-only entry with AES-256-GCM/Argon2id validation
- **Initialize New Vault** — Create a new encrypted vault directly from the GUI (no CLI needed)
- **Change Password** — Change your master password from within the GUI
- **Session Auto-Lock** — 5-minute inactivity timeout automatically locks the vault
- **Toast Notifications** — Visual feedback for all operations

### Managing Your Data
- **Add Account** — Create accounts, optionally organized into pools/groups
- **Add Address** — Standardized chain dropdown (BTC Taproot, EVM, ZEC, etc.) with Custom option
- **Add Mnemonic** — Store 24-word recovery phrases, encrypted and hidden by default
- **Add Private Key** — Chain-specific private keys with standardized chain labels
- **Delete Address** — Remove individual addresses with confirmation dialog
- **Copy to Clipboard** — One-click copy of any address

## Importing Addresses from CSV or Excel

You can bulk-import addresses from a CSV (`.csv`) or Excel (`.xlsx`, `.xls`) file instead of adding them one by one. This is the fastest way to populate your vault.

### Required Format

Your file must have a header row with these column names:

| Column | Required | Description |
|--------|----------|-------------|
| **Account** | Yes | The account name (e.g., "Ledger 1", "Hardware Wallet") |
| **Coin** | Yes | The chain/type — must match the dropdown values below |
| **Address** | Yes | The wallet address |
| **Notes** | No | Optional notes (e.g., "Hot wallet", "Exchange deposit") |
| **Chain** | No | Optional — usually left empty (the Coin field is the primary type) |

### Valid Coin Values

```text
BTC Taproot (bc1p)
BTC SegWit (bc1q)
BTC (Bitcoin)
EVM (Ethereum / Arbitrum / Base)
EVM ERC-20
EVM Railgun
SOL (Solana)
ZEC (Zcash)
ZEC Transparent
ZEC Orchard
XMR (Monero)
DASH (Dash)
RUNE (THORChain)
SUI (Sui)
TRON (Tron)
ATOM (Cosmos)
DOT (Polkadot)
ADA (Cardano)
XRP (Ripple)
SCRT (Secret Network)
```

You can also use **any custom chain name** (e.g., `KASPA`, `AVAX`) — it will be stored as-is.

### How to Import

1. Prepare your CSV or Excel file with the correct format above
2. Open Key Manager and unlock your vault
3. Click **"Import Addresses"** in the left panel
4. Select your `.csv` or `.xlsx` file
5. Review the import summary (addresses added, skipped, errors)
6. Your addresses are now in the vault — organized by account

## Supported Chains (Dropdown)

- **BTC:** BTC Taproot (bc1p), BTC SegWit (bc1q), BTC (Bitcoin)
- **EVM:** EVM (Ethereum / Arbitrum / Base), EVM ERC-20, EVM Railgun
- **Privacy:** ZEC (Zcash), ZEC Transparent, ZEC Orchard, XMR (Monero)
- **Other:** SOL (Solana), DASH (Dash), RUNE (THORChain), SUI (Sui), TRON (Tron), ATOM (Cosmos), DOT (Polkadot), ADA (Cardano), XRP (Ripple), SCRT (Secret Network)
- **Custom...** — type your own chain name for unsupported chains

## File Locations

### Portable Mode (USB Drive — Default)

When you run `key_manager_gui.exe` from a USB drive or folder:

- **Program:** `USB_DRIVE\key_manager_gui.exe`
- **Encrypted Vault:** `USB_DRIVE\.key_manager\key_vault.encrypted`

### Script Mode (Development)

When running from Python source code:

- **Encrypted Vault:** `~/.key_manager/key_vault.encrypted` (your home directory)

### Headless Agent Mode

- **Agent script:** `src/key_manager_agent.py`
- **Vault file:** Any path via `--vault` flag (typically USB drive mount point)
- **Config:** `KEY_MANAGER_PASSWORD` environment variable

## Security

- **Encryption:** AES-256-GCM authenticated encryption
- **Key Derivation:** Argon2id (64MB memory, 3 iterations, 4 lanes)
- **Password Storage:** None — password exists only in memory during active session
- **Auto-Lock:** 5-minute inactivity timeout clears sensitive data (GUI); `--timeout` for agent
- **Sensitive Data:** Mnemonics and private keys are masked by default, revealed only on demand
- **No Telemetry:** No data ever leaves your computer
- **Agent binds to localhost only** — no external network access to the signing service

## Account Structure

Key Manager starts as a **blank slate**. You create your own accounts and organize them however you like:

- Create accounts with any name (e.g., "Ledger 1", "Trezor", "Exchange")
- Optionally organize accounts into pools/groups
- Most users will only need one or two accounts
- The structure is entirely up to you

## ColdStack CLI (Advanced — Optional)

ColdStack also includes a command-line interface for power users and automation.

**CLI Commands:**
```
key_manager init                              # Initialize a new vault
key_manager unlock                            # Unlock an existing vault
key_manager accounts                          # List all accounts
key_manager addresses [account]               # Show addresses
key_manager add-address <account> <coin> <chain> <address> [--notes=...]
key_manager delete-address <account> <index>  # Delete address by index
key_manager add-account <account> [--pool=...] # Create an account
key_manager add-mnemonic <account> <24 words> # Store a mnemonic
key_manager show-mnemonic <account>            # View mnemonic (requires re-entry)
key_manager add-key <account> <key> [--chain=...] # Add a private key
key_manager show-key <account>                # View private keys
key_manager import-csv <file_path> [--format-guide]  # Import from CSV/Excel
key_manager change-password                   # Change master password
key_manager lock                               # Lock the session
key_manager status                             # Show vault statistics
key_manager gen-password                       # Generate a secure password
key_manager derive-address <account> --chain <chain> [--index=0] [--list-chains]  # Derive from mnemonic
key_manager generate-mnemonic [--strength=256]   # Generate a new BIP39 mnemonic
key_manager validate-mnemonic <account>           # Validate stored mnemonic checksum
```

## Technical Details

- **GUI Framework:** CustomTkinter (dark theme)
- **Encryption:** cryptography library (AES-256-GCM + Argon2id)
- **Build Tool:** PyInstaller (onefile EXE)
- **Python:** 3.14 (GUI), 3.12 (headless agent on WSL)
- **Portable:** No installation required, runs from any folder/USB
- **Headless Agent:** Python 3.10+, `pycryptodomex`, `cryptography`

## Version

**v3.0** — June 2026 — ColdStack brand launch
- BIP39 mnemonic-to-address derivation engine (7 chains: EVM, BTC Taproot/SegWit/Legacy, Solana, Dash, Sui)
- "Derive Addresses" and "Derive All Chains" buttons in mnemonic section
- "Derive from Mnemonic" checkbox in Add Private Key dialog
- Enhanced private key display with derivation path + source indicators + link icon
- CLI commands: `derive-address`, `generate-mnemonic`, `validate-mnemonic`
- Schema enhancement: private keys/addresses store derivation metadata (source, path, index)
- Fully backward-compatible: v2 vaults open without migration

**v2.4** — June 2026
- Initialize vault from GUI (no CLI needed)
- Import addresses from CSV/Excel with formatting guide
- Change password from GUI
- Delete addresses from GUI
- Standardized chain dropdown with 20+ predefined chains + Custom option
- Multi-key private key support with chain labels
- Blank slate (no pre-determined accounts/pools — users create their own)
- Mnemonic section always visible (independent of addresses)
- Private key section always visible (independent of addresses)
- **NEW:** Headless agent (`key_manager_agent.py`) for AI-driven signing via HTTP

---
*ColdStack — Your crypto keys, encrypted, portable, yours.*
*Built by [Kris Racette](https://krisracette.me) — [Executive Mind](https://executivemind.io)*