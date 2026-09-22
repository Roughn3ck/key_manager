# Forge Prompt — ColdStack v5.3.17: Aerodrome close signer fix + Aerodrome ledger recording

Kris tried to close the Pack's G1 Aerodrome position (EURC/cbBTC Slipstream, Deposit
#75255240) and got:

```
✗ Close error: Wallet has no ETH for gas. Send ETH to your wallet address to pay for
transactions. (Details: Agent error (broadcast_tx): insufficient funds for gas * price +
value: have 0 want 1892191000000)
```

## Verified ground truth (Slater, on-chain, read-only, 2026-09-22 — Base)

- Position #75255240 lives on Slipstream NFPM v1 `0x827922686190790b37229fd06084350E74485b72`
  (v2 `0xe1f8cd9AC4e4A65F54f38a5CdAfCA44f6dD68b53` reverts: nonexistent token).
- **ownerOf(#75255240) = 0xAe8E5FDb8857736C2218532Fd9D68430aAbAC6ae** — the Pack wallet,
  exactly as the GUI shows.
- **0xAe8E… holds 0.00296 ETH on Base** (want was only 0.00000189 ETH — ~1500× covered).
  It also holds dust on ETH mainnet (0.0000138); the K&P EVM address holds 0.0008 on Base.
- The node's error says the broadcasting account had **exactly 0** — so the signer was
  NONE of the above: the close flow signed from a different, unfunded derived address.

**Diagnosis: signer-resolution bug** — the close dialog's `account` → agent key derivation
produced an address that is NOT the position owner. Either the wrong vault account was
passed, or the EVM/Base derivation path for the right account disagrees with the address
the GUI displays (the v5.3.10 "path is authoritative" lesson — check per-account custom
paths leaking across chains: Project X/HyperEVM paths applied to Base, or index/branch
mismatch).

## Fix 1 — Owner-anchored signer resolution (all EVM venue closes)

1. Before building any close tx: read the position's on-chain owner (`ownerOf(tokenId)`
   on the right NFPM / venue equivalent). Resolve the signer by matching the VAULT
   account whose derived address FOR THE POSITION'S CHAIN equals the owner — never a
   default/selected/first account.
2. If no vault account derives to the owner address: abort with a precise error naming
   the position owner and the addresses the vault CAN derive on that chain ("position
   owner 0xAe8E… matches no account in this vault for Base") — never fall back.
3. Gas pre-check (and the "Wallet has no ETH for gas" message) must query the RESOLVED
   signer on the position's chain, and the message must name the resolved address.
4. Every `broadcast_tx` agent error must echo the derived signer address — add it to the
   error payload — so this class of bug is diagnosable in one glance.
5. Trace + fix the actual root cause in the current flow: how the Aerodrome close dialog
   picks `account`, and how the agent derives the Base address for it. The Orca close
   signed correctly (K&P Solana wallet) — compare its path.

## Fix 2 — Aerodrome ledger recording (extends the v5.3.16 close recorder)

Implement the Aerodrome leg of `forge-coldstack-close-recorder.md` (read it first — same
contract, atomicity, locking discipline, pending-record fallback, auto-export):

- **CloseResult capture (EVM flavor):** per-tx snapshots of the wallet's ETH +
  per-token balances (balanceOf) around each tx → per-tx deltas give returned legs +
  fee legs with correct tx-hash attribution; gas per tx from the receipt
  (gasUsed × effectiveGasPrice, ETH-denominated, Base); blockTime from the receipt;
  token prices at close via PriceEngine. Note: EURC is EUR-backed — record USD values
  from PriceEngine where available; do NOT guess CAD/EUR/AUD (Kimi's FX pipeline
  backfills — G1 entry values are already in her db).
- **Writes (one sqlite transaction, existing db.py functions):**
  - TRANSACTIONS: `lp_withdraw` row per returned leg (EURC, cbBTC) + `yield` rows for
    collected fees, CATEGORY 'lp'/'yield', CHAIN 'Base'; TX_HASH = the signature of the
    tx that MOVED those tokens (Slipstream collect/decrease tx); the NFT-burn tx hash
    only in LP_SNAPSHOTS.NOTES. FEE_ASSET/FEE_AMOUNT/FEE_USD = that row's tx gas (ETH).
  - LP_POSITIONS: STATUS='closed', CLOSED_DATE = close blockTime (UTC ISO). Match the
    row by TOKEN_ID = '75255240' (pack db LP_POSITIONS row 7, EURC/cbBTC, opened
    2026-08-18, entry $1,978.84). Ambiguous/missing → pending file, never fabricate.
  - LP_SNAPSHOTS: close row — TOKEN_A_AMOUNT/TOKEN_B_AMOUNT = returned amounts,
    TOKEN_A/B_PRICE_USD at close, TOTAL_VALUE_USD, IN_RANGE=0, NOTES = all sigs +
    "CLOSED via ColdStack" (upsert on UNIQUE(LP_POSITION_ID, REPORT_DATE)).
  - FEE_EVENTS: dated rows, SOURCE='HARVEST' (CHECK constraint stands), TX_HASH=collect sig.
- **Auto-export** after a successful record (the existing ColdTrack export) → the
  sentinel hot-reloads. Close → ledger → view → sentinel.

If the v5.3.16 recorder core isn't built yet when you pick this up, build the core per
that prompt's architecture section and wire both Orca and Aerodrome through it.

## Acceptance (all must pass — NO on-chain actions during dev)

1. `python -m py_compile`; all existing tests green.
2. **Signer-resolution integration check (read-only):** for position #75255240, the
   resolved signer == 0xAe8E5FDb8857736C2218532Fd9D68430aAbAC6ae, and the gas check
   returns its real Base balance (~0.003 ETH), not 0. No broadcast — the real close is
   Kris's rerun with the built exe.
3. Unit tests on a COPY of the pack portfolio db: synthetic Aerodrome CloseResult
   (EURC + cbBTC legs, fee legs, ETH gas, blockTime) → all four tables written
   column-exact + idempotent; no-match → pending file with the right error.
4. Widget smoke: close dialog + the new resolved-signer gas message + pending-record
   retry states render.
5. The Pack's live Orca cbBTC/SOL position stays untouched (read-only reference).

## Constraints

- **Do NOT build the EXE** — Kris builds. **No git push, no releases.**
- Version bump → v5.3.17 + STATUS.md (coordinate with any pending v5.3.15/16 bumps — one
  combined release line is fine).
- Live DBs read-only during dev; no schema changes; no key-vault changes.

## Report back

- The actual root cause of the wrong signer (which account/path the flow used, with the
  derived address that broadcast).
- The owner-anchored resolution design + the #75255240 check output.
- Aerodrome recorder: capture design, per-table write mapping, test transcript.