# ColdStack: Orca — Use closePositionWithTokenExtensions for Token-2022 Position NFTs

## Project
ColdStack — `/mnt/b/Blockchain/coldstack/` (v5.3.1 line)

## Root Cause — DEFINITIVE (fully verified on-chain)

Orca's current UI opens positions via `open_position_with_token_extensions` — the position NFT is a **Token-2022 mint with a metadata extension**. The legacy `closePosition` instruction on the Whirlpools program types `position_mint` as **SPL-only** (`Account<token::Mint>`), so it can NEVER close a Token-2022 position — no parameter change can fix it. That's the `AccountOwnedByWrongProgram` (3007) error we've been chasing.

The program has a **dedicated instruction for this**: `closePositionWithTokenExtensions`.

Evidence chain (verified today, 2026-08-30):
1. G3's position NFT mint `7g4WnDf...` is Token-2022-owned (getAccountInfo + getTokenAccountsByOwner under `TokenzQd...`)
2. Position PDA `FcSLqV...` under `whirLbMiic` exists (216 bytes) — genuine v1 Whirlpool position
3. Anchor v0.30.1 source: `AccountOwnedByWrongProgram` logs `Left = ACTUAL owner (TokenzQd)`, `Right = EXPECTED owner (Tokenkeg)` — legacy closePosition expects SPL
4. Orca source (`orca-so/whirlpools`, `programs/whirlpool/src/instructions/close_position_with_token_extensions.rs`): dedicated close instruction for Token-2022 positions — burns NFT, closes token account, **closes the mint account too**, closes position (rent to receiver)
5. **Full simulation of the new instruction against mainnet succeeded** (`err: None`): BurnChecked → CloseAccount ×2 → success, 16833 CU. The position is already empty (liquidity=0, fees=0, rewards=0), so the close will succeed immediately once this fix is in.

## The New Instruction vs Legacy

Identical 6-account layout. Only the discriminator and the token program requirement differ:

| | Legacy `closePosition` | `closePositionWithTokenExtensions` |
|---|---|---|
| Discriminator | `7b86510031446262` | `01b6873b9b1963df` |
| Accounts | authority(sig), receiver(mut), position(mut), position_mint(mut), position_token_account(mut), token_program | **same order** |
| token_program | SPL Token program | **MUST be Token-2022** (`TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb`) — hard requirement |
| position_mint type | SPL-only | Token-2022 (`InterfaceAccount<Mint>`) |
| Precondition | position empty | position empty (liquidity=0, fee_owed=0, reward_owed=0), not locked |

Anchor discriminator = `sha256("global:" + snake_case_name)[:8]`. Verified: computed legacy matches `DISC_CLOSE_POSITION` in the code; computed TE = `01b6873b9b1963df`.

## Fix — `src/venue_adapters/orca_writer.py`

### 1. Add the discriminator (near the other `DISC_*` constants)

```python
# closePositionWithTokenExtensions — close instruction for Token-2022 position NFTs
# (positions opened via Orca's current UI mint Token-2022 NFTs with metadata extension;
#  the legacy closePosition instruction is SPL-only and rejects them)
DISC_CLOSE_POSITION_TE = bytes.fromhex("01b6873b9b1963df")
```

### 2. Branch in `_build_close_position_ix` (~line 645)

Current (end of method):
```python
token_prog = self._get_token_program(pos["position_mint"])
whirlpool_prog = _b58decode(WHIRLPOOL_PROGRAM)
accounts = [
    _AccountMeta(wallet_b, True, False),
    _AccountMeta(wallet_b, False, True),                              # receiver
    _AccountMeta(_b58decode(pos["position_address"]), False, True),
    _AccountMeta(_b58decode(pos["position_mint"]), False, True),
    _AccountMeta(_b58decode(pos_token_acct), False, True),
    _AccountMeta(_b58decode(token_prog), False, False),
]
return whirlpool_prog, accounts, DISC_CLOSE_POSITION
```

Change to:
```python
token_prog = self._get_token_program(pos["position_mint"])
# Token-2022 position NFTs (Orca's current UI) require the dedicated
# closePositionWithTokenExtensions instruction — same account layout,
# different discriminator, token program MUST be Token-2022.
disc = (DISC_CLOSE_POSITION_TE
        if token_prog == TOKEN_2022_PROGRAM_ID
        else DISC_CLOSE_POSITION)
whirlpool_prog = _b58decode(WHIRLPOOL_PROGRAM)
accounts = [
    _AccountMeta(wallet_b, True, False),
    _AccountMeta(wallet_b, False, True),                              # receiver
    _AccountMeta(_b58decode(pos["position_address"]), False, True),
    _AccountMeta(_b58decode(pos["position_mint"]), False, True),
    _AccountMeta(_b58decode(pos_token_acct), False, True),
    _AccountMeta(_b58decode(token_prog), False, False),
]
return whirlpool_prog, accounts, disc
```

Ensure `TOKEN_2022_PROGRAM_ID` is imported from `orca_adapter` (it should already be — verify the import line).

### 3. No other changes needed

- `_get_position_token_account` already finds Token-2022 NFT accounts (mint filter works across both programs)
- `_get_token_program` detection is verified correct (returns TokenzQd for this mint)
- `close_position` flow: decrease (step 1) and collectFees (step 2) already work for Token-2022 NFT accounts on the deployed program — only step 3 (close) needed the new instruction
- The precondition "position empty" is handled by the existing flow (decrease → collectFees before close)

## Verification

1. `python -m py_compile src/venue_adapters/orca_writer.py`
2. Grep check:
```bash
grep -n "DISC_CLOSE_POSITION_TE" src/venue_adapters/orca_writer.py
grep -n "disc = (DISC_CLOSE_POSITION_TE" src/venue_adapters/orca_writer.py
```
3. Runtime test (Kris, from the GUI): close the Orca G3 position — expected: one clean close transaction (position is already empty). The NFT is burned, the token account AND mint account are closed, position rent returns to the wallet. The pool card should then disappear from the LP tab on the next refresh, and the position should show as closed.

## Known Future Consideration (do NOT implement now)

If a future close ever fails with `ClosePositionNotEmpty` on a pool with **active rewards**, the close flow will need a `collect_reward` step before close (the empty check includes reward_owed). Not needed for this position (rewards all zero).

**Do NOT rebuild the EXE, update README.md, or STATUS.md yet.**
**Do NOT git push or create GitHub releases yet.** The final v5.3.1 wrap-up prompt (docs, VERSION bump, build, release, backups) comes after the G3 close is confirmed.