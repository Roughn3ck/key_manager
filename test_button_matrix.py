"""Fixture tests for the v5.3.30-forge capability-driven button matrix.

These tests assert that every venue/state renders exactly the buttons declared
in `venue_adapters.capabilities` and that a [button-render] trace line is
emitted for every card.
"""
import sys
import tkinter as tk
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent / "src"))

import customtkinter as ctk

from lp_engine import LPPosition
from lp_tab import LPTab
from venue_adapters.hyperliquid_writer import HyperliquidWriter
from venue_adapters.bsc_writer import BSCWriter
from venue_adapters.orca_writer import OrcaWriter
from venue_adapters.cetus_writer import CetusWriter
from venue_adapters.aerodrome_writer import AerodromeWriter


class StubRoot:
    def after(self, ms, fn):
        fn()


class StubKM:
    address_db = {"saved_pools": []}


class StubGui:
    online_mode = False
    key_manager = StubKM()
    current_password = None
    last_note = None
    price_engine = None
    lp_engine = MagicMock()

    def getw(self, v, p):
        writers = {
            "hyperliquid": HyperliquidWriter.__new__(HyperliquidWriter),
            "bsc": BSCWriter.__new__(BSCWriter),
            "orca": OrcaWriter.__new__(OrcaWriter),
            "cetus": CetusWriter.__new__(CetusWriter),
            "aerodrome": AerodromeWriter.__new__(AerodromeWriter),
        }
        w = writers.get(v)
        if w:
            w._unlocked = True
        return w

    lp_engine.get_writer = getw
    lp_engine._writers = {
        "hyperliquid": HyperliquidWriter.__new__(HyperliquidWriter),
        "bsc": BSCWriter.__new__(BSCWriter),
        "orca": OrcaWriter.__new__(OrcaWriter),
        "cetus": CetusWriter.__new__(CetusWriter),
        "aerodrome": AerodromeWriter.__new__(AerodromeWriter),
    }

    def show_notification(self, msg, error=False):
        self.last_note = (msg, error)

    def copy_to_clipboard(self, text):
        pass

    def _format_currency(self, x):
        return "$" + str(x)


def _make_tab():
    gui = StubGui()
    gui.root = StubRoot()
    tab = LPTab(gui)
    tab._lp_widgets["selector_menu"] = type("M", (), {"get": lambda self: "Account"})()
    root = tk.Tk()
    ctk.set_appearance_mode("dark")
    scroll = ctk.CTkScrollableFrame(root)
    tab._lp_widgets["scroll"] = scroll
    tab._lp_widgets["status_label"] = ctk.CTkLabel(root, text="")
    return tab, scroll, StringIO()


def _capture_render(tab, pos):
    old_stdout = sys.stdout
    captured = StringIO()
    sys.stdout = captured
    try:
        tab._lp_render_card(pos)
    finally:
        sys.stdout = old_stdout
    return captured.getvalue()


def _buttons_for_card(scroll, card_index=0):
    """Return the list of CTkButton text values on a rendered card."""
    card = scroll.winfo_children()[card_index]
    queue = list(card.winfo_children())
    buttons = []
    while queue:
        widget = queue.pop(0)
        if isinstance(widget, ctk.CTkButton):
            buttons.append(widget.cget("text"))
        queue.extend(widget.winfo_children())
    return buttons


def test_orca_card_renders_collect_and_close_only():
    tab, scroll, _ = _make_tab()
    pos = LPPosition(
        position_id="solana:FbNHxe9VV797JWG7XH2msjwp5Rvb6dGzndwwkEXXXBKX",
        venue="Orca",
        chain="solana",
        pair="cbBTC/SOL",
        range_low=1,
        range_high=2,
        current_price=1.5,
        position_in_range_pct=50,
        fees_earned={},
        fees_earned_usd=0,
    )
    trace = _capture_render(tab, pos)
    buttons = _buttons_for_card(scroll)
    assert any("Collect" in b for b in buttons), buttons
    assert any("Close" in b for b in buttons), buttons
    assert not any("Compound" in b for b in buttons), buttons
    assert "[button-render]" in trace, trace
    assert "venue=Orca" in trace, trace
    print("PASS test_orca_card_renders_collect_and_close_only")


