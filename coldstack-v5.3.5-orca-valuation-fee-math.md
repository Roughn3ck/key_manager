# ColdStack v5.3.5 — Orca Valuation + Fee Math

**Base:** commit `5be6226` (v5.3.4, shipped + deployed to portfolio folders 2026-09-10 13:28).
Single-prompt train: this prompt IS the final prompt — includes docs, build, release, backup.

**Author:** Slater (CTO). **Approved by:** Kris, 2026-09-10 ("Could we fix this calculation?").
**Scope:** Orca position USD valuation + pending-fee computation. One venue, mostly one file
(`src/venue_adapters/orca_adapter.py`). Do not expand.

---

## Context — live diagnosis, on-chain ground truth (2026-09-10)

K&P Orca position (SOL/cbBTC) displays wrong in ColdStack:

| Metric | ColdStack (v5.3.4) | Orca UI (truth) |
|---|---|---|
| Position value | **$407.61** | **$1,712.68** |
| Pending fees | **$630.91** | **$8.91** |

Position `F98SmNgmft21dRAwfXGPtWu95Kb1WcSm58WgaQzUZQQR`, mint
`FbNHxe9VV797JWG7XH2msjwp5Rvb6dGzndwwkEXXXBKX`, pool
`CeaZcxBNLpJWtxzt58qQmfMBtJY8pQLvursXTJYGQpbN` (tick_spacing 16, fee_rate 1600).

