# Forge Prompt — ColdStack v5.3.28: Staked Aerodrome write operations (unstake · claim · close)

New functionality (distinct from the v5.3.27 tidy-up batch): staked Slipstream
positions get write operations. Test subject: the Pack's G2 position — Kris will run
the live sequence after your build.

## Verified ground truth (Slater, on-chain 2026-09-28)

- Position: ETH/cbBTC Slipstream **#7088644**, NFPM **v2**
  (`0xe1f8cd9AC4e4A65F54f38a5CdAfCA44f6dD68b53`)
- Staked in CLGauge `0x61E0B10423a0009C3f83ab4313813d29437d0817`
  (verified: ownerOf = the gauge; gauge.pool() = `0x42d4a22cad0f5a49681a5715ce994af73a43b76b`)
- Staker/owner wallet: G2's account — derives to `0x40c33B69e7aB4B22Eb8ec7D164e155F769F8c948`
- Position is out of range (close withdraws one-sided — expected).
- AERO emissions accrue to the staker via the gauge; LP swap fees accrue to the
  position normally (NFPM tokensOwed).

## Build

1. **Verify the gauge ABI first** (the onchain-position-decode discipline): from
   `github.com/dromos-labs/metadex-slipstream-public` (Aerodrome's own source) pin the
   exact CLGauge functions + selectors — unstake (`withdraw(tokenId)`), claim
   (`getReward` — its exact signature, e.g. (uint256) vs (uint256, address[])), and
   any approval/allowance requirements — then sanity-verify each selector live
   (eth_call probing where semantics allow) before writing code. No guessed ABIs.
2. **New module** `src/venue_adapters/aerodrome_gauge_writer.py` (standalone per
   Kris's architecture rule; minimal lp_tab wiring):
   - `unstake(token_id)` → gauge.withdraw — signer = the pool's bound account
     (owner-anchored, G2 here); pre-flight **eth_call simulation** of the exact
     calldata from the signer (revert → abort with the revert reason, no broadcast);
     broadcast via the agent; confirm NFT returned (ownerOf == wallet) before
     reporting success.
   - `claim_rewards(token_id)` → gauge.getReward — same pre-flight gate; AERO amount
     from the receipt; record via the close-recorder CLI as a FEE_EVENTS row
     (SOURCE='HARVEST', NOTES "AERO emissions claim — gauge", VALUE_USD via
     PriceEngine, CAD via FX_RATES per the sole-writer contract).
   - **Guided sequence "Close Staked Position"**: claim → unstake → the EXISTING close
     flow (decrease + collect + burn), each step gated on the previous step's
     confirmed state (claim confirmed → unstake confirmed via ownerOf → close via the
     live machinery). Per-step progress in the dialog with tx sigs; the close record
     writes the standard package (CAPITAL_EVENTS etc.).
3. **Card UX:** staked cards gain Unstake / Claim buttons + the guided Close;
   the existing "Staked — unstake required" guard evolves into the guided entry
   point. After unstake (manual or guided), the normal Close works as-is.
4. **Errors:** every step's failure shows the real revert reason (pre-flight or
   broadcast); a mid-sequence failure leaves the position in its actual on-chain
   state with a clear "resume from step N" path (idempotent per step — claim is
   re-runnable, unstake skip-checks ownerOf, close re-runs the standard flow).

## Acceptance

1. NO on-chain actions in tests — eth_call pre-flights and recorded-fixture unit
   tests only: calldata correctness for withdraw/getReward (pinned selectors),
   the sequence state machine (each gate), pre-flight-revert handling, idempotent
   resume paths, recorder row for the claim (db COPY).
2. Live run is Kris's, on G2: claim → unstake → close, verified end-state
   (ownerOf gone, funds in wallet, ledger rows written + auto-export).
3. `python -m py_compile`; full suite green; v5.3.28 + STATUS.md.
   **No EXE build, no push, no release.** Live DBs read-only during dev.

## Report back (brief)

- The pinned gauge ABI (selectors + signatures, where verified from).
- The sequence state machine design + pre-flight transcript (eth_call only).
- Test transcript; files changed (should be the new module + minimal wiring).