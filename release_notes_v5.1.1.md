# ColdStack v5.1.1 — Add/Remove Liquidity + Gas Fix + Fee Tracking

v5.1.1 brings manual liquidity management to ColdStack. Add or remove liquidity from your LP positions directly in the GUI — no browser needed. Plus critical fixes for compound fees, gas price, and fee tracking.

---

## ➕ Add / Remove Liquidity

Manage your LP positions without leaving ColdStack.

- **+ (Add Liquidity)**: Green button on each LP position card. Opens a dialog with token inputs, 50%/Max buttons, wallet balances, and an auto-balance toggle.
- **− (Remove Liquidity)**: Orange button. Opens a dialog with a percentage slider (0-100%), estimated token output, and optional "collect fees after removal" checkbox.
- **✎ (Edit Position)**: Purple button. Placeholder for v5.2 (tick range editing, rebalancing).
- **New module** (`src/lp_liquidity_manager.py`): All liquidity dialog logic in a separate file — keeps the main GUI lean.

### Auto-Balance (Zap In)

When adding liquidity, toggle "Auto-Balance (Zap In)" to enter one token amount and let the system swap the excess to match your position's current ratio. Similar to Krystal's Zap In feature.

---

## 🔧 Bug Fixes

### Position Decode Fix
Fixed `_read_position_data()` byte offsets — was reading token0/token1 addresses as fee/tickLower/tickUpper, causing `OverflowError: Result too large` after every collect. Now reads from correct offsets (fee at 256:320, tickLower at 320:384, tickUpper at 384:448).

### Gas Price 6x Boost
HyperEVM's `eth_gasPrice` returns 0.1 Gwei (the base fee), but the actual market gas price is ~0.28 Gwei. The 3x boost (0.3 Gwei) was barely at market rate and caused "nonce too high" rejections. Increased to 6x (0.6 Gwei) — well above market, clears the mempool reliably.

### Compound Fees Flow
The full compound flow now works end-to-end: Collect → Swap (UBTC→WHYPE) → IncreaseLiquidity. Previously failed at the position decode step with OverflowError. Now completes successfully on meaningful fee amounts.

### Fee Tracking
Fixed `_evm_rpc_call` being called as an instance method on `HyperliquidAdapter` instead of as a module-level function. Fee collection events now properly recorded with `first_seen_date`, `initial_deposit_usd`, `total_fees_collected_usd`, and `fee_history`.

### Fee Status Detection
`_estimate_uncollected_fees()` now returns a status (`ok`/`zero`/`error`) alongside amounts. The UI distinguishes between "No uncollected fees" (genuinely zero) and "RPC unreachable" (connection failed).

---

## 📊 Position Tracking

LP position cards now show:
- **PnL**: Capital gain/loss with percentage
- **APR**: Annualized fee yield based on collected fees
- **Days Active**: Time since first position load

---

## 🔄 Swap Method

The `swap()` method in `hyperliquid_writer.py` is no longer stubbed. Uses the confirmed SwapRouter (`0x1ebdfc75ffe3ba3de61e7138a3e8706ac841af9b`) with `exactInputSingle`. Also added `get_swap_quote()` for read-only price estimation.

---

**Full Changelog**: v5.1...v5.1.1