Facts decoded live from mainnet (verified against Orca's UI to the dollar):
- Position is IN RANGE: tick −89494 within [−91040, −89024].
- Liquidity 1,966,298,455 is a **plain u128, NOT Q64.64** — the plain interpretation
  reproduces Orca's valuation exactly. `_compute_holdings` scale math is CORRECT — do not touch it.
- True holdings: ~3.999 SOL + ~0.01668 cbBTC (≈ $1,712 at SOL $101.68 / cbBTC $78,251,
  Jupiter 2026-09-10).
- ColdStack's $407.61 = the SOL leg only, at a correct SOL price (~$101.93). The cbBTC leg
  (~$1,305) is silently priced at **$0**.
- ColdStack's $630.91 = 6.1897 SOL fees (× ~$101.93) — the SOL fee leg alone, ~70× overstated.

---

## Bug 1 — Unlisted mints silently price at $0 (valuation)

**Chain of failure:** cbBTC mint `cbbtcf3aa214zXHbiAZQwf4122FBYbraNdFqgw4iMij` is not in
`SOLANA_TOKENS` (orca_adapter.py:258) → `_get_sol_token_symbol` falls back to a truncated
mint string (`cbbt…iMij`) → `_usd_value` → price engine can't price it → returns None →
`or 0.0` → the whole cbBTC leg vanishes from value AND fee USD totals.

**Fix 1a — add the mint to `SOLANA_TOKENS`:**
```python
"cbbtcf3aa214zXHbiAZQwf4122FBYbraNdFqgw4iMij": {"symbol": "cbBTC", "decimals": 8},
```
`price_engine.PEGGED_TOKENS` already maps `cbBTC → BTC` (price_engine.py:43-45) — pricing
then flows through BTC with **zero price-engine changes**.

**Fix 1b — general: mint-based price fallback for ANY unlisted mint.**
- When the symbol resolution is the truncated-mint fallback, price the leg by MINT via
  Jupiter lite API: `GET https://lite-api.jup.ag/price/v3?ids={mint}` (public, no key;
  response `{mint: {usdPrice, …}}`). 5s timeout, module-level cache, ≥60s TTL.
- The fallback lives in `orca_adapter.py`. Do NOT rewire `price_engine.py`.
- **Never silently zero an unpriceable leg.** If even Jupiter fails: show the token amount
  on the card and mark the leg "⚠ unpriced". A confident wrong total is worse than an
  honest partial one.

## Bug 2 — Fees charge out-of-range trading (~70× overestimate)

`_compute_fees_owed` uses `L × (fee_growth_global − checkpoint)` — exact only while the
position stays in range. This position spent most of the time since its checkpoint OUT of
range; the approximation billed it for all pool trading anyway.
(checkpoint_a ≈ 2.33e19 vs global_a ≈ 8.14e19 at diagnosis.)

**Fix — compute `feeGrowthInside` via tick arrays.** The HyperEVM adapter already has the
pattern (`_read_tick_fee_growth_outside` + `_compute_fee_growth_inside`, hyperliquid_adapter.py) —
port it to Solana:
- Tick-array PDA: seeds `["tick_array", whirlpool, start_index i32 LE]` under
  `WHIRLPOOL_PROGRAM_ID` (orca_adapter.py:46).
- `TICK_ARRAY_SIZE = 88`; `start_index = (tick // (tick_spacing × 88)) × tick_spacing × 88`.
  Floor division — these ticks are NEGATIVE (−91040); verify Python `//` semantics against the
  whirlpool-math crate before trusting it.
- TickArray account layout (Anchor): discriminator 8 + start_tick_index i32 4 + ticks[88]×113 +
  whirlpool 32 (9,988 bytes). Tick entry: initialized bool 1 + liquidity_net i128 16 +
  liquidity_gross u128 16 + fee_growth_outside_a u128 16 + fee_growth_outside_b u128 16 +
  reward_growths_outside [u128×3] 48 = 113 bytes. Entry i = (tick − start) // tick_spacing;
  offset = 12 + i × 113. **Verify this layout empirically** — the $8.91 gate below is the proof.
- Inside-growth (V3 rules, all mod 2^128):
  - `below = fee_growth_outside(lower)` if current ≥ lower, else `global − outside(lower)`
  - `above = fee_growth_outside(upper)` if current < upper, else `global − outside(upper)`
  - `inside = global − below − above`
  - `delta = (inside − checkpoint) mod 2^128`; `accrued = (L × delta) >> 64`;
    `fees = owed + accrued`
  - Double-check the outside-accumulator direction rules against whirlpool-math.
- If a tick-array account is missing/unreadable: fall back to the current global approximation
  but mark the fee line as an estimate. Never crash the scan.
- Fix centrally in `_compute_fees_owed` (or its replacement) and apply at ALL call sites —
  position fetch (`_fetch_position_at_address`) AND `fetch_fees_earned` (used by live fee
  refresh). Both currently share the bug; both feed `_record_fees` (vault/ColdTrack records),
  so this fix also corrects what gets recorded for collected fees.

---

## Hard verification gate (the ground truth is the point)

- Position `F98SmN…`: computed pending fees ≈ **$8.91** (re-check Orca UI at test time) —
  NOT $630. Report the computed split (SOL leg + cbBTC leg).
- Computed value ≈ holdings × live prices (≈ $1,712 at diagnosis prices; allow market drift,
  cross-check vs fresh Orca UI within ~2%).
- Pair renders as **SOL/cbBTC**; both legs priced.
- An unpriceable mint (rare token) shows the ⚠ unpriced marker with raw amount — never silent $0.
- A known always-in-range position (if one is available in saved pools) still computes the same
  fees as before the change (regression check — in-range math must not drift).

## Out of scope (do NOT touch)
- `_compute_holdings` and the sqrt/liquidity scale math (verified correct on-chain).
- Orca writer TX logic, rebalance flows, ColdTrack schema, `price_engine.py` core, other venues.

## Ship checklist
1. Bump `VERSION = "5.3.5"` in `src/gui_main_v5.py` (single source of truth).
2. `python -m py_compile` all changed files.
3. Sidecar gates unchanged but still run (v5.3.3+ protocol): `node --check` sweep +
   cold-start boot smoke test (no sidecar changes expected in this train).
4. STATUS.md: new v5.3.5 section. README.md only if it documents Orca pricing/fees.
5. Build: `python build_gui_v5.py` → dist/ → USB_DEPLOYMENT (script handles backup).
6. Commit as v5.3.5, push, create the v5.3.5 release (review existing release formatting first;
   new v tag).
7. Backup src + related active files to `backups/v5.3.5/`.
8. Commit this prompt file (house precedent).
9. Deploy: coordinate with Kris — the K&P app may be running (Windows file lock); he closes it,
   then sync the new EXE to `kimi/portfolios/kitandpaul/` (and any other live portfolio folders).

## Verification checklist (report each)
- [ ] SOLANA_TOKENS includes cbBTC; pair shows SOL/cbBTC
- [ ] Jupiter mint-price fallback works for an unlisted mint (test with a real one), cached,
      and shows ⚠ unpriced on total failure — never silent $0
- [ ] F98SmN… fees ≈ $8.91 (report the SOL/cbBTC split) — not $630
- [ ] F98SmN… value ≈ holdings × live prices (~$1,712-class), within ~2% of fresh Orca UI
- [ ] In-range regression: a previously-correct position's fees unchanged
- [ ] Missing tick-array fallback flagged as estimate, no crash
- [ ] VERSION 5.3.5; STATUS.md; py_compile clean; EXE builds; release + backup done;
      deploy reported