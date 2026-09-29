"""Regression tests for v5.3.27d: canonical card keys + no vanish.

Reproduces the bug where three different key formats meant a saved pool whose
fetch succeeded still fell into the "Fetch failed" placeholder branch and the
fetching state destroyed all existing cards.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent / "src"))

import customtkinter as ctk


class _FakePosition:
    def __init__(self, venue, position_id, pair):
        self.venue = venue
        self.position_id = position_id
        self.pair = pair
        self.error = None
        self.wallet_address = ""


def _stub_root():
    root = ctk.CTk()
    root.withdraw()
    return root


def _new_tab(saved):
    class _KM:
        address_db = {"saved_pools": saved, "accounts": {}}

    class _Root:
        @staticmethod
        def after(ms, fn): fn()

    class _FakeGUI:
        key_manager = _KM()
        price_engine = None
        lp_engine = True
        online_mode = True
        root = _Root

    import lp_tab as lt
    tab = lt.LPTab.__new__(lt.LPTab)
    tab.gui = _FakeGUI()
    tab._lp_widgets = {}
    return tab


def test_canonical_key_normalizes_formats():
    """Historical 2-segment, 3-segment, and prefix-only keys all map to one canonical key."""
    import lp_tab as lt

    pos = _FakePosition("Aerodrome", "base:76866113", "EURC/cbBTC")
    canonical = lt.LPTab._lp_card_key(pos.venue, pos.position_id)

    # All historical formats should resolve to the same key.
    assert canonical == "aerodrome:base:76866113"
    assert lt.LPTab._lp_card_key("aerodrome", "base:76866113") == canonical
    assert lt.LPTab._lp_card_key("Aerodrome", "base:76866113") == canonical

    sol = _FakePosition("Orca", "solana:FbNHxe9VV797JWG7XH2msjwp5Rvb6dGzndwwkEXXXBKX", "cbBTC/SOL")
    sol_key = lt.LPTab._lp_card_key(sol.venue, sol.position_id)
    assert sol_key == "orca:solana:FbNHxe9VV797JWG7XH2msjwp5Rvb6dGzndwwkEXXXBKX".lower()
    print("PASS test_canonical_key_normalizes_formats")


def test_fetched_saved_pool_is_not_a_placeholder():
    """A saved pool present in fetched positions must not take the placeholder branch."""
    saved = [
        {"token_id": 76866113, "venue": "Aerodrome", "pair": "EURC/cbBTC"},
    ]
    tab = _new_tab(saved)
    tab._lp_venue_prefix = lambda v: "base"

    pos = _FakePosition("Aerodrome", "base:76866113", "EURC/cbBTC")

    captured = {}

    def _fake_render_card(p):
        captured["rendered"] = p.position_id

    def _fake_placeholder(scroll, entry, prefix, tid, venue, pair, state="auto", error=None):
        captured["placeholder"] = (state, error)

    tab._lp_render_card = _fake_render_card
    tab._lp_render_saved_placeholder = _fake_placeholder
    tab._lp_update_button_states = lambda: None

    # Simulate the in-place update path with a successful fetch.
    tab._lp_update_saved_cards_in_place([pos], errors={})

    assert captured.get("rendered") == "base:76866113", captured
    assert "placeholder" not in captured, f"fetched pool rendered as placeholder: {captured}"
    print("PASS test_fetched_saved_pool_is_not_a_placeholder")


def test_fetching_state_does_not_destroy_existing_cards():
    """The fetching state must append only; existing cards stay."""
    saved = [
        {"token_id": 76866113, "venue": "Aerodrome", "pair": "EURC/cbBTC"},
        {"token_id": 12345, "venue": "HyperEVM", "pair": "WHYPE/UBTC"},
    ]
    tab = _new_tab(saved)
    tab._lp_venue_prefix = lambda v: {"Aerodrome": "base", "HyperEVM": "hyperevm"}.get(v, "bsc")

    # Pre-populate the panel as if a previous scan already rendered G1.
    existing = _FakePosition("HyperEVM", "hyperevm:12345", "WHYPE/UBTC")
    tab._lp_render_card = lambda p: None
    tab._lp_render_saved_placeholder = lambda *a, **k: None
    tab._lp_update_button_states = lambda: None

    destroyed = []

    class _FakeScroll:
        def __init__(self):
            self.children = []
        def winfo_children(self):
            return self.children
        def destroy(self):
            destroyed.append("scroll")

    scroll = _FakeScroll()
    tab._lp_widgets["scroll"] = scroll
    tab._lp_widgets["position_cards"] = {
        tab._lp_card_key(existing.venue, existing.position_id): MagicMock(),
    }

    # Simulate fetching state with an extra position arriving.
    extra = _FakePosition("Aerodrome", "base:76866113", "EURC/cbBTC")
    tab._lp_render_fetching_state(saved, [extra])

    assert not destroyed, "fetching state destroyed the scroll/container"
    assert tab._lp_widgets["position_cards"], "existing card registry was wiped"
    print("PASS test_fetching_state_does_not_destroy_existing_cards")


def main():
    test_canonical_key_normalizes_formats()
    test_fetched_saved_pool_is_not_a_placeholder()
    test_fetching_state_does_not_destroy_existing_cards()
    print("ALL LP CARD KEY TESTS PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
