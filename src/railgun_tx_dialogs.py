"""Railgun transaction dialogs for ColdStack.

Three modal dialogs (Shield / Unshield / Private Transfer) sharing a common base.
They follow the house pattern: CTkToplevel modals, grab_set, centered, threaded
submit, copyable results, and no logging of sensitive material.
"""
import decimal
import threading
from typing import Any, Callable, Dict, List, Optional

import customtkinter as ctk


# Railgun supported chains (SDK 7.6.1 — 4 chains; Base/Optimism on SDK upgrade >10.4.0/7.6.1)
RAILGUN_CHAINS = ["ethereum", "arbitrum", "bsc", "polygon"]


def _truncate_addr(addr: str, prefix: int = 10, suffix: int = 8) -> str:
    """Middle-truncate an address for display."""
    if not addr or len(addr) <= prefix + suffix + 3:
        return addr or ""
    return f"{addr[:prefix]}...{addr[-suffix:]}"


def _decode_amount(amount_str: str, decimals: Optional[int]) -> str:
    """Convert human-readable amount to base-units string.

    Args:
        amount_str: Human-readable amount (e.g. "1.23")
        decimals: Token decimals. If None, returns the amount as-is (base units mode).

    Returns:
        Base-units integer as a string.
    """
    if decimals is None:
        # Raw base-units mode — return as-is (assume user entered base units)
        return amount_str.strip()
    d = decimal.Decimal(amount_str.strip())
    multiplier = decimal.Decimal(10) ** decimals
    base = int(d * multiplier)
    return str(base)


