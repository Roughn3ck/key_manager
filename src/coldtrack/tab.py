"""ColdTrack tab UI for the ColdStack GUI.

Displays portfolio overview and account list synced from the vault.
User-initiated sync button bridges vault data to ColdTrack DB.
"""
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import customtkinter as ctk

from coldtrack.db import ColdTrackDB
from coldtrack.importer import ColdTrackImporter


def _truncate_address(addr: str, prefix_len: int = 6, suffix_len: int = 4) -> str:
    """Middle-truncate an address for display.

    EVM: '0x1234...5678'  Solana: '7bEGyi7M...3Xpx'
    No hex assumptions — works for any address format.
    """
    if not addr:
        return ""
    if len(addr) <= prefix_len + suffix_len + 3:
        return addr
    return f"{addr[:prefix_len]}...{addr[-suffix_len:]}"


class ColdTrackTab:
    """ColdTrack tab UI for the ColdStack GUI.

    Displays portfolio overview and account list synced from the vault.
    User-initiated sync button bridges vault data to ColdTrack DB.
    """

    def __init__(self, gui_instance: Any) -> None:
        """
        Args:
            gui_instance: Reference to the main GUI (for accessing address_db,
                          base_dir, online_mode)
        """
        self.gui = gui_instance
        self._widgets: Dict[str, Any] = {}
        self._last_sync: Optional[str] = None

    def create_tab(self, parent: ctk.CTkFrame) -> None:
        """Build the tab UI inside parent_tab (the CTkFrame returned by tabview.add("ColdTrack")).

        Follows the house pattern — LPTab.create_tab() and RailgunTab.create_tab() do exactly this.
        """
        self._build_ui(parent)

    def _build_ui(self, parent: ctk.CTkFrame) -> None:
        """Build the tab layout:
        - Top bar: 'Sync from Vault' button + status label
        - Portfolio overview: cards or summary rows for each portfolio
        - Account list: scrollable frame with accounts grouped by portfolio
        """
        # Main container
        container = ctk.CTkFrame(parent, corner_radius=0)
        container.pack(fill="both", expand=True, padx=10, pady=10)

        # ─── Section: Header ───
        header_frame = ctk.CTkFrame(container, fg_color="transparent")
        header_frame.pack(fill="x", pady=(0, 10))

        ctk.CTkLabel(
            header_frame,
            text="📊 ColdTrack",
            font=ctk.CTkFont(size=18, weight="bold"),
        ).pack(side="left", padx=(0, 10))

        self._widgets["sync_btn"] = ctk.CTkButton(
            header_frame,
            text="Sync from Vault",
            command=self.on_sync_clicked,
            width=140,
            height=32,
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color=("#2196F3", "#1565C0"),
            hover_color=("#1976D2", "#0D47A1"),
        )
        self._widgets["sync_btn"].pack(side="right", padx=(10, 0))

        self._widgets["status_label"] = ctk.CTkLabel(
            header_frame,
            text="",
            font=ctk.CTkFont(size=11),
            text_color=("gray40", "gray60"),
        )
        self._widgets["status_label"].pack(side="right", padx=(0, 10))

        # ─── Section: Portfolio Cards ───
        self._widgets["portfolios_frame"] = ctk.CTkFrame(
            container, corner_radius=8
        )
        self._widgets["portfolios_frame"].pack(fill="x", pady=(0, 10))

        # ─── Section: Accounts ───
        accounts_label = ctk.CTkLabel(
            container,
            text="Accounts",
            font=ctk.CTkFont(size=14, weight="bold"),
        )
        accounts_label.pack(anchor="w", pady=(0, 5))

        self._widgets["accounts_scroll"] = ctk.CTkScrollableFrame(
            container, corner_radius=8
        )
        self._widgets["accounts_scroll"].pack(fill="both", expand=True)

        # Empty state
        self._widgets["empty_label"] = ctk.CTkLabel(
            self._widgets["accounts_scroll"],
            text="No data yet. Click 'Sync from Vault' to import your accounts.",
            font=ctk.CTkFont(size=13),
            text_color=("gray40", "gray60"),
        )
        self._widgets["empty_label"].pack(expand=True, pady=40)

    def on_sync_clicked(self) -> None:
        """Handle 'Sync from Vault' button click."""
        if not self.gui.current_password:
            self.gui.show_notification("Vault not unlocked", error=True)
            return

        address_db = getattr(self.gui.key_manager, "address_db", None)
        if not address_db:
            self.gui.show_notification("Vault data not available", error=True)
            return

        self._widgets["sync_btn"].configure(state="disabled", text="Syncing...")
        self._widgets["status_label"].configure(text="Syncing...")

        def _sync_thread():
            db = None
            try:
                db = ColdTrackDB()
                db.init_schema()
                importer = ColdTrackImporter(db, address_db)
                result = importer.full_sync()
                self._last_sync = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
                self.gui.root.after(0, lambda: self._on_sync_complete(result))
            except Exception as e:
                self.gui.root.after(0, lambda: self._on_sync_error(str(e)))
            finally:
                if db:
                    db.close()

        threading.Thread(target=_sync_thread, daemon=True).start()

    def _on_sync_complete(self, result: Dict[str, int]) -> None:
        """Handle successful sync."""
        self._widgets["sync_btn"].configure(state="normal", text="Sync from Vault")
        self._widgets["status_label"].configure(text=f"Last sync: {self._last_sync}")
        self.gui.show_notification(
            f"ColdTrack sync complete: {result['portfolios']} portfolios, {result['accounts']} accounts"
        )
        self.refresh_display()

    def _on_sync_error(self, error_msg: str) -> None:
        """Handle sync error."""
        self._widgets["sync_btn"].configure(state="normal", text="Sync from Vault")
        self._widgets["status_label"].configure(text="Sync failed")
        self.gui.show_notification(f"ColdTrack sync failed: {error_msg}", error=True)

    def refresh_display(self) -> None:
        """Reload portfolios and accounts from ColdTrack DB and update the UI."""
        db = None
        try:
            db = ColdTrackDB()
            db.init_schema()
            portfolios = db.get_portfolios()
            accounts = db.get_accounts()
            self._render_portfolios(portfolios, db)
            self._render_accounts(accounts)
        except Exception:
            # DB may not exist yet — show empty state
            self._render_portfolios([], None)
            self._render_accounts([])
        finally:
            if db:
                db.close()

    def on_tab_selected(self) -> None:
        """Called when the user switches to the ColdTrack tab."""
        self.refresh_display()

    # ------------------------------------------------------------------
    # Rendering helpers
    # ------------------------------------------------------------------

    def _render_portfolios(
        self, portfolios: List[Dict[str, Any]], db: Optional[ColdTrackDB]
    ) -> None:
        """Render portfolio summary cards."""
        frame = self._widgets.get("portfolios_frame")
        if not frame:
            return
        for widget in frame.winfo_children():
            widget.destroy()

        if not portfolios:
            ctk.CTkLabel(
                frame,
                text="No portfolios synced yet.",
                font=ctk.CTkFont(size=12),
                text_color=("gray40", "gray60"),
            ).pack(padx=15, pady=10)
            return

        for p in portfolios:
            card = ctk.CTkFrame(frame, corner_radius=6)
            card.pack(fill="x", padx=5, pady=3)

            # Portfolio name (bold)
            name = p.get("NAME", "Unknown")
            ctk.CTkLabel(
                card,
                text=name,
                font=ctk.CTkFont(size=14, weight="bold"),
            ).pack(side="left", padx=(10, 5))

            # Type badge
            ptype = p.get("TYPE", "internal")
            badge_color = ("#2196F3", "#1565C0") if ptype == "internal" else ("#FF9800", "#E65100")
            ctk.CTkLabel(
                card,
                text=ptype,
                font=ctk.CTkFont(size=10),
                text_color="white",
                fg_color=badge_color,
                corner_radius=4,
                padx=6,
            ).pack(side="left", padx=5)

            # Reporting currency + tax jurisdiction
            info_parts = []
            if p.get("REPORTING_CURRENCY"):
                info_parts.append(p["REPORTING_CURRENCY"])
            if p.get("TAX_JURISDICTION"):
                info_parts.append(p["TAX_JURISDICTION"])
            if info_parts:
                ctk.CTkLabel(
                    card,
                    text=" | ".join(info_parts),
                    font=ctk.CTkFont(size=11),
                    text_color=("gray40", "gray60"),
                ).pack(side="left", padx=5)

            # Account count
            if db:
                accs = db.get_accounts(p["ID"])
                count = len(accs)
                ctk.CTkLabel(
                    card,
                    text=f"{count} account{'s' if count != 1 else ''}",
                    font=ctk.CTkFont(size=11),
                    text_color=("gray40", "gray60"),
                ).pack(side="right", padx=10)

    def _render_accounts(self, accounts: List[Dict[str, Any]]) -> None:
        """Render the account list."""
        scroll = self._widgets.get("accounts_scroll")
        if not scroll:
            return
        for widget in scroll.winfo_children():
            widget.destroy()

        if not accounts:
            empty = self._widgets.get("empty_label")
            if empty:
                empty.pack(expand=True, pady=40)
            return

        # Group by portfolio
        by_portfolio: Dict[str, List[Dict[str, Any]]] = {}
        for acc in accounts:
            pf_id = acc.get("PORTFOLIO_ID", 0)
            pf_name = f"Portfolio {pf_id}"
            by_portfolio.setdefault(pf_name, []).append(acc)

        for pf_name, accs in sorted(by_portfolio.items()):
            # Portfolio group header
            ctk.CTkLabel(
                scroll,
                text=pf_name,
                font=ctk.CTkFont(size=13, weight="bold"),
            ).pack(anchor="w", padx=10, pady=(10, 5))

            for acc in accs:
                row = ctk.CTkFrame(scroll, fg_color="transparent")
                row.pack(fill="x", padx=15, pady=1)

                # Account name (bold)
                name = acc.get("NAME", "Unknown")
                ctk.CTkLabel(
                    row,
                    text=name,
                    font=ctk.CTkFont(size=12, weight="bold"),
                ).pack(side="left", padx=(0, 8))

                # Type badge
                atype = acc.get("TYPE", "wallet")
                ctk.CTkLabel(
                    row,
                    text=atype,
                    font=ctk.CTkFont(size=9),
                    text_color="white",
                    fg_color=("#4CAF50", "#2E7D32"),
                    corner_radius=3,
                    padx=5,
                ).pack(side="left", padx=(0, 8))

                # Chain
                chain = acc.get("CHAIN", "")
                if chain:
                    ctk.CTkLabel(
                        row,
                        text=chain,
                        font=ctk.CTkFont(size=10),
                        text_color=("#666666", "#AAAAAA"),
                    ).pack(side="left", padx=(0, 8))

                # Address (truncated)
                addr = acc.get("ADDRESS", "")
                if addr:
                    ctk.CTkLabel(
                        row,
                        text=_truncate_address(addr),
                        font=ctk.CTkFont(size=10, family="monospace"),
                        text_color=("#666666", "#AAAAAA"),
                    ).pack(side="left", padx=(0, 8))

                # Platform
                platform = acc.get("PLATFORM", "")
                if platform:
                    ctk.CTkLabel(
                        row,
                        text=platform,
                        font=ctk.CTkFont(size=10),
                        text_color=("#666666", "#AAAAAA"),
                    ).pack(side="left", padx=(0, 8))

                # Privacy shielded indicator
                if acc.get("IS_PRIVACY_SHIELDED"):
                    ctk.CTkLabel(
                        row,
                        text="🛡️",
                        font=ctk.CTkFont(size=12),
                    ).pack(side="left", padx=2)
