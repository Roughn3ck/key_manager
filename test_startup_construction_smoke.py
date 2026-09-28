"""Headless construction smoke test for the v5.3.26 login screen + LP tab.

This test builds the login screen and LP tab without a real GUI event loop,
verifying that widget creation order regressions (like the v5.3.26 login_frame
NameError) are caught before any EXE build.

Run: python test_startup_construction_smoke.py
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent / "src"))

import customtkinter as ctk


def _stub_root():
    root = ctk.CTk()
    root.withdraw()
    return root


def _stub_key_manager():
    km = MagicMock()
    km.address_db = {"accounts": {"G1": {}}}
    km.vault = MagicMock()
    km.vault.name = "test_vault"
    return km


def _stub_gui():
    gui = MagicMock()
    gui.root = _stub_root()
    gui.key_manager = _stub_key_manager()
    gui.online_mode = True
    gui.price_engine = MagicMock()
    gui.lp_engine = MagicMock()
    gui.price_engine.fetch_prices.return_value = {}
    gui.LP_PLATFORM_MAP = {
        "Auto-detect": "auto",
        "HyperEVM": "hyperliquid",
        "Aerodrome": "aerodrome",
        "BSC": "bsc",
        "Orca": "orca",
        "Cetus": "cetus",
    }
    gui.LP_PLATFORM_MAP_reverse = {v: k for k, v in gui.LP_PLATFORM_MAP.items()}
    gui._format_currency = lambda x: f"${x:,.2f}"
    gui.copy_to_clipboard = lambda x: None
    gui.show_notification = lambda *a, **k: None
    return gui


def test_login_screen_construction():
    from gui_main_v5 import VERSION
    import gui_main_v5 as gm

    # Stub out heavy initializers so the test is fast and offline-safe.
    with patch.object(gm, "CryptoEngine", MagicMock), \
         patch.object(gm, "BalanceEngine", MagicMock), \
         patch.object(gm, "PriceEngine", MagicMock), \
         patch.object(gm, "LPEngine", MagicMock), \
         patch.object(gm, "HyperliquidVaultTracker", MagicMock), \
         patch.object(gm, "VaultTab", MagicMock), \
         patch.object(gm, "RailgunTab", MagicMock), \
         patch.object(gm, "ColdTrackTab", MagicMock):
        app = gm.ColdStackGUI()
        app.create_login_screen()

    root = app.root
    children = root.winfo_children()
    assert children, "Login screen should create at least one widget"
    labels = [w for w in children[0].winfo_children()
              if isinstance(w, ctk.CTkLabel)]
    texts = [l.cget("text") for l in labels]
    assert any(VERSION in t for t in texts), f"Version label missing; labels={texts}"
    assert any("Cetus" in t for t in texts), f"Cetus platform missing; labels={texts}"
    root.destroy()
    print("PASS test_login_screen_construction")


def test_lp_tab_construction():
    from lp_tab import LPTab

    root = _stub_root()
    gui = _stub_gui()
    gui.root = root
    tab = LPTab(gui)
    frame = ctk.CTkFrame(root)
    tab.create_tab(frame)

    assert tab is not None
    assert hasattr(tab, "_lp_widgets")
    scroll = tab._lp_widgets.get("scroll")
    assert scroll is not None, "LP tab should create scroll widget"
    root.destroy()
    print("PASS test_lp_tab_construction")


def main():
    test_login_screen_construction()
    test_lp_tab_construction()
    print("ALL STARTUP CONSTRUCTION SMOKE TESTS PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
