# ColdStack v5.3.4 — Orca Scan Routing + Close Confirmation

**Base:** commit `cbbf817` (v5.3.3, shipped). Work on top of it. Do not start until
you have confirmed v5.3.3 is fully shipped (it is committed; verify `git log -1`).
This is a single-prompt train: this prompt IS the final prompt — it includes docs,
build, release, and backup steps.

**Author:** Slater (CTO). **Approved by:** Kris, 2026-09-10.
**Scope locked to Tasks 1–4.** Task 5 is opportunistic carryover with a hard cut line.

---

## Context (from a live K&P session, 2026-09-10)

Kris closed Project X position `hyperevm:529523` from the K&P portfolio GUI
(`kimi/portfolios/kitandpaul/coldstack.exe`). The close WORKED on-chain
(`gui_debug.log`: `action=close_position token_id=529523 decrease=0x0182… collect=0xc113…`)
but exposed three gaps:

1. **Close is fire-and-forget.** `_lp_close_position_dialog._do_close` (EVM path) broadcasts
   decrease + collect, then `time.sleep(3)` → `_lp_do_fetch()`. No receipt wait, no status
   check. The GUI says "2 TXs **submitted**" — never "confirmed". The building block exists:
   `hyperliquid_writer._wait_for_tx_receipt(tx_hash, timeout=120, poll_interval=2.0)`
   (returns receipt dict with `status`; **raises** RuntimeError on revert; returns None on
   timeout). It is already used by the Remove Liquidity dialog — the close flow just
   doesn't use it.
2. **Orca never scanned.** Same session: the wallet holds an active Orca Whirlpool position,
   but the log has ZERO `[orca-scan]` lines. Root causes in Task 3.
3. **Closed saved pools resurrect.** The saved-pool merge in `_lp_do_full_scan` (and the
   `_lp_fetch_saved_only` fast path) re-adds saved positions with NO `liquidity == 0`
   filter — the main scan filter (`lp_engine.fetch_all_positions`) is bypassed. A starred
   position that has been closed reappears on every scan.

---

## Task 1 — EVM close confirmation (HyperEVM / BSC / Base)

In `src/lp_tab.py`, `_lp_close_position_dialog._do_close` (the EVM branch):

- After `writer.close_position(...)` returns `tx_hashes`: **wait for each receipt** with
  `writer._wait_for_tx_receipt(tx_hash, timeout=120, poll_interval=2.0)`. This runs in the
  existing background thread — no UI blocking. While waiting, show
  "Closing position… waiting for on-chain confirmation".
- **Full close (2 TXs):** both receipts `status == "0x1"` → then verify business-level
  closure: `writer._get_position_liquidity(writer._parse_token_id(position.position_id)) == 0`.
  - Verified → notification "Position closed ✓ confirmed on-chain" → remove the card and
    the saved-pool bookmark via the Task 4 helper → update status label.
  - Liquidity still > 0 (should not happen on a clean close) → treat as partial: keep the
    card, warn "Liquidity still on-chain — retry Close or Collect Fees".
- **Partial close (1 TX — collect failed):** wait that receipt; keep the existing
  "Partially closed… Retry 'Collect Fees'" notification. Do NOT remove the card.
- **Failure paths:** RuntimeError (revert) → error notification with the tx hash, keep the
  card. Receipt None after 120s → "Close TX unconfirmed after 120s — check the explorer",
  keep the card. **Never claim success without receipts.**
- Remove the `time.sleep(3)` + blind `_lp_do_fetch()` from the confirmed path. Manual
  Refresh stays available; the card is removed immediately on confirmation instead.

## Task 2 — Solana close confirmation (Orca)

`_do_close_sol`: `orca_writer.close_position` already confirms each TX internally
(`_wait_for_confirmation`, 45s each) — do not touch the writer. Add post-close
verification in the GUI layer:

- After close returns: poll up to ~10s until the position account is gone —
  `_get_account_data(_derive_position_address(mint))` returns None (closePosition burns
  the NFT; helpers live in `venue_adapters/orca_adapter.py`).
- Gone → "Position closed ✓ confirmed on-chain" → card removal + bookmark cleanup
  (Task 4 helper).
- Still there after the poll → "Close TXs submitted but position still on-chain — verify"
  and keep the card.

## Task 3 — Orca scan routing

**3a. `_lp_do_filtered_full_scan(address, venue_key)` when `venue_key == "orca"` and the
current wallet is NOT a Solana base58 address** (HYPE `0x` wallet, or Account mode):
- Account mode: resolve `sol = self._lp_resolve_account_address(self._lp_get_current_account_name(), prefer="solana")`.
  If it's a valid Solana address → scan `sol` instead of the `0x` address (today the `0x`
  address is passed straight to `OrcaAdapter.fetch_all_positions`, which returns `[]`
  silently — `_is_solana_address` is False).