class _BaseTxDialog:
    """Base class for Railgun transaction dialogs (shared logic)."""

    DIALOG_TITLE = "Railgun Transaction"
    DIALOG_WIDTH = 560
    DIALOG_HEIGHT = 480

    def __init__(self, gui, sidecar, wallet_id: str, railgun_address: str,
                 encryption_key: Optional[str], account_name: str,
                 on_success: Optional[Callable] = None):
        self.gui = gui
        self.sidecar = sidecar
        self.wallet_id = wallet_id
        self.railgun_address = railgun_address
        self.encryption_key = encryption_key
        self.account_name = account_name
        self.on_success = on_success

        # Token info state
        self.token_symbol: Optional[str] = None
        self.token_decimals: Optional[int] = None
        self._token_info_fetched = False

        # Dialog window
        self.dialog = ctk.CTkToplevel(gui.root)
        self.dialog.title(self.DIALOG_TITLE)
        self.dialog.geometry(f"{self.DIALOG_WIDTH}x{self.DIALOG_HEIGHT}")
        self.dialog.transient(gui.root)
        self.dialog.grab_set()
        gui._center_dialog(self.dialog)

        # Widgets (populated in build_ui)
        self._widgets: Dict[str, Any] = {}

    def build_ui(self) -> None:
        """Build the dialog UI. Subclasses call super() then add their own fields."""
        ctk.CTkLabel(
            self.dialog,
            text=self.DIALOG_TITLE,
            font=ctk.CTkFont(size=16, weight="bold")
        ).pack(pady=(15, 10))

        form = ctk.CTkFrame(self.dialog, fg_color="transparent")
        form.pack(fill="both", expand=True, padx=20, pady=(0, 10))

        # ─── Chain ───
        ctk.CTkLabel(form, text="Chain:", anchor="w").pack(anchor="w", pady=(0, 2))
        self._widgets["chain_var"] = ctk.StringVar(value=RAILGUN_CHAINS[0])
        chain_menu = ctk.CTkOptionMenu(
            form,
            variable=self._widgets["chain_var"],
            values=RAILGUN_CHAINS,
            width=300,
        )
        chain_menu.pack(fill="x", pady=(0, 8))

        # ─── Token Address ───
        token_row = ctk.CTkFrame(form, fg_color="transparent")
        token_row.pack(fill="x", pady=(0, 2))
        ctk.CTkLabel(token_row, text="Token Address:", anchor="w").pack(side="left")
        self._widgets["token_entry"] = ctk.CTkEntry(
            form,
            placeholder_text="0x... or paste from scan results",
            width=420,
            font=ctk.CTkFont(size=11, family="monospace"),
        )
        self._widgets["token_entry"].pack(fill="x", pady=(0, 2))
        self._widgets["token_entry"].bind("<FocusOut>", self._on_token_focus_out)
        self._widgets["token_entry"].bind("<Return>", self._on_token_focus_out)

        self._widgets["token_info_label"] = ctk.CTkLabel(
            form, text="", font=ctk.CTkFont(size=10), text_color="gray60"
        )
        self._widgets["token_info_label"].pack(anchor="w", pady=(0, 8))

        # ─── Amount ───
        ctk.CTkLabel(form, text="Amount:", anchor="w").pack(anchor="w", pady=(0, 2))
        self._widgets["amount_entry"] = ctk.CTkEntry(
            form,
            placeholder_text="0.0 (human units)",
            width=200,
            font=ctk.CTkFont(size=12),
        )
        self._widgets["amount_entry"].pack(anchor="w", pady=(0, 2))

        self._widgets["amount_base_label"] = ctk.CTkLabel(
            form, text="", font=ctk.CTkFont(size=10), text_color="gray50"
        )
        self._widgets["amount_base_label"].pack(anchor="w", pady=(0, 8))

        # Separator — subclasses add their fields above the button row

        self._widgets["status_label"] = ctk.CTkLabel(
            form, text="", font=ctk.CTkFont(size=11)
        )
        self._widgets["status_label"].pack(anchor="w", pady=(5, 5))

        btn_frame = ctk.CTkFrame(self.dialog, fg_color="transparent")
        btn_frame.pack(pady=(5, 15))

        self._widgets["confirm_btn"] = ctk.CTkButton(
            btn_frame,
            text="Confirm",
            command=self._on_confirm,
            width=140,
            font=ctk.CTkFont(size=12, weight="bold"),
        )
        self._widgets["confirm_btn"].pack(side="left", padx=10)

        ctk.CTkButton(
            btn_frame,
            text="Cancel",
            command=self.dialog.destroy,
            width=100,
            fg_color="gray30",
        ).pack(side="left", padx=10)

    # ─── Token info ───

    def _on_token_focus_out(self, _event=None):
        """Fetch token symbol/decimals when the token address loses focus."""
        token_addr = self._widgets["token_entry"].get().strip()
        chain = self._widgets["chain_var"].get()
        if not token_addr or not chain:
            return
        # Allow "ETH" (native) or 0x-prefixed addresses
        if not (token_addr.upper() == "ETH" or token_addr.startswith("0x")):
            return

        def _fetch():
            try:
                result = self.sidecar.get_token_info(chain, token_addr)
                self.gui.root.after(0, lambda r=result: self._on_token_info(r))
            except Exception:
                self.gui.root.after(0, lambda: self._on_token_info({"status": "error"}))

        threading.Thread(target=_fetch, daemon=True).start()

    def _on_token_info(self, result: Dict[str, Any]):
        """Handle token info response."""
        if result.get("status") == "ok":
            self.token_symbol = result.get("symbol")
            self.token_decimals = result.get("decimals")
            is_native = result.get("native", False)
            parts = []
            if is_native:
                parts.append("ETH \u2192 wraps to")
            if self.token_symbol:
                parts.append(f"{self.token_symbol} before shielding")
            if self.token_decimals is not None:
                parts.append(f"({self.token_decimals} decimals)")
            self._widgets["token_info_label"].configure(
                text=" ".join(parts) if parts else "Token info not available",
                text_color=("#2196F3", "#1565C0"),
            )
            self._token_info_fetched = True
            # Update amount hint
            if self.token_decimals is not None:
                self._widgets["amount_entry"].configure(
                    placeholder_text=f"0.0 (ETH units)"
                )
        else:
            self.token_symbol = None
            self.token_decimals = None
            self._widgets["token_info_label"].configure(
                text="Token info unavailable — using raw base units",
                text_color="orange",
            )

    # ─── Amount conversion ───

    def _get_base_amount(self) -> Optional[str]:
        """Convert the amount entry to base units. Returns None on invalid input."""
        amount_str = self._widgets["amount_entry"].get().strip()
        if not amount_str:
            self._widgets["status_label"].configure(text="Amount is required", text_color="red")
            return None
        try:
            base = _decode_amount(amount_str, self.token_decimals)
            self._widgets["amount_base_label"].configure(
                text=f"= {base} base units" if self.token_decimals is not None else f"= {base} base units (raw)",
                text_color="gray50",
            )
            return base
        except (decimal.InvalidOperation, ValueError, decimal.Overflow):
            self._widgets["status_label"].configure(text="Invalid amount format", text_color="red")
            return None

    # ─── Validation ───

    def _validate_common(self) -> bool:
        """Validate preconditions common to all dialogs. Returns True if OK to proceed."""
        if not self.sidecar:
            self._widgets["status_label"].configure(text="Sidecar not connected", text_color="red")
            return False
        if not self.wallet_id:
            self._widgets["status_label"].configure(text="No shielded wallet loaded", text_color="red")
            return False
        chain = self._widgets["chain_var"].get()
        if chain not in RAILGUN_CHAINS:
            self._widgets["status_label"].configure(text=f"Unsupported chain: {chain}", text_color="red")
            return False
        token = self._widgets["token_entry"].get().strip()
        if not token or not token.startswith("0x"):
            self._widgets["status_label"].configure(text="Token address is required (0x...)", text_color="red")
            return False
        if not self.account_name:
            self._widgets["status_label"].configure(text="No account selected", text_color="red")
            return False
        return True

    # ─── Confirmation ───

    def _confirm_summary(self) -> Optional[Dict[str, str]]:
        """Collect and return the transaction parameters. Returns None if validation fails."""
        if not self._validate_common():
            return None
        base_amount = self._get_base_amount()
        if base_amount is None:
            return None
        return {
            "chain": self._widgets["chain_var"].get(),
            "token_address": self._widgets["token_entry"].get().strip(),
            "token_symbol": self.token_symbol or "UNKNOWN",
            "amount_human": self._widgets["amount_entry"].get().strip(),
            "amount_base": base_amount,
            "wallet_id": self.wallet_id,
            "account_name": self.account_name,
        }

    def _on_confirm(self):
        """Handle Confirm button. Subclasses override this."""
        raise NotImplementedError

    def _submit(self, params: Dict[str, str]):
        """Submit the transaction in a background thread. Subclasses override the sidecar call."""
        raise NotImplementedError

    def _show_result(self, result: Dict[str, Any]):
        """Show the transaction result. Subclasses override for custom messages."""
        if result.get("status") == "ok":
            tx_hash = result.get("txHash", "")
            self._widgets["status_label"].configure(
                text=f"Success! TX: {_truncate_addr(tx_hash)}",
                text_color="green",
            )
            if self.on_success:
                self.on_success(result)
        else:
            error_msg = result.get("error", "Unknown error")
            self._widgets["status_label"].configure(
                text=f"Error: {error_msg[:80]}",
                text_color="red",
            )
            self.gui.show_notification(error_msg, error=True)


