"""Repro test for v5.3.27 #1: saved pools persist during a single-position search.

Scenarios covered:
- A single-position fetch does NOT clear the existing saved-pool cards.
- The in-place rescan uses each saved pool's own bound wallet/account, not the
  top-bar selector's account.
- A genuinely failing pool marks only itself with the real error.
- A rescan that cannot reach a pool (timeout/transient) keeps the existing card
  and adds a quiet "refresh pending" note.

Run: python test_search_persistence.py
"""
import sys
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

# Make background threads run synchronously in tests so root.after() callbacks
# can be flushed with a single root.update() call.
class _ImmediateThread(threading.Thread):
    def start(self):
        self.run()

sys.path.insert(0, str(Path(__file__).parent / "src"))
threading.Thread = _ImmediateThread

import customtkinter as ctk


G1_WALLET = "0x1111111111111111111111111111111111111111"
N1_WALLET = "0x2222222222222222222222222222222222222222"
G2_WALLET = "0x3333333333333333333333333333333333333333"


def _stub_gui(saved_pools=None, selector_account="G2"):
    gui = MagicMock()
    gui.root = ctk.CTk()
    gui.root.withdraw()
    gui.key_manager = MagicMock()
    gui.key_manager.address_db = {
        "saved_pools": saved_pools or [],
        "accounts": {
            "G1": {"addresses": [{"coin": "EVM", "chain": "HYPE", "address": G1_WALLET}]},
            "N1": {"addresses": [{"coin": "EVM", "chain": "BASE", "address": N1_WALLET}]},
            "G2": {"addresses": [{"coin": "EVM", "chain": "BASE", "address": G2_WALLET}]},
        },
    }
    gui.current_password = "testpw"
    gui.show_notification = MagicMock()
    gui.online_mode = True
    gui.lp_engine = MagicMock()
    gui.price_engine = MagicMock()
    gui.copy_to_clipboard = lambda x: None
    gui._format_currency = lambda x: f"${x:.2f}"
    gui.LP_PLATFORM_MAP = {"Aerodrome (BASE)": "aerodrome"}
    gui.LP_PLATFORM_MAP_reverse = {"aerodrome": "Aerodrome (BASE)"}
    return gui


def _make_lp_position(position_id, venue="Aerodrome", pair="PAIR/PAIR"):
    from lp_engine import LPPosition
    return LPPosition(
        position_id=position_id,
        venue=venue,
        chain="BASE" if venue == "Aerodrome" else "HYPE",
        pair=pair,
        pool_id="0x" + "ab" * 20,
        raw_data={"token_id": int(position_id.split(":", 1)[1]), "liquidity": 1},
    )


def _build_tab(saved_pools):
    from lp_tab import LPTab
    gui = _stub_gui(saved_pools)
    tab = LPTab(gui)
    frame = ctk.CTkFrame(gui.root)
    tab.create_tab(frame)
    # Pre-render existing saved-pool cards (simulating the user already on the tab).
    for entry in saved_pools:
        prefix = tab._lp_venue_prefix(entry["venue"])
        pos = _make_lp_position(f"{prefix}:{entry['token_id']}", venue=entry["venue"], pair=entry.get("pair", ""))
        tab._lp_render_card(pos)
    return tab, gui


def test_saved_pools_persist_during_search():
    from lp_engine import LPPosition
    saved = [
        {"token_id": 11, "venue": "HyperEVM", "wallet_address": G1_WALLET, "pair": "WHYPE/UBTC", "account_name": "G1"},
        {"token_id": 22, "venue": "Aerodrome", "wallet_address": N1_WALLET, "pair": "WETH/cbBTC", "account_name": "N1"},
    ]
    tab, gui = _build_tab(saved)
    scroll = tab._lp_widgets["scroll"]
    initial_cards = len(scroll.winfo_children())
    assert initial_cards == 2, f"Expected 2 pre-rendered cards, got {initial_cards}"

    # Top-bar selector is G2 (different from G1/N1 bindings).
    tab._lp_widgets["selector_menu"].set("Account")
    tab._lp_widgets["account_menu"].set("G2")
    tab._lp_widgets["address_entry"].insert(0, G2_WALLET)
    tab._lp_widgets["position_entry"].insert(0, "999")

    # The new G2 position returned by fetch_position.
    new_pos = _make_lp_position("base:999", venue="Aerodrome", pair="AERO/USDC")
    gui.lp_engine.fetch_position.return_value = new_pos

    # Capture which wallet each adapter was asked to use.
    used_wallets: dict = {}

    def fake_hyperliquid(tid, price_engine, wallet_address=""):
        used_wallets[("HyperEVM", tid)] = wallet_address
        return _make_lp_position(f"hyperevm:{tid}", venue="HyperEVM", pair="WHYPE/UBTC")

    def fake_aerodrome(tid, price_engine, wallet_address=""):
        used_wallets[("Aerodrome", tid)] = wallet_address
        return _make_lp_position(f"base:{tid}", venue="Aerodrome", pair="WETH/cbBTC")

    with patch("venue_adapters.hyperliquid_adapter.HyperliquidAdapter") as mock_hype, \
         patch("venue_adapters.aerodrome_adapter.AerodromeAdapter") as mock_aero, \
         patch.object(tab, "_lp_do_fetch") as mock_full_scan, \
         patch.object(tab, "_lp_auto_fetch_all_saved") as mock_auto_fetch:

        inst_hype = MagicMock()
        inst_hype.fetch_evm_position_by_token_id = fake_hyperliquid
        mock_hype.return_value = inst_hype

        inst_aero = MagicMock()
        inst_aero._fetch_position_by_token_id = fake_aerodrome
        inst_aero._find_staked_positions_via_saved_pools.return_value = []
        mock_aero.return_value = inst_aero

        tab._lp_do_fetch_single()
        # tk event loop + thread callbacks need a moment; wait then flush.
        import time
        time.sleep(0.3)
        gui.root.update()

    # Full wallet scan must never be triggered by a single-position fetch.
    mock_full_scan.assert_not_called()
    mock_auto_fetch.assert_not_called()

    # Existing cards were never cleared (scroll still has >= 2 children).
    final_cards = len(scroll.winfo_children())
    assert final_cards >= 3, f"Expected at least 3 cards (2 saved + 1 new), got {final_cards}"

    # Each saved pool refetched with its OWN bound wallet, not G2.
    assert used_wallets.get(("HyperEVM", 11)) == G1_WALLET, used_wallets
    assert used_wallets.get(("Aerodrome", 22)) == N1_WALLET, used_wallets
    assert G2_WALLET not in used_wallets.values(), "Rescan must not use the selector wallet"

    print("PASS test_saved_pools_persist_during_search")


