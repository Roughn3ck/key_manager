"""
LP Positions Tab - Proof of Concept for ColdStack v5.0

This file demonstrates how the LP Positions tab integrates into the existing
CustomTkinter GUI without rebuilding the whole application.

It provides:
  - build_lp_tab_content(...) -> returns a scrollable frame of LP cards
  - a standalone mini GUI that runs with python lp_gui_poc.py

Security:
  - Read-only. No keys are loaded.
  - Online mode is passed in; the Refresh button is disabled when offline.

Version: v5.0 (June 2026)
"""
import sys
import os
import threading
import tkinter as tk
from typing import Any, Callable, List, Optional

# Add src directory to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import customtkinter as ctk

from lp_engine import LPEngine
from price_engine import PriceEngine


def build_lp_tab_content(
    parent: ctk.CTkBaseClass,
    online_mode: bool,
    price_engine: Optional[PriceEngine] = None,
    initial_address: str = "",
    on_copy: Optional[Callable[[str], None]] = None,
) -> ctk.CTkFrame:
    """Build and return the LP Positions tab content.

    Args:
        parent: The widget to pack the tab content into.
        online_mode: Whether ColdStack is currently online.
        price_engine: Shared PriceEngine instance.
        initial_address: Optional default value for the address entry.
        on_copy: Optional callback(address) when Copy is clicked.

    Returns:
        The root frame containing the LP tab (can be packed into a tabview).
    """
    root = ctk.CTkFrame(parent, fg_color="transparent")
    root.pack(fill="both", expand=True, padx=10, pady=10)

    # Top bar: address entry + refresh button
    top_bar = ctk.CTkFrame(root, fg_color="transparent")
    top_bar.pack(fill="x", pady=(0, 10))

    address_label = ctk.CTkLabel(top_bar, text="Wallet / Pool ID:", font=ctk.CTkFont(size=13))
    address_label.pack(side="left", padx=(0, 8))

    address_entry = ctk.CTkEntry(top_bar, width=420, font=ctk.CTkFont(size=12))
    address_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
    address_entry.insert(0, initial_address)

    refresh_btn = ctk.CTkButton(
        top_bar,
        text="Refresh",
        width=100,
        height=30,
        font=ctk.CTkFont(size=12, weight="bold"),
    )
    refresh_btn.pack(side="right")

    # Offline banner
    if not online_mode:
        offline_banner = ctk.CTkLabel(
            root,
            text="🔒 Offline -- Enable Online Mode in Settings to fetch LP positions",
            font=ctk.CTkFont(size=11),
            text_color="gray50",
        )
        offline_banner.pack(fill="x", pady=(0, 10))

    # Scrollable card container
    scroll = ctk.CTkScrollableFrame(root)
    scroll.pack(fill="both", expand=True)

    status_label = ctk.CTkLabel(
        root,
        text="",
        font=ctk.CTkFont(size=11),
        text_color="gray50",
    )
    status_label.pack(fill="x", pady=(5, 0))

    lp_engine = LPEngine(online_mode=online_mode, price_engine=price_engine)

    def _clear_cards():
        for widget in scroll.winfo_children():
            widget.destroy()

    def _render_card(position: Any):
        """Render one LPPosition as a card matching v4.1 address card style."""
        card = ctk.CTkFrame(scroll, corner_radius=10)
        card.pack(fill="x", pady=5, padx=5)

        info = ctk.CTkFrame(card, fg_color="transparent")
        info.pack(side="left", fill="both", expand=True, padx=10, pady=8)

        # Header line: emoji + pair + venue
        header_text = f"{position.health_emoji} {position.pair}  ·  {position.venue}"
        header = ctk.CTkLabel(
            info,
            text=header_text,
            font=ctk.CTkFont(size=14, weight="bold"),
        )
        header.pack(anchor="w")

        # Range / price line
        range_parts = []
        if position.range_low is not None and position.range_high is not None:
            range_parts.append(
                f"Range: {position.range_low:g} – {position.range_high:g}"
            )
        if position.current_price is not None:
            range_parts.append(f"Current: {position.current_price:g}")
        if position.position_in_range_pct is not None:
            range_parts.append(f"Position: {position.position_in_range_pct:.1f}%")
        if range_parts:
            ctk.CTkLabel(
                info,
                text="  ·  ".join(range_parts),
                font=ctk.CTkFont(size=11),
                text_color="gray70",
            ).pack(anchor="w", pady=(2, 0))

        # Fees line
        fees_parts = []
        if position.fees_earned_usd is not None and position.fees_earned_usd != 0:
            fees_parts.append(f"Fees earned: ${position.fees_earned_usd:,.2f}")
        if position.fees_earned:
            for sym, amt in position.fees_earned.items():
                if amt:
                    fees_parts.append(f"{amt:g} {sym}")
        if fees_parts:
            ctk.CTkLabel(
                info,
                text="  ·  ".join(fees_parts),
                font=ctk.CTkFont(size=11),
                text_color="gray70",
            ).pack(anchor="w", pady=(2, 0))

        # Value / PnL line
        value_parts = []
        if position.current_value_usd is not None:
            value_parts.append(f"Value: ${position.current_value_usd:,.2f}")
        if position.pnl_usd is not None:
            sign = "+" if position.pnl_usd >= 0 else ""
            value_parts.append(f"PnL: {sign}${position.pnl_usd:,.2f}")
        if position.pnl_pct is not None:
            sign = "+" if position.pnl_pct >= 0 else ""
            value_parts.append(f"({sign}{position.pnl_pct:.2f}%)")
        if value_parts:
            ctk.CTkLabel(
                info,
                text="  ·  ".join(value_parts),
                font=ctk.CTkFont(size=11),
                text_color="gray70",
            ).pack(anchor="w", pady=(2, 0))

        # Suggestion line
        suggestion_label = ctk.CTkLabel(
            info,
            text=f"Suggestion: {position.suggested_action}",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color={
                "safe": "#51cf94",
                "watch": "#ffd43b",
                "near_edge": "#ff922b",
                "out_of_range": "#ff6b6b",
                "profit_take": "#74c0fc",
            }.get(position.status, "gray70"),
        )
        suggestion_label.pack(anchor="w", pady=(4, 0))

        # Error note (non-fatal)
        if position.error:
            ctk.CTkLabel(
                info,
                text=f"Note: {position.error}",
                font=ctk.CTkFont(size=10),
                text_color="gray50",
            ).pack(anchor="w", pady=(2, 0))

        # Button frame
        button_frame = ctk.CTkFrame(card, fg_color="transparent")
        button_frame.pack(side="right", padx=10, pady=8)

        card_refresh = ctk.CTkButton(
            button_frame,
            text="Refresh",
            width=80,
            height=26,
            font=ctk.CTkFont(size=10),
            state="disabled" if not online_mode else "normal",
        )
        card_refresh.pack(pady=2)

        if on_copy and position.position_id:
            copy_btn = ctk.CTkButton(
                button_frame,
                text="Copy",
                width=80,
                height=26,
                font=ctk.CTkFont(size=10),
                command=lambda pid=position.position_id: on_copy(pid),
            )
            copy_btn.pack(pady=2)

    def _do_fetch():
        address = address_entry.get().strip()
        if not address:
            status_label.configure(text="Enter a wallet or pool address.")
            return
        status_label.configure(text="Fetching...")
        refresh_btn.configure(state="disabled")
        _clear_cards()

        def _fetch_thread():
            try:
                positions = lp_engine.fetch_all_positions(address)
                # Post back to main thread
                root.after(0, lambda: _on_positions_loaded(positions, address))
            except Exception as e:
                root.after(0, lambda: _on_error(str(e)))

        threading.Thread(target=_fetch_thread, daemon=True).start()

    def _on_positions_loaded(positions: List[Any], address: str):
        _clear_cards()
        if not positions:
            no_data = ctk.CTkLabel(
                scroll,
                text=f"No LP positions found for {address}",
                font=ctk.CTkFont(size=13),
                text_color="gray60",
            )
            no_data.pack(pady=20)
        else:
            for pos in positions:
                _render_card(pos)
        status_label.configure(text=f"Last check: {len(positions)} position(s)")
        refresh_btn.configure(state="normal" if online_mode else "disabled")

    def _on_error(message: str):
        _clear_cards()
        err = ctk.CTkLabel(
            scroll,
            text=f"Error: {message}",
            font=ctk.CTkFont(size=12),
            text_color="#ff6b6b",
        )
        err.pack(pady=20)
        status_label.configure(text="Fetch failed")
        refresh_btn.configure(state="normal" if online_mode else "disabled")

    refresh_btn.configure(command=_do_fetch)

    return root


# ---------------------------------------------------------------------------
# Standalone mini GUI for quick manual testing
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("dark-blue")

    root = ctk.CTk()
    root.title("ColdStack LP Positions Tab - PoC")
    root.geometry("900x700")

    tabview = ctk.CTkTabview(root)
    tabview.pack(fill="both", expand=True, padx=10, pady=10)
    tabview.add("Vault")
    tabview.add("Addresses")
    lp_tab = tabview.add("LP Positions")

    # Demo address; in real GUI this would be pulled from selected account.
    demo_address = "0x9858EfFD232B4033E47d90003D41EC34EcaEda94"

    price_engine = PriceEngine()
    build_lp_tab_content(
        parent=lp_tab,
        online_mode=True,
        price_engine=price_engine,
        initial_address=demo_address,
        on_copy=lambda addr: print("Copied:", addr),
    )

    root.mainloop()
