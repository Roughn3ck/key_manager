# Forge Prompt — ColdStack v5.3.18: Saved-pool account binding + finish Aerodrome close loop

The G1 Aerodrome close now WORKS end-to-end (verified on-chain 2026-09-23: position
#75255240 liquidity = 0, tokensOwed = 0/0, EURC + cbBTC in the Pack wallet). Three
items remain from Kris's report + Slater's verification.

## Fix A — Saved pools carry their account binding (Kris's ask, the headline)

The close used the top-bar Account selector (G2 was selected → pre-flight mismatch:
owner 0xAe8E… vs G2's derived 0x40c3…). A saved pool must carry its own wallet:

1. Persist the owning vault account (name + derived address + chain) into the
   saved-pool record at fetch/save time (the fetch flow knows which account/wallet it
   scanned — record it).
2. Position actions (close, collect fees, rebalance, refresh) resolve the account from
   the RECORD's binding — the top-bar Account selector governs only NEW fetches/opens.
3. Legacy records without a binding: resolve via the existing v5.3.17 owner-anchored
   path (on-chain ownerOf on both Slipstream NFPMs → vault account whose derived
   address matches) and PERSIST the resolved account back into the record (self-heal).
4. Pre-flight error wording must distinguish causes, never "stale record" for a mere
   selector mismatch: (a) "position owner 0x… matches no vault account on this chain —
   refetch", (b) "record is bound to account X (0x…) but owner is Y — refetch".
   (The guard itself stays FATAL and is doing its job — a wrong-account close would
   revert on-chain and waste gas.)

## Fix B — Finish the Aerodrome ledger recording (stubbed at lp_tab:3418)

The writer already captures the CloseResult (v5.3.16 bookkeeping: decrease_sig /
collect_sig + per-tx deltas). The lp_tab recording step still prints "ledger recording
not implemented for venue". Wire it per forge-coldstack-close-recorder.md:

- Capture: per-tx wallet token-balance deltas → EURC/cbBTC legs + fee legs with the
  sig of the tx that MOVED them (decrease sig for withdrawals, collect sig for fees);
  ETH gas per tx from receipts; blockTime; prices at close via PriceEngine (USD only —
  no guessed CAD/EUR/AUD, Kimi's FX pipeline backfills).
- Write (one sqlite transaction, existing db.py functions): TRANSACTIONS lp_withdraw +
  yield rows (CHAIN='Base', gas in FEE_ASSET/FEE_AMOUNT/FEE_USD); LP_POSITIONS
  STATUS='closed' + CLOSED_DATE — match pack db row 7 by platform+pool+`status='active'`
  (TOKEN_ID secondary — row 7 carries the STALE pre-rebalance id 74933503; correct the
  identifier to the live 75255240 as part of the close write, noted in NOTES);
  LP_SNAPSHOTS close row (amounts, prices, TOTAL_VALUE_USD, IN_RANGE=0, sigs in NOTES);
  FEE_EVENTS (SOURCE='HARVEST'). If the row is ALREADY closed (e.g. Kimi recorded it
  manually), append the sigs to NOTES only — never duplicate close data.
- Pending-record fallback on db failure (close still reports success), then the
  existing ColdTrack auto-export → strategy_view.json → sentinel hot-reload.

## Fix C — Burn the empty NFT (definitive close, matching Orca semantics)

ColdStack's Aerodrome close is decrease+collect only — the empty NFT husk remains
(ownerOf still returns the wallet; ghost positions). After decrease+collect, when a
read-back proves liquidity == 0 AND tokensOwed == 0, call the Slipstream NFPM
`burn(tokenId)` (selector check first, both managers). Burn sig goes to NOTES only —
never a withdraw hash. If the burn tx fails: funds-out is still reported as successful
plus a clear "NFT burn failed — retry" state. This also lets Kris rerun close on the
existing #75255240 husk to burn it after the build (decrease/collect skip on empty,
recorder sees the already-closed row → NOTES-only append per Fix B).

## Fix D — Post-close saved-pool cleanup

After close confirmation, remove/mark the saved-pool card (mirror the Orca behavior:
"card removal is gated on the position PDA gone" — for Aerodrome, gate on the burn
confirming, or on liquidity==0 read-back if burn unavailable).

## Acceptance (NO on-chain actions during dev)

1. `python -m py_compile`; all existing tests green.
2. Unit tests: close uses the record's binding regardless of the top-bar selector;
   legacy record self-heals via owner-anchored resolution and persists the binding;
   both pre-flight error wordings asserted; recorder end-to-end on a db COPY with a
   synthetic Aerodrome CloseResult (four tables column-exact, idempotent, TOKEN_ID
   correction path, already-closed → NOTES-only); burn leg ordering (only after
   empty-read-back); pending fallback path.
3. Widget smoke for the new error/success states. Version bump v5.3.18 + STATUS.md.

## Constraints

- **No EXE build** (Kris builds), **no git push, no releases.** Live DBs read-only
  during dev; no schema changes; no vault changes; no on-chain txs in tests.
- Ground truth (verified 2026-09-23): #75255240 on NFPM v1 0x827922686190790b37229fd06084350E74485b72,
  owner 0xAe8E5FDb8857736C2218532Fd9D68430aAbAC6ae, liquidity 0, owed 0/0. Row 7:
  pack coldtrack.db LP_POSITIONS (EURC/cbBTC, opened 2026-08-18, entry $1,978.84,
  stale TOKEN_ID 74933503 — dead on both managers). Live pool token1 cbBTC =
  0xcbb7c0000aB88B473b1f5afd9ef808440eed33bf (verify the CBBTC_BASE registry constant
  against it and fix if mismatched — leg symbols must come from live token addresses).

## Report back

- Binding design + self-heal path; recorder wiring + per-table mapping; burn leg
  selector + flow order; test transcript; files changed.