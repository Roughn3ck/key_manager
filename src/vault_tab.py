"""ColdStack Hyperliquid Vaults tab (extracted from gui_main_v5.py in v5.1.4)."""
import threading
from datetime import datetime, timezone

import customtkinter as ctk

from lp_engine import OfflineError
from vault_tracker import HyperliquidVaultTracker, VaultPosition


class VaultTab:
    """VaultTab extracted from ColdStackGUI."""

    def __init__(self, gui):
        self.gui = gui
        self._vault_widgets = {}

    def create_section(self, parent):
        """Build the Hyperliquid Vaults section inside the Vault tab.

        Placed below the existing two-panel (accounts + chain view) layout.
        Provides a wallet address entry, a refresh button, and a scrollable
        card container — mirroring the LP tab pattern.
        """
        section = ctk.CTkFrame(parent, corner_radius=0)
        section.pack(fill="both", expand=True, side="top", pady=(0, 0))

        # Section header
        header_frame = ctk.CTkFrame(section, fg_color="transparent")
        header_frame.pack(fill="x", padx=10, pady=(2, 2))

        ctk.CTkLabel(
            header_frame,
            text="\U0001F3DB Hyperliquid Vaults",
            font=ctk.CTkFont(size=15, weight="bold"),
        ).pack(side="left")

        vault_refresh_btn = ctk.CTkButton(
            header_frame, text="Refresh Vaults", width=110, height=28,
            font=ctk.CTkFont(size=12, weight="bold"),
            command=self._vault_do_fetch,
        )
        vault_refresh_btn.pack(side="right")
        self._vault_widgets["refresh_btn"] = vault_refresh_btn

        ctk.CTkButton(
            header_frame, text="Explore Vaults", width=120, height=28,
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color=("#6f42c1", "#5a32a3"),
            hover_color=("#5a32a3", "#42288a"),
            command=self._vault_explore,
        ).pack(side="right", padx=(5, 0))

        # Wallet selector bar: Address or Account mode
        wallet_bar = ctk.CTkFrame(section, fg_color="transparent")
        wallet_bar.pack(fill="x", padx=10, pady=(2, 5))

        # Mode selector dropdown
        selector_menu = ctk.CTkOptionMenu(
            wallet_bar, values=["Address", "Account"], width=90,
            font=ctk.CTkFont(size=11),
            command=self._vault_on_selector_change,
        )
        selector_menu.set(self.gui.vault_selector_mode if hasattr(self, 'vault_selector_mode') else "Account")
        selector_menu.pack(side="left", padx=(0, 5))
        self._vault_widgets["selector_menu"] = selector_menu

        # Address entry (shown when "Address" mode is selected)
        vault_addr_entry = ctk.CTkEntry(wallet_bar, width=320,
                                        font=ctk.CTkFont(size=11))
        self._vault_widgets["address_entry"] = vault_addr_entry

        # Account dropdown (shown when "Account" mode is selected)
        account_names = []
        if self.gui.key_manager:
            account_names = sorted(
                self.gui.key_manager.address_db.get("accounts", {}).keys()
            )
        account_menu = ctk.CTkOptionMenu(
            wallet_bar, values=account_names if account_names else ["(no accounts)"],
            width=200, font=ctk.CTkFont(size=11),
            command=self._vault_on_account_change,
        )
        self._vault_widgets["account_menu"] = account_menu

        # Pack the appropriate widget based on current mode
        self._vault_apply_selector_mode()

        # Deposit to Vault bar
        deposit_bar = ctk.CTkFrame(section, fg_color="transparent")
        deposit_bar.pack(fill="x", padx=10, pady=(2, 5))

        self._vault_widgets["vault_addr_entry"] = ctk.CTkEntry(
            deposit_bar, width=320,
            placeholder_text="Paste vault address (0x...)",
            font=ctk.CTkFont(size=11),
        )

        ctk.CTkButton(
            deposit_bar, text="Deposit to Vault", width=130, height=28,
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color=("#20c997", "#1aa179"),
            hover_color=("#1aa179", "#158f63"),
            command=self._vault_deposit_dialog,
        ).pack(side="right")
        self._vault_widgets["vault_addr_entry"].pack(side="left", fill="x", expand=True, padx=(0, 5))

        # Offline banner
        self._vault_widgets["offline_banner"] = ctk.CTkLabel(
            section,
            text="\U0001F512 Offline -- Enable Online Mode in Settings to fetch Hyperliquid vaults",
            font=ctk.CTkFont(size=11), text_color="gray50",
        )
        if not self.gui.online_mode:
            self._vault_widgets["offline_banner"].pack(fill="x", padx=10, pady=(0, 5))

        # Scrollable card container (fixed height so it doesn't eat the whole tab)
        vault_scroll = ctk.CTkScrollableFrame(section)
        vault_scroll.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self._vault_widgets["scroll"] = vault_scroll

        # Status label
        vault_status = ctk.CTkLabel(
            section, text="", font=ctk.CTkFont(size=11), text_color="gray50",
        )
        vault_status.pack(fill="x", padx=10, pady=(0, 8))
        self._vault_widgets["status_label"] = vault_status

        self._vault_update_online_state()

    def _vault_update_online_state(self):
        """Enable/disable vault section widgets based on online_mode."""
        if not self._vault_widgets:
            return
        refresh_btn = self._vault_widgets.get("refresh_btn")
        if refresh_btn:
            refresh_btn.configure(
                state="normal" if self.gui.online_mode else "disabled"
            )
        offline_banner = self._vault_widgets.get("offline_banner")
        if offline_banner:
            try:
                if self.gui.online_mode:
                    offline_banner.pack_forget()
                else:
                    offline_banner.pack(fill="x", padx=10, pady=(0, 5))
            except Exception:
                pass

    def _vault_apply_selector_mode(self):
        """Show/hide address entry vs account menu based on selector mode."""
        selector = self._vault_widgets.get("selector_menu")
        entry = self._vault_widgets.get("address_entry")
        account_menu = self._vault_widgets.get("account_menu")
        if not selector or not entry:
            return
        mode = selector.get()
        if mode == "Account":
            entry.pack_forget()
            if account_menu:
                account_menu.pack(side="left", fill="x", expand=True, padx=(0, 5))
        else:
            if account_menu:
                account_menu.pack_forget()
            entry.pack(side="left", fill="x", expand=True, padx=(0, 5))

    def _vault_on_selector_change(self, choice: str):
        """Handle Address/Account mode switch."""
        self.gui.vault_selector_mode = choice
        self._vault_apply_selector_mode()
        # Save config
        if self.gui.key_manager and self.gui.current_password:
            cfg = self.gui.key_manager.address_db.setdefault("config", {})
            cfg["vault_selector_mode"] = choice
            self.gui.key_manager.save_encrypted_data(self.gui.current_password)
        # If Account mode, try to resolve and render
        if choice == "Account":
            account_menu = self._vault_widgets.get("account_menu")
            if account_menu:
                acct = account_menu.get()
                if acct and acct != "(no accounts)":
                    self._vault_on_account_change(acct)
        else:
            # Address mode: render saved vaults for current address
            entry = self._vault_widgets.get("address_entry")
            if entry:
                addr = entry.get().strip()
                if addr:
                    self._vault_render_saved_only(addr)

    def _vault_on_account_change(self, choice: str):
        """Handle account selection from dropdown."""
        if not choice or choice == "(no accounts)":
            return
        self.gui.vault_selected_account = choice
        addr = self._vault_resolve_account_address(choice)
        entry = self._vault_widgets.get("address_entry")
        if entry and addr:
            entry.delete(0, "end")
            entry.insert(0, addr)
            # Save config
            if self.gui.key_manager and self.gui.current_password:
                cfg = self.gui.key_manager.address_db.setdefault("config", {})
                cfg["vault_selected_account"] = choice
                self.gui.key_manager.save_encrypted_data(self.gui.current_password)
            # Render saved vaults
            self._vault_render_saved_only(addr)

    def _vault_resolve_account_address(self, account_name: str) -> str:
        """Resolve an account name to its first EVM/HYPE address."""
        if not self.gui.key_manager:
            return ""
        accounts_data = self.gui.key_manager.address_db.get("accounts", {})
        addresses = accounts_data.get(account_name, {}).get("addresses", [])
        for addr in addresses:
            coin = addr.get("coin", "").lower()
            chain = addr.get("chain", "").lower()
            if "evm" in coin or "evm" in chain or "hype" in coin or "hype" in chain:
                return addr.get("address", "")
        return ""

    def _vault_explore(self):
        """Open the Hyperliquid vaults page in the user's default browser."""
        import webbrowser
        webbrowser.open("https://app.hyperliquid.xyz/vaults")

    def _vault_deposit_dialog(self):
        """Open a deposit dialog for the vault address in the entry."""
        if not self.gui.online_mode:
            self.gui.show_notification("Offline - enable Online Mode in Settings", error=True)
            return
        entry = self._vault_widgets.get("vault_addr_entry")
        if not entry:
            return
        vault_address = entry.get().strip()
        if not vault_address or not vault_address.startswith("0x") or len(vault_address) != 42:
            self.gui.show_notification("Enter a valid vault address (0x...)", error=True)
            return
        # Resolve the current wallet address and account name
        wallet_address = self._vault_get_current_wallet_address()
        if not wallet_address:
            self.gui.show_notification("Select or enter a wallet address first", error=True)
            return
        account_name = self._vault_get_current_account_name()
        if not account_name:
            self.gui.show_notification("Could not resolve vault account for this address", error=True)
            return
        # Open the deposit dialog
        from vault_deposit_dialog import VaultDepositDialog
        VaultDepositDialog(
            root=self.gui.root,
            agent_url=self.gui._get_agent_url(),
            account_name=account_name,
            wallet_address=wallet_address,
            vault_address=vault_address,
            show_notification=self.gui.show_notification,
        )

    def _vault_get_current_wallet_address(self):
        """Get the wallet address currently shown in the vault tab."""
        entry = self._vault_widgets.get("address_entry")
        if entry:
            addr = entry.get().strip()
            if addr:
                return addr
        # Try resolving from account dropdown
        selector = self._vault_widgets.get("selector_menu")
        if selector and selector.get() == "Account":
            account_menu = self._vault_widgets.get("account_menu")
            if account_menu:
                acct = account_menu.get()
                if acct and acct != "(no accounts)":
                    return self._vault_resolve_account_address(acct)
        return ""

    def _vault_get_current_account_name(self):
        """Get the vault account name for the current wallet."""
        selector = self._vault_widgets.get("selector_menu")
        if selector and selector.get() == "Account":
            account_menu = self._vault_widgets.get("account_menu")
            if account_menu:
                acct = account_menu.get()
                if acct and acct != "(no accounts)":
                    return acct
        # Try to find account name from address
        addr = self._vault_get_current_wallet_address()
        if addr and self.gui.key_manager:
            for acct_name, acct_data in self.gui.key_manager.address_db.get("accounts", {}).items():
                for a in acct_data.get("addresses", []):
                    if a.get("address", "").lower() == addr.lower():
                        return acct_name
        return ""

    def _vault_restore_state(self):
        """Restore vault tab state from config after login."""
        if not self._vault_widgets:
            return
        selector = self._vault_widgets.get("selector_menu")
        if not selector:
            return
        mode = getattr(self, 'vault_selector_mode', 'Account')
        selector.set(mode)
        self._vault_apply_selector_mode()
        if mode == "Account":
            account_menu = self._vault_widgets.get("account_menu")
            if account_menu:
                acct = getattr(self, 'vault_selected_account', '')
                if acct:
                    # Refresh account list
                    account_names = []
                    if self.gui.key_manager:
                        account_names = sorted(
                            self.gui.key_manager.address_db.get("accounts", {}).keys()
                        )
                    if account_names:
                        account_menu.configure(values=account_names)
                    if acct in account_names:
                        account_menu.set(acct)
                        self._vault_on_account_change(acct)
        else:
            # Address mode: restore any previously entered address
            entry = self._vault_widgets.get("address_entry")
            if entry:
                addr = entry.get().strip()
                if addr:
                    self._vault_render_saved_only(addr)
        # Always show all saved vaults immediately on unlock regardless of wallet
        self._vault_render_saved_only("")

    def _vault_prefill_address(self, account_name: str):
        """Pre-fill the vault section address entry with the account's EVM/HYPE address.

        Also renders any saved vaults for that address from the encrypted vault
        so the user sees cached data immediately on tab load.
        """
        if not self._vault_widgets or not self.gui.key_manager:
            return
        # Refresh account dropdown with current accounts
        account_menu = self._vault_widgets.get("account_menu")
        if account_menu:
            account_names = sorted(
                self.gui.key_manager.address_db.get("accounts", {}).keys()
            )
            if account_names:
                account_menu.configure(values=account_names)
        # Restore saved selector state
        self._vault_restore_state()
        # If restore didn't resolve an address, try the selected account
        entry = self._vault_widgets.get("address_entry")
        if not entry:
            return
        if entry.get().strip():
            return  # Already has an address from restore
        accounts_data = self.gui.key_manager.address_db.get("accounts", {})
        addresses = accounts_data.get(account_name, {}).get("addresses", [])
        for addr in addresses:
            coin = addr.get("coin", "").lower()
            chain = addr.get("chain", "").lower()
            if "evm" in coin or "evm" in chain or "hype" in coin or "hype" in chain:
                wallet_addr = addr.get("address", "")
                entry.delete(0, "end")
                entry.insert(0, wallet_addr)
                # Render saved vaults from cache immediately
                if wallet_addr:
                    self._vault_render_saved_only(wallet_addr)
                return

    def _vault_do_fetch(self):
        """Fetch Hyperliquid vault positions for the entered address (threaded)."""
        if not self.gui.vault_tracker or not self.gui.online_mode:
            self.gui.show_notification("Offline - enable Online Mode in Settings", error=True)
            return
        entry = self._vault_widgets.get("address_entry")
        if not entry:
            return
        address = entry.get().strip()
        if not address:
            status = self._vault_widgets.get("status_label")
            if status:
                status.configure(text="Enter a wallet address to fetch vaults.")
            return

        status = self._vault_widgets.get("status_label")
        refresh_btn = self._vault_widgets.get("refresh_btn")
        scroll = self._vault_widgets.get("scroll")
        if status:
            status.configure(text="Fetching vaults...")
        if refresh_btn:
            refresh_btn.configure(state="disabled")
        if scroll:
            for widget in scroll.winfo_children():
                widget.destroy()

        def _fetch_thread():
            try:
                positions = self.gui.vault_tracker.fetch_positions(address)
                self.gui.root.after(0, lambda: self._vault_on_loaded(positions, address))
            except Exception as e:
                self.gui.root.after(0, lambda: self._vault_on_error(str(e)))

        threading.Thread(target=_fetch_thread, daemon=True).start()

    def _vault_on_loaded(self, positions, address):
        """Render fetched vault positions as cards."""
        scroll = self._vault_widgets.get("scroll")
        status = self._vault_widgets.get("status_label")
        refresh_btn = self._vault_widgets.get("refresh_btn")
        if not scroll:
            return
        for widget in scroll.winfo_children():
            widget.destroy()

        # Filter out the "error" sentinel position (from failed userVaultEquities)
        real_positions = [p for p in positions if not (p.vault_address == "" and p.error)]

        # v5.1: Merge in saved vaults that weren't returned by the API
        if self.gui.key_manager:
            saved_vaults = self.gui.key_manager.address_db.get("saved_vaults", [])
            if isinstance(saved_vaults, list):
                api_vault_addrs = {p.vault_address.lower() for p in real_positions}
                for entry in saved_vaults:
                    if not isinstance(entry, dict):
                        continue
                    if entry.get("wallet_address", "").lower() != address.lower():
                        continue
                    va = entry.get("vault_address", "")
                    if va and va.lower() not in api_vault_addrs:
                        saved_pos = VaultPosition.from_saved_dict(entry)
                        real_positions.append(saved_pos)

        if not real_positions:
            # Check if we got an error sentinel
            error_positions = [p for p in positions if p.error and p.vault_address == ""]
            if error_positions:
                ctk.CTkLabel(
                    scroll, text=f"Error: {error_positions[0].error}",
                    font=ctk.CTkFont(size=12), text_color="#ff6b6b",
                ).pack(pady=20)
            else:
                ctk.CTkLabel(
                    scroll,
                    text=f"No vault positions found for {address}",
                    font=ctk.CTkFont(size=13), text_color="gray60",
                ).pack(pady=20)
        else:
            for pos in real_positions:
                self._vault_render_card(pos)

        if status:
            count = len(real_positions)
            status.configure(text=f"Last check: {count} vault position(s)")
        if refresh_btn:
            refresh_btn.configure(
                state="normal" if self.gui.online_mode else "disabled"
            )

    def _vault_on_error(self, message: str):
        """Show an error in the vault section."""
        scroll = self._vault_widgets.get("scroll")
        status = self._vault_widgets.get("status_label")
        refresh_btn = self._vault_widgets.get("refresh_btn")
        if scroll:
            for widget in scroll.winfo_children():
                widget.destroy()
            ctk.CTkLabel(
                scroll, text=f"Error: {message}",
                font=ctk.CTkFont(size=12), text_color="#ff6b6b",
            ).pack(pady=20)
        if status:
            status.configure(text="Fetch failed")
        if refresh_btn:
            refresh_btn.configure(
                state="normal" if self.gui.online_mode else "disabled"
            )

    def _vault_render_card(self, position: VaultPosition):
        """Render one VaultPosition as a card matching LP card style."""
        scroll = self._vault_widgets.get("scroll")
        if not scroll:
            return

        card = ctk.CTkFrame(scroll, corner_radius=10)
        card.pack(fill="x", pady=4, padx=5)
        card._vault_address = position.vault_address

        info = ctk.CTkFrame(card, fg_color="transparent")
        info.pack(side="left", fill="both", expand=True, padx=10, pady=8)

        # Heading: vault name (large bold font - same as old address font)
        header = f"\U0001F3DB {position.vault_name}"
        ctk.CTkLabel(
            info, text=header,
            font=ctk.CTkFont(size=14, weight="bold"),
        ).pack(anchor="w")

        # Sub-heading: vault address (smaller font - same as old Deposited font)
        if position.vault_address:
            ctk.CTkLabel(
                info, text=position.vault_address,
                font=ctk.CTkFont(size=10), text_color="gray50",
            ).pack(anchor="w", pady=(2, 0))

        # Metrics line: APR · TVL · Vault age · Deposit age
        metric_parts = []
        if position.apr is not None:
            metric_parts.append(f"APR: {position.apr:.1f}%")
        else:
            metric_parts.append("APR: see Hyperliquid")
        if position.tvl_usd is not None:
            metric_parts.append(f"TVL: {self.gui._format_currency(position.tvl_usd)}")
        # Vault age: from first_deposit_time (earliest portfolio timestamp)
        if position.first_deposit_time is not None:
            delta = datetime.now(timezone.utc) - position.first_deposit_time
            days = delta.days
            hours = delta.seconds // 3600
            metric_parts.append(f"Vault age: {days}d {hours}h")
        else:
            metric_parts.append("Vault age: unknown")
        # Deposit age: from user_deposit_time (user's personal vaultEntryTime)
        if position.user_deposit_time is not None:
            delta = datetime.now(timezone.utc) - position.user_deposit_time
            days = delta.days
            hours = delta.seconds // 3600
            metric_parts.append(f"Deposit age: {days}d {hours}h")
        else:
            metric_parts.append("Deposit age: unknown")
        if metric_parts:
            ctk.CTkLabel(
                info, text="  \u00b7  ".join(metric_parts),
                font=ctk.CTkFont(size=11), text_color="gray70",
            ).pack(anchor="w", pady=(2, 0))

        # Deposited + Current value
        value_parts = []
        if position.deposited_usd is not None:
            value_parts.append(f"Deposited: {self.gui._format_currency(position.deposited_usd)}")
        if position.current_value_usd is not None:
            value_parts.append(f"Current: {self.gui._format_currency(position.current_value_usd)}")
        if value_parts:
            ctk.CTkLabel(
                info, text="  \u00b7  ".join(value_parts),
                font=ctk.CTkFont(size=11), text_color="gray70",
            ).pack(anchor="w", pady=(2, 0))

        # P&L (APR moved to metrics line above)
        pnl_parts = []
        if position.unrealized_pnl_usd is not None:
            sign = "+" if position.unrealized_pnl_usd >= 0 else ""
            pnl_parts.append(f"P&L: {sign}{self.gui._format_currency(position.unrealized_pnl_usd)}")
        if position.unrealized_pnl_pct is not None:
            sign = "+" if position.unrealized_pnl_pct >= 0 else ""
            pnl_parts.append(f"({sign}{position.unrealized_pnl_pct:.2f}%)")
        if pnl_parts:
            ctk.CTkLabel(
                info, text="  \u00b7  ".join(pnl_parts),
                font=ctk.CTkFont(size=11), text_color="gray70",
            ).pack(anchor="w", pady=(2, 0))

        # Shares + Share price
        share_parts = []
        if position.shares is not None:
            share_parts.append(f"Shares: {position.shares:,.2f}")
        if position.share_price_usd is not None:
            share_parts.append(f"Share price: ${position.share_price_usd:.4f}")
        if share_parts:
            ctk.CTkLabel(
                info, text="  \u00b7  ".join(share_parts),
                font=ctk.CTkFont(size=11), text_color="gray70",
            ).pack(anchor="w", pady=(2, 0))

        # Error note
        if position.error:
            ctk.CTkLabel(
                info, text=f"Note: {position.error}",
                font=ctk.CTkFont(size=10), text_color="#ff922b",
            ).pack(anchor="w", pady=(2, 0))

        # Debug: log raw_data if vault name fell back to address-based placeholder
        if position.vault_name.startswith("Vault 0x") or position.vault_name == "Unknown Vault":
            print(f"[vault] Name fallback for {position.vault_address}: raw_data={position.raw_data}")

        # Button frame
        button_frame = ctk.CTkFrame(card, fg_color="transparent")
        button_frame.pack(side="right", padx=10, pady=8)
        button_frame._is_button_frame = True

        if position.vault_address:
            ctk.CTkButton(
                button_frame, text="Copy Address", width=100, height=26,
                font=ctk.CTkFont(size=10),
                command=lambda addr=position.vault_address: self.gui.copy_to_clipboard(addr),
            ).pack(pady=2)

        # v5.1: Save Vault / Delete Saved button
        wallet_addr = ""
        entry = self._vault_widgets.get("address_entry")
        if entry:
            wallet_addr = entry.get().strip()

        is_saved = self._vault_is_saved(wallet_addr, position.vault_address)

        # Detect if this is a cached/saved-only position (no live data from API)
        is_cached = (not position.deposited_usd and not position.current_value_usd and is_saved)

        # Show "Saved (cached)" badge for saved-only vaults (no live data)
        if is_cached:
            ctk.CTkLabel(
                info, text="\U0001F516 Saved (cached)",
                font=ctk.CTkFont(size=9), text_color="#0d6efd",
            ).pack(anchor="w", pady=(2, 0))

        if is_saved:
            del_btn = ctk.CTkButton(
                button_frame, text="Delete Saved", width=100, height=26,
                font=ctk.CTkFont(size=10),
                fg_color=("#dc3545", "#c82333"),
                hover_color=("#c82333", "#a71d2a"),
                command=lambda pos=position: self._vault_delete_saved(pos),
            )
            del_btn.pack(pady=2)
            del_btn._save_delete_btn = True
            # Show Refresh button for cached (saved-only) vaults
            if is_cached:
                ctk.CTkButton(
                    button_frame, text="Refresh", width=100, height=26,
                    font=ctk.CTkFont(size=10),
                    fg_color=("#17a2b8", "#138496"),
                    hover_color=("#138496", "#117a8b"),
                    command=lambda pos=position: self._vault_refresh_saved(pos),
                ).pack(pady=2)
        else:
            save_btn = ctk.CTkButton(
                button_frame, text="Save Vault", width=100, height=26,
                font=ctk.CTkFont(size=10),
                fg_color=("#0d6efd", "#0b5ed7"),
                hover_color=("#0b5ed7", "#0a58ca"),
                command=lambda pos=position: self._vault_save_vault(pos),
            )
            save_btn.pack(pady=2)
            save_btn._save_delete_btn = True

    def _vault_is_saved(self, wallet_address: str, vault_address: str) -> bool:
        """Check if a vault is saved in the encrypted vault (vault-address only)."""
        if not self.gui.key_manager:
            return False
        saved = self.gui.key_manager.address_db.get("saved_vaults", [])
        if not isinstance(saved, list):
            return False
        vl = vault_address.lower()
        return any(
            isinstance(e, dict)
            and e.get("vault_address", "").lower() == vl
            for e in saved
        )

    def _vault_save_vault(self, position: VaultPosition):
        """Save a vault to the encrypted vault's saved_vaults list."""
        if not self.gui.key_manager or not self.gui.current_password:
            self.gui.show_notification("Vault not unlocked", error=True)
            return
        entry = self._vault_widgets.get("address_entry")
        wallet_address = entry.get().strip() if entry else ""
        if not wallet_address:
            self.gui.show_notification("Enter a wallet address first", error=True)
            return
        # Ensure wallet_address is set on the position
        if not position.wallet_address:
            position.wallet_address = wallet_address
        # Check for duplicate
        if self._vault_is_saved(wallet_address, position.vault_address):
            self.gui.show_notification("Vault already saved")
            return
        saved_list = self.gui.key_manager.address_db.setdefault("saved_vaults", [])
        if not isinstance(saved_list, list):
            saved_list = []
            self.gui.key_manager.address_db["saved_vaults"] = saved_list
        saved_list.append(position.to_saved_dict())
        ok = self.gui.key_manager.save_encrypted_data(self.gui.current_password)
        if ok:
            self.gui.show_notification(f"Vault saved: {position.vault_name}")
            # Update the Save/Delete button on the existing card without re-fetching
            self._vault_update_save_button(position)
        else:
            self.gui.show_notification("Failed to save vault", error=True)

    def _vault_delete_saved(self, position: VaultPosition):
        """Remove a vault from the encrypted vault's saved_vaults list."""
        if not self.gui.key_manager or not self.gui.current_password:
            self.gui.show_notification("Vault not unlocked", error=True)
            return
        entry = self._vault_widgets.get("address_entry")
        wallet_address = entry.get().strip() if entry else ""
        if not wallet_address:
            self.gui.show_notification("Enter a wallet address first", error=True)
            return
        saved_list = self.gui.key_manager.address_db.get("saved_vaults", [])
        if not isinstance(saved_list, list):
            self.gui.show_notification("No saved vaults to delete", error=True)
            return
        vl = position.vault_address.lower()
        original_len = len(saved_list)
        self.gui.key_manager.address_db["saved_vaults"] = [
            e for e in saved_list
            if not (isinstance(e, dict) and e.get("vault_address", "").lower() == vl)
        ]
        if len(self.gui.key_manager.address_db["saved_vaults"]) == original_len:
            self.gui.show_notification("Vault not found in saved list", error=True)
            return
        ok = self.gui.key_manager.save_encrypted_data(self.gui.current_password)
        if ok:
            self.gui.show_notification(f"Deleted saved vault: {position.vault_name}")
            self._vault_update_save_button(position)
        else:
            self.gui.show_notification("Failed to delete saved vault", error=True)

    def _vault_update_save_button(self, position: VaultPosition):
        """Update the Save/Delete button on an existing vault card without re-fetching."""
        scroll = self._vault_widgets.get("scroll")
        if not scroll:
            return
        # Find the card for this vault by vault_address
        target_card = None
        for widget in scroll.winfo_children():
            # Cards are CTkFrame; check if they have a matching vault_address attribute
            if hasattr(widget, "_vault_address") and widget._vault_address == position.vault_address:
                target_card = widget
                break
        if not target_card:
            return
        # Find the button frame (right side) and update the Save/Delete button
        for child in target_card.winfo_children():
            if isinstance(child, ctk.CTkFrame) and hasattr(child, "_is_button_frame"):
                # Destroy existing Save/Delete buttons and re-render
                for btn in child.winfo_children():
                    if hasattr(btn, "_save_delete_btn"):
                        btn.destroy()
                # Re-render the correct button
                is_saved = self._vault_is_saved(
                    position.wallet_address or "", position.vault_address
                )
                if is_saved:
                    btn = ctk.CTkButton(
                        child, text="Delete Saved", width=100, height=26,
                        font=ctk.CTkFont(size=10),
                        fg_color=("#dc3545", "#c82333"),
                        hover_color=("#c82333", "#a71d2a"),
                        command=lambda pos=position: self._vault_delete_saved(pos),
                    )
                    btn.pack(pady=2)
                    btn._save_delete_btn = True
                else:
                    btn = ctk.CTkButton(
                        child, text="Save Vault", width=100, height=26,
                        font=ctk.CTkFont(size=10),
                        fg_color=("#0d6efd", "#0b5ed7"),
                        hover_color=("#0b5ed7", "#0a58ca"),
                        command=lambda pos=position: self._vault_save_vault(pos),
                    )
                    btn.pack(pady=2)
                    btn._save_delete_btn = True
                break

    def _vault_refresh_saved(self, position: VaultPosition):
        """Refresh a saved vault's data by fetching live data from the API."""
        if not self.gui.vault_tracker or not self.gui.online_mode:
            self.gui.show_notification("Offline - enable Online Mode in Settings", error=True)
            return
        if not self.gui.key_manager or not self.gui.current_password:
            self.gui.show_notification("Vault not unlocked", error=True)
            return
        entry = self._vault_widgets.get("address_entry")
        wallet_address = entry.get().strip() if entry else ""
        if not wallet_address:
            self.gui.show_notification("Enter a wallet address first", error=True)
            return
        vault_address = position.vault_address
        if not vault_address:
            self.gui.show_notification("No vault address to refresh", error=True)
            return

        status = self._vault_widgets.get("status_label")
        if status:
            status.configure(text=f"Refreshing {position.vault_name}...")

        def _refresh_thread():
            try:
                fresh_pos = self.gui.vault_tracker.fetch_single_position(
                    wallet_address, vault_address
                )
                if fresh_pos and not fresh_pos.error:
                    # Update the saved snapshot in address_db
                    saved_list = self.gui.key_manager.address_db.get("saved_vaults", [])
                    if isinstance(saved_list, list):
                        vl = vault_address.lower()
                        for i, e in enumerate(saved_list):
                            if (
                                isinstance(e, dict)
                                and e.get("vault_address", "").lower() == vl
                            ):
                                # Update with fresh snapshot, preserving original wallet address
                                saved_wallet = e.get("wallet_address", wallet_address)
                                fresh_pos.wallet_address = saved_wallet
                                saved_list[i] = fresh_pos.to_saved_dict()
                                break
                        self.gui.key_manager.save_encrypted_data(self.gui.current_password)
                        self.gui.root.after(0, lambda: self.gui.show_notification(
                            f"Refreshed: {fresh_pos.vault_name}"))
                    # Re-render
                    self.gui.root.after(0, self._vault_do_fetch)
                else:
                    error_msg = fresh_pos.error if fresh_pos else "No data returned"
                    self.gui.root.after(0, lambda: self.gui.show_notification(
                        f"Refresh failed: {error_msg}", error=True))
            except Exception as e:
                self.gui.root.after(0, lambda: self.gui.show_notification(
                    f"Refresh error: {e}", error=True))

        threading.Thread(target=_refresh_thread, daemon=True).start()

    def _vault_render_saved_only(self, wallet_address: str):
        """Render saved vaults for a wallet from the encrypted vault (no API call).

        Called on tab load / wallet selection to show cached vaults immediately.
        """
        scroll = self._vault_widgets.get("scroll")
        if not scroll or not self.gui.key_manager:
            return
        # Clear existing cards
        for widget in scroll.winfo_children():
            widget.destroy()

        saved_vaults = self.gui.key_manager.address_db.get("saved_vaults", [])
        if not isinstance(saved_vaults, list):
            return

        saved_positions = []
        for entry in saved_vaults:
            if not isinstance(entry, dict):
                continue
            va = entry.get("vault_address", "")
            if va:
                saved_pos = VaultPosition.from_saved_dict(entry)
                saved_positions.append(saved_pos)

        if saved_positions:
            for pos in saved_positions:
                self._vault_render_card(pos)
            status = self._vault_widgets.get("status_label")
            if status:
                status.configure(text=f"Showing {len(saved_positions)} saved vault(s) (cached)")
        else:
            ctk.CTkLabel(
                scroll,
                text="No saved vaults. Enter a wallet address and click Refresh Vaults to fetch.",
                font=ctk.CTkFont(size=13), text_color="gray60",
            ).pack(pady=20)
