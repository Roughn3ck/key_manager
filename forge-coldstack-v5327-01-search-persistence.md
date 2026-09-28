# Forge Prompt — ColdStack v5.3.27 #1: version bump + saved pools persist during search

v5.3.27 = **tidy-up & hardening release only** — minor bug fixes across previous items,
NO new functionality. Iterative: **no git push, no EXE build** (Kris compiles) until
the batch finalizes. This is prompt #1 of the series.

## 1. Version bump + STATUS (do first)

- `src/gui_main_v5.py`: VERSION = "5.3.27" — including the **lockscreen** version
  string (the same place updated for 5.3.26).
- STATUS.md: current version line → v5.3.27 (work in progress — minor bug fixes &
  hardening); add section `## v5.3.27 — Minor bug fixes & hardening (in progress)`
  to accumulate the batch's fixes.
- Note: `forge-coldstack-save-in-place.md` (save stops rescanning, persists the card
  instantly) rides in this same batch — same panel-persistence theme as below.

## 2. Bug: saved pools clear during a search, then return as "Fetch failed"

Kris's repro: select a NEW account (G2) + Platform Aerodrome → Fetch Position. During
the search the saved-pool cards VANISH from the display; when the G2 position is
found, the saved pools come back — but now every card reads
"Fetch failed — live data unavailable. Click Scan Wallet to retry."

Required behavior — **saved positions persist during the search, end to end:**

1. **Never clear the saved-pool cards while a fetch is in flight.** The card list
   renders from the saved-pool records with their last-known content; the search's
   progress states ("Fetching positions…" etc.) apply to the SEARCH result area, not
   to the existing cards. Trace where the fetch flow rebuilds/empties the card panel
   and cut it.
2. **The post-fetch rescan must resolve each pool via its OWN bound account + venue**
   (v5.3.18 bindings, v5.3.19 rescan machinery) — NOT the top-bar selector. The G2
   repro suggests the rescan is currently resolving from the selected account, so
   pools bound to G1/N1/etc. refetch with the wrong wallet and come back "failed."
   Verify with the G2 repro and fix the resolution path.
3. **Honest failure semantics:** a card shows "Fetch failed" ONLY when that pool's own
   refetch genuinely failed (log the real per-pool error to gui_debug.log). If the
   rescan can't complete (transient RPC, timeout), the card KEEPS its existing
   content with a quiet "refresh pending" state — never a failure mark over known
   data.
4. No functionality changes beyond the above; keep diffs surgical.

## Acceptance

1. Repro test (stub adapters): fetch a new account's position → saved cards never
   empty; each card refetched via its own binding (assert per-pool account+venue
   resolution ≠ the selector); one genuinely-failing pool marks only itself with the
   real error; rescan-timeout case keeps content + "refresh pending."
2. Live read-only: Kris's G2 + Aerodrome repro — saved pools stay visible throughout,
   all cards render clean after.
3. `python -m py_compile`; full suite green. **No push, no EXE build, no release.**
4. STATUS.md v5.3.27 section started with this fix's line.

## Report back (brief)

- Where the fetch flow cleared the card panel (exact line) + the fix.
- The rescan's account-resolution path you found (selector vs binding) + the fix.
- Test transcript.