class ShieldDialog(_BaseTxDialog):
    """Shield (public → private) dialog."""

    DIALOG_TITLE = "Shield (Public → Private)"
    DIALOG_HEIGHT = 520

    def build_ui(self) -> None:
        """Build the shield-specific UI."""
        super().build_ui()

        # Signing wallet info
        info_frame = ctk.CTkFrame(self.dialog, fg_color="transparent")
        info_frame.pack(fill="x", padx=20, pady=(0, 5))
        self._widgets["signing_info"] = ctk.CTkLabel(
            info_frame,
            text=f"Signing wallet: {self.account_name} (pays gas + holds tokens)",
            font=ctk.CTkFont(size=11),
            text_color="gray60",
        )
        self._widgets["signing_info"].pack(anchor="w")

        # ─── Destination (0zk address, prefilled with own address) ───
        ctk.CTkLabel(self.dialog, text="Destination (0zk):", anchor="w").pack(anchor="w", padx=20, pady=(0, 2))
        self._widgets["dest_entry"] = ctk.CTkEntry(
            self.dialog,
            placeholder_text="0zk...",
            width=500,
            font=ctk.CTkFont(size=11, family="monospace"),
        )
        self._widgets["dest_entry"].pack(fill="x", padx=20, pady=(0, 8))
        if self.railgun_address:
            self._widgets["dest_entry"].insert(0, self.railgun_address)

        # Move the button frame to the bottom (parent was dialog)
        btn_frame = self._widgets["confirm_btn"].master
        btn_frame.pack_forget()

        # Rebuild button frame below the destination field
        btn_frame = ctk.CTkFrame(self.dialog, fg_color="transparent")
        btn_frame.pack(pady=(5, 15))
        self._widgets["confirm_btn"] = ctk.CTkButton(
            btn_frame, text="Shield", command=self._on_confirm,
            width=140, font=ctk.CTkFont(size=12, weight="bold"),
            fg_color=("#2196F3", "#1565C0"),
        )
        self._widgets["confirm_btn"].pack(side="left", padx=10)
        ctk.CTkButton(
            btn_frame, text="Cancel", command=self.dialog.destroy,
            width=100, fg_color="gray30",
        ).pack(side="left", padx=10)

    def _validate_common(self) -> bool:
        """Override to allow "ETH" (native token) as a valid token value."""
        # Validate everything except the 0x prefix check
        if not self.sidecar:
            self._widgets["status_label"].configure(text="Sidecar not connected", text_color="red")
            return False
        if not self.wallet_id:
            self._widgets["status_label"].configure(text="No shielded wallet loaded", text_color="red")
            return False
        chain = self._widgets["chain_var"].get()
        if chain not in RAILGUN_CHAINS:
            self._widgets["status_label"].configure(text=f"Unsupported chain: {chain}", text_color="red")
            return False
        token = self._widgets["token_entry"].get().strip()
        if not token:
            self._widgets["status_label"].configure(text="Token address is required", text_color="red")
            return False
        # Allow "ETH" (native) or standard 0x ERC-20 addresses
        if token.upper() != "ETH" and not token.startswith("0x"):
            self._widgets["status_label"].configure(
                text="Token address must be an ERC-20 address (0x...) or native ETH", text_color="red")
            return False
        if not self.account_name:
            self._widgets["status_label"].configure(text="No account selected", text_color="red")
            return False
        return True

    def _on_confirm(self):
        """Collect params and confirm."""
        params = self._confirm_summary()
        if not params:
            return
        params["to_address"] = self._widgets["dest_entry"].get().strip()
        if not params["to_address"] or not params["to_address"].startswith("0zk"):
            self._widgets["status_label"].configure(text="Destination 0zk address is required", text_color="red")
            return
        self._submit(params)

    def _submit(self, params: Dict[str, str]):
        """Submit shield transaction."""
        self._widgets["confirm_btn"].configure(state="disabled", text="Shielding...")
        self._widgets["status_label"].configure(text="Generating proof… this can take 20-30s", text_color="gray60")

        # Fetch mnemonic from vault
        if not self.gui.key_manager:
            self._widgets["status_label"].configure(text="Vault not unlocked", text_color="red")
            self._widgets["confirm_btn"].configure(state="normal", text="Shield")
            return
        mnemonic = self.gui.key_manager.show_mnemonic(self.account_name)
        if not mnemonic:
            self._widgets["status_label"].configure(text="No mnemonic for account", text_color="red")
            self._widgets["confirm_btn"].configure(state="normal", text="Shield")
            return

        def _run():
            try:
                result = self.sidecar.shield(
                    chain=params["chain"],
                    from_wallet_id=params["wallet_id"],
                    to_address=params["to_address"],
                    token_address=params["token_address"],
                    amount=params["amount_base"],
                    signing_mnemonic=mnemonic,
                )
                self.gui.root.after(0, lambda r=result: self._show_result(r))
            except Exception as e:
                self.gui.root.after(0, lambda err=str(e): self._show_result({"status": "error", "error": err}))

        threading.Thread(target=_run, daemon=True).start()


