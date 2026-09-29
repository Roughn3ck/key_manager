"""Regression tests for v5.3.27e: fetch pipeline uses multi-account refresh.

Reproduces the bug where _lp_do_fetch and _lp_do_filtered_scan were
single-address, placeholder-first flows that stranded every saved pool bound
to a different account.
"""
import sys
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent / "src"))

import customtkinter as ctk


class _FakePosition:
    def __init__(self, venue, position_id, pair, wallet_address=""):
        self.venue = venue
        self.position_id = position_id
        self.pair = pair
        self.wallet_address = wallet_address
        self.error = None
        self.health_emoji = ""
        self.fees_earned = {}
        self.fees_earned_usd = None
        self.pnl_usd = None
        self.pnl_pct = None
        self.apy = None
        self.days_active = None
        self.deposit_amounts = {}
        self.current_value_usd = None
        self.range_low = None
        self.range_high = None
        self.current_price = None
        self.position_in_range_pct = None
        self.suggested_action = ""


def _new_tab(saved_pools, accounts=None):
    accounts = accounts or {}

    class _KM:
        address_db = {"saved_pools": saved_pools, "accounts": accounts}

    _root = ctk.CTk()
    _root.withdraw()
    _root.after = lambda ms, fn: fn()

    class _FakeGUI:
        key_manager = _KM()
        price_engine = None
        lp_engine = True
        online_mode = True
        root = _root
        LP_PLATFORM_MAP = {}
        LP_PLATFORM_MAP_reverse = {}

        @staticmethod
        def _format_currency(v):
            return f"${v:,.2f}"

        @staticmethod
        def copy_to_clipboard(_):
            pass

        @staticmethod
        def show_notification(msg, error=False):
            pass

    import lp_tab as lt
    tab = lt.LPTab.__new__(lt.LPTab)
    tab.gui = _FakeGUI()
    tab._lp_widgets = {}
    tab._lp_auto_fetched = False
    tab._lp_last_fetched_address = ""
    tab._root = _root
    return tab


def test_do_fetch_routes_through_all_saved_entries():
    """Scan Wallet must refresh every saved pool, not only those for the current address."""
    saved = [
        {"token_id": 11, "venue": "HyperEVM", "pair": "WHYPE/UBTC", "account_name": "G1"},
        {"token_id": 22, "venue": "Aerodrome", "pair": "WETH/cbBTC", "account_name": "N1"},
    ]
    tab = _new_tab(saved)
    tab._lp_venue_prefix = lambda v: {"HyperEVM": "hyperevm", "Aerodrome": "base"}.get(v, "bsc")

    fetched_addresses = []

    def _fake_fetch_all_saved_entries(all_saved, extra_positions=None):
        for e in all_saved:
            fetched_addresses.append((e.get("venue"), e.get("account_name")))
        return [], {}

    captured_scan_address = []

    def _fake_full_scan(address):
        captured_scan_address.append(address)
        tab._lp_on_loaded([], address)

    tab._lp_fetch_all_saved_entries = _fake_fetch_all_saved_entries
    tab._lp_do_full_scan = _fake_full_scan
    tab._lp_update_button_states = lambda: None

    entry = ctk.CTkEntry(tab.gui.root)
    entry.insert(0, "0x0000000000000000000000000000000000000000")
    tab._lp_widgets["address_entry"] = entry
    tab._lp_widgets["status_label"] = ctk.CTkLabel(tab.gui.root, text="")
    tab._lp_widgets["refresh_btn"] = ctk.CTkButton(tab.gui.root, text="")

    tab._lp_do_fetch()

    assert set(fetched_addresses) == {("HyperEVM", "G1"), ("Aerodrome", "N1")}, fetched_addresses
    # The discovery scan ran for the selected 0x address.
    assert captured_scan_address == ["0x0000000000000000000000000000000000000000"], captured_scan_address
    print("PASS test_do_fetch_routes_through_all_saved_entries")


def test_filtered_scan_does_not_filter_saved_pools():
    """Platform filter scopes only the discovery scan; saved-pool refresh covers all."""
    saved = [
        {"token_id": 11, "venue": "HyperEVM", "pair": "WHYPE/UBTC", "account_name": "G1"},
        {"token_id": 22, "venue": "Aerodrome", "pair": "WETH/cbBTC", "account_name": "N1"},
    ]
    tab = _new_tab(saved)
    tab._lp_venue_prefix = lambda v: {"HyperEVM": "hyperevm", "Aerodrome": "base"}.get(v, "bsc")

    fetched = []

    def _fake_fetch_all_saved_entries(all_saved, extra_positions=None):
        for e in all_saved:
            fetched.append(e.get("venue"))
        return [], {}

    def _fake_filtered_full_scan(address, venue_key):
        tab._lp_on_loaded([], address)

    tab._lp_fetch_all_saved_entries = _fake_fetch_all_saved_entries
    tab._lp_do_filtered_full_scan = _fake_filtered_full_scan
    tab._lp_update_button_states = lambda: None

    tab._lp_widgets["status_label"] = ctk.CTkLabel(tab.gui.root, text="")
    tab._lp_widgets["refresh_btn"] = ctk.CTkButton(tab.gui.root, text="")

    tab._lp_do_filtered_scan("0x0000000000000000000000000000000000000000", "aerodrome")

    assert "HyperEVM" in fetched and "Aerodrome" in fetched, fetched
    print("PASS test_filtered_scan_does_not_filter_saved_pools")


