# ColdStack v5.3.7 — Derive Addresses Cleanup + SUI Derivation/Balance Fixes

**Base:** current HEAD `afeca90` (v5.3.6, shipped 2026-09-11). Single-prompt train: this
prompt IS the final prompt — includes docs, build, release, backup.

**Author:** Slater (CTO). **Approved by:** Kris, 2026-09-11 (14:28 request).
**Scope:** Solana derive-dropdown collapse + SUI derivation + SUI balance check. No other chains.

---

## Context — both issues diagnosed with ground truth (2026-09-11)

1. **Derive Addresses dropdown is cluttered.** `SUPPORTED_CHAINS` (derivation_engine.py)
   carries 7 Solana entries: base "SOL (Solana)" (m/44′/501′/0′/0′) plus six wallet-compat
   variants ("— Account 1/2/3" = m/44′/501′/N′/0′, "— Address 1/2" = m/44′/501′/0′/N′).
   Kris wants ONE "SOL (Solana)" entry with the existing Address Index field deriving
   additional addresses.
2. **SUI is double-broken** (Kris: N1 definitely holds SUI but the derived address
   `0x5ec7…89b` reads nothing):
   - **Derivation:** the engine uses hdwallet v3.6.1's built-in "Sui" (BIP44Derivation +
     `h.address()`) which produces a DIFFERENT KEY than the official Sui scheme
     (verified with a test vector — pubkey mismatch, 33-byte flag-prefixed pubkey).
     Same bug class the repo already fixed for Solana with `_derive_solana_slip10`.
   - **Balance:** official `fullnode.mainnet.sui.io` JSON-RPC is DEPRECATED (live-verified:
     "Method not found. JSON-RPC on public fullnodes has been deprecated. Please migrate to
     gRPC or GraphQL"). The parser runs `.get("totalBalance", "0")` on the error response
     (no `result` key) → **silent 0.0**. Even a correct address would read zero today.
   - `0x5ec7…89b` verified EMPTY on-chain (publicnode: coinObjectCount 0) — nothing
     stranded; it's the hdwallet-scheme address. The real SUI sits at the official-scheme
     address (what a Sui wallet app shows for the same mnemonic).

---

## Task 1 — Collapse the Solana dropdown to one entry

- Remove the six variant entries from `SUPPORTED_CHAINS` ("SOL (Solana) — Account 1/2/3",
  "SOL (Solana) — Address 1/2") and the plumbing that only serves them (variant routing
  map, `account_index`/`_solana_address_index` config reads for variants). Keep the base
  "SOL (Solana)" entry.
- **Route the Address Index to the ACCOUNT level:** "SOL (Solana)" + index N →
  **m/44′/501′/N′/0′** (the Phantom/Solflare "Account N" progression). Currently the
  dialog's index maps to the 4th level (m/44′/501′/0′/N′) — change the solana branch of
  `derive_from_mnemonic` so the index drives level 3 and level 4 stays 0.
- The dialog's path preview must update live: show `m/44′/501′/{N}′/0′` with N = current
  Address Index (update on both chain change and index change).
- **Old-variant compatibility via the editable path field:** if the user edits the path to
  exactly `m/44′/501′/X′/Y′` (4 hardened levels), derive with account=X, final=Y. This keeps
  old "Address 1/2"-style addresses reachable without dropdown clutter. Non-matching paths
  produce a clear error, not a wrong key.
- "Derive Another" (index increment) keeps working — now stepping the account level.
- The collapse automatically applies to all three dialogs that share `DERIVATION_CHAINS`
  (derivation dialog, add-private-key derive, custom-mnemonic) and cleans up
  `derive_all_chains`.
- **Compatibility:** previously saved vault addresses are unaffected — each stores its own
  `derivation_path`. No migration.

## Task 2 — Fix SUI derivation (official scheme, custom SLIP-0010)

Add `_derive_sui_slip10` mirroring `_derive_solana_slip10`, and route
`address_type == "sui"` in `derive_from_mnemonic` to it (stop using hdwallet's "Sui"
entirely for derivation; clean-remove the `_CRYPTO_MAP` "Sui" entry if unused elsewhere):

- SLIP-0010 Ed25519, ALL-hardened, **5 levels**: m/44′/784′/0′/0′/{address_index}′ —
  the official Sui path. Address Index drives the LAST level (official wallet progression).
- Address = `"0x" + blake2b-256(pubkey_32bytes + b"\x00")` — Ed25519Pure scheme flag
  **APPENDED**, never prefixed.
- Use the repo's own helpers (`slip10_master_key_from_seed`, `slip10_derive_hardened`,
  `ed25519_privkey_to_pubkey`) — they're field-proven by the Solana path.
