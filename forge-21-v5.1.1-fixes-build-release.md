# Forge Prompt 21: v5.1.1 Fixes — Version String, Hover Tooltips, Rebuild, Docs, Release

## Project
ColdStack (`/mnt/b/Blockchain/coldstack/`)
**Version: v5.1.1**

This is the FINAL prompt for v5.1.1. Apply all fixes, update docs, rebuild, and release.

## Files to modify
1. `src/gui_main_v5.py` — Version string + hover tooltips on + / − / ✎ buttons
2. `build_gui_v5.py` — Version strings
3. `AGENTS.md` — Update version reference
4. `.clinerules` — Update version reference if needed
5. `README.md` — Update version
6. `STATUS.md` — Add v5.1.1 section
7. `release_notes_v5.1.1.md` — Create release notes

## Fixes Required

### Fix 1: Version string on unlock screen

File: `src/gui_main_v5.py` (~line 410)

Change:
```python
text="v5.1 - ColdStack | Vault Tracking + HyperEVM ERC-20",
```
To:
```python
text="v5.1.1 - ColdStack | Add/Remove Liquidity + Gas Fix + Fee Tracking",
```

Also update the file header comment (~line 5):
```python
Version: v5.1.1 (July 2026) - LP Liquidity Manager + Gas Price Fix + Fee Tracking
```

### Fix 2: Hover tooltips on + / − / ✎ buttons

File: `src/gui_main_v5.py`, in `_lp_render_card()` where the + / − / ✎ buttons are created (~line 4690-4725)

Add hover tooltips to the three buttons. CustomTkinter doesn't have a built-in tooltip, so use a simple approach — bind `<Enter>` and `<Leave>` events to update a status label or use tkinter's balloon help.

The simplest approach: use a small tooltip helper at the top of the file (or inline in `_lp_render_card`):

```python
# Add this helper function near the top of gui_main_v5.py (after imports)

def _add_tooltip(widget, text):
    """Bind a simple hover tooltip to a widget."""
    def on_enter(event):
        widget._tooltip = ctk.CTkLabel(widget, text=text,
                                        font=ctk.CTkFont(size=10),
                                        fg_color="gray20",
                                        corner_radius=4,
                                        padx=8, pady=4)
        widget._tooltip.place(x=0, y=-25, anchor="nw")
    def on_leave(event):
        if hasattr(widget, "_tooltip"):
            widget._tooltip.destroy()
            del widget._tooltip
    widget.bind("<Enter>", on_enter)
    widget.bind("<Leave>", on_leave)
```

Then add tooltips to each button:
```python
# After creating the + button:
_add_tooltip(add_btn, "Add Liquidity")

# After creating the − button:
_add_tooltip(remove_btn, "Remove Liquidity")

# After creating the ✎ button:
_add_tooltip(edit_btn, "Edit Position")
```

**Important:** The buttons are created with `lambda pos=position:` callbacks. Make sure the tooltip binding doesn't interfere with the lambda. Bind tooltips AFTER the button creation, before `.pack()`.

**Alternative approach if the place() tooltip is problematic inside a packed frame:** Use the status bar at the bottom of the window. Bind `<Enter>` on the button to update the status bar text, and `<Leave>` to clear it. This is simpler and avoids z-order issues.

```python
def _add_status_tooltip(widget, status_label, text):
    """Show text in a status label on hover."""
    widget.bind("<Enter>", lambda e: status_label.configure(text=text))
    widget.bind("<Leave>", lambda e: status_label.configure(text=""))
```

Use whichever approach works cleanly with the existing card layout. The goal: when the user hovers over +, they see "Add Liquidity". Over −, "Remove Liquidity". Over ✎, "Edit Position".

### Fix 3: Build script version strings

File: `build_gui_v5.py`

Update all `v5.1` references to `v5.1.1`:
- Line 3: `PyInstaller build script for ColdStack GUI v5.1.1.`
- Line 6: `Version: v5.1.1 (July 2026) - LP Liquidity Manager + Gas Fix + Fee Tracking`
- Line 15: `4. Version strings updated to v5.1.1`
- Line 36: `"""Run PyInstaller to build the ColdStack v5.1.1 EXE."""`
- Line 37: `print("Building ColdStack v5.1.1 executable...")`

### Fix 4: AGENTS.md

File: `AGENTS.md`

Update the production version reference:
```
Production version is v5.1.
```
To:
```
Production version is v5.1.1.
```

Add a note about the new module:
```
## Modules

- `src/lp_liquidity_manager.py` (v5.1.1 NEW): Add/Remove/Edit liquidity dialogs. Separated from gui_main_v5.py for maintainability. Contains AddLiquidityDialog (with auto-balance/Zap In), RemoveLiquidityDialog (with percentage slider), and EditPositionDialog (stub for v5.2).
```

### Fix 5: .clinerules

File: `.clinerules`

Update any version references from v5.1 to v5.1.1. If no version is mentioned, add:
```
## Version
Current: v5.1.1
```