def test_on_loaded_does_not_clear_existing_cards():
    """The full-scan callback must merge, not destroy, existing saved-pool cards."""
    saved = [
        {"token_id": 11, "venue": "HyperEVM", "pair": "WHYPE/UBTC", "account_name": "G1"},
    ]
    tab = _new_tab(saved)
    tab._lp_venue_prefix = lambda v: "hyperevm"
    tab._lp_update_button_states = lambda: None

    root = ctk.CTk()
    root.withdraw()
    scroll = ctk.CTkScrollableFrame(root)
    tab._lp_widgets["scroll"] = scroll
    tab._lp_widgets["status_label"] = ctk.CTkLabel(root, text="")

    # Pre-populate a saved-pool card
    existing = _FakePosition("HyperEVM", "hyperevm:11", "WHYPE/UBTC")
    tab._lp_render_card(existing)
    original_children = list(scroll.winfo_children())
    assert original_children

    # Discovery scan returns a different new position
    new_pos = _FakePosition("Aerodrome", "base:22", "WETH/cbBTC")
    tab._lp_on_loaded([new_pos], "0x0000000000000000000000000000000000000000")

    children_after = list(scroll.winfo_children())
    # The original card should still exist (merged, not cleared)
    assert any(c is original_children[0] for c in children_after), "existing card was destroyed"
    # And the new card should be present too
    keys = set(tab._lp_widgets.get("position_cards", {}).keys())
    assert "aerodrome:base:22" in keys, keys
    print("PASS test_on_loaded_does_not_clear_existing_cards")
    root.destroy()


def test_placeholder_default_state_is_fetching_not_failed():
    """A placeholder rendered without an error must show the neutral fetching text."""
    import lp_tab as lt
    saved = [
        {"token_id": 11, "venue": "HyperEVM", "pair": "WHYPE/UBTC", "account_name": "G1"},
    ]
    tab = _new_tab(saved)
    tab._lp_venue_prefix = lambda v: "hyperevm"
    tab._lp_pool_account_label = lambda e: "G1"
    tab._lp_saved_pool_is_closed = lambda *a, **k: False

    root = ctk.CTk()
    root.withdraw()
    scroll = ctk.CTkScrollableFrame(root)
    tab._lp_widgets["scroll"] = scroll

    tab._lp_render_saved_placeholder(scroll, saved[0], "hyperevm", 11, "HyperEVM", "WHYPE/UBTC")

    labels = [c for c in scroll.winfo_children()[0].winfo_children()[0].winfo_children() if isinstance(c, ctk.CTkLabel)]
    texts = [l.cget("text") for l in labels]
    assert any("Fetching positions…" in t for t in texts), texts
    assert not any("Fetch failed — live data unavailable" in t for t in texts), texts
    print("PASS test_placeholder_default_state_is_fetching_not_failed")
    root.destroy()


def test_render_trace_format():
    """[card-render] trace line must include fn, key, state, and error."""
    saved = [
        {"token_id": 11, "venue": "HyperEVM", "pair": "WHYPE/UBTC", "account_name": "G1"},
    ]
    tab = _new_tab(saved)
    tab._lp_venue_prefix = lambda v: "hyperevm"
    tab._lp_pool_account_label = lambda e: "G1"
    tab._lp_saved_pool_is_closed = lambda *a, **k: False

    root = ctk.CTk()
    root.withdraw()
    scroll = ctk.CTkScrollableFrame(root)
    tab._lp_widgets["scroll"] = scroll

    captured = StringIO()
    old_stdout = sys.stdout
    sys.stdout = captured
    try:
        tab._lp_render_saved_placeholder(scroll, saved[0], "hyperevm", 11, "HyperEVM", "WHYPE/UBTC", error="RPC bad")
    finally:
        sys.stdout = old_stdout

    line = captured.getvalue()
    assert line.startswith("[card-render]"), line
    assert "fn=_lp_render_saved_placeholder" in line, line
    assert "key=hyperevm:hyperevm:11" in line, line
    assert "state=failed" in line, line
    assert "error=RPC bad" in line, line
    print("PASS test_render_trace_format")
    root.destroy()


def main():
    test_do_fetch_routes_through_all_saved_entries()
    test_filtered_scan_does_not_filter_saved_pools()
    test_on_loaded_does_not_clear_existing_cards()
    test_placeholder_default_state_is_fetching_not_failed()
    test_render_trace_format()
    print("ALL LP FETCH PIPELINE TESTS PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
