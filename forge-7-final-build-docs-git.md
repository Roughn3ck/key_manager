# Forge Prompt 7 (FINAL) — Build, Documentation, Git Push, GitHub Release

**Project:** ColdStack
**Files:** All modified files in `src/`, plus `README.md`, `STATUS.md`, `AGENTS.md`
**Version:** v5.1 → v5.2

**This is the FINAL prompt. After all previous prompts are complete and verified:**

## Step 1: Syntax Check All Modified Files

```bash
cd /mnt/b/Blockchain/coldstack
python -m py_compile src/gui_main_v5.py
python -m py_compile src/venue_adapters/hyperliquid_adapter.py
python -m py_compile src/lp_engine.py
python -m py_compile src/vault_tracker.py
```

All must pass with no errors.

## Step 2: Update Documentation

### STATUS.md

Add a v5.2 section at the top:

```markdown
## v5.2 (July 2026)

### Bug Fixes
- Fee buttons (Compound, Collect, Close Position) now visible in Standard mode (not just Advanced)
- "Withdraw Fees" button replaced with "Close Position" (decreaseLiquidity + collect)
- Canceling a fee operation confirmation no longer clears the screen
- Switching Standard/Advanced mode re-renders LP cards immediately
- Currency display: Vault and LP tabs now show USD + selected display currency (e.g. AUD, CAD)
- Scan Wallet NFT detection: increased scan window from 2000 to 5000, with fallback extension
- Transaction hash entry: better error message suggesting numeric Position ID
- Pool address entry: shows pool price/token pair without error note
- Fee reporting: live fee data via collect() eth_call with wallet_address context
- Auto-detect + Position ID: user-friendly "Select a Platform" notification
- Fetch Position with empty Position ID: falls through to Scan Wallet
- Scan Wallet button: enabled immediately when wallet address is available

### UI Improvements
- % In Range: green/bold when in range, red/bold when out of range
- Value: green/bold, positioned after Holdings
- Suggestion: yellow/bold, at the end of Line 3
- Scan Wallet: time warning dialog before starting full scan
```

### README.md

Update the version references from v5.1 to v5.2. Update the "Known Issues" section to remove fixed items. Update the feature list.

### AGENTS.md

Update the "Known Issues" section to remove fixed items. Update the version reference from v5.1 to v5.2.

## Step 3: Build the EXE

```bash
cd /mnt/b/Blockchain/coldstack
python build_gui_v5.py
```

The build must succeed. If it fails, fix the error before continuing. The EXE should be in `USB_DEPLOYMENT/coldstack.exe`.

## Step 4: Git Push

```bash
cd /mnt/b/Blockchain/coldstack
git add -A
git status  # Review what's being committed
git commit -m "v5.2: Fee buttons in Standard mode, Close Position, currency display, scan detection fix, card formatting"
git push origin main
```

## Step 5: GitHub Release

1. Review the existing releases at https://github.com/Roughn3ck/key_manager/releases
2. If v5.1 release exists with a coldstack.exe asset: this is a new version (v5.2), create a new release
3. Create a new release with tag `v5.2`
4. Title: `ColdStack v5.2 — Fee Buttons, Close Position, Currency Display, Scan Fix`
5. Upload `USB_DEPLOYMENT/coldstack.exe` as the release asset
6. Copy the v5.2 changelog from STATUS.md into the release description

## Step 6: Backup

Create a backup of the current src and related files:

```bash
cd /mnt/b/Blockchain/coldstack
mkdir -p backups/5.2
cp -r src/ backups/5.2/src/
cp build_gui_v5.py backups/5.2/
cp AGENTS.md backups/5.2/
cp README.md backups/5.2/
cp STATUS.md backups/5.2/
cp .clinerules backups/5.2/
cp CLAUDE.md backups/5.2/
```

## Verification Checklist

- [ ] All py_compile checks pass
- [ ] EXE builds successfully
- [ ] EXE runs and GUI opens
- [ ] Fee buttons visible in Standard mode
- [ ] "Close Position" button works (or at least shows confirmation)
- [ ] Currency shows USD + selected currency in Vault and LP tabs
- [ ] Scan Wallet finds known positions
- [ ] Card formatting: green % In Range, green Value, yellow Suggestion
- [ ] Cancel on fee dialog preserves position on screen
- [ ] Mode switch re-renders cards
- [ ] Git push succeeds
- [ ] GitHub release created with EXE asset
- [ ] Backup created in backups/5.2/