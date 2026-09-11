# ColdStack v5.3.11 — Hotfix: SUI address flag order (official = flag FIRST)

**Base:** HEAD `0f6e363` (v5.3.10). Single-prompt hotfix train — docs, build, release, backup.
**Author:** Slater (CTO). **Approved by:** Kris, 2026-09-11 (16:59 ground-truth experiment).
**Scope:** ONE line of functional code + its comments + vectors. No path logic, no balance,
no other chains. v5.3.10's path parsing is correct and stays.

---

## Context — the bug, its origin, and the proof (all verified 2026-09-11)

ColdStack derives the correct SUI **key** (SLIP-0010 all-hardened `m/44'/784'/0'/0'/0'` —
field-verified) but computes the **address hash with the scheme flag on the wrong side**.

- Current code (`derivation_engine.py` line ~504): `blake2b(pubkey_bytes + b"\x00")` — flag APPENDED.
- **Official Sui (Rust source, `sui-types/src/base_types.rs`):**
  ```rust
  impl From<&PublicKey> for SuiAddress {
      fn from(pk: &PublicKey) -> Self {
          hasher.update([pk.flag()]);   // flag FIRST
          hasher.update(pk);           // then the pubkey
  ```
  → `address = blake2b(flag ‖ pubkey)` — flag **first**.
- **Ground-truth measurement (Kris, 16:59):** importing the public BIP39 test vector
  (`abandon…about`) into his real Suiet wallet previews
  `0x5e93a736d04fbb25737aa40bee40171ef79f65fae833749e3c089fe7cc2161f1` — which is exactly
  the standard key with the **flag-first** hash. Suiet, Mysten SDK, and every working
  mainnet wallet use flag-first. The v5.3.7 spec (my prompt) specified it backwards; the
  v5.3.7 "official vector" was computed by a reference harness with the same mistake
  (self-referential validation). This corrects it.

**Practical impact:** none on keys — the private key ColdStack derived and stored for N1 is
the true controlling key of Suiet's address. Only the displayed address was wrong.

## Task 1 — Fix the flag order (src/derivation_engine.py)

- Line ~504: `digest = hashlib.blake2b(pubkey_bytes + b"\x00", digest_size=32).digest()`
  → `digest = hashlib.blake2b(b"\x00" + pubkey_bytes, digest_size=32).digest()`
- Fix the two comments that state the wrong order (the `_derive_sui_slip10` docstring
  ~line 447 "Ed25519Pure scheme APPENDS…" and the dispatch comment ~line 592): the
  official scheme hashes the flag FIRST: `address = "0x" + blake2b-256(0x00 ‖ pubkey)`.
- Nothing else changes. The path parsing, index routing, and key derivation (SLIP-0010
  chain) from v5.3.7/v5.3.10 are correct and verified — do not touch them.

## Vector gates (must reproduce EXACTLY — flag-first, reference-anchored to the
Suiet measurement + official Rust source; test mnemonic `abandon…about`)

- No path, index 0 → `0x5e93a736d04fbb25737aa40bee40171ef79f65fae833749e3c089fe7cc2161f1`
  (equals the address Suiet itself previews for this phrase — the anchor)
- No path, index 1 → `0xf7c7a39996ac7f1c307b96c96d65cce0855dcc7ccd021c453964f2f62f98e71f`
- Path `m/44'/784'/1'/0'/0'` (Suiet Account 2 form) → `0x082d099250999ab8450a9ef3a962edf9e2449e1045be32ba5a0f2c6117ff7167`
- Path `m/44'/784'/2'/0'/0'` (Suiet Account 3 form) → `0xf195b51c63745071891b1f53170cac2cab2a49da6ee1fe8eabe50989234c8119`
- Explicit path `m/44'/784'/0'/0'/0'` → same as no-path index 0
- Invalid paths still error loudly (v5.3.10 regression: `m/44'/784'/0'/0`, `m/44'/60'/0'/0'/0'`)
- SOL regression unchanged: `HAgk14JpMQLgt6rVgv7cBQFJWFto5Dqxi472uT3DKpqk` (index 0)

**Post-deploy closure (Kris runs with the real N1 phrase; report-only):**
- SUI index 0 → MUST equal `0x04887176a0791ac1837bc654533990820a33f2c289b8636c7066a1865191b314`
  (his Suiet address holding ~1,327.89 SUI)
- SUI path `m/44'/784'/1'/0'/0'` → MUST equal `0x4697d24ae41413865865857cc2abdb0d9591fad383219471af945d381b33064a`
  (his Suiet Account 2 — end-to-end proof on the real wallet)

## Ship checklist

1. `VERSION = "5.3.11"` in `src/gui_main_v5.py`.
2. `python -m py_compile` on `derivation_engine.py`.
3. STATUS.md v5.3.11 entry: state the correction plainly — v5.3.7 fixed hdwallet's broken
   key derivation but specified the address flag on the wrong side; official Sui and all
   wallets hash flag-first; caught via user ground-truth experiment against Suiet + the
   official Rust source.
4. Build: `python build_gui_v5.py` → dist/ → USB_DEPLOYMENT.
5. Commit as v5.3.11, push, GitHub release v5.3.11 (new tag, house convention).
6. `backups/v5.3.11/` (src + related active files).
7. Commit this prompt file (house precedent).
8. Deploy: coordinate with Kris (app-closed EXE swap); sync EXE **and** `rpc_endpoints.json`
   to `kimi/portfolios/kitandpaul/` and `kimi/portfolios/the-pack-portfolio/` (standing rule).

## Verification checklist (report each)

- [ ] All seven vector gates pass exactly (values above)
- [ ] Invalid-path loud errors still work; SOL regression holds
- [ ] Kris's post-deploy closure: N1 SUI idx0 = 0x0488… (then Save to Account; Check Balance
      should show ≈1,327.89 SUI; the earlier saved 0x25d3… row can then be removed — its
      stored key was always the right key, only the address label was wrong)
- [ ] VERSION 5.3.11; STATUS.md; py_compile clean; release + backup; deploy reported

## Out of scope (do NOT touch)

- Key derivation (SLIP-0010 chain), path parsing (v5.3.10), balance engine, RPC config,
  other chains, vault.