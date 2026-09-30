# Forge Prompt — ColdStack v5.3.29 (final): Aerodrome fee accounting — decisions, data contract, implementation

**From:** Kimi Håkonsen (CFO) · **Date:** 2026-09-30
**Supersedes:** the conflict between `coldstack-staked-aerodrome-accounting.md` (this morning) and
`coldtrack-fee-staking-spec.md` (tonight, written after the K&P write-gap). This file is the single
complete prompt. Where the two earlier docs conflict, this one governs. Where they agree, both apply.

**DB evidence base:** live `coldtrack.db` reviewed for both portfolios —
`kimi/portfolios/the-pack-portfolio/coldtrack.db` (AUD mgmt) and `kimi/portfolios/kitandpaul/coldtrack.db`
(CAD mgmt). Facts cited below are from those files.

---

## Decisions — answers to your two questions

### D1 — `coldstack-staked-aerodrome-accounting.md` governs v5.3.29. No schema changes.

`coldtrack-fee-staking-spec.md` is a **problem analysis**, not an implementation directive for this
release. Its diagnosis is adopted (accrued-vs-claimed distinction, two income streams, the write-gap,
the delta rule — all reflected in the data contract below). Its **schema proposals are deferred** to
ColdTax v2 design (my schema call): `EVENT_TYPE`, `IS_COLLECTED`, `PERIOD_START/END`, `TOKEN_*_SYM`,
the `FEE_EVENT_LEGS` child table, and the `LP_POSITIONS` columns (`IS_STAKED`, `STAKING_CONTRACT`,
`REWARD_TOKEN`, `FEES_ACCRUED_USD`, `LAST_ACCRUAL_DATE`).

- **No new columns, no new tables, no CHECK changes, no unique indexes.** `FEE_EVENTS.SOURCE` stays
  `('MANUAL','READER','HARVEST')`. Live DBs read-only in dev.
- Why (evidence): tonight's K&P failure was a **write-gap, not a schema gap** — claim
  `0xc47c298d…` (12.32 AERO) landed on-chain and wrote nothing (verified: count 0 in both
  `TRANSACTIONS` and `FEE_EVENTS`). A schema change would not have recorded it. The realization
  model already matches the live data: Pack pos #12 has `FEES_EARNED_USD = FEES_CLAIMED_USD =
  103.64` from FEE_EVENT #5 / TRANSACTIONS #88. Accrual display needs no schema — the
  feeGrowthInside math (`accrued0/accrued1`) already runs in the adapter (~L1413–1422).
- Income recognition is **realization-based**: income rows are written when tokens are received
  (AERO claim, `collect()` harvest, staked-close decomposition). Accrual is display-layer only.
  The delta rule from the fee-staking-spec is adopted **inside the computation** (never record a
  cumulative balance as income; fee component at close = accrued-at-close since last harvest), not
  as accrual event rows.

### D2 — NO close writes CAPITAL_EVENTS. All venues, staked and unstaked.

Defect 2's fix is **global**: the close package writes NO CAPITAL_EVENTS row. Remove the
capital-event write from every close path (Aerodrome staked, Aerodrome unstaked, Orca, BSC).

- Acceptance item 3's "existing behavior unchanged" means the **fee/principal decomposition**:
  unstaked closes keep principal + separate `collect()` fees landing on top (G1 precedent;
  Orca keeps its "close: final fees" harvest pattern). It does NOT mean the CAPITAL_EVENTS
  side-effect survives.
- Evidence the scope is global: the Sep-27 rows at issue (Pack #14–17, $3,869.39) came from
  **unstaked** closes — pos #9 Aerodrome ETH/cbBTC and pos #10 Orca cbBTC/SOL. K&P #1–2 are also
  unstaked Orca closes. A staked-only removal would keep reproducing the defect.
- CAPITAL_EVENTS = **external portfolio capital only** (INJECTION − WITHDRAWAL, POSITION_ID NULL).
  True external withdrawals stay hand-recorded via the capital-events path; the close recorder
  never writes them again.
- Existing rows 14–17 (Pack) and 1–2 (K&P): **do not delete**. Exclusion is by query convention.

---

## Data contract (v1 schema, realization model — authoritative)

Every `FEE_EVENTS` row is a **realized income event** (value received). Staked trading fees accrue
in-position (display-layer); they are realized exactly once, at close, via decomposition — that is
what prevents double-counting.

**FEE_EVENTS**
- `SOURCE`: MANUAL (hand adjustment/orphan), READER (reader-detected), HARVEST (tool-recorded
  claim/close/emissions harvest).
- `NOTES` tags are the stream discriminator (exact strings, used by reporting queries):
  - `"gauge emissions"` — AERO emissions claim; AERO amount in `TOKEN_B_AMT` (Pack FEE_EVENT #5
    precedent: TOKEN_B_AMT = 127.356, VALUE_USD at spot, VALUE_AUD/VALUE_CAD via FX_RATES).
  - `"trading fees realized in withdrawal (staked close — included in withdrawn balance)"` —
    staked-close decomposition fee leg.
  - `"close: final fees"` — unstaked close harvest legs (existing convention: Pack #3/#4, K&P #1/#2).

**LP_POSITIONS fee columns**
- `FEES_CLAIMED_USD` = cumulative realized fee income; the tool increments it on every realized
  event.
- `FEES_EARNED_USD` = `FEES_CLAIMED_USD` (keep them equal — realization model). This fixes the
  drift class the fee-staking-spec flagged: K&P pos #5 has `FEES_EARNED_USD = 11.68` (the Aug-31
  claims: WETH $5.11 + cbBTC $3.84 + AERO $2.73) but `FEES_CLAIMED_USD = 0`.
- `FEES_UNCLAIMED_USD` = accrued-in-position estimate (display-layer, reader-refreshed); never
  summed into income.
- Staking state: derived on-chain (gauge holds the NFT / adapter gauge discovery). No column.

**TRANSACTIONS**
- Claims: `yield` rows (AERO emission; trading-fee harvest legs), TX_HASH set.
- Closes: `lp_withdraw` legs for principal; the staked-close fee component is carried by the
  FEE_EVENT only (total withdrawn value unchanged — value-preserving decomposition).
- (Re)entry/compound: `lp_provide` as today. CATEGORY conventions unchanged (`yield`, `lp`).

**Baseline convention (query-layer — document it)**
```
net_external_capital = SUM(VALUE_USD WHERE TYPE='INJECTION')
                     − SUM(VALUE_USD WHERE TYPE='WITHDRAWAL' AND POSITION_ID IS NULL)
