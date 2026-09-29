# Forge Prompt — ColdStack v5.3.27 #1e: The fetch flows use the WRONG pipeline (single-address, placeholder-first)

Round 4 status: Kris's repro is UNCHANGED after #1d. The canonical-key work is fine —
but the flows his repro fires were never touched by it. Slater traced the actual call
chain; here is the complete mechanism.

## The repro path (Kris: Fetch Position, Platform Aerodrome, account G2, blank input)

Dispatches to the scan flows at:
- `_lp_do_fetch` (lp_tab.py:1018) — generic fetch
- `_lp_do_filtered_scan(address, venue_key)` (lp_tab.py:1519) — platform-filtered fetch

Both do THE SAME THING (verbatim from 1068–1074 and 1540–1546):

```python
# Render placeholders immediately, then fast-fetch saved pools,
# then schedule the full scan in the background.
self._lp_render_saved_placeholders(address)      # ← THE VANISH
self._lp_fetch_saved_only(address)               # ← THE STUCK-FAILED
self.gui.root.after(2000, lambda: self._lp_do_full_scan(address))
```
(Same pattern in `_lp_preload_saved_only`, line 613.)

## Why every round of fixes showed nothing

1. **The vanish:** `_lp_render_saved_placeholders(address)` renders EVERY saved pool
   as a placeholder card immediately (replacing rendered data cards). That's the
   disappear-on-search — it was never the "destroy loop" we removed in the other
   functions; it's this bulk renderer.
2. **The permanent "Fetch failed":** the placeholders render in the failure state
   (`state="auto"`, `error=None` → "Fetch failed — live data unavailable. Click Scan
   Wallet to retry." — the label at line 1775). They are supposed to be REPLACED by
   `_lp_fetch_saved_only(address)` — but that function fetches saved pools **filtered
   to the single selected address** (`load_saved_pools(..., wallet_address=address)`).
   While scanning as **G2**, the pools bound to G1/G4/G6 wallets are OUT OF SCOPE —
   their placeholders can never be replaced → they stay "Fetch failed" forever.
3. **The multi-account machinery we built across #1–#1d** (`_lp_fetch_all_saved_entries`
   + `_lp_update_saved_cards_in_place`, per-pool bindings, canonical keys, the
   `[rescan] … bound=G4/G6/G1 → OK` lines in the log) lives on a DIFFERENT pipeline —
   the tab-open/auto-fetch path. The fetch flows never call it. We fixed the pipeline
   the repro doesn't use.

**Root cause: the fetch flows are single-address, placeholder-first; the app is
multi-account, data-first.** The pack portfolio's saved pools span G1–G6 wallets — any
flow scoped to one address strands every other account's cards.

## Fixes

1. **Route ALL fetch flows through the multi-account pipeline.** `_lp_do_fetch`,
   `_lp_do_filtered_scan`, `_lp_preload_saved_only`, `_lp_fetch_saved_only`, and the
   post-fetch refresh in `_lp_do_fetch_single` must operate on **ALL saved entries
   with their own bindings** (`_lp_fetch_all_saved_entries`), update cards via
   `_lp_update_saved_cards_in_place` (canonical keys), and NEVER filter saved pools
   by the selected address. The selected account/platform scopes only the NEW fetch
   (which position/pool to look for), never the refresh of existing pools.
2. **Delete the placeholder-first pattern.** No bulk
   `_lp_render_saved_placeholders(address)` over already-rendered data cards.
   Placeholders exist ONLY for pools that have never been fetched this session;
   existing cards persist and update in place.
3. **Honest placeholder states:** a placeholder's default state for an unfetched pool
   is "⏳ Fetching positions…" (neutral) — the failure text (with the REAL error)
   appears only when that pool's own fetch genuinely failed. The generic
   error=None default text at line 1775 must be unreachable in normal flows.
4. **Mandatory render trace (end the whack-a-mole):** one log line per card render —
   `[card-render] fn=<function> key=<canonical key> state=<data|fetching|failed|closed>
   error=<text|->` — so the next GUI run proves, from the log alone, which pipeline
   rendered every card. Keep it permanently; it's cheap.

## Acceptance

1. Kris's live repro, from the log: `[card-render]` lines show every saved pool
   (G1/G4/G6) rendered via `state=data` (or a genuine per-pool error) — no
   `state=failed error=-` defaults; no vanish (cards persist through the search).
2. Unit tests: fetch-single with an account whose wallet differs from all saved
   pools → all saved pools still refresh via their own bindings (no address filter);
   placeholder-default-state rule; the render-trace line format.
3. `python -m py_compile`; full suite green. **No push, no EXE build.**
   STATUS.md v5.3.27: append this round.

## Report back (brief)

- Which call sites were rerouted; confirmation the bulk placeholder render is gone
  from fetch flows.
- The `[card-render]` trace from your local run of the G2 repro.