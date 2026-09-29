# Forge Prompt — ColdStack v5.3.28 patch 3: staker validation by SIMULATION, not logs

Kris retested with patch 2 in the build. New error (patch 2's own wording — the
chain is live):

```
✗ staked position #7088644: staker unknown matches no vault account on this chain — refetch
```

**"Staker unknown" = the stake-transfer `eth_getLogs` lookup returned nothing** (the
public-RPC range limits — same class as the v5.3.24 scan failures), so every
validation that depends on the transfer's `from` starved. The log-based design is
brittle; replace it with one that cannot fail this way:

## Fix — the gauge's own authorization is the oracle

For a gauge-held position, validate candidate signers by **simulating the exact
operation** via `eth_call` from the candidate's derived address:

1. Candidates, in order: (a) the saved-pool binding account, (b) the account context
   passed to the verify call (fetch/selection), (c) all vault accounts derived on
   Base (bounded loop).
2. For each candidate: `eth_call` simulate `gauge.withdraw(tokenId)` (and/or
   `getReward(tokenId)`) with `from` = the candidate's derived address.
   - **No revert → the candidate IS the staker** (the gauge's withdraw checks
     msg.sender == staker — the simulation is the exact authorization the chain
     enforces at broadcast). Proceed with that account.
   - Revert with the auth/not-staker error → next candidate.
   - Revert with a NON-auth error → the candidate passed authorization and hit some
     other precondition (claim-first etc.) — treat as VALIDATED (the true staker)
     and let the guided flow's per-step handling deal with the precondition.
     Pin the actual revert strings from the dromos-labs contract source so the
     auth-vs-other classification is exact, not string-guessed.
3. Only if NO candidate validates: `staked position #<id>: no vault account is
   authorized to act on this position — refetch`. (The word "unknown" disappears:
   we know exactly who is authorized and that none of our accounts is.)
4. Delete the getLogs-based staker lookup from the verify path (keep the transfer
   machinery where it's still needed for discovery — this is only about signer
   resolution). This simulation IS the pre-flight that the writer already does —
   unify them: one simulate-then-broadcast path per operation.

## Acceptance

1. Unit tests (fixtures + stubbed eth_call): candidate order; no-revert → proceeds;
   auth-revert → next; non-auth revert → validated-with-precondition; all-revert →
   the no-account-authorized error. No getLogs anywhere in the signer path.
2. Kris's live rerun on G2: the verify resolves (expect candidate (b) — G2 — to
   simulate clean), the sequence runs claim → unstake → close (~12 AERO, 0.00222
   WETH + 0.00007 cbBTC fees, ~0.03086 cbBTC withdrawn), end-state + ledger
   verified.
3. `python -m py_compile`; full suite green. **No EXE build, no push.**
   STATUS.md v5.3.28: append the patch line.

## Report back (brief)

- The pinned revert strings (auth vs preconditions) from the contract source.
- The candidate loop + simulation wiring (call sites). Test transcript.