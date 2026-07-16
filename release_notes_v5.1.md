# ColdStack v5.1 — Vault Tracking + HyperEVM ERC-20 + LP Fee Fix + Saved Pools + LP UX Fixes

Portable, offline-first Windows GUI for managing encrypted BIP39 mnemonics, addresses, and private keys. Stores everything in `key_vault.encrypted` with AES-256-GCM + Argon2id. Go Online (user-toggled) enables read-only balances, prices, LP positions, and Hyperliquid vault tracking.

## What's new since the initial v5.1 release

### LP Positions tab fixes (Prompts 1-5)
- **Scan Wallet button state**: Re-enabled after unlock/restoring selector state when online and Account mode is selected.
- **Auto-detect improvements**: `HyperliquidAdapter.can_handle()` now accepts 66-character hex transaction hashes and numeric token IDs, so entering a Project X LP transaction hash with Platform set to "Auto-detect" resolves correctly.
- **Persistent saved pools**: All saved pools render as placeholder cards immediately on unlock, across all wallets, with pair/venue/token ID/wallet shown and "Not fetched" placeholders for live fields.
- **Fetch single saved pool**: Click **Fetch** on any saved-pool placeholder to pull live data for that token ID using the stored wallet and venue.
- **Card reformat**: LP cards now show three compact lines:
  1. Pair · Venue · Position ID
  2. Range · Current Price · % In/Out of Range (slider follows)
  3. Fees earned (with token breakdown) · Value · PnL · Holdings · Suggestion
- **Withdraw Fees button**: Added alongside **Collect Fees** and **Compound Fees** in Advanced mode. Calls the same Uniswap V3 `collect()` function as Collect Fees; "Withdraw" is provided as a clearer alternative label.

### HL1 Vaults tab fixes (Prompt 1)
- Save/Delete Vault buttons now toggle in place without clearing the scroll frame.
- Selector defaults to Account mode.
- Saved vaults auto-load on unlock and are shown for all wallets.

### Other v5.1 features
- Hyperliquid L1 vault positions via `/info` API (`userVaultEquits` + `vaultDetails`).
- Real LP uncollected fees via static `collect()` eth_call on the Project X PositionManager (selector `0xfc6f7865`).
- HyperEVM ERC-20 balance support (USDC, WHYPE, UBTC).
- Embedded `key_manager_agent` HTTP server on `127.0.0.1:8842` for fee collection/compounding.
- Bundled HTTPS/SSL stack + certifi CA certificates so the portable EXE works online.

## Download

| File | Size | Description |
|------|------|-------------|
| `coldstack.exe` | ~46 MB | Full GUI application — ColdStack v5.1 |

No installation required. Copy `USB_DEPLOYMENT/coldstack.exe` to any folder or USB drive and run it.

## Running from source

```bash
python src/gui_main_v5.py
```

## Build

```bash
python build_gui_v5.py
```

## Security

- Offline by default — zero network requests until you enable **Go Online** in Settings.
- Only public addresses are queried; private keys and mnemonics never leave the encrypted vault.
- Writer operations require explicit confirmation and sign via the embedded agent — the GUI never holds private keys.
