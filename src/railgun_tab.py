"""
Railgun Tab — Privacy protocol integration for ColdStack.

Provides GUI for managing the Railgun sidecar, loading shielded wallets
from existing vault accounts, viewing private balances, and executing
shield/unshield/transfer operations.

Version: v5.2.3 (August 2026)
"""
import threading
import hashlib
from typing import Dict, List, Optional, Any
from pathlib import Path

import customtkinter as ctk

# Import the Python bridge client
from railgun_bridge import RailgunSidecar
from railgun_tx_dialogs import ShieldDialog, UnshieldDialog, TransferDialog

# Keep in sync with sidecar/src/networks.js WRAPPED_NATIVE (SDK 7.6.1 — 4 chains)
WRAPPED_NATIVE_BY_CHAIN = {
    "ethereum": ("WETH", "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"),
    "arbitrum": ("WETH", "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1"),
    "bsc":      ("WBNB", "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c"),
    "polygon":  ("WMATIC", "0x0d500B1d8E8eF31E21C99d1Db9A6444d3ADf1270"),
}


class RailgunTab:
    """Railgun privacy tab for ColdStack GUI.

    Manages the Railgun Node.js sidecar lifecycle and provides
    UI for shielded wallet operations using existing vault accounts.
    """

    def __init__(self, gui):
        self.gui = gui
        self.sidecar: Optional[RailgunSidecar] = None
        self._widgets: Dict[str, Any] = {}
        self._railgun_wallet_id: Optional[str] = None
        self._railgun_address: Optional[str] = None
        self._encryption_key: Optional[str] = None
        self._balance_labels: Dict[str, ctk.CTkLabel] = {}
        self._sidecar_running = False
        self._selected_account: Optional[str] = None

    def create_tab(self, parent):
        """Create the Railgun tab content inside the parent frame."""
        # Main scrollable container — in case the whole tab is tall
        container = ctk.CTkFrame(parent, corner_radius=0)
        container.pack(fill="both", expand=True, padx=10, pady=10)

        # ─── Section 1: Sidecar Status ───
        status_frame = ctk.CTkFrame(container, corner_radius=8)
        status_frame.pack(fill="x", pady=(0, 10))

        ctk.CTkLabel(
            status_frame,
            text="🔒 Railgun Privacy Engine",
            font=ctk.CTkFont(size=18, weight="bold")
        ).pack(pady=(10, 5))

        # Status row
        status_row = ctk.CTkFrame(status_frame, fg_color="transparent")
        status_row.pack(fill="x", padx=15, pady=(0, 10))

        self._widgets["sidecar_status"] = ctk.CTkLabel(
            status_row,
            text="Sidecar: Not running",
            font=ctk.CTkFont(size=13),
            text_color="#888888"
        )
        self._widgets["sidecar_status"].pack(side="left", padx=(0, 20))

        self._widgets["engine_status"] = ctk.CTkLabel(
            status_row,
            text="Engine: Not initialized",
            font=ctk.CTkFont(size=13),
            text_color="#888888"
        )
        self._widgets["engine_status"].pack(side="left", padx=(0, 20))

        self._widgets["wallet_status"] = ctk.CTkLabel(
            status_row,
            text="Wallet: Not loaded",
            font=ctk.CTkFont(size=13),
            text_color="#888888"
        )
        self._widgets["wallet_status"].pack(side="left")

        # ─── Provider status line (per-chain provider health) ───
        provider_row = ctk.CTkFrame(status_frame, fg_color="transparent")
        provider_row.pack(fill="x", padx=15, pady=(0, 10))

        self._widgets["provider_status"] = ctk.CTkLabel(
            provider_row,
            text="Providers: —",
            font=ctk.CTkFont(size=11),
            text_color="#888888"
        )
        self._widgets["provider_status"].pack(anchor="w")

        # Action buttons row
        button_row = ctk.CTkFrame(status_frame, fg_color="transparent")
        button_row.pack(fill="x", padx=15, pady=(0, 10))

        self._widgets["btn_start_sidecar"] = ctk.CTkButton(
            button_row,
            text="Start Sidecar",
            width=120,
            command=self._on_start_sidecar
        )
        self._widgets["btn_start_sidecar"].pack(side="left", padx=(0, 10))

        self._widgets["btn_stop_sidecar"] = ctk.CTkButton(
            button_row,
            text="Stop Sidecar",
            width=120,
            state="disabled",
            fg_color="#cc4444",
            hover_color="#aa3333",
            command=self._on_stop_sidecar
        )
        self._widgets["btn_stop_sidecar"].pack(side="left", padx=(0, 10))

        self._widgets["btn_init_engine"] = ctk.CTkButton(
            button_row,
            text="Init Engine",
            width=120,
            state="disabled",
            command=self._on_init_engine
        )
        self._widgets["btn_init_engine"].pack(side="left", padx=(0, 10))

        self._widgets["btn_refresh_status"] = ctk.CTkButton(
            button_row,
            text="Refresh Status",
            width=120,
            state="disabled",
            command=self._on_refresh_status
        )
        self._widgets["btn_refresh_status"].pack(side="left", padx=(0, 10))

        self._widgets["btn_reload_providers"] = ctk.CTkButton(
            button_row,
            text="Reload Providers",
            width=140,
            state="disabled",
            fg_color="#FF9800",
            hover_color="#F57C00",
            command=self._on_reload_providers,
        )
        self._widgets["btn_reload_providers"].pack(side="left")

        # ─── Section 2: Wallet Loading (from vault accounts) ───
        wallet_frame = ctk.CTkFrame(container, corner_radius=8)
        wallet_frame.pack(fill="x", pady=(0, 10))

        ctk.CTkLabel(
            wallet_frame,
            text="Shielded Wallet",
            font=ctk.CTkFont(size=16, weight="bold")
        ).pack(pady=(10, 5))

        # Account dropdown (selects from existing vault accounts with mnemonics)
        acct_row = ctk.CTkFrame(wallet_frame, fg_color="transparent")
        acct_row.pack(fill="x", padx=15, pady=(0, 5))
        ctk.CTkLabel(acct_row, text="Vault Account:", width=120, anchor="w").pack(side="left")
        self._widgets["account_dropdown"] = ctk.CTkOptionMenu(
            acct_row,
            values=["No accounts found"],
            command=self._on_account_selected,
            width=300
        )
        self._widgets["account_dropdown"].pack(side="left", fill="x", expand=True, padx=(5, 0))

        # Refresh account list button
        self._widgets["btn_refresh_accounts"] = ctk.CTkButton(
            wallet_frame,
            text="Refresh Account List",
            width=150,
            command=self._refresh_account_list
        )
        self._widgets["btn_refresh_accounts"].pack(padx=15, pady=(5, 5), anchor="w")

        # Railgun password entry (used to derive encryption key)
        pass_row = ctk.CTkFrame(wallet_frame, fg_color="transparent")
        pass_row.pack(fill="x", padx=15, pady=(0, 5))
        ctk.CTkLabel(pass_row, text="Railgun Password:", width=120, anchor="w").pack(side="left")
        self._widgets["password_entry"] = ctk.CTkEntry(
            pass_row, show="•", width=300, placeholder_text="Password to encrypt your shielded wallet"
        )
        self._widgets["password_entry"].pack(side="left", fill="x", expand=True, padx=(5, 0))

        # Load wallet button
        self._widgets["btn_load_wallet"] = ctk.CTkButton(
            wallet_frame,
            text="Load Shielded Wallet",
            width=180,
            state="disabled",
            command=self._on_load_wallet
        )
        self._widgets["btn_load_wallet"].pack(padx=15, pady=(5, 10), anchor="w")

        # Wallet info display
        self._widgets["wallet_info"] = ctk.CTkLabel(
            wallet_frame,
            text="",
            font=ctk.CTkFont(size=12),
            justify="left",
            anchor="w"
        )
        self._widgets["wallet_info"].pack(fill="x", padx=15, pady=(0, 10))

        # ─── Section 3: Balances (FIXED HEIGHT — doesn't eat the screen) ───
        balance_frame = ctk.CTkFrame(container, corner_radius=8)
        balance_frame.pack(fill="x", expand=False, pady=(0, 10))

        ctk.CTkLabel(
            balance_frame,
            text="Shielded Balances",
            font=ctk.CTkFont(size=16, weight="bold")
        ).pack(pady=(10, 5))

        # Refresh button
        self._widgets["btn_refresh_balances"] = ctk.CTkButton(
            balance_frame,
            text="Refresh Balances",
            width=150,
            state="disabled",
            command=self._on_refresh_balances
        )
        self._widgets["btn_refresh_balances"].pack(padx=15, pady=(0, 10), anchor="w")

        # Fixed-height scrollable balance list (200px max — scrolls if more)
        self._balance_scroll = ctk.CTkScrollableFrame(balance_frame, height=200)
        self._balance_scroll.pack(fill="x", expand=False, padx=15, pady=(0, 10))
        self._balance_scroll.pack_propagate(False)

        self._widgets["balance_placeholder"] = ctk.CTkLabel(
            self._balance_scroll,
            text="No balances loaded. Load a wallet and click Refresh.",
            font=ctk.CTkFont(size=13),
            text_color="#888888"
        )
        self._widgets["balance_placeholder"].pack(pady=20)

        # ─── Section 4: Transactions ───
        tx_frame = ctk.CTkFrame(container, corner_radius=8)
        tx_frame.pack(fill="x", pady=(0, 10))

        ctk.CTkLabel(
            tx_frame,
            text="Transactions",
            font=ctk.CTkFont(size=16, weight="bold")
        ).pack(pady=(10, 5))

        tx_row = ctk.CTkFrame(tx_frame, fg_color="transparent")
        tx_row.pack(fill="x", padx=15, pady=(0, 10))

        self._widgets["btn_shield"] = ctk.CTkButton(
            tx_row, text="Shield", width=100, state="disabled",
            command=self._on_shield
        )
        self._widgets["btn_shield"].pack(side="left", padx=(0, 10))

        self._widgets["btn_unshield"] = ctk.CTkButton(
            tx_row, text="Unshield", width=100, state="disabled",
            command=self._on_unshield
        )
        self._widgets["btn_unshield"].pack(side="left", padx=(0, 10))

        self._widgets["btn_transfer"] = ctk.CTkButton(
            tx_row, text="Private Transfer", width=140, state="disabled",
            command=self._on_transfer
        )
        self._widgets["btn_transfer"].pack(side="left")

    # ─── Account List ───

    def _get_accounts_with_mnemonics(self) -> List[str]:
        """Get list of 'Pool > Account' strings for accounts that have mnemonics in the vault."""
        if not self.gui.key_manager:
            return []

        address_db = self.gui.key_manager.address_db
        pools = address_db.get("pools", {})
        mnemonics = address_db.get("mnemonics", {})
        accounts_with_mnemonics = []

        for pool_name, pool_data in pools.items():
            for account_name in pool_data.get("accounts", []):
                if account_name in mnemonics:
                    accounts_with_mnemonics.append(f"{pool_name} > {account_name}")

        # Also check for accounts with mnemonics that aren't in any pool
        all_pool_accounts = set()
        for pool_data in pools.values():
            all_pool_accounts.update(pool_data.get("accounts", []))

        for account_name in mnemonics:
            if account_name not in all_pool_accounts:
                accounts_with_mnemonics.append(f"No Pool > {account_name}")

        return accounts_with_mnemonics

    def _refresh_account_list(self):
        """Refresh the account dropdown with current vault accounts."""
        accounts = self._get_accounts_with_mnemonics()
        if accounts:
            self._widgets["account_dropdown"].configure(values=accounts)
            self._widgets["account_dropdown"].set("Select an account...")
        else:
            self._widgets["account_dropdown"].configure(values=["No accounts with mnemonics found"])
            self._widgets["account_dropdown"].set("No accounts with mnemonics found")
        self.gui.show_notification(f"Found {len(accounts)} accounts with mnemonics")

    def _on_account_selected(self, selection: str):
        """Called when user selects an account from the dropdown."""
        self._selected_account = selection
        # Enable load button if we have a valid selection
        if " > " in selection:
            self._widgets["btn_load_wallet"].configure(state="normal")
        else:
            self._widgets["btn_load_wallet"].configure(state="disabled")

    @staticmethod
    def _parse_account_selection(selection: str) -> str:
        """Extract account name from 'Pool > Account' format."""
        if " > " in selection:
            parts = selection.split(" > ", 1)
            return parts[1]
        return selection

    @staticmethod
    def _derive_encryption_key(password: str) -> str:
        """Derive a 32-byte hex encryption key from a password string."""
        return hashlib.sha256(password.encode('utf-8')).hexdigest()

    # ─── Sidecar Lifecycle ───

    def _on_start_sidecar(self):
        """Start the Railgun sidecar process."""
        try:
            self.sidecar = RailgunSidecar()
            self.gui.show_notification("Starting Railgun sidecar...")

            def _start():
                try:
                    ok = self.sidecar.start()
                    if ok:
                        self._sidecar_running = True
                        self.gui.root.after(0, lambda: self._update_sidecar_ui(running=True))
                        self.gui.root.after(0, lambda: self._refresh_account_list())
                        self.gui.root.after(0, lambda: self.gui.show_notification("Railgun sidecar started"))
                    else:
                        self.gui.root.after(0, lambda: self.gui.show_notification("Sidecar failed to start", error=True))
                except Exception as e:
                    msg = f"Sidecar error: {e}"
                    self.gui.root.after(0, lambda: self.gui.show_notification(msg, error=True))

            threading.Thread(target=_start, daemon=True).start()
        except Exception as e:
            self.gui.show_notification(f"Failed to start sidecar: {e}", error=True)

    def _on_stop_sidecar(self):
        """Stop the Railgun sidecar process."""
        if self.sidecar:
            try:
                self.sidecar.stop()
                self._sidecar_running = False
                self._railgun_wallet_id = None
                self._railgun_address = None
                self._update_sidecar_ui(running=False)
                self.gui.show_notification("Railgun sidecar stopped")
            except Exception as e:
                self.gui.show_notification(f"Stop error: {e}", error=True)

    def _update_sidecar_ui(self, running):
        """Update UI elements based on sidecar running state."""
        if running:
            self._widgets["sidecar_status"].configure(
                text="Sidecar: Running", text_color="#4caf50"
            )
            self._widgets["btn_start_sidecar"].configure(state="disabled")
            self._widgets["btn_stop_sidecar"].configure(state="normal")
            self._widgets["btn_init_engine"].configure(state="normal")
            self._widgets["btn_refresh_status"].configure(state="normal")
            self._widgets["btn_reload_providers"].configure(state="normal")
        else:
            self._widgets["sidecar_status"].configure(
                text="Sidecar: Not running", text_color="#888888"
            )
            self._widgets["engine_status"].configure(
                text="Engine: Not initialized", text_color="#888888"
            )
            self._widgets["wallet_status"].configure(
                text="Wallet: Not loaded", text_color="#888888"
            )
            self._widgets["provider_status"].configure(text="Providers: —")
            self._widgets["btn_start_sidecar"].configure(state="normal")
            self._widgets["btn_stop_sidecar"].configure(state="disabled")
            self._widgets["btn_init_engine"].configure(state="disabled")
            self._widgets["btn_refresh_status"].configure(state="disabled")
            self._widgets["btn_reload_providers"].configure(state="disabled")
            self._widgets["btn_load_wallet"].configure(state="disabled")
            self._widgets["btn_refresh_balances"].configure(state="disabled")
            self._widgets["btn_shield"].configure(state="disabled")
            self._widgets["btn_unshield"].configure(state="disabled")
            self._widgets["btn_transfer"].configure(state="disabled")

    # ─── Engine Init ───

    def _on_init_engine(self):
        """Initialize the Railgun engine with ColdStack's RPC config."""
        if not self.sidecar or not self._sidecar_running:
            self.gui.show_notification("Sidecar not running", error=True)
            return

        rpc_config = self.gui.rpc_config
        if not rpc_config:
            self.gui.show_notification("No RPC config loaded. Go Online first.", error=True)
            return

        def _init():
            try:
                result = self.sidecar.init_engine(rpc_config)
                self.gui.root.after(0, lambda: self._on_engine_init_done(result))
            except Exception as e:
                msg = f"Engine init failed: {e}"
                self.gui.root.after(0, lambda: self.gui.show_notification(msg, error=True))

        self.gui.show_notification("Initializing Railgun engine (may take a moment)...")
        threading.Thread(target=_init, daemon=True).start()

    def _on_engine_init_done(self, result):
        """Called when engine init completes."""
        if result.get("status") == "ok" or result.get("status") == "already_initialized":
            self._widgets["engine_status"].configure(
                text="Engine: Initialized", text_color="#4caf50"
            )
            self._widgets["btn_load_wallet"].configure(state="normal")
            self._widgets["btn_shield"].configure(state="normal")
            self._widgets["btn_unshield"].configure(state="normal")
            self._widgets["btn_transfer"].configure(state="normal")

            providers = result.get("providers", {})
            loaded = [k for k, v in providers.items() if v.get("status") == "ok"]
            self.gui.show_notification(f"Engine initialized. Providers: {', '.join(loaded)}")
        else:
            self.gui.show_notification(
                f"Engine init error: {result.get('error', 'unknown')}", error=True
            )

    # ─── Wallet Loading ───

    def _on_load_wallet(self):
        """Load a Railgun wallet from a vault account + Railgun password."""
        if not self.sidecar or not self._sidecar_running:
            self.gui.show_notification("Sidecar not running", error=True)
            return

        if not self._selected_account or " > " not in self._selected_account:
            self.gui.show_notification("Select a vault account first", error=True)
            return

        password = self._widgets["password_entry"].get().strip()
        if not password:
            self.gui.show_notification("Enter a Railgun password", error=True)
            return

        # Get the account name from the dropdown selection
        account_name = self._parse_account_selection(self._selected_account)

        # Get the mnemonic from the vault
        if not self.gui.key_manager:
            self.gui.show_notification("Vault not unlocked", error=True)
            return
        mnemonic = self.gui.key_manager.show_mnemonic(account_name)
        if not mnemonic:
            self.gui.show_notification(f"No mnemonic found for account '{account_name}'", error=True)
            return

        # Derive the encryption key from the password
        encryption_key = self._derive_encryption_key(password)

        def _load():
            try:
                result = self.sidecar.load_wallet(mnemonic, encryption_key)
                self._railgun_wallet_id = result.get("walletId")
                self._railgun_address = result.get("railgunAddress")
                self._encryption_key = encryption_key
                self.gui.root.after(0, lambda: self._on_wallet_loaded(result))
            except Exception as e:
                msg = f"Wallet load failed: {e}"
                self.gui.root.after(0, lambda: self.gui.show_notification(msg, error=True))

        self.gui.show_notification(f"Loading shielded wallet from '{account_name}'...")
        threading.Thread(target=_load, daemon=True).start()

    def _on_wallet_loaded(self, result):
        """Called when wallet loading completes."""
        if result.get("status") == "ok":
            addr_short = f"{self._railgun_address[:12]}...{self._railgun_address[-8:]}" if self._railgun_address else "unknown"
            self._widgets["wallet_status"].configure(
                text=f"Wallet: {addr_short}",
                text_color="#4caf50"
            )
            self._widgets["wallet_info"].configure(
                text=f"Wallet ID: {self._railgun_wallet_id}\n"
                     f"0zk Address: {self._railgun_address}\n"
                     f"Account: {self._selected_account}\n"
                     f"Created: {result.get('created', 'existing')}"
            )
            self._widgets["btn_refresh_balances"].configure(state="normal")
            self.gui.show_notification("Shielded wallet loaded")
        else:
            self.gui.show_notification(
                f"Wallet load error: {result.get('error', 'unknown')}", error=True
            )

    # ─── Balance Refresh ───

    def _on_refresh_balances(self):
        """Refresh shielded balances for all loaded Railgun networks."""
        if not self.sidecar or not self._sidecar_running:
            self.gui.show_notification("Sidecar not running", error=True)
            return
        if not self._railgun_wallet_id:
            self.gui.show_notification("No wallet loaded", error=True)
            return

        chains = ["ethereum", "arbitrum", "bsc", "polygon"]

        def _refresh():
            for chain in chains:
                try:
                    self.sidecar.refresh_balances(chain, [self._railgun_wallet_id])
                except Exception as e:
                    print(f"[railgun] Balance refresh for {chain}: {e}")

            import time
            time.sleep(2)

            try:
                status = self.sidecar.get_balance_status()
                self.gui.root.after(0, lambda: self._render_balances(status))
            except Exception as e:
                msg = f"Balance status failed: {e}"
                self.gui.root.after(0, lambda: self.gui.show_notification(msg, error=True))

        self.gui.show_notification("Scanning shielded balances...")
        threading.Thread(target=_refresh, daemon=True).start()

    def _render_balances(self, status):
        """Render balance data in the fixed-height scrollable frame."""
        for widget in self._balance_scroll.winfo_children():
            widget.destroy()

        buckets = status.get("buckets", {})
        wallet_buckets = buckets.get(self._railgun_wallet_id, {})

        if not wallet_buckets:
            self._widgets["balance_placeholder"] = ctk.CTkLabel(
                self._balance_scroll,
                text="No shielded balances found. Try refreshing again after scan completes.",
                font=ctk.CTkFont(size=13),
                text_color="#888888"
            )
            self._widgets["balance_placeholder"].pack(pady=20)
            self.gui.show_notification("No shielded balances found")
            return

        for chain, chain_buckets in wallet_buckets.items():
            chain_frame = ctk.CTkFrame(self._balance_scroll, corner_radius=6)
            chain_frame.pack(fill="x", pady=5)

            ctk.CTkLabel(
                chain_frame,
                text=f"━━━ {chain.upper()} ━━━",
                font=ctk.CTkFont(size=14, weight="bold"),
                anchor="w"
            ).pack(fill="x", padx=10, pady=(5, 2))

            for bucket_name, bucket_data in chain_buckets.items():
                erc20_amounts = bucket_data.get("erc20Amounts", [])
                if not erc20_amounts:
                    continue

                color = "#4caf50" if bucket_name == "Spendable" else "#888888"
                bucket_text = f"  {bucket_name}:"
                ctk.CTkLabel(
                    chain_frame,
                    text=bucket_text,
                    font=ctk.CTkFont(size=12, weight="bold" if bucket_name == "Spendable" else "normal"),
                    text_color=color,
                    anchor="w"
                ).pack(fill="x", padx=10)

                for erc20 in erc20_amounts:
                    token_addr = erc20.get("tokenAddress", "")
                    amount = erc20.get("amount", "0")
                    # Label known wrapped-native tokens with their symbol
                    token_label = token_addr
                    for ch, (sym, wrapped_addr) in WRAPPED_NATIVE_BY_CHAIN.items():
                        if token_addr.lower() == wrapped_addr.lower():
                            token_label = f"{sym} · {token_addr[:6]}…{token_addr[-4:]}"
                            break
                    else:
                        token_label = f"{token_addr[:10]}..."
                    ctk.CTkLabel(
                        chain_frame,
                        text=f"    {token_label}: {amount}",
                        font=ctk.CTkFont(size=11),
                        text_color=color,
                        anchor="w"
                    ).pack(fill="x", padx=15)

        self.gui.show_notification("Shielded balances updated")

    # ─── Status Refresh ───

    def _on_refresh_status(self):
        """Refresh the sidecar status display."""
        if not self.sidecar or not self._sidecar_running:
            return

        def _check():
            try:
                healthy = self.sidecar.is_healthy()
                if healthy:
                    status = self.sidecar.get_engine_status()
                    self.gui.root.after(0, lambda: self._update_status_display(status))
                else:
                    self.gui.root.after(0, lambda: self._update_sidecar_ui(running=False))
            except Exception:
                self.gui.root.after(0, lambda: self._update_sidecar_ui(running=False))

        threading.Thread(target=_check, daemon=True).start()

    def _update_status_display(self, status):
        """Update the status labels from engine status response."""
        if status.get("engineInitialized"):
            self._widgets["engine_status"].configure(
                text="Engine: Initialized", text_color="#4caf50"
            )
        else:
            self._widgets["engine_status"].configure(
                text="Engine: Not initialized", text_color="#888888"
            )

        wallets = status.get("walletsLoaded", 0)
        if wallets > 0:
            self._widgets["wallet_status"].configure(
                text=f"Wallet: {wallets} loaded", text_color="#4caf50"
            )
        else:
            self._widgets["wallet_status"].configure(
                text="Wallet: Not loaded", text_color="#888888"
            )

        # Per-chain provider status line
        providers = status.get("providers", {})
        if providers:
            parts = []
            for chain, info in sorted(providers.items()):
                if info.get("error"):
                    parts.append(f"{chain} ✗ {info['error'][:40]}")
                else:
                    parts.append(f"{chain} ✓")
            self._widgets["provider_status"].configure(
                text="Providers: " + " · ".join(parts)
            )
        else:
            self._widgets["provider_status"].configure(text="Providers: —")

    # ─── Provider Retry ───

    def _on_reload_providers(self):
        """Retry failed (or all) provider connections without engine restart."""
        if not self.sidecar or not self._sidecar_running:
            self.gui.show_notification("Sidecar not running", error=True)
            return

        # Determine which chains need reloading
        try:
            status = self.sidecar.get_engine_status()
            providers = status.get("providers", {})
            failed = [chain for chain, info in providers.items() if info.get("error")]
        except Exception:
            failed = []

        # If nothing marked failed, reload all 4
        if not failed:
            chains = ["ethereum", "arbitrum", "bsc", "polygon"]
            self.gui.show_notification(f"No failed providers — reloading all 4 chains...")
        else:
            chains = failed
            self.gui.show_notification(f"Retrying failed providers: {', '.join(chains)}")

        self._widgets["provider_status"].configure(text="Providers: reloading…")
        self._widgets["btn_reload_providers"].configure(state="disabled")

        def _retry():
            results = []
            for chain in chains:
                try:
                    result = self.sidecar.reload_provider(chain)
                    if result.get("status") == "ok":
                        results.append(f"{chain} ✓")
                    else:
                        results.append(f"{chain} ✗ {result.get('error', '?')[:40]}")
                except Exception as e:
                    results.append(f"{chain} ✗ {str(e)[:40]}")
            summary = " · ".join(results)
            self.gui.root.after(0, lambda s=summary: self._widgets["provider_status"].configure(
                text=f"Providers: {s}"))
            self.gui.root.after(0, lambda: self._widgets["btn_reload_providers"].configure(state="normal"))
            self.gui.root.after(0, self._on_refresh_status)

        threading.Thread(target=_retry, daemon=True).start()

    # ─── Transaction Dialogs ───

    def _on_shield(self):
        """Open shield dialog — public → private."""
        if not self._validate_tx_preconditions():
            return
        account_name = self._parse_account_selection(self._selected_account)
        dialog = ShieldDialog(
            gui=self.gui,
            sidecar=self.sidecar,
            wallet_id=self._railgun_wallet_id,
            railgun_address=self._railgun_address,
            encryption_key=self._encryption_key,
            account_name=account_name,
            on_success=lambda r: self._on_refresh_balances(),
        )
        dialog.build_ui()

    def _on_unshield(self):
        """Open unshield dialog — private → public."""
        if not self._validate_tx_preconditions():
            return
        account_name = self._parse_account_selection(self._selected_account)
        dialog = UnshieldDialog(
            gui=self.gui,
            sidecar=self.sidecar,
            wallet_id=self._railgun_wallet_id,
            railgun_address=self._railgun_address,
            encryption_key=self._encryption_key,
            account_name=account_name,
            on_success=lambda r: self._on_refresh_balances(),
        )
        dialog.build_ui()

    def _on_transfer(self):
        """Open private transfer dialog — 0zk → 0zk."""
        if not self._validate_tx_preconditions():
            return
        account_name = self._parse_account_selection(self._selected_account)
        dialog = TransferDialog(
            gui=self.gui,
            sidecar=self.sidecar,
            wallet_id=self._railgun_wallet_id,
            railgun_address=self._railgun_address,
            encryption_key=self._encryption_key,
            account_name=account_name,
            on_success=lambda r: self._on_refresh_balances(),
        )
        dialog.build_ui()

    def _validate_tx_preconditions(self) -> bool:
        """Validate that sidecar/wallet/account are ready for a transaction."""
        if not self.sidecar or not self._sidecar_running:
            self.gui.show_notification("Sidecar not running — start it first", error=True)
            return False
        if not self._railgun_wallet_id:
            self.gui.show_notification("No shielded wallet loaded — load a wallet first", error=True)
            return False
        if not self._selected_account or " > " not in self._selected_account:
            self.gui.show_notification("No vault account selected", error=True)
            return False
        return True

    # ─── Cleanup ───

    def on_close(self):
        """Called when the GUI is closing — stop the sidecar."""
        if self.sidecar:
            try:
                self.sidecar.stop()
            except Exception:
                pass
