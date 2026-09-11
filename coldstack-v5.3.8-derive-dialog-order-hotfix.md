# ColdStack v5.3.8 — Hotfix: Derive Addresses dialog half-builds (widget-order NameError)

**Base:** HEAD `a23a576` (v5.3.7). Single-prompt hotfix train — includes docs, build, release, backup.
**Author:** Slater (CTO). **Approved by:** Kris, 2026-09-11 (reported the regression at 14:50).
**Scope:** ONE bug. Do not touch derivation engine, balance engine, or any other code.

---

## Regression

Since v5.3.7, the **Derive Addresses dialog** (from mnemonic) opens half-built:
chain dropdown, path field and tip render — but **no Address Index field, no results
area, and no buttons** (Derive / Save to Account / Derive Another / Close are all missing).
Reported live by Kris on the deployed v5.3.7 EXE.

## Root cause (pinned to lines — verified against HEAD)

`show_derivation_dialog` in `src/account_dialogs.py`:

- `_read_index()` (def ~line 899) reads `index_entry` — **created at ~line 927**.
- `update_path()` (def ~line 902) calls `_read_index()` unconditionally.
- `update_path()` is **invoked directly at ~line 923** (right after
  `chain_combo.bind(...)`) — BEFORE `index_entry` exists.

Result: `NameError: free variable 'index_entry' referenced before assignment` on dialog
open. The exception aborts `show_derivation_dialog` mid-build — everything below the call
site never executes. Tkinter swallows the traceback (prints to stderr — invisible in the
frozen EXE), leaving a silent half-built dialog.

`py_compile` passes and all derivation vectors pass — this is purely a widget
**creation-order** bug introduced by v5.3.7's live path preview.

## Fix (one move — nothing else)

Relocate the Address Index widget creation block — the lines from the
`# Address index` comment through `index_entry.pack(anchor="w", pady=(0, 10))`
(label + `CTkEntry` + `insert("0")` + `pack`) — to **immediately after
`path_hint.pack(...)`**, i.e. BEFORE the `def _read_index():` definition. Move the two
`index_entry.bind(...)` lines together with the block.

After the move the order must be:

```
path_hint.pack(...)
# Address index (moved above — update_path reads index_entry)
index label + index_entry + insert("0") + pack
index_entry.bind("<KeyRelease>", ...) / bind("<FocusOut>", ...)
def _read_index() ...
def update_path(event=None) ...
chain_combo.bind("<<ComboboxSelected>>", update_path)
update_path()
```

`update_path()`'s initial call now runs with `index_entry` alive. No logic changes, no
reformatting, no drive-by edits.

**Sanity scan (read-only):** grep `src/account_dialogs.py` for the same pattern in the
other dialogs (add-private-key derive ~571, custom-mnemonic ~690) — a nested function
reading a widget that is created after a direct call to that function. Only fix if the
pattern actually exists there; v5.3.7 did not touch them, so expect none.

## Why the vectors still pass (do NOT re-touch the engine)

The v5.3.7 derivation engine was independently re-verified after this regression report
(reference implementations, not self-checks) — all MATCH:

- SUI official vector (abandon…about, idx 0) → `0x830426b60ffac6adab1503cfe82b6d0bdcce8bf6027d0eedd0941b3b29299e60`
- SOL base (idx 0) → `HAgk14JpMQLgt6rVgv7cBQFJWFto5Dqxi472uT3DKpqk`
- SOL dialog flow (path `m/44'/501'/1'/0'` + address_index=1) → `Hh8QwFUA6MtVu1qAoq12ucvFHNwCcVTV7hpWjeY1Hztb`
- SOL legacy (path `m/44'/501'/0'/1'`) → `GKreMsHvt8A79VApjboYDq3J4ZCXSJRYYQk9BscMbi1H`
- SUI dialog flow (path `m/44'/784'/0'/0'/1'` + address_index=1) → `0x238c688586ea0f0eee06b695ff4f1ca5f1bc70a1a93f55c52900ca8f8d5dc82d`
- EVM idx 0/1 → `0x9858EfFD232B4033E47d90003D41EC34EcaEda94` / `0x6Fac4D18c912343BF86fa7049364Dd4E424Ab9C0`

Re-run these six after the fix (they must be unchanged — they gate the engine, the hotfix
gates the dialog). Any drift = stop and report.

## Runtime smoke gate (required — this bug class is invisible to py_compile)

GUI paths must be exercised at runtime before ship. WSLg gives you a real display.

Throwaway smoke script (do NOT commit) that:

1. Builds a stub `gui`: `gui.key_manager.show_mnemonic(name)` returns the test mnemonic
   `abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon about`;
   every other attribute/method is a no-op lambda (button callbacks are not invoked at
   build time).
2. Calls `show_derivation_dialog(gui, "SMOKE")` on a real ctk root.
3. Asserts: no exception; the dialog contains ≥4 `CTkButton` children (walk
   `winfo_children` recursively); an `Address Index` label exists; the path field contains
   a non-empty path.
4. Prints PASS/FAIL per assertion, then destroys the dialog.

Paste the smoke output into the build notes. If the smoke script cannot run in the build
environment, say so explicitly in the report instead of claiming success.

## Ship checklist

1. `VERSION = "5.3.8"` in `src/gui_main_v5.py`.
2. `python -m py_compile` on changed files.
3. STATUS.md: v5.3.8 entry — the regression, the one-move fix, the new runtime-smoke gate.
4. Build: `python build_gui_v5.py` → dist/ → USB_DEPLOYMENT.
5. Commit as v5.3.8, push, create GitHub release v5.3.8 (new tag — house convention for
   post-ship fixes; review existing release formatting).
6. `backups/v5.3.8/` (src + related active files).
7. Commit this prompt file (house precedent).
8. Deploy: coordinate with Kris — the app may be running (Windows file lock); he closes
   it, then sync the EXE to `kimi/portfolios/kitandpaul/` (and other live portfolio folders).

## Verification checklist (report each)

- [ ] Smoke script output: no exception, ≥4 buttons, index field present, path populated
- [ ] All six derivation vectors unchanged (values above)
- [ ] Dialog opens with the full button row + auto-derived address (Kris confirms post-deploy)
- [ ] VERSION 5.3.8; STATUS.md; py_compile clean; EXE builds; release + backup done
- [ ] Deploy reported (app-closed swap)