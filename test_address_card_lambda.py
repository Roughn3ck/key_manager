"""Regression test for the address-card click lambda crash.

The v5.3.27 log showed:

    TypeError: ColdStackGUI.create_address_card.<locals>.<lambda>()
        missing 1 required positional argument: 'e'

This test constructs a ColdStackGUI address card and generates synthetic
<Enter>, <Leave>, and <Button-1> events on every bound widget to ensure the
lambdas accept the event argument and do not raise TypeError.

Run: python test_address_card_lambda.py
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


def _stub_gui():
    gui = MagicMock()
    gui.root = _stub_root()
    gui._balance_labels = {}
    gui.online_mode = True
    gui.copy_to_clipboard = MagicMock()
    return gui


def _walk(w, acc):
    acc.append(w)
    for child in w.winfo_children():
        _walk(child, acc)
    return acc


def test_address_card_event_bindings():
    import gui_main_v5 as gm
    gui = _stub_gui()
    with patch.object(gm, "is_balance_supported", return_value=True), \
         patch.object(gm, "ctk", ctk):
        app = gm.ColdStackGUI()
        app.root = gui.root
        app._balance_labels = gui._balance_labels
        app.copy_to_clipboard = gui.copy_to_clipboard
        # Build a minimal chain_view_container.
        app.chain_view_container = ctk.CTkFrame(app.root)

        address_data = {
            "address": "0x1234567890123456789012345678901234567890",
            "coin": "ETH",
            "chain": "Ethereum",
            "source": "manual",
        }
        app.create_address_card(address_data, 0, "G1")
        card = app.chain_view_container.winfo_children()[0]

    # Generate synthetic events on every bound widget.
    failed = []
    widgets = _walk(card, [])
    for w in widgets:
        for binding in (w.bind() or []):
            try:
                w.event_generate(binding)
            except TypeError as err:
                failed.append(f"{binding}: {err}")

    assert not failed, "Event handlers rejected the event argument:\n" + "\n".join(failed)

    gui.root.destroy()
    print("PASS test_address_card_event_bindings")


def main():
    test_address_card_event_bindings()
    print("ALL ADDRESS CARD LAMBDA TESTS PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
