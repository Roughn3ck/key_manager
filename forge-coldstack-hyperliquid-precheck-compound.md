# Forge Prompt — ColdStack v5.3.29 companion: HyperEVM (Project X) ownership pre-check + compound-fee accounting

Kris clicked Compound Fees on the K&P Project X position (WHYPE/UBTC, token **#545983**,
KP coldtrack.db LP_POSITIONS row 6, wallet `0x8958Bd96896De55bFe31b1A6Eb2B280ebE098509`)
and correctly got the honest gate: "ownership pre-check not yet implemented for venue
'hyperliquid'". The compound flow itself exists (the click reached the gate); the
venue pre-check + the compound accounting are what's missing. Same v5.3.29 batch as
the staked-Aerodrome accounting prompt.

## Fix 1 — HyperEVM ownership pre-check (the last venue)

Mirror the Aerodrome/BSC pattern exactly: `ownerOf(tokenId)` on the Project X position
manager → match against vault accounts (binding first, owner-anchored fallback —
HyperEVM derives the same EVM address) → resolved signer proceeds; mismatch → the
precise wordings. The agent's chain guard applies with the writer's HyperEVM RPC +
chain id (the broadcast path is historically proven — the Sep-10 PX close ran through
ColdStack). Adapter-exposed ownerOf (no hand-encoded calldata in lp_tab — the BSC
round-1 lesson) + the pyflakes lint holds + the pre-check tests EXECUTE the real
function with stubs.

## Fix 2 — Compound-fee accounting (follows Kimi's established model)

A compound = collect fees → re-add them as liquidity. Recording, via the sole-writer
CLI (live DBs read-only in dev; her schema untouched):

1. **FEE_EVENTS — income at realization:** one row, `SOURCE='HARVEST'`, NOTES
   "auto-compound — fees reinvested", TOKEN_A_AMT/TOKEN_B_AMT as collected,
   VALUE_USD at spot, TX_HASH = the compound tx. Increment `FEES_CLAIMED_USD` /
   `FEES_EARNED_USD` on the position row.
2. **Entry-basis update — internal reinvestment:** AMOUNT_A_ENTRY / AMOUNT_B_ENTRY /
   TOTAL_VALUE_USD_ENTRY += the reinvested amounts (the position's basis grows by
   the compounded fees — so capital-growth math never double-counts the fee income).
3. **NO CAPITAL_EVENTS row** — internal reinvestment, not external capital
   (Defect 2's external-only rule from Kimi's brief).
4. **LP_SNAPSHOTS:** a post-compound snapshot (new amounts, prices, IN_RANGE state,
   NOTES with the tx hash + "auto-compound").
5. Dedupe by TX_HASH (a re-run never double-records). If the CLI needs a `compound`
   subcommand, add it alongside close/fees with the same validation gates.

**Flag in your report for Kimi's confirmation:** the entry-basis increment is the
accounting mechanism for internal reinvestment (income counted once at FEE_EVENTS;
basis grows so capital growth stays clean). Her model implies it; she blesses the
exact field treatment.

## Acceptance

1. Executable pre-check tests (stubs): PX owner-match proceeds; mismatch wordings;
   no hand-encoded calldata in lp_tab (pyflakes clean).
2. Compound accounting fixtures on a db COPY: FEE_EVENT + entry increments + snapshot
   + NO capital event + dedupe; the real recording path executed (not just messages).
3. `python -m py_compile`; full suite green. **No EXE build, no push.**
   STATUS.md v5.3.29: append.

## Report back (brief)

- The HyperEVM pre-check wiring + which manager address the ownerOf reads.
- The compound accounting implementation + the CLI subcommand shape.
- Test transcript.