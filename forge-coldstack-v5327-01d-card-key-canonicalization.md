# Forge Prompt — ColdStack v5.3.27 #1d: Card-key canonicalization — the Fetch-failed root cause (found, with line evidence)

Kris retested (v5.3.27b+c in his exe): saved pools STILL vanish during fetch and ALL
return as "Fetch failed — live data unavailable" (repro: fetch the G2 Aerodrome staked
position; pack pools G1/G4/G6 all show failures). Today's gui_debug.log shows every
pool fetching **OK** with correct bindings (`[rescan] BSC … bound=G4 → OK`,
`Aerodrome … bound=G6 → OK`, `Aerodrome … bound=G1 → OK`) — **the fetches succeed; the
render layer loses them.** Slater traced the render code and found the root cause:

## The bug: THREE card-key formats that can never match

1. `_lp_update_saved_cards_in_place` keys fetched positions as
   **`f"{pos.venue}:{pos.position_id}"`** (2 segments, e.g. `Aerodrome:76866113`) —
   used for `seen`, dedupe, and the card registry.
2. The SAME function matches saved-pool entries via
   **`f"{venue}:{prefix}:{tid}"`** (3 segments, e.g. `Aerodrome:aerodrome:76866113`,
   with `prefix = self._lp_venue_prefix(venue)`) — checked against `seen`, which only
   contains 2-segment keys. **This can never be True**, so every saved pool falls to
   the error/pending branches even when its fetch succeeded.
3. `_lp_render_fetching_state` renders fetched cards (2-segment keys into
   `rendered_ids`), then checks saved pools with **`f"{prefix}:{tid}"`** (yet another
   format) — also never matching → every saved pool renders as a placeholder. And it
   **destroys all existing card widgets first** (`widget.destroy()` loop) — THAT is
   the vanish.

Net effect: every fetch destroys the cards, re-renders the fetched position, and
paints every saved pool as the generic failure placeholder — regardless of fetch
success. Tab-open renders fine because that path doesn't use this matching.

## Fixes

1. **ONE canonical card key everywhere.** Define a single helper, e.g.
   `_lp_card_key(venue, position_id)` → normalized `venue.lower() + ":" +
   normalized_id` (numeric ids stringified, Solana mints/Sui object ids as-is, case
   normalized) and use it at EVERY site: the card registry (`position_cards`),
   `rendered_ids`, `seen`, the `errors` dict (produced in
   `_lp_fetch_all_saved_entries` with the SAME key), and saved-pool matching in
   both `_lp_render_fetching_state` and `_lp_update_saved_cards_in_place`.
   Sweep the whole LP tab for any other ad-hoc `f"{...}:{...}"` card keys and
   convert them.
2. **`_lp_render_fetching_state` must NOT destroy existing cards.** Cards stay
   rendered; the progress state goes in the status line only. The vanish dies here.
3. **With canonical keys, enforce the state matrix:** saved pool fetched OK → its
   card updates with data (never a placeholder); genuine per-pool error → the card
   shows the REAL error text; not reached → existing card + quiet "refresh pending".
   No path may render the generic "Fetch failed — live data unavailable" for a
   pool whose fetch succeeded.

## Acceptance

1. Unit test (the regression that catches this class): construct the key via each
   historical format for one saved pool and assert all resolve to the one canonical
   form; a saved pool present in fetched positions MUST NOT take the placeholder
   branch; the errors dict keys match the canonical form.
2. Kris's live repro (read-only): fetch the G2 Aerodrome staked position → saved
   G1/G4/G6 cards never vanish and never show placeholders; all four render with
   data (log: their `[rescan] … OK` lines correspond to rendered data cards).
3. `python -m py_compile`; full suite green. **No push, no EXE build.**
   STATUS.md v5.3.27 section: append this round.

## Report back (brief)

- The canonical-key helper + the count of ad-hoc key sites converted.
- Confirmation the destroy-loop is gone from the fetching state.
- The live repro transcript (cards persist, no placeholders).