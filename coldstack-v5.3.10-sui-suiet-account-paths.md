# ColdStack v5.3.10 — SUI custom-path parsing (Suiet account-level derivation)

**Base:** HEAD `f369059` (v5.3.9). Single-prompt train — includes docs, build, release, backup.
**Author:** Slater (CTO). **Approved by:** Kris, 2026-09-11 (16:38 report + review request).
**Scope:** SUI derivation path handling ONLY. No other chains, no balance code.

---

## Context — from the live review of Kris's report (2026-09-11, all verified)

Kris's Suiet wallet shows `0x04887176a0791ac1837bc654533990820a33f2c289b8636c7066a1865191b314`
(≈1,327.89 SUI on-chain) while ColdStack derives `0x25d3ac9e…` from N1's mnemonic.

Findings:

1. **The engine is correct.** Deployed EXEs are byte-identical to the audited build
   (md5 `3a460f4e…` ×3). The SUI code path (dialog → `do_derive` → `derive_from_mnemonic` →
   `_derive_sui_slip10`) implements the official scheme exactly, and independent reference
   vectors reproduce it byte-for-byte (12- and 24-word). ColdStack derives the official
   first account of the phrase it holds — `0x25d3…` IS N1's official-scheme account-0 address.

2. **Suiet's actual scheme, read from Kris's installed extension** (Brave, Suiet 0.9.0,
   `khpkpbbcccdmmclmpigdgddabeilkdpd`): first account derives the same standard path
   `m/44'/784'/0'/0'/0'`; its MULTI-ACCOUNT progression walks the **account level**:
   ```js
   static derivationHdPath(t) { return `m/44'/784'/${t}'/0'/0'` }
   ```
   Suiet "Account 2" = `m/44'/784'/1'/0'/0'`, "Account 3" = `m/44'/784'/2'/0'/0'`, …

3. **ColdStack GAP (verified live):** `_derive_sui_slip10` **ignores the custom path** —
   `derive_from_mnemonic(mn, "SUI (Sui)", path="m/44'/784'/1'/0'/0'")` returns the SAME
   address as the default (the `path` argument only feeds the result-dict display).
   Consequence: Suiet account-level addresses are currently **unreachable** in ColdStack —
   the dialog's editable path field is cosmetic for SUI, and the Address Index field drives
   the 5th segment (`m/44'/784'/0'/0'/N'`), not the account level Suiet uses.

So if Kris's Suiet wallet is N1's phrase at "Account 2+" (account level ≥1), ColdStack
cannot derive that address today. This train closes the gap.

## Task 1 — Parse the custom path in `_derive_sui_slip10` (src/derivation_engine.py)

- Signature stays. When a path is supplied, it is now **authoritative** for derivation
  (not just display). Parse with the strict SUI form (all-hardened, 5 levels):
  `^m/44'/784'/(\d+)'/(\d+)'/(\d+)'$` → derive levels [44', 784', A', B', I']
  exactly (A=account, B=change/external flag 0|1, I=index — Suiet's template
  `m/44'/784'/{accountIndex}'/{isExternal?1:0}'/{addressIndex}'`).
- **Invalid path → raise a clear error** (e.g. `Invalid SUI derivation path: must be
  m/44'/784'/A'/B'/I'`). NEVER silently fall back to the default — a typo'd path silently
  deriving the default address is exactly the bug class we are killing. The dialog already
  surfaces engine errors via the status label.
- No path supplied → current behavior unchanged: `m/44'/784'/0'/0'/{address_index}'`.
- The result dict's `path` field reflects the actually-derived path.

## Task 2 — Dialog hint (src/account_dialogs.py, `show_derivation_dialog`)

Extend the `path_hint` tip (keep it one compact label, both sentences):
SOL — Address Index drives the account level (m/44'/501'/{N}'/0'); SUI — index drives the
final level; for Suiet account K use path `m/44'/784'/K'/0'/0'`.

No other dialog changes. `update_path`'s preview behavior stays (preview refreshes on
chain/index change and overwrites a manual path edit — existing known behavior, same as
other chains; the user re-edits after selecting the chain).

## Vector gates (must reproduce EXACTLY — reference-computed 2026-09-11)

Test mnemonic: `abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon about`

- No path, index 0 → `0x830426b60ffac6adab1503cfe82b6d0bdcce8bf6027d0eedd0941b3b29299e60` (unchanged)
- No path, index 1 → `0x238c688586ea0f0eee06b695ff4f1ca5f1bc70a1a93f55c52900ca8f8d5dc82d` (unchanged)
- Path `m/44'/784'/1'/0'/0'` (Suiet Account 2) → `0x170eb76c1e75354ba79c1775811d40a187108ef7a2b6475d38d98edcd0e434c5`
- Path `m/44'/784'/2'/0'/0'` (Suiet Account 3) → `0xced357590bee5d8792d7bb6b20e20c1506242d5f80fc04927646f62cf107d3aa`
- Path `m/44'/784'/0'/0'/0'` (explicit, equals default) → same as no-path index 0
- Invalid paths (`m/44'/784'/0'/0`, `m/44'/60'/0'/0'/0'`, unhardened `m/44/784/0/0/0`) → clear errors, NO silent default
- SOL regression: `HAgk14JpMQLgt6rVgv7cBQFJWFto5Dqxi472uT3DKpqk` (index 0, must not drift)

## Runtime smoke gate (standing requirement for GUI-path changes)

Re-run the v5.3.8 dialog smoke (stub gui under a real display): dialog builds with all
four buttons, no exception. Plus one engine-level flow check using the EXACT `do_derive`
call pattern (`path=preview, address_index=idx`) for: SUI index 2, and SUI with an
edited account-1 path — paste outputs into build notes.

## Ship checklist

1. `VERSION = "5.3.10"` in `src/gui_main_v5.py`.
2. `python -m py_compile` changed files.
3. STATUS.md v5.3.10 entry (Suiet account-level derivation unlocked; path is now
   authoritative for SUI; invalid paths error loudly).
4. Build: `python build_gui_v5.py` → dist/ → USB_DEPLOYMENT.
5. Commit as v5.3.10, push, GitHub release v5.3.10 (new tag, house convention).
6. `backups/v5.3.10/` (src + related active files).
7. Commit this prompt file (house precedent).
8. Deploy: coordinate with Kris (app-closed EXE swap) — sync EXE **and**
   `rpc_endpoints.json` together to `kimi/portfolios/kitandpaul/` and
   `kimi/portfolios/the-pack-portfolio/` (standing rule since v5.3.9).

## Verification checklist (report each)

- [ ] All seven vector gates pass exactly (values above)
- [ ] Invalid paths raise visible errors — no silent fallback (test all three forms)
- [ ] Dialog smoke: buttons render, no exception
- [ ] `do_derive`-pattern flow checks: SUI idx 2 + Suiet account-2 path
- [ ] VERSION 5.3.10; STATUS.md; py_compile clean; release + backup; deploy reported

## Out of scope (do NOT touch)

- SOL/EVM/BTC/DASH derivation, balance engine, RPC config (v5.3.9 just shipped), vault.
- Whether Kris's Suiet wallet is N1's phrase at a later account or a different mnemonic —
  operational question resolved user-side (Suiet account number + phrase comparison),
  not code.