# ColdStack

*Offline-first crypto key vault with LP position management for Hyperliquid.*

[![GitHub release (latest by date)](https://img.shields.io/github/v/release/Roughn3ck/key_manager)](https://github.com/Roughn3ck/key_manager/releases) [![License](https://img.shields.io/badge/license-MIT-blue)](LICENSE) [![Platform](https://img.shields.io/badge/platform-Windows-blue)]() [![Status](https://img.shields.io/badge/status-Production-success)]()

**Latest release: [v5.1.3 — Swap Module + Wallet Card Redesign](https://github.com/Roughn3ck/key_manager/releases/tag/v5.1.3)**

---

## What ColdStack Does

ColdStack is a portable Windows application that securely stores your crypto wallet keys and manages your Hyperliquid LP positions — all from an encrypted vault that runs offline by default.

No browser. No browser extension. No cloud. Your keys never leave your machine.

### Three core capabilities:

**1. Key Vault** — Store and manage your crypto keys with military-grade encryption.
- BIP39 mnemonic derivation (EVM, BTC Taproot/SegWit/Legacy, Solana, Dash, Sui)
- AES-256-GCM + Argon2id encryption
- 20+ supported chains with CSV/Excel bulk import
- Password show/hide toggle, auto-lock with override

**2. Balance & Portfolio Tracking** — Optional read-only market data when you go online.
- Wallet balances across EVM, BTC, SOL, and Hyperliquid (L1 + HyperEVM)
- ERC-20 token support (USDC, WHYPE, UBTC on HyperEVM)
- CoinGecko price feeds with multi-currency display (USD, AUD, CAD, EUR, CHF)
- Hyperliquid vault tracking (deposits, APR, P&L)

**3. LP Position Management** — Full concentrated liquidity management for HyperEVM.
- Position cards with health status, range, fees, PnL, APR, days active
- Add Liquidity (+) with auto-balance "Zap In" — swap excess token to match position ratio
- Remove Liquidity (−) with percentage slider and estimated output
- Compound Fees — collect → swap → re-deposit in one flow
- Collect Fees, Close Position, and Save Pool operations
- Every write requires explicit user confirmation — no autonomous trading

---

## Quick Start

1. **Download** `coldstack.exe` from the [latest release](https://github.com/Roughn3ck/key_manager/releases)
2. **Run** it — double-click, no installation, no Python needed
3. **Click** "Initialize New Vault" and create your master password
4. **Add** your accounts, addresses, mnemonics, and private keys

That's it. ~50MB self-contained EXE. Works on any Windows 10/11 machine.

> **⚠️ No recovery** — if you lose your master password, your vault is gone. There is no reset.

---

## Architecture

```
+-------------+      +---------------------------+      +------------------+
|  USB Drive  |      |  ColdStack GUI            |      |  HyperEVM         |
|             |      |  (coldstack.exe)          |      |  RPC / Hyperliquid|
| key_vault   |----->|                           |<---->|  API              |
| .encrypted  |      |  ┌─ Wallet tab            |      |                  |
|             |      |  ├─ HL1 Vaults tab        |      |  Read: positions, |
|             |      |  └─ LP Positions tab      |      |  balances, fees   |
|             |      |                           |      |  Write: collect,  |
|             |      |  Embedded signing agent    |      |  swap, deposit    |
|             |      |  (localhost:8842)          |      |                  |
+-------------+      +---------------------------+      +------------------+
```

### Security Model

- **Offline by default** — zero network requests unless you toggle "Go Online"
- **Keys never exposed** — signing happens inside the embedded agent; private keys never returned to any caller
- **Every write is user-confirmed** — no bots, no auto-trading, no autonomous rebalancing
- **Auto-lock** — 5-minute inactivity timeout (with override checkbox)
- **No telemetry** — the only outbound request is a user-initiated update check

### Headless Agent

The signing agent also runs standalone for automation:

```bash
export KEY_MANAGER_PASSWORD="your-password"
python3 src/key_manager_agent.py --vault key_vault.encrypted --serve --port 8842
```

| Command | Purpose |
|---------|---------|
| `status` | Check vault state |
| `list_accounts` | Full account tree |
| `get_address` | Public address only (no keys) |
| `sign_tx` | Sign without broadcasting |
| `broadcast_tx` | Sign and broadcast to chain |
| `lock` | Lock the vault |

---

## LP Position Management

ColdStack speaks Uniswap V3 concentrated liquidity on HyperEVM. It reads and writes to the Project X PositionManager and SwapRouter contracts directly.

### What works today (v5.1.3)

| Operation | Status |
|-----------|--------|
| Scan positions (NFT ownership) | ✅ |
| Read position data (ticks, fee, liquidity) | ✅ |
| Read uncollected fees (collect eth_call) | ✅ |
| Display PnL, APR, days active | ✅ |
| Add Liquidity (+) | ✅ |
| Remove Liquidity (−) | ✅ |
| Compound Fees (collect → swap → re-deposit) | ✅ |
| Collect Fees | ✅ |
| Close Position | ✅ |
| Auto-Balance / Zap In (swap to match ratio) | ✅ |
| Swap (exactInputSingle) | ✅ |
| Edit Position (tick range, rebalance) | 🔜 v5.2 |
| EVM ↔ HL1 Transfer (bridge assets) | ✅ |
| Vault Explore + Deposit | ✅ |
| HyperEVM balance fiat conversion (WHYPE, UBTC) | ✅ |
| Close Position auto-refresh | ✅ |

### The Compound Fees Flow

```
1. Collect fees → PositionManager.collect()
2. Swap UBTC → WHYPE → SwapRouter.exactInputSingle()
3. Increase Liquidity → PositionManager.increaseLiquidity()
```

Three on-chain transactions, each confirmed independently. Gas is paid in WHYPE (~$0.005 per tx at 0.6 Gwei).

---

## Supported Chains

| Category | Chains |
|----------|--------|
| **EVM** | Ethereum, Arbitrum, Base, BSC, Polygon, Optimism, HyperEVM |
| **BTC** | Taproot (bc1p), SegWit (bc1q), Legacy |
| **Privacy** | ZEC, Monero |
| **Other** | Solana, Dash, Sui, Tron, Cosmos, Polkadot, Cardano, Ripple, Secret Network, THORChain |
| **BIP39 Derivation** | EVM, BTC (3 types), Solana, Dash, Sui |

---

## Technical Stack

| Component | Technology |
|-----------|-----------|
| GUI | CustomTkinter (dark theme) |
| Encryption | AES-256-GCM + Argon2id (cryptography library) |
| Build | PyInstaller (onefile EXE, ~50MB) |
| Python | 3.14 (GUI/EXE), 3.12 (headless agent) |
| Network | stdlib urllib.request only — no external HTTP dependencies |
| Price feeds | CoinGecko free tier (no API key) |

---

## Roadmap

### v5.2 — Edit Position
- Tick range editing (narrow/widen the range)
- Rebalance flow (close → swap → reopen at new range)
- Slippage protection (amountOutMinimum > 0)

### v5.3+ — Multi-Venue
- Krystal API integration
- Orca, Raydium, Cetus (Solana DEXs)
- Cross-chain LP position aggregation

### ColdTax — Tax Module
- Transaction database (shares encrypted vault file)
- Realized/unrealized gains tracking
- Tax reporting with multi-currency support (USD, AUD, CAD)
- ColdStack integration: reads LP position history from vault

### Mobile
- Companion app (read-only vault access)
- QR code signing delegation

---

## Development

```bash
# Run GUI from source
python src/gui_main_v5.py

# Build EXE
python build_gui_v5.py
# → USB_DEPLOYMENT/coldstack.exe

# CLI (script mode only)
python src/main.py derive-address --account "G5" --chain "EVM" --index 0
```

### Project Structure

```
src/
  gui_main_v5.py              Main GUI (6K+ lines)
  lp_liquidity_manager.py     Add/Remove/Edit liquidity dialogs
  evm_transfer_dialog.py      EVM ↔ HL1 transfer dialog
  vault_deposit_dialog.py     Vault deposit dialog
  key_manager_agent.py        Embedded signing agent
  vault_tracker.py            Hyperliquid vault positions
  lp_engine.py                LP position aggregation
  saved_pools.py              Persistent pool storage
  balance_engine.py           Wallet balance fetching
  price_engine.py             CoinGecko price feeds
  derivation_engine.py        BIP39 HD wallet derivation
  venue_adapters/
    hyperliquid_adapter.py    Read adapter (positions, fees, balances)
    hyperliquid_writer.py     Write adapter (collect, swap, increase, close)
    venue_writer.py           Abstract base + dataclasses
```

### Contributing

ColdStack is built by [Kris Racette](https://krisracette.me) at [Executive Mind](https://executivemind.io). Architecture by Slater (CTO). Code by Forge, Cline, and Claude Code.

---

*ColdStack — Your crypto keys, encrypted, portable, yours.*
*Full version history at the [releases page](https://github.com/Roughn3ck/key_manager/releases).*