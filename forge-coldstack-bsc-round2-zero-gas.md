# Forge Prompt — ColdStack v5.3.28 BSC round 2: zero-gas tx + estimation must gate the broadcast

Progress: G4's ownership pre-check now passes (signer 0xF04A26e3…bCE3Aa ✓, wallet has
0.05698881 BNB ✓). New failure at broadcast:

```
✗ Close error: Agent error (broadcast_tx): intrinsic gas too low: gas 0,
  minimum needed 21896
```

**The signed tx carried `gas=0`.** Either the BSC writer passed a zero gas default,
or the agent's `eth_estimateGas` step failed and the failure was swallowed into a
zero. Trace which, then fix the class:

1. **Trace gas=0 to its source:** bsc_writer's close path → its `broadcast_tx` call —
   does it pass `gas_limit=0` (a parameter default)? Or does the agent's
   estimate-then-buffer block (sign_tx) leave `gas_limit` unset on estimation
   failure? Report the exact line.
2. **Zero-gas can never broadcast:** in the agent, after estimation: if
   `gas_limit` is None/0 (or < the intrinsic minimum ~21,000 + data), ABORT with
   "gas estimation failed — not broadcasting" plus the underlying reason. A failed
   estimate is a pre-flight signal (the tx would likely revert) — surface it, never
   route around it.
3. **Clean the writer defaults:** any `gas_limit: int = 0` style defaults in the
   BSC/Aerodrome writers become `None` (let the agent estimate, then apply the
   existing 20% buffer).
4. **Estimate IS a pre-flight:** if `eth_estimateGas` reverts for a close tx, that
   revert reason must reach the dialog (rotate RPCs if the primary strips revert
   data) — the same honest-classification rule from patch 4's G2 work, now applied to
   EVM estimation.

## Acceptance

1. Unit tests: writer passes None (never 0); estimate-failure → abort with the real
   reason (fixture: RPC that strips revert data → the rotated path still reports);
   estimate-success → 20%-buffered gas in the built tx.
2. Full suite green; pyflakes stays clean (round 1's lint holds).
   `python -m py_compile`. **No EXE build, no push.** STATUS.md v5.3.28: append.

## Report back (brief)

- The zero's origin (exact line) + the guard added.
- Test transcript.