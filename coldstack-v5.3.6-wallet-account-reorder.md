# ColdStack v5.3.6 — Wallet Screen Account Reordering

**Base:** current HEAD (`1f8433a`, daily-backup commit on top of v5.3.5 `45300db`, shipped).
Single-prompt train: this prompt IS the final prompt — includes docs, build, release, backup.

**Author:** Slater (CTO). **Approved by:** Kris, 2026-09-11.
**Scope:** reorder accounts WITHIN a pool group on the Wallet screen. Nothing else.

---

## Context

Kris wants accounts on the Wallet screen reorderable within their pool group (e.g. "Genesis"),
never across pools. The architecture is already 80% there:

- Vault model: `address_db["pools"] = {pool_name: {"accounts": [account_name, …]}}` — the
  account list **IS the display order** (`refresh_left_panel` renders each pool's accounts in
  list order; `add_account(name, pool)` appends).
- Persistence pattern is established: mutate → `key_manager.save_encrypted_data(gui.current_password)`
  (same as `show_add_account_dialog.do_add`).
- The Wallet screen left panel is built by `refresh_left_panel` (gui_main_v5.py ~line 2003):
  pool header labels (`-- {pool} --`) then one `CTkButton` per account, packed directly.

**UX decision (locked):** ▲/▼ move buttons on each account row — NOT drag-and-drop
(tkinterdnd2 is fragile in a frozen PyInstaller EXE, and click-to-move is friendlier for
non-technical users of this app). One click = one position swap = one immediate save.

---

## Task 1 — Reorderable account rows in the left panel

In `refresh_left_panel` (gui_main_v5.py), refactor each account row from a single packed
button into a small row frame:

```
[ account button (existing style, existing select_account behavior) ][ ▲ ][ ▼ ]
```

- Keep the existing account button look (transparent fg, border, 220×35-ish) and its
  `select_account(pool_name, account)` command — clicking still selects the account.
- ▲/▼: small buttons (~28×28) on the row's right edge, subtle gray, hover feedback.
- ▲ disabled (`state="disabled"`) when the account is first in its pool; ▼ disabled when last.
- Unassigned section: NO reorder buttons (it's rendered `sorted()`; leave it — out of scope).

## Task 2 — Reorder logic

New helper, e.g. `_move_account_in_pool(self, pool_name, account_name, direction)`:

- Find `address_db["pools"][pool_name]["accounts"]`; locate `account_name`'s index.
- Swap with index±1. **Never moves across pools** — a swap past the list edge is a no-op
  (the disabled buttons already prevent it; keep the guard anyway as defense-in-depth).
- Guard `current_password` (vault must be unlocked — the left panel only exists unlocked,
  but if the password is somehow gone, show a notification and abort without mutating).
- After the swap: `save_encrypted_data(current_password)` → `refresh_left_panel()`.
- The currently selected account (`self.current_account` / `self.current_pool`) keeps its
  selection across the re-render (right panel title unchanged).
- Repeated clicking is safe: each click is one atomic swap+save (vault is small; the
  save-on-every-move pattern matches `do_add`).

Notes:
- No key_manager_agent changes — order-only mutations are invisible to key operations, and
  GUI-side vault saves with the agent running are the established pattern (v5.3.4+).
- Dropdowns elsewhere (LP tab / vault tab account menus) use `sorted()` and stay alphabetical —
  explicitly OUT of scope; do not touch them.

## Ship checklist
1. Bump `VERSION = "5.3.6"` in `src/gui_main_v5.py`.
2. `python -m py_compile` changed files.
3. Sidecar gates still run (protocol; no sidecar changes expected): `node --check` sweep +
   cold-start boot smoke test.
4. STATUS.md: new v5.3.6 section. README.md only if it documents the Wallet screen.
5. Build: `python build_gui_v5.py` → dist/ → USB_DEPLOYMENT (script handles backup).
6. Commit as v5.3.6, push, create the v5.3.6 release (review existing release formatting;
   new v tag).
7. Backup src + related active files to `backups/v5.3.6/`.
8. Commit this prompt file (house precedent).
9. Deploy: coordinate with Kris — app may be running (Windows file lock); he closes it, then
   sync the new EXE to `kimi/portfolios/kitandpaul/` (and other live portfolio folders).

## Verification checklist (report each)
- [ ] Pool with 3+ accounts: ▲ moves an account up one slot, ▼ down one; panel refreshes in place
- [ ] First account's ▲ and last account's ▼ are disabled — no wraparound, no cross-pool moves
- [ ] Order persists: reorder → close app → reopen → unlock → order intact (vault round-trip)
- [ ] Unassigned section renders unchanged, still sorted, no reorder buttons
- [ ] Account selection survives a reorder click (right panel title unchanged)
- [ ] Dropdowns elsewhere still work and remain alphabetical
- [ ] VERSION 5.3.6; STATUS.md; py_compile clean; EXE builds; release + backup done;
      deploy reported