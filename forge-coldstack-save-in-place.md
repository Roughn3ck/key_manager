# Forge Prompt — ColdStack v5.3.27: Save shouldn't rescan — persist in place

The staked-position work is confirmed good (Fetch + Scan Wallet both find it, Staked
badge shows — nice). One UX polish from Kris: **clicking Save on a fetched position
triggers a full wallet rescan** — the panel flashes and reloads everything. It
shouldn't: the data is already in memory on the card.

## Fix (small)

1. Trace the Save handler: it currently routes through (or alongside) the v5.3.19
   fetch-refresh path that rescans every saved pool. Split them:
   - **Save** = persist the saved-pool record (with its v5.3.18 account binding) +
     render/keep the card from the in-memory LPPosition. **Zero network calls.**
     New save → the card appears with its fetched content immediately.
   - **Fetch** keeps the existing refresh behavior.
2. The card state after save: no "Fetching…" flash, content unchanged, a subtle
   "Saved ✓" confirmation. If a card was previously rendered, it stays as-is.
3. Keep deliberate refresh paths available (the existing rescan machinery) for
   explicit re-syncs — just never implicit on save.

## Acceptance

1. Stub-adapter test: Save on a fetched position makes **zero** adapter/RPC calls;
   record persisted with binding; card content identical before/after; no
   "Fetching…" state shown.
2. Live read-only: save a fetched position → instant, panel untouched; the existing
   fetch-refresh flow still works (fetch a new position → saved pools refresh).
3. `python -m py_compile`; full suite green; v5.3.27 + STATUS.md.
   **No EXE build, no push, no release.** Live DBs read-only; no on-chain actions.

## Report back (brief)

- The save path you cut the rescan out of (exact call site) + the confirmation UX.
- Test transcript.