### Fix 6: README.md

File: `README.md`

Update version to v5.1.1. Add a v5.1.1 section with the new features:
- Add/Remove Liquidity UI (+ / − icons on position cards)
- Auto-balance (Zap In) toggle for adding liquidity
- Gas price 6x boost for HyperEVM (fixes "nonce too high" rejections)
- Position decode offset fix (fixes OverflowError in compound fees)
- Fee status detection (ok/zero/error)
- Fee/performance tracking (APR, PnL, days active)
- `_evm_rpc_call` bug fix in fee tracking
- New `lp_liquidity_manager.py` module
- Unstubbed `swap()` method in hyperliquid_writer.py

### Fix 7: STATUS.md

File: `STATUS.md`

Add at the top (before the v5.1 section):

```markdown
## v5.1.1 - Add/Remove Liquidity + Gas Fix + Fee Tracking (July 2026)

### New Features
- **Add/Remove Liquidity UI**: + / − / ✎ icons on LP position cards next to Value
  - "+" opens Add Liquidity dialog with token inputs, 50%/Max buttons, and auto-balance (Zap In) toggle
  - "−" opens Remove Liquidity dialog with percentage slider and estimated output
  - "✎" opens Edit Position stub (coming in v5.2)
- **Auto-balance (Zap In)**: When enabled, system swaps excess token to match position ratio before depositing
- **New module `lp_liquidity_manager.py`**: All liquidity management dialogs in a separate file
- **Unstubbed `swap()` method**: SwapRouter.exactInputSingle now working for standalone swaps
- **`get_swap_quote()`**: Read-only swap price estimation for UI display

### Bug Fixes
- **Position decode offset fix (Forge-17/18)**: `_read_position_data()` now reads fee/tickLower/tickUpper from correct byte offsets (was reading token0/token1 as fee/ticks → OverflowError)
- **Gas price 6x boost**: Increased from 3x to 6x to clear HyperEVM mempool rejections (eth_gasPrice returns 0.1 Gwei, market is ~0.28 Gwei, 6x = 0.6 Gwei)
- **Fee tracking bug**: Fixed `_evm_rpc_call` called as instance method instead of module function
- **Compound fees flow**: Collect → Swap → IncreaseLiquidity now works end-to-end (IncreaseLiquidity fails only on dust amounts, which is expected contract behavior)
- **Nonce retry logic**: Fresh nonce fetch on "nonce too high" with 3s delay

### Verification
- Compound fees: collect ✅ → swap ✅ → increaseLiquidity ✅ (on meaningful amounts)
- Add/Remove liquidity dialogs open from position cards
- Position decode: tick_lower=-300060, tick_upper=-297120, fee=3000 (correct)
- Fee tracking: PnL, APR, days active display on position cards
```

### Fix 8: release_notes_v5.1.1.md

File: `release_notes_v5.1.1.md` (new file in repo root)

Create markdown release notes matching the style of the v5.1 release on GitHub. Use clean markdown (not HTML). Structure:

```markdown
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
```

## Build

After all fixes are applied, rebuild:

```bash
cd /mnt/b/Blockchain/coldstack
python build_gui_v5.py
cp USB_DEPLOYMENT/coldstack.exe /mnt/b/OpenClaw/.openclaw/workspace/kimi/portfolios/executivemind/coldstack.exe
```

## Git Push

```bash
cd /mnt/b/Blockchain/coldstack
git add -A
git commit -m "v5.1.1: Add/Remove Liquidity UI, gas 6x boost, position decode fix, fee tracking, swap unstub"
git tag v5.1.1
git push origin main
git push origin v5.1.1
```

## GitHub Release

Create a new release on GitHub:
- Tag: `v5.1.1`
- Title: `v5.1.1 — Add/Remove Liquidity + Gas Fix + Fee Tracking`
- Body: Copy the contents of `release_notes_v5.1.1.md`
- Asset: Attach `USB_DEPLOYMENT/coldstack.exe`

Use: `gh release create v5.1.1 --title "v5.1.1 — Add/Remove Liquidity + Gas Fix + Fee Tracking" --notes-file release_notes_v5.1.1.md USB_DEPLOYMENT/coldstack.exe`

## Backup

After the release is created, back up the source files:
```bash
mkdir -p backups/v5.1.1
cp -r src/ build_gui_v5.py AGENTS.md .clinerules README.md STATUS.md release_notes_v5.1.1.md backups/v5.1.1/
```

## Summary of All Changes

| File | Change |
|------|--------|
| `src/gui_main_v5.py` | Version string v5.1.1, hover tooltips on +/−/✎ buttons |
| `build_gui_v5.py` | Version strings v5.1.1 |
| `AGENTS.md` | Update production version to v5.1.1, add lp_liquidity_manager module note |
| `.clinerules` | Update version reference |
| `README.md` | Update version, add v5.1.1 features section |
| `STATUS.md` | Add v5.1.1 section at top |
| `release_notes_v5.1.1.md` | New file — GitHub release notes |