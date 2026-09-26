"""LP panel refresh + account-label tests (v5.3.19).

1. fetch-single refresh: after a single fetch, every saved pool is refetched via
   its OWN venue adapter + bound account; one failing pool marks only its card
   (no cross-pool contamination).
2. Account label: bound account renders on the card; legacy/unresolvable shows
   "(unbound)".

Run:  python test_lp_panel_refresh.py
"""
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).parent / "src"))
sys.path.insert(0, str(Path(__file__).parent))

import lp_tab as lt  # noqa: E402
from venue_adapters.hyperliquid_adapter import HyperliquidAdapter  # noqa: E402
from venue_adapters.aerodrome_adapter import AerodromeAdapter  # noqa: E402
from venue_adapters.orca_adapter import OrcaAdapter  # noqa: E402


SOL_MINT = "FbNHxe9VV797JWG7XH2msjwp5Rvb6dGzndwwkEXXXBKX"


class _Pos:
    def __init__(self, position_id, venue, pair):
        self.position_id = position_id
        self.venue = venue
        self.pair = pair
        self.error = None


class _SyncThread:
    """Run the fetch thread synchronously so the test is deterministic."""
    def __init__(self, target=None, daemon=None, **kw):
        self.target = target

    def start(self):
        if self.target:
            self.target()


def _new_tab(saved, accounts):
    class _KM:
        address_db = {"saved_pools": saved, "accounts": accounts}

    class _Root:
        @staticmethod
        def after(ms, fn):
            fn()

    class _FakeGUI:
        key_manager = _KM()
        price_engine = None
        lp_engine = True
        online_mode = True
        root = _Root

    tab = lt.LPTab.__new__(lt.LPTab)
    tab.gui = _FakeGUI()
    tab._lp_widgets = {}
    return tab


def test_fetch_single_refreshes_all_saved():
    calls = []
    rendered = []
    placeholders = []
    order = []

    saved = [
        {"token_id": 545983, "venue": "HyperEVM", "pair": "WHYPE/UBTC",
         "account_name": "G1", "account_address": "0xAAA", "account_chain": "hyperevm"},
        {"token_id": 75269474, "venue": "Aerodrome", "pair": "WETH/cbBTC",
         "account_name": "G2", "account_address": "0xBBB", "account_chain": "base"},
        {"token_id": SOL_MINT, "venue": "Orca", "pair": "cbBTC/SOL",
         "account_name": "G3", "account_address": "Sol111", "account_chain": "solana"},
    ]
    accounts = {"G1": {}, "G2": {}, "G3": {}}
    tab = _new_tab(saved, accounts)

    tab._lp_render_card = lambda pos: (order.append("card"), rendered.append(pos))
    tab._lp_render_saved_placeholder = lambda scroll, entry, prefix, tid, venue, pair, state="auto", error=None: (
        order.append(("ph", state, error)), placeholders.append(entry))
    tab._lp_update_button_states = lambda: None
    # v5.3.20: the neutral "Fetching…" state must be rendered BEFORE any result.
    real_fs = tab._lp_render_fetching_state
    tab._lp_render_fetching_state = lambda a, e: (order.append("fetching"), real_fs(a, e))

    real_thread = lt.threading.Thread
    real = {
        "hype": HyperliquidAdapter.fetch_evm_position_by_token_id,
        "aero_fetch": AerodromeAdapter._fetch_position_by_token_id,
        "aero_staked": AerodromeAdapter._find_staked_positions_via_saved_pools,
        "orca": OrcaAdapter._fetch_by_position_mint,
    }

    def hype(self, tid, pe, wallet_address=None):
        calls.append(("hype", tid, wallet_address))
        return _Pos(f"hyperevm:{tid}", "HyperEVM", "WHYPE/UBTC")

    def aero_fetch(self, tid, pe, wallet_address=None):
        calls.append(("aero", tid, wallet_address))
        raise RuntimeError("simulated Aerodrome RPC failure")

    def aero_staked(self, wallet, pe, entries):
        return []

    def orca(self, tid, pe, wallet_address=None):
        calls.append(("orca", tid, wallet_address))
        return _Pos(f"solana:{tid}", "Orca", "cbBTC/SOL")

    extra = _Pos("hyperevm:999", "HyperEVM", "WHYPE/USDC")
    try:
        lt.threading.Thread = _SyncThread
        HyperliquidAdapter.fetch_evm_position_by_token_id = hype
        AerodromeAdapter._fetch_position_by_token_id = aero_fetch
        AerodromeAdapter._find_staked_positions_via_saved_pools = aero_staked
        OrcaAdapter._fetch_by_position_mint = orca

        # This is the fetch-single hook: refresh every saved card.
        tab._lp_refresh_after_single([extra])
    finally:
        lt.threading.Thread = real_thread
        HyperliquidAdapter.fetch_evm_position_by_token_id = real["hype"]
        AerodromeAdapter._fetch_position_by_token_id = real["aero_fetch"]
        AerodromeAdapter._find_staked_positions_via_saved_pools = real["aero_staked"]
        OrcaAdapter._fetch_by_position_mint = real["orca"]

    # Each saved pool dispatched to its OWN venue adapter with its OWN bound account.
    assert ("hype", 545983, "0xAAA") in calls, calls
    assert ("aero", 75269474, "0xBBB") in calls, calls
    assert ("orca", SOL_MINT, "Sol111") in calls, calls

    # Live cards: the extra single position + the two successfully refetched pools.
    live_ids = {p.position_id for p in rendered}
    assert "hyperevm:999" in live_ids, live_ids
    assert "hyperevm:545983" in live_ids, live_ids
    assert f"solana:{SOL_MINT}" in live_ids, live_ids

    # The failing Aerodrome pool marks ONLY its own card.
    assert len(placeholders) == 1, placeholders
    assert placeholders[0]["token_id"] == 75269474, placeholders[0]

    # v5.3.21: the failed pool receives its specific error message.
    auto_ph = [o for o in order if isinstance(o, tuple) and o[0] == "ph" and o[1] == "auto"]
    assert len(auto_ph) == 1, order
    assert "simulated Aerodrome RPC failure" in (auto_ph[0][2] or ""), auto_ph[0]

    # v5.3.20: the neutral "Fetching…" state is rendered before any result card.
    assert order and order[0] == "fetching", order

    print("✅ fetch-single refreshes all saved pools (own venue+account, no cross-marking)")


