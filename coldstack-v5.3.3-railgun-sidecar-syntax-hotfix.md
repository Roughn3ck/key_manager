# ColdStack v5.3.3 Hotfix — Broken engine.js in the v5.3.2 ship (syntax error)

Project: `/mnt/b/Blockchain/coldstack`. SMALL, URGENT, sidecar-only. No EXE rebuild, no GUI changes.
This fixes a broken committed file before anything else rides.

## What happened (evidence, 2026-09-03)

The v5.3.2 commit `14e6dd5` shipped a **syntactically broken** `sidecar/src/routes/engine.js`:

```
$ node --check sidecar/src/routes/engine.js
sidecar/src/routes/engine.js:270
export default router;
^^^^^^
SyntaxError: Unexpected token 'export'
```

Root cause: the new `POST /engine/load-provider` route was inserted at line ~213 **without the
`/engine/status` route's closing `});`** — the status route's `res.json({...});` ends at line ~212,
and the next line is the load-provider comment block. One missing `});` closes the file unparseable.

Impact: any fresh sidecar start crashes instantly — node exits with the SyntaxError on stderr, which the
GUI bridge never captures (`_read_logs` reads stdout only) → user sees only "✗ Sidecar failed to start"
with zero diagnostic output. The v5.3.2 EXE itself is fine; only the sidecar .js file is broken.

It was masked in testing because the sidecar was already running on the pre-sync file when the new
engine.js landed on disk — the broken file only loads on a cold start.

## Tasks

### Task 1 — Fix engine.js (the missing brace)
Restore the `/engine/status` route's closing `});` so the file parses. `node --check
sidecar/src/routes/engine.js` MUST pass. Do NOT touch any other logic.

### Task 2 — Verification gate (this must never happen again)
- Run `node --check` over **every** `sidecar/src/**/*.js` file and list results in the report.
- Add the same `node --check` sweep to the repo's ship checklist (AGENTS.md build/ship section or the
  build script — wherever pre-build verification lives).
- Add a **boot smoke test** to the ship checklist: `node src/server.js` from the sidecar dir, poll
  `http://127.0.0.1:8765/health` until ok (≤30s), then kill. Sidecar ships only if it boots.

### Task 3 — Commit + release
- Commit the brace fix as **v5.3.3** (STATUS.md one-liner: "Hotfix: engine.js syntax error in v5.3.2 —
  /engine/status route closure; sidecar failed to start on fresh boots").
- Push. Create the v5.3.3 release (review existing release formatting; new v tag per protocol).
- Note in the release body that v5.3.2's sidecar file was broken and v5.3.3 is the required fix.

### Task 4 — Redeploy sidecar
- Sync `sidecar/src/` → `/mnt/b/OpenClaw/.openclaw/workspace/kimi/portfolios/the-pack-portfolio/sidecar/src/`
  (Kris's live run location — the app may be RUNNING; only the sidecar restarts, so no EXE lock issue).
- Re-run `node --check` on the deployed copy.

## Out of scope (defer to v5.3.4 train — do NOT do now)
- Bridge `_read_logs` stderr capture (needs EXE rebuild)
- `/engine/load-provider` already-loaded-chain guard
- `STARTUP_TIMEOUT` bump (30s → 90s) + callbacks.js `chain=[object Object]` logging fix

## Verification checklist (report each)
- [ ] `node --check` passes on all sidecar/src .js files (list each)
- [ ] Boot smoke test: sidecar healthy from cold start (report boot seconds)
- [ ] Commit pushed, v5.3.3 release created
- [ ] Portfolio + USB sidecar copies synced and verified