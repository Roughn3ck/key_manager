# Forge Prompt — ColdStack v5.3.27 #1b: search-persistence, revised (log-proven findings)

Kris retested #1 (f6d9f83): no visible change — saved pools still vanish during a
search and return as "Fetch failed". Slater traced the pack portfolio's gui_debug.log
+ the code. **The rescan machinery WORKS** — the problem is elsewhere, and the log
names it:

```
[rescan] BSC #2651550: chain=evm wallet=0xF04A26e3… bound=G4  → OK pair=WBNB/LINK
[rescan] Aerodrome #5545002: wallet=0x817C07dA… bound=G6      → OK pair=cbXRP/cbBTC
[rescan] Aerodrome #76866113: wallet=0xAe8E5FDb… bound=G1    → OK pair=EURC/cbBTC
```
Per-pool bindings resolve correctly (G4/G6/G1, own wallets) and fetch OK. But other
saved pools never even reach a `[rescan]` line — they fail EARLIER, and the whole-card
"Fetch failed — live data unavailable" text misrepresents real, specific failures:

```
[refresh_fees] error: invalid literal for int() with base 10: '0x18bee062…9de4fad'   ← Cetus position OBJECT ID fed to int()
[compound_fees] error: invalid literal for int() with base 10: '0x714a63a0…::i32::I32' ← I32 still crashing this path
[close_position] pre-flight: Agent error (get_solana_address): No private keys found for account 'G3'  ← Orca pool's bound account has NO Solana keys
TypeError: ColdStackGUI.create_address_card.<locals>.<lambda>() missing 1 required positional argument: 'e'  ← address-card click handler broken (account switching!)
Error loading data: Decryption failed: …  ← mid-session, undiagnosed
```

## Fixes (evidence-ordered)

1. **Address-card click lambda** (`create_address_card`, gui_main_v5.py:1206): the
   click handler is missing its event arg — clicking an address card (SELECTING
   ACCOUNT G2 — the first step of Kris's repro!) throws. Fix the lambda signature.
2. **Vanish-during-search**: with #1 fixed, re-run the G2 repro and trace the render
   path during fetch-single. The saved-card container must NEVER be rebuilt/cleared
   while a fetch is in flight — the "Fetching…" state renders around the cards, not
   instead of them. Find the site that empties/rebuilds the card panel and cut it.
3. **Cetus int() crashes**: `refresh_fees` feeds the POSITION OBJECT ID to int() and
   `compound_fees` still hits the raw I32 type string — the I32 fix didn't cover
   these call sites. Apply the I32 helper + object-id handling at EVERY parse point:
   grep-sweep all `int(` on position/tick/object-id fields across the Cetus paths and
   unit-test each with live shapes (bits decode/encode round-trip; object ids stay
   strings).
4. **Error layering — the headline fix**: a card must NEVER show "Fetch failed —
   live data unavailable" for a partial failure. A fee-refresh/compound failure on a
   position whose data decoded fine degrades to a "fees unavailable" pill. Whole-card
   failure text is reserved for total fetch failure, and must show the REAL error
   (e.g. "bound account G3 has no Solana keys — rebind or add the key"), never the
   generic line. Enforce at the marking sites: `_lp_update_saved_cards_in_place`
   placeholder text = the actual errors[key]; wallet-resolution failures produce
   precise messages.
5. **Diagnose `Decryption failed`** (log line 21656): instrument which store/decrypt
   fails and when; likely the saved-pool store or address_db under a mid-session key
   state. Fix or at minimum log precisely.

## Acceptance

1. G2 repro (live read-only): clicking the G2 address card works (no TypeError);
   saved cards stay rendered throughout the fetch; the three healthy pools refresh
   in place with no failure marks; the Orca card shows the precise Solana-keys
   message; the Cetus card shows data with no int() crash (fees pill or value).
2. Unit tests: lambda signature; I32/object-id round-trips at the crashed call sites;
   error-layering (fee failure → pill, not card failure; real error text verbatim).
3. `python -m py_compile`; full suite green. **No push, no EXE build** (Kris compiles).
   STATUS.md v5.3.27 section: append this round's lines.

## Report back (brief)

- The vanish site you found + the lambda fix.
- The Cetus int() call sites fixed.
- The error-layering design + the G3 message. Test transcript.