def test_fetching_state_is_neutral():
    """v5.3.20: the in-flight state shows 'Fetching positions…' and never probes
    on-chain closed or renders a warning."""
    import customtkinter as ctk
    ctk.set_appearance_mode("dark")
    root = ctk.CTk()
    root.withdraw()

    saved = [{"token_id": 545983, "venue": "HyperEVM", "pair": "WHYPE/UBTC",
              "account_name": "G1"}]
    tab = _new_tab(saved, {"G1": {}})

    rendered = []

    class _StubFrame:
        def __init__(self, *a, **k): pass
        def pack(self, *a, **k): pass
        def winfo_children(self): return []

    class _StubLabel(_StubFrame):
        def __init__(self, parent, text="", **k):
            rendered.append(text)

    class _StubButton(_StubFrame):
        def __init__(self, *a, **k): pass

    class _Status:
        def configure(self, **k): pass

    real = (ctk.CTkFrame, ctk.CTkLabel, ctk.CTkButton)
    ctk.CTkFrame, ctk.CTkLabel, ctk.CTkButton = _StubFrame, _StubLabel, _StubButton
    closed_probes = []
    try:
        tab._lp_saved_pool_is_closed = lambda *a, **k: closed_probes.append(True) or False
        tab._lp_update_button_states = lambda: None
        tab._lp_widgets = {"scroll": _StubFrame(), "status_label": _Status()}
        tab._lp_render_fetching_state(saved, [])
    finally:
        ctk.CTkFrame, ctk.CTkLabel, ctk.CTkButton = real
        root.destroy()

    assert any("Fetching positions" in t for t in rendered), rendered
    assert not any("Fetch failed" in t for t in rendered), rendered
    assert closed_probes == [], "fetching state must not probe on-chain closed status"
    print("✅ fetching state is neutral (no closed probe, no warning)")


def test_refresh_failure_does_not_wipe_saved_cards():
    saved = [{"token_id": 545983, "venue": "HyperEVM", "pair": "WHYPE/UBTC",
              "account_name": "G1", "account_address": "0xAAA"}]
    tab = _new_tab(saved, {"G1": {}})
    called = {"auto": 0, "error": 0}
    tab._lp_auto_fetch_all_saved = lambda extra_positions=None: called.__setitem__("auto", called["auto"] + 1)
    tab._lp_on_error = lambda msg: called.__setitem__("error", called["error"] + 1)

    tab._lp_refresh_after_single(error="boom")
    assert called["auto"] == 1 and called["error"] == 0, called

    # No saved pools -> surface the error instead.
    tab2 = _new_tab([], {})
    called2 = {"auto": 0, "error": 0}
    tab2._lp_auto_fetch_all_saved = lambda extra_positions=None: called2.__setitem__("auto", called2["auto"] + 1)
    tab2._lp_on_error = lambda msg: called2.__setitem__("error", called2["error"] + 1)
    tab2._lp_refresh_after_single(error="boom")
    assert called2["error"] == 1 and called2["auto"] == 0, called2

    print("✅ refresh keeps saved cards on failure; surfaces error only when none saved")


