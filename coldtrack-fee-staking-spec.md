# ColdTrack — Fee & Staked-Position Recording Spec (for Slater)

**From:** Kimi (CFO) · **Date:** 2026-09-30 · **Status:** spec for implementation
**Context:** K&P Aerodrome ETH/cbBTC (Base) monthly fee claims repeatedly fail to
record correctly in `coldtrack.db`. This spec resolves the whole class of cases at once.

---

## 1. The problem class

Every month we claim, and the DB records it wrong (or not at all). Root causes:

1. **Accrued vs claimed is not distinguished.** `FEE_EVENTS` can't tell "fees sitting in
   the position, not yet harvested" from "fees moved to wallet".
2. **Two income streams are conflated.** Aerodrome pays (a) **trading fees** in
   WETH/cbBTC (accrued in-pool, claimable) and (b) **AERO emissions** (a reward, claimed
   to wallet). These are different economic events and different tax treatment.
3. **Staked positions aren't represented.** A staked Aerodrome position (`getReward`
   claim) vs an unstaked position vs a close are three different flows; the schema
   models only one.
4. **Stake tx write-gap.** Claims land on-chain but nothing writes the row
   (confirmed 2026-09-30: emissions claim `0xc47c298d…` on Base, not in DB).

---

## 2. The three event flows to support

### A. Accrual (fees earned, NOT collected)
Trading fees accumulate in the pool (Aerodrome `fee0`/`fee1`). They are **income accruing
now**, recognised this period, with **no tx hash** (nothing moved). Balance remains in
the pool. These must subtract from capital appreciation in unrealised P&L.

### B. Harvest — trading fees (collected, WETH/cbBTC)
User calls `collectFees`. Tokens move pool → wallet. Has a tx hash. Converts accrual to
realised income. **Deducts** from the pool's fee balance.

### C. Harvest — emissions/staked (collected, AERO)
User calls `getReward` on a **staked** position. AERO (or other reward token) moves
emission-contract → wallet. Has a tx hash. This is the "10% of trade fees paid to LP
providers who stake" bucket. **Independent of trading fees.**

### D. Close (deployed capital returned)
Liquidity withdrawn to wallet; position closed. Capital event, not income.

---

## 3. Schema changes required

### 3.1 `LP_POSITIONS` — add staking + accrual tracking

New columns:
| Column | Type | Purpose |
|---|---|---|
| `IS_STAKED` | INTEGER (0/1) | Is the position staked in a gauge? Drives which claim flow applies. |
| `STAKING_CONTRACT` | TEXT | Gauge/reward contract address (e.g. `0x41b2126661c673c2bedd208cc72e85dc51a5320a`). |
| `REWARD_TOKEN` | TEXT | Emission token symbol (e.g. `AERO`). |
| `FEES_ACCRUED_USD` | REAL | **Uncollected** trading-fee income to date (this period, not yet harvested). |
| `LAST_ACCRUAL_DATE` | TEXT | Date accrual was last measured. |

Keep existing `FEES_UNCLAIMED_USD` / `FEES_CLAIMED_USD`, but define them precisely:
- `FEES_ACCRUED_USD` = income recognised, **not** in wallet (still in pool)
- `FEES_CLAIMED_USD` = cumulative trading fees **harvested to wallet**
- `FEES_EARNED_USD` = **computed** = accrued + claimed (never hand-set — fixes the
  double-count bug from Sep where 57.50 was written over a 19.66 delta)

### 3.2 `FEE_EVENTS` — add the fields that distinguish the flows

New columns:
| Column | Type | Purpose |
|---|---|---|
| `EVENT_TYPE` | TEXT CHECK | `ACCRUAL` \| `TRADING_FEE_HARVEST` \| `EMISSION_HARVEST` |
| `IS_COLLECTED` | INTEGER (0/1) | 0 = still in pool (accrual), 1 = in wallet |
| `PERIOD_START` | TEXT | For accruals: prior measurement date (delta basis) |
| `PERIOD_END` | TEXT | Current measurement date |
| `TOKEN_A_AMT`/`TOKEN_B_AMT` | REAL | Existing — amounts of each token |
| `TOKEN_A_SYM`/`TOKEN_B_SYM` | TEXT | **NEW** — symbols so a 3rd asset (AERO) fits |
| `AMOUNT_A`/`AMOUNT_B`/`AMOUNT_C` | REAL | Generic multi-asset legs (AERO needs a 3rd slot) |
| `VALUE_USD`/`VALUE_CAD` | REAL | Existing |

Rationale: Aerodrome claims return **3 assets** (WETH, cbBTC, AERO). Two-token columns
don't fit; add a third leg or normalise into a child table `FEE_EVENT_LEGS`.