def test_bsc_card_renders_collect_and_close_only():
    tab, scroll, _ = _make_tab()
    pos = LPPosition(
        position_id="bsc:123",
        venue="BSC",
        chain="bnb chain",
        pair="WBNB/LINK",
        range_low=1,
        range_high=2,
        current_price=1.5,
        position_in_range_pct=50,
        fees_earned={},
        fees_earned_usd=0,
    )
    trace = _capture_render(tab, pos)
    buttons = _buttons_for_card(scroll)
    assert any("Collect" in b for b in buttons), buttons
    assert any("Close" in b for b in buttons), buttons
    assert not any("Compound" in b for b in buttons), buttons
    assert "[button-render]" in trace, trace
    assert "venue=BSC" in trace, trace
    print("PASS test_bsc_card_renders_collect_and_close_only")


def test_cetus_card_renders_all_three_buttons():
    tab, scroll, _ = _make_tab()
    pos = LPPosition(
        position_id="sui:0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
        venue="Cetus",
        chain="sui",
        pair="LBTC/SUI",
        range_low=1,
        range_high=2,
        current_price=1.5,
        position_in_range_pct=50,
        fees_earned={},
        fees_earned_usd=0,
    )
    trace = _capture_render(tab, pos)
    buttons = _buttons_for_card(scroll)
    assert any("Collect" in b for b in buttons), buttons
    assert any("Compound" in b for b in buttons), buttons
    assert any("Close" in b for b in buttons), buttons
    assert "[button-render]" in trace, trace
    assert "venue=Cetus" in trace, trace
    print("PASS test_cetus_card_renders_all_three_buttons")


def test_hyperliquid_card_renders_all_three_buttons():
    tab, scroll, _ = _make_tab()
    pos = LPPosition(
        position_id="hyperevm:1",
        venue="HyperEVM",
        chain="hyperevm",
        pair="WHYPE/UBTC",
        range_low=1,
        range_high=2,
        current_price=1.5,
        position_in_range_pct=50,
        fees_earned={},
        fees_earned_usd=0,
    )
    trace = _capture_render(tab, pos)
    buttons = _buttons_for_card(scroll)
    assert any("Collect" in b for b in buttons), buttons
    assert any("Compound" in b for b in buttons), buttons
    assert any("Close" in b for b in buttons), buttons
    assert "[button-render]" in trace, trace
    assert "venue=HyperEVM" in trace, trace
    print("PASS test_hyperliquid_card_renders_all_three_buttons")


def test_aerodrome_unstaked_card_renders_all_three_buttons():
    tab, scroll, _ = _make_tab()
    pos = LPPosition(
        position_id="base:456",
        venue="Aerodrome",
        chain="base",
        pair="WETH/cbBTC",
        range_low=1,
        range_high=2,
        current_price=1.5,
        position_in_range_pct=50,
        fees_earned={},
        fees_earned_usd=0,
    )
    trace = _capture_render(tab, pos)
    buttons = _buttons_for_card(scroll)
    assert any("Collect" in b for b in buttons), buttons
    assert any("Compound" in b for b in buttons), buttons
    assert any("Close" in b for b in buttons), buttons
    assert "[button-render]" in trace, trace
    assert "venue=Aerodrome" in trace, trace
    print("PASS test_aerodrome_unstaked_card_renders_all_three_buttons")


def test_staked_aerodrome_card_renders_dedicated_buttons_no_compound():
    tab, scroll, _ = _make_tab()
    pos = LPPosition(
        position_id="base:789",
        venue="Aerodrome",
        chain="base",
        pair="WETH/cbBTC",
        range_low=1,
        range_high=2,
        current_price=1.5,
        position_in_range_pct=50,
        fees_earned={},
        fees_earned_usd=0,
        raw_data={"is_staked": True},
    )
    trace = _capture_render(tab, pos)
    buttons = _buttons_for_card(scroll)
    assert any("Claim" in b for b in buttons), buttons
    assert any("Unstake" in b for b in buttons), buttons
    assert any("Close Staked" in b for b in buttons), buttons
    assert not any("Compound" in b for b in buttons), buttons
    assert not any("Collect" in b and "AERO" not in b for b in buttons), buttons
    assert "[button-render]" in trace, trace
    assert "staked" in trace, trace
    print("PASS test_staked_aerodrome_card_renders_dedicated_buttons_no_compound")


def main():
    test_orca_card_renders_collect_and_close_only()
    test_bsc_card_renders_collect_and_close_only()
    test_cetus_card_renders_all_three_buttons()
    test_hyperliquid_card_renders_all_three_buttons()
    test_aerodrome_unstaked_card_renders_all_three_buttons()
    test_staked_aerodrome_card_renders_dedicated_buttons_no_compound()
    print("ALL FORGE BUTTON-MATRIX FIXTURE TESTS PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