- **Do NOT "fix" this by bumping hdwallet** — an unverifiable lib bump risks other chains;
  the custom derivation is deterministic and gated by test vectors.

**Hard gates (must reproduce EXACTLY):**
- Test mnemonic `abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon about`,
  "SUI (Sui)", index 0 → pubkey `900b4d81eecea3df2f74b14200c4f4cf3f49afaca7a634ffd2cf6ff82bdaecf2`,
  address `0x830426b60ffac6adab1503cfe82b6d0bdcce8bf6027d0eedd0941b3b29299e60`.
- Solana regression: "SOL (Solana)", index 0 → `HAgk14JpMQLgt6rVgv7cBQFJWFto5Dqxi472uT3DKpqk`
  (field-verified against Brave — must not drift).

Impact note for the release body: SUI addresses previously derived by ColdStack were
wrong-scheme (verified empty on-chain; nothing stranded). After this fix users must
re-derive SUI and re-save; the new address will match what Sui wallet apps show.

## Task 3 — Fix SUI balance fetching

In `balance_engine.py` (`fetch_sui_balance` / `_fetch_sui_from_url`, ~line 743):

- **Primary endpoint → `https://sui-rpc.publicnode.com`** (live-verified working 2026-09-11;
  returns `totalBalance`, `coinBalance`, `fundsInAddressBalance`). The old official
  `fullnode.mainnet.sui.io` is dead for JSON-RPC — remove it. Configure one fallback
  endpoint and LIVE-VERIFY it during the build (blastapi is shut down and 1rpc.io is
  rate-limited — both verified unusable; pick and prove another, or ship publicnode-only
  with honest errors).
- **Error detection:** if the JSON response contains `"error"`, return None — the caller
  already maps None to a visible "Could not fetch SUI balance". NEVER return a balance
  from an error response.
- **Parser:** prefer `"totalBalance"`, fall back to `"coinBalance"` (schema differs across
  node versions); divide by 1e9 (MIST → SUI). A real zero (`"totalBalance":"0"` with a
  valid result object) is a legitimate 0.0 — show it honestly.
- Log raw RPC errors to the debug log (match the pattern other fetchers use).
- `is_balance_supported` already covers "sui" (verified — the Check Balance button is
  enabled for SUI addresses). No change needed there.

---

## Out of scope (do NOT touch)
- EVM / BTC / DASH derivation (field-proven, vector-tested — do not disturb).
- Mnemonic/vault crypto, key_manager_agent, sidecar, LP/vault tabs, other venues.
- Pool-group ordering, account dropdown ordering (separate concerns).

## Ship checklist
1. Bump `VERSION = "5.3.7"` in `src/gui_main_v5.py`.
2. `python -m py_compile` all changed files.
3. Sidecar gates (protocol; no sidecar changes expected): `node --check` sweep + boot smoke.
4. STATUS.md: new v5.3.7 section. README.md only if it documents derivation chains or balance checks.
5. Build: `python build_gui_v5.py` → dist/ → USB_DEPLOYMENT (script handles backup).
6. Commit as v5.3.7, push, create the v5.3.7 release (review existing release formatting; new v tag;
   note the re-derive-SUI guidance in the release body).
7. Backup src + related active files to `backups/v5.3.7/`.
8. Commit this prompt file (house precedent).
9. Deploy: coordinate with Kris — app may be running (Windows file lock); he closes it, then
   sync the new EXE to `kimi/portfolios/kitandpaul/` (and other live portfolio folders).

## Verification checklist (report each)
- [ ] SOL dropdown shows ONE "SOL (Solana)" in all three dialogs; Derive All Chains shows one SOL
- [ ] SOL index 0 → HAgk14Jp… (regression gate); index 1 derives m/44′/501′/1′/0′; preview
      shows the live path with N; "Derive Another" steps account-level
- [ ] User-edited m/44′/501′/X′/Y′ derives correctly (old "Address N" compat)
- [ ] SUI vector: abandon…about → 0x830426b6… EXACTLY (report pubkey too)
- [ ] SUI balance: funded address returns a balance from publicnode; an RPC error shows
      "Could not fetch SUI balance" — never a silent 0.0; MIST→SUI conversion correct
- [ ] Old saved vault addresses load unchanged (no migration)
- [ ] VERSION 5.3.7; STATUS.md; py_compile clean; EXE builds; release + backup done;
      deploy reported