# Forge Prompt — ColdStack v5.3.29: Staked Aerodrome accounting (fees realized in withdrawal, AERO claims, no CAPITAL_EVENTS on close)

Kimi's data contract below is AUTHORITATIVE (accounting semantics). Slater's
implementation directives + the [Slater] notes reconcile it with the code and schema.
Fixes the recorder, the staked-card display, and the close package.

---

## Defect 1 — Staked CL positions: two reward streams, different accounting (Kimi)

**1a. Trading fees (token0/token1) — fee income, realized at close, INCLUDED in the
withdrawal, never "claimable" while staked:**
- While staked, trading fees are not separately claimable; they accrue inside the
  position and come out with the withdrawn amounts at decrease/close.
- NOT capital growth — fee income for `FEES_EARNED_USD`/`FEE_EVENTS` (income vs
  capital, AU tax; pool fee-ROI reporting).
- **Accrual phase:** show fee growth as **"accrued (in position)"** (accrued-fee
  field/display), never "claimable" — the "claimable" display is what prompted the
  failed manual trading-fee claim attempt.
- **Realization (unstake/close):** decompose withdrawn amounts into principal + fees.
  Record the fee component as a FEE_EVENT and increment `FEES_CLAIMED_USD` /
  `FEES_EARNED_USD`, flagged **"included in the withdrawn balance"** — never added on
  top (no double count).
- **Unstaked positions unchanged** (G1 precedent): `collect()` fees land as separate
  amounts on top of principal — keep that path as-is.

[Slater] Implementation:
- The decomposition uses the adapter's existing feeGrowthInside machinery (the
  `[fees-delta]` accrued0/accrued1 math already runs in the fees reader) — fees =
  accrued at close; principal = withdrawn − fees. No new math.
- **FEE_EVENT SOURCE:** Kimi suggested `AUTO_COMPOUND`, but FEE_EVENTS' CHECK
  constraint allows only ('MANUAL','READER','HARVEST') and SQLite cannot extend a
  CHECK without a table rebuild (schema change, out of scope). Use
  **`SOURCE='HARVEST'` + NOTES "trading fees realized in withdrawal (staked close —
  included in withdrawn balance)"**. If she later wants a dedicated source value,
  that's her schema call.
- Staked-card display: the Fees area shows trading-fee growth as "accrued (in
  position)" and AERO as the only "claimable" item; **Collect Fees on a staked card
  = AERO emissions claim ONLY** (never a trading-fee collect attempt). This aligns
  the v5.3.28 guided flow: claim(AERO) → unstake → close.

**1b. AERO emissions — separately claimable, added on top (Kimi):**
- Detect the gauge claim / AERO transfer to wallet; record: TRANSACTIONS `yield`
  AERO row (USD at spot, tx hash) + FEE_EVENTS `SOURCE='HARVEST'` (note: gauge
  emissions) + increment `FEES_CLAIMED_USD`/`FEES_EARNED_USD`. Emissions are never
  part of the position balance — no decomposition.

[Slater] Implementation:
- **Dependency: patch 4's gauge-interface pinning is a prerequisite** — the deployed
  gauge (clone → impl `0x434bccab…`) does not carry the writer's pinned
  claimEmissions/withdraw selectors; pin the real claim function via the impl
  bytecode probe + historical-claim decode before wiring detection. Kris already
  claimed successfully today via the Aerodrome UI (tx `0x3cfcb159…e0`, 127.356 AERO —
  already hand-recorded as TRANSACTIONS #88 + FEE_EVENT #5), so the historical decode
  has a live reference.
- ColdStack-initiated claims record on success. External claims (like today's) are
  detected on refresh — AERO transfer from the gauge address to the wallet since
  the last known state — and offered as an auto-record (pending-review pattern),
  **deduped by tx hash** (today's 0x3cfcb159… must never double-record).

## Defect 2 — CAPITAL_EVENTS on close: internal ≠ external (Kimi)

- The Sep-27 closes (G2 pos 9, G3 pos 10) wrote CAPITAL_EVENTS WITHDRAWAL rows 14–17
  ($3,869.39). Wrong: internal moves (LP → own wallet), already captured by
  `lp_withdraw` transactions. CAPITAL_EVENTS is **external portfolio capital only**
  (INJECTION − WITHDRAWAL baseline).
- **Fix: the close package writes NO CAPITAL_EVENTS row. Position-level liquidity
  tracking, if ever wanted, uses a distinct internal scope that can never enter
  external-capital math.**
- Existing rows 14–17: **do not delete** (tool-written; deletion invites drift) —
  exclusion is by query convention: baseline = INJECTION rows only; WITHDRAWAL rows
  with POSITION_ID set are excluded from baseline math.

[Slater] This **reverses the v5.3.22 prompt's** CAPITAL_EVENTS directive — that
inclusion was Slater's over-extension; Kimi's external-only model governs. The
recorder + export queries adopt the INJECTION-only baseline convention; confirm the
sentinel export's capital-events flow excludes position-tagged WITHDRAWALs from the
net-injection math and matches her convention.

## Acceptance (Kimi's, plus executable/dedupe additions)

1. Claim AERO on a staked position → yield/AERO txn + FEE_EVENT (HARVEST, "gauge
   emissions") recorded automatically, USD at spot, tx hash. **Executable tests run
   the real recording path with fixtures (the BSC round-1 lesson); dedupe test: the
   already-recorded tx 0x3cfcb159… never double-records.**
2. Close a staked position → withdrawn amounts decomposed; fee portion recorded as
   income *included in* the withdrawal; total withdrawn value unchanged; no
   CAPITAL_EVENTS row.
3. Close an unstaked position → existing behavior (principal + separate collect()
   fees) unchanged.
4. Staked "claimable" = AERO only; trading-fee growth shows "accrued (in position)".
5. External-capital baseline (INJECTION-only) unaffected by any open/close.
6. CHECK constraint untouched; pyflakes + full suite green; `python -m py_compile`.

**Constraints:** no EXE build, no push, no release. Sole-writer CLI only (live DBs
read-only in dev; Kimi's schema untouched). Version v5.3.29 + STATUS.md.

## Report back (brief)

- The realized-fee decomposition wiring + the staked-card display states.
- The pinned claim interface (patch 4 dependency) + external-claim detection design.
- The CAPITAL_EVENTS removal + export-baseline convention. Test transcript.