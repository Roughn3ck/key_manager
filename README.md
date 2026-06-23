# Key Manager - Secure Crypto Key Storage

## Quick Start

1. **Copy** `key_manager_gui.exe` to a USB drive (or any folder)
2. **Run** it — double-click the EXE
3. **Click** "Initialize New Vault" on the login screen
4. **Create** a master password (there is NO recovery if you lose it)
5. **Start adding** your accounts, addresses, mnemonics, and private keys

That's it. No installation. No Python. No dependencies. Just run the EXE.

> **Important:** The EXE is completely self-contained. It runs on **any Windows computer** without Python installed. All libraries (Python runtime, crypto, GUI framework) are bundled inside the ~38MB EXE.

## What is Key Manager?

Key Manager is a secure application for storing and managing your cryptocurrency wallet information. It encrypts your data with **AES-256-GCM** authenticated encryption and **Argon2id** key derivation — the same standards used by password managers and crypto exchanges.

You can store:
- **Wallet addresses** organized by account and chain
- **24-word recovery phrases (mnemonics)** — encrypted, hidden by default, revealed on demand
- **Private keys** — multiple chain-specific keys per account
- **Account notes** for additional context

Everything is encrypted at rest. Your master password is never stored — it exists only in memory during your active session.

## GUI Features (v2.4)

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

The **Coin** column must match these values exactly (they match the GUI dropdown):

```
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

### Example CSV

```csv
Account,Coin,Address,Notes
Ledger 1,BTC Taproot (bc1p),bc1p...your...address,Cold storage
Ledger 1,EVM (Ethereum / Arbitrum / Base),0x...your...address,Main ETH
Trezor,DASH (Dash),X...your...address,
Trezor,ZEC Transparent,t1...your...address,Zcash transparent
Exchange,EVM ERC-20,0x...token...address,USDT
```

### Example Excel

| Account | Coin | Address | Notes |
|---------|------|---------|-------|
| Ledger 1 | BTC Taproot (bc1p) | bc1p...your...address | Cold storage |
| Ledger 1 | EVM (Ethereum / Arbitrum / Base) | 0x...your...address | Main ETH |
| Trezor | DASH (Dash) | X...your...address | |
| Trezor | ZEC Transparent | t1...your...address | Zcash transparent |
| Exchange | EVM ERC-20 | 0x...token...address | USDT |

### How to Import

1. Prepare your CSV or Excel file with the correct format above
2. Open Key Manager and unlock your vault
3. Click **"Import Addresses"** in the left panel
4. Select your `.csv` or `.xlsx` file
5. Review the import summary (addresses added, skipped, errors)
6. Your addresses are now in the vault — organized by account

> **Tip:** The import dialog shows a formatting guide with all valid Coin values. You can also run `key_manager import-csv --format-guide` from the CLI to see the same guide.

## Supported Chains (Dropdown)

The standardized chain dropdown includes:

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

The vault is stored **alongside the EXE** in a hidden `.key_manager` folder. Everything stays on the USB drive — your data goes where your USB goes.

### Script Mode (Development)

When running from Python source code:

- **Encrypted Vault:** `~/.key_manager/key_vault.encrypted` (your home directory)

## Security

- **Encryption:** AES-256-GCM authenticated encryption
- **Key Derivation:** Argon2id (64MB memory, 3 iterations, 4 lanes)
- **Password Storage:** None — password exists only in memory during active session
- **Auto-Lock:** 5-minute inactivity timeout clears sensitive data
- **Sensitive Data:** Mnemonics and private keys are masked by default, revealed only on demand with password re-entry, auto-hide after 5 minutes
- **No Telemetry:** No data ever leaves your computer

## Account Structure

Key Manager starts as a **blank slate**. You create your own accounts and organize them however you like:

- Create accounts with any name (e.g., "Ledger 1", "Trezor", "Exchange")
- Optionally organize accounts into pools/groups
- Most users will only need one or two accounts
- The structure is entirely up to you

## CLI (Advanced — Optional)

Key Manager also includes a command-line interface for power users and automation. This is optional — the GUI handles everything most users need.

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
```

> **Note:** The CLI is a developer/power-user tool. Most users should use the GUI EXE exclusively.

## Technical Details

- **GUI Framework:** CustomTkinter (dark theme)
- **Encryption:** cryptography library (AES-256-GCM + Argon2id)
- **Build Tool:** PyInstaller (onefile EXE)
- **Python:** 3.14
- **Portable:** No installation required, runs from any folder/USB

## Version

**v2.4** — June 2026
- Initialize vault from GUI (no CLI needed)
- Import addresses from CSV/Excel with formatting guide
- Change password from GUI
- Delete addresses from GUI
- Removed "Add to Queue" button (simplified UI)
- Standardized chain dropdown with 20+ predefined chains + Custom option
- Multi-key private key support with chain labels
- Blank slate (no pre-determined accounts/pools — users create their own)
- Mnemonic section always visible (independent of addresses)
- Private key section always visible (independent of addresses)

---
*Key Manager — Your crypto keys, encrypted, portable, yours.*