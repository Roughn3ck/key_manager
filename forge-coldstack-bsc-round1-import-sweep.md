# Forge Prompt — ColdStack v5.3.28 BSC round 1: missing helper import + lint sweep + executable tests

Kris's G4 close now surfaces properly (the patch-4 error rules work: dialog shows the
real error, live token id #2722883 used) but fails at:

```
✗ Ownership check failed for bsc:2722883: name '_pad_int_to_64' is not defined
```

**Root cause: a missing import.** `_pad_int_to_64` is an adapter-side calldata helper
(`aerodrome_adapter`/`aerodrome_staked` define/import it; the BSC pre-check in
lp_tab.py:~3682 hand-encodes `ownerOf(tokenId)` with it — undefined in lp_tab's
namespace). `py_compile` can't catch NameErrors — this shipped silently.

## Fixes (BSC-focused round — stay on this until G4 closes)

1. **Cleanest layering:** the BSC ADAPTER exposes the ownership read — e.g.
   `bsc_adapter.owner_of(token_id)` → returns (owner, manager) trying BOTH BSC
   managers (Uniswap V3 NFPM + PancakeSwap V3) — and lp_tab's
   `_lp_verify_bsc_position_ownership` CALLS it instead of hand-encoding calldata.
   lp_tab should never build EVM calldata itself (that's how this class of bug
   happens). If a hand-encoded call is ever justified, import the helpers explicitly.
2. **Sweep for the same disease:** run a pyflakes/flake8 (undefined-names) pass over
   lp_tab.py, bsc_adapter.py, bsc_writer.py and fix every undefined-name hit in the
   close/pre-check paths — not just this one. Add the lint to the repo's test
   invocation so it's permanent (py_compile alone is insufficient for runtime names).
3. **Executable tests (the gap that let this ship):** the patch-4 BSC unit tests
   asserted messages without EXECUTING the real function — which is why a NameError
   passed. Rewrite them to call the ACTUAL `_lp_verify_bsc_position_ownership` with
   a stub adapter/writer (assert: owner-match proceeds; mismatch wording; the
   dual-manager resolution). A test that doesn't execute the code path isn't a test.
4. Then stop — the NEXT live run of the G4 close exercises the actual close sequence
   (decrease → collect → burn on the right manager, ledger via the recorder CLI,
   CHAIN='BSC', gas in BNB). We fix what it reveals next round.

## Acceptance

1. `pyflakes src/lp_tab.py src/venue_adapters/bsc_*.py` → zero undefined names.
2. The executable BSC pre-check tests pass (dual-manager fixtures).
3. `python -m py_compile`; full suite green. **No EXE build, no push.**
   STATUS.md v5.3.28: append.

## Report back (brief)

- The adapter-exposed ownership read + the removed hand-encoded calldata.
- The lint findings (any other undefined names fixed).
- The executable test transcript.