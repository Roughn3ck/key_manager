# Forge Prompt — ColdStack v5.3.27 #1c: Derive Addresses must refresh the live key session (+ G3 Solana recognition audit)

Kris reproduced a new bug while re-testing G3: after using **Derive Addresses** (SOL
selected), switching to the LP Positions tab showed **ALL saved pools as "Fetch
failed"**. **Locking and unlocking fixed it.** This also retroactively explains the
original G3 symptom: the Solana key WAS in the vault, yet the agent said
"No private keys found for account 'G3'" — a stale live-session key index, not a
missing key.

## Diagnosis (Slater, from the evidence)

Lock/unlock re-initializes the key manager agent session and its in-memory key /
address index. The Derive Addresses flow updates the VAULT but does NOT refresh the
running session's index or the GUI's address caches — so the LP tab's wallet
resolution (agent `get_solana_address` per account+chain, used by the rescan and
binding heal) queries a stale index → every pool fails to resolve its wallet → all
cards "Fetch failed". Relock rebuilds the index → everything works.

## Fixes

1. **Derive Addresses refreshes the live session.** After any derivation completes,
   refresh/invalidate what the LP tab and agent read: the agent's in-memory key index,
   the address_db caches, the GUI account-address maps. Post-derivation, switching to
   the LP tab must resolve all saved pools WITHOUT a lock/unlock cycle. (If the agent
   serves from a thread/session, re-index or re-handshake as part of the derive flow.)
2. **Legacy Solana key recognition audit.** Kris's G3 Solana key existed in the vault
   but `get_solana_address('G3')` returned "No private keys found". Determine why a
   stored Solana key can be invisible to the per-chain lookup: keys stored under an
   older tagging/path scheme (pre-v5.3.10 path handling?) not covered by the current
   index? Fix the index to recognize legacy-stored keys so no one else hits this wall;
   Kris's fresh re-derivation is the live test case.
3. **Precise card errors** (extends #1b): the Orca card's failure text must
   distinguish: key not derived for chain → "account G3 has no derived Solana key —
   derive it"; key present but session stale → should never happen after fix 1;
   address mismatch vs on-chain owner → name both addresses. Never the generic
   "live data unavailable" for any of these.
4. **Verification ground truth** (for the acceptance test): the pack's Orca position:
   NFT mint `GHsXL7LM15Y1XueUK1Rx8PS8CS6RS1qcaKEp9rHdZfam`, position address
   `BcQgfjipcvXTJPK8dfpJxtFCTzpMRHB4fCrUDWng9B5V`. G3's derived SOL address must be
   the on-chain owner of that NFT — the ownerOf-anchored resolution proves it
   end-to-end when the card decodes.

## Acceptance

1. Live read-only: Derive Addresses (any account) → switch to LP tab → all saved
   pools resolve with NO lock/unlock (the G2/derive repro from Kris's session).
2. The Orca cbBTC/SOL card resolves with the fresh G3 key (pair cbBTC/SOL, Staked/
   owner as applicable) — or shows a precise per-fix-3 message if anything mismatches.
3. Unit tests: session-index refresh after derivation (stub agent: derive → lookup
   finds the new key without relock); legacy-key indexing (a key stored under the old
   scheme is found).
4. `python -m py_compile`; full suite green. **No push, no EXE build.**
   STATUS.md v5.3.27 section: append this round's lines.

## Report back (brief)

- What the derive flow failed to refresh + the refresh design.
- The legacy-key-index finding (why G3's old key was invisible).
- Live test transcript (derive → LP tab without relock; Orca card state).