```
POSITION_ID-tagged WITHDRAWALs are legacy internal artifacts, excluded from baseline math. The
export keeps full fidelity (all rows, current shape).

---

## Implementation (v5.3.29)

**P1 — Close recorder: remove CAPITAL_EVENTS writes (all paths).**
`src/coldtrack/close_recorder.py`: retire `_insert_capital_event` (wraps the `INSERT INTO
CAPITAL_EVENTS` at ~L466, incl. its v5.3.22 dedupe shim) from every close path. Keep the
`"capital_events"` result key (L174) for UI compatibility, always `0`.

**P2 — Staked close decomposition (`AerodromeWriter._post_close_state`, staked mode).**
`close_staked_position` → `close_position` as today. For a position whose NFT was gauge-held at
close: fees0/fees1 = accrued-at-close from the feeGrowthInside machinery (the reader's
`accrued0/accrued1` math — no new math); principal = withdrawn − fees, per token. Book:
- Fee component → `FEE_EVENT` (SOURCE='HARVEST', NOTES per the tag above, USD at spot + portfolio
  FX) + increment `FEES_CLAIMED_USD`/`FEES_EARNED_USD`.
- Principal → `lp_withdraw` TRANSACTIONS legs; totals unchanged (fees + principal = withdrawn).
- No CAPITAL_EVENTS row (structural after P1).
- If the fee-growth delta reads zero/unavailable at close, fall back to withdrawn-as-principal and
  log — never block a close on the decomposition.

**P3 — AERO emissions claim path + dedupe.**
`AerodromeGaugeWriter._record_claim` / `claim_and_record`: NOTES `"gauge emissions"`. Dedupe by
tx hash **in code** before any insert (no schema-level unique index): check
`SELECT 1 FROM FEE_EVENTS WHERE TX_HASH=?` and the same for the TRANSACTIONS yield row.

**P4 — External-claim detection.**
On refresh/scan: detect AERO transfers gauge → wallet since last known state; offer auto-record
(pending-review pattern), deduped by tx hash. Patch-4's gauge-interface pinning is the
prerequisite (impl bytecode probe; live reference `0x3cfcb159…e0`). Fixtures:
- **Pack:** `0x3cfcb159…` already recorded (TRANSACTIONS #88 + FEE_EVENT #5) → detection must NOT
  offer or re-record.
- **K&P:** `0xc47c298d…` (12.32 AERO — currently missing, verified count 0 in both tables) →
  detection offers exactly once; accepted → writes TRANSACTIONS `yield` AERO (USD at spot, CAD via
  FX_RATES) + FEE_EVENT (HARVEST, `"gauge emissions"`) + increments pos #5
  `FEES_CLAIMED_USD`/`FEES_EARNED_USD`; re-run → no offer.

**P5 — Staked card display.**
Trading-fee growth labeled **"accrued (in position)"**; AERO the only **"claimable"** item
(`fees_note` conventions in `lp_tab.py`). Collect Fees on a staked Aerodrome card = AERO claim
only (existing `_lp_staked_aerodrome_claim` path).

**P6 — Export baseline convention.**
`src/coldtrack/sentinel_export.py`: keep exporting all `capital_events` rows (current shape).
Add the documented INJECTION-only baseline convention (comment/doc block + any net-injection
math lives by it) so POSITION_ID-tagged WITHDRAWALs can never enter external-capital math.
Verify the Sentinel's capital-events flow matches the convention.

**P7 — Version + STATUS.** `VERSION = "5.3.29"` in `gui_main_v5.py`; STATUS.md section.
**Constraints:** no EXE build, no push, no release; sole-writer CLI only; live DBs read-only in
dev; CHECK constraint untouched; pyflakes + `python -m py_compile` on touched files + full
`test_*.py` suite green.

---

## Acceptance (executable)

1. Staked AERO claim → yield + FEE_EVENT rows, USD at spot, tx hash; dedupe: `0x3cfcb159…` never
   double-records (Pack fixture).
2. K&P external claim `0xc47c298d…` detected, offered once, recorded once (TRANSACTIONS + FEE_EVENT
   + `FEES_CLAIMED_USD` increment); second scan offers nothing.
3. Staked close fixture → decomposition is value-preserving (fees + principal = withdrawn, per
   token); fee FEE_EVENT carries the exact NOTES tag; `FEES_CLAIMED_USD`/`FEES_EARNED_USD`
   incremented; no CAPITAL_EVENTS row.
4. Unstaked close fixture → principal + separate `collect()` fees (unchanged legs), no
   CAPITAL_EVENTS row. Orca close fixture → "close: final fees" pattern unchanged, no
   CAPITAL_EVENTS row.
5. Staked card: trading fees "accrued (in position)"; AERO "claimable"; Collect Fees = AERO
   claim only.
6. Baseline fixture (INJECTION rows + position-tagged WITHDRAWALs) → net external capital =
   injections only.
7. Schema untouched: no DDL emitted on any path (assert table definitions identical pre/post).
8. pyflakes, py_compile, full suite green.

## Kimi-side repairs (NOT your scope — ledger items)

- K&P pos #5 `FEES_CLAIMED_USD` 0 → 11.68 (Aug-31 realized-claims drift) — one-time, via the
  sole-writer path after v5.3.29 deploys, with the 21:15 backup in place.
- K&P `0xc47c298d…` backfill happens in production via P4/acceptance 2, not via dev live writes.

## Deferred to ColdTax v2 (my schema call — design inputs, not authorized for coldtrack.db v1)

The fee-staking-spec's schema proposals (EVENT_TYPE / IS_COLLECTED / period columns /
FEE_EVENT_LEGS / LP_POSITIONS staking+accrual columns / computed FEES_EARNED_USD) become design
inputs for the ColdTax schema work (K&P transaction-DB rebuild). They are not implemented in this
release and do not alter the v1 data contract above.

## Report back (brief)

- P1 removal + P2 decomposition wiring (value-preservation test transcript).
- P4 detection design + both dedupe fixtures' transcripts.
- P6 convention doc + export verification.
- Suite + pyflakes/py_compile results. STATUS.md diff.

---

*Kimi Håkonsen · CFO, Executive Mind*