def test_genuine_failure_marks_only_that_card():
    saved = [
        {"token_id": 11, "venue": "HyperEVM", "wallet_address": G1_WALLET, "pair": "WHYPE/UBTC", "account_name": "G1"},
        {"token_id": 22, "venue": "Aerodrome", "wallet_address": N1_WALLET, "pair": "WETH/cbBTC", "account_name": "N1"},
    ]
    tab, gui = _build_tab(saved)
    scroll = tab._lp_widgets["scroll"]

    tab._lp_widgets["selector_menu"].set("Account")
    tab._lp_widgets["account_menu"].set("G2")
    tab._lp_widgets["address_entry"].insert(0, G2_WALLET)
    tab._lp_widgets["position_entry"].insert(0, "999")

    new_pos = _make_lp_position("base:999", venue="Aerodrome", pair="AERO/USDC")
    gui.lp_engine.fetch_position.return_value = new_pos

    real_error = "RPC rejected eth_call for token 22"

    def fake_hyperliquid(tid, price_engine, wallet_address=""):
        return _make_lp_position(f"hyperevm:{tid}", venue="HyperEVM", pair="WHYPE/UBTC")

    def fake_aerodrome(tid, price_engine, wallet_address=""):
        if tid == 22:
            raise RuntimeError(real_error)
        return _make_lp_position(f"base:{tid}", venue="Aerodrome", pair="WETH/cbBTC")

    with patch("venue_adapters.hyperliquid_adapter.HyperliquidAdapter") as mock_hype, \
         patch("venue_adapters.aerodrome_adapter.AerodromeAdapter") as mock_aero:

        inst_hype = MagicMock()
        inst_hype.fetch_evm_position_by_token_id = fake_hyperliquid
        mock_hype.return_value = inst_hype

        inst_aero = MagicMock()
        inst_aero._fetch_position_by_token_id = fake_aerodrome
        inst_aero._find_staked_positions_via_saved_pools.return_value = []
        mock_aero.return_value = inst_aero

        tab._lp_do_fetch_single()
        import time
        time.sleep(0.3)
        gui.root.update()

    def _all_labels(widget):
        texts = []
        if isinstance(widget, ctk.CTkLabel):
            texts.append(widget.cget("text"))
        for child in widget.winfo_children():
            texts.extend(_all_labels(child))
        return texts

    card_texts = [" | ".join(_all_labels(w)) for w in scroll.winfo_children()]

    failed_cards = [t for t in card_texts if "Fetch failed" in t or real_error in t]
    healthy_cards = [t for t in card_texts if "WHYPE/UBTC" in t or "AERO/USDC" in t]
    assert len(failed_cards) == 1, f"Expected 1 failed card, got {len(failed_cards)}: {card_texts}"
    assert any(real_error in t for t in failed_cards), failed_cards
    assert len(healthy_cards) >= 2, f"Expected healthy cards to remain: {card_texts}"

    print("PASS test_genuine_failure_marks_only_that_card")


def test_timeout_keeps_content_with_refresh_pending():
    saved = [
        {"token_id": 11, "venue": "HyperEVM", "wallet_address": G1_WALLET, "pair": "WHYPE/UBTC", "account_name": "G1"},
    ]
    tab, gui = _build_tab(saved)
    scroll = tab._lp_widgets["scroll"]

    tab._lp_widgets["selector_menu"].set("Account")
    tab._lp_widgets["account_menu"].set("G2")
    tab._lp_widgets["address_entry"].insert(0, G2_WALLET)
    tab._lp_widgets["position_entry"].insert(0, "999")

    new_pos = _make_lp_position("base:999", venue="Aerodrome", pair="AERO/USDC")
    gui.lp_engine.fetch_position.return_value = new_pos

    # Simulate rescan returning nothing and no errors for the saved pool.
    with patch.object(tab, "_lp_fetch_all_saved_entries", return_value=([], {})):
        tab._lp_do_fetch_single()
        gui.root.update()

    def _all_labels(widget):
        texts = []
        if isinstance(widget, ctk.CTkLabel):
            texts.append(widget.cget("text"))
        for child in widget.winfo_children():
            texts.extend(_all_labels(child))
        return texts

    card_texts = []
    for widget in scroll.winfo_children():
        card_texts.extend(_all_labels(widget))

    assert any("refresh pending" in t for t in card_texts), f"Expected refresh pending note: {card_texts}"
    assert not any("Fetch failed" in t for t in card_texts), f"Should not show Fetch failed on timeout: {card_texts}"

    print("PASS test_timeout_keeps_content_with_refresh_pending")


def main():
    test_saved_pools_persist_during_search()
    test_genuine_failure_marks_only_that_card()
    test_timeout_keeps_content_with_refresh_pending()
    print("ALL SEARCH-PERSISTENCE TESTS PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