- If no Solana address resolves → set status label: "Orca scan skipped — no Solana address
  saved for this account. Add the wallet's Solana address to the vault, or scan the
  Solana address directly in Address mode." Return an empty scan. **No silent zeros.**
- Address mode with a typed `0x` address: same visible skip message pointing at
  Address mode with the Solana wallet address.
- Raw Solana address already entered → behavior unchanged.

**3b. `_lp_do_full_scan` Orca extra pass:** when Account mode is active but no Solana
address resolves, don't skip silently — append a short note to the final scan status
(e.g. "5 positions · Orca skipped (no Solana address for account)") so a missing pool
is explainable instead of mysterious.

**3c.** `_lp_do_filtered_scan` inherits the fix via `_lp_do_filtered_full_scan` — verify,
no double-implement.

## Task 4 — Closed-position resurrection filter + shared cleanup helper

- In `_lp_do_full_scan`'s saved-pool merge loop AND `_lp_fetch_saved_only`: when
  "Include closed" is UNCHECKED, skip fetched saved positions with
  `raw_data.get("liquidity", 0) == 0` (EVM/Base/BSC). For Orca saved positions, skip when
  liquidity == 0 AND no uncollected fees (mirror `OrcaAdapter.fetch_all_positions`).
  With "Include closed" CHECKED, show them (current behavior).
- **Do not auto-delete bookmarks during scans** — filter only. Bookmark cleanup happens
  on confirmed close (Tasks 1–2), which is explicit user action.
- Refactor `_lp_remove_pool` into a small shared helper, e.g.
  `_lp_forget_position(position, card_frame=None, notify="Pool removed: …")` — removes the
  saved-pool entry (`remove_saved_pool(address_db, token_id, venue)`), saves the vault,
  destroys the card (both the `solana:` and EVM card-key branches), updates the saved-pools
  count. The close flows call it with a close-appropriate message. Keep the existing
  "Remove Pool" button behavior identical.

## Task 5 — Carryover from v5.3.3 deferral (EXE-rebuild items — opportunistic)

Only after Tasks 1–4 pass all verification. If anything here gets hairy, **cut it and
defer to the next train** — do not hold the release:
- `railgun_bridge.py` `_read_logs` stderr capture (sidecar stderr is invisible on crash).
- `STARTUP_TIMEOUT` 30s → 90s.
- Sidecar `/engine/load-provider` already-loaded-chain guard (`sidecar/src/routes/engine.js`).
- `callbacks.js` `chain=[object Object]` logging fix.

---

## Out of scope (do NOT touch)
- Orca writer TX logic (confirmed working — v5.2.5 round-trip).
- Rebalance flows, ColdTrack/tax schema, price engine, vault tracker.

## Ship checklist (single-prompt protocol)
1. Bump `VERSION = "5.3.4"` in `src/gui_main_v5.py` (line 9, single source of truth).
2. `python -m py_compile` every changed `.py` (lp_tab.py is the hot file).
3. Sidecar gates (v5.3.3 protocol): `node --check` on every `sidecar/src/**/*.js` +
   cold-start boot smoke test (`node src/server.js` → health OK ≤30s → kill).
4. STATUS.md: new v5.3.4 section (Summary / Fixed: close confirmation, Orca routing,
   resurrection filter / Process). README.md only if it documents close or scan behavior.
5. Build: `python build_gui_v5.py` → `dist/coldstack.exe` → USB_DEPLOYMENT copy with
   previous-exe backup (script handles it).
6. Commit as v5.3.4, push. Review existing release formatting on the releases page, then
   create the v5.3.4 release with the new v tag.
7. Backup src + related active files to `backups/v5.3.4/` (protocol).
8. Commit this prompt file with the release (v5.3.3 precedent).
9. **Deploy — coordinate with Kris first:** live EXEs run from portfolio folders
   (`kimi/portfolios/kitandpaul/coldstack.exe`, `kimi/portfolios/the-pack-portfolio/`).
   The app may be RUNNING — Windows file lock. Kris closes the app, then the new EXE is
   copied in. Sidecar copies sync without lock concerns (v5.3.3 pattern).

## Verification checklist (report each)
- [ ] EVM close: receipts waited + status checked + liquidity==0 verified before card removal;
      revert and timeout paths keep the card; no blind sleep/refetch on the confirmed path
- [ ] Solana close: position-account-gone check gates the card removal
- [ ] Orca filtered scan, account WITH Solana address → scans the Solana address,
      `[orca-scan]` line appears in the log
- [ ] Orca filtered scan, account WITHOUT Solana address → visible skip message
- [ ] Full-scan status line notes the Orca skip reason when applicable
- [ ] Closed saved pool no longer resurrects with "Include closed" unchecked;
      still visible when checked
- [ ] `py_compile` clean; sidecar `node --check` + boot smoke pass
- [ ] VERSION 5.3.4; STATUS.md updated; EXE builds; release created; `backups/v5.3.4/` exists
- [ ] Deploy steps reported (or explicitly deferred pending Kris closing the app)