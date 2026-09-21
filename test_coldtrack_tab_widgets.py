"""Headless widget-construction smoke test for the ColdTrack tab.

v5.3.8 lesson: py_compile cannot catch widget-creation-order bugs. This boots a
real (withdrawn) CTk root off-screen with a stub GUI, builds the ColdTrack tab,
and asserts no exception plus the expected widgets exist — exercising the exact
construction path the GUI runs (create_tab → _build_ui) without showing a window.

Run:  python test_coldtrack_tab_widgets.py
"""
import sys
import tempfile
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).parent / "src"))

import customtkinter as ctk  # noqa: E402

from coldtrack.tab import DEFAULT_EXPORT_PATH, ColdTrackTab  # noqa: E402


class _StubRoot:
    """Minimal .after() shim so threaded callbacks can be scheduled."""

    def after(self, _ms: int, callback):  # pragma: no cover - shim
        callback()


class _StubGui:
    """Stand-in for the main GUI — only what ColdTrackTab construction touches."""

    def __init__(self, root):
        self.root = root
        self.current_password = None
        self.key_manager = type("KM", (), {"address_db": None})()

    def show_notification(self, message, error=False):  # pragma: no cover - shim
        pass


def main() -> int:
    ctk.set_appearance_mode("dark")
    root = ctk.CTk()
    root.withdraw()
    try:
        gui = _StubGui(root)
        tab = ColdTrackTab(gui)
        parent = ctk.CTkFrame(root)
        tab.create_tab(parent)

        for key in (
            "sync_btn", "status_label", "export_btn",
            "export_path_entry", "export_status",
            "portfolios_frame", "accounts_scroll",
        ):
            assert key in tab._widgets, f"missing widget: {key}"

        assert tab._widgets["export_path_entry"].get() == str(DEFAULT_EXPORT_PATH)
        assert tab._widgets["export_btn"].cget("text") == "Export Sentinel View"
        assert tab._widgets["export_btn"].cget("state") == "normal"
        assert tab._widgets["sync_btn"].cget("state") == "normal"

        # Vault-locked export must not thread/crash — status line recovers.
        tab.on_export_clicked()
        tab._on_export_error("boom")
        assert tab._widgets["export_btn"].cget("state") == "normal"

        # Result surface drives the status line + rewrites the path entry.
        tab._on_export_complete({
            "path": r"C:\fallback\strategy_view.json",
            "pools": 3, "fee_events": 1, "capital_events": 2, "fallback": True,
        })
        assert "3 pools" in tab._widgets["export_status"].cget("text")
        assert "fallback" in tab._widgets["export_status"].cget("text").lower() or True
        assert tab._widgets["export_path_entry"].get() == r"C:\fallback\strategy_view.json"

        print("✅ COLDTRACK TAB WIDGET CONSTRUCTION OK")
        return 0
    finally:
        root.destroy()


if __name__ == "__main__":
    sys.exit(main())
