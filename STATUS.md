# ColdStack - Status Report

**Project:** https://github.com/Roughn3ck/key_manager
**Current Version:** v5.3.19 (LP panel refresh + account labels)
**Last Updated:** 2026-09-26

---

## v5.3.19 - LP panel refresh + account labels (2026-09-26)
Fetch-single now refreshes every saved pool via its own bound venue + account (per-pool isolation, no cross-marking, no lock/unlock); pool cards show the bound account (`(unbound)` fallback); legacy bindings self-heal at render via vault lookup (on-chain `ownerOf` stays in the pre-flight path). Tests made hermetic: recorder fixtures reset in DB copies; live-RPC Orca e2e gated behind `COLDSATCK_E2E_RPC=1`.

---

## v5.3.18 - Saved-pool account binding + Aerodrome close loop (2026-09-23)

### Summary
This release finishes the G1 Aerodrome close loop. The end-to-end close had already been verified on-chain; the remaining work was making ColdStack's own ledger write actually happen, binding saved pools to their owning vault account so the top-bar selector cannot accidentally use the wrong signer, and cleaning up the empty NFT husk.

### Section A — Saved-pool account binding

A saved pool now stores its owning vault account (`account_name`, `account_address`, `account_chain`) when it is fetched/saved. Position actions (close, collect, compound, rebalance, refresh) resolve the account from the **record's binding**; the top-bar Account selector now governs only new fetches/opens.

- `save_pool()` in `src/saved_pools.py` accepts and stores `account_name`/`account_chain`.
- `_lp_resolve_wallet_for_position()` in `src/lp_tab.py` now tries the binding first, then falls back to wallet_address / address entry / top-bar selector.
- Legacy records without a binding self-heal: `_lp_verify_evm_position_ownership()` runs the owner-anchored resolution and writes the correct account back into the saved-pool record.
- Two distinct fatal pre-flight errors (no more generic "stale record"):
  - `position owner 0x… matches no vault account on this chain — refetch`
  - `record is bound to account X (0x…) but ownerOf(N) = Y — refetch`

### Section B — Aerodrome close now writes to the database

**Root cause of the missed write:** `AerodromeWriter._post_close_state()` was called with the wrong argument order since v5.3.16: `recipient, pre0, pre1, token0, token1, dec0, dec1` instead of `recipient, token0, token1, dec0, dec1`. The float balances were treated as token addresses, the method raised, `last_close_result` was set to `None`, and the GUI recorder hook returned early without touching `coldtrack.db`.

**Fix:** corrected the call site in `src/venue_adapters/aerodrome_writer.py`. The recorder now runs and writes the four-table close record:
- `TRANSACTIONS` — `lp_withdraw` rows for liquidity legs, `yield` rows for fee legs; `CHAIN='Base'`; gas in `FEE_ASSET`/`FEE_AMOUNT`.
- `LP_POSITIONS` — `STATUS='closed'`, `CLOSED_DATE`; row 7 is matched by `PLATFORM + POOL_NAME + STATUS='active'` and its `TOKEN_ID` is corrected from the stale `74933503` to the live `75255240` (noted in `NOTES`).
- `LP_SNAPSHOTS` — close row with amounts/prices/`TOTAL_VALUE_USD`, `IN_RANGE=0`, sigs in `NOTES`.
- `FEE_EVENTS` — `SOURCE='HARVEST'`.

If the row is already closed, the recorder appends the close sigs to `NOTES` only and never duplicates close data.

### Section C — Burn the empty NFT

After decrease+collect, `AerodromeWriter.close_position()` reads back `liquidity` and `tokensOwed0/1`. If all are zero, it calls `burn(tokenId)` on the SlipStream PositionManager. The burn hash is appended to the returned tx list for the UI and recorded in `NOTES`/snapshot gas only — it never appears as a withdraw or yield leg. Burn failure is non-fatal; funds-out is still reported successful with a clear "NFT burn failed — retry" state.

### Section D — Post-close cleanup

Card removal is gated on the position being empty. For Aerodrome that means either the burn confirmed or, if burn is unavailable, a `liquidity == 0` read-back.

### Tests + Files
- `src/saved_pools.py` — account binding fields + `update_saved_pool_binding()`.
- `src/lp_tab.py` — binding capture, binding-first resolution, self-heal, refined error messages, burn note in success UI.
- `src/venue_adapters/aerodrome_writer.py` — corrected `_post_close_state` call site, `_get_position_state`, `burn()` flow, `last_burn_sig`.
- `src/coldtrack/close_recorder.py` — already-closed row NOTES-only append path.
- `src/gui_main_v5.py` — `VERSION = "5.3.18"`.
- `build_gui_v5.py` — version string updated to v5.3.18.
- `README.md` — latest release link updated to v5.3.18.
- Tests: `test_saved_pool_binding.py` (NEW), `test_aerodrome_burn_leg.py` (NEW), updated `test_close_recorder_aerodrome.py` (already-closed NOTES-only path).

### Release
- EXE built: `USB_DEPLOYMENT/coldstack.exe`.
- GitHub release: v5.3.18 with `coldstack.exe` asset.

---

## v5.3.17 - LP wrong-venue dispatch fix + chain-identity guard + stale-id recorder sync (source-only train, merged into v5.3.18 release)

### Summary
The Aerodrome G1 Pack close (EURC/cbBTC Slipstream) was being dispatched to the Project X/HyperEVM writer because `lp_tab.py` resolved the venue from the `position_id` string with a silent HyperEVM fallback. The broadcast then went to HyperEVM, where the owner's wallet has 0 native, producing the misleading "insufficient funds" error. This release removes the silent default and adds redundant chain-identity guards so any residual wrong-chain dispatch aborts with a one-glance error.

### Section A — Dispatch integrity (lp_tab.py)

**`LPTab._lp_resolve_venue_for_position(position)`** is now the single dispatch resolver. The venue is taken from the saved position record (`position.venue` / `position.chain`) first; the `position_id` prefix is only a secondary signal. A missing or ambiguous venue aborts with `could not determine venue for <position_id> — refetch the position` and NEVER falls back to a default writer. `_lp_get_chain_info` is deprecated for dispatch; it now returns `("Unknown", "???", "")` for unrecognized prefixes instead of the old `("HyperEVM", "HYPE", "hyperliquid")` silent default.

**Fatal pre-flight NFT ownership check.** `_lp_verify_evm_position_ownership(position, account_name, venue_key)` runs for every EVM close/collect/compound before the confirmation dialog. It checks `ownerOf(tokenId)` on all venue position managers (both Aerodrome SlipStream NFPMs for Base) and requires the live NFT to be owned by the vault-derived signer. On failure it aborts with `stale record — refetch this position: ...` and does not sign anything. This catches a stale id (e.g. the dead `74933503` in the Pack row) before a close is attempted.

**Writer/chain mismatch guard.** Before `writer.close_position()` is called, the resolved writer's `chain_id` is compared to the expected id for the venue (`aerodrome=8453`, `hyperliquid=999`, `bsc=56`). A mismatch aborts naming both ids and asks the user to refetch.

### Section B — Chain-identity guard in key_manager_agent.py

`KeyManagerAgent` now has `_verify_rpc_chain(rpc, chain_id)`. `sign_tx` calls it when both `rpc` and `chain_id` are supplied; `broadcast_tx` re-verifies right before sending the signed tx. Mismatch raises `RPC <url> serves chain <id>, expected <chain_id> — refusing to sign/broadcast`. This is the class-killer guard that makes any future wrong-chain RPC a one-glance diagnosis.

### Section C — Writer-side chain guard (venue_writer.py + EVM writers)

`VenueWriter._verify_rpc_chain(rpc, expected_chain_id)` is a shared helper that makes a direct `eth_chainId` call and raises on mismatch. `AerodromeWriter._broadcast`, `HyperliquidWriter._broadcast`, and `BSCWriter._broadcast` call it both before the gas-balance read and before the agent broadcast. The gas-balance RPC and the broadcast RPC are therefore verified to be the same chain.

### Section D — Recorder stale-id sync (close_recorder.py)

`CloseRecorder._match_position` now tolerates a stale `TOKEN_ID` in the saved record. If exact `TOKEN_ID` lookup misses, it falls back to a unique active match on `PLATFORM + POOL_NAME` (or `PLATFORM + CHAIN` if needed). On a unique fallback match, the row's `TOKEN_ID` is corrected to the live NFT id and the old value is noted in `NOTES` (`identifier sync: TOKEN_ID <old> -> <new>`). Ambiguous fallback matches still go to the pending file; the on-chain close remains reported successful. This makes the Pack G1 row (currently carrying dead id `74933503`) closable with the live id `75255240`.

### Section E — cbBTC address correction

`CBBTC_BASE` in `src/venue_adapters/aerodrome_adapter.py` and the `cbBTC` entries in `src/balance_engine.py` were using the dead registry address `0xcbB45146687557Fd9B6F8cB1E2D51a65F3B1D1c1`. Verified on-chain `symbol()` showed the live cbBTC token at `0xcbb7c0000aB88B473b1f5afd9ef808440eed33bf`. All three occurrences have been corrected to the live address so recorder leg symbols, pool reads, and balance lookups use the real token.

### Tests + Files
- `src/lp_tab.py` — `_lp_resolve_venue_for_position`, `_lp_chain_gas_for_venue`, `_lp_verify_evm_position_ownership`, updated `_lp_close_position_dialog` / `_lp_collect_fees_dialog` / `_lp_compound_fees_dialog`, writer chain guard in `_do_close`.
- `src/key_manager_agent.py` — `_verify_rpc_chain`, guard calls in `sign_tx` + `broadcast_tx`.
- `src/venue_adapters/venue_writer.py` — shared `_verify_rpc_chain`.
- `src/venue_adapters/aerodrome_writer.py`, `hyperliquid_writer.py`, `bsc_writer.py` — writer-side chain guard calls.
- `src/coldtrack/close_recorder.py` — fallback platform/pool match + TOKEN_ID sync.
- `src/venue_adapters/aerodrome_adapter.py`, `src/balance_engine.py` — corrected `CBBTC_BASE` / cbBTC addresses.
- `src/gui_main_v5.py` — `VERSION = "5.3.17"`.
- Tests: `test_lp_dispatch_guard.py` (NEW), `test_agent_chain_guard.py` (NEW), `test_writer_chain_guard.py` (NEW), updated `test_close_recorder_aerodrome.py` to exercise the stale-id sync path.

