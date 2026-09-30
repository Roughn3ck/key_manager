# Forge Prompt — ColdStack v5.3.28 BSC round 3: RPC rotation for estimate/broadcast

Round 2's guard works (Kris's error: "gas estimation failed — not broadcasting") —
the underlying cause: `eth_estimateGas` hit **HTTP 429 on
`bnb.api.onfinality.io/public`** and there is no fallback endpoint, so one 429 kills
the close. Slater probed the BSC endpoint landscape 2026-09-30 — all of these are
healthy and serve chainId 0x38:

```
https://bsc-dataseed.binance.org/     ✓   https://bsc-dataseed2.binance.org/  ✓
https://bsc-dataseed1.binance.org/   ✓   https://bsc-rpc.publicnode.com      ✓
https://1rpc.io/bnb                  ✓   https://bnb.api.onfinality.io/public ✓ (429s under bursts)
```

## Fixes

1. **Rotation + backoff for the BSC estimate/broadcast path** (the same resilience
   discipline the read paths got in v5.3.21): on 429/403/timeout, wait (linear
   backoff, e.g. 2s × attempt) and rotate to the next endpoint; only when ALL
   endpoints fail does the abort fire — worded "all BSC RPCs failed estimation"
   (never a single-endpoint name unless it truly was the only one tried).
2. **Update the endpoint list**: rpc_endpoints.json (repo + the deployed sync note)
   gains the full healthy list above as the bsc entry's primary + fallbacks; the
   BSC writer/adapter resolves its RPC from that config rather than a single
   hardcoded URL.
3. **Pacing between the close sequence's calls** (ownerOf pre-check → estimate →
   broadcast, per step): a short delay (~1s) between consecutive calls to the same
   endpoint keeps burst-triggered 429s away.
4. Where the writer currently hardcodes onfinality (or any single BSC URL), replace
   with the config-driven rotation.

## Acceptance

1. Unit tests (fixtures): 429 → backoff → rotate → succeed; all endpoints 429 →
   the honest abort naming the count tried; config-driven endpoint resolution.
2. Full suite green; pyflakes holds. `python -m py_compile`.
   **No EXE build, no push.** STATUS.md v5.3.28: append.

## Report back (brief)

- The rotation wiring (where the single endpoint was hardcoded).
- Test transcript.