def test_account_label_resolution_and_render_smoke():
    import customtkinter as ctk
    ctk.set_appearance_mode("dark")
    root = ctk.CTk()
    root.withdraw()

    calls = []

    saved = [{"token_id": 545983, "venue": "HyperEVM", "pair": "WHYPE/UBTC",
              "wallet_address": "0xAAA"}]
    accounts = {"G1": {"addresses": [{"address": "0xAAA", "chain": "hyperevm"}]}}
    tab = _new_tab(saved, accounts)

    # Bound entry -> its account name.
    assert tab._lp_pool_account_label({"account_name": "G7"}) == "G7"
    # Legacy entry with a matching vault wallet -> resolved + persisted (self-heal).
    assert tab._lp_pool_account_label(saved[0]) == "G1"
    assert saved[0].get("account_name") == "G1", saved[0]
    # Unresolvable -> "(unbound)"; no entry -> "".
    assert tab._lp_pool_account_label({"wallet_address": "0xNOPE"}) == "(unbound)"
    assert tab._lp_pool_account_label(None) == ""

    # Widget-construction smoke: the label renders on the card.
    rendered = []

    class _StubFrame:
        def __init__(self, *a, **k): pass
        def pack(self, *a, **k): pass
        def winfo_children(self): return []

    class _StubLabel(_StubFrame):
        def __init__(self, parent, text="", **k):
            rendered.append(text)

    class _StubButton(_StubFrame):
        def __init__(self, *a, **k): pass

    real = (ctk.CTkFrame, ctk.CTkLabel, ctk.CTkButton)
    ctk.CTkFrame, ctk.CTkLabel, ctk.CTkButton = _StubFrame, _StubLabel, _StubButton
    try:
        tab._lp_saved_pool_is_closed = lambda entry, prefix, tid, venue: False
        tab._lp_render_saved_placeholder(_StubFrame(), {"account_name": "G1"}, "hyperevm", 545983, "HyperEVM", "WHYPE/UBTC")
        tab._lp_render_saved_placeholder(_StubFrame(), {"wallet_address": "0xNOPE"}, "hyperevm", 545984, "HyperEVM", "WHYPE/UBTC")
    finally:
        ctk.CTkFrame, ctk.CTkLabel, ctk.CTkButton = real
        root.destroy()

    assert any("·  G1" in t for t in rendered), rendered
    assert any("·  (unbound)" in t for t in rendered), rendered

    print("✅ account label resolves/persists and renders (bound + unbound)")


def test_rescan_resolves_address_per_chain():
    """Part 2: saved-pool rescan resolves the wallet address from the pool's chain."""
    sui_addr = "0x04887176a0791ac1837bc654533990820a33f2c289b8636c7066a1865191b314"
    sol_addr = "FbNHxe9VV797JWG7XH2msjwp5Rvb6dGzndwwkEXXXBKX"
    evm_addr = "0x" + "a" * 40

    saved = [
        {"token_id": 1, "venue": "HyperEVM", "pair": "WHYPE/UBTC",
         "account_name": "G1", "account_address": evm_addr},
        {"token_id": 2, "venue": "Aerodrome", "pair": "EURC/cbBTC",
         "account_name": "G2", "account_address": "0x" + "b" * 40},
        {"token_id": "obj" * 11, "venue": "Orca", "pair": "cbBTC/SOL",
         "account_name": "G3", "account_address": sol_addr},
        {"token_id": sui_addr, "venue": "Cetus", "pair": "LBTC/SUI",
         "account_name": "N1", "account_address": sui_addr},
    ]
    accounts = {
        "G1": {"addresses": [{"address": evm_addr, "chain": "EVM"}]},
        "G2": {"addresses": [{"address": "0x" + "b" * 40, "chain": "Base"}]},
        "G3": {"addresses": [{"address": sol_addr, "chain": "SOL"}]},
        "N1": {"addresses": [{"address": sui_addr, "chain": "SUI"}]},
    }
    tab = _new_tab(saved, accounts)

    assert tab._lp_resolve_wallet_for_saved_pool(saved[0]) == (evm_addr, "evm", "G1")
    assert tab._lp_resolve_wallet_for_saved_pool(saved[1]) == ("0x" + "b" * 40, "evm", "G2")
    assert tab._lp_resolve_wallet_for_saved_pool(saved[2]) == (sol_addr, "solana", "G3")
    assert tab._lp_resolve_wallet_for_saved_pool(saved[3]) == (sui_addr, "sui", "N1")

    # Wrong-chain stored address is corrected by deriving from the bound account.
    wrong = {"token_id": 1, "venue": "HyperEVM", "pair": "WHYPE/UBTC",
             "account_name": "G1", "account_address": sui_addr}
    assert tab._lp_resolve_wallet_for_saved_pool(wrong) == (evm_addr, "evm", "G1")

    print("✅ rescan resolves wallet address per venue/chain")


def main():
    test_fetch_single_refreshes_all_saved()
    test_fetching_state_is_neutral()
    test_refresh_failure_does_not_wipe_saved_cards()
    test_account_label_resolution_and_render_smoke()
    test_rescan_resolves_address_per_chain()
    print("✅ ALL LP PANEL REFRESH + ACCOUNT LABEL TESTS PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