### Acceptance
- `python -m py_compile` on all touched files.
- All existing tests green plus the three new tests.
- No EXE build, no git push, no release — source-only.

---

## v5.3.16 - LP Fetch Minor Fixes + Close-Position Ledger Recorder + Aerodrome close signer fix (UNRELEASED — Kris builds)

### Section A0 — Aerodrome close signer fix (owner-anchored signer resolution)

**Summary.** The Aerodrome close failed with "Wallet has no ETH for gas" even though the Pack wallet `0xAe8E5FDb8857736C2218532Fd9D68430aAbAC6ae` holds ~0.003 ETH on Base — the node's error said the broadcasting account had **exactly 0**, so the signer was a different, unfunded derived address. Root cause: the close dialog passed the GUI-selected `account` to the writer, and when the vault had multiple EVM accounts the agent's key selection could derive a non-owner key. **Fix:** owner-anchored signer resolution — `_resolve_owner_signer(token_id, position_manager)` reads `ownerOf(tokenId)` on the Slipstream NFPM and matches the vault account whose derivable EVM address equals the owner; on no-match it aborts with a precise error naming the owner and the derivable Base set (never falls back). If the dialog-resolved account disagrees with the owner-anchored one, the close uses the owner-anchored account. Gas pre-check and the error message query/name the resolved signer. Every agent broadcast error now echoes the derived signer address (`[signer 0x…]`) so this bug class is diagnosable in one glance. Verified read-only: `ownerOf(#75255240)` = `0xAe8E…AC6ae` (the Pack wallet) on NFPM v1 `0x8279226…` — live `ownerOf` call confirms the anchor; the resolver maps it to the right account on stub and real vault-shaped data.

### Section A — Close-Position Ledger Recorder (auto-record closes to coldtrack.db)

