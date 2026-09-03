# ColdStack v5.3.2 — Railgun Hotfix Ship (show_mnemonic crash + unshield-to-native + chain trim)

Project: `/mnt/b/Blockchain/coldstack` (B:\Blockchain\coldstack).
This is the FINAL prompt of the v5.3.2 train — it includes build, docs, git, release, and deployment.

## Context — live-run evidence (2026-09-03)

Kris is mid-run: **shield ETH on Arbitrum from GSS → send to G6 → unshield**. Two blockers.

### Blocker 1 (CRITICAL — new): `AttributeError: 'ColdStackGUI' object has no attribute 'show_mnemonic'`

Live traceback from the running v5.3.1 EXE (`gui_debug.log`, 2026-09-03):

```
File "railgun_tab.py", line 469, in _on_load_wallet
AttributeError: 'ColdStackGUI' object has no attribute 'show_mnemonic'. Did you mean: 'hide_mnemonic'?
```

`show_mnemonic` is NOT a `ColdStackGUI` method. It lives on the key manager:
`PortableKeyManager.show_mnemonic` (gui_main_v5.py:259) and `main.KeyManager.show_mnemonic` (main.py:156).
The working pattern is already used in production code — account_dialogs.py:607: `gui.key_manager.show_mnemonic(acct)`.

**FOUR call sites have the wrong pattern `self.gui.show_mnemonic(...)`** — every Railgun transaction path
crashes at the mnemonic fetch, silently (the handler dies before any notification shows):

| File | Line | Crashes |
|------|------|---------|
| `src/railgun_tab.py` | 469 (`_on_load_wallet`) | Load Wallet |
| `src/railgun_tx_dialogs.py` | 396 (`ShieldDialog._submit`) | Shield |
| `src/railgun_tx_dialogs.py` | 505 (`UnshieldDialog._submit`) | Unshield |
| `src/railgun_tx_dialogs.py` | 602 (`TransferDialog._submit`) | Private transfer |

### Blocker 2 (known, pending): 6-chain cosmetic init error + unshield pays WETH

Engine init **succeeds**, then the success-response builder crashes at `sidecar/src/routes/engine.js:141`
(`SUPPORTED_RAILGUN_NETWORKS.map(n => n.toString())` — networks.js includes base/optimism, which are
`undefined` in the Railgun SDK). The GUI shows "✗ Engine init failed: Sidecar error: Cannot read
properties of undefined (reading 'toString')" even though the engine actually initialized (today's log
proves arbitrum loaded fine). Separately, `/transfer/unshield` pays out WRAPPED ETH (WETH) — no unwrap chain.

The complete fix is ALREADY SPEC'd — execute ALL tasks in:
`/mnt/b/OpenClaw/.openclaw/workspace/slater/prompts/completed/coldstack-railgun-native-eth-unshield.md`

## Tasks

### Task 1 — Fix the four show_mnemonic call sites
Change `self.gui.show_mnemonic(` → `self.gui.key_manager.show_mnemonic(` at all four sites above.
- Add a None guard first: if `self.gui.key_manager` is None → `show_notification("Vault not unlocked", error=True)` and return.
- Sweep `railgun_tab.py` + `railgun_tx_dialogs.py` for any other `self.gui.<method>` that only exists on
  PortableKeyManager/main.KeyManager (e.g. `show_private_key`) — fix to `self.gui.key_manager.<method>`.
- Do NOT touch `self.gui.show_notification` calls — that IS a real ColdStackGUI method.

### Task 2 — Execute the unshield prompt (already written)
Run ALL tasks (1–6) in `/mnt/b/OpenClaw/.openclaw/workspace/slater/prompts/completed/coldstack-railgun-native-eth-unshield.md`
(networks.js trim to 4 chains, GUI chain surfaces, sidecar unshield-to-native chain, UnshieldDialog native
option, balance labels, checks). Also fix the hardcoded "all 6 chains" text at railgun_tab.py ~line 678 → 4.

### Task 3 — Checks
- `python -m py_compile` on every touched .py file
- `node --check` on every touched sidecar .js file
- Grep: zero remaining `self.gui.show_mnemonic` references

### Task 4 — Build + version
- `gui_main_v5.py` VERSION → 5.3.2
- Build the Windows EXE (`build_gui_v5.py`); confirm Windows PE32+ output.

### Task 5 — Docs + git + release
- STATUS.md: v5.3.2 entry (show_mnemonic fix, 4-chain trim, unshield-to-native; bundles the uncommitted
  RPC-endpoint-refresh + provider-retry work)
- README.md: latest-release link → v5.3.2. AGENTS.md: version refs → v5.3.2.
- Commit ALL uncommitted work (rpc_endpoints.json, balance_engine.py, rpc_config.py, railgun_bridge.py,
  railgun_tab.py, railgun_tx_dialogs.py, sidecar/src changes, doc-file deletions) as one v5.3.2 commit. Push.
- GitHub release: NEW release + v5.3.2 tag. Review the existing release formatting on the repo's releases page first; match it.

### Task 6 — Deploy (⚠️ Kris runs the app from the portfolio dir)
⚠️ The app must be CLOSED before replacing coldstack.exe (Windows locks running EXEs). Tell Kris to close it first.
- Copy `dist/coldstack.exe` → `/mnt/b/OpenClaw/.openclaw/workspace/kimi/portfolios/the-pack-portfolio/coldstack.exe`
- Sync `sidecar/src/` → `/mnt/b/OpenClaw/.openclaw/workspace/kimi/portfolios/the-pack-portfolio/sidecar/src/`
  (node_modules already deployed there; ensure engine.js, networks.js, transfer.js + any new files land)
- Also sync `sidecar/src/` → the USB_DEPLOYMENT sidecar copy
- Verify: `node --check` on the deployed transfer.js/networks.js/engine.js; deployed EXE size matches dist build

### Task 7 — Backup
- Copy active `src/` + `sidecar/src/` + build scripts + `rpc_endpoints.json` → `backups/v5.3.2/`

## Out of scope
No ColdTrack changes, no LP/Solana work, no new features beyond the tasks above.

## Verification checklist (report each)
- [ ] All 4 show_mnemonic sites fixed (+ sweep results)
- [ ] networks.js = 4 chains (ethereum, arbitrum, bsc, polygon)
- [ ] /transfer/unshield returns native ETH for token "ETH" (chained unwrap)
- [ ] Init Engine success response builds without the toString crash
- [ ] py_compile + node --check pass
- [ ] EXE built (v5.3.2 banner) + deployed to portfolio + sidecar synced (portfolio + USB)
- [ ] Commit + push + v5.3.2 release created
- [ ] backups/v5.3.2/ created