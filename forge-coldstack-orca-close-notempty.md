# Forge Prompt — ColdStack: Orca closePosition `ClosePositionNotEmpty` (6005) — round 2

Follow-up to `forge-coldstack-orca-close-token2022.md`. Your IllegalOwner fix WORKED: the
close tx now parses and executes `ClosePositionWithTokenExtensions` cleanly (disc
01b6873b9b1963df, 6 accounts). The GUI rerun now fails one step later:

```
✗ Close error: Agent error (broadcast_solana_tx): Solana RPC error: {'code': -32002,
'message': 'Transaction simulation failed', 'data': {'err': {'InstructionError': [0,
{'Custom': 6005}]}, 'logs': ['Program whirLbMiic… invoke [1]',
'Program log: Instruction: ClosePositionWithTokenExtensions',
'Program log: AnchorError occurred. Error Code: ClosePositionNotEmpty. Error Number: 6005.
Error Message: Position is not empty It cannot be closed.', …]}}
```

The GUI dialog truncates the log — the missing lines after the message ("AnchorError
thrown in <file>:<line>", "Invoked at instruction/account #N") are the smoking gun. One of
your first tasks is to capture them.

## Verified evidence (Slater, on-chain, 2026-09-21 — treat as ground truth)

Position: K&P Orca cbBTC/SOL, whirlpool `CeaZcxBNLpJWtxzt58qQmfMBtJY8pQLvursXTJYGQpbN`,
program `whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc`.

- Position account `F98SmNgmft21dRAwfXGPtWu95Kb1WcSm58WgaQzUZQQR` (216 bytes): whirlpool +
  position_mint anchors verified; **liquidity @72 = 0** (the first attempt's decrease
  SUCCEEDED — withdrawn SOL+cbBTC are in K&P's ATAs); ticks −91040/−89024; the tail
  (offsets 128–216) is ALL ZERO; only non-zero tail fields are two u128-growth-looking
  values at 96–112 (0x014f7f0b7fe5b4c294) and 120–128 (0x000b9e80b554ccae).
- Position mint `FbNHxe9V…` (Token-2022, space 451) and the position account still exist
  → close never completed. The rerun ran: decrease skipped (liquidity 0), collectFees tx
  SUCCEEDED, close tx failed at simulation (never landed — nothing changed on-chain).
- **The contradiction:** by a v1-style layout parse the position is EMPTY (liquidity 0,
  fee_owed 0, reward tails zero) — yet the program rejects with "not empty". So EITHER
  the 2.x Position layout (216 bytes ≠ v1's ~225) puts liquidity/fee/reward fields at
  offsets where the bytes are non-zero, OR the tx's `position` account was not F98SmN.
- Pool reward slot 0 is ACTIVE (mint `orcaEKTdK7LKz57vaAYr9QeNsVEPfiu6QeMU1kektZE`, 6
  decimals, supply 75M; vault `Cqj7caoFayYa26ApYfwg3AB3tjPkhzJa93SncMPcULNq` holds
  15047 raw = 0.015047 tokens; reward_last_updated = today). **Vault math proves total
  unclaimed reward owed ≤ ~15047 raw** — so reward amount_owed is NOT the 3.27e15 value
  at offset 120; the position cannot owe more than the vault ever held. Rewards owed ≈ 0.
- The K&P portfolio record is CORRECT: coldtrack.db LP_POSITIONS row 4 stores TOKEN_ID =
  `FbNHxe9V…` and POSITION_ADDRESS = `F98SmN…` — no seed error. `_get_position_data`
  derives the position PDA from the mint (["position", mint] seeds) — correct mint →
  correct account.
- ColdStack's liquidity parse is consistent with the program: the first attempt's
  decrease ran with the exact on-chain liquidity and succeeded.
- Note: Slater's independent read-only simulation of the new 6-account close layout
  against F98SmN was blocked by public-RPC rate limiting from this machine — run yours.

## Step 1 — Reproduce with FULL logs (no code changes yet)

1. Build the close ix exactly as `_build_close_position_ix` does for this position and
   simulate it (sigVerify=false, no keys, NEVER broadcast). Print the COMPLETE logs array.
2. Log the exact accounts the builder passes (position, position_mint,
   position_token_account, wallet/authority) plus a hexdump of the position account it
   resolved — is it F98SmN or something else?
3. Also fix the GUI error path to surface the full RPC `logs` array (the dialog currently
   truncates the anchor log — future errors must not lose the "thrown at" lines).

Outcomes: if the sim returns `err: null` against F98SmN, the GUI run must have carried
different accounts — instrument and find where they diverge (stale cache, dialog passing
a different position_id, etc.). If the sim returns 6005, the constraint sees something the
v1-style parse doesn't — go to Step 2.

## Step 2 — Pin the authoritative 2.x source (github.com/orca-so/whirlpools)

1. The deployed program's `Position` state struct (state/position.rs or equivalent):
   exact field order/offsets for the 216-byte account. Reconcile with the hexdump above —
   identify what the non-zero values at 96–128 actually are, and where fee_owed_a/b and
   reward amount_owed really live.
2. `close_position` and `close_position_with_token_extensions` handlers: the EXACT set of
   constraints behind error 6005 `ClosePositionNotEmpty` (liquidity? fees? rewards? more?).
   Quote the constraint code in your report.

## Step 3 — Fix per the evidence

Ranked candidates (fix what the evidence supports, not all of them):
- **Position decoder wrong for 2.x** → rewrite `_decode_position_data` to the authoritative
  layout (this also future-proofs GUI display of fees/rewards for every position).
- **Rewards owed must be collected before close** → add a `collect_reward` step to
  `close_position()` for each initialized reward with amount_owed > 0 (pin its account
  layout from the same source; include reward_mint/reward_vault from the pool's
  reward_infos; token program per reward mint).
- **Wrong/stale accounts in the GUI-built tx** → fix the resolution/caching path so the
  close always uses the record's mint-derived PDA + freshly-fetched accounts.

## Step 4 — Verify (acceptance — all must pass)

1. `python -m py_compile` on every touched file; existing test_*.py still pass.
2. Read-only mainnet simulation (sigVerify=false, no broadcast): after the fix, the close
   ix against the LIVE position F98SmN returns **err: null** (plus collect_reward sims if
   added). Pace requests ≥3s, retry 429s with backoff, rotate publicnode /
   api.mainnet-beta. The wallet = owner of the position-mint NFT
   (getTokenLargestAccounts on the mint → amount-1 holder).
3. Golden-layout unit tests for the fixed builders (accounts/order/flags/discriminators)
   including a 2.x position-bytes fixture asserting the new decoder's field offsets.
4. The GUI error path now surfaces full anchor logs.

## Constraints

- **Do NOT build the EXE** — Kris builds it. **No git push, no releases.**
- Bump version (→ v5.3.14, covering both close fixes) + STATUS.md line; stop there.
- No coldtrack.db / portfolio / key-vault changes. `src/venue_adapters/` + the GUI error
  path + tests only.

## Report back

- The full anchor log from your sim (the "thrown at" line + account index).
- Which constraint 6005 actually is (quote the source) and which theory was right.
- The authoritative 216-byte Position layout you implemented.
- Sim results (err: null) + test output + files changed.