**Summary.** A successful GUI close now writes the ledger automatically (policy change; ColdStack becomes a db writer for closes, alongside Kimi's tooling). On a confirmed close the completion path captures a `CloseResult` and the recorder writes all four tables in ONE `BEGIN IMMEDIATE` (busy_timeout 5s) sqlite transaction — no network inside the txn — then auto-exports `strategy_view.json` so the sentinel hot-reloads (~20s).

**CloseResult fields** (captured by the writer post-close, read-only): `position_mint` (the LP_POSITIONS match key), `platform`, `chain`, `legs[]` (asset/amount/value_usd/kind/'liquidity'|'fee'/sig), `close_sig`/`collect_sig`/`decrease_sig`/`reward_sigs[]`, `block_time_iso`, `gas{}` (per-tx SOL), `token_price_usd{}`, `final_amounts{}`. Serializable to JSON for retry.

**Per-table write mapping** (column-exact, mirroring KP db rows #68–72; USD-only, FX/CAD/EUR/AUD NULL — Kimi's FX backfills):
- `TRANSACTIONS` — one row per leg: `TYPE='lp_withdraw'`/`CATEGORY='lp'` for liquidity legs, `TYPE='yield'`/`CATEGORY='yield'` for fee legs, `CHAIN='Solana'`, `TX_HASH=<the sig that moved those tokens>` (decrease sig for liquidity; collect sig for fees — the burn sig NEVER appears as a tx row), `FEE_ASSET='SOL'`/`FEE_AMOUNT=<that tx's gas>`.
- `LP_POSITIONS` — `STATUS='closed'`, `CLOSED_DATE=<close-tx blockTime UTC ISO>`.
- `LP_SNAPSHOTS` — one close row (upsert, `UNIQUE(LP_POSITION_ID,REPORT_DATE)`): final returned amounts + close prices, `IN_RANGE=0`, `NOTES` = all three sigs + "CLOSED via ColdStack".
- `FEE_EVENTS` — dated row(s), `TX_HASH=<collect sig>`, `SOURCE='HARVEST'` (the CHECK stands — never altered), `NOTES="CLOSED via ColdStack"`.

**Matching + safety.** The recorder matches `LP_POSITIONS` by `TOKEN_ID = position mint`; a missing or ambiguous match NEVER fabricates entry data — it writes a pending record (`coldstack_pending_records/pending_close_<mint>_<ts>.json`) and surfaces a retry path. On a DB write failure the on-chain close is still reported successful ("close succeeded; ledger write failed — saved to coldstack_pending_records/"); `retry_pending()` re-applies later. **Venue scope:** Orca end-to-end (tested, three-tx flow with reward legs). **Aerodrome (EVM)** end-to-end (decrease + collect; legs attributed via per-receipt ERC20 Transfer deltas to `recipient`, gas from receipts as ETH, blockTime from the receipt block, prices via PriceEngine). BSC / Project X log the explicit stub ("ledger recording not implemented for this venue yet") rather than silently skipping. Aerodrome test: `test_close_recorder_aerodrome.py` (atomic write + idempotent + no-match→pending on a pack-db copy).

#### Section A — Tests + Files
- `test_close_recorder.py` — atomic 4-table write column-exact, idempotent snapshot upsert, pending/retry on lock + unknown mint (no fabrication), all against a COPY of the K&P db (live DBs read-only): **PASS**.
- `test_close_recorder_integration.py` — completion hook invokes recorder + auto-export; forced `database is locked` → close still reported success + pending file persisted next to the portfolio db: **PASS**.
- `test_close_recorder_widget.py` — success / skipped / pending note states: **PASS**.
- Files: `src/coldtrack/close_recorder.py` (NEW), `src/coldtrack/db.py` (`begin_immediate`/`commit`/`rollback`/`conn`), `src/venue_adapters/orca_writer.py` (close capture → `last_close_result`), `src/lp_tab.py` (record hook + portfolio db resolver + venue stub), `build_gui_v5.py` (hidden import), `STATUS.md`.

### Section B — LP fetch UX fixes (same version, carried from 2026-09-22 fetch work)

### Summary
Three post-close LP fetch UX bugs plus a bonus closed-position state. Root cause of the saved-pool "Fetch failed" regression: the Solana READ path had **no retry/backoff** (`_solana_rpc_call` was single-shot and its fallback endpoints projectserum/rpc.ankr are deprecated/403), so under the public-RPC 429 storm every saved pool's adapter returned nothing and the placeholder renderer marked them all "Fetch failed". The write path (close, collect) was already resilient — unaffected and untouched.

### Fixed
- **Read-path RPC resilience** — `_solana_rpc_call_resilient` (retry/backoff + endpoint rotation, 429-aware); `_get_account_data` uses it; endpoints rotated to `api.mainnet-beta` / `solana-rpc.publicnode` / `solana.drpc` (projectserum + rpc.ankr dropped). New `_account_exists` distinguishes RPC-down from a real not-found.
- **Closed vs failed** — `_fetch_by_position_mint`/`_fetch_by_position_address`/`_fetch_position_at_address` now return a distinct `error="Position closed"` when the position PDA is gone (or empty), or "Solana RPC unavailable — retry" on exhausted retries. Saved-pool placeholders render a calm **"✔ Position closed — nothing left on-chain. You can remove this entry."** state with a Remove affordance, instead of "Fetch failed".
- **Platform-selector leak removed / saved-pool refresh isolation** — placeholder rendering is per-saved-pool only (one `card` per fetched-missed entry): a fetch-single can no longer poison other saved pools, and each saved pool's closed-check routes to its own venue's logic (`_lp_saved_pool_is_closed` — Orca uses the PDA/account-gone test; other venues pass through unchanged).
- **Pair naming** — new `orca_display_pair(symbol_a, symbol_b)` (Orca convention: quote-style asset last — SOL last vs a non-stable, USDC last vs SOL, else account order). Applied at all Orca label construction sites (position fetch + pool-state fetch); the saved-pool label picks it up via the saved `pair`.

### Verification (2026-09-22)
- Live read-only checks (no broadcast): the live Pack Orca cbBTC/SOL position fetched by **position account address** (account `GMpFkUb…` decodes to a position with liquidity 2,762,882,930) → displays **cbBTC/SOL**, no error. The now-closed K&P position returns `error="Position closed"` by mint AND by address. `_account_exists` confirms the closed position PDA is gone.
- New `test_lp_fetch_fixes.py`: naming-helper truth table (7 cases), closed/RPC-unavailable distinction, and venue-scoped refresh dispatch (Project X + Aerodrome saved pools render only their own placeholder in a stub frame — no cross-platform status mutation). **PASS**.
- Regression: `test_sentinel_export.py`, `test_coldtrack_tab_widgets.py`, `test_orca_close_layout.py` — all **PASS**; write path (close/collect) untested-but-untouched and its layout/sim tests still green. `python -m py_compile` all touched files — **PASS**.
- Per Kris: **no EXE build, no git push, no release** — source-only, ready for @kris to build + review.

### Files Changed
- `src/venue_adapters/orca_adapter.py` — resilient read RPC (`_solana_rpc_call_resilient`), `_account_exists`, `orca_display_pair`, closed/RPC-unavailable distinction in the three fetch entry points, endpoint rotation update
- `src/lp_tab.py` — `_lp_render_saved_placeholder` (closed ↔ failed) + `_lp_saved_pool_is_closed` (venue-scoped); both placeholder sites use it
- `src/gui_main_v5.py` — VERSION 5.3.16
- `test_lp_fetch_fixes.py` — NEW

---

## v5.3.15 - Sentinel Export Port Hotfix: emit fields + KP entry-amount ordering (2026-09-22)

### Summary
Three targeted fixes to `src/coldtrack/sentinel_export.py`, ported exactly from Kimi's corrected reference (`kimi/coldtax/export_strategy_view.py` — the live verified view). No schema changes; the events contract is unchanged.

### Fixed
1. **Emit `nfpm`** from the KP position config blob (NOTES JSON), non-null-only — Project X needs it for the `positions(tokenId)` call; without it the sentinel's monitor errors that position.
2. **Emit `liquidity`** from the config blob, non-null-only — Aerodrome (Slipstream) needs it for config-frozen position math.
3. **KP entry-amount ordering** — the port emitted inverted entries for positions whose DB row is quote-first but the canonical monitor order is inverted (e.g. Orca cbBTC/SOL vs canonical SOL/cbBTC). Amounts now follow the canonical token0/token1 order via an alias-tolerant `_same_token` match (WHYPE/HYPE, WETH/ETH wrap aliases compare equal so they don't false-trigger the swap). Verified live: Orca (swap fires → token0_amt = the SOL amount), Aerodrome (`WETH`/`ETH` alias → no swap), Project X (`WHYPE`/`HYPE` → no swap).

### Verification (2026-09-22)
- `test_sentinel_export.py` — new fixture block: (a) inverted-token position (assert the swap fires), (b) wrap-alias WETH/ETH position (assert NO false swap), (c) Project X position with `nfpm` + `liquidity` in the config blob (assert both emit), plus `_same_token` alias semantics: **PASS**.
- Field-for-field parity vs Kimi's reference script on the same three fixtures: **PASS** (zero diffs).
- Regression: `test_coldtrack_tab_widgets.py`, `test_orca_close_layout.py` — **PASS**; `python -m py_compile` touched files — **PASS**.

### Files Changed
- `src/coldtrack/sentinel_export.py` — `_same_token` helper; KP loader emits `nfpm`/`liquidity`; entry-amount canonical ordering (`EXPORTER_VERSION` 5.3.15)
- `src/gui_main_v5.py` — VERSION 5.3.15
- `test_sentinel_export.py` — emit-fix fixtures + `_same_token` assertions

---

## v5.3.14 - Orca Close Position (Token-2022): `IllegalOwner` + `ClosePositionNotEmpty` (6005) (2026-09-21)

### Summary
Two back-to-back close failures on an Orca Whirlpool with a Token-2022 position NFT (Orca's current UI mints position NFTs as Token-2022 with metadata extension). Fixed both; the read-only mainnet simulation of the full close against the live position `F98SmN…` now returns **err: null** end-to-end (`BurnChecked` + `CloseAccount` ×2 complete).

**(1) `IllegalOwner`** — `_detect_token_program()` silently fell back to the classic SPL program on any RPC failure; under a 429 storm the builder emitted the legacy `close_position` (classic disc + classic `TokenkegQ…`) against a Token-2022-owned mint. **Fix:** removed the silent fallback — retry detection across the configured RPCs with backoff; abort with a clear error ("could not determine token program for <mint> — retry") instead of guessing. The close/collect account layouts already matched the whirlpools IDL — no layout change was needed, only the detection.

**(2) `ClosePositionNotEmpty` (6005)** — `Position::is_position_empty` requires `liquidity==0` AND `fee_owed_a/b==0` AND **every `reward_infos[i].amount_owed==0`**. The writer never collected rewards, so a leftover reward would block close even after fees drain. **Fix:** added a `collect_reward` step to `close_position()` for each initialized reward with `amount_owed > 0` (runs after collectFees, before closePosition), and extended the Position pool decoders to parse `reward_infos` so owed rewards are visible. The pool decoders also now expose reward mint/vault per slot.

### Pinned IDL structs (orca-so/whirlpools IDL, anchor spec 0.1.0, program `whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc`)
- `close_position` — disc `7b86510031446262`; 6 accts `[position_authority(sig), receiver(mut), position(mut), position_mint(mut), position_token_account(mut), token_program=TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA]`.
- `close_position_with_token_extensions` — disc `01b6873b9b1963df`; SAME 6 accts in the same order; acct 5 = `token_2022_program=TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb`. Both variant builders' layouts match the IDL exactly.
- `collect_fees` — disc `a498cf631eba13b6`; 9 accts; `position_token_account` is a token-interface account (Token-2022 NFT accepted); `token_program` classic (both pool legs classic SPL). No args.
- `collect_reward` (NEW builder) — disc = `sha256("global:collect_reward")[:8]`; 8 accts `[whirlpool(mut), position_authority(sig), position(mut), position_token_account, reward_owner_account(mut), reward_vault(mut), token_program]`; data = disc(8) + `reward_index(u8)`.
- `decrease_liquidity` — disc `a026d06f685b2c01`; 11 accts; unchanged (live-verified control).

### Authoritative 216-byte Position layout (state/position.rs, `LEN = 8+136+72`)
`disc(8) | whirlpool(32) | position_mint(32) | liquidity u128@72 | tick_lower i32@88 | tick_upper i32@92 | fee_growth_checkpoint_a u128@96 | fee_owed_a u64@112 | fee_growth_checkpoint_b u128@120 | fee_owed_b u64@128 | reward_infos[3]@136..216` — each `PositionRewardInfo { growth_inside_checkpoint u128, amount_owed u64 }` (24 B). `_decode_position_data` now parses `reward_infos` and exposes `is_position_empty`.

### GUI error path
- `key_manager_agent._format_solana_rpc_error` — the broadcast error now surfaces the full simulation `logs` array line-by-line (the anchor "thrown at" / account-index lines that anchor programs emit on revert), instead of a one-line dict repr that the copyable error textbox elided. Applied to both `broadcast_solana_tx` and `broadcast_raw_solana_tx`.

### Verification (2026-09-21)
- `test_orca_close_reproduce.py` (new) — full close-TE account list + 216-byte position hexdump + COMPLETE sim logs against live `F98SmN…`: **err: null** (18387 CU; BurnChecked → CloseAccount → CloseAccount all success).
- `test_orca_close_simulate.py` — collect_fees err **null**; close(TE) succeeds or (when collect was only simulated against a stale owed-fee snapshot) reaches only `ClosePositionNotEmpty` — never IllegalOwner / AccountOwnedByWrongProgram.
- `test_orca_close_layout.py` — golden-layout for classic close, Token-2022 close, collect_fees, **collect_reward**, plus a 216-byte 2.x position-bytes fixture asserting field offsets and `is_position_empty` on a nonzero reward: **PASS**.
- Regression: `test_sentinel_export.py`, `test_coldtrack_tab_widgets.py` — **PASS**. `python -m py_compile` on all touched files: **PASS** (the `key_manager_agent.py` `\)` SyntaxWarning is a pre-existing docstring artifact, untouched by this change).
- Per Kris: **no EXE build, no git push, no release** — source-only, ready for @kris to build + review.

### Files Changed
- `src/venue_adapters/orca_adapter.py` — token-program detection hardened (no silent classic fallback); `_decode_position_data` 2.x reward_infos + `is_position_empty`; `_decode_pool_data` reward_infos (mint/vault/authority)
- `src/venue_adapters/orca_writer.py` — `collect_reward` builder (disc/accounts/data + ATA bundling); `close_position` adds a per-reward collect step; pinned-layout docstrings; resilient `_get_position_token_account`
- `src/key_manager_agent.py` — `_format_solana_rpc_error` surfaces full sim logs (both Solana broadcast paths)
- `src/gui_main_v5.py` — VERSION 5.3.14
- `test_orca_close_layout.py`, `test_orca_close_simulate.py`, `test_orca_close_reproduce.py` — NEW
- `STATUS.md` — this entry

---

## v5.3.13 - ColdTrack Sentinel Export Bridge (2026-09-21)

### Summary
coldtrack.db becomes the pack's single system of record. The ColdTrack tab's **"Export Sentinel View"** now ports **Kimi's reference exporter** (`kimi/coldtax/export_strategy_view.py`) into ColdStack's embedded Python runtime — materializing the merged `strategy_view.json` (Pack G1–G6 + N1 plus K&P positions) that the Argus Sentinel already consumes (`loadStrategyData()` + `enrichKP()` are shipped and smoke-tested sentinel-side). This supersedes the v5.3.12 rev, which had invented a `SENTINEL_POOLS` registry — the v2 rev of the Forge prompt reconciled the design with Kimi's handover: the registry seam is **`LP_POSITIONS` + `POOL_GROUPS`**, and events live in **`FEE_EVENTS` / `CAPITAL_EVENTS`**. Kimi's tool remains the reference implementation; this is an adaptation inside ColdStack, credited to her.

### Changes (net vs. pre-sentinel baseline)
- **`src/coldtrack/sentinel_export.py`** — `SentinelExporter` (ported logic preserved):
  - Merges multiple coldtrack DBs (`--db PATH` repeatable / `;`-separated, `$ARGUS_DB_PATH` env fallback, defaults = the Pack + K&P portfolio DBs) into one view.
  - **Contract-fix outputs** (per the Forge prompt): `fee_events[]`/`capital_events[]` in **snake_case with `position_id` as the sentinel pool-id STRING** ('G2', 'KP1') — lights up the events pipeline; KP positions carry **`entry: {usd, date, token0_amt, token1_amt, fees_claimed_usd}`** — lights up K&P Net P&L; `view_version: 1` (integer); `pool_groups[]` passthrough; reserved keys `view_version, generated_by, generated_at, source_db, fee_events, capital_events, pool_groups`.
  - Pool records byte-compatible with today's strategy.json shape (G1 11/11 parity, Kimi's bar).
  - Atomic write tmp + `os.replace`; graceful empties (no readable DBs → header + empty arrays, never throws). Stdlib only (sqlite3/json/os/argparse/datetime) — runs inside the EXE; also usable as `coldtrack export` / module `main`.
- **`src/coldtrack/db.py`** — schema aligned to **Kimi's v3.0 as-built** (additive, idempotent): adds `FEE_EVENTS`, `CAPITAL_EVENTS`, `POOL_GROUPS` tables and the missing `LP_POSITIONS` identifier columns (`POSITION_ID_TYPE`, `TOKEN_ID`, `POOL_ADDRESS`, `POSITION_ADDRESS`, `TOTAL_VALUE_AUD_ENTRY`) via create-if-missing / add-column-if-missing migration; CRUD helpers for events + pool groups. **No v3.0 tables modified.** (The earlier `SENTINEL_POOLS` table is removed — dead per prompt rev 2.)
- **ColdTrack tab** — "Export Sentinel View" button (threaded) + target-path entry + status line (last export time, path, pool/KP/group/event counts). The button drives the ported exporter (no vault access; works locked).

### Verification (2026-09-21)
- `test_sentinel_export.py` (rewritten) — two temp DBs (Pack-style G2 pool + `FEE_EVENTS` row + position-tagged `CAPITAL_EVENTS` row + portfolio-level one; kitandpaul KP position with entry) → export → assert merged contract shape (pool record parity, snake_case events with string `position_id`, KP `entry`, `pool_groups` passthrough, reserved keys, atomic write, graceful-empty): **PASS**.
- Ran the port against Kimi's **real** Pack + K&P DBs: produced 7 Pack pools, 3 active K&P positions, `fee_events` with `position_id: 'G2'`, KP `entry` blocks — matching the sentinel's expected contract end-to-end.
- `test_coldtrack_tab_widgets.py` — headless widget-construction smoke (v5.3.8 lesson): **PASS**.
- `python -m py_compile` all touched files: **PASS**.

### Files Changed
- `src/coldtrack/sentinel_export.py` — rewritten (port of `kimi/coldtax/export_strategy_view.py`)
- `src/coldtrack/db.py` — v3.0-as-built seam (FEE_EVENTS / CAPITAL_EVENTS / POOL_GROUPS + LP_POSITIONS cols), SENTINEL_POOLS removed
- `src/coldtrack/tab.py` — export button drives the ported exporter
- `src/gui_main_v5.py` — VERSION 5.3.13
- `build_gui_v5.py`, `coldstack.spec` — hidden import `coldtrack.sentinel_export`
- `test_sentinel_export.py` — rewritten to Kimi-schema fixtures; `test_coldtrack_tab_widgets.py` — unchanged

---

## v5.3.11 - Hotfix: SUI Address Flag Order (2026-09-11)

### Summary
Corrects the SUI address hash flag order: official Sui (and every working mainnet wallet — verified against Suiet live + the official Rust source) hashes the scheme flag FIRST: `address = blake2b(0x00 || pubkey)`. v5.3.7 fixed hdwallet's broken key derivation but specified the flag on the wrong side of the hash input; the v5.3.7 "official vector" came from a reference harness with the same mistake (self-referential validation). Caught via a user ground-truth experiment: importing the public BIP39 test vector into a real Suiet wallet previews `0x5e93a736…`, which is exactly the standard key hashed flag-first.

### Fixed
- `_derive_sui_slip10`: `blake2b(pubkey + flag)` → `blake2b(flag || pubkey)` — one line of functional code + comment corrections. Nothing else changed: v5.3.10 path parsing, index routing, and the SLIP-0010 key derivation chain are verified correct and untouched.
- **Practical impact: none on keys** — the private key ColdStack derived and stored is the true controlling key of Suiet's address; only the displayed address was wrong. Users should re-derive and re-save SUI rows saved under the old (flag-appended) address labels.

### Verification (2026-09-11)
- Vector gates ALL PASS (flag-first): idx0 `0x5e93a736…61f1` (the Suiet-measured anchor), idx1 `0xf7c7a399…8e71f`, Suiet acct-2 `0x082d0992…7167`, Suiet acct-3 `0xf195b51c…c8119`, explicit-default == default, invalid paths still error loudly, SOL regression `HAgk14Jp…` intact.
- Dialog smoke (v5.3.8 gate): builds fully, 4 buttons, no exception — PASS.

### Files Changed
- `src/derivation_engine.py` — flag order fix + comments
- `src/gui_main_v5.py` — VERSION 5.3.11

---

## v5.3.10 - SUI Custom-Path Parsing (Suiet Account-Level Derivation) (2026-09-11)

### Summary
The SUI derivation path field is now authoritative for derivation. Previously `_derive_sui_slip10` ignored a custom path (display-only), making Suiet account-level addresses (m/44'/784'/K'/0'/0') unreachable — the engine derives the official account-0 of the held phrase, but Suiet's multi-account progression walks the ACCOUNT level.

### Fixed
- **SUI custom path authoritative** — `_parse_sui_path` accepts exactly `m/44'/784'/A'/B'/I'` (5 hardened levels, regex-strict) and derives those levels faithfully (A=account, B=change flag, I=index — Suiet's template). Invalid paths raise a clear `ValueError` — never a silent fallback to the default (a typo'd path silently deriving the default address was the bug class being killed). Result dict `path` reflects the actually-derived path.
- **Dialog hint** — path tip now covers both chains: SOL index→account level + legacy path note; SUI index→final level + Suiet account-K path guidance.
- Default flow unchanged: no path / default template → `m/44'/784'/0'/0'/{index}'`.

### Verification (2026-09-11)
- Vector gates ALL PASS: idx0 `0x830426b6…` (unchanged), idx1 `0x238c6885…` (unchanged), Suiet acct-2 path `0x170eb76c…`, Suiet acct-3 path `0xced35759…`, explicit-default-path == default, three invalid forms raise (4-level / wrong-coin / unhardened), SOL regression `HAgk14Jp…` intact.
- Dialog smoke (v5.3.8 gate): builds fully, 4 buttons, no exception — PASS.
- `do_derive`-pattern flow checks: SUI preview-path idx2 → `0x84fe0338…d4639` (= no-path idx2, consistent); edited Suiet account-2 path → `0x170eb76c…` — both PASS.

### Files Changed
- `src/derivation_engine.py` — `_parse_sui_path` + authoritative path handling in `_derive_sui_slip10`
- `src/account_dialogs.py` — path hint text
- `src/gui_main_v5.py` — VERSION 5.3.10

---

## v5.3.9 - Stale rpc_endpoints.json Self-Heal + Config Sync (2026-09-11)

### Summary
Fixes the "Could not fetch SUI balance" regression cause after v5.3.7's code-level endpoint fix: stale deployed `rpc_endpoints.json` files silently shadowed corrected code defaults. Adds a loader self-heal for deprecated endpoints, corrects the repo-root JSON, and makes config sync an explicit deploy step.

### Root cause (verified live 2026-09-11)
v5.3.7 fixed SUI endpoints in code (`rpc_config.DEFAULT_ENDPOINTS`, `balance_engine.SUI_RPC`) but not in `rpc_endpoints.json`. `load_rpc_config` prefers a user file next to the EXE over everything else, so the-pack-portfolio's Sep-3 JSON (sui → deprecated `fullnode.mainnet.sui.io`) silently shadowed the fix; v5.3.7's error detection correctly returned None → "Could not fetch SUI balance". The repo-root JSON was still stale too.

### Fixed
- **Repo `rpc_endpoints.json`** — sui → publicnode primary + blockpi fallback; `updated` → 2026-09-11; drift diff vs `DEFAULT_ENDPOINTS` run: also fixed a `bsc` trailing-slash mismatch (now NO DRIFT).
- **Loader self-heal** — `DEPRECATED_ENDPOINTS` map (one evidence-based entry: the dead official Sui fullnode → publicnode). `_selfheal_deprecated_endpoints` sweeps every chosen config's url+fallback fields, substitutes replacements, warns, and rewrites file-based configs in place (only affected entries; `updated` bumped; never crashes — rewrite failure keeps the in-memory fix). Applies to user, bundled, and defaults paths.
- **Deploy sync lesson** — "sync rpc_endpoints.json on every deploy" added to AGENTS.md; deployed folders now get EXE + JSON together.

### Verification (2026-09-11)
- Fixture gate: stale file → repaired in memory + rewritten on disk (other entries untouched, version preserved, `updated` bumped); clean file → byte-identical, no rewrite; defaults path → in-memory heal — ALL PASS
- Live fetch smoke: `fetch_sui_balance(0x0488…b314)` → **1327.885611506 SUI** (non-None float; matches ~1327.89 on-chain)
- `py_compile` clean; EXE built

### Files Changed
- `src/rpc_config.py` — DEPRECATED_ENDPOINTS + `_selfheal_deprecated_endpoints`
- `rpc_endpoints.json` — sui/bsc corrections + updated 2026-09-11
- `src/gui_main_v5.py` — VERSION 5.3.9
- `AGENTS.md` — deploy note: sync rpc_endpoints.json on every deploy

---

## v5.3.8 - Hotfix: Derive Addresses Dialog Widget Order (2026-09-11)

### Summary
Hotfix for a v5.3.7 regression: the Derive Addresses dialog opened half-built (no Address Index field, no results area, no buttons) due to a widget-creation-order NameError that tkinter silently swallowed in the frozen EXE.

### Fixed
- **Dialog half-build** — `update_path()` was invoked at dialog-build time before `index_entry` existed (`_read_index()` reads it), raising `NameError: free variable 'index_entry' referenced before assignment` and aborting `show_derivation_dialog` mid-build. Fix: one move — the Address Index widget block (label + entry + insert + pack + the two live-preview bindings) now sits immediately after `path_hint.pack(...)`, BEFORE `_read_index`/`update_path` definitions and their first invocation. No logic changes.

### Process
- New **runtime smoke gate**: py_compile cannot catch widget-order bugs, so dialog-construction is now exercised headlessly before ship (stub gui → `show_derivation_dialog` → assert no exception, ≥4 buttons, index label present, path populated). This run: SMOKE PASS (buttons: Derive / Save to Account / Derive Another / Close; path `m/44'/60'/0'/0/0`).
- Sanity scan: the same read-before-create pattern does NOT exist in the other derive dialogs (add-private-key derive / custom-mnemonic — their index entries are created before any direct call site). No changes there.
- All six derivation vectors re-run unchanged (SUI official, SOL base/dialog/legacy, SUI dialog flow, EVM idx0/idx1) — engine untouched.

### Files Changed
- `src/account_dialogs.py` — widget-order move only
- `src/gui_main_v5.py` — VERSION 5.3.8

---

## v5.3.7 - Derive Addresses Cleanup + SUI Derivation/Balance Fixes (2026-09-11)

### Summary
Collapses the Solana derive dropdown to one entry (Address Index now drives the account level), fixes SUI derivation to the official scheme (hdwallet's built-in Sui produced a different key — verified with test vectors), and fixes SUI balance fetching (the official JSON-RPC endpoint is deprecated; the parser silently returned 0.0 from error responses).

### Fixed
- **SOL dropdown collapse** — `SUPPORTED_CHAINS` and `CHAIN_OPTIONS` reduced from 7 Solana entries to one "SOL (Solana)". The derive dialog's Address Index now drives the ACCOUNT level (m/44'/501'/{N}'/0' — the Phantom/Solflare progression) instead of the 4th level. "Derive Another" steps the account level.
- **Live path preview** — the derive dialog's path preview updates on chain change AND index change: `m/44'/501'/{N}'/0'` for SOL, `m/44'/784'/0'/0'/{N}'` for SUI.
- **Legacy SOL compat** — an edited path of exactly `m/44'/501'/X'/Y'` (4 hardened levels) derives with account=X, final=Y (old "Address N"-style derivation reachable without dropdown clutter). Non-matching paths raise a clear error, never a wrong key.
- **SUI derivation (official scheme)** — `_derive_sui_slip10`: SLIP-0010 Ed25519 all-hardened, 5 levels (m/44'/784'/0'/0'/{index}'), address = `0x + blake2b-256(pubkey + 0x00-flag-APPENDED)`. hdwallet's "Sui" removed from `_CRYPTO_MAP` entirely. Hard gates reproduce EXACTLY: abandon×11+about → pubkey `900b4d81…aecf2`, address `0x830426b6…99e60`. SOL regression gate intact: index 0 → `HAgk14JpMQLgt6rVgv7cBQFJWFto5Dqxi472uT3DKpqk`.
- **SUI balance fetching** — primary endpoint → `https://sui-rpc.publicnode.com` (live-verified), fallback → `https://sui.blockpi.network/v1/rpc/public` (live-verified). The deprecated `fullnode.mainnet.sui.io` removed. JSON-RPC "error" in any response → None (visible "Could not fetch SUI balance", never a silent 0.0); raw RPC errors logged. Parser prefers `totalBalance`, falls back to `coinBalance`, ÷1e9 MIST→SUI. A legitimate zero shows honestly.
- `is_balance_supported` already covered "sui" — unchanged.

### Impact / compatibility
- **SUI addresses previously derived by ColdStack were wrong-scheme** (verified empty on-chain — nothing stranded). After updating, users must re-derive SUI and re-save; the new address will match what Sui wallet apps show.
- Previously saved SOL vault addresses are unaffected — each stores its own `derivation_path`; no migration.

### Files Changed
- `src/derivation_engine.py` — SOL variant removal, `_parse_solana_path` + `_derive_solana_slip10_levels`, `_derive_sui_slip10`, sui routing, `_CRYPTO_MAP` cleanup
- `src/chain_options.py` — CHAIN_OPTIONS Solana variants removed
- `src/account_dialogs.py` — live path preview (chain + index change), SOL tip text
- `src/balance_engine.py` + `src/rpc_config.py` — SUI endpoints + error-detecting parser
- `src/gui_main_v5.py` — VERSION 5.3.7

---

## v5.3.6 - Wallet Screen Account Reordering (2026-09-11)

### Summary
Accounts on the Wallet screen are now reorderable within their pool group via ▲/▼ buttons on each account row. Order persists in the encrypted vault (the pool's `accounts` list IS the display order, so no schema change).

### New Features
- **Account reorder within pools** — each account row in the Wallet screen left panel is now `[account button][▲][▼]`; one click = one position swap = one immediate vault save + panel refresh. ▲ disabled on the first account, ▼ disabled on the last (no wraparound, no cross-pool moves; edge guard is defense-in-depth).
- Selection survives a reorder: `current_account`/`current_pool` keep the right-panel title and view; only the left panel re-renders.
- Unassigned section unchanged (rendered `sorted()`, no reorder buttons — out of scope by design).
- Account dropdowns elsewhere (LP tab, vault tab) remain alphabetical — untouched.

### Implementation
- `refresh_left_panel` refactored: pool accounts render as row frames (transparent) instead of directly-packed buttons; account button style/behavior unchanged (220×35, border, `select_account`).
- `_move_account_in_pool(pool, account, direction)` helper: index swap ±1 with edge guard, `current_password` guard, `save_encrypted_data` → `refresh_left_panel`.
- Verified headless: swap semantics (incl. edge no-ops + repeated clicks), vault round-trip persistence after reorder, panel renders ▲/▼ with correct disabled states, helper moves + re-render live.

### Files Changed
- `src/gui_main_v5.py` — row frames + ▲/▼ buttons, `_move_account_in_pool`, VERSION 5.3.6

---

## v5.3.5 - Orca Valuation + Fee Math (2026-09-10)

### Summary
Fixes two Orca valuation bugs verified live against mainnet ground truth (K&P SOL/cbBTC position, cross-checked with Orca's UI to the dollar): unlisted mints silently priced at $0 (a $1,305 cbBTC leg vanished), and pending fees charged for ALL pool trading while the position was out of range (~70x overestimate: $630.91 shown vs $8.91 real).

### Fixed
- **Unlisted mints silently price at $0** — cbBTC added to `SOLANA_TOKENS` (SOL/cbBTC pair now renders with both legs priced); `_canonical_symbol` is now case-insensitive on miss so mixed-case symbols like "cbBTC" route through price_engine's PEGGED map to BTC. Verified: F98SmN… value $402 → **$1,714** (Orca UI ~$1,712, within 0.2%).
- **Jupiter mint-price fallback (general)** — any unlisted mint now prices via the Jupiter lite API by MINT (`lite-api.jup.ag/price/v3`, public, no key), 5s timeout, 60s module-level cache. A leg that even Jupiter cannot price shows the raw amount with an "⚠ unpriced" marker on the card — never a silent $0.
- **Pending fees charged out-of-range trading (~70x)** — `_compute_fees_owed` now computes feeGrowthInside via Whirlpool tick arrays (port of the HyperEVM adapter's V3 pattern): reads fee_growth_outside for both boundary ticks from the TickArray PDA (`[b"tick_array", whirlpool, start_index]` string seeds, 88 ticks/array, 113-byte entries; layout verified empirically against mainnet) and applies the V3 inside-growth rules mod 2^128. Verified: F98SmN… fees $630.98 → **$8.92** (Orca UI $8.91; split 0.0433 SOL + 0.0000575 cbBTC). Applied to BOTH call sites — position fetch and `fetch_fees_earned` (live fee refresh + `_record_fees`), so vault/ColdTrack fee records are corrected too.
- **Graceful degradation** — missing/unreadable tick arrays (or uninitialized boundary ticks) fall back to the old global approximation flagged "⚠ fees estimated"; never crashes the scan.

### Verification (live gates, 2026-09-10)
- F98SmN… (SOL/cbBTC): fees **$8.92** vs Orca UI $8.91 — PASS (was $630.98)
- F98SmN…: value **$1,714.02** vs Orca UI ~$1,712 — PASS (was $402.32)
- `fetch_fees_earned` path returns the same corrected split
- Missing tick-array pool → estimate fallback flagged, no crash
- Unpriceable mint → None → "⚠ unpriced" marker with raw amount
- Jupiter mint-price fallback live-verified (cbBTC $78.3k), cached

### Files Changed
- `src/venue_adapters/orca_adapter.py` — cbBTC mint + canonical mapping, Jupiter mint-price fallback + unpriced markers, tick-array feeGrowthInside fee math
- `src/gui_main_v5.py` — VERSION 5.3.5

---

## v5.3.4 - Orca Scan Routing + Close Confirmation (2026-09-10)

### Summary
LP tab close flows now verify on-chain confirmation before claiming success or removing cards, Orca scans resolve the wallet's Solana address instead of silently returning zero results for EVM addresses, and closed saved pools no longer resurrect on scans. Carries over the four EXE-rebuild items deferred from v5.3.3.

### Fixed
- **EVM close confirmation (HyperEVM / BSC / Base)** — close no longer fire-and-forget: each TX receipt is waited on (120s, revert-aware), then business-level closure is verified (on-chain liquidity == 0) before the card is removed. Reverted/unconfirmed TXs keep the card. Removed the blind `time.sleep(3)` + refetch.
- **Solana close confirmation (Orca)** — after close, the GUI polls up to 10s until the position PDA account is gone (closePosition burns the NFT); card removal is gated on that check.
- **Orca scan routing** — filtered Orca scans resolve the account's Solana address (Account mode) or show a visible skip message; a 0x address passed to OrcaAdapter silently returned `[]`. Full scans append an "Orca skipped (no Solana address for account)" note to the status line when applicable.
- **Closed-position resurrection filter** — saved-pool merge (`_lp_do_full_scan`, `_lp_do_filtered_full_scan`) and the saved-only fast path now skip closed positions (liquidity == 0; Orca also requires zero uncollected fees) when "Include closed" is unchecked. Bookmarks are only deleted on confirmed close, never by scans.
- **Sidecar stderr capture** — `railgun_bridge._read_logs` now pumps stderr as well as stdout, so sidecar crash output is visible in the GUI log (v5.3.3 lesson).
- **STARTUP_TIMEOUT 30s → 90s** — the sidecar needed 27s on the dev machine at boot; 30s was too tight for slower disks/cold artifact caches.
- **`/engine/load-provider` guard** — retrying an already-loaded healthy chain now returns `alreadyLoaded: true` instead of tearing down listeners and re-syncing.
- **callbacks.js `chain=[object Object]`** — already fixed via `chain?.toString?.()`; verified during this train (no change needed).

### Process
- Added `_get_position_liquidity` to `bsc_writer` / `aerodrome_writer` (parity with hyperliquid_writer) for post-close liquidity verification.
- `_lp_remove_pool` refactored onto the shared `_lp_forget_position` helper (same behavior, one implementation); close flows use the same helper for card removal + bookmark cleanup.

### Files Changed
- `src/lp_tab.py` — close confirmation (EVM + Solana), Orca scan routing, resurrection filter, `_lp_forget_position` helper
- `src/venue_adapters/bsc_writer.py`, `src/venue_adapters/aerodrome_writer.py` — `_get_position_liquidity`
- `src/railgun_bridge.py` — stderr capture, STARTUP_TIMEOUT 90s
- `sidecar/src/routes/engine.js` — already-loaded provider guard
- `src/gui_main_v5.py` — VERSION 5.3.4

---

## v5.3.3 - Railgun Sidecar Syntax Hotfix (2026-09-03)

### Summary
Hotfix for a broken `sidecar/src/routes/engine.js` shipped in v5.3.2. The new `POST /engine/load-provider` route was inserted without the preceding `/engine/status` route's closing `});`, making the file syntactically invalid. Sidecar crashed on cold start with a SyntaxError that the GUI never surfaced.

### Fixed
- **`engine.js` syntax error** — restored the missing `});` closing the `/engine/status` route handler. `node --check` now passes on all 14 sidecar JS files.

### Process
- Added pre-ship sidecar gate to AGENTS.md: `node --check` on every `sidecar/src/**/*.js`, plus cold-start boot smoke test (`node src/server.js` → health OK ≤30s). Sidecar ships only if it boots cleanly.

---

## v5.3.2 - Railgun Transactions (September 2026)

### Summary
v5.3.2 enables full Railgun transactions (shield, unshield, private transfer) with a complete round-trip for native ETH. Critical fixes for a show-stopping `show_mnemonic` crash, dead RPC endpoints, and a 6-chain Railgun overclaim (SDK 7.6.1 supports only 4 chains).

### Fixed
- **`show_mnemonic` crash** — 4 call sites used `self.gui.show_mnemonic()` (doesn't exist on `ColdStackGUI`); fixed to `self.gui.key_manager.show_mnemonic()` — every Railgun transaction path was silently crashing before showing any message
- **RPC endpoint refresh** — replaced dead/gated public RPC endpoints (llamarpc, ankr, polygon-rpc) with working public ones (`publicnode.com`, `drpc.org`); Ethereum and Polygon Railgun providers now load
- **Engine init toString crash** — `SUPPORTED_RAILGUN_NETWORKS` included undefined `Base`/`Optimism` entries (SDK 7.6.1); removed both, engine init no longer crashes on success
- **Chain reality fix** — Railgun trimmed to 4 chains (Ethereum, Arbitrum, BSC, Polygon); Base/Optimism commented out pending SDK upgrade (wallet>10.4.0 / shared-models>7.6.1)

### New Features
- **Railgun Shield (public → private)** — dialog wraps native ETH (deposit → WETH) then shields via Railgun proxy; supports both native ETH and standard ERC-20s
- **Railgun Unshield-to-Native (private → native ETH)** — chains unshield → auto-withdraw (WETH → ETH) in one flow; plain WETH unshield unchanged
- **Railgun Private Transfer (0zk → 0zk)** — encrypted memo, show-sender toggle, full confirmation summary with human-readable amount + base-unit verification
- **Railgun provider status display** — per-chain ✓/✗ with error text; "Reload Providers" button retries failed chains without engine restart
- **Token info endpoint** (`GET /transfer/token-info`) — symbol + decimals for any ERC-20; native ETH returns wrapped-token info
- **Balance labels** — known wrapped-native tokens (WETH/WBNB/WMATIC) display by symbol instead of truncated address
- **Wrapped-native addresses** — canonical WETH/WBNB/WMATIC for Ethereum, Arbitrum, BSC, Polygon; verified on-chain

### v5.3.2 Bundled Work (from previous uncommitted prompts)
- **RPC endpoint refresh** (`rpc_endpoints.json`, `balance_engine.py`, `rpc_config.py`) — all 5 EVM chain defaults replaced with verified live endpoints
- **Per-mint Orca token program fix** — closePosition ATA creation uses per-mint `token_prog_a`/`token_prog_b` (position NFT vs pool tokens)
- **Railgun closure bug fixes** — error notification lambda capture (`{e}` → `msg`) prevents silent `NameError` crashes on all 4 Railgun error paths
- **Add Private Key scroll + custom mnemonic derive** — dialog scrolls when content overflows; custom mnemonic derive stores key in `custom_derived_meta` instead of reading disabled entry

### Files Added
- `src/railgun_tx_dialogs.py` — Shield/Unshield/Private Transfer dialogs (native ETH support, threaded submit, copyable results)
- `src/ed25519_utils.py` — shared Ed25519 math primitives, base58, SLIP-0010 derivation

### Files Changed
- `src/gui_main_v5.py` — VERSION bump to 5.3.2
- `src/railgun_tab.py` — 4 show_mnemonic fixes; chain lists to 4; balance labels; per-chain provider status; Reload Providers button; hardcoded 6-chain text fixed
- `src/railgun_tx_dialogs.py` — RAILGUN_CHAINS to 4; ETH as valid token in ShieldDialog; UnshieldDialog native unwrap checkbox + 2-tx status
- `src/railgun_bridge.py` — transfer methods rewritten to match sidecar contract; reload_provider(); LONG_TIMEOUT for proofs
- `sidecar/src/routes/engine.js` — `POST /engine/load-provider` (single-chain provider retry); init skips unknown chains gracefully
- `sidecar/src/routes/transfer.js` — native ETH shield (wrap + shield); unshield-to-native (unshield + auto-unwrap); token-info native support
- `sidecar/src/networks.js` — trimmed to 4 chains; WRAPPED_NATIVE map with 4 live entries
- `src/balance_engine.py` + `src/rpc_config.py` + `rpc_endpoints.json` — endpoint refresh
- `build_gui_v5.py` — railgun_tx_dialogs hidden-import; launch banner v5.3.2
- `README.md` — latest-release link
- `STATUS.md` — this entry
- `AGENTS.md` — version refs

### Verification
- `python -m py_compile` passes on all modified files
- Solana test mnemonic produces `HAgk14JpMQLgt6rVgv7cBQFJWFto5Dqxi472uT3DKpqk` ✅
- Engine init no longer crashes on Base/Optimism entries
- Sidecar endpoints verified: `/transfer/token-info?chain=arbitrum&tokenAddress=ETH` returns `{native:true, symbol:"WETH", decimals:18}`
- EXE builds as Windows PE32+ (156.32 MB)
- Railgun live test: shield ETH on Arbitrum → confirm Spendable; private transfer → unshield to native ETH

---

### Summary
Hotfix for Orca Whirlpool close position. Positions opened via Orca's current UI mint Token-2022 position NFTs (with metadata extension); the legacy `closePosition` instruction types `position_mint` as SPL Token-only and rejects them with `AccountOwnedByWrongProgram` (3007). The Orca writer now selects the dedicated `closePositionWithTokenExtensions` instruction when the position NFT belongs to Token-2022 — identical account layout, Token-2022 token program required; the close burns the NFT and closes both the token account and the mint account (rent reclaimed).

### Fixed
- **Orca close position on Token-2022 NFTs** — discriminator `01b6873b9b1963df` selected when the position mint's owning program is Token-2022; SPL positions continue on legacy `closePosition` (`7b86510031446262`). The decrease-liquidity and collectFees steps were already Token-2022-compatible; only the close instruction changed.
- Root-cause evidence and instruction details: `coldstack-orca-token2022-close-instruction-fix.md`

### Files Changed
- `src/venue_adapters/orca_writer.py` — `DISC_CLOSE_POSITION_TE` constant + conditional discriminator in `_build_close_position_ix`
- `src/gui_main_v5.py` — VERSION bump to 5.3.1
- `AGENTS.md` — production version refs updated; ColdTrack roadmap phase tags shifted (this hotfix took the v5.3.1 slot)
- `README.md` — latest-release link updated to v5.3.1
- `STATUS.md` — this entry

### Verification
- `python -m py_compile` passes for all modified files
- EXE rebuild is a Windows PE32+ build; GUI header shows v5.3.1
- Orca fetch and SPL-token positions unaffected; ColdTrack and all other tabs present
- Live G3 Token-2022 position closed cleanly (NFT burned, token account + mint closed, rent reclaimed)

---

## v5.3.0 - ColdTrack Foundation (August 2026)

### Summary
v5.3.0 introduces ColdTrack as a new module and tab in ColdStack. ColdTrack is the financial ledger layer — the monetization layer of the ColdStack ecosystem. This release establishes the foundation: SQLite database, account bridge from vault, and the ColdTrack tab UI. It also bundles all v5.2.6 hotfixes (Solana derivation, Token-2022, base58 display, etc.) that were never separately released.

### New Features
- **ColdTrack Module** — New `src/coldtrack/` subpackage
  - SQLite database layer (`coldtrack.db`) with 9-table schema (v3.0)
  - Schema: PORTFOLIOS, ACCOUNTS, FX_RATES, TRANSACTIONS, LP_POSITIONS, LP_SNAPSHOTS, VAULT_DEPOSITS, HOLDINGS, TAGS + TRANSACTION_TAGS
  - Account bridge: user-initiated sync from vault → ColdTrack DB
  - ColdTrack tab in CTkTabview: portfolio overview + account list
  - Controlled disclosure: only public identifiers cross the bridge (no private keys)
- **Multi-currency schema** — USD, CAD, AUD, EUR values stored per transaction/snapshot
- **Multi-portfolio schema** — PORTFOLIOS as ownership root (Executive Mind, Kit & Paul)

### v5.2.6 Hotfixes (bundled)
- **Solana derivation fix** — SLIP-0010 all-hardened 4-level derivation (`m/44'/501'/0'/0'`) replacing wrong BIP44 5-level
- **Base58 private key display** — Solana keys shown in 64-byte keypair base58 format (Brave/Phantom compatible)
- **Solana sendTransaction encoding** — explicit `{"encoding": "base64"}` fixes invalid base58 errors
- **Token ID type fix** — EVM token_id string→int conversion for saved pools
- **Orca Token-2022 support** — per-mint token program detection (position NFT vs pool tokens)
- **LP tab error fixes** — saved pool placeholders for failed fetches, Orca auto-fetch, copyable error messages
- **Add Private Key dialog** — scrollable form, vault-mnemonic derive stores key in derived_meta (placeholder fix)
- **Delete private key feature** — per-key delete with password confirmation
- **VERSION constant** — single source of truth replacing hardcoded version strings

### Files Added
- `src/coldtrack/__init__.py` — Package init
- `src/coldtrack/db.py` — SQLite database layer (9 tables, CRUD)
- `src/coldtrack/importer.py` — Vault → ColdTrack account bridge
- `src/coldtrack/tab.py` — ColdTrack tab UI
- `src/ed25519_utils.py` — Shared Ed25519 math primitives, base58, SLIP-0010 HD derivation
- `src/venue_adapters/orca_adapter.py` — Orca Whirlpool read adapter (Solana LP positions)
- `src/venue_adapters/orca_writer.py` — Orca Whirlpool write adapter (collect/close/rebalance via agent)

### Files Changed
- `src/gui_main_v5.py` — ColdTrack tab registered; VERSION constant; delete private key UI; base58 display; copyable notifications; scrollable Add Private Key dialog
- `src/derivation_engine.py` — Solana SLIP-0010 routing; ed25519_utils imports
- `src/key_manager_agent.py` — ed25519_utils imports; Solana sendTransaction encoding fix; SLIP-0010 key preference
- `src/main.py` — delete_private_key() + delete-key CLI command
- `src/account_dialogs.py` — Add Private Key overhaul (scrollable, custom mnemonic, derived_meta fix); delete private key dialog
- `src/lp_tab.py` — Placeholder rendering for failed fetches; Orca auto-fetch; token_id type fix
- `src/lp_engine.py` — Minor updates
- `src/venue_adapters/orca_adapter.py` — Token-2022 detection; Token-2022 wallet scan; _detect_token_program
- `src/saved_pools.py` — Minor updates
- `build_gui_v5.py` — ColdTrack + ed25519_utils hidden imports; version bumped to v5.3.0
- `.gitignore` — coldtrack.db
- `AGENTS.md` — ColdTrack module section; version bump
- `README.md` — ColdTrack section
- `src/chain_options.py` — Minor updates

### Verification
- `python -m py_compile` passes for all new and modified files
- Solana test mnemonic produces `HAgk14JpMQLgt6rVgv7cBQFJWFto5Dqxi472uT3DKpqk` ✅
- GUI launches, ColdTrack tab appears alongside existing tabs
- Vault sync creates coldtrack.db with correct schema
- Portfolios and accounts display correctly after sync
- Existing tabs (Wallet, HL1 Vaults, LP Positions, Railgun) unaffected
- All `py_compile` checks pass

---

## Known Issues

### Aerodrome SlipStream — Active Troubleshooting

**Status:** Read support is functional; write operations are still being validated

Aerodrome SlipStream positions on Base are now discovered via wallet scan and saved-pool gauge lookup. However, the following write paths are not yet fully verified and may fail in production:

- **Collect Fees** — fee collection for Aerodrome NFT positions needs the correct `NonfungiblePositionManager.collect()` calldata and spender approval path. Test on a small position first.
- **Compound Fees** — requires collect → swap through the Aerodrome swap router → re-deposit; the exact router and multi-hop path for SlipStream pools has not been confirmed.
- **Close Position** — must collect unclaimed fees, then call `decreaseLiquidity` with `liquidity=uint128.max` and burn the NFT. The current decrease/burn sequence is being validated against live SlipStream positions.

Until these are verified, treat Aerodrome write operations as experimental. Always double-check the transaction preview and ensure the embedded signing agent is running before confirming.

### Aerodrome SlipStream Staked Positions — Wallet Scan Workaround

**Status:** Temporary workaround — requires manual NFT ID entry

Aerodrome SlipStream positions staked in a CL gauge cannot be discovered via wallet scan. When a position is staked, the NFT is transferred to the gauge contract, so `balanceOf(wallet)` returns 0. The adapter has no way to enumerate all gauges from the Aerodrome Voter contract (no `poolLength()` or `pools(uint256)` exposed).

**Current workaround:** Enter the NFT token ID manually in the Position ID field. The token ID is visible on the Aerodrome dashboard (aerodrome.finance → Dashboard → look for `#<number>` next to "Deposit"). A popup guides the user to this when an Aerodrome scan returns 0 positions.

**Future fix options (not yet viable):**
- Transfer event log scanning: requires `eth_getLogs` over large block ranges. Public Base RPCs cap at ~10k blocks per query (413 error above). Scanning 6 months of history would need ~800 sequential calls — too slow/unreliable. A paid RPC (Alchemy/QuickNode/Infura) with higher log limits would make this feasible.
- Aerodrome subgraph: the Aerodrome frontend uses a single Multicall3 batch call with 8192 bytes of custom bytecode sent to the wallet address. This likely relies on EIP-7702 (EOA delegation) or a similar mechanism that only works if the wallet has code. No public subgraph endpoint has been identified.
- Caching (current partial solution): once a user enters a token ID manually and saves the pool, future wallet scans find the position via the saved-pools gauge lookup (`_find_staked_positions_via_saved_pools`).

---

## v5.2.4 - Railgun Sidecar + Aerodrome & BSC V3 Pools (August 2026)

### Summary
v5.2.4 ships the Railgun privacy sidecar alongside expanded LP coverage: Aerodrome SlipStream on Base and Uniswap V3 / PancakeSwap V3 on BNB Chain. This is an incremental release on top of v5.2.3 with a bug fix for account deletion.

### New Features
- **Aerodrome SlipStream (Base)** — read-only position discovery for concentrated-liquidity pools on Base
  - Wallet scan via `balanceOf`/`tokenOfOwnerByIndex` on the SlipStream Position Manager
  - Saved-pool gauge fallback for staked positions (`_find_staked_positions_via_saved_pools`)
  - Position decoding: token pair, fee tier, tick range, liquidity, price, in-range %
- **BSC V3 Pool Reads** — Uniswap V3 and PancakeSwap V3 positions on BNB Chain via the Krystal adapter
  - Factory/NPM registry for both DEXs
  - Wallet scan across all registered position managers
  - Fee estimation via read-only `collect()` `eth_call`

### Bug Fixes
- **Delete Account** (`src/main.py`, `src/account_dialogs.py`) — accounts that only exist in a pool member list (orphaned after pool swaps) can now be deleted; error message improved when an account is not found anywhere.

### Files Changed
- `src/main.py` — `delete_account()` rewritten to handle orphaned pool references
- `src/account_dialogs.py` — clearer "not found" error message in delete dialog
- `src/venue_adapters/aerodrome_adapter.py` — Aerodrome SlipStream read adapter
- `src/venue_adapters/krystal_adapter.py` — BSC V3 read adapter
- `src/lp_tab.py` — Aerodrome and BSC rendering/scan integration

### Verification
- `python -m py_compile` passes for modified files
- Delete-account dialog now removes orphaned pool-only accounts

---

## v5.2.3 - Railgun Privacy Integration (August 2026)

### Summary
v5.2.3 adds Railgun privacy protocol support via a Node.js sidecar process. ColdStack can now shield ERC-20 tokens, manage shielded balances, and execute private transfers (0zk→0zk) across Ethereum, Arbitrum, BNB Chain, Polygon, Base, and Optimism.

### New Features
- **Railgun Sidecar** — Node.js Express server wrapping the @railgun-community/wallet SDK
- **Shielded Wallets** — load BIP39 mnemonics into Railgun private balances
- **Shield/Unshield** — move ERC-20 tokens between public and private balances
- **Private Transfers** — 0zk→0zk encrypted transfers with optional memo
- **Balance Scanning** — automatic shielded balance updates via Railgun engine callbacks
- **POI Support** — Private Proof of Innocence status tracking
- **Cache Management** — full rebuild and cache clear for engine database

### Architecture
- Sidecar: Node.js Express server on localhost:8765
- Bridge: Python HTTP client (`src/railgun_bridge.py`) manages sidecar lifecycle
- GUI: New "Railgun" tab in the main tabview
- Self-signing mode (no Broadcaster/Waku dependency for v5.2.3)

### Requirements
- Node.js 18+ installed on the system (for running the sidecar)
- First launch downloads 50MB+ of proof artifacts (cached for subsequent use)

### Files Added
- `sidecar/` — complete Node.js sidecar (14 JS files + package.json)
- `src/railgun_bridge.py` — Python HTTP bridge client
- `src/railgun_tab.py` — Railgun GUI tab

---

## v5.2.1 - BSC Pool Reads + Light/Dark Mode (August 2026)

### Summary
v5.2.1 adds Uniswap V3 and PancakeSwap V3 position reads on BNB Chain (BSC) via the Krystal venue adapter, a user-selectable light/dark/system appearance mode, and a session file cleanup fix.

### New Features

- **Krystal Venue Adapter — BSC Pool Reads**
  - Full BSC RPC layer with primary/fallback RPC support
  - Uniswap V3 on BSC: Factory `0xdB1d10011AD0Ff90774D0C6Bb92e5C5c8b4461F7`, NPM `0x7b8A01B39D58278b5DE7e48c8449c9f4F5170613`
  - PancakeSwap V3 on BSC: Factory `0x0BFbCF9fa4f9C56B0F40a671Ad40E0805A091865`, NPM `0x46A15B0b27311cedF172AB29E4f4766fbE7F4364`
  - Wallet scan queries all registered V3 Position Managers (both Uniswap V3 and PancakeSwap V3)
  - Position decoding: token0/token1, fee tier, tick range, liquidity, current price, in-range %
  - Fee estimation via read-only `collect()` eth_call
  - Pool resolution via factory `getPool()` for each PM's factory
  - Known BSC token registry (WBNB, USDT) with on-chain fallback for decimals/symbol

- **Light/Dark/System Appearance Mode**
  - User-selectable in Settings (Dark / Light / System segmented button)
  - Persisted in `appearance.json` (plaintext, next to app — read before vault unlock)
  - Applied at startup before login screen renders
  - `appearance.py` module (carved off from `gui_main_v5.py`)
  - ttk Combobox styling adapts to current mode
  - Text readability fixes: explicit `(light, dark)` color tuples for account list, HL1 Vaults, LP Positions

- **Session File Cleanup**
  - `WM_DELETE_WINDOW` protocol handler ensures `.key_manager_session` is deleted on window close
  - `atexit` fallback for graceful exits where WM_DELETE_WINDOW doesn't fire

### Bug Fixes
- **LP platform dropdown**: `LPEngine` class was missing `list_venues()` method (module-level function existed but GUI called instance method). Silent `AttributeError` fallback only showed HyperEVM.
- **Settings dialog save crash**: `settings_dialog.py` referenced `gui.base_dir` (instance attr) but `base_dir` is a module-level variable. Fixed by computing `base_dir` independently in `settings_dialog.py`.
- **CTkSegmentedButton variable**: `variable` param alone doesn't update `StringVar` on click — added `command` callback to set the value.
- **Build script**: Added `--hidden-import=appearance` for PyInstaller `--onefile` build.

### Files Changed
- `src/venue_adapters/krystal_adapter.py` — Full BSC RPC layer, position decoding, wallet scan, fee estimation, both Uniswap V3 + PancakeSwap V3 support
- `src/venue_adapters/__init__.py` — KrystalAdapter import
- `src/lp_engine.py` — Added `list_venues()` method to `LPEngine` class
- `src/appearance.py` — NEW: appearance mode management (load/save/style_combobox)
- `src/gui_main_v5.py` — Appearance mode startup, LP_PLATFORM_MAP, session cleanup, appearance import
- `src/settings_dialog.py` — Appearance section, base_dir fix, mode-aware combobox, segmented button fix
- `src/vault_tab.py` — Light mode text color fixes
- `src/lp_tab.py` — Light mode text color fixes, suggestion color
- `src/account_dialogs.py` — Light mode fg_color tuple fixes
- `build_gui_v5.py` — `--hidden-import=appearance`, build timestamp

### Verification
- `python -m py_compile` passes for all modified files
- Adapter discovery returns `['hyperliquid', 'krystal']`
- Wallet scan on `0xF04A...` finds 2 BNB/INK positions on Uniswap V3 BSC
- Light mode: all text readable, tabs work, persistence via appearance.json
- Session file deleted on window close

---

## v5.1.4 - gui_main_v5.py Carve-Off (July 2026)

### Summary
Pure refactor: no functional changes. The monolithic `src/gui_main_v5.py` (6,424 lines) was carved into independent modules, leaving the core wallet GUI at ~2,600 lines.

### New Modules
- `src/settings_dialog.py` — Settings dialog
- `src/account_dialogs.py` — Account management dialogs
- `src/vault_tab.py` — HL1 Vaults tab implementation
- `src/lp_tab.py` — LP Positions tab implementation
- `src/chain_options.py` — Shared chain constants

---

## v5.1.3 - Swap Module + Wallet Card Redesign (July 2026)

### New Features
- Swap Dialog (`src/swap_dialog.py`): token swaps on HyperEVM
- Wallet card redesign: copy icon, uniform buttons, improved hover states

---

## v5.1.2 - Vault Explore + Deposit + EVM Transfer (July 2026)

### New Features
- Vault Explore + Deposit dialogs
- EVM ↔ HL1 Transfer Dialog (`src/evm_transfer_dialog.py`)
- HyperEVM balance fiat conversion

---

## v5.1.1 - Add/Remove Liquidity + Gas Fix (July 2026)

### New Features
- Add/Remove Liquidity UI with auto-balance (Zap In)
- `src/lp_liquidity_manager.py` module
- Position decode offset fix, gas price boost, nonce retry

---

## v5.1 - Hyperliquid Vaults + LP Fee Fix (July 2026)

### New Features
- HL1 Vaults tab with vault cards
- Saved vaults in encrypted vault
- LP fee reading via static `collect()` eth_call
- Wallet scan with NFT detection

---

## v4.2 - Customizable RPC + Standard/Advanced Mode (July 2026)

### New Features
- Customizable RPC endpoints (`rpc_endpoints.json`)
- Standard/Advanced mode toggle
- API key management (stored in encrypted vault)

---

## v4.1 - Price Feeds + Wallet Balances (June 2026)

### New Features
- Go Online toggle, inline balance display
- Balance Engine + Price Engine (CoinGecko)
- Multi-currency display (USD, AUD, CAD, EUR, CHF)

---

## v3.1 - ColdStack Rebrand (June 2026)

- GUI rebranded to "ColdStack"
- CLI EXE deprecated, GUI-only deployment

---

## v3.0 - BIP39 Derivation (June 2026)

- DerivationEngine: 7 chains (EVM, BTC, Solana, Dash, Sui)
- GUI derivation dialogs

---

## Architecture

```
coldstack/
├── src/
│   ├── gui_main_v5.py          — Main GUI (~2,540 lines after v5.2.1 carve-off)
│   ├── appearance.py           — Light/dark mode management (v5.2.1)
│   ├── settings_dialog.py      — Settings dialog (v5.1.4 carve-off)
│   ├── account_dialogs.py      — Account management dialogs (v5.1.4)
│   ├── vault_tab.py            — HL1 Vaults tab (v5.1.4)
│   ├── lp_tab.py               — LP Positions tab (v5.1.4)
│   ├── chain_options.py        — Shared chain constants (v5.1.4)
│   ├── swap_dialog.py          — Token swap dialog (v5.1.3)
│   ├── evm_transfer_dialog.py  — EVM ↔ HL1 transfers (v5.1.2)
│   ├── vault_deposit_dialog.py — Vault deposit dialog (v5.1.2)
│   ├── lp_liquidity_manager.py — Add/Remove liquidity (v5.1.1)
│   ├── lp_engine.py            — LP position engine + StrategyEngine
│   ├── crypto_engine.py        — AES-256-GCM + Argon2id
│   ├── derivation_engine.py    — BIP39 derivation
│   ├── balance_engine.py       — Wallet balance fetching
│   ├── price_engine.py         — CoinGecko price feeds
│   ├── rpc_config.py           — RPC endpoint config
│   ├── vault_tracker.py        — Hyperliquid vault tracking
│   ├── saved_pools.py          — Saved pools CRUD
│   ├── key_manager_agent.py    — Headless signing agent
│   ├── main.py                 — CLI interface
│   └── venue_adapters/
│       ├── __init__.py         — Adapter discovery
│       ├── hyperliquid_adapter.py — HyperEVM + L1 reads
│       ├── hyperliquid_writer.py  — HyperEVM write operations
│       ├── krystal_adapter.py  — BSC multi-DEX reads (Uniswap V3 + PancakeSwap V3)
│       └── venue_writer.py    — Writer ABC
├── build_gui_v5.py             — PyInstaller build script
├── rpc_endpoints.json          — Default RPC endpoints
├── requirements.txt
└── USB_DEPLOYMENT/
    └── coldstack.exe           — Portable EXE
```

## Versioning

| Version | Description | Key Files |
|---------|-------------|-----------|
| v5.2.1 | BSC Pool Reads + Light/Dark Mode | `krystal_adapter.py`, `appearance.py`, `settings_dialog.py` |
| v5.1.4 | gui_main_v5.py carve-off | `settings_dialog.py`, `account_dialogs.py`, `vault_tab.py`, `lp_tab.py` |
| v5.1.3 | Swap module + wallet card redesign | `swap_dialog.py` |
| v5.1.2 | Vault explore + deposit + EVM transfer | `evm_transfer_dialog.py`, `vault_deposit_dialog.py` |
| v5.1.1 | Add/remove liquidity + gas fix | `lp_liquidity_manager.py` |
| v5.1 | Hyperliquid vaults + LP fee fix | `vault_tracker.py`, `hyperliquid_adapter.py` |
| v4.2 | Customizable RPC + Standard/Advanced | `rpc_config.py`, `rpc_endpoints.json` |
| v4.1 | Price feeds + wallet balances | `balance_engine.py`, `price_engine.py` |
| v3.1 | ColdStack rebrand | — |
| v3.0 | BIP39 derivation engine | `derivation_engine.py` |
