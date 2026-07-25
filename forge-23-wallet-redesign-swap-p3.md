# Forge-23: Prompt 3 of 3 — Build, Documentation, and Git Push

## Project
ColdStack (`/mnt/b/Blockchain/coldstack/`)

## Context
Prompt 1 redesigned the wallet card buttons. Prompt 2 created the swap dialog module. This final prompt handles documentation updates, build, and git push.

## Task 1: Update Documentation

### File: `STATUS.md`
Add a new section at the top:

```markdown
## v5.1.3 - Swap Module + Wallet Card Redesign (July 2026)

### New Features
- **Swap Dialog**: New `src/swap_dialog.py` module for token swaps on HyperEVM
  - Swap between HYPE, WHYPE, USDC, UBTC via Uniswap V3 SwapRouter
  - Wrap/unwrap WHYPE ↔ HYPE natively
  - Auto pool selection (prefers lowest fee tier)
  - Price quote from pool slot0
  - Slippage protection (default 1%, configurable)
  - Gas estimate display
  - Bridge tab: EVM ↔ HL1 transfers (reuses evm_transfer_dialog logic)
- **Wallet Card Redesign**:
  - Copy moved to inline icon (⧉) next to address text
  - "Delete" renamed to "Remove"
  - "EVM ↔ HL1" button removed from address cards (moved into Swap dialog Bridge tab)
  - New "Swap" button on supported chains
  - Uniform button widths (all 100px)
  - Improved hover states and colors
- **Unwrap WHYPE**: Part of the swap module — swap WHYPE → HYPE calls WHYPE.withdraw()

### New Files
- `src/swap_dialog.py` — Self-contained swap dialog module

### Files Changed
- `src/gui_main_v5.py` — Address card redesign, swap button, removed EVM↔HL1 button
- `build_gui_v5.py` — Version strings v5.1.3, swap_dialog hidden import
- `STATUS.md` — v5.1.3 changelog
- `README.md` — Updated for v5.1.3
- `.clinerules` — Version update

### Version String Updates
Update ALL version references from v5.1.2 to v5.1.3:
1. `src/gui_main_v5.py` — Header comment (line 5), lock screen version label (~line 412), update check `current_version` (~line 944)
2. `build_gui_v5.py` — All version strings (docstring, print statements, launcher script)
3. `README.md` — Latest release link and version
4. `STATUS.md` — Current Version line
5. `.clinerules` — Current production version
6. `AGENTS.md` — Production version reference
7. `CLAUDE.md` — Production version reference

Lock screen should show: `v5.1.3 - ColdStack | Swap Module + Wallet Card Redesign`

Update check `current_version` should be: `"5.1.3"`

### build_gui_v5.py Updates
- Update all v5.1.2 → v5.1.3 version strings
- Add `--hidden-import=swap_dialog` to the PyInstaller args
- Update `PREVIOUS_VERSION_TAG = "v5_1_2"` (backup the v5.1.2 EXE)

## Task 2: Syntax Check

Run `python -m py_compile` on ALL modified files:
- `src/gui_main_v5.py`
- `src/swap_dialog.py`
- `build_gui_v5.py`

All must pass before building.

## Task 3: Build the EXE

```bash
python build_gui_v5.py
```

This must succeed. The EXE should be at `USB_DEPLOYMENT/coldstack.exe`.

## Task 4: Git Commit and Push

```bash
git add -A
git commit -m "v5.1.3: Swap module + wallet card redesign

New features:
- Swap dialog (swap_dialog.py): HYPE/WHYPE/USDC/UBTC swaps on HyperEVM
- Wrap/unwrap WHYPE ↔ HYPE
- Auto pool selection (lowest fee tier)
- Price quotes from pool slot0
- Slippage protection (1% default)
- Bridge tab (EVM ↔ HL1 transfers)
- Wallet card redesign: inline copy icon, uniform buttons, Swap button
- 'Delete' renamed to 'Remove'
- EVM↔HL1 button moved into Swap dialog"

git push origin master
```

## Constraints
- This is the FINAL prompt — do all documentation, build, and push
- Update ALL version strings consistently
- Do NOT leave the EXE stale — build after compiling
- Do NOT push if the build fails