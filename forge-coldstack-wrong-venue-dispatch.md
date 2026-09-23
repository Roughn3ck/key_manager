# Forge Prompt — ColdStack v5.3.17: Wrong-venue dispatch in LP close (Aerodrome → HyperEVM) + chain guard

Third round on the Aerodrome G1 close (Pack, EURC/cbBTC Slipstream #75255240). The
v5.3.16 signer work is CONFIRMED GOOD: the error's `[signer 0xAe8E…]` is the actual
signing key's address (from `private_key_to_address(privkey)`), and the owner-anchored
resolution picked the right account. The remaining failure is now pinned:

```
Agent error (broadcast_tx): insufficient funds for gas * price + value:
have 0 want 1897266800000 [signer 0xAe8E5FDb8857736C2218532Fd9D68430aAbAC6ae]
```

## Verified ground truth (Slater, on-chain + code, 2026-09-23)

- 0xAe8E… on **Base: 0.00296 ETH** (verified on base.publicnode.com, 1rpc.io/base,
  mainnet.base.org — funded, ~1500× the needed gas). On BSC 0.0153, Arbitrum 0.0099,
  ETH mainnet 0.00022. On **Optimism and Polygon: exactly 0.**
- **0xAe8E… on HyperEVM (Project X): EXACTLY 0 native** (rpc.hyperliquid.xyz/evm and
  hyperliquid.drpc.org both return 0x0).
- The "want" (1.897e12 wei) fits HyperEVM pricing (~60k gas × ~0.03 gwei) and Base's
  post-2025 pricing — but Base is ruled out by the funded balance. The node that
  rejected was serving a chain where the wallet is 0 → HyperEVM (or Optimism/Polygon).
- All Base-side plumbing is verified clean: AerodromeWriter.__init__ hardcodes
  rpc=BASE_RPC_URL ("https://base.publicnode.com" — valid Base, verified chainId 0x2105),
  the runtime global swap (aerodrome_adapter.py:553) only toggles between two Base
  endpoints, the agent's broadcast_tx broadcasts to the PASSED rpc, and all deployed
  rpc_endpoints.json files (both portfolios + USB_DEPLOYMENT) have correct base entries.
- **The dispatch path** (lp_tab.py `_lp_close_position_dialog`): resolves
  `chain_name, gas_token, venue_key = self._lp_get_chain_info(position.position_id)`
  then `writer = self.gui.lp_engine.get_writer(venue_key, …)` then
  `writer.close_position(position.position_id, account_name)`. The venue is inferred
  FROM THE POSITION ID STRING — with some fallback/default when the id doesn't parse.

**Root cause: the G1 close resolved the wrong venue_key (Project X/HyperEVM) for the
Aerodrome position — the broadcast went to HyperEVM, where the position owner's wallet
has 0 native.** The reads stayed correct because the Aerodrome adapter's reads use
hardcoded Base globals regardless of the writer dispatched.

## Fixes

1. **Dispatch integrity (the actual bug).** Trace `_lp_get_chain_info(position_id)`
   with the G1 record's real position_id and log what it returns. The venue must come
   from the position's saved record (the venue/platform captured at fetch time), never
   from parsing the id string with a silent default. If the venue can't be determined
   from the record: abort with "could not determine venue for <position_id> — refetch
   the position" — NEVER dispatch to a default writer. Before closing, assert the
   resolved writer's chain matches the position's chain field (Base for this
   position); mismatch → abort naming both.
2. **Chain-identity guard in the agent (class-killer).** `sign_tx` and `broadcast_tx`
   already receive BOTH `rpc` and `chain_id`. Add: call `get_chain_id(rpc)` first and
   require it to equal the passed chain_id; mismatch → abort
   `"RPC <url> serves chain <id>, expected <chain_id> — refusing to sign/broadcast"`.
   This turns any future wrong-chain dispatch into a one-glance error. (Same guard in
   the GUI gas pre-check path — pre-check and broadcast must use ONE resolved,
   chain-verified rpc.)
3. **Aerodrome ledger recording.** The close path already has per-venue recording
   stubs (`lp_tab.py:3418` "ledger recording not implemented for venue"). Wire the
   Aerodrome leg per `forge-coldstack-close-recorder.md` + `forge-coldstack-aerodrome-close-signer.md`:
   CloseResult capture (per-tx wallet token-balance deltas → EURC/cbBTC legs + fee legs
   with correct tx-hash attribution; ETH gas per tx from receipts; blockTime; prices at
   close via PriceEngine; EURC is EUR-backed — USD only, no guessed FX), then the atomic
   four-table write via the existing db.py functions (TRANSACTIONS lp_withdraw/yield
   rows; LP_POSITIONS STATUS='closed'+CLOSED_DATE; LP_SNAPSHOTS close row with prices,
   IN_RANGE=0, sigs in NOTES; FEE_EVENTS SOURCE='HARVEST'), LP_POSITIONS matched by
   TOKEN_ID='75255240' (pack db row 7), pending-record fallback on db failure (close
   still reported successful), then the existing ColdTrack auto-export so the sentinel
   hot-reloads.

## Acceptance (NO on-chain actions during dev — the real close is Kris's rerun)

1. `python -m py_compile`; all existing tests green.
2. **Dispatch test:** with the G1 position record, `_lp_close_position_dialog` resolves
   AerodromeWriter + a Base rpc; a record with a missing/ambiguous venue aborts with the
   clear error (stub the writer layer; assert no agent call happens).
3. **Agent guard tests:** rpc serving chain 10 (stub) vs expected 8453 → abort message
   asserted; matching chainId proceeds. The guard must fire in sign_tx AND broadcast_tx.
4. **Recorder tests on a db COPY:** synthetic Aerodrome CloseResult → four tables
   column-exact, idempotent; forced db-lock → pending-record file + retry path; the
   close still reports success.
5. Widget smoke for the abort messages + close-success/recording states.

## Constraints

- **Do NOT build the EXE** — Kris builds. **No git push, no releases.**
- Version bump → v5.3.17 + STATUS.md entry (merge pending bumps if convenient).
- Live DBs read-only during dev; no schema changes; no vault changes; the Pack's live
  Orca cbBTC/SOL position stays read-only.

## Report back

- What `_lp_get_chain_info` returned for the G1 position_id and why (the exact parse
  path / default that produced the wrong venue), with the fix.
- The chain-guard design + test output.
- Aerodrome recorder capture design + per-table write mapping + test transcript.