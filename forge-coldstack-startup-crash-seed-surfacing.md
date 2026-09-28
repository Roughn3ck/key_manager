# Forge Prompt — ColdStack v5.3.26 completion: startup crash + seed surfacing (log-proven)

Kris's kitandpaul gui_debug.log settles three things. Evidence quoted verbatim below.

## 1. FIRST: fix the startup crash (blocks all testing)

```
ColdStack started at 2026-09-28 11:02:17
Traceback ... File "gui_main_v5.py", line 422, in create_login_screen
NameError: name 'login_frame' is not defined. Did you mean: 'main_frame'?
```

The current working tree (you are mid-edit — build_gui_v5.py, close_recorder, tab all
modified) crashes before the login screen renders. `login_frame` is referenced at
line 422 before its creation — the widget-creation-order class (v5.3.8 lesson). Fix it,
then run the headless construction smoke test (stub GUI → build login screen + LP tab →
assert no exception, expected widgets present) as a permanent regression test. Do not
ship any exe state that fails this test.

## 2. The seed WORKS — surface it in the GUI

From the 10:15 session, the seed ran and fully decoded the staked position:

```
[aerodrome-value] token 75269474: val0=845.06 val1=741.18 fees=38.79 pool_value=1586.23
[rescan] Aerodrome #75269474: OK pair=WETH/cbBTC
```

Yet the GUI still told Kris "Aerodrome Positions Not Found". So the fetch result path
drops the seeded position between fetch_all_positions returning it and the dialog/card
render — OR the "Not Found" dialog fires without consulting the seeded/scan results.
Trace the display path and fix: seeded positions that decode cleanly MUST appear as
cards; the "Positions Not Found" message must never fire when results exist. Note
`[saved_pools] loaded 0 saved pools` — with an empty saved-pool store the seeded scan
result is the only source; make sure that flow renders it.

## 3. Staked fallback window (minor, honest bounds)

```
[aerodrome-staked] scanned 2 chunks in 1.3s, found 0 logs, per-PM={}
```

2 chunks ≈ recent blocks only — it cannot reach an Aug-22 stake transfer. Either
implement the first-activity-block anchor or log honestly: "fallback scan covers last
N blocks only". The ledger seed is the primary path; don't burn effort here beyond
the honest message.

## Acceptance

1. Headless construction smoke test passes (login screen + LP tab).
2. Live read-only from the kitandpaul folder: Aerodrome fetch → the staked position
   renders as a card (ETH/cbBTC, STAKED badge, value ≈ $1,586, fees ≈ $38.79); the
   "Not Found" dialog does NOT fire; console shows the seed line + per-path counts.
3. `python -m py_compile`; full suite green; commit everything in flight as v5.3.26
   (repo HEAD is still the Sep 27 backup). **No EXE build, no push, no release.**
   Live DBs read-only; no on-chain actions.

## Report back (brief)

- The login_frame fix + smoke test transcript.
- Where the display path dropped the seeded position (exact line) + the fix.
- Live fetch transcript from kitandpaul showing the card.