**Recommended (cleaner):** child table
```
FEE_EVENT_LEGS(
  ID, FEE_EVENT_ID FK, ASSET TEXT, AMOUNT REAL,
  VALUE_USD REAL, VALUE_CAD REAL, PRICE_USD REAL
)
```
so any N-asset claim is representable without schema churn.

### 3.3 Accrual mechanics (the delta rule)

Accrual each period = **current fee balance − prior fee balance**:
```
accrual_WETH = fee0_now − fee0_prev
accrual_cbBTC = fee1_now − fee1_prev
```
Read live from the pool contract. Store as an `ACCRUAL` FEE_EVENT (IS_COLLECTED=0),
dated at period end. On harvest, create a `TRADING_FEE_HARVEST` event and zero the
accrual. **Never record the cumulative balance as income** — always the delta.

---

## 4. `TRANSACTIONS` — how each flow is written

| Flow | TYPE | ASSET | Notes |
|---|---|---|---|
| Accrual | `yield_accrual` | WETH, cbBTC (one row each) | `TX_HASH` NULL; `NOTES` "accrued, uncollected" |
| Trading harvest | `yield` | WETH, cbBTC | `TX_HASH` = collectFees tx |
| Emission harvest | `yield` | AERO | `TX_HASH` = getReward tx; `NOTES` "emissions, staked" |
| Capital return | `lp_withdraw` | each token | close tx |

Add a `CATEGORY` convention: `yield_trading` / `yield_emission` / `lp`.

---

## 5. Atomicity + write triggers (the recurring failure)

- **Write on confirmed on-chain state**, not on the button's return code. After a claim,
  poll the position's fee/reward balance; when it changes, write the event.
- **No success message without a committed row.** If the DB write fails, surface an error.
- **Support staked claim detection:** decode method `0x1c4b774b` (`getReward`) → emission
  harvest; `collectFees` selectors → trading harvest; `decreaseLiquidity`+`collect` → close.

---

## 6. The K&P Aerodrome ETH/cbBTC worked example (Sept 2026)

Position #5, staked, deposit ID 75269474, gauge `0x41b21266…`.

**August baseline (recorded):** AERO 5.85534, WETH 0.00213, cbBTC 0.00005 (all harvested).

**September (this claim, first since Aug 31):**
- **Emission harvest (COLLECTED):** 12.32 AERO — tx `0xc47c298dbf88326ce6ca6d2a88e3a80f06340a1865929af43c9b66ec7b013334`
  → `FEE_EVENTS(EVENT_TYPE=EMISSION_HARVEST, IS_COLLECTED=1)` + `TRANSACTIONS(yield, AERO)`.
- **Trading fees (ACCRUAL, uncollected):** WETH 0.00566, cbBTC 0.00018
  → total accrued-to-date. **Subtract the August harvested amount** to get the new
  income: WETH delta = 0.00566 → (this is the cumulative pool fee balance; prior was
  0.00213 harvested) → **new trading-fee income ≈ 0.00353 WETH + 0.00013 cbBTC**.
  → `FEE_EVENTS(EVENT_TYPE=ACCRUAL, IS_COLLECTED=0)` + `TRANSACTIONS(yield_accrual)`.

**Position value (deployed principal, excl. fees):** 0.27602 WETH + 0.00996 cbBTC.
Fees are NOT included in this figure (confirmed by IL signature: WETH down, cbBTC up —
price drift, not compounding).

**Wallet base holdings (outside pool):** 0.02622999 WETH, 18.1772798 AERO,
0.00079981 ETH, 0 cbBTC.

---

## 7. Unrealised P&L rule (Kris's requirement)

```
Accrued income (this period) = current trading fees − previous trading fees
                             = recognised income, NOT capital appreciation
Unrealised trading P&L       = position value change − accrued income
Cumulative portfolio value   = deployed capital + cumulative realised fees (trading + emission)
```

i.e. **fee income must be stripped out of capital appreciation.** Working on the basis
that staked principal excludes fees.

---

## 8. Acceptance test

On next monthly claim:
1. AERO emission harvest → 1 `FEE_EVENTS` row + 1 `TRANSACTIONS(yield, AERO)` row, tx hash set.
2. WETH/cbBTC accrual → `FEE_EVENTS(EVENT_TYPE=ACCRUAL)` with correct deltas, `IS_COLLECTED=0`.
3. `LP_POSITIONS.FEES_ACCRUED_USD` updated; `FEES_EARNED_USD` = accrued + claimed (computed).
4. No duplicate / no cumulative-total-written-as-delta.
5. Staked flag + gauge recorded on the position.

---

*Kimi Håkonsen · CFO, Executive Mind*
