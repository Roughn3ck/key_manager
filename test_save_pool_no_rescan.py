"""Test that _lp_save_pool persists the saved-pool record and refreshes the card
in place without making any adapter/RPC calls.

Run: python test_save_pool_no_rescan.py
"""
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent / "src"))

import customtkinter as ctk


def _stub_gui():
    gui = MagicMock()
    gui.root = ctk.CTk()
    gui.root.withdraw()
    gui.key_manager = MagicMock()
    gui.key_manager.address_db = {"saved_pools": [], "accounts": {}}
    gui.current_password = "testpw"
    gui.show_notification = MagicMock()
    gui.online_mode = True
    gui.price_engine = MagicMock()
    gui.lp_engine = MagicMock()
    gui.copy_to_clipboard = lambda x: None
    gui._format_currency = lambda x: f"${x:.2f}"
    gui.LP_PLATFORM_MAP = {}
    gui.LP_PLATFORM_MAP_reverse = {}
    return gui


def _make_position(position_id="base:75269474", pair="WETH/cbBTC"):
    from lp_engine import LPPosition
    return LPPosition(
        position_id=position_id,
        venue="Aerodrome",
        chain="BASE",
        pair=pair,
        pool_id="0x70acdf2ad0bf2402c957154f944c19ef4e1cbae1",
        raw_data={"token_id": 75269474, "is_staked": True, "liquidity": 13396883586502},
    )


def test_save_pool_no_rescan():
    from lp_tab import LPTab

    gui = _stub_gui()
    tab = LPTab(gui)
    frame = ctk.CTkFrame(gui.root)
    tab.create_tab(frame)

    pos = _make_position()
    tab._lp_last_fetched_address = "0x8958Bd96896De55bFe31b1A6Eb2B280ebE098509"

    # Render the card so the Save button exists and is tracked.
    tab._lp_render_card(pos)
    cards = tab._lp_widgets["position_cards"]
    assert cards, "Card should be tracked after render"
    key = f"{pos.venue}:{pos.position_id}"
    assert key in cards, f"Card key {key} missing; cards={list(cards.keys())}"
    original_card = cards[key]

    # Spy: ensure no full wallet scan or fee-refresh network call is triggered.
    with patch.object(tab, "_lp_do_fetch") as mock_do_fetch, \
         patch.object(tab, "_lp_auto_fetch_all_saved") as mock_auto_fetch, \
         patch.object(tab, "_lp_refresh_position_fees") as mock_refresh_fees:

        tab._lp_save_pool(pos)

        # No network/re-scan side effects.
        mock_do_fetch.assert_not_called()
        mock_auto_fetch.assert_not_called()
        mock_refresh_fees.assert_not_called()

        # Flush the Tk after queue so the in-place card refresh runs.
        gui.root.update()

        # Record persisted.
        saved = gui.key_manager.address_db["saved_pools"]
        assert len(saved) == 1, f"Expected 1 saved pool, got {len(saved)}"
        entry = saved[0]
        assert entry["token_id"] == 75269474
        assert entry["venue"] == "Aerodrome"
        assert entry["pair"] == "WETH/cbBTC"
        assert entry["wallet_address"] == tab._lp_last_fetched_address
        gui.key_manager.save_encrypted_data.assert_called()

        # Card re-rendered in place: key still present, widget replaced.
        assert key in tab._lp_widgets["position_cards"]
        assert tab._lp_widgets["position_cards"][key] is not original_card
        assert not original_card.winfo_exists()

        # Status shows Saved confirmation.
        status = tab._lp_widgets.get("status_label")
        assert status is not None
        status_text = status.cget("text")
        assert "Saved" in status_text and "✓" in status_text, f"Status={status_text}"

        print("PASS test_save_pool_no_rescan")


def test_save_existing_pool_noop():
    """Saving a pool that is already saved should not re-render or rescan."""
    from lp_tab import LPTab

    gui = _stub_gui()
    tab = LPTab(gui)
    frame = ctk.CTkFrame(gui.root)
    tab.create_tab(frame)

    pos = _make_position()
    tab._lp_last_fetched_address = pos.pool_id or "0x8958Bd96896De55bFe31b1A6Eb2B280ebE098509"
    gui.key_manager.address_db["saved_pools"].append({
        "token_id": 75269474,
        "venue": "Aerodrome",
        "wallet_address": tab._lp_last_fetched_address,
        "pool_address": pos.pool_id,
        "pair": pos.pair,
    })

    with patch.object(tab, "_lp_do_fetch") as mock_do_fetch, \
         patch.object(tab, "_lp_update_card_saved_state") as mock_update:
        tab._lp_save_pool(pos)
        mock_do_fetch.assert_not_called()
        mock_update.assert_not_called()

    print("PASS test_save_existing_pool_noop")


def main():
    test_save_pool_no_rescan()
    test_save_existing_pool_noop()
    print("ALL SAVE-POOL NO-RESCAN TESTS PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
