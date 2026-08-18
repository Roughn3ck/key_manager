"""ColdStack LP Positions tab (extracted from gui_main_v5.py in v5.1.4)."""
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import customtkinter as ctk

from lp_engine import OfflineError
from saved_pools import (
    load_saved_pools, save_pool, remove_saved_pool, is_pool_saved,
    update_position_tracking, get_position_tracking,
    update_saved_pool_wallet,
)
from venue_adapters.venue_writer import (
    CollectFeesParams, CompoundFeesParams,
)
from lp_liquidity_manager import open_add_liquidity, open_remove_liquidity, open_edit_position
from lp_liquidity_manager import _add_status_tooltip as _lp_tooltip


class LPTab:
    """LPTab extracted from ColdStackGUI."""

    def __init__(self, gui):
        self.gui = gui
        self._lp_widgets = {}
        self._lp_auto_fetched = False
        self._lp_last_fetched_address = ""

    def create_tab(self, parent):
        """Build the LP Positions tab content with wallet scan + single position fetch."""
        root = ctk.CTkFrame(parent, fg_color="transparent")
        root.pack(fill="both", expand=True, padx=10, pady=10)

        # -- Row 1: Wallet selector bar (Address or Account mode) --
        wallet_bar = ctk.CTkFrame(root, fg_color="transparent")
        wallet_bar.pack(fill="x", pady=(0, 5))

        # Mode selector dropdown
        selector_menu = ctk.CTkOptionMenu(
            wallet_bar, values=["Address", "Account"], width=90,
            font=ctk.CTkFont(size=11),
            command=self._lp_on_selector_change,
        )
        selector_menu.set(self.gui.lp_selector_mode if hasattr(self, 'lp_selector_mode') else "Address")
        selector_menu.pack(side="left", padx=(0, 5))
        self._lp_widgets["selector_menu"] = selector_menu

        # Address entry (shown when "Address" mode is selected)
        address_entry = ctk.CTkEntry(wallet_bar, width=320,
                                     font=ctk.CTkFont(size=11))
        address_entry.bind("<KeyRelease>", lambda e: self._lp_update_button_states())
        address_entry.bind("<FocusOut>", lambda e: self._lp_update_button_states())
        self._lp_widgets["address_entry"] = address_entry

        # Account dropdown (shown when "Account" mode is selected)
        account_names = []
        if self.gui.key_manager:
            account_names = sorted(
                self.gui.key_manager.address_db.get("accounts", {}).keys()
            )
        account_menu = ctk.CTkOptionMenu(
            wallet_bar, values=account_names if account_names else ["(no accounts)"],
            width=200, font=ctk.CTkFont(size=11),
            command=self._lp_on_account_change,
        )
        self._lp_widgets["account_menu"] = account_menu

        # Pack the appropriate widget based on current mode
        self._lp_apply_selector_mode()

        refresh_btn = ctk.CTkButton(
            wallet_bar, text="Scan Wallet", width=110, height=30,
            font=ctk.CTkFont(size=12, weight="bold"),
            command=self._lp_do_fetch,
        )
        refresh_btn.pack(side="right")
        self._lp_widgets["refresh_btn"] = refresh_btn

        include_closed_var = ctk.CTkCheckBox(
            wallet_bar, text="Include closed",
            font=ctk.CTkFont(size=11),
            checkbox_width=16, checkbox_height=16,
        )
        include_closed_var.pack(side="right", padx=(5, 0))
        self._lp_widgets["include_closed_var"] = include_closed_var

        # -- Row 2: Single position fetch (NEW) --
        pos_bar = ctk.CTkFrame(root, fg_color="transparent")
        pos_bar.pack(fill="x", pady=(0, 10))

        ctk.CTkLabel(pos_bar, text="Platform:",
                     font=ctk.CTkFont(size=13)).pack(side="left", padx=(0, 5))

        # Build venue list from adapter registry + auto-detect
        # v5.1: User-friendly platform names instead of raw adapter keys
        venue_options = ["Auto-detect"]
        try:
            if self.gui.lp_engine:
                raw_venues = self.gui.lp_engine.list_venues()
                # Map raw venue keys to friendly display names
                for v in raw_venues:
                    friendly = self.gui.LP_PLATFORM_MAP_reverse.get(v, v)
                    if friendly not in venue_options:
                        venue_options.append(friendly)
        except Exception:
            venue_options.append("HyperEVM (Project X)")

        platform_menu = ctk.CTkOptionMenu(
            pos_bar, variable=None, values=venue_options, width=160,
            font=ctk.CTkFont(size=12),
        )
        platform_menu.set("Auto-detect")
        platform_menu.pack(side="left", padx=(0, 8))
        self._lp_widgets["platform_menu"] = platform_menu

        ctk.CTkLabel(pos_bar, text="Position ID / NFT ID / Pool Address:",
                     font=ctk.CTkFont(size=11)).pack(side="left", padx=(0, 5))

        position_entry = ctk.CTkEntry(pos_bar, width=280, font=ctk.CTkFont(size=12))
        position_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self._lp_widgets["position_entry"] = position_entry

        fetch_pos_btn = ctk.CTkButton(
            pos_bar, text="Fetch Position", width=120, height=30,
            font=ctk.CTkFont(size=12, weight="bold"),
            command=self._lp_do_fetch_single,
        )
        fetch_pos_btn.pack(side="right")
        self._lp_widgets["fetch_pos_btn"] = fetch_pos_btn

        # v5.1: Clear button — clears position entry, cards, and status
        clear_btn = ctk.CTkButton(
            pos_bar, text="Clear", width=80, height=30,
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color="gray30",
            command=self._lp_clear_single,
        )
        clear_btn.pack(side="right", padx=(0, 5))
        self._lp_widgets["clear_btn"] = clear_btn

        # Offline banner
        self._lp_widgets["offline_banner"] = ctk.CTkLabel(
            root,
            text="🔒 Offline -- Enable Online Mode in Settings to fetch LP positions",
            font=ctk.CTkFont(size=11), text_color=("#666666", "gray50"),
        )
        if not self.gui.online_mode:
            self._lp_widgets["offline_banner"].pack(fill="x", pady=(0, 10))

        # Scrollable card container
        scroll = ctk.CTkScrollableFrame(root)
        scroll.pack(fill="both", expand=True)
        self._lp_widgets["scroll"] = scroll

        # Status label
        status_label = ctk.CTkLabel(
            root, text="", font=ctk.CTkFont(size=11), text_color=("#666666", "gray50"),
        )
        status_label.pack(fill="x", pady=(5, 0))
        self._lp_widgets["status_label"] = status_label

        self._lp_update_online_state()

        # v5.1: Saved Pools counter label
        self._lp_widgets["saved_pools_count_label"] = ctk.CTkLabel(
            root, text="", font=ctk.CTkFont(size=10), text_color="#0d6efd",
        )
        self._lp_widgets["saved_pools_count_label"].pack(fill="x", pady=(0, 5))

    def _lp_update_saved_pools_count(self, address: str = ""):
        """Update the saved-pools counter in the status label.

        Shows the total number of saved pools across all wallets. If an address
        is provided, also shows the per-wallet count when it differs from the
        total.
        """
        if not self.gui.key_manager:
            return
        all_saved = load_saved_pools(self.gui.key_manager.address_db)
        total = len(all_saved)
        status = self._lp_widgets.get("status_label")
        if not status or total == 0:
            return
        # Per-wallet count (for informational purposes)
        wallet_count = 0
        if address:
            wallet_count = len(
                load_saved_pools(self.gui.key_manager.address_db, wallet_address=address)
            )
        current = status.cget("text") or ""
        # Strip any previous saved-pools suffix to avoid duplication
        base = current.split("  ·  Saved Pools:")[0]
        if wallet_count and wallet_count != total:
            status.configure(
                text=f"{base}  ·  Saved Pools: {wallet_count} for this wallet ({total} total)"
            )
        else:
            status.configure(text=f"{base}  ·  Saved Pools: {total} total")

    def _lp_update_button_states(self):
        """Update LP tab button states based on current conditions."""
        if not self._lp_widgets:
            return
        refresh_btn = self._lp_widgets.get("refresh_btn")
        fetch_pos_btn = self._lp_widgets.get("fetch_pos_btn")
        address = self._lp_get_current_wallet_address()
        has_address = bool(address and address.startswith("0x") and len(address) == 42)
        enabled = self.gui.online_mode and has_address
        if refresh_btn:
            refresh_btn.configure(state="normal" if enabled else "disabled")
        if fetch_pos_btn:
            fetch_pos_btn.configure(state="normal" if enabled else "disabled")

    def _lp_update_online_state(self):
        """Enable/disable LP tab widgets based on online_mode."""
        if not self._lp_widgets:
            return
        self._lp_update_button_states()
        offline_banner = self._lp_widgets.get("offline_banner")
        if offline_banner:
            try:
                if self.gui.online_mode:
                    offline_banner.pack_forget()
                else:
                    offline_banner.pack(fill="x", pady=(0, 10))
            except Exception:
                pass

    def _lp_apply_selector_mode(self):
        """Show/hide address entry vs account menu based on selector mode."""
        selector = self._lp_widgets.get("selector_menu")
        entry = self._lp_widgets.get("address_entry")
        account_menu = self._lp_widgets.get("account_menu")
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
        self._lp_update_button_states()

    def _lp_on_selector_change(self, choice: str):
        """Handle Address/Account mode switch."""
        self.gui.lp_selector_mode = choice
        self._lp_apply_selector_mode()
        # Save config
        if self.gui.key_manager and self.gui.current_password:
            cfg = self.gui.key_manager.address_db.setdefault("config", {})
            cfg["lp_selector_mode"] = choice
            self.gui.key_manager.save_encrypted_data(self.gui.current_password)
        # If Account mode, try to resolve and fetch
        if choice == "Account":
            account_menu = self._lp_widgets.get("account_menu")
            if account_menu:
                acct = account_menu.get()
                if acct and acct != "(no accounts)":
                    self._lp_on_account_change(acct)
        else:
            # Address mode: update saved-pools counter for current address
            entry = self._lp_widgets.get("address_entry")
            if entry:
                addr = entry.get().strip()
                if addr:
                    self._lp_update_saved_pools_count(addr)

    def _lp_on_account_change(self, choice: str):
        """Handle account selection from dropdown."""
        if not choice or choice == "(no accounts)":
            return
        self.gui.lp_selected_account = choice
        addr = self._lp_resolve_account_address(choice)
        entry = self._lp_widgets.get("address_entry")
        if entry and addr:
            entry.delete(0, "end")
            entry.insert(0, addr)
            # Save config
            if self.gui.key_manager and self.gui.current_password:
                cfg = self.gui.key_manager.address_db.setdefault("config", {})
                cfg["lp_selected_account"] = choice
                self.gui.key_manager.save_encrypted_data(self.gui.current_password)
            # Update saved-pools counter, button states, and auto-fetch
            self._lp_update_saved_pools_count(addr)
            self._lp_update_button_states()
            self._lp_maybe_auto_fetch()

    def _lp_resolve_account_address(self, account_name: str) -> str:
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

    def _lp_get_current_wallet_address(self) -> str:
        """Return the wallet address currently selected in the LP tab.

        Respects the Address/Account selector: in Account mode, resolves the
        selected account's first EVM/HYPE address; in Address mode, returns the
        raw address entry contents.
        """
        selector = self._lp_widgets.get("selector_menu")
        mode = selector.get() if selector else "Address"
        if mode == "Account":
            account_menu = self._lp_widgets.get("account_menu")
            account_name = account_menu.get() if account_menu else ""
            if account_name and account_name != "(no accounts)":
                return self._lp_resolve_account_address(account_name)
            return ""
        entry = self._lp_widgets.get("address_entry")
        return entry.get().strip() if entry else ""

    def _lp_get_current_account_name(self) -> str:
        """Return the vault account name for the LP tab's current selection.

        In Account mode this is the dropdown value. In Address mode it
        reverse-resolves the address to an account name from the vault.
        """
        selector = self._lp_widgets.get("selector_menu")
        mode = selector.get() if selector else "Address"
        if mode == "Account":
            account_menu = self._lp_widgets.get("account_menu")
            name = account_menu.get() if account_menu else ""
            return name if name != "(no accounts)" else ""
        wallet_address = self._lp_get_current_wallet_address()
        if not wallet_address or not self.gui.key_manager:
            return ""
        accounts_data = self.gui.key_manager.address_db.get("accounts", {})
        for acct, data in accounts_data.items():
            for addr in data.get("addresses", []):
                if addr.get("address", "").lower() == wallet_address.lower():
                    return acct
        return ""

    def _lp_restore_state(self):
        """Restore LP tab selector state from config after login."""
        if not self._lp_widgets:
            return
        selector = self._lp_widgets.get("selector_menu")
        if not selector:
            return
        mode = getattr(self, 'lp_selector_mode', 'Address')
        selector.set(mode)
        self._lp_apply_selector_mode()
        if mode == "Account":
            account_menu = self._lp_widgets.get("account_menu")
            if account_menu:
                acct = getattr(self, 'lp_selected_account', '')
                if acct:
                    # Refresh account list in case accounts were added
                    account_names = []
                    if self.gui.key_manager:
                        account_names = sorted(
                            self.gui.key_manager.address_db.get("accounts", {}).keys()
                        )
                    if account_names:
                        account_menu.configure(values=account_names)
                    if acct in account_names:
                        account_menu.set(acct)
                        self._lp_on_account_change(acct)
        else:
            # Address mode: update saved-pools counter if address entry has content
            entry = self._lp_widgets.get("address_entry")
            if entry:
                addr = entry.get().strip()
                if addr:
                    self._lp_update_saved_pools_count(addr)
        # Auto-load saved pool placeholders (all wallets, not filtered)
        self._lp_render_all_saved_placeholders()
        # Auto-fetch all saved pools in background (works even without a wallet address in the entry)
        if self.gui.online_mode:
            self._lp_auto_fetch_all_saved()
        # Ensure Scan Wallet / Fetch Position buttons are enabled after restore
        refresh_btn = self._lp_widgets.get("refresh_btn")
        if refresh_btn and self.gui.online_mode:
            refresh_btn.configure(state="normal")
        fetch_pos_btn = self._lp_widgets.get("fetch_pos_btn")
        if fetch_pos_btn and self.gui.online_mode:
            fetch_pos_btn.configure(state="normal")

    def _lp_prefill_address(self, account_name: str):
        """Pre-fill the LP tab address entry with the account's EVM address.

        If the LP selector is in "Account" mode, set the account dropdown to
        the selected account instead of filling the address entry directly.

        Priority order (Address mode only):
        1. If entry already has an address, keep it.
        2. If saved pools exist whose wallet_address matches an EVM address
           of the selected account, pre-fill that address.
        3. If no account-specific match, use the first saved pool's wallet.
        4. Fall back to the account's first EVM/HYPE address.

        After filling, update the saved-pools counter and trigger auto-fetch
        if online mode is on and we haven't fetched yet.
        """
        if not self._lp_widgets or not self.gui.key_manager:
            return
        entry = self._lp_widgets.get("address_entry")
        if not entry:
            return

        # If in Account mode, set the account dropdown and let
        # _lp_on_account_change resolve the address.
        selector = self._lp_widgets.get("selector_menu")
        if selector and selector.get() == "Account":
            account_menu = self._lp_widgets.get("account_menu")
            if account_menu:
                account_names = sorted(
                    self.gui.key_manager.address_db.get("accounts", {}).keys()
                )
                if account_names:
                    account_menu.configure(values=account_names)
                if account_name in account_names:
                    account_menu.set(account_name)
                    self._lp_on_account_change(account_name)
            return

        # If entry already has an address, keep it
        current = entry.get().strip()
        if current:
            self._lp_update_saved_pools_count(current)
            return

        all_saved = load_saved_pools(self.gui.key_manager.address_db)
        accounts_data = self.gui.key_manager.address_db.get("accounts", {})
        addresses = accounts_data.get(account_name, {}).get("addresses", [])

        # Step 2: Check if any saved pool's wallet matches an account EVM address
        account_evm_addresses = []
        for addr in addresses:
            coin = addr.get("coin", "").lower()
            chain = addr.get("chain", "").lower()
            if "evm" in coin or "evm" in chain or "hype" in coin or "hype" in chain:
                account_evm_addresses.append(addr.get("address", "").lower())

        for saved_entry in all_saved:
            saved_wallet = saved_entry.get("wallet_address", "").lower()
            if saved_wallet and saved_wallet in account_evm_addresses:
                entry.delete(0, "end")
                entry.insert(0, saved_entry.get("wallet_address", ""))
                self._lp_update_saved_pools_count(saved_entry.get("wallet_address", ""))
                self._lp_maybe_auto_fetch()
                return

        # Step 3: Fall back to the first saved pool's wallet globally
        if all_saved:
            first_wallet = all_saved[0].get("wallet_address", "")
            if first_wallet:
                entry.delete(0, "end")
                entry.insert(0, first_wallet)
                self._lp_update_saved_pools_count(first_wallet)
                self._lp_maybe_auto_fetch()
                return

        # Step 4: Fall back to the account's first EVM address
        for addr in addresses:
            coin = addr.get("coin", "").lower()
            chain = addr.get("chain", "").lower()
            if "evm" in coin or "evm" in chain or "hype" in coin or "hype" in chain:
                entry.delete(0, "end")
                entry.insert(0, addr.get("address", ""))
                self._lp_update_saved_pools_count(addr.get("address", ""))
                self._lp_maybe_auto_fetch()
                return

    def _lp_preload_saved_only(self, address: str):
        """Pre-load saved pool positions for a wallet without triggering a full scan.

        This is called on tab change to show cached positions quickly.
        The user must manually click Fetch/Scan to discover new positions.
        """
        if not self.gui.lp_engine or not self.gui.online_mode:
            return
        saved = load_saved_pools(self.gui.key_manager.address_db, wallet_address=address)
        if not saved:
            return
        # Render saved pool placeholders immediately
        self._lp_render_saved_placeholders(address)
        # Fetch saved positions in background (fast — 1-4 seconds)
        self._lp_fetch_saved_only(address)

    def _lp_auto_fetch_all_saved(self):
        """Auto-fetch ALL saved pools on tab open / restore.

        Works without a wallet address in the entry by using each saved
        pool's stored wallet_address. Fetches all saved pools across all
        wallets and venues in a single background thread.
        """
        if not self.gui.lp_engine or not self.gui.online_mode:
            return
        if not self.gui.key_manager:
            return
        all_saved = load_saved_pools(self.gui.key_manager.address_db)
        if not all_saved:
            return

        from venue_adapters.hyperliquid_adapter import HyperliquidAdapter
        from venue_adapters.bsc_adapter import BSCAdapter

        hype_adapter = HyperliquidAdapter()
        bsc_adapter = BSCAdapter()

        def _auto_fetch_thread():
            positions = []
            for entry in all_saved:
                tid = entry.get("token_id")
                venue = entry.get("venue", "HyperEVM")
                wallet = entry.get("wallet_address", "")
                if not tid:
                    continue
                if venue == "HyperEVM":
                    try:
                        pos = hype_adapter.fetch_evm_position_by_token_id(
                            tid, self.gui.price_engine, wallet_address=wallet
                        )
                        if pos and not pos.error:
                            # Attach wallet address for display
                            if wallet:
                                pos.wallet_address = wallet
                            positions.append(pos)
                    except Exception:
                        pass
                elif venue in ("Aerodrome", "aerodrome"):
                    try:
                        from venue_adapters.aerodrome_adapter import AerodromeAdapter
                        pos = AerodromeAdapter()._fetch_position_by_token_id(
                            tid, self.gui.price_engine, wallet_address=wallet
                        )
                        if pos and not pos.error:
                            # Attach wallet address for display
                            if wallet:
                                pos.wallet_address = wallet
                            positions.append(pos)
                    except Exception:
                        pass
                elif venue in ("BSC", "bsc"):
                    try:
                        pos = bsc_adapter._fetch_position_by_token_id(
                            tid, self.gui.price_engine, wallet_address=wallet
                        )
                        if pos and not pos.error:
                            # Attach wallet address for display
                            if wallet:
                                pos.wallet_address = wallet
                            positions.append(pos)
                    except Exception:
                        pass

            # v5.2.2: Also check gauges for saved Aerodrome pools. The direct
            # token-id fetch above catches unstaked NFTs; this catches staked
            # positions where the gauge owns the NFT.
            aero_saved = [
                e for e in all_saved
                if e.get("venue", "").lower() == "aerodrome"
            ]
            if aero_saved:
                try:
                    from venue_adapters.aerodrome_adapter import AerodromeAdapter
                    aero_adapter = AerodromeAdapter()
                    for entry in aero_saved:
                        wallet = entry.get("wallet_address", "")
                        if not wallet:
                            continue
                        staked = aero_adapter._find_staked_positions_via_saved_pools(
                            wallet, self.gui.price_engine, [entry]
                        )
                        for staked_pos in staked:
                            if not any(p.position_id == staked_pos.position_id for p in positions):
                                # Attach wallet address for display
                                if wallet:
                                    staked_pos.wallet_address = wallet
                                positions.append(staked_pos)
                except Exception:
                    pass

            self.gui.root.after(0, lambda: self._lp_on_loaded_all_saved(positions))

        threading.Thread(target=_auto_fetch_thread, daemon=True).start()

    def _lp_on_loaded_all_saved(self, positions):
        """Render auto-fetched saved pools, preserving any wallet address context."""
        if not positions:
            return
        # Use the first position's wallet to update the saved-pools counter
        scroll = self._lp_widgets.get("scroll")
        if scroll:
            for widget in scroll.winfo_children():
                widget.destroy()
        # Deduplicate
        seen = set()
        unique = []
        for pos in positions:
            key = f"{pos.venue}:{pos.position_id}"
            if key not in seen:
                seen.add(key)
                unique.append(pos)
        for pos in unique:
            self._lp_render_card(pos)
        status = self._lp_widgets.get("status_label")
        if status:
            status.configure(text=f"Last check: {len(unique)} saved position(s) auto-loaded")
        self._lp_update_button_states()

    def _lp_maybe_auto_fetch(self):
        """If online and not yet auto-fetched, schedule a fetch."""
        if self.gui.online_mode and not self._lp_auto_fetched:
            entry = self._lp_widgets.get("address_entry")
            addr = entry.get().strip() if entry else ""
            if addr and addr.startswith("0x") and len(addr) == 42:
                self._lp_auto_fetched = True
                # Don't auto-trigger full scan — just preload saved pools
                self._lp_preload_saved_only(addr)

    def _lp_do_fetch(self):
        """Fetch LP positions for the entered address (threaded).

        v5.1: Fast-path — if saved pools exist for this wallet, fetch them
        first (1-4 seconds) so the user sees live cards quickly, then run
        the full wallet scan in the background to discover new positions.
        If no saved pools exist, the full scan runs immediately (existing
        behavior).
        """
        if not self.gui.lp_engine or not self.gui.online_mode:
            self.gui.show_notification("Offline - enable Online Mode in Settings", error=True)
            return
        selector = self._lp_widgets.get("selector_menu")
        mode = selector.get() if selector else "Address"
        address = self._lp_get_current_wallet_address()
        if not address:
            status = self._lp_widgets.get("status_label")
            if status:
                if mode == "Account":
                    status.configure(text="Select an account first")
                else:
                    status.configure(text="Enter a wallet address first")
            return
        # Warn user about scan time for full wallet scans without saved pools
        saved = load_saved_pools(self.gui.key_manager.address_db, wallet_address=address)
        if not saved:
            from tkinter import messagebox
            confirm = messagebox.askyesno(
                "Scan Wallet — Estimated Time",
                "Scan Wallet will search your wallet address across all enabled platforms.\n"
                "This could take up to 20 minutes.\n\n"
                "To locate a pool quickly, select the Platform and use \"Fetch Position\".\n\n"
                "Would you like to use Scan Wallet to search all platforms while you grab a coffee ☕?",
            )
            if not confirm:
                return

        status = self._lp_widgets.get("status_label")
        refresh_btn = self._lp_widgets.get("refresh_btn")
        scroll = self._lp_widgets.get("scroll")
        if refresh_btn:
            refresh_btn.configure(state="disabled")
        if scroll:
            for widget in scroll.winfo_children():
                widget.destroy()

        # v5.1: Fast-path check — if saved pools exist, fetch them first.
        if saved:
            if status:
                status.configure(text="Fetching saved positions... Full wallet scan will follow.")
            # Render placeholders immediately, then fast-fetch saved pools,
            # then schedule the full scan in the background.
            self._lp_render_saved_placeholders(address)
            self._lp_fetch_saved_only(address)
            self.gui.root.after(2000, lambda: self._lp_do_full_scan(address))
        else:
            if status:
                status.configure(text="Scanning wallet for new positions — this may take up to 6 minutes.")
            # No saved pools — do full scan immediately (existing behavior).
            self._lp_do_full_scan(address)

    def _lp_fetch_saved_only(self, address: str):
        """Fast-path: fetch only saved-pool positions by token ID (threaded).

        Queries each saved pool's token ID via HyperliquidAdapter — no full
        wallet scan.  For 1-3 saved pools this takes 1.4-4.2 seconds vs ~6
        minutes for the full NFT scan.
        """
        from venue_adapters.hyperliquid_adapter import HyperliquidAdapter

        saved = load_saved_pools(self.gui.key_manager.address_db, wallet_address=address)
        if not saved:
            return

        adapter = HyperliquidAdapter()

        def _fast_thread():
            positions = []
            for entry in saved:
                tid = entry.get("token_id")
                venue = entry.get("venue", "HyperEVM")
                if not tid:
                    continue
                if venue == "HyperEVM":
                    try:
                        pos = adapter.fetch_evm_position_by_token_id(
                            tid, self.gui.price_engine, wallet_address=address
                        )
                        if pos and not pos.error:
                            positions.append(pos)
                    except Exception:
                        pass
                elif venue in ("Aerodrome", "aerodrome"):
                    try:
                        from venue_adapters.aerodrome_adapter import AerodromeAdapter
                        pos = AerodromeAdapter()._fetch_position_by_token_id(
                            tid, self.gui.price_engine, wallet_address=address
                        )
                        if pos and not pos.error:
                            positions.append(pos)
                    except Exception:
                        pass
                elif venue in ("BSC", "bsc"):
                    try:
                        from venue_adapters.bsc_adapter import BSCAdapter
                        pos = BSCAdapter()._fetch_position_by_token_id(
                            tid, self.gui.price_engine, wallet_address=address
                        )
                        if pos and not pos.error:
                            positions.append(pos)
                    except Exception:
                        pass
            # v5.2.2: Saved Aerodrome pools may be staked in a gauge, and
            # fetch_all_positions cannot enumerate them. Check gauges here.
            aero_saved = [
                e for e in saved
                if e.get("venue", "").lower() == "aerodrome"
            ]
            if aero_saved:
                try:
                    from venue_adapters.aerodrome_adapter import AerodromeAdapter
                    staked = AerodromeAdapter()._find_staked_positions_via_saved_pools(
                        address, self.gui.price_engine, aero_saved
                    )
                    for staked_pos in staked:
                        if not any(p.position_id == staked_pos.position_id for p in positions):
                            positions.append(staked_pos)
                except Exception:
                    pass
            self.gui.root.after(0, lambda: self._lp_on_loaded(positions, address))

        threading.Thread(target=_fast_thread, daemon=True).start()

    def _lp_do_full_scan(self, address: str):
        """Full wallet scan: fetch all positions + merge saved pools (threaded).

        Extracted from _lp_do_fetch() in v5.1 so the fast-path can run it in
        the background after saved-pool cards have been rendered.
        """
        status = self._lp_widgets.get("status_label")
        if status:
            status.configure(
                text="Fetching positions — scanning HyperEVM. This may take up to 6 minutes. Please be patient."
            )

        include_closed = self._lp_widgets.get("include_closed_var")
        include_closed = include_closed.get() if include_closed else False

        def _fetch_thread():
            try:
                positions = self.gui.lp_engine.fetch_all_positions(
                    address, include_closed=include_closed
                )
                # v5.1: Merge saved pools for this wallet (fast token-ID lookup)
                saved = load_saved_pools(self.gui.key_manager.address_db, wallet_address=address)
                print(f"[saved_pools] loaded {len(saved)} saved pools for {address}")
                if saved:
                    from venue_adapters.hyperliquid_adapter import HyperliquidAdapter
                    hype_adapter = HyperliquidAdapter()
                    from venue_adapters.bsc_adapter import BSCAdapter
                    k_adapter = BSCAdapter()
                    for entry in saved:
                        tid = entry.get("token_id")
                        venue = entry.get("venue", "HyperEVM")
                        if not tid:
                            continue
                        if venue == "HyperEVM":
                            # Skip if already in scanned positions
                            pid = f"hyperevm:{tid}"
                            if any(p.position_id == pid for p in positions):
                                continue
                            try:
                                pos = hype_adapter.fetch_evm_position_by_token_id(tid, self.gui.price_engine)
                                if pos and not pos.error:
                                    positions.append(pos)
                            except Exception:
                                pass
                        elif venue in ("Aerodrome", "aerodrome"):
                            pid = f"base:{tid}"
                            if any(p.position_id == pid for p in positions):
                                continue
                            try:
                                from venue_adapters.aerodrome_adapter import AerodromeAdapter
                                pos = AerodromeAdapter()._fetch_position_by_token_id(
                                    tid, self.gui.price_engine, wallet_address=address
                                )
                                if pos and not pos.error:
                                    positions.append(pos)
                            except Exception:
                                pass
                        elif venue in ("BSC", "bsc"):
                            pid = f"bsc:{tid}"
                            if any(p.position_id == pid for p in positions):
                                continue
                            try:
                                pos = k_adapter._fetch_position_by_token_id(
                                    tid, self.gui.price_engine, wallet_address=address
                                )
                                if pos and not pos.error:
                                    positions.append(pos)
                            except Exception:
                                pass
                # v5.2.2: Aerodrome wallet scan cannot enumerate non-sequential
                # NFTs, so also check gauges for any saved Aerodrome pools.
                aero_saved = [
                    e for e in saved
                    if e.get("venue", "").lower() == "aerodrome"
                ]
                if aero_saved:
                    try:
                        from venue_adapters.aerodrome_adapter import AerodromeAdapter
                        staked = AerodromeAdapter()._find_staked_positions_via_saved_pools(
                            address, self.gui.price_engine, aero_saved
                        )
                        for staked_pos in staked:
                            if not any(p.position_id == staked_pos.position_id for p in positions):
                                positions.append(staked_pos)
                    except Exception:
                        pass

                # v5.2.2: If full scan found no Aerodrome positions, guide the
                # user to manually enter their NFT token ID (staked positions
                # are held by gauges and invisible to wallet scans).
                aero_positions = [p for p in positions if p.venue and p.venue.lower() == "aerodrome"]
                if not aero_positions and not aero_saved:
                    from tkinter import messagebox
                    self.gui.root.after(0, lambda: messagebox.showinfo(
                        "Aerodrome Positions",
                        "No Aerodrome positions found via wallet scan.\n\n"
                        "If you have staked Aerodrome SlipStream positions, they won't appear "
                        "in a wallet scan because the gauge contract holds the NFT.\n\n"
                        "To find your position:\n"
                        "1. Go to aerodrome.finance → Dashboard\n"
                        "2. Find your position under \"Liquidity Rewards\"\n"
                        "3. Look for the # number next to \"Deposit\" (e.g. #50282741)\n"
                        "4. Enter that number in the Position ID / NFT ID field and click Fetch Position",
                    ))

                self.gui.root.after(0, lambda: self._lp_on_loaded(positions, address))
            except OfflineError:
                self.gui.root.after(0, lambda: self._lp_on_error("Offline mode enabled"))
            except Exception as e:
                self.gui.root.after(0, lambda: self._lp_on_error(str(e)))

        threading.Thread(target=_fetch_thread, daemon=True).start()

    def _lp_do_filtered_scan(self, address: str, venue_key: str):
        """Scan a wallet for positions on a specific venue only (no warning dialog).

        Used when the user selects a specific Platform and clicks Fetch Position
        with an empty Position ID. Skips the "20 minutes" warning since the
        scan is limited to one venue.
        """
        if not self.gui.lp_engine or not self.gui.online_mode:
            self.gui.show_notification("Offline - enable Online Mode in Settings", error=True)
            return

        status = self._lp_widgets.get("status_label")
        refresh_btn = self._lp_widgets.get("refresh_btn")
        scroll = self._lp_widgets.get("scroll")
        if refresh_btn:
            refresh_btn.configure(state="disabled")
        if scroll:
            for widget in scroll.winfo_children():
                widget.destroy()

        # Check saved pools first for fast-path
        saved = load_saved_pools(self.gui.key_manager.address_db, wallet_address=address)
        if saved:
            if status:
                status.configure(text=f"Fetching saved positions on {venue_key}... Full scan will follow.")
            self._lp_render_saved_placeholders(address)
            self._lp_fetch_saved_only(address)
            self.gui.root.after(2000, lambda: self._lp_do_filtered_full_scan(address, venue_key))
        else:
            if status:
                status.configure(text=f"Scanning wallet on {venue_key}...")
            self._lp_do_filtered_full_scan(address, venue_key)

    def _lp_do_filtered_full_scan(self, address: str, venue_key: str):
        """Full scan filtered to a specific venue (threaded)."""
        status = self._lp_widgets.get("status_label")
        if status:
            status.configure(text=f"Fetching positions on {venue_key}...")

        include_closed = self._lp_widgets.get("include_closed_var")
        include_closed = include_closed.get() if include_closed else False

        def _fetch_thread():
            try:
                # Use fetch_all_positions with venue_key filter
                positions = self.gui.lp_engine.fetch_all_positions(
                    address, venue_key=venue_key, include_closed=include_closed
                )
                # Merge saved pools for this wallet
                saved = load_saved_pools(self.gui.key_manager.address_db, wallet_address=address)
                if saved:
                    from venue_adapters.hyperliquid_adapter import HyperliquidAdapter
                    from venue_adapters.bsc_adapter import BSCAdapter
                    hype_adapter = HyperliquidAdapter()
                    k_adapter = BSCAdapter()
                    for entry in saved:
                        tid = entry.get("token_id")
                        venue = entry.get("venue", "HyperEVM")
                        if not tid:
                            continue
                        if venue == "HyperEVM":
                            pid = f"hyperevm:{tid}"
                            if any(p.position_id == pid for p in positions):
                                continue
                            try:
                                pos = hype_adapter.fetch_evm_position_by_token_id(tid, self.gui.price_engine)
                                if pos and not pos.error:
                                    positions.append(pos)
                            except Exception:
                                pass
                        elif venue in ("Aerodrome", "aerodrome"):
                            pid = f"base:{tid}"
                            if any(p.position_id == pid for p in positions):
                                continue
                            try:
                                from venue_adapters.aerodrome_adapter import AerodromeAdapter
                                pos = AerodromeAdapter()._fetch_position_by_token_id(
                                    tid, self.gui.price_engine, wallet_address=address
                                )
                                if pos and not pos.error:
                                    positions.append(pos)
                            except Exception:
                                pass
                        elif venue in ("BSC", "bsc"):
                            pid = f"bsc:{tid}"
                            if any(p.position_id == pid for p in positions):
                                continue
                            try:
                                pos = k_adapter._fetch_position_by_token_id(
                                    tid, self.gui.price_engine, wallet_address=address
                                )
                                if pos and not pos.error:
                                    positions.append(pos)
                            except Exception:
                                pass
                # v5.2.2: Aerodrome wallet scan cannot enumerate non-sequential
                # NFTs, so also check gauges for any saved Aerodrome pools.
                aero_saved = [
                    e for e in saved
                    if e.get("venue", "").lower() == "aerodrome"
                ]
                if aero_saved and venue_key in (None, "aerodrome"):
                    try:
                        from venue_adapters.aerodrome_adapter import AerodromeAdapter
                        staked = AerodromeAdapter()._find_staked_positions_via_saved_pools(
                            address, self.gui.price_engine, aero_saved
                        )
                        for staked_pos in staked:
                            if not any(p.position_id == staked_pos.position_id for p in positions):
                                positions.append(staked_pos)
                    except Exception:
                        pass

                # v5.2.2: If Aerodrome scan returned 0 positions, show a helpful popup.
                # Staked Aerodrome NFTs can't be found via wallet scan (the gauge owns the NFT).
                # The user must enter their NFT token ID manually.
                if venue_key == "aerodrome" and not positions:
                    from tkinter import messagebox
                    self.gui.root.after(0, lambda: messagebox.showinfo(
                        "Aerodrome Positions Not Found",
                        "Your Aerodrome SlipStream positions may be staked in a gauge, "
                        "which makes them invisible to a wallet scan.\n\n"
                        "To find your position:\n"
                        "1. Go to aerodrome.finance → Dashboard\n"
                        "2. Find your position under \"Liquidity Rewards\"\n"
                        "3. Look for the # number next to \"Deposit\" (e.g. #50282741)\n"
                        "4. Enter that number in the Position ID / NFT ID field\n"
                        "   and click Fetch Position",
                    ))

                self.gui.root.after(0, lambda: self._lp_on_loaded(positions, address))
            except OfflineError:
                self.gui.root.after(0, lambda: self._lp_on_error("Offline mode enabled"))
            except Exception as e:
                self.gui.root.after(0, lambda: self._lp_on_error(str(e)))

        threading.Thread(target=_fetch_thread, daemon=True).start()

    def _lp_do_fetch_single(self):
        """Fetch a single LP position by NFT ID / position ID / pool address (threaded).

        v5.1: Smart fetch — if the entered value is a numeric token ID or
        hyperevm: prefix that matches a saved pool for the current wallet,
        use the saved pool's venue to skip auto-detect.  Also maps friendly
        platform dropdown names back to adapter keys.

        v5.1: Address/Account selector aware — captures the current wallet
        address before spawning the thread so Save Pool can resolve it even
        when the widget state changes during the fetch.
        """
        if not self.gui.lp_engine or not self.gui.online_mode:
            self.gui.show_notification("Offline - enable Online Mode in Settings", error=True)
            return

        selector = self._lp_widgets.get("selector_menu")
        mode = selector.get() if selector else "Address"
        wallet_address = self._lp_get_current_wallet_address()
        if not wallet_address:
            status = self._lp_widgets.get("status_label")
            if status:
                if mode == "Account":
                    status.configure(text="Select an account first")
                else:
                    status.configure(text="Enter a wallet address first")
            return

        pos_entry = self._lp_widgets.get("position_entry")
        if not pos_entry:
            return
        position_id = pos_entry.get().strip()
        if not position_id:
            # No position ID entered — fall through to wallet scan
            # Check if a specific platform is selected
            platform_menu = self._lp_widgets.get("platform_menu")
            selected_venue = None
            if platform_menu:
                val = platform_menu.get()
                if val and val != "Auto-detect":
                    selected_venue = self.gui.LP_PLATFORM_MAP.get(val, val)

            if selected_venue:
                # Specific platform selected — do a filtered scan without the warning
                self._lp_do_filtered_scan(wallet_address, selected_venue)
            else:
                # Auto-detect — full scan with warning
                self._lp_do_fetch()
            return

        # v5.1: Map friendly platform name back to adapter key
        platform_menu = self._lp_widgets.get("platform_menu")
        selected_venue = None
        if platform_menu:
            val = platform_menu.get()
            if val and val != "Auto-detect":
                # Map friendly name -> adapter key, or pass through as-is
                selected_venue = self.gui.LP_PLATFORM_MAP.get(val, val)

        # v5.1: Smart saved-pool lookup — if the entered value is a numeric
        # token ID or hyperevm: prefix, check saved pools for the current
        # wallet to auto-select the venue.
        raw_id = position_id
        if raw_id.startswith("hyperevm:"):
            raw_id = raw_id.split(":", 1)[1]
        try:
            numeric_tid = int(raw_id)
        except (ValueError, TypeError):
            numeric_tid = None

        if numeric_tid is not None and wallet_address:
            saved = load_saved_pools(self.gui.key_manager.address_db, wallet_address=wallet_address)
            for entry in saved:
                if entry.get("token_id") == numeric_tid:
                    # Found a matching saved pool — use its venue
                    saved_venue = entry.get("venue", "HyperEVM")
                    # Map saved venue back to adapter key
                    if saved_venue == "HyperEVM":
                        selected_venue = "hyperliquid"
                    else:
                        selected_venue = saved_venue.lower()
                    break

        status = self._lp_widgets.get("status_label")
        refresh_btn = self._lp_widgets.get("refresh_btn")
        fetch_pos_btn = self._lp_widgets.get("fetch_pos_btn")
        scroll = self._lp_widgets.get("scroll")
        if status:
            status.configure(text="Fetching position...")
        if refresh_btn:
            refresh_btn.configure(state="disabled")
        if fetch_pos_btn:
            fetch_pos_btn.configure(state="disabled")
        if scroll:
            for widget in scroll.winfo_children():
                widget.destroy()

        # Preserve the wallet address through the fetch so Save Pool can use
        # it even if the selector state changes while the thread runs.
        self._lp_last_fetched_address = wallet_address

        def _fetch_single_thread():
            try:
                position = self.gui.lp_engine.fetch_position(
                    position_id, venue_key=selected_venue, wallet_address=wallet_address
                )
                # Check for venue detection errors
                if position and position.error and "Unsupported" in (position.error or ""):
                    self.gui.root.after(0, lambda: self.gui.show_notification(
                        "Select a Platform to fetch the position.", error=True))
                    # Re-enable buttons
                    refresh_btn = self._lp_widgets.get("refresh_btn")
                    fetch_pos_btn = self._lp_widgets.get("fetch_pos_btn")
                    if refresh_btn:
                        self.gui.root.after(0, lambda: refresh_btn.configure(state="normal" if self.gui.online_mode else "disabled"))
                    if fetch_pos_btn:
                        self.gui.root.after(0, lambda: fetch_pos_btn.configure(state="normal" if self.gui.online_mode else "disabled"))
                    return
                self.gui.root.after(0, lambda: self._lp_on_loaded([position], wallet_address))
            except OfflineError:
                self.gui.root.after(0, lambda: self._lp_on_error("Offline mode enabled"))
            except Exception as e:
                self.gui.root.after(0, lambda: self._lp_on_error(str(e)))

        threading.Thread(target=_fetch_single_thread, daemon=True).start()

    def _lp_fetch_saved_single(self, saved_pos):
        """Fetch live data for a single saved pool position by its token ID."""
        if not self.gui.lp_engine or not self.gui.online_mode:
            self.gui.show_notification("Offline - enable Online Mode in Settings", error=True)
            return
        wallet_address = getattr(saved_pos, 'wallet_address', '')
        if not wallet_address:
            self.gui.show_notification("No wallet address associated with this saved pool", error=True)
            return
        # Set the wallet address in the entry so the fetch works
        entry = self._lp_widgets.get("address_entry")
        if entry:
            entry.delete(0, "end")
            entry.insert(0, wallet_address)
        # Set platform to the saved venue
        platform_menu = self._lp_widgets.get("platform_menu")
        if platform_menu:
            venue_key = saved_pos.venue or "HyperEVM"
            # Normalize saved venue to adapter key, then to friendly menu name
            adapter_key = venue_key.lower()
            if adapter_key in ("hyperevm", "hyperliquid"):
                adapter_key = "hyperliquid"
            elif adapter_key in ("bsc", "krystal"):
                adapter_key = "bsc"
            elif adapter_key in ("aerodrome", "base"):
                adapter_key = "aerodrome"
            friendly = self.gui.LP_PLATFORM_MAP_reverse.get(adapter_key, venue_key)
            platform_menu.set(friendly)
        # Fetch by token ID (strip venue prefix if present)
        pos_entry = self._lp_widgets.get("position_entry")
        if pos_entry:
            pos_entry.delete(0, "end")
            raw_id = str(saved_pos.position_id)
            if ":" in raw_id:
                raw_id = raw_id.split(":", 1)[1]
            pos_entry.insert(0, raw_id)
        self._lp_do_fetch_single()

    def _lp_on_loaded(self, positions, address):
        """Render fetched LP positions as cards."""
        scroll = self._lp_widgets.get("scroll")
        status = self._lp_widgets.get("status_label")
        refresh_btn = self._lp_widgets.get("refresh_btn")
        if not scroll:
            return
        for widget in scroll.winfo_children():
            widget.destroy()
        # v5.1: Deduplicate by venue:position_id
        seen = set()
        unique_positions = []
        for pos in positions:
            key = f"{pos.venue}:{pos.position_id}"
            if key not in seen:
                seen.add(key)
                unique_positions.append(pos)
        if not unique_positions:
            ctk.CTkLabel(scroll, text=f"No LP positions found for {address}",
                         font=ctk.CTkFont(size=13), text_color=("#555555", "gray60")).pack(pady=20)
        else:
            for pos in unique_positions:
                self._lp_initialize_tracking(pos, address)
                self._lp_render_card(pos)
        if status:
            status.configure(text=f"Last check: {len(unique_positions)} position(s)")
        self._lp_update_button_states()
        # v5.1: Update saved-pools counter after rendering live cards
        self._lp_update_saved_pools_count(address)

    def _lp_initialize_tracking(self, position, wallet_address: str):
        """Initialize saved-pool tracking for a position if missing.

        If the position matches a saved pool without tracking data, seed it with
        the current position value as the initial deposit. The caller must later
        save the vault to persist changes.
        """
        if not (
            position.position_id.startswith("hyperevm:") or
            position.position_id.startswith("bsc:") or
            position.position_id.startswith("base:")
        ):
            return
        try:
            raw_id = position.position_id.split(":", 1)[1]
            token_id = int(raw_id)
        except (ValueError, IndexError):
            return
        if position.position_id.startswith("bsc:"):
            venue = "BSC"
        elif position.position_id.startswith("base:"):
            venue = "Aerodrome"
        else:
            venue = "HyperEVM"
        if not is_pool_saved(self.gui.key_manager.address_db, token_id, venue):
            return
        tracking = get_position_tracking(self.gui.key_manager.address_db, token_id, venue)
        if not tracking.get("first_seen_date"):
            update_position_tracking(
                self.gui.key_manager.address_db,
                token_id,
                venue,
                current_value_usd=position.current_value_usd,
            )

    def _lp_on_error(self, message: str):
        """Show an error in the LP tab."""
        scroll = self._lp_widgets.get("scroll")
        status = self._lp_widgets.get("status_label")
        refresh_btn = self._lp_widgets.get("refresh_btn")
        if scroll:
            for widget in scroll.winfo_children():
                widget.destroy()
            ctk.CTkLabel(scroll, text=f"Error: {message}",
                         font=ctk.CTkFont(size=12), text_color="#ff6b6b").pack(pady=20)
        if status:
            status.configure(text="Fetch failed")
        if refresh_btn:
            refresh_btn.configure(state="normal" if self.gui.online_mode else "disabled")
        fetch_pos_btn = self._lp_widgets.get("fetch_pos_btn")
        if fetch_pos_btn:
            fetch_pos_btn.configure(state="normal" if self.gui.online_mode else "disabled")

    def _lp_render_card(self, position):
        """Render one LPPosition as a card matching v4.1 address card style."""
        scroll = self._lp_widgets.get("scroll")
        if not scroll:
            return
        card = ctk.CTkFrame(scroll, corner_radius=10)
        card.pack(fill="x", pady=5, padx=5)
        # v5.1: Track rendered cards by position_id so individual cards can be
        # removed without triggering a full wallet rescan.
        cards = self._lp_widgets.setdefault("position_cards", {})
        key = f"{position.venue}:{position.position_id}"
        cards[key] = card
        info = ctk.CTkFrame(card, fg_color="transparent")
        info.pack(side="left", fill="both", expand=True, padx=10, pady=8)

        # Line 1: tokens · venue · ID (on one line)
        header_text = f"{position.health_emoji} {position.pair}  ·  {position.venue}  ·  ID: {position.position_id}"
        ctk.CTkLabel(info, text=header_text,
                     font=ctk.CTkFont(size=14, weight="bold")).pack(anchor="w")

        # Wallet address (if available from saved pool)
        wallet_addr = getattr(position, "wallet_address", "")
        if wallet_addr and len(wallet_addr) >= 16:
            ctk.CTkLabel(info, text=f"Wallet: {wallet_addr[:10]}...{wallet_addr[-6:]}",
                         font=ctk.CTkFont(size=10), text_color=("#666666", "gray50")).pack(anchor="w", pady=(1, 0))

        # Line 2: Range · Current · % In/Out Range (with colored % In Range)
        range_frame = ctk.CTkFrame(info, fg_color="transparent")
        range_frame.pack(fill="x", pady=(2, 0))
        range_parts = []
        if position.range_low is not None and position.range_high is not None:
            range_parts.append(f"Range: {position.range_low:g} – {position.range_high:g}")
        if position.current_price is not None:
            range_parts.append(f"Current: {position.current_price:g}")
        if range_parts:
            ctk.CTkLabel(range_frame, text="  ·  ".join(range_parts),
                         font=ctk.CTkFont(size=11), text_color=("#444444", "gray70")).pack(side="left", anchor="w")
        if position.position_in_range_pct is not None:
            pct = position.position_in_range_pct
            in_range = 0 <= pct <= 100
            pct_color = "#51cf94" if in_range else "#ff6b6b"
            pct_text = f"  ·  {pct:.1f}% {'In Range' if in_range else 'Out of Range'}"
            ctk.CTkLabel(range_frame, text=pct_text,
                         font=ctk.CTkFont(size=11, weight="bold"),
                         text_color=pct_color).pack(side="left", anchor="w")
        elif position.range_low is not None:
            ctk.CTkLabel(range_frame, text="  ·  ? Not fetched",
                         font=ctk.CTkFont(size=11), text_color=("#666666", "gray50")).pack(side="left", anchor="w")

        # v5.1: Position range slider with marker
        if position.range_low is not None and position.range_high is not None:
            pct = position.position_in_range_pct
            in_range = pct is not None and 0 <= pct <= 100
            marker_color = "#51cf94" if in_range else "#ff6b6b"
            marker_pos = max(0, min(100, pct)) if pct is not None else 50

            # Slider container
            slider_frame = ctk.CTkFrame(info, fg_color="transparent", height=28)
            slider_frame.pack(fill="x", pady=(4, 2), padx=(10, 0))

            # Track (background bar)
            track = ctk.CTkFrame(slider_frame, height=6, corner_radius=3,
                                 fg_color="gray25")
            track.pack(fill="x", side="top", pady=(8, 0))

            # Marker (small vertical bar on the track)
            marker = ctk.CTkFrame(slider_frame, width=4, height=14, corner_radius=2,
                                  fg_color=marker_color)
            marker.place(relx=marker_pos / 100.0, rely=0.15, anchor="n")

        # Line 3: Fees (gray) · PnL (gray) · Holdings (gray) · Value (green/bold) · Suggestion (yellow/bold)
        line3_frame = ctk.CTkFrame(info, fg_color="transparent")
        line3_frame.pack(fill="x", pady=(2, 0))

        line3_gray_parts = []

        # Build the token-denominated fee breakdown from fees_earned dict.
        # For staked/special positions (fees_note is set), show ALL tokens
        # including zeros so the user can see which tokens the position earns.
        if getattr(position, "fees_note", None):
            token_fees = " · ".join(
                f"{amt:g} {sym}" for sym, amt in position.fees_earned.items()
            )
        else:
            token_fees = " · ".join(
                f"{amt:g} {sym}" for sym, amt in position.fees_earned.items() if amt
            )

        # v5.2.2: Use appropriate precision for fee amounts so tiny values
        # (e.g. 0.00032 WETH) don't get rounded to 0.
        def _fmt_fee(amt: float, sym: str) -> str:
            """Format a fee amount with appropriate precision."""
            if amt == 0:
                return f"0 {sym}"
            if amt < 0.001:
                return f"{amt:.8f} {sym}".rstrip("0").rstrip(".")
            if amt < 1:
                return f"{amt:.6f} {sym}".rstrip("0").rstrip(".")
            return f"{amt:g} {sym}"

        if getattr(position, "fees_note", None):
            token_fees = " · ".join(
                _fmt_fee(amt, sym) for sym, amt in position.fees_earned.items()
            )
        else:
            token_fees = " · ".join(
                _fmt_fee(amt, sym) for sym, amt in position.fees_earned.items() if amt
            )

        if position.fees_earned_usd is not None and position.fees_earned_usd != 0:
            fee_str = f"Fees earned: {self.gui._format_currency(position.fees_earned_usd)}"
            if token_fees:
                fee_str += f" ({token_fees})"
            line3_gray_parts.append(fee_str)
            # Also show fees_note as supplementary info (e.g. "Staked · trading fees → veAERO voters")
            if getattr(position, "fees_note", None):
                line3_gray_parts.append(position.fees_note)
        elif token_fees:
            # Show token-denominated fees even when USD value is 0 or unavailable
            line3_gray_parts.append(f"Fees: {token_fees}")
            if getattr(position, "fees_note", None):
                line3_gray_parts.append(position.fees_note)
        elif getattr(position, "fees_note", None):
            # No token amounts to show, but we have a note
            line3_gray_parts.append(f"Fees: {position.fees_note}")
        if position.pnl_usd is not None:
            sign = "+" if position.pnl_usd >= 0 else ""
            pnl_str = f"PnL: {sign}{self.gui._format_currency(position.pnl_usd)}"
            if position.pnl_pct is not None:
                sign2 = "+" if position.pnl_pct >= 0 else ""
                pnl_str += f" ({sign2}{position.pnl_pct:.2f}%)"
            line3_gray_parts.append(pnl_str)
        if position.apy is not None:
            line3_gray_parts.append(f"APR: {position.apy:.2f}%")
        if position.days_active is not None and position.days_active > 0:
            line3_gray_parts.append(f"Active: {position.days_active}d")
        if position.deposit_amounts:
            holdings_parts = [f"{amt:g} {sym}" for sym, amt in position.deposit_amounts.items() if amt]
            if holdings_parts:
                line3_gray_parts.append(f"Holdings: {' · '.join(holdings_parts)}")

        if line3_gray_parts:
            ctk.CTkLabel(line3_frame, text="  ·  ".join(line3_gray_parts),
                         font=ctk.CTkFont(size=11), text_color=("#444444", "gray70")).pack(side="left", anchor="w")

        # Value — green and bold, after Holdings, before Suggestion
        if position.current_value_usd is not None:
            ctk.CTkLabel(line3_frame, text=f"  ·  Value: {self.gui._format_currency(position.current_value_usd)}",
                         font=ctk.CTkFont(size=11, weight="bold"),
                         text_color="#51cf94").pack(side="left", anchor="w")

            # v5.1.1: Add / Remove / Edit liquidity icons (HyperEVM only for now)
            if position.position_id and position.position_id.startswith("hyperevm:"):
                status_label = self._lp_widgets.get("status_label")

                add_btn = ctk.CTkButton(line3_frame, text="+", width=26, height=26,
                                        font=ctk.CTkFont(size=14, weight="bold"),
                                        fg_color=("#20c997", "#1aa179"),
                                        hover_color=("#1aa179", "#158f63"),
                                        command=lambda pos=position: self._lp_open_add_liquidity(pos))
                add_btn.pack(side="left", padx=(8, 2), anchor="w")
                if status_label:
                    _lp_tooltip(add_btn, status_label, "Add Liquidity")

                remove_btn = ctk.CTkButton(line3_frame, text="−", width=26, height=26,
                                           font=ctk.CTkFont(size=14, weight="bold"),
                                           fg_color=("#fd7e14", "#dc6602"),
                                           hover_color=("#dc6602", "#b85700"),
                                           command=lambda pos=position: self._lp_open_remove_liquidity(pos))
                remove_btn.pack(side="left", padx=2, anchor="w")
                if status_label:
                    _lp_tooltip(remove_btn, status_label, "Remove Liquidity")

                edit_btn = ctk.CTkButton(line3_frame, text="✎", width=26, height=26,
                                         font=ctk.CTkFont(size=12),
                                         fg_color=("#6f42c1", "#5a32a3"),
                                         hover_color=("#5a32a3", "#42288a"),
                                         command=lambda pos=position: self._lp_open_edit_position(pos))
                edit_btn.pack(side="left", padx=2, anchor="w")
                if status_label:
                    _lp_tooltip(edit_btn, status_label, "Edit Position")

        # Suggestion — yellow and bold, at the end
        if position.suggested_action:
            ctk.CTkLabel(line3_frame, text=f"  ·  Suggestion: {position.suggested_action}",
                         font=ctk.CTkFont(size=11, weight="bold"),
                         text_color=("#fd7e14", "#ffd43b")).pack(side="left", anchor="w")

        if position.error:
            ctk.CTkLabel(info, text=f"Note: {position.error}",
                         font=ctk.CTkFont(size=10), text_color=("#666666", "gray50")).pack(anchor="w", pady=(2, 0))

        # Compact action buttons — single row, small icons
        button_frame = ctk.CTkFrame(card, fg_color="transparent")
        button_frame.pack(side="right", padx=8, pady=8)

        # Row 1: Collect + Compound + Close (action buttons)
        action_row = ctk.CTkFrame(button_frame, fg_color="transparent")
        action_row.pack(fill="x")

        status_label = self._lp_widgets.get("status_label")
        can_manage_lp = position.position_id and (
            position.position_id.startswith("hyperevm:") or
            position.position_id.startswith("bsc:") or
            position.position_id.startswith("base:")
        )

        if can_manage_lp:
            collect_btn = ctk.CTkButton(action_row, text="💰 Collect", width=75, height=24,
                              font=ctk.CTkFont(size=9, weight="bold"),
                              fg_color=("#fd7e14", "#dc6602"),
                              command=lambda pos=position: self._lp_collect_fees_dialog(pos))
            collect_btn.pack(side="left", padx=1)
            if status_label:
                _lp_tooltip(collect_btn, status_label, "Collect Fees")

            compound_btn = ctk.CTkButton(action_row, text="🔄 Compound", width=85, height=24,
                              font=ctk.CTkFont(size=9, weight="bold"),
                              fg_color=("#20c997", "#1aa179"),
                              command=lambda pos=position: self._lp_compound_fees_dialog(pos))
            compound_btn.pack(side="left", padx=1)
            if status_label:
                _lp_tooltip(compound_btn, status_label, "Compound Fees")

            close_btn = ctk.CTkButton(action_row, text="✕ Close", width=65, height=24,
                              font=ctk.CTkFont(size=9, weight="bold"),
                              fg_color=("#6f42c1", "#5a32a3"),
                              hover_color=("#5a32a3", "#42288a"),
                              command=lambda pos=position: self._lp_close_position_dialog(pos))
            close_btn.pack(side="left", padx=1)
            if status_label:
                _lp_tooltip(close_btn, status_label, "Close Position")

        # Row 2: Copy + Save/Remove (utility buttons)
        util_row = ctk.CTkFrame(button_frame, fg_color="transparent")
        util_row.pack(fill="x", pady=(2, 0))

        if position.position_id:
            copy_btn = ctk.CTkButton(util_row, text="📋 Copy", width=65, height=22,
                              font=ctk.CTkFont(size=9),
                              fg_color="gray40",
                              command=lambda pid=position.position_id: self.gui.copy_to_clipboard(pid))
            copy_btn.pack(side="left", padx=1)

        if can_manage_lp:
            try:
                _token_id = int(position.position_id.split(":", 1)[1])
            except (ValueError, IndexError):
                _token_id = 0
            _venue = position.venue or "HyperEVM"
            if _token_id and is_pool_saved(self.gui.key_manager.address_db, _token_id, _venue):
                remove_btn = ctk.CTkButton(util_row, text="✕ Remove", width=70, height=22,
                                  font=ctk.CTkFont(size=9),
                                  fg_color=("#dc3545", "#c82333"),
                                  hover_color=("#c82333", "#a71d2a"),
                                  command=lambda pos=position: self._lp_remove_pool(pos))
                remove_btn.pack(side="left", padx=1)
            else:
                save_btn = ctk.CTkButton(util_row, text="★ Save", width=60, height=22,
                                  font=ctk.CTkFont(size=9),
                                  fg_color=("#0d6efd", "#0b5ed7"),
                                  command=lambda pos=position: self._lp_save_pool(pos))
                save_btn.pack(side="left", padx=1)

    def _lp_save_pool(self, position):
        """Save the current position's public identifiers to saved_pools.json."""
        if not position.position_id:
            return
        if not (
            position.position_id.startswith("hyperevm:") or
            position.position_id.startswith("bsc:") or
            position.position_id.startswith("base:")
        ):
            self.gui.show_notification("Only HyperEVM, BSC and BASE positions can be saved")
            return
        # Extract token_id from "<prefix>:<token_id>"
        try:
            token_id = int(position.position_id.split(":", 1)[1])
        except (ValueError, IndexError):
            self.gui.show_notification("Could not parse token ID", error=True)
            return
        # Resolve wallet address from the current selector mode, falling back
        # to the address captured at fetch time if the widget state changed.
        selector = self._lp_widgets.get("selector_menu")
        mode = selector.get() if selector else "Address"
        wallet_address = self._lp_get_current_wallet_address()
        if not wallet_address:
            wallet_address = getattr(self, "_lp_last_fetched_address", "")
        if not wallet_address:
            if mode == "Account":
                self.gui.show_notification("Select an account first", error=True)
            else:
                self.gui.show_notification("Enter a wallet address first", error=True)
            return
        pool_address = position.pool_id or ""
        pair = position.pair or ""
        venue = position.venue or "HyperEVM"
        print(f"[saved_pools] saving token_id={token_id} to address_db id={id(self.gui.key_manager.address_db)}")
        ok = save_pool(self.gui.key_manager.address_db, wallet_address, token_id, venue, pool_address, pair)
        if ok:
            # Re-encrypt the vault to persist the saved pool
            ok = self.gui.key_manager.save_encrypted_data(self.gui.current_password)
            print(f"[saved_pools] vault saved, saved_pools count={len(self.gui.key_manager.address_db.get('saved_pools', []))}")
        if ok:
            self.gui.show_notification(f"Pool saved: {pair} (#{token_id})")
        else:
            self.gui.show_notification("Failed to save pool", error=True)

    def _lp_render_saved_placeholders(self, address: str):
        """Render placeholder cards for saved pools immediately from cache.

        Called at the start of _lp_do_fetch() so the user sees cached pool
        data (pair, venue, token_id) while the full wallet scan runs in the
        background.  When the scan completes, _lp_on_loaded() clears the
        scroll frame and re-renders with live data — replacing these
        placeholders automatically.
        """
        if not self.gui.key_manager:
            return
        scroll = self._lp_widgets.get("scroll")
        if not scroll:
            return
        saved = load_saved_pools(self.gui.key_manager.address_db, wallet_address=address)
        if not saved:
            return
        for entry in saved:
            tid = entry.get("token_id")
            venue = entry.get("venue", "HyperEVM")
            pair = entry.get("pair", "Unknown Pair")
            if not tid:
                continue
            if venue == "HyperEVM":
                prefix = "hyperevm"
            elif venue == "Aerodrome":
                prefix = "base"
            else:
                prefix = "bsc"
            # Placeholder card
            card = ctk.CTkFrame(scroll, corner_radius=10)
            card.pack(fill="x", pady=5, padx=5)
            info = ctk.CTkFrame(card, fg_color="transparent")
            info.pack(side="left", fill="both", expand=True, padx=10, pady=8)

            header_text = f"\u23F3 {pair}  \u00b7  {venue}"
            ctk.CTkLabel(info, text=header_text,
                         font=ctk.CTkFont(size=14, weight="bold")).pack(anchor="w")
            ctk.CTkLabel(info, text=f"ID: {prefix}:{tid}",
                         font=ctk.CTkFont(size=10), text_color=("#666666", "gray50")).pack(anchor="w", pady=(2, 0))
            if venue == "HyperEVM":
                platform_label = "Platform: HyperEVM (Project X)"
            elif venue == "Aerodrome":
                platform_label = "Platform: Aerodrome (BASE)"
            else:
                platform_label = "Platform: BSC (BNB Chain)"
            ctk.CTkLabel(info, text=platform_label,
                         font=ctk.CTkFont(size=11), text_color=("#444444", "gray70")).pack(anchor="w", pady=(2, 0))
            ctk.CTkLabel(info, text="Fetching live data...",
                         font=ctk.CTkFont(size=11), text_color=("#666666", "gray50")).pack(anchor="w", pady=(2, 0))

            button_frame = ctk.CTkFrame(card, fg_color="transparent")
            button_frame.pack(side="right", padx=10, pady=8)

            # Remove Pool button on placeholder (same logic as live cards)
            class _PlaceholderPos:
                def __init__(self, position_id, pair, venue):
                    self.position_id = position_id
                    self.pair = pair
                    self.venue = venue
                    self.pool_id = ""

            ph_pos = _PlaceholderPos(f"{prefix}:{tid}", pair, venue)
            ctk.CTkButton(button_frame, text="Remove Pool", width=90, height=26,
                          font=ctk.CTkFont(size=10),
                          fg_color=("#dc3545", "#c82333"),
                          hover_color=("#c82333", "#a71d2a"),
                          command=lambda pos=ph_pos, card=card: self._lp_remove_pool(pos, card)
                          ).pack(pady=2)

    def _lp_render_all_saved_placeholders(self):
        """Render ALL saved pools from the encrypted vault as placeholder cards.

        Shows known details (pair, venue, token_id, range) from the saved snapshot.
        Does NOT require a wallet address — shows all saved pools regardless of
        wallet. Live data (current ratio, fees, value) is shown as 'Not fetched'
        until the user clicks Fetch or Scan Wallet.
        """
        if not self.gui.key_manager:
            return
        scroll = self._lp_widgets.get("scroll")
        if not scroll:
            return
        # Don't clear if cards are already showing (live data takes priority)
        if scroll.winfo_children():
            return
        all_saved = load_saved_pools(self.gui.key_manager.address_db)
        if not all_saved:
            return

        class _SavedPos:
            def __init__(self, position_id, pair, venue, pool_id, wallet_address):
                self.position_id = position_id
                self.pair = pair
                self.venue = venue
                self.pool_id = pool_id
                self.wallet_address = wallet_address

        for entry in all_saved:
            tid = entry.get("token_id")
            venue = entry.get("venue", "HyperEVM")
            pair = entry.get("pair", "Unknown Pair")
            pool_address = entry.get("pool_address", "")
            wallet_address = entry.get("wallet_address", "")
            if not tid:
                continue
            if venue == "HyperEVM":
                prefix = "hyperevm"
            elif venue == "Aerodrome":
                prefix = "base"
            else:
                prefix = "bsc"
            card = ctk.CTkFrame(scroll, corner_radius=10)
            card.pack(fill="x", pady=5, padx=5)
            info = ctk.CTkFrame(card, fg_color="transparent")
            info.pack(side="left", fill="both", expand=True, padx=10, pady=8)

            header_text = f"⏳ {pair}  ·  {venue}  ·  ID: {prefix}:{tid}"
            ctk.CTkLabel(info, text=header_text,
                         font=ctk.CTkFont(size=14, weight="bold")).pack(anchor="w")
            if pool_address:
                ctk.CTkLabel(info, text=f"Pool: {pool_address[:20]}...",
                             font=ctk.CTkFont(size=10), text_color=("#666666", "gray50")).pack(anchor="w", pady=(2, 0))
            ctk.CTkLabel(info, text="Range: Not fetched · Current: Not fetched",
                         font=ctk.CTkFont(size=11), text_color=("#666666", "gray50")).pack(anchor="w", pady=(2, 0))
            ctk.CTkLabel(info, text="Fees: Not fetched · Value: Not fetched · Holdings: Not fetched",
                         font=ctk.CTkFont(size=11), text_color=("#666666", "gray50")).pack(anchor="w", pady=(2, 0))
            wallet_display = (
                f"Wallet: {wallet_address[:10]}...{wallet_address[-6:]}"
                if len(wallet_address) > 16
                else f"Wallet: {wallet_address}"
            )
            ctk.CTkLabel(info, text=wallet_display,
                         font=ctk.CTkFont(size=10), text_color=("#666666", "gray50")).pack(anchor="w", pady=(2, 0))

            button_frame = ctk.CTkFrame(card, fg_color="transparent")
            button_frame.pack(side="right", padx=10, pady=8)

            saved_pos = _SavedPos(f"{prefix}:{tid}", pair, venue, pool_address, wallet_address)
            ctk.CTkButton(button_frame, text="Fetch", width=70, height=26,
                          font=ctk.CTkFont(size=10),
                          command=lambda pos=saved_pos: self._lp_fetch_saved_single(pos)
                          ).pack(pady=2)
            ctk.CTkButton(button_frame, text="Reassign", width=70, height=26,
                          font=ctk.CTkFont(size=10),
                          fg_color=("#0d6efd", "#0b5ed7"),
                          hover_color=("#0b5ed7", "#0a4fdb"),
                          command=lambda pos=saved_pos, card=card: self._lp_reassign_pool_wallet(pos, card)
                          ).pack(pady=2)
            ctk.CTkButton(button_frame, text="Remove Pool", width=90, height=26,
                          font=ctk.CTkFont(size=10),
                          fg_color=("#dc3545", "#c82333"),
                          hover_color=("#c82333", "#a71d2a"),
                          command=lambda pos=saved_pos, card=card: self._lp_remove_pool(pos, card)
                          ).pack(pady=2)

        status = self._lp_widgets.get("status_label")
        if status:
            status.configure(
                text=f"Showing {len(all_saved)} saved pool(s) (cached — click Fetch for live data)"
            )

    def _lp_reassign_pool_wallet(self, saved_pos, card):
        """Reassign a saved pool to a different wallet address.

        Opens a dialog showing all vault accounts with EVM addresses,
        letting the user pick the correct account for this pool.
        """
        from tkinter import messagebox, simpledialog, Toplevel, StringVar
        import tkinter as tk

        if not self.gui.key_manager:
            return

        # Build a list of vault accounts with EVM addresses
        accounts_data = self.gui.key_manager.address_db.get("accounts", {})
        account_options = []
        address_map = {}  # display_string -> (account_name, evm_address)

        for acct_name in sorted(accounts_data.keys()):
            addresses = accounts_data.get(acct_name, {}).get("addresses", [])
            for addr_entry in addresses:
                coin = addr_entry.get("coin", "").lower()
                chain = addr_entry.get("chain", "").lower()
                if "evm" in coin or "evm" in chain or "hype" in coin or "hype" in chain:
                    evm_addr = addr_entry.get("address", "")
                    if evm_addr:
                        display = f"{acct_name} — {evm_addr[:8]}...{evm_addr[-6:]}"
                        account_options.append(display)
                        address_map[display] = (acct_name, evm_addr)

        if not account_options:
            self.gui.show_notification("No vault accounts with EVM addresses found", error=True)
            return

        # Also add a manual entry option
        account_options.append("Manual address entry...")

        # Show selection dialog
        dialog = Toplevel(self.gui.root)
        dialog.title("Reassign Pool Wallet")
        dialog.geometry("450x350")
        dialog.transient(self.gui.root)
        dialog.grab_set()

        ctk.CTkLabel(dialog, text=f"Reassign: {saved_pos.pair} ({saved_pos.position_id})",
                     font=ctk.CTkFont(size=14, weight="bold")).pack(pady=(10, 5), padx=15, anchor="w")
        current_addr = saved_pos.wallet_address or ""
        if current_addr:
            ctk.CTkLabel(dialog, text=f"Current wallet: {current_addr[:10]}...{current_addr[-6:]}",
                         font=ctk.CTkFont(size=11), text_color=("#666666", "gray50")).pack(pady=(0, 10), padx=15, anchor="w")

        listbox = tk.Listbox(dialog, height=10,
                             font=("TkDefaultFont", 11), selectbackground="#0d6efd",
                             selectforeground="white")
        for opt in account_options:
            listbox.insert("end", opt)
        listbox.selection_set(0)
        listbox.pack(fill="both", expand=True, padx=15, pady=(0, 10))

        def _on_select():
            sel = listbox.curselection()
            if not sel:
                return
            choice = account_options[sel[0]]

            if choice == "Manual address entry...":
                manual_addr = simpledialog.askstring(
                    "Manual Address", "Enter the EVM wallet address:",
                    parent=dialog
                )
                if not manual_addr or not manual_addr.strip().startswith("0x") or len(manual_addr.strip()) != 42:
                    self.gui.show_notification("Invalid address", error=True)
                    return
                new_wallet = manual_addr.strip()
            else:
                _, new_wallet = address_map[choice]

            # Parse token_id and venue from position_id
            prefix = "hyperevm"
            try:
                pos_id = saved_pos.position_id
                if ":" in pos_id:
                    prefix, tid_str = pos_id.split(":", 1)
                    tid = int(tid_str)
                else:
                    tid = int(pos_id)

                if prefix == "base":
                    venue = "Aerodrome"
                elif prefix == "bsc":
                    venue = "BSC"
                else:
                    venue = "HyperEVM"
            except (ValueError, IndexError):
                self.gui.show_notification("Could not parse position ID", error=True)
                dialog.destroy()
                return

            # Update the saved pool entry
            ok = update_saved_pool_wallet(
                self.gui.key_manager.address_db, tid, venue, new_wallet
            )
            if ok:
                # Re-encrypt the vault to persist
                self.gui.key_manager.save_encrypted_data(self.gui.current_password)
                self.gui.show_notification(
                    f"Pool reassigned to {new_wallet[:10]}...{new_wallet[-6:]}"
                )
                dialog.destroy()
                # Re-render the saved pool cards
                self._lp_clear_single()
                self._lp_render_all_saved_placeholders()
                if self.gui.online_mode:
                    self._lp_auto_fetch_all_saved()
            else:
                self.gui.show_notification("Failed to reassign pool", error=True)
                dialog.destroy()

        ctk.CTkButton(dialog, text="Reassign", command=_on_select,
                      font=ctk.CTkFont(size=12, weight="bold")).pack(pady=(0, 10), padx=15)

        dialog.bind("<Return>", lambda e: _on_select())
        dialog.bind("<Escape>", lambda e: dialog.destroy())

    def _lp_remove_pool(self, position, card_frame=None):
        """Remove a saved pool from the encrypted vault.

        Args:
            position: Object with position_id ("hyperevm:<token_id>" or
                "bsc:<token_id>"), venue, pair.
            card_frame: Optional card widget to destroy directly (used by
                placeholder cards that are not tracked in position_cards).
        """
        if not position.position_id or not (
            position.position_id.startswith("hyperevm:") or
            position.position_id.startswith("bsc:") or
            position.position_id.startswith("base:")
        ):
            self.gui.show_notification("Only HyperEVM, BSC and BASE positions can be removed")
            return
        try:
            token_id = int(position.position_id.split(":", 1)[1])
        except (ValueError, IndexError):
            self.gui.show_notification("Could not parse token ID", error=True)
            return
        venue = position.venue or "HyperEVM"
        pair = position.pair or "Unknown"
        ok = remove_saved_pool(self.gui.key_manager.address_db, token_id, venue)
        if ok:
            ok = self.gui.key_manager.save_encrypted_data(self.gui.current_password)
        if ok:
            self.gui.show_notification(f"Pool removed: {pair} (#{token_id})")
            # Remove only this pool's card from the scroll frame — do not trigger
            # a full wallet rescan.
            entry = self._lp_widgets.get("address_entry")
            addr = entry.get().strip() if entry else ""
            if card_frame:
                card_frame.destroy()
            else:
                if position.position_id.startswith("bsc:"):
                    position_prefix = "bsc"
                elif position.position_id.startswith("base:"):
                    position_prefix = "base"
                else:
                    position_prefix = "hyperevm"
                card_key = f"{venue}:{position_prefix}:{token_id}"
                cards = self._lp_widgets.get("position_cards", {})
                card_frame = cards.pop(card_key, None)
                if card_frame:
                    card_frame.destroy()
            self._lp_update_saved_pools_count(addr)
            scroll = self._lp_widgets.get("scroll")
            status = self._lp_widgets.get("status_label")
            cards = self._lp_widgets.get("position_cards", {})
            remaining = len(cards)
            if scroll and not scroll.winfo_children():
                ctk.CTkLabel(scroll, text="No LP positions found",
                             font=ctk.CTkFont(size=13), text_color=("#555555", "gray60")).pack(pady=20)
            if status:
                status.configure(text=f"Last check: {remaining} position(s)")
        else:
            self.gui.show_notification("Failed to remove pool", error=True)

    def _lp_clear_single(self):
        """Clear the position entry, rendered cards, and status label (v5.1)."""
        pos_entry = self._lp_widgets.get("position_entry")
        if pos_entry:
            pos_entry.delete(0, "end")
        scroll = self._lp_widgets.get("scroll")
        if scroll:
            for widget in scroll.winfo_children():
                widget.destroy()
        status = self._lp_widgets.get("status_label")
        if status:
            status.configure(text="")
        # Update saved-pools counter for current address
        entry = self._lp_widgets.get("address_entry")
        addr = entry.get().strip() if entry else ""
        self._lp_update_saved_pools_count(addr)

    def _lp_refresh_position_fees(self, position_id: str, wallet_address: str):
        """Refresh just the fee display for a single LP position after collect/compound.

        Re-reads the position data from the adapter and updates the card in-place,
        without triggering a full wallet rescan.

        Args:
            position_id: The position ID (e.g., 'hyperevm:512359' or 'bsc:2242261')
            wallet_address: The wallet address for fee reading
        """
        if not self.gui.lp_engine or not self.gui.online_mode:
            return

        def _refresh_thread():
            try:
                raw_id = position_id
                if ":" in raw_id:
                    raw_id = raw_id.split(":", 1)[1]
                numeric_tid = int(raw_id)

                if position_id.startswith("bsc:"):
                    from venue_adapters.bsc_adapter import BSCAdapter
                    adapter = BSCAdapter()
                    venue = "BSC"
                    tracking = get_position_tracking(self.gui.key_manager.address_db, numeric_tid, "BSC")
                    fresh_pos = adapter._fetch_position_by_token_id(
                        numeric_tid, self.gui.price_engine, wallet_address=wallet_address
                    )
                elif position_id.startswith("base:"):
                    from venue_adapters.aerodrome_adapter import AerodromeAdapter
                    adapter = AerodromeAdapter()
                    venue = "Aerodrome"
                    tracking = get_position_tracking(self.gui.key_manager.address_db, numeric_tid, "Aerodrome")
                    fresh_pos = adapter._fetch_position_by_token_id(
                        numeric_tid, self.gui.price_engine, wallet_address=wallet_address
                    )
                else:
                    from venue_adapters.hyperliquid_adapter import HyperliquidAdapter
                    adapter = HyperliquidAdapter()
                    venue = "HyperEVM"
                    tracking = get_position_tracking(self.gui.key_manager.address_db, numeric_tid, "HyperEVM")
                    fresh_pos = adapter.fetch_evm_position_by_token_id(
                        numeric_tid, self.gui.price_engine, wallet_address=wallet_address
                    )

                if fresh_pos and not fresh_pos.error:
                    if tracking:
                        fresh_pos = self._lp_merge_tracking(fresh_pos, tracking)
                    self.gui.root.after(0, lambda: self._lp_update_card_fees(position_id, fresh_pos, venue))
                else:
                    self.gui.root.after(0, lambda: self._lp_update_card_fees_zero(position_id, venue))
            except Exception as e:
                print(f"[refresh_fees] error: {e}")
                self.gui.root.after(0, lambda: self._lp_update_card_fees_zero(position_id, ""))

        threading.Thread(target=_refresh_thread, daemon=True).start()

    def _lp_merge_tracking(self, position, tracking: Dict[str, Any]):
        """Recompute LPPosition tracking fields from saved-pools tracking data."""
        initial_deposit = tracking.get("initial_deposit_usd")
        total_fees = tracking.get("total_fees_collected_usd", 0.0)
        first_seen = tracking.get("first_seen_date")

        if initial_deposit and initial_deposit > 0 and position.current_value_usd:
            position.pnl_usd = (position.current_value_usd + total_fees) - initial_deposit
            position.pnl_pct = (position.pnl_usd / initial_deposit) * 100

        if first_seen:
            try:
                from datetime import datetime, timezone
                first_dt = datetime.fromisoformat(first_seen.replace("Z", "+00:00"))
                position.days_active = (datetime.now(timezone.utc) - first_dt).days
            except Exception:
                position.days_active = None

        if (
            initial_deposit
            and initial_deposit > 0
            and total_fees > 0
            and position.days_active
            and position.days_active > 0
        ):
            position.apy = (total_fees / initial_deposit) * (365 / position.days_active) * 100

        return position

    def _lp_update_card_fees(self, position_id: str, fresh_pos, venue: str = "HyperEVM"):
        """Update a single position card with fresh fee data (in-place).

        Args:
            position_id: The position ID (e.g., 'hyperevm:512359' or 'bsc:2242261')
            fresh_pos: The fresh LPPosition with updated fees
            venue: The venue name used as the card key prefix
        """
        cards = self._lp_widgets.get("position_cards", {})
        key = f"{venue}:{position_id}"
        card = cards.get(key)
        if not card:
            card = cards.get(position_id)
        if not card:
            print(f"[update_card_fees] card not found for {key}, doing auto-fetch")
            self._lp_auto_fetch_all_saved()
            return

        # Destroy the old card and re-render with fresh data in the same position
        card.destroy()
        cards.pop(key, None)
        cards.pop(position_id, None)
        self._lp_render_card(fresh_pos)

        status = self._lp_widgets.get("status_label")
        if status:
            status.configure(text=f"Fees updated for {fresh_pos.pair}")

    def _lp_update_card_fees_zero(self, position_id: str, venue: str = ""):
        """Fallback: refresh all saved pools after a collect operation.

        Does NOT write to the position entry or trigger a single-position fetch
        (which would clear the screen). Instead re-fetches all saved pools.
        """
        self._lp_auto_fetch_all_saved()

    def _lp_open_add_liquidity(self, position):
        """Open the Add Liquidity dialog from the new module."""
        wallet_address = self._lp_get_current_wallet_address()
        if not wallet_address:
            self.gui.show_notification("No wallet address selected", error=True)
            return
        open_add_liquidity(self, position, self.gui.key_manager, self.gui.price_engine, wallet_address)

    def _lp_open_remove_liquidity(self, position):
        """Open the Remove Liquidity dialog from the new module."""
        wallet_address = self._lp_get_current_wallet_address()
        if not wallet_address:
            self.gui.show_notification("No wallet address selected", error=True)
            return
        open_remove_liquidity(self, position, self.gui.key_manager, self.gui.price_engine, wallet_address)

    def _lp_open_edit_position(self, position):
        """Open the Edit Position stub from the new module."""
        open_edit_position(self, position)

    def _lp_get_chain_info(self, position_id: str) -> tuple:
        """Return (chain_name, gas_token, venue_key) for a position_id.

        Args:
            position_id: e.g. 'hyperevm:2242261' or 'bsc:2242261'

        Returns:
            Tuple of (chain_display_name, gas_token_symbol, writer_venue_key).
        """
        if position_id.startswith("bsc:"):
            return ("BNB Chain (BSC)", "BNB", "bsc")
        elif position_id.startswith("base:"):
            return ("BASE", "ETH", "aerodrome")
        elif position_id.startswith("hyperevm:"):
            return ("HyperEVM", "HYPE", "hyperliquid")
        else:
            return ("HyperEVM", "HYPE", "hyperliquid")

    def _lp_resolve_wallet_for_position(self, position) -> tuple:
        """Resolve (wallet_address, account_name) for a position.

        Tries in order:
        1. The address entry widget
        2. The _lp_last_fetched_address from the fetch
        3. The saved pool's wallet_address (by looking up the position in saved_pools)

        Returns:
            Tuple of (wallet_address, account_name). May be ("", "") if unresolvable.
        """
        # 1. Try the address entry
        wallet_address = self._lp_get_current_wallet_address()
        if not wallet_address:
            wallet_address = getattr(self, "_lp_last_fetched_address", "")

        # 2. Try to find the wallet from saved pools
        if not wallet_address and self.gui.key_manager and position.position_id:
            try:
                raw_id = position.position_id.split(":", 1)[1]
                token_id = int(raw_id)
                venue = position.venue or "HyperEVM"
                all_saved = load_saved_pools(self.gui.key_manager.address_db)
                for entry in all_saved:
                    if entry.get("token_id") == token_id and entry.get("venue", "") == venue:
                        wallet_address = entry.get("wallet_address", "")
                        break
            except (ValueError, IndexError):
                pass

        # Pre-fill the address entry so the user sees which wallet is being used
        if wallet_address:
            entry = self._lp_widgets.get("address_entry")
            if entry and not entry.get().strip():
                entry.delete(0, "end")
                entry.insert(0, wallet_address)

        # 3. Resolve account name from wallet address
        account_name = ""
        if wallet_address and self.gui.key_manager:
            accounts_data = self.gui.key_manager.address_db.get("accounts", {})
            for acct, data in accounts_data.items():
                for addr in data.get("addresses", []):
                    if addr.get("address", "").lower() == wallet_address.lower():
                        account_name = acct
                        break
                if account_name:
                    break

        # Also try _lp_get_current_account_name as fallback
        if not account_name:
            account_name = self._lp_get_current_account_name()

        return wallet_address, account_name

    def _lp_compound_fees_dialog(self, position):
        """Show confirmation dialog and compound fees for an LP position."""
        from tkinter import messagebox
        wallet_address, account_name = self._lp_resolve_wallet_for_position(position)
        if not wallet_address:
            self.gui.show_notification("Could not resolve wallet address for this position", error=True)
            return
        if not account_name:
            self.gui.show_notification("Could not resolve vault account for this address", error=True)
            return

        chain_name, gas_token, venue_key = self._lp_get_chain_info(position.position_id)

        # BSC compound is not yet supported (requires swap implementation)
        if position.position_id.startswith("bsc:"):
            self.gui.show_notification(
                "Compound fees is not yet implemented on BSC. Use Collect Fees instead."
            )
            return

        confirm = messagebox.askyesno(
            "Confirm: Compound Fees",
            "You are about to compound fees for position:\n"
            f"  {position.pair} ({position.position_id})\n\n"
            f"This will submit multiple transactions on {chain_name}:\n"
            "  1. Collect accrued fees\n"
            "  2. Swap to optimal ratio (if needed)\n"
            "  3. Increase liquidity with collected amounts\n\n"
            f"Ensure your wallet has {gas_token} for gas.\n"
            "The key_manager_agent must be running and unlocked.\n\n"
            "Continue?",
        )
        if not confirm:
            return
        self.gui.show_notification("Compounding fees... (multi-TX operation)")

        def _do_compound():
            try:
                writer = self.gui.lp_engine.get_writer(venue_key, self.gui.current_password)
                if writer is None:
                    self.gui.root.after(0, lambda: self.gui.show_notification(
                        "Writer not available", error=True))
                    return
                if not writer.is_available():
                    self.gui.root.after(0, lambda: self.gui.show_notification(
                        "Agent not running. Start key_manager_agent with --serve.", error=True))
                    return
                tx_hashes = writer.compound_fees(CompoundFeesParams(
                    account=account_name,
                    position_id=position.position_id,
                ))
                if tx_hashes:
                    self.gui.root.after(0, lambda: self.gui.show_notification(
                        f"Compound fees done. {len(tx_hashes)} TXs submitted. First: {tx_hashes[0][:20]}..."))
                    # Record collected fees in saved-pools tracking
                    self._lp_record_fee_collection(position.position_id, wallet_address, tx_hashes)
                    # Wait for the last tx to be mined, then refresh fees
                    self.gui.root.after(8000, lambda: self._lp_refresh_position_fees(
                        position.position_id, wallet_address))
                else:
                    self.gui.root.after(0, lambda: self.gui.show_notification(
                        "Compound fees: no transactions submitted", error=True))
            except Exception as e:
                error_msg = str(e)
                print(f"[compound_fees] error: {error_msg}")
                if "insufficient funds" in error_msg.lower() or "Insufficient gas" in error_msg:
                    error_msg = (
                        f"Wallet has no {gas_token} for gas. Send {gas_token} to your wallet address "
                        f"to pay for transactions. (Details: {error_msg})"
                    )
                elif "nonce too high" in error_msg.lower():
                    error_msg = (
                        "Transaction rejected (nonce conflict). Wait a moment and try again. "
                        f"(Details: {error_msg})"
                    )
                elif "OverflowError" in error_msg or "result too large" in error_msg.lower():
                    error_msg = (
                        "Internal error during transaction signing. The transaction may have "
                        "succeeded on-chain — check Project X to verify. Try again if needed."
                    )
                self.gui.root.after(0, lambda: self.gui.show_notification(
                    f"Compound error: {error_msg}", error=True))
                # Even on error, the tx may have gone through — refresh fees after a delay
                self.gui.root.after(5000, lambda: self._lp_refresh_position_fees(
                    position.position_id, wallet_address))

        threading.Thread(target=_do_compound, daemon=True).start()

    def _lp_collect_fees_dialog(self, position):
        """Show confirmation dialog and collect fees for an LP position."""
        from tkinter import messagebox
        wallet_address, account_name = self._lp_resolve_wallet_for_position(position)
        if not wallet_address:
            self.gui.show_notification("Could not resolve wallet address for this position", error=True)
            return
        if not account_name:
            self.gui.show_notification("Could not resolve vault account for this address", error=True)
            return

        chain_name, gas_token, venue_key = self._lp_get_chain_info(position.position_id)

        confirm = messagebox.askyesno(
            "Confirm: Collect Fees",
            "You are about to collect fees for position:\n"
            f"  {position.pair} ({position.position_id})\n\n"
            f"This will spend gas on {chain_name}.\n"
            f"Ensure your wallet has {gas_token} for gas.\n"
            "The key_manager_agent must be running and unlocked.\n\n"
            "Continue?",
        )
        if not confirm:
            return
        self.gui.show_notification("Collecting fees...")

        def _do_collect():
            try:
                writer = self.gui.lp_engine.get_writer(venue_key, self.gui.current_password)
                if writer is None:
                    self.gui.root.after(0, lambda: self.gui.show_notification(
                        "Writer not available", error=True))
                    return
                if not writer.is_available():
                    self.gui.root.after(0, lambda: self.gui.show_notification(
                        "Agent not running. Start key_manager_agent with --serve.", error=True))
                    return
                tx_hash = writer.collect_fees(CollectFeesParams(
                    account=account_name,
                    position_id=position.position_id,
                    recipient=wallet_address,
                ))
                if tx_hash:
                    self.gui.root.after(0, lambda: self.gui.show_notification(
                        f"Fees collected. TX: {tx_hash[:20]}..."))
                    # Record collected fees in saved-pools tracking
                    self._lp_record_fee_collection(position.position_id, wallet_address, [tx_hash])
                    # Wait a moment for the tx to be mined, then refresh fees
                    self.gui.root.after(5000, lambda: self._lp_refresh_position_fees(
                        position.position_id, wallet_address))
                else:
                    self.gui.root.after(0, lambda: self.gui.show_notification(
                        "Collect failed: no tx hash returned", error=True))
            except Exception as e:
                error_msg = str(e)
                print(f"[collect_fees] error: {error_msg}")
                if "insufficient funds" in error_msg.lower() or "Insufficient gas" in error_msg:
                    error_msg = (
                        f"Wallet has no {gas_token} for gas. Send {gas_token} to your wallet address "
                        f"to pay for transactions. (Details: {error_msg})"
                    )
                elif "nonce too high" in error_msg.lower():
                    error_msg = (
                        "Transaction rejected (nonce conflict). Wait a moment and try again. "
                        f"(Details: {error_msg})"
                    )
                elif "OverflowError" in error_msg or "result too large" in error_msg.lower():
                    error_msg = (
                        "Internal error during transaction signing. The transaction may have "
                        "succeeded on-chain — check Project X to verify. Try again if needed."
                    )
                self.gui.root.after(0, lambda: self.gui.show_notification(
                    f"Collect error: {error_msg}", error=True))
                # Even on error, the tx may have gone through — refresh fees after a delay
                self.gui.root.after(5000, lambda: self._lp_refresh_position_fees(
                    position.position_id, wallet_address))

        threading.Thread(target=_do_collect, daemon=True).start()

    def _lp_record_fee_collection(self, position_id: str, wallet_address: str, tx_hashes: List[str]):
        """Record collected fees from a collect/compound tx into saved-pools tracking.

        This is called after a successful collect_fees or compound_fees. It reads
        the collect tx receipt to get exact fee amounts, converts them to USD, and
        updates the saved pool's cumulative fee tracking.
        """
        try:
            raw_id = position_id.split(":", 1)[1]
            token_id = int(raw_id)
        except (ValueError, IndexError):
            return

        venue = "HyperEVM"
        if not is_pool_saved(self.gui.key_manager.address_db, token_id, venue):
            return

        def _record_thread():
            try:
                from venue_adapters.hyperliquid_adapter import HyperliquidAdapter, _evm_rpc_call
                from price_engine import PriceEngine
                adapter = HyperliquidAdapter()
                # The first tx in compound_fees is always the collect tx.
                collect_tx = tx_hashes[0] if tx_hashes else ""
                if not collect_tx:
                    return

                # Parse Transfer events from the collect tx receipt to get exact fees
                receipt = _evm_rpc_call("eth_getTransactionReceipt", [collect_tx])
                fee0_raw = 0
                fee1_raw = 0
                TRANSFER_SIG = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
                if receipt and isinstance(receipt, dict):
                    wallet_lower = wallet_address.lower()
                    for log in receipt.get("logs", []):
                        topics = log.get("topics", [])
                        if len(topics) < 3:
                            continue
                        if topics[0].lower() != TRANSFER_SIG:
                            continue
                        recipient = topics[2][26:].lower()
                        if recipient != wallet_lower[2:]:
                            continue
                        token = log.get("address", "").lower()
                        amount = int(log.get("data", "0x0"), 16)
                        if token == "0x5555555555555555555555555555555555555555":
                            fee0_raw = amount
                        elif token == "0x9fdbda0a5e284c32744d2f17ee5c74b284993463":
                            fee1_raw = amount

                fee0_human = fee0_raw / 1e18
                fee1_human = fee1_raw / 1e8
                price_engine = self.gui.price_engine
                usd0 = price_engine.convert_balance_to_fiat(fee0_human, "HYPE", currency="usd") or 0.0
                usd1 = price_engine.convert_balance_to_fiat(fee1_human, "BTC", currency="usd") or 0.0
                fees_usd = usd0 + usd1

                if fees_usd <= 0:
                    return

                from datetime import datetime, timezone
                fee_event = {
                    "date": datetime.now(timezone.utc).isoformat(),
                    "fee0": fee0_human,
                    "fee1": fee1_human,
                    "usd": fees_usd,
                    "tx_hash": collect_tx,
                }
                update_position_tracking(
                    self.gui.key_manager.address_db,
                    token_id,
                    venue,
                    fees_collected_usd=fees_usd,
                    fee_event=fee_event,
                )
                # Persist vault changes
                if self.gui.current_password:
                    self.gui.key_manager.save_encrypted_data(self.gui.current_password)
            except Exception as e:
                print(f"[record_fees] error: {e}")

        threading.Thread(target=_record_thread, daemon=True).start()

    def _lp_close_position_dialog(self, position):
        """Show confirmation dialog and close an LP position completely.

        Calls decreaseLiquidity(100%) + collect() to withdraw all liquidity
        and fees, effectively closing the position.
        """
        from tkinter import messagebox
        wallet_address, account_name = self._lp_resolve_wallet_for_position(position)
        if not wallet_address:
            self.gui.show_notification("Could not resolve wallet address for this position", error=True)
            return
        if not account_name:
            self.gui.show_notification("Could not resolve vault account for this address", error=True)
            return

        chain_name, gas_token, venue_key = self._lp_get_chain_info(position.position_id)

        confirm = messagebox.askyesno(
            "Confirm: Close Position",
            "You are about to CLOSE this position completely:\n"
            f"  {position.pair} ({position.position_id})\n\n"
            "This will:\n"
            "  1. Withdraw ALL liquidity from the position\n"
            "  2. Collect any remaining fees\n\n"
            "Your position NFT will remain but with zero liquidity.\n"
            f"This will spend gas on {chain_name}.\n"
            f"Ensure your wallet has {gas_token} for gas.\n"
            "The key_manager_agent must be running and unlocked.\n\n"
            "Continue?",
        )
        if not confirm:
            return
        self.gui.show_notification("Closing position... (multi-TX operation)")

        def _do_close():
            try:
                writer = self.gui.lp_engine.get_writer(venue_key, self.gui.current_password)
                if writer is None:
                    self.gui.root.after(0, lambda: self.gui.show_notification(
                        "Writer not available", error=True))
                    return
                if not writer.is_available():
                    self.gui.root.after(0, lambda: self.gui.show_notification(
                        "Agent not running. Start key_manager_agent with --serve.", error=True))
                    return
                tx_hashes = writer.close_position(position.position_id, account_name)
                if tx_hashes:
                    if len(tx_hashes) == 2:
                        self.gui.root.after(0, lambda: self.gui.show_notification(
                            "Position closed. 2 TXs submitted (decrease + collect). Refreshing..."))
                    elif len(tx_hashes) == 1:
                        self.gui.root.after(0, lambda: self.gui.show_notification(
                            "Partially closed: liquidity removed but collect failed. "
                            "Retry 'Collect Fees' to withdraw funds. Refreshing..."))
                    time.sleep(3)
                    self.gui.root.after(0, lambda: self._lp_do_fetch())
                else:
                    self.gui.root.after(0, lambda: self.gui.show_notification(
                        "Close position: no transactions submitted", error=True))
            except Exception as e:
                error_msg = str(e)
                print(f"[close_position] error: {error_msg}")
                if "insufficient funds" in error_msg.lower() or "Insufficient gas" in error_msg:
                    error_msg = (
                        f"Wallet has no {gas_token} for gas. Send {gas_token} to your wallet address "
                        f"to pay for transactions. (Details: {error_msg})"
                    )
                self.gui.root.after(0, lambda: self.gui.show_notification(
                    f"Close error: {error_msg}", error=True))

        threading.Thread(target=_do_close, daemon=True).start()

    def _lp_rerender_cards(self):
        """Re-render existing LP position cards to reflect mode changes.

        Called when the user switches between Standard and Advanced mode.
        Preserves the current position data but re-creates the card widgets
        so buttons that are mode-dependent appear/disappear immediately.
        """
        cards = self._lp_widgets.get("position_cards", {})
        if not cards:
            return
        # We need the position objects to re-render. Since we don't store them,
        # we trigger a re-fetch of the current wallet instead.
        # But if we have saved positions data, we can re-render from that.
        # Simplest approach: re-fetch the current wallet.
        address = self._lp_get_current_wallet_address()
        if address and self.gui.online_mode:
            self._lp_do_fetch()


