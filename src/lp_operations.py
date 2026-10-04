"""ColdStack LP operation controller: capability-driven GUI button rendering.

v5.3.30: this module is the thin facade used by `lp_tab.py`.  It inherits the
capability model from `lp_operation_core` and delegates actual execution to
`lp_operation_runner`.  Keeping rendering separate from execution keeps both
files small and focused.
"""
from typing import Dict, List, Optional

import customtkinter as ctk

from lp_engine import LPPosition
from lp_operation_core import LPOpCore
from lp_operation_runner import LPOperationRunner


class LPOperationController(LPOpCore):
    """GUI-facing controller: render buttons and dispatch to the runner."""

    def __init__(self, lp_tab):
        super().__init__(lp_tab)
        self._runner = LPOperationRunner(lp_tab)

    # ------------------------------------------------------------------
    # Button rendering
    # ------------------------------------------------------------------

    def render_standard_buttons(
        self,
        button_frame: ctk.CTkFrame,
        position: LPPosition,
        tooltip_target: Optional[ctk.CTkLabel] = None,
        caps: Optional[Dict[str, bool]] = None,
    ) -> List[ctk.CTkButton]:
        """Render Collect/Compound/Close buttons based on capabilities.

        Args:
            caps: Optional capability override. If None, capabilities are resolved
                from the position via the capability matrix.
        """
        buttons: List[ctk.CTkButton] = []
        if caps is None:
            caps = self.capabilities(position)
        # Accept a VenueCapabilities dataclass as well as a dict.
        caps_dict = {
            "collect": getattr(caps, "collect", False),
            "compound": getattr(caps, "compound", False),
            "close": getattr(caps, "close", False),
        }

        def _tooltip(btn: ctk.CTkButton, text: str) -> None:
            from lp_liquidity_manager import _add_status_tooltip
            if tooltip_target:
                _add_status_tooltip(btn, tooltip_target, text)

        if caps_dict["collect"]:
            btn = ctk.CTkButton(
                button_frame, text="💰 Collect", width=75, height=24,
                font=ctk.CTkFont(size=9, weight="bold"),
                fg_color=("#fd7e14", "#dc6602"),
                command=lambda pos=position: self._runner.run_collect(pos),
            )
            btn.pack(side="left", padx=(0, 2))
            _tooltip(btn, "Collect Fees")
            buttons.append(btn)

        if caps_dict["compound"]:
            btn = ctk.CTkButton(
                button_frame, text="🔄 Compound", width=85, height=24,
                font=ctk.CTkFont(size=9, weight="bold"),
                fg_color=("#20c997", "#1aa179"),
                command=lambda pos=position: self._runner.run_compound(pos),
            )
            btn.pack(side="left", padx=(0, 2))
            _tooltip(btn, "Compound Fees")
            buttons.append(btn)

        if caps_dict["close"]:
            btn = ctk.CTkButton(
                button_frame, text="✕ Close", width=65, height=24,
                font=ctk.CTkFont(size=9, weight="bold"),
                fg_color=("#6f42c1", "#5a32a3"),
                hover_color=("#5a32a3", "#42288a"),
                command=lambda pos=position: self._runner.run_close(pos),
            )
            btn.pack(side="left", padx=(0, 2))
            _tooltip(btn, "Close Position")
            buttons.append(btn)

        return buttons

    # ------------------------------------------------------------------
    # Backwards-compatible dialog shims
    # ------------------------------------------------------------------

    def run_collect(self, position: LPPosition) -> None:
        self._runner.run_collect(position)

    def run_compound(self, position: LPPosition) -> None:
        self._runner.run_compound(position)

    def run_close(self, position: LPPosition) -> None:
        self._runner.run_close(position)
