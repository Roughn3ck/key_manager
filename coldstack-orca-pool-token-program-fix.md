# ColdStack: Orca — Per-Mint Token Program for Pool Tokens (Fix IncorrectProgramId at ATA Creation)

## Project
ColdStack — `/mnt/b/Blockchain/coldstack/`

## Root Cause
The Token-2022 fix (completed prompt `coldstack-orca-token2022-fix.md`) applied ONE token program — detected from the **position NFT mint** — to every `_ensure_ata_ix` call, including the **pool token** ATAs. But:

- Position NFT mint: owned by **Token-2022** (`TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb`)
- Pool token mints (token_mint_a / token_mint_b, e.g. SOL/USDC): owned by **SPL Token** (`TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA`)

Two failure modes from this:

1. **Wrong ATA address derived**: `_derive_ata_address(wallet, mint, token_program)` uses the token program in its PDA seeds. Deriving with Token-2022 for an SPL mint produces a DIFFERENT address than the wallet's real SPL ATA — so the existence check misses the existing ATA and builds a create instruction.
2. **Wrong create instruction**: The ATA create passes Token-2022 as the token program for an SPL-owned mint. On-chain, the ATA program (`ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL`) invokes Token-2022's `GetAccountDataSize` on the SPL mint → `IncorrectProgramId`:

```
Program ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL invoke [1]
Program log: Create
Program TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb invoke [2]
Program log: Instruction: GetAccountDataSize
Program log: Error: IncorrectProgramId
```

## The Rule
**Detect the token program PER MINT, not per position:**
- Pool token ATAs → program of `pool["token_mint_a"]` / `pool["token_mint_b"]` (each detected separately)
- Position NFT accounts (closePosition `token_program`, position_token_account ops) → program of the position mint
- The single `token_program` account in collectFees / modifyLiquidity / swap instructions → use the **pool mint** program (token_mint_a's). Original Whirlpools pools have both tokens on the same program; if `prog_a != prog_b`, log a warning.

`_build_swap_ix` already does this correctly (lines ~743-748) — follow that pattern.

## Fix — `src/venue_adapters/orca_writer.py`

### 1. `_build_collect_fees_ix` (~line 549)

Current:
```python
token_prog = self._get_token_program(pos["position_mint"])
_, ata_a = self._ensure_ata_ix(wallet_b, wallet, pool["token_mint_a"], token_program=token_prog)
_, ata_b = self._ensure_ata_ix(wallet_b, wallet, pool["token_mint_b"], token_program=token_prog)
```

Change to:
```python
token_prog_a = self._get_token_program(pool["token_mint_a"])
token_prog_b = self._get_token_program(pool["token_mint_b"])
_, ata_a = self._ensure_ata_ix(wallet_b, wallet, pool["token_mint_a"], token_program=token_prog_a)
_, ata_b = self._ensure_ata_ix(wallet_b, wallet, pool["token_mint_b"], token_program=token_prog_b)
```

And in the accounts list, the `token_program` account (account 8, ~line 563):
```python
# Current:
_AccountMeta(_b58decode(token_prog), False, False),               # token_program
# Change to:
_AccountMeta(_b58decode(token_prog_a), False, False),             # token_program (pool tokens)
```

### 2. `_build_modify_liquidity_ix` (~line 598)

Same change as above — replace `token_prog = self._get_token_program(pos["position_mint"])` with per-mint detection of `token_mint_a` / `token_mint_b`, use them for the two `_ensure_ata_ix` calls, and use `token_prog_a` for the `token_program` account (account 1, ~line 616).

### 3. `_build_close_position_ix` (~line 651)

**KEEP AS IS** — `token_prog = self._get_token_program(pos["position_mint"])` is CORRECT here. closePosition's token_program governs the position NFT mint (Token-2022). Do not change.

### 4. `collect_fees` method (~line 910)

Current:
```python
token_prog = self._get_token_program(position_mint)
extra_ixs = []
ata_ix_a, _ = self._ensure_ata_ix(wallet_b, wallet, pool["token_mint_a"], token_program=token_prog)
if ata_ix_a:
    extra_ixs.append(ata_ix_a)
ata_ix_b, _ = self._ensure_ata_ix(wallet_b, wallet, pool["token_mint_b"], token_program=token_prog)
if ata_ix_b:
    extra_ixs.append(ata_ix_b)
```

Change to:
```python
token_prog_a = self._get_token_program(pool["token_mint_a"])
token_prog_b = self._get_token_program(pool["token_mint_b"])
extra_ixs = []
ata_ix_a, _ = self._ensure_ata_ix(wallet_b, wallet, pool["token_mint_a"], token_program=token_prog_a)
if ata_ix_a:
    extra_ixs.append(ata_ix_a)
ata_ix_b, _ = self._ensure_ata_ix(wallet_b, wallet, pool["token_mint_b"], token_program=token_prog_b)
if ata_ix_b:
    extra_ixs.append(ata_ix_b)
```

### 5. `close_position` method — two sites

Site A (~line 944), same replacement as `collect_fees` above (per-mint `token_prog_a`/`token_prog_b`).

Site B — the step-2 fallback (~line 972):
```python
if not extra_ixs:
    ata_ix_a, _ = self._ensure_ata_ix(wallet_b, wallet, pool["token_mint_a"], token_program=token_prog)
    ata_ix_b, _ = self._ensure_ata_ix(wallet_b, wallet, pool["token_mint_b"], token_program=token_prog)
```
Change both `token_program=` args to the per-mint detected values (use the `token_prog_a` / `token_prog_b` computed in site A — hoist them above the `if` block so both sites share them).

### 6. `_build_swap_ix` (~line 783)

The ATA creation here is already per-mint, but the swap instruction's `token_program` account (account 0) still hardcodes SPL:
```python
_AccountMeta(_b58decode(SPL_TOKEN_PROGRAM_ID), False, False),
```
Change to:
```python
_AccountMeta(_b58decode(token_prog_a), False, False),
```
(uses the `token_prog_a` already detected at line ~743). Also switch lines 743-744 from direct `_detect_token_program(...)` calls to `self._get_token_program(...)` so the cache is used consistently.

## Verification (grep checks after editing)

```bash
grep -n "_ensure_ata_ix" src/venue_adapters/orca_writer.py
```
Every call site passing a pool mint (`token_mint_a`/`token_mint_b`) must use a per-mint detected program. No call site may pass the position mint's program for a pool mint ATA.

```bash
grep -n "token_prog = self._get_token_program(pos\[" src/venue_adapters/orca_writer.py
```
Must match ONLY `_build_close_position_ix`.

## Why this works end-to-end for the G3 close
- decreaseLiquidity (skipped — liquidity already 0 from the earlier successful run)
- collectFees: derives the wallet's REAL SPL ATAs (they already exist from the earlier successful decrease run), finds them, returns None create-ixs → clean collectFees tx with SPL as token_program
- closePosition: position mint detected as Token-2022 → correct program passed → NFT burned, position closed, rent reclaimed

**Do NOT rebuild the EXE, update README.md, or STATUS.md yet.**
**Do NOT git push or create GitHub releases yet.**