# ColdStack v5.3.9 — Stale rpc_endpoints.json self-heal + repo config sync

**Base:** HEAD `e17bd86` (v5.3.8). Single-prompt train — includes docs, build, release, backup.
**Author:** Slater (CTO). **Approved by:** Kris, 2026-09-11 (15:36 report).
**Scope:** rpc_endpoints.json handling ONLY. No derivation changes — the v5.3.7 SUI engine is
vector-verified (12- and 24-word) and correct. No balance-engine parsing changes (v5.3.7's
error detection works as designed).

---

## Context — what happened (verified live, 2026-09-11)

Kris derived a SUI address from N1's mnemonic (v5.3.8 dialog, working) and hit
"Could not fetch SUI balance" on Check Balance.

Root cause chain:

1. v5.3.7 changed the SUI endpoints in **code** (`DEFAULT_ENDPOINTS` in `src/rpc_config.py`
   and the `SUI_RPC` constant in `src/balance_engine.py` → publicnode primary + blockpi
   fallback) — but did NOT update **`rpc_endpoints.json`** (repo root), and did not sync
   the JSON to deployed folders.
2. `load_rpc_config` (src/rpc_config.py) prefers a **user file in the runtime directory
   (next to the EXE)** over everything else: "user-customized file first … if config is
   not None: return config". The deployed folder
   `kimi/portfolios/the-pack-portfolio/` still held a Sep-3 `rpc_endpoints.json`
   (v5.3.3-era) whose `sui` entry points at the DEAD official
   `https://fullnode.mainnet.sui.io` (JSON-RPC deprecated by Sui — verified live:
   "Method not found. JSON-RPC on public fullnodes has been deprecated").
   That stale entry silently shadowed the fixed defaults → v5.3.7's error detection
   correctly refused to parse the error into a 0.0 → visible "Could not fetch SUI balance".
3. The repo-root `rpc_endpoints.json` (which is also the "bundled" fallback in script
   mode, and the source for deployments) is **still stale**: sui = fullnode.mainnet.sui.io.
4. Deployment history shows the ship process syncs the EXE only — the JSON was last
   written next to the the-pack EXE on Sep 3 and never since. kitandpaul has no JSON
   (works off the corrected defaults).

**Interim mitigation already applied (2026-09-11 15:45, by Slater):** the stale file was
renamed to `rpc_endpoints.json.stale-sui.bak` in the the-pack-portfolio folder — that
instance now falls back to hardcoded defaults (publicnode + blockpi, both live-verified).
Keep the .bak file as-is; this train supersedes it by deploying a correct JSON.

## Task 1 — Update repo-root `rpc_endpoints.json`

- `sui` entry → `"url": "https://sui-rpc.publicnode.com"`, `"auth": null`,
  `"fallback": "https://sui.blockpi.network/v1/rpc/public"`.
- Bump the file's `"updated"` field to `2026-09-11`.
- Diff the rest of the file against `DEFAULT_ENDPOINTS` in `src/rpc_config.py` — the JSON
  is documented to mirror it. Report (and fix, per-chain) any drift found; do not
  reformat unrelated entries.

## Task 2 — Loader self-heal for deprecated endpoints (src/rpc_config.py)

Add a small, evidence-based deprecation map:

```python
DEPRECATED_ENDPOINTS: Dict[str, str] = {
    # Official Sui fullnodes: JSON-RPC deprecated (live-verified 2026-09-11)
    "https://fullnode.mainnet.sui.io": "https://sui-rpc.publicnode.com",
}
```

Behavior in `load_rpc_config` (or `_try_load_file` after a successful load):

- Scan every chain's `url` and `fallback` in the loaded config. If either matches a
  deprecated URL, substitute the replacement (same field), print a clear warning
  (`RPC config: deprecated endpoint for <chain> replaced (<old> → <new>)`), and
  **rewrite the loaded JSON file in place**: only the affected entries change, all other
  chains/fields preserved verbatim, `"updated"` field bumped to today. If the rewrite
  fails (permissions), keep the in-memory fix and log it — never crash over config.
- Applies to user, bundled, and default paths alike (cheap: run the sweep over whatever
  config was chosen).
- This is deliberately minimal: one verified-dead URL. Do not add speculative entries.

**Runtime gate (required):** a throwaway fixture test — write a temp `rpc_endpoints.json`
containing the dead Sui URL, point the loader at it (`base_dir`), load, and assert:
(a) sui url is now publicnode in the returned config, (b) the temp file was rewritten
with the fix and other entries untouched, (c) a clean load with no deprecated URLs is
byte-identical in behavior (no rewrite, no warning). Paste output into build notes.

## Task 3 — Fetch smoke (live)

Call `fetch_sui_balance` (via `BalanceEngine` with default config) on the public address
`0x04887176a0791ac1837bc654533990820a33f2c289b8636c7066a1865191b314` — assert a
non-None float is returned (public RPC, no key involved). Paste the value in build notes.
(Current on-chain balance ≈ 1327.89 SUI — assert non-None only, not the exact amount.)

## Task 4 — Ship + deploy with config sync

Standard checklist, plus the new explicit step:

1. `VERSION = "5.3.9"` in `src/gui_main_v5.py`.
2. `python -m py_compile` changed files.
3. STATUS.md v5.3.9 entry (root cause + fix + the deploy-sync lesson).
4. Build: `python build_gui_v5.py` → dist/ → USB_DEPLOYMENT.
5. Commit as v5.3.9, push, GitHub release v5.3.9 (new tag; review existing formatting).
6. `backups/v5.3.9/` (src + related active files incl. the corrected rpc_endpoints.json).
7. Commit this prompt file (house precedent).
8. **Deploy (coordinate with Kris — app-closed swap): new EXE + the corrected
   `rpc_endpoints.json` together to every live folder — at minimum
   `kimi/portfolios/kitandpaul/` and `kimi/portfolios/the-pack-portfolio/`
   (which currently has no runtime JSON; deploying one there makes the config explicit;
   the `.stale-sui.bak` stays untouched).** Add "sync rpc_endpoints.json on every
   deploy" to the repo's deploy notes (AGENTS.md or STATUS deploy section) so this
   never drifts again.

## Verification checklist (report each)

- [ ] Fixture test: deprecated URL repaired in memory + file rewritten; clean file untouched
- [ ] Repo-root rpc_endpoints.json: sui = publicnode + blockpi fallback; drift diff reported
- [ ] Live fetch smoke: SUI balance for 0x0488… returns non-None float
- [ ] Deployed folders: EXE + JSON synced (report folder list); .stale-sui.bak left in place
- [ ] VERSION 5.3.9; STATUS.md; py_compile clean; release + backup done; deploy reported

## Out of scope (do NOT touch)

- Derivation engine (verified correct: SUI official vector, 24-word vector, SOL/EVM flows).
- Balance-engine parsing (v5.3.7 error handling works as designed).
- The derived-address-vs-Suiet question — operational resolution (user-side index sweep /
  watch-only address), not code.