class UnshieldDialog(_BaseTxDialog):
    """Unshield (private → public) dialog."""

    DIALOG_TITLE = "Unshield (Private → Public)"
    DIALOG_HEIGHT = 520

    def build_ui(self) -> None:
        """Build the unshield-specific UI."""
        super().build_ui()

        # Signing wallet info — get public address from vault
        info_frame = ctk.CTkFrame(self.dialog, fg_color="transparent")
        info_frame.pack(fill="x", padx=20, pady=(0, 5))
        pub_addr = self._get_public_address()
        self._widgets["signing_info"] = ctk.CTkLabel(
            info_frame,
            text=f"Signing wallet: {self.account_name} ({_truncate_addr(pub_addr)}) — pays gas, receives tokens",
            font=ctk.CTkFont(size=11),
            text_color="gray60",
        )
        self._widgets["signing_info"].pack(anchor="w")

        # ─── Destination (public address, prefilled) ───
        ctk.CTkLabel(self.dialog, text="Destination (public):", anchor="w").pack(anchor="w", padx=20, pady=(0, 2))
        self._widgets["dest_entry"] = ctk.CTkEntry(
            self.dialog,
            placeholder_text="0x...",
            width=500,
            font=ctk.CTkFont(size=11, family="monospace"),
        )
        self._widgets["dest_entry"].pack(fill="x", padx=20, pady=(0, 8))
        if pub_addr:
            self._widgets["dest_entry"].insert(0, pub_addr)

        # Rebuild button frame
        btn_frame = ctk.CTkFrame(self.dialog, fg_color="transparent")
        btn_frame.pack(pady=(5, 15))
        self._widgets["confirm_btn"] = ctk.CTkButton(
            btn_frame, text="Unshield", command=self._on_confirm,
            width=140, font=ctk.CTkFont(size=12, weight="bold"),
            fg_color=("#FF9800", "#E65100"),
        )
        self._widgets["confirm_btn"].pack(side="left", padx=10)
        ctk.CTkButton(
            btn_frame, text="Cancel", command=self.dialog.destroy,
            width=100, fg_color="gray30",
        ).pack(side="left", padx=10)

    def _get_public_address(self) -> str:
        """Get the public EVM address for the selected account from the vault."""
        if not self.gui.key_manager:
            return ""
        accounts = self.gui.key_manager.address_db.get("accounts", {})
        acct = accounts.get(self.account_name, {})
        addrs = acct.get("addresses", [])
        for a in addrs:
            chain = (a.get("chain", "") or "").upper()
            coin = (a.get("coin", "") or "").upper()
            if "EVM" in chain or "ETHEREUM" in chain or "ARBITRUM" in chain or "BASE" in chain:
                return a.get("address", "")
        # Fallback: first 0x address
        for a in addrs:
            addr = a.get("address", "")
            if addr.startswith("0x") and len(addr) == 42:
                return addr
        return ""

    def _on_confirm(self):
        """Collect params and confirm."""
        params = self._confirm_summary()
        if not params:
            return
        params["to_address"] = self._widgets["dest_entry"].get().strip()
        if not params["to_address"] or not params["to_address"].startswith("0x"):
            self._widgets["status_label"].configure(text="Destination public address is required (0x...)", text_color="red")
            return
        if not self.encryption_key:
            self._widgets["status_label"].configure(text="No encryption key — reload wallet", text_color="red")
            return
        # Native ETH: override token_address to "ETH" when user wants auto-unwrap
        if self._is_native_eth_token() and self._widgets["unwrap_native_var"].get():
            params["token_address"] = "ETH"
        self._submit(params)

    def _submit(self, params: Dict[str, str]):
        """Submit unshield transaction."""
        is_native = params.get("token_address") == "ETH"
        self._widgets["confirm_btn"].configure(state="disabled", text="Unshielding...")
        status_text = "Generating proof… this can take 20-30s"
        if is_native:
            status_text = "Unshield WETH → auto-unwrap to native ETH (2 transactions)…"
        self._widgets["status_label"].configure(text=status_text, text_color="gray60")

        if not self.gui.key_manager:
            self._widgets["status_label"].configure(text="Vault not unlocked", text_color="red")
            self._widgets["confirm_btn"].configure(state="normal", text="Unshield")
            return
        mnemonic = self.gui.key_manager.show_mnemonic(self.account_name)
        if not mnemonic:
            self._widgets["status_label"].configure(text="No mnemonic for account", text_color="red")
            self._widgets["confirm_btn"].configure(state="normal", text="Unshield")
            return

        def _run():
            try:
                result = self.sidecar.unshield(
                    chain=params["chain"],
                    from_wallet_id=params["wallet_id"],
                    to_address=params["to_address"],
                    token_address=params["token_address"],
                    amount=params["amount_base"],
                    encryption_key=self.encryption_key,
                    signing_mnemonic=mnemonic,
                )
                self.gui.root.after(0, lambda r=result: self._show_result(r))
            except Exception as e:
                self.gui.root.after(0, lambda err=str(e): self._show_result({"status": "error", "error": err}))

        threading.Thread(target=_run, daemon=True).start()


