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

    tab._lp_render_card = lambda pos: rendered.append(pos)
    tab._lp_render_saved_placeholder = lambda scroll, entry, prefix, tid, venue, pair: placeholders.append(entry)
    tab._lp_update_button_states = lambda: None

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

    print("✅ fetch-single refreshes all saved pools (own venue+account, no cross-marking)")


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


def main():
    test_fetch_single_refreshes_all_saved()
    test_refresh_failure_does_not_wipe_saved_cards()
    test_account_label_resolution_and_render_smoke()
    print("✅ ALL LP PANEL REFRESH + ACCOUNT LABEL TESTS PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
