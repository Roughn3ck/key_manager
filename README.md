# ColdStack

*Secure offline crypto key vault with BIP39 derivation engine.*

[![GitHub release (latest by date)](https://img.shields.io/github/v/release/Roughn3ck/key_manager)](https://github.com/Roughn3ck/key_manager/releases) [![License](https://img.shields.io/badge/license-MIT-blue)](LICENSE) [![Platform](https://img.shields.io/badge/platform-Windows-blue)]() [![Status](https://img.shields.io/badge/status-Production%20v5.1-success)]()

## Download

**Latest release: [ColdStack v5.1 - Vault Tracking + HyperEVM ERC-20](https://github.com/Roughn3ck/key_manager/releases/tag/v5.1)**

| File | Size | Description |
|------|------|-------------|
| `coldstack.exe` | ~50MB | Full GUI application - ColdStack branded, v5.1 with Vault Tracking, HyperEVM ERC-20, LP Engine, Hyperliquid Writer |

> The CLI executable (`key_manager.exe`) has been deprecated and removed from USB_DEPLOYMENT as of v3.1. The CLI source remains available for script-mode use (`python src/main.py`).

> No installation required. Just download, run, and click "Initialize New Vault". Works on any Windows 10/11 machine - no Python needed.

---

## v5.1 Features - Vault Tracking + HyperEVM ERC-20 + LP Fee Fix (July 2026)

- **Hyperliquid Vault Tracking** (`src/vault_tracker.py`): New read-only module that fetches Hyperliquid vault positions via the `/info` API (`userVaultEquities` + `vaultDetails`). Displays in a new "HL1 Vaults" tab.
  - Vault card shows **Vault Name** as a heading, **Vault Address** in smaller text, **TVL**, **APR**, **Vault Age**, **Deposit Age**, **Deposited**, **Current Value**, and **Unrealized P&L**.
  - **Save Vault** / **Delete Saved** buttons persist vault snapshots inside the encrypted `key_vault.encrypted` database — no separate JSON files. Saved vaults render instantly on reopen using cached data.
  - **Wallet selector** offers two modes: type a raw 0x address, or pick an account from the Wallet tab and auto-resolve its first EVM/HYPE address.
  - **APR** sourced directly from Hyperliquid `vaultDetails.apr`.
  - **TVL** computed from `maxDistributable` (or sum of follower equities).
  - **Vault Age** from earliest portfolio history timestamp; **Deposit Age** from the user's personal `followerState.vaultEntryTime`.
- **LP Fee Reading Fix**: HyperEVM uncollected fees are now read via a static `eth_call` to the Uniswap V3 `collect((uint256,address,uint128,uint128))` function (selector `0xfc6f7865`) on the Project X PositionManager. This returns real uncollected fee amounts matching the Project X UI tooltip. The previous `feeGrowthGlobal` delta approach (which overcounted) and the disabled `PROJECT_X_FEE_ESTIMATION_ENABLED` flag are replaced/removed.
- **HyperEVM ERC-20 Token Balances**: Balance engine now fetches ERC-20 tokens on HyperEVM (chain ID 999). Supports USDC (`0xb88339CB7199b77E23DB6E890353E22632Ba630f` - confirmed via Circle developer docs), WHYPE, and UBTC. The `fetch_hype_balances()` method now queries both HyperEVM native + ERC-20 tokens + Hyperliquid L1 spot balances.
- **Combined Chain Option**: "HYPE (Hyperliquid)" and "Hyperliquid L1 (Spot)" replaced with single "Hyperliquid (HL1 & HyperEVM)" chain option that fetches all three balance types in one call.
- **Password Show/Hide Toggle**: Eye icon button on the login screen to toggle password visibility while typing.
- **"Do not autolock" Checkbox**: Checkbox in the status bar next to the session timer. When checked, the wallet stays open indefinitely - the 5-minute auto-lock is disabled. Timer shows "Session: No timeout" when active.
- **Spot Positions Filtered**: LP Positions tab no longer shows Hyperliquid L1 spot holdings (e.g., `0x...:spot:USDC`) - these are just token balances, not LP positions.
- **Closed LP Positions Filtered**: LP Positions tab filters out closed EVM LP positions (liquidity == 0 in NFT position data) to avoid showing $0.00 value positions with confusing "Out of Range" suggestions.
- **Stablecoin Currency Conversion Fix**: USDC, USDT, and other stablecoins now properly convert to the user's configured display currency (CAD, AUD, EUR, etc.) using CoinGecko USDC price in the target currency, instead of always returning 1:1 USD value.
- **Dispatcher Fix**: Balance engine dispatcher fixed so "Hyperliquid (HL1 & HyperEVM)" chain routes to `fetch_hype_balances()` (which fetches HyperEVM + L1) instead of being intercepted by the HL1-only branch.
- **Build Fix (July 9)**: PyInstaller now bundles the full HTTPS/SSL stack (`ssl`, `_ssl`, `http.client`, `socket`, `_socket`) so the EXE can resolve `https://` URLs. All network features (LP positions, perp state, spot balances, price feeds, vault tracker, update checker) confirmed working in the built EXE.
- **LP Positions Tab**: New CTkTabview with "Wallet" (was "Vault"), "HL1 Vaults" (new), "LP Positions" (unchanged). LP tab shows all LP positions for a wallet address with health emoji, pair, range, fees, value, PnL, and strategy suggestions.
- **LP Engine** (`lp_engine.py`): Read-only LP position aggregation with adapter registry. Fetches positions from Hyperliquid L1 (perp/spot) and HyperEVM (concentrated-liquidity pools). Strategy engine analyzes positions and suggests actions (Hold/Rebalance/Withdraw).
- **Hyperliquid Adapter** (`hyperliquid_adapter.py`): 1000+ line read adapter for HyperEVM NFT Position Manager + L1 perp/spot positions. Batch RPC with rate-limit handling. WHYPE/UBTC pool support. Fee estimation uses static `collect()` eth_call.
- **Hyperliquid Writer** (`hyperliquid_writer.py`): Full VenueWriter implementation for HyperEVM. Wrap/unwrap HYPE, approve tokens, open/increase/decrease/collect/close LP positions, rebalance. Signs via the embedded key_manager_agent on `localhost:8842` - never holds private keys. An external agent can still be launched manually if desired.
- **Write Operations with Confirmation**: Advanced mode shows "Collect Fees" button on LP cards. Every write operation requires explicit user confirmation dialog. Writer is a tool, not an autonomous agent.
- **Collect Fees Tested**: Collecting uncollected fees from a Project X HyperEVM LP position has been tested and confirmed working end-to-end via the embedded agent.
- **Address Pre-fill**: LP tab and HL1 Vaults tab address entry auto-fill from the selected account's first EVM/HYPE address.
- **Offline-aware**: LP tab and HL1 Vaults tab respect Go Online toggle. Refresh disabled when offline. Offline banner shown.

### Running v5.1
- **GUI (script mode):** `python src/gui_main_v5.py`
- **Build EXE:** `python build_gui_v5.py` -> `USB_DEPLOYMENT/coldstack.exe`
- **CLI (script mode only):** `python src/main.py`
- **No new dependencies**: Uses stdlib `urllib.request` only. Swap router stubbed (HyperEVM swap router address TBD).
- **Backward compatible**: v4.2 vaults open in v5.0 without migration. LP tab is additive.

### Security Rules (v5.1)
1. LP Engine is read-only by default. Writer only accessible when vault is unlocked + user confirms each operation.
2. Writer never holds private keys. All signing goes through the headless agent.
3. Every write operation requires GUI confirmation dialog.
4. Offline mode blocks all LP operations (reads and writes).
5. No auto-trading, no bots, no autonomous rebalancing. Strategy engine SUGGESTS. User DECIDES.

---

## v4.2 Features - Customizable RPC + Advanced Mode (July 2026)

- **Customizable RPC Endpoints**: User-editable `rpc_endpoints.json` config file. Contains ONLY public RPC URLs - no API keys. Falls back to hardcoded defaults if missing or malformed. Co-located with EXE for portability.
- **Standard/Advanced Mode Toggle**: Settings dialog has two modes. Standard (default): identical to v4.1. Advanced: reveals RPC endpoint editor + API key fields. Mode persists in encrypted vault config.
- **RPC Endpoint Editor**: Advanced mode Settings shows scrollable list of all 17 chains with editable URL fields. Per-chain "Reset" button + "Reset All to Defaults" button.
- **API Key Management**: API keys (Helius, Infura, Alchemy, Quicknode) stored in encrypted vault `config.api_keys`. Never written to `rpc_endpoints.json`. The JSON file only contains `"auth": "helius"` references - the actual key value is looked up from the vault at runtime.
- **Fallback URLs**: Each chain can have a fallback URL. Balance engine tries primary first, falls back on failure. Improves reliability of balance fetching.
- **EVM Chain Progress Display**: When checking EVM balances, the label updates in real-time showing which chain is being queried ("Fetching Ethereum...", "Fetching Arbitrum...", etc.) instead of a static "Fetching..." message.
- **Hyperliquid (HL1 & HyperEVM) Optimized Search**: The Hyperliquid chain type now searches HyperEVM (gas + ERC-20 tokens: USDC, WHYPE, UBTC) + Hyperliquid L1 (spot balances). Fetches all balance types in one call.
- **Schema Versioning**: Vault config now has `schema_version` field. Old vaults default to v1 (v4.1 behavior). New vaults get v2. All changes are additive - never breaks old vaults.
- **No new dependencies**: Still uses stdlib `urllib.request` only.

### Chain Routing

| Chain Type | What It Searches | API Calls |
|------------|-----------------|-----------|
| `EVM (Ethereum / Arbitrum / Base)` | All 7 EVM chains + ERC-20 tokens | 14+ |
| `Hyperliquid (HL1 & HyperEVM)` | HyperEVM gas + ERC-20 (USDC, WHYPE, UBTC) + L1 spot | 5+ |


### Security Boundary

| Data | Storage | Encrypted? |
|------|---------|------------|
| Public RPC URLs | `rpc_endpoints.json` | No - public info |
| API keys (Helius, etc.) | `key_vault.encrypted` -> `config.api_keys` | Yes - secrets |
| Mnemonics, private keys | `key_vault.encrypted` | Yes - existing |

---

## v4.1 Features - Price Feeds + Wallet Balances + Go Online (June 2026)

- **Go Online toggle**: Settings dialog with "Go Online" switch (OFF by default). When ON: enables read-only balance fetching and price feeds. Offline by default - no network requests unless explicitly enabled.
- **Inline balance display**: Balances shown inline on existing address cards (not a separate tab). Native balance by default (e.g., 0.5 ETH, 0.001 BTC). Balance label hidden until a fetch is triggered.
- **Check Balance button**: Per-card "Check Balance" button on each address card. Greyed out when offline, active when online. Click to fetch balance for that address only.
- **Compact card layout**: Coin + chain + "derived" tag on ONE line, address below, balance only when fetched, notes only when present. Reduced vertical space per card.
- **Currency toggle**: Set Default Currency in Settings (USD, AUD, CAD, EUR, CHF, or None). Shows fiat equivalent alongside native balance when set.
- **Balance Engine**: Fetches wallet balances via public RPC endpoints. Supports EVM (Ethereum, Arbitrum, Base, BSC, Polygon, Optimism), BTC, SOL, DASH, SUI. Uses stdlib `urllib.request` - no new dependencies.
- **Price Engine**: CoinGecko API (free tier, no API key). 60-second in-memory cache. No persistent storage of prices.
- **Online/Offline indicator**: Status bar shows green "Online" or grey "Offline". Last refresh timestamp shown subtly.
- **Manual refresh only**: No auto-refresh or background polling. User clicks "Check Balance" per-card.
- **Re-render on toggle**: Switching online/offline immediately updates button states on all address cards.
- **Backward compatible**: Existing v3.1 vaults open without migration. Vault config (online_mode, currency) stored in encrypted vault.

### Running v4.1
- **GUI (script mode):** `python src/gui_main_v4.py`
- **Build EXE:** `python build_gui_v4.py` -> `USB_DEPLOYMENT/coldstack.exe`
- **CLI (script mode only):** `python src/main.py`

### Security
- **Offline by default**: Zero network requests unless user explicitly enables "Go Online"
- **Only public addresses queried**: Private keys and mnemonics NEVER leave the vault
- **No price storage**: Prices cached in-memory only (60s TTL), never written to disk
- **Config encrypted**: Online mode and currency settings stored in the encrypted vault

---

## v3.1 Features - ColdStack Rebrand + Check for Updates (June 2026)

- **Brand rebrand**: GUI is now branded "ColdStack" (window title, login screen, version label)
- **Check for Updates button**: User-initiated update check in the status bar. Makes a single GitHub API call to check for newer releases. Offline by default - no background polling, no telemetry.
- **Threaded network request**: Update check runs in a background thread so the GUI stays responsive
- **EXE rename**: GUI executable is now `coldstack.exe`
- **CLI EXE deprecated**: The `key_manager.exe` CLI executable is no longer built or distributed. CLI remains available via script mode only (`python src/main.py`).
- **Vault co-located with app**: The vault (`key_vault.encrypted`) now lives in the same directory as the application, not in `~/.key_manager/`.

### Running v3.1
- **GUI (script mode):** `python src/gui_main_v3_1.py`
- **Build EXE:** `python build_gui_v3_1.py` -> `USB_DEPLOYMENT/coldstack.exe`
- **CLI (script mode only):** `python src/main.py derive-address --account "MyAccount" --chain "EVM (Ethereum / Arbitrum / Base)" --index 0`

## v3.0 Features - BIP39 Mnemonic Derivation (June 2026)

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
- **Build EXE:** `python build_gui_v3.py` -> `USB_DEPLOYMENT/key_manager_gui.exe` (legacy v3)
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

1. **Copy** `coldstack.exe` to a USB drive (or any folder)
2. **Run** it - double-click the EXE
3. **Click** "Initialize New Vault" on the login screen
4. **Create** a master password (there is NO recovery if you lose it)
5. **Start adding** your accounts, addresses, mnemonics, and private keys

That's it. No installation. No Python. No dependencies. Just run the EXE.

> **Important:** The EXE is completely self-contained. It runs on **any Windows computer** without Python installed. All libraries (Python runtime, crypto, GUI framework) are bundled inside the ~50MB EXE.

## What is ColdStack?

ColdStack is a secure application for storing and managing your cryptocurrency wallet information. It encrypts your data with **AES-256-GCM** authenticated encryption and **Argon2id** key derivation - the same standards used by password managers and crypto exchanges.

You can store:
- **Wallet addresses** organized by account and chain
- **24-word recovery phrases (mnemonics)** - encrypted, hidden by default, revealed on demand
- **Private keys** - multiple chain-specific keys per account
- **Account notes** for additional context

Everything is encrypted at rest. Your master password is never stored - it exists only in memory during your active session.

## Two Interfaces

### GUI (Windows EXE)
- **File:** `coldstack.exe` (built from `src/gui_main_v3_1.py`)
- **For:** Human use - view, add, manage accounts and keys
- **Features:** Dark theme, add accounts/addresses/mnemonics, BIP39 derivation, CSV/Excel import, check for updates, auto-lock

### Headless Agent (Python)
- **File:** `src/key_manager_agent.py`
- **For:** AI agent use - Vault signs and broadcasts transactions via HTTP
- **Port:** 8842 (localhost only - binds to `127.0.0.1`)
- **Protocol:** JSON over HTTP POST

```
+-------------+     +--------------------------+     +-----------------+
|  USB Drive   |     |  key_manager_agent.py    |     |  Vault (AI)     |
|  (F:\)       |     |  (headless process)      |     |  (DeepSeek V3.2)|
|              |     |                          |     |                 |
| key_vault    |---->|  unlock(password)        |<----|  sends JSON     |
| .encrypted   |     |  decrypt in memory       |     |  commands via   |
|              |     |  sign tx internally      |     |  HTTP to :8842  |
|              |     |  NEVER return privkey    |     |                 |
+-------------+     +--------------------------+     +-----------------+
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
- ALWAYS use `sign_tx` or `broadcast_tx` - keys stay internal
- ALWAYS send `{"cmd": "lock"}` when done
- Password comes from `KEY_MANAGER_PASSWORD` env var only

## ColdStack GUI Features (v3.1)

### Core Features
- **Modern Dark Theme** - Easy on the eyes, built with CustomTkinter
- **Secure Login Screen** - Password-only entry with AES-256-GCM/Argon2id validation
- **Initialize New Vault** - Create a new encrypted vault directly from the GUI (no CLI needed)
- **Change Password** - Change your master password from within the GUI
- **Session Auto-Lock** - 5-minute inactivity timeout automatically locks the vault (with "Do not autolock" checkbox to disable)
- **Toast Notifications** - Visual feedback for all operations
- **Check for Updates** - User-initiated update check (offline by default, no background polling)

### Managing Your Data
- **Add Account** - Create accounts, optionally organized into pools/groups
- **Add Address** - Standardized chain dropdown (BTC Taproot, EVM, ZEC, etc.) with Custom option
- **Add Mnemonic** - Store 24-word recovery phrases, encrypted and hidden by default
- **Add Private Key** - Chain-specific private keys with standardized chain labels
- **Delete Address** - Remove individual addresses with confirmation dialog
- **Copy to Clipboard** - One-click copy of any address

## Importing Addresses from CSV or Excel

You can bulk-import addresses from a CSV (`.csv`) or Excel (`.xlsx`, `.xls`) file instead of adding them one by one. This is the fastest way to populate your vault.

### Required Format

Your file must have a header row with these column names:

| Column | Required | Description |
|--------|----------|-------------|
| **Account** | Yes | The account name (e.g., "Ledger 1", "Hardware Wallet") |
| **Coin** | Yes | The chain/type - must match the dropdown values below |
| **Address** | Yes | The wallet address |
| **Notes** | No | Optional notes (e.g., "Hot wallet", "Exchange deposit") |
| **Chain** | No | Optional - usually left empty (the Coin field is the primary type) |

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

You can also use **any custom chain name** (e.g., `KASPA`, `AVAX`) - it will be stored as-is.

### How to Import

1. Prepare your CSV or Excel file with the correct format above
2. Open ColdStack and unlock your vault
3. Click **"Import Addresses"** in the left panel
4. Select your `.csv` or `.xlsx` file
5. Review the import summary (addresses added, skipped, errors)
6. Your addresses are now in the vault - organized by account

## Supported Chains (Dropdown)

- **BTC:** BTC Taproot (bc1p), BTC SegWit (bc1q), BTC (Bitcoin)
- **EVM:** EVM (Ethereum / Arbitrum / Base), EVM ERC-20, EVM Railgun
- **Privacy:** ZEC (Zcash), ZEC Transparent, ZEC Orchard, XMR (Monero)
- **Other:** SOL (Solana), DASH (Dash), RUNE (THORChain), SUI (Sui), TRON (Tron), ATOM (Cosmos), DOT (Polkadot), ADA (Cardano), XRP (Ripple), SCRT (Secret Network)
- **Custom...** - type your own chain name for unsupported chains

## File Locations

### Portable Mode (USB Drive - Default)

When you run `coldstack.exe` from a USB drive or folder:

- **Program:** `USB_DRIVE\coldstack.exe`
- **Encrypted Vault:** `USB_DRIVE\key_vault.encrypted` (co-located with the EXE)

### Script Mode (Development)

When running from Python source code:

- **Encrypted Vault:** `<project_root>/key_vault.encrypted` (co-located with the application)

### Headless Agent Mode

- **Agent script:** `src/key_manager_agent.py`
- **Vault file:** Any path via `--vault` flag (typically USB drive mount point)
- **Config:** `KEY_MANAGER_PASSWORD` environment variable

## Security

- **Encryption:** AES-256-GCM authenticated encryption
- **Key Derivation:** Argon2id (64MB memory, 3 iterations, 4 lanes)
- **Password Storage:** None - password exists only in memory during active session
- **Auto-Lock:** 5-minute inactivity timeout clears sensitive data (GUI); `--timeout` for agent
- **Sensitive Data:** Mnemonics and private keys are masked by default, revealed only on demand
- **No Telemetry:** No data ever leaves your computer (except the user-initiated update check, which only queries the GitHub releases API)
- **Agent binds to localhost only** - no external network access to the signing service

## Account Structure

ColdStack starts as a **blank slate**. You create your own accounts and organize them however you like:

- Create accounts with any name (e.g., "Ledger 1", "Trezor", "Exchange")
- Optionally organize accounts into pools/groups
- Most users will only need one or two accounts
- The structure is entirely up to you

## ColdStack CLI (Script Mode Only - EXE Deprecated)

ColdStack also includes a command-line interface for power users and automation. **Note:** As of v3.1, the CLI executable (`key_manager.exe`) is no longer built or distributed. The CLI is available only via script mode (`python src/main.py`).

**CLI Commands:**
```
python src/main.py init                              # Initialize a new vault
python src/main.py unlock                            # Unlock an existing vault
python src/main.py accounts                          # List all accounts
python src/main.py addresses [account]               # Show addresses
python src/main.py add-address <account> <coin> <chain> <address> [--notes=...]
python src/main.py delete-address <account> <index>  # Delete address by index
python src/main.py add-account <account> [--pool=...] # Create an account
python src/main.py add-mnemonic <account> <24 words> # Store a mnemonic
python src/main.py show-mnemonic <account>            # View mnemonic (requires re-entry)
python src/main.py add-key <account> <key> [--chain=...] # Add a private key
python src/main.py show-key <account>                # View private keys
python src/main.py import-csv <file_path> [--format-guide]  # Import from CSV/Excel
python src/main.py change-password                   # Change master password
python src/main.py lock                               # Lock the session
python src/main.py status                             # Show vault statistics
python src/main.py gen-password                       # Generate a secure password
python src/main.py derive-address <account> --chain <chain> [--index=0] [--list-chains]  # Derive from mnemonic
python src/main.py generate-mnemonic [--strength=256]   # Generate a new BIP39 mnemonic
python src/main.py validate-mnemonic <account>           # Validate stored mnemonic checksum
```

## Technical Details

- **GUI Framework:** CustomTkinter (dark theme)
- **Encryption:** cryptography library (AES-256-GCM + Argon2id)
- **Build Tool:** PyInstaller (onefile EXE)
- **Python:** 3.14 (GUI), 3.12 (headless agent on WSL)
- **Portable:** No installation required, runs from any folder/USB
- **Headless Agent:** Python 3.10+, `pycryptodomex`, `cryptography`

## Version

**v5.1** - July 2026 - Vault Tracking + HyperEVM ERC-20 + LP Fee Fix + Saved Vaults
- Hyperliquid Vault Tracking (`src/vault_tracker.py`) with HL1 Vaults tab: Vault Name heading, Vault Address, TVL, APR, Vault Age, Deposit Age, Deposited, Current Value, Unrealized P&L
- Save/Delete Vault buttons persist full vault snapshots inside `key_vault.encrypted`
- Wallet selector: raw address entry or account dropdown from Wallet tab
- LP fee reading fix: static `collect()` eth_call (selector `0xfc6f7865`) returns real uncollected fees from Project X PositionManager
- HyperEVM ERC-20 token support (USDC, WHYPE, UBTC) in balance engine
- Combined "Hyperliquid (HL1 & HyperEVM)" chain option (replaces HYPE + Spot)
- Password show/hide eye toggle on login screen
- "Do not autolock" checkbox in status bar
- Tab renaming: Wallet, HL1 Vaults, LP Positions
- Spot positions filtered from LP results
- Closed LP positions filtered (liquidity == 0)
- Stablecoin currency conversion fix (USDC -> CAD/AUD/EUR via CoinGecko)
- Balance dispatcher fix for Hyperliquid chain routing
- No new dependencies (stdlib urllib.request only)
- Backward compatible: v5.0 vaults open without migration

**v5.0** - July 2026 - LP Engine + Hyperliquid Writer + LP Positions Tab
- CTkTabview with "Vault" and "LP Positions" tabs
- LP Engine with adapter registry (HyperliquidAdapter for HyperEVM + L1)
- HyperliquidWriter for position management (wrap, approve, open, collect, close, rebalance)
- Strategy engine with health emoji (safe/watch/near_edge/out_of_range/profit_take)
- Write operations via key_manager_agent (localhost:8842) - never holds private keys
- Every write operation requires GUI confirmation dialog
- Address pre-fill from selected account
- Swap router stubbed (HyperEVM swap router address TBD)
- No new dependencies (stdlib urllib.request only)
- Backward compatible: v4.2 vaults open without migration

**v4.1** - June 2026 - Price Feeds + Wallet Balances + Go Online
- "Go Online" toggle in Settings (offline by default, user-initiated)
- "Check Balance" button per address card (greyed when offline, active when online)
- Inline balance display on address cards (EVM, BTC, SOL, DASH, SUI)
- Compact card layout (coin + chain + derived on one line)
- CoinGecko price feeds with 60s cache (USD, AUD, CAD, EUR, CHF)
- Balance Engine (`src/balance_engine.py`) and Price Engine (`src/price_engine.py`)
- Online/Offline indicator in status bar
- No new dependencies (uses stdlib `urllib.request`)
- Backward compatible: v3.1 vaults open without migration

**v3.1** - June 2026 - ColdStack rebrand + Check for Updates
- GUI rebranded to "ColdStack" (window title, login screen, version label, class `ColdStackGUI`)
- "Check for Updates" button in status bar (user-initiated, offline by default, threaded)
- GUI EXE renamed from `key_manager_gui.exe` to `coldstack.exe`
- CLI EXE deprecated and removed from USB_DEPLOYMENT (CLI source remains for script-mode use)
- Vault now co-located with the application (not in `~/.key_manager/`)
- Build script fixes: backup name corrected, launcher script rebranded
- No new dependencies (update check uses stdlib `urllib.request`)
- Backward compatible: existing vaults work without migration

**v3.0** - June 2026 - BIP39 derivation engine launch
- BIP39 mnemonic-to-address derivation engine (7 chains: EVM, BTC Taproot/SegWit/Legacy, Solana, Dash, Sui)
- "Derive Addresses" and "Derive All Chains" buttons in mnemonic section
- "Derive from Mnemonic" checkbox in Add Private Key dialog
- Enhanced private key display with derivation path + source indicators + link icon
- CLI commands: `derive-address`, `generate-mnemonic`, `validate-mnemonic`
- Schema enhancement: private keys/addresses store derivation metadata (source, path, index)
- Fully backward-compatible: v2 vaults open without migration

**v2.4** - June 2026
- Initialize vault from GUI (no CLI needed)
- Import addresses from CSV/Excel with formatting guide
- Change password from GUI
- Delete addresses from GUI
- Standardized chain dropdown with 20+ predefined chains + Custom option
- Multi-key private key support with chain labels
- Blank slate (no pre-determined accounts/pools - users create their own)
- Mnemonic section always visible (independent of addresses)
- Private key section always visible (independent of addresses)
- **NEW:** Headless agent (`key_manager_agent.py`) for AI-driven signing via HTTP

---
*ColdStack - Your crypto keys, encrypted, portable, yours.*
*Built by [Kris Racette](https://krisracette.me) - [Executive Mind](https://executivemind.io)*