class TransferDialog(_BaseTxDialog):
    """Private Transfer (0zk → 0zk) dialog."""

    DIALOG_TITLE = "Private Transfer (0zk → 0zk)"
    DIALOG_HEIGHT = 560

    def build_ui(self) -> None:
        """Build the transfer-specific UI."""
        super().build_ui()

        # ─── Destination (0zk, manual) ───
        ctk.CTkLabel(self.dialog, text="Destination (0zk):", anchor="w").pack(anchor="w", padx=20, pady=(0, 2))
        self._widgets["dest_entry"] = ctk.CTkEntry(
            self.dialog,
            placeholder_text="0zk... recipient address",
            width=500,
            font=ctk.CTkFont(size=11, family="monospace"),
        )
        self._widgets["dest_entry"].pack(fill="x", padx=20, pady=(0, 8))

        # ─── Memo ───
        ctk.CTkLabel(self.dialog, text="Memo (optional):", anchor="w").pack(anchor="w", padx=20, pady=(0, 2))
        self._widgets["memo_entry"] = ctk.CTkEntry(
            self.dialog,
            placeholder_text="Optional encrypted memo",
            width=500,
            font=ctk.CTkFont(size=11),
        )
        self._widgets["memo_entry"].pack(fill="x", padx=20, pady=(0, 5))

        # ─── Show sender checkbox ───
        self._widgets["show_sender_var"] = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(
            self.dialog,
            text="Show sender address to recipient",
            variable=self._widgets["show_sender_var"],
        ).pack(anchor="w", padx=20, pady=(0, 10))

        # ─── Native ETH unwrap checkbox (shown when token is ETH or wrapped-native) ───
        self._widgets["unwrap_native_var"] = ctk.BooleanVar(value=True)
        self._widgets["unwrap_native_cb"] = ctk.CTkCheckBox(
            self.dialog,
            text="Unwrap to native ETH after unshield (2 transactions)",
            variable=self._widgets["unwrap_native_var"],
        )
        # Hidden by default — shown when token is ETH or wrapped-native address
        self._widgets["unwrap_native_cb"].pack(anchor="w", padx=20, pady=(0, 10))

        # Rebuild button frame
        btn_frame = ctk.CTkFrame(self.dialog, fg_color="transparent")
        btn_frame.pack(pady=(5, 15))
        self._widgets["confirm_btn"] = ctk.CTkButton(
            btn_frame, text="Unshield", command=self._on_confirm,
            width=140, font=ctk.CTkFont(size=12, weight="bold"),
            fg_color=("#FF9800", "#E65100"),
        )
        self._widgets["confirm_btn"].pack(side="left", padx=10)
        ctk.CTkButton(
            btn_frame, text="Cancel", command=self.dialog.destroy,
            width=100, fg_color="gray30",
        ).pack(side="left", padx=10)

    def _is_native_eth_token(self) -> bool:
        """Check if the token field contains ETH or the chain's wrapped-native address."""
        token = self._widgets.get("token_entry")
        if not token:
            return False
        token_val = token.get().strip()
        if token_val.upper() == "ETH":
            return True
        return False

    def _on_token_focus_out(self, _event=None):
        """Fetch token info — extended to handle ETH native token."""
        token_addr = self._widgets["token_entry"].get().strip()
        chain = self._widgets["chain_var"].get()
        if not token_addr or not chain:
            return
        if not (token_addr.upper() == "ETH" or token_addr.startswith("0x")):
            return

        # Show/hide the native unwrap checkbox
        if self._is_native_eth_token():
            self._widgets["unwrap_native_cb"].pack(anchor="w", padx=20, pady=(0, 10))
        else:
            self._widgets["unwrap_native_cb"].pack_forget()

        def _fetch():
            try:
                result = self.sidecar.get_token_info(chain, token_addr)
                self.gui.root.after(0, lambda r=result: self._on_token_info(r))
            except Exception:
                self.gui.root.after(0, lambda: self._on_token_info({"status": "error"}))

        threading.Thread(target=_fetch, daemon=True).start()

    def _on_token_info(self, result: Dict[str, Any]):
        """Handle token info — extends base to handle native ETH."""
        if result.get("status") == "ok":
            self.token_symbol = result.get("symbol")
            self.token_decimals = result.get("decimals")
            is_native = result.get("native", False)
            parts = []
            if is_native:
                parts.append("ETH \u2190 held privately as")
            if self.token_symbol:
                parts.append(self.token_symbol)
            if self.token_decimals is not None:
                parts.append(f"({self.token_decimals} decimals)")
            self._widgets["token_info_label"].configure(
                text=" ".join(parts) if parts else "Token info not available",
                text_color=("#2196F3", "#1565C0"),
            )
            self._token_info_fetched = True
            if self.token_decimals is not None:
                self._widgets["amount_entry"].configure(
                    placeholder_text=f"0.0 ({'ETH' if is_native else self.token_symbol or 'token'} units)"
                )
        else:
            self.token_symbol = None
            self.token_decimals = None
            self._widgets["token_info_label"].configure(
                text="Token info unavailable — using raw base units",
                text_color="orange",
            )

    def _on_confirm(self):
        """Collect params and confirm."""
        params = self._confirm_summary()
        if not params:
            return
        params["to_address"] = self._widgets["dest_entry"].get().strip()
        if not params["to_address"] or not params["to_address"].startswith("0zk"):
            self._widgets["status_label"].configure(text="Destination 0zk address is required", text_color="red")
            return
        if not self.encryption_key:
            self._widgets["status_label"].configure(text="No encryption key — reload wallet", text_color="red")
            return
        params["memo_text"] = self._widgets["memo_entry"].get().strip() or None
        params["show_sender"] = self._widgets["show_sender_var"].get()
        self._submit(params)

    def _submit(self, params: Dict[str, str]):
        """Submit private transfer."""
        self._widgets["confirm_btn"].configure(state="disabled", text="Sending...")
        self._widgets["status_label"].configure(text="Generating proof… this can take 20-30s", text_color="gray60")

        if not self.gui.key_manager:
            self._widgets["status_label"].configure(text="Vault not unlocked", text_color="red")
            self._widgets["confirm_btn"].configure(state="normal", text="Send Transfer")
            return
        mnemonic = self.gui.key_manager.show_mnemonic(self.account_name)
        if not mnemonic:
            self._widgets["status_label"].configure(text="No mnemonic for account", text_color="red")
            self._widgets["confirm_btn"].configure(state="normal", text="Send Transfer")
            return

        def _run():
            try:
                result = self.sidecar.shielded_transfer(
                    chain=params["chain"],
                    from_wallet_id=params["wallet_id"],
                    to_address=params["to_address"],
                    token_address=params["token_address"],
                    amount=params["amount_base"],
                    encryption_key=self.encryption_key,
                    signing_mnemonic=mnemonic,
                    memo_text=params.get("memo_text"),
                    show_sender=params.get("show_sender", True),
                )
                self.gui.root.after(0, lambda r=result: self._show_result(r))
            except Exception as e:
                self.gui.root.after(0, lambda err=str(e): self._show_result({"status": "error", "error": err}))

        threading.Thread(target=_run, daemon=True).start()
