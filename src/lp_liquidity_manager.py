"""
ColdStack LP Liquidity Manager -- Add/Remove/Edit liquidity dialogs.

Separate module to keep gui_main_v5.py focused on layout/navigation.
All liquidity management UI lives here.
"""
import threading
import time
import customtkinter as ctk
from tkinter import messagebox
from typing import Optional, List, Dict
from datetime import datetime

from venue_adapters.venue_writer import (
    SwapParams,
    IncreaseLiquidityParams,
    DecreaseLiquidityParams,
    CollectFeesParams,
    RebalanceParams,
)

# Token constants (match hyperliquid_writer.py)
WHYPE = "0x5555555555555555555555555555555555555555"
UBTC = "0x9FDBdA0A5e284c32744D2f17Ee5c74B284993463"
WHYPE_DECIMALS = 18
UBTC_DECIMALS = 8


def _resolve_account_name(parent, wallet_address: str) -> Optional[str]:
    """Resolve a wallet address to a vault account name, if possible."""
    key_manager = getattr(parent, "key_manager", None)
    if not key_manager or not wallet_address:
        return None
    accounts_data = key_manager.address_db.get("accounts", {})
    for acct, data in accounts_data.items():
        for addr in data.get("addresses", []):
            if addr.get("address", "").lower() == wallet_address.lower():
                return acct
    return None


def _get_writer(parent):
    """Return an unlocked HyperliquidWriter from the parent's LPEngine, or None."""
    lp_engine = getattr(parent, "lp_engine", None)
    current_password = getattr(parent, "current_password", None)
    if not lp_engine:
        return None
    writer = lp_engine.get_writer("hyperliquid", current_password)
    return writer


def _notify(parent, message: str, error: bool = False):
    """Call parent's show_notification safely."""
    show = getattr(parent, "show_notification", None)
    if show:
        show(message, error=error)


def _add_status_tooltip(widget, status_label, text):
    """Show text in a status label when the widget is hovered."""
    widget.bind("<Enter>", lambda e: status_label.configure(text=text))
    widget.bind("<Leave>", lambda e: status_label.configure(text=""))


def _safe_after(parent, delay_ms: int, callback):
    """Schedule a callback on the parent's root widget after delay_ms."""
    root = getattr(parent, "root", None)
    if root:
        root.after(delay_ms, callback)


class AddLiquidityDialog:
    """Add Liquidity sub-window with manual entry or auto-balance (Zap In)."""

    def __init__(self, parent, position, key_manager, price_engine, wallet_address):
        """
        Args:
            parent: The main GUI window (for notifications and refresh callbacks)
            position: LPPosition object for the position to add liquidity to
            key_manager: KeyManagerAgent instance for signing txs
            price_engine: PriceEngine for USD conversions
            wallet_address: The wallet address to use
        """
        self.parent = parent
        self.position = position
        self.key_manager = key_manager
        self.price_engine = price_engine
        self.wallet_address = wallet_address

        self.win = ctk.CTkToplevel(parent.root)
        self.win.title("Add Liquidity")
        self.win.geometry("440x640")
        self.win.resizable(False, False)
        self.win.grab_set()

        self._build_ui()

    def _build_ui(self):
        """Build the Add Liquidity dialog."""
        # -- Header --
        header_frame = ctk.CTkFrame(self.win, fg_color="transparent")
        header_frame.pack(fill="x", padx=20, pady=(15, 5))

        ctk.CTkLabel(header_frame, text="Add Liquidity",
                     font=ctk.CTkFont(size=18, weight="bold")).pack(side="left")
        ctk.CTkButton(header_frame, text="\u2715", width=28, height=28,
                      fg_color="transparent", hover_color="gray20",
                      command=self.win.destroy).pack(side="right")

        # -- Subheader: pair . fee . range status --
        pair = self.position.pair or "WHYPE/UBTC"
        in_range = (self.position.position_in_range_pct is not None
                    and 0 <= self.position.position_in_range_pct <= 100)
        range_status = "IN RANGE" if in_range else "OUT OF RANGE"
        fee_text = f"{pair} \u00b7 0.3% fee \u00b7 {range_status}"
        ctk.CTkLabel(self.win, text=fee_text,
                     font=ctk.CTkFont(size=11), text_color="gray70").pack(anchor="w", padx=20)

        # -- Position ratio indicator --
        holdings = self.position.deposit_amounts or {}
        tokens = list(holdings.keys()) if holdings else ["HYPE", "BTC"]
        # Compute the position's current value ratio
        # Use price_engine to convert to USD for accurate ratio
        usd_values = {}
        for sym, amt in holdings.items():
            if amt and self.price_engine:
                fiat = self.price_engine.convert_balance_to_fiat(
                    amt, "HYPE" if sym in ("HYPE", "WHYPE") else "BTC", currency="usd"
                )
                usd_values[sym] = fiat or 0
            else:
                usd_values[sym] = 0
        total_usd = sum(usd_values.values())
        if total_usd > 0:
            ratio_parts = []
            for sym in tokens:
                pct = (usd_values.get(sym, 0) / total_usd) * 100
                ratio_parts.append(f"{pct:.0f}% {sym}")
            ratio_text = "Position Ratio: " + " / ".join(ratio_parts)
        else:
            # Fallback to raw amounts if no price data
            total_raw = sum(holdings.values())
            if total_raw > 0:
                ratio_parts = [f"{(holdings[s] / total_raw) * 100:.0f}% {sym}" for sym in tokens]
                ratio_text = "Position Ratio: " + " / ".join(ratio_parts)
            else:
                ratio_text = "Position Ratio: N/A"
        ctk.CTkLabel(self.win, text=ratio_text,
                     font=ctk.CTkFont(size=10), text_color="gray60").pack(anchor="w", padx=20, pady=(2, 0))

        # -- Token input boxes --
        # Determine token0/token1 from position deposit_amounts
        # deposit_amounts is a dict like {"HYPE": 71.1058, "BTC": 0.00401725}
        tokens = list(self.position.deposit_amounts.keys()) if self.position.deposit_amounts else ["HYPE", "BTC"]

        # Token display info
        token_info = {
            "HYPE": {"label": "HYPE", "address": WHYPE, "decimals": WHYPE_DECIMALS},
            "BTC": {"label": "UBTC", "address": UBTC, "decimals": UBTC_DECIMALS},
        }

        self.token_entries: Dict[str, ctk.CTkEntry] = {}
        self.balance_labels: Dict[str, ctk.CTkLabel] = {}

        for i, sym in enumerate(tokens):
            info = token_info.get(sym, {"label": sym, "address": "", "decimals": 18})

            # Token input box
            box = ctk.CTkFrame(self.win, fg_color="gray15", corner_radius=10)
            box.pack(fill="x", padx=20, pady=(10 if i == 0 else 5, 5))

            # Token name + balance
            top_row = ctk.CTkFrame(box, fg_color="transparent")
            top_row.pack(fill="x", padx=10, pady=(8, 0))
            ctk.CTkLabel(top_row, text=info["label"],
                         font=ctk.CTkFont(size=13, weight="bold")).pack(side="left")

            # Wallet balance for this token (fetch via RPC)
            balance_text = f"Balance: {self._get_wallet_balance(sym):.8f}"
            bal_label = ctk.CTkLabel(top_row, text=balance_text,
                                      font=ctk.CTkFont(size=10), text_color="gray60")
            bal_label.pack(side="right")
            self.balance_labels[sym] = bal_label

            # Input row
            input_row = ctk.CTkFrame(box, fg_color="transparent")
            input_row.pack(fill="x", padx=10, pady=(5, 10))

            entry = ctk.CTkEntry(input_row, placeholder_text="0.0",
                                 font=ctk.CTkFont(size=16))
            entry.pack(side="left", fill="x", expand=True, padx=(0, 5))
            self.token_entries[sym] = entry

            # 50% and Max buttons
            ctk.CTkButton(input_row, text="50%", width=40, height=28,
                          font=ctk.CTkFont(size=10),
                          command=lambda s=sym: self._fill_percent(s, 50)
                          ).pack(side="left", padx=2)
            ctk.CTkButton(input_row, text="Max", width=40, height=28,
                          font=ctk.CTkFont(size=10),
                          command=lambda s=sym: self._fill_percent(s, 100)
                          ).pack(side="left", padx=2)

            # "+" separator between the two token boxes
            if i == 0 and len(tokens) > 1:
                ctk.CTkLabel(self.win, text="+",
                             font=ctk.CTkFont(size=16, weight="bold"),
                             text_color="gray50").pack(pady=2)

        # -- Liquidity composition bar --
        comp_frame = ctk.CTkFrame(self.win, fg_color="transparent")
        comp_frame.pack(fill="x", padx=20, pady=(10, 5))
        ctk.CTkLabel(comp_frame, text="Liquidity Composition:",
                     font=ctk.CTkFont(size=11), text_color="gray70").pack(anchor="w")

        # Composition bar (updates as user types)
        self.comp_bar = ctk.CTkProgressBar(comp_frame, height=8, corner_radius=4)
        self.comp_bar.pack(fill="x", pady=5)
        self.comp_label = ctk.CTkLabel(comp_frame, text="",
                                       font=ctk.CTkFont(size=10), text_color="gray60")
        self.comp_label.pack(anchor="w")

        # Bind entry changes to update composition
        for sym, entry in self.token_entries.items():
            entry.bind("<KeyRelease>", lambda e, s=sym: self._on_entry_edit(s))

        # -- Auto-balance (Zap In) toggle --
        self.auto_balance_var = ctk.BooleanVar(value=False)
        ab_frame = ctk.CTkFrame(self.win, fg_color="gray15", corner_radius=8)
        ab_frame.pack(fill="x", padx=20, pady=10)

        ab_top = ctk.CTkFrame(ab_frame, fg_color="transparent")
        ab_top.pack(fill="x", padx=10, pady=(8, 0))
        ctk.CTkLabel(ab_top, text="Auto-Balance (Zap In)",
                     font=ctk.CTkFont(size=12, weight="bold")).pack(side="left")
        ctk.CTkSwitch(ab_top, variable=self.auto_balance_var, text="",
                      command=self._on_auto_balance_toggle
                      ).pack(side="right")

        ctk.CTkLabel(ab_frame,
                     text="When ON: enter one token amount, system swaps\n"
                          "to match the position's current ratio, then deposits.",
                     font=ctk.CTkFont(size=10), text_color="gray60"
                     ).pack(anchor="w", padx=10, pady=(0, 8))

        # -- Footer: slippage + total deposit --
        footer = ctk.CTkFrame(self.win, fg_color="transparent")
        footer.pack(fill="x", padx=20, pady=(5, 10))

        ctk.CTkLabel(footer, text="Slippage: 2%",
                     font=ctk.CTkFont(size=11), text_color="gray70").pack(side="left")
        self.total_label = ctk.CTkLabel(footer, text="Total Deposit: $0.00",
                                         font=ctk.CTkFont(size=11, weight="bold"),
                                         text_color="#51cf94")
        self.total_label.pack(side="right")

        # -- Submit button --
        self.submit_btn = ctk.CTkButton(
            self.win, text="ENTER AMOUNT", height=44,
            font=ctk.CTkFont(size=14, weight="bold"),
            fg_color=("#20c997", "#1aa179"),
            state="disabled",
            command=self._submit
        )
        self.submit_btn.pack(fill="x", padx=20, pady=10)

    def _get_wallet_balance(self, symbol: str) -> float:
        """Fetch wallet balance for a token via RPC."""
        # For v5.1.1: fetch from the adapter or directly via RPC
        try:
            from venue_adapters.hyperliquid_adapter import _evm_rpc_call
            token_addr = (WHYPE if symbol in ("HYPE", "WHYPE") else UBTC)
            decimals = (WHYPE_DECIMALS if symbol in ("HYPE", "WHYPE") else UBTC_DECIMALS)
            # balanceOf(address) selector = 0x70a08231
            data = "0x70a08231" + self.wallet_address[2:].lower().zfill(64)
            result = _evm_rpc_call("eth_call", [{"to": token_addr, "data": data}, "latest"])
            if result:
                raw = int(result, 16)
                return raw / (10 ** decimals)
        except Exception:
            pass
        return 0.0

    def _compute_paired_amount(self, filled_sym: str, filled_amount: float) -> Optional[float]:
        """Given one token amount, compute the paired token amount based on position ratio.

        Uses the position's current holdings to determine the ratio.
        Returns the paired amount in human-readable units, or None if ratio can't be computed.
        """
        holdings = self.position.deposit_amounts or {}
        if not holdings or len(holdings) < 2:
            return None

        tokens = list(holdings.keys())
        # Find the other token
        other_sym = None
        for t in tokens:
            if t != filled_sym:
                other_sym = t
                break
        if other_sym is None:
            return None

        # Compute USD value ratio
        filled_usd = 0
        other_usd = 0
        if self.price_engine:
            filled_key = "HYPE" if filled_sym in ("HYPE", "WHYPE") else "BTC"
            other_key = "HYPE" if other_sym in ("HYPE", "WHYPE") else "BTC"
            filled_usd = self.price_engine.convert_balance_to_fiat(filled_amount, filled_key, currency="usd") or 0
            other_holding = holdings.get(other_sym, 0)
            other_usd = self.price_engine.convert_balance_to_fiat(other_holding, other_key, currency="usd") or 0
            filled_holding = holdings.get(filled_sym, 0)
            filled_holding_usd = self.price_engine.convert_balance_to_fiat(filled_holding, filled_key, currency="usd") or 0

        if filled_usd <= 0 or other_usd <= 0 or filled_holding_usd <= 0:
            return None

        # Ratio: if position is 95% HYPE / 5% BTC
        # User fills HYPE = X (USD value = X_usd)
        # Required BTC USD value = X_usd * (other_pct / filled_pct)
        # Required BTC amount = required_btc_usd / btc_price
        total_usd = filled_holding_usd + other_usd
        filled_pct = filled_holding_usd / total_usd  # e.g., 0.95
        other_pct = other_usd / total_usd            # e.g., 0.05

        required_other_usd = filled_usd * (other_pct / filled_pct)

        # Convert back to token amount
        other_key = "HYPE" if other_sym in ("HYPE", "WHYPE") else "BTC"
        other_price = self.price_engine.get_price(other_key, "usd") if hasattr(self.price_engine, 'get_price') else None
        if other_price and other_price > 0:
            return required_other_usd / other_price
        # Fallback: use the holding ratio directly
        if filled_holding_usd > 0 and other_usd > 0:
            ratio = holdings.get(other_sym, 0) / holdings.get(filled_sym, 1)
            return filled_amount * ratio
        return None

    def _fill_percent(self, symbol: str, pct: int):
        """Fill entry with pct% of wallet balance, then auto-fill the paired token."""
        balance = self._get_wallet_balance(symbol)
        amount = balance * pct / 100
        self.token_entries[symbol].delete(0, "end")
        self.token_entries[symbol].insert(0, f"{amount:.8f}".rstrip('0').rstrip('.'))

        # Auto-calculate paired token amount based on position ratio
        if not self.auto_balance_var.get():
            paired_amount = self._compute_paired_amount(symbol, amount)
            if paired_amount is not None and paired_amount > 0:
                # Find the other token symbol
                tokens = list(self.token_entries.keys())
                other_sym = None
                for t in tokens:
                    if t != symbol:
                        other_sym = t
                        break
                if other_sym:
                    other_balance = self._get_wallet_balance(other_sym)
                    self.token_entries[other_sym].delete(0, "end")
                    if paired_amount <= other_balance:
                        self.token_entries[other_sym].insert(0, f"{paired_amount:.8f}".rstrip('0').rstrip('.'))
                        # Sufficient — reset text color
                        self.token_entries[other_sym].configure(text_color="white")
                    else:
                        # Not enough balance — fill what we have and warn
                        self.token_entries[other_sym].insert(0, f"{other_balance:.8f}".rstrip('0').rstrip('.'))
                        # Mark the insufficient token entry in red
                        self.token_entries[other_sym].configure(text_color="#ff6b6b")
                        # Show warning
                        self.comp_label.configure(
                            text=f"\u26a0 Insufficient {other_sym} balance for full ratio. "
                                 f"Need {paired_amount:.8f}, have {other_balance:.8f}. "
                                 f"Toggle Auto-Balance (Zap In) to swap excess {symbol} \u2192 {other_sym}.",
                            text_color="#ffd43b")

        self._update_composition()

    def _update_composition(self):
        """Update the composition bar, total deposit, and ratio match indicator."""
        tokens = list(self.token_entries.keys())
        amounts = []
        for sym in tokens:
            try:
                val = float(self.token_entries[sym].get() or "0")
            except ValueError:
                val = 0
            amounts.append(val)

        total = sum(amounts)

        # Compute entered ratio
        if total > 0:
            pct0 = amounts[0] / total
            self.comp_bar.set(pct0)
            entered_text = f"{pct0*100:.0f}% {tokens[0]} / {(1-pct0)*100:.0f}% {tokens[1]}"
        else:
            self.comp_bar.set(0)
            entered_text = ""

        # Compute target ratio from position holdings (in USD)
        holdings = self.position.deposit_amounts or {}
        if holdings and self.price_engine:
            usd_vals = {}
            for sym, amt in holdings.items():
                key = "HYPE" if sym in ("HYPE", "WHYPE") else "BTC"
                usd_vals[sym] = self.price_engine.convert_balance_to_fiat(amt, key, currency="usd") or 0
            total_holding_usd = sum(usd_vals.values())
            if total_holding_usd > 0:
                target_parts = []
                for sym in tokens:
                    pct = (usd_vals.get(sym, 0) / total_holding_usd) * 100
                    target_parts.append(f"{pct:.0f}% {sym}")
                target_text = " / ".join(target_parts)
            else:
                target_text = "N/A"
        else:
            target_text = "N/A"

        # Combined label: show entered ratio + target ratio
        if total > 0 and target_text != "N/A":
            match = "\u2713" if self._ratios_match(amounts, holdings) else "\u26a0"
            self.comp_label.configure(
                text=f"Entered: {entered_text}  {match}  Target: {target_text}",
                text_color="gray60")
        elif total > 0:
            self.comp_label.configure(text=entered_text, text_color="gray60")
        else:
            self.comp_label.configure(text=f"Target: {target_text}", text_color="gray50")

        # Update total deposit in USD
        usd_total = 0.0
        price_map = {"HYPE": "HYPE", "BTC": "BTC"}
        for sym, amt in zip(tokens, amounts):
            if amt > 0 and self.price_engine:
                fiat = self.price_engine.convert_balance_to_fiat(amt, price_map.get(sym, sym), currency="usd")
                usd_total += fiat or 0
        self.total_label.configure(text=f"Total Deposit: ${usd_total:.2f}")

        # Enable/disable submit button
        has_input = any(a > 0 for a in amounts)
        if self.auto_balance_var.get():
            # Auto-balance: only need one input
            has_input = sum(amounts) > 0
        if has_input:
            self.submit_btn.configure(state="normal",
                                       text="ADD LIQUIDITY")
        else:
            self.submit_btn.configure(state="disabled",
                                       text="ENTER AMOUNT")

    def _ratios_match(self, amounts, holdings, tolerance=0.05):
        """Check if entered amounts roughly match the position's holding ratio.

        tolerance: 5% deviation allowed (user doesn't need exact match).
        """
        if len(amounts) < 2 or not holdings or len(holdings) < 2:
            return True  # Don't warn if we can't compute

        tokens = list(self.token_entries.keys())
        # Compute USD values of entered amounts
        entered_usd = []
        for sym, amt in zip(tokens, amounts):
            if amt <= 0:
                return False
            key = "HYPE" if sym in ("HYPE", "WHYPE") else "BTC"
            usd = self.price_engine.convert_balance_to_fiat(amt, key, currency="usd") or 0
            entered_usd.append(usd)

        total_entered = sum(entered_usd)
        if total_entered <= 0:
            return True

        # Compute holding USD values
        holding_usd = []
        for sym in tokens:
            amt = holdings.get(sym, 0)
            key = "HYPE" if sym in ("HYPE", "WHYPE") else "BTC"
            usd = self.price_engine.convert_balance_to_fiat(amt, key, currency="usd") or 0
            holding_usd.append(usd)

        total_holding = sum(holding_usd)
        if total_holding <= 0:
            return True

        # Compare ratios
        for i in range(len(entered_usd)):
            entered_pct = entered_usd[i] / total_entered
            holding_pct = holding_usd[i] / total_holding
            if abs(entered_pct - holding_pct) > tolerance:
                return False
        return True

    def _on_entry_edit(self, sym):
        """Reset warning state when user manually edits an entry."""
        self.token_entries[sym].configure(text_color="white")
        self._update_composition()

    def _on_auto_balance_toggle(self):
        """When auto-balance is toggled, update UI hints."""
        if self.auto_balance_var.get():
            # Auto-balance ON: user can enter one token, system handles the rest
            self.comp_label.configure(text="Auto-balance ON -- enter one amount, ratio will be matched automatically")
        else:
            self._update_composition()

    def _submit(self):
        """Execute the add liquidity flow."""
        # Resolve vault account from wallet address
        account_name = _resolve_account_name(self.parent, self.wallet_address)
        if not account_name:
            _notify(self.parent, "Could not resolve vault account for this address", error=True)
            return

        tokens = list(self.token_entries.keys())
        amounts = []
        for sym in tokens:
            try:
                val = float(self.token_entries[sym].get() or "0")
            except ValueError:
                val = 0.0
            amounts.append(val)

        if sum(amounts) <= 0:
            _notify(self.parent, "Enter an amount to add", error=True)
            return

        _notify(self.parent, "Preparing add liquidity...")

        def _do_add():
            try:
                writer = _get_writer(self.parent)
                if writer is None:
                    _safe_after(self.parent, 0, lambda: _notify(
                        self.parent, "Writer not available", error=True))
                    return
                if not writer.is_available():
                    _safe_after(self.parent, 0, lambda: _notify(
                        self.parent, "Agent not running. Start key_manager_agent with --serve.", error=True))
                    return

                token0 = WHYPE
                token1 = UBTC
                amount0 = amounts[0]
                amount1 = amounts[1]

                if self.auto_balance_var.get():
                    # Determine target ratio from position holdings
                    holdings = self.position.deposit_amounts or {}
                    hype_amt = holdings.get("HYPE", 0.0)
                    btc_amt = holdings.get("BTC", 0.0)
                    input_amts = dict(zip(tokens, amounts))
                    input_total = sum(amounts)

                    # Decide which token is present and whether it is excess relative to holdings ratio
                    if len(tokens) >= 2:
                        ratio = hype_amt / (hype_amt + btc_amt) if (hype_amt + btc_amt) > 0 else 0.5
                        first_is_hype = tokens[0] in ("HYPE", "WHYPE")
                        first_amt = input_amts.get(tokens[0], 0.0)
                        if first_is_hype:
                            target_hype = input_total * ratio
                            if first_amt > target_hype:
                                swap_in_token = WHYPE
                                swap_out_token = UBTC
                                swap_amount = first_amt - target_hype
                            else:
                                swap_in_token = UBTC
                                swap_out_token = WHYPE
                                swap_amount = input_total - first_amt - (input_total * (1 - ratio))
                                if swap_amount <= 0:
                                    swap_amount = 0.0
                        else:
                            # First token is BTC
                            target_btc = input_total * (1 - ratio)
                            if first_amt > target_btc:
                                swap_in_token = UBTC
                                swap_out_token = WHYPE
                                swap_amount = first_amt - target_btc
                            else:
                                swap_in_token = WHYPE
                                swap_out_token = UBTC
                                swap_amount = input_total - first_amt - (input_total * ratio)
                                if swap_amount <= 0:
                                    swap_amount = 0.0

                        if swap_amount > 0:
                            _safe_after(self.parent, 0, lambda: _notify(
                                self.parent, f"Auto-balancing: swapping {swap_amount:.8f} {swap_in_token}..."))
                            quote = writer.get_swap_quote(swap_in_token, swap_out_token, swap_amount)
                            _safe_after(self.parent, 0, lambda: _notify(
                                self.parent, f"Swap quote: {quote:.8f} {swap_out_token}" if quote else "Swap quote unavailable"))
                            swap_tx = writer.swap(SwapParams(
                                account=account_name,
                                token_in=swap_in_token,
                                token_out=swap_out_token,
                                amount_in=swap_amount,
                                recipient=self.wallet_address,
                            ))
                            _safe_after(self.parent, 0, lambda: _notify(
                                self.parent, f"Swap broadcast: {swap_tx[:20]}..."))
                            receipt = writer._wait_for_tx_receipt(swap_tx, timeout=120, poll_interval=2.0)
                            if receipt is None:
                                _safe_after(self.parent, 0, lambda: _notify(
                                    self.parent, "Swap not mined in time -- continuing with entered amounts", error=True))
                            elif receipt.get("status") != "0x1":
                                _safe_after(self.parent, 0, lambda: _notify(
                                    self.parent, "Swap reverted -- continuing with entered amounts", error=True))

                            # Re-read wallet balances after swap and use them as deposit amounts
                            try:
                                amount0 = writer._read_balance(self.wallet_address, token0) / (10 ** WHYPE_DECIMALS)
                                amount1 = writer._read_balance(self.wallet_address, token1) / (10 ** UBTC_DECIMALS)
                                # Cap at the original intended total so pre-existing funds are not consumed
                                original_hype = input_amts.get("HYPE", input_amts.get("WHYPE", 0.0))
                                original_btc = input_amts.get("BTC", 0.0)
                                amount0 = min(amount0, original_hype * 1.05)
                                amount1 = min(amount1, original_btc * 1.05)
                            except Exception:
                                pass

                tx_hash = writer.increase_liquidity(IncreaseLiquidityParams(
                    account=account_name,
                    position_id=self.position.position_id,
                    amount0=amount0,
                    amount1=amount1,
                ))

                _safe_after(self.parent, 0, lambda: _notify(
                    self.parent, f"Adding liquidity. TX: {tx_hash[:20]}..."))
                receipt = writer._wait_for_tx_receipt(tx_hash, timeout=120, poll_interval=2.0)
                if receipt and receipt.get("status") == "0x1":
                    _safe_after(self.parent, 0, lambda: _notify(self.parent, "Liquidity added \u2713"))
                else:
                    _safe_after(self.parent, 0, lambda: _notify(
                        self.parent, "Liquidity added TX mined, verify on-chain", error=True))

                refresh = getattr(self.parent, "_lp_refresh_position_fees", None)
                if refresh:
                    _safe_after(self.parent, 5000, lambda: refresh(self.position.position_id, self.wallet_address))
                _safe_after(self.parent, 5000, self.win.destroy)
            except Exception as e:
                error_msg = str(e)
                print(f"[add_liquidity] error: {error_msg}")
                if "insufficient funds" in error_msg.lower() or "Insufficient gas" in error_msg:
                    error_msg = (
                        f"Wallet has no HYPE for gas. Send HYPE to your wallet address "
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
                        "succeeded on-chain -- check Project X to verify. Try again if needed."
                    )
                _safe_after(self.parent, 0, lambda: _notify(
                    self.parent, f"Add liquidity error: {error_msg}", error=True))

        threading.Thread(target=_do_add, daemon=True).start()


class RemoveLiquidityDialog:
    """Remove Liquidity sub-window with percentage slider."""

    def __init__(self, parent, position, key_manager, price_engine, wallet_address):
        self.parent = parent
        self.position = position
        self.key_manager = key_manager
        self.price_engine = price_engine
        self.wallet_address = wallet_address

        self.win = ctk.CTkToplevel(parent.root)
        self.win.title("Remove Liquidity")
        self.win.geometry("440x520")
        self.win.resizable(False, False)
        self.win.grab_set()

        self._build_ui()

    def _build_ui(self):
        """Build the Remove Liquidity dialog."""
        # -- Header --
        header_frame = ctk.CTkFrame(self.win, fg_color="transparent")
        header_frame.pack(fill="x", padx=20, pady=(15, 5))

        ctk.CTkLabel(header_frame, text="Remove Liquidity",
                     font=ctk.CTkFont(size=18, weight="bold")).pack(side="left")
        ctk.CTkButton(header_frame, text="\u2715", width=28, height=28,
                      fg_color="transparent", hover_color="gray20",
                      command=self.win.destroy).pack(side="right")

        # -- Subheader --
        pair = self.position.pair or "WHYPE/UBTC"
        in_range = (self.position.position_in_range_pct is not None
                    and 0 <= self.position.position_in_range_pct <= 100)
        range_status = "IN RANGE" if in_range else "OUT OF RANGE"
        ctk.CTkLabel(self.win, text=f"{pair} \u00b7 {range_status}",
                     font=ctk.CTkFont(size=11), text_color="gray70").pack(anchor="w", padx=20)

        # -- Current liquidity --
        # Read from position or fetch on-chain
        ctk.CTkLabel(self.win, text="Current Position Liquidity:",
                     font=ctk.CTkFont(size=11), text_color="gray70").pack(anchor="w", padx=20, pady=(15, 0))
        ctk.CTkLabel(self.win, text=f"{self._get_position_liquidity():,}",
                     font=ctk.CTkFont(size=14, weight="bold")).pack(anchor="w", padx=20)

        # -- Percentage slider --
        slider_frame = ctk.CTkFrame(self.win, fg_color="transparent")
        slider_frame.pack(fill="x", padx=20, pady=15)

        ctk.CTkLabel(slider_frame, text="Amount to Remove:",
                     font=ctk.CTkFont(size=11), text_color="gray70").pack(anchor="w")

        self.pct_var = ctk.DoubleVar(value=0)
        slider = ctk.CTkSlider(slider_frame, from_=0, to=100, variable=self.pct_var,
                               command=self._on_slider_change)
        slider.pack(fill="x", pady=5)

        # Percentage entry
        pct_row = ctk.CTkFrame(slider_frame, fg_color="transparent")
        pct_row.pack(fill="x")
        self.pct_entry = ctk.CTkEntry(pct_row, width=80, placeholder_text="0")
        self.pct_entry.pack(side="left")
        ctk.CTkLabel(pct_row, text="%", font=ctk.CTkFont(size=13)).pack(side="left", padx=(2, 0))

        # -- Estimated output --
        est_frame = ctk.CTkFrame(self.win, fg_color="gray15", corner_radius=8)
        est_frame.pack(fill="x", padx=20, pady=10)
        ctk.CTkLabel(est_frame, text="Estimated Output:",
                     font=ctk.CTkFont(size=11, weight="bold")).pack(anchor="w", padx=10, pady=(8, 4))
        self.est_label = ctk.CTkLabel(est_frame, text="\u2014",
                                       font=ctk.CTkFont(size=12), text_color="gray70")
        self.est_label.pack(anchor="w", padx=10, pady=(0, 8))

        # -- Collect fees checkbox --
        self.collect_after_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(self.win, text="Collect fees after removal",
                        variable=self.collect_after_var,
                        font=ctk.CTkFont(size=11)).pack(anchor="w", padx=20, pady=5)

        # -- Note at 100% --
        self.note_label = ctk.CTkLabel(self.win, text="",
                                        font=ctk.CTkFont(size=10), text_color="#ffd43b")
        self.note_label.pack(anchor="w", padx=20)

        # -- Submit button --
        self.submit_btn = ctk.CTkButton(
            self.win, text="REMOVE LIQUIDITY", height=44,
            font=ctk.CTkFont(size=14, weight="bold"),
            fg_color=("#fd7e14", "#dc6602"),
            state="disabled",
            command=self._submit
        )
        self.submit_btn.pack(fill="x", padx=20, pady=10)

    def _get_position_liquidity(self) -> int:
        """Fetch current position liquidity from chain."""
        writer = _get_writer(self.parent)
        if writer is None:
            # Fallback: read via adapter
            try:
                from venue_adapters.hyperliquid_adapter import _evm_rpc_call
                token_id = int(self.position.position_id.split(":", 1)[1])
                data = "0x99fbab88" + format(token_id, '064x')
                result = _evm_rpc_call("eth_call", [{"to": "0xead19ae861c29bbb2101e834922b2feee69b9091", "data": data}, "latest"])
                if result:
                    body = result[2:]
                    liquidity = int(body[448:512], 16)
                    return liquidity
            except Exception:
                pass
            return 0
        try:
            token_id = writer._parse_token_id(self.position.position_id)
            if token_id is None:
                return 0
            return writer._get_position_liquidity(token_id)
        except Exception:
            return 0

    def _on_slider_change(self, value):
        """Update estimated output when slider moves."""
        pct = int(value)
        self.pct_entry.delete(0, "end")
        self.pct_entry.insert(0, str(pct))

        if pct == 0:
            self.submit_btn.configure(state="disabled")
            self.est_label.configure(text="\u2014")
            self.note_label.configure(text="")
            return

        self.submit_btn.configure(state="normal")

        # Estimate output based on position holdings * pct
        holdings = self.position.deposit_amounts or {}
        est_parts = []
        for sym, amt in holdings.items():
            est_amt = amt * pct / 100
            est_parts.append(f"{est_amt:.8f} {sym}")
        self.est_label.configure(text=" \u00b7 ".join(est_parts))

        if pct == 100:
            self.note_label.configure(text="This will close your position. Use Close Position for the full flow.")
        else:
            self.note_label.configure(text="")

    def _submit(self):
        """Execute the remove liquidity flow."""
        account_name = _resolve_account_name(self.parent, self.wallet_address)
        if not account_name:
            _notify(self.parent, "Could not resolve vault account for this address", error=True)
            return

        try:
            pct = int(self.pct_entry.get() or "0")
        except ValueError:
            _notify(self.parent, "Invalid percentage", error=True)
            return

        if pct <= 0 or pct > 100:
            _notify(self.parent, "Enter a percentage between 1 and 100", error=True)
            return

        _notify(self.parent, "Preparing remove liquidity...")

        def _do_remove():
            try:
                writer = _get_writer(self.parent)
                if writer is None:
                    _safe_after(self.parent, 0, lambda: _notify(
                        self.parent, "Writer not available", error=True))
                    return
                if not writer.is_available():
                    _safe_after(self.parent, 0, lambda: _notify(
                        self.parent, "Agent not running. Start key_manager_agent with --serve.", error=True))
                    return

                total_liquidity = writer._get_position_liquidity(writer._parse_token_id(self.position.position_id))
                liquidity_to_remove = int(total_liquidity * pct / 100)
                if liquidity_to_remove <= 0:
                    _safe_after(self.parent, 0, lambda: _notify(
                        self.parent, "No liquidity to remove", error=True))
                    return

                tx_hash = writer.decrease_liquidity(DecreaseLiquidityParams(
                    account=account_name,
                    position_id=self.position.position_id,
                    liquidity=liquidity_to_remove,
                ))

                _safe_after(self.parent, 0, lambda: _notify(
                    self.parent, f"Removing liquidity. TX: {tx_hash[:20]}..."))
                receipt = writer._wait_for_tx_receipt(tx_hash, timeout=120, poll_interval=2.0)
                if receipt and receipt.get("status") == "0x1":
                    _safe_after(self.parent, 0, lambda: _notify(self.parent, "Liquidity removed \u2713"))
                else:
                    _safe_after(self.parent, 0, lambda: _notify(
                        self.parent, "Remove TX mined, verify on-chain", error=True))

                if self.collect_after_var.get():
                    _safe_after(self.parent, 0, lambda: _notify(
                        self.parent, "Collecting fees after removal..."))
                    collect_tx = writer.collect_fees(CollectFeesParams(
                        account=account_name,
                        position_id=self.position.position_id,
                        recipient=self.wallet_address,
                    ))
                    writer._wait_for_tx_receipt(collect_tx, timeout=120, poll_interval=2.0)
                    _safe_after(self.parent, 0, lambda: _notify(
                        self.parent, f"Fees collected. TX: {collect_tx[:20]}..."))

                refresh = getattr(self.parent, "_lp_refresh_position_fees", None)
                if refresh:
                    _safe_after(self.parent, 5000, lambda: refresh(self.position.position_id, self.wallet_address))
                _safe_after(self.parent, 5000, self.win.destroy)
            except Exception as e:
                error_msg = str(e)
                print(f"[remove_liquidity] error: {error_msg}")
                if "insufficient funds" in error_msg.lower() or "Insufficient gas" in error_msg:
                    error_msg = (
                        f"Wallet has no HYPE for gas. Send HYPE to your wallet address "
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
                        "succeeded on-chain -- check Project X to verify. Try again if needed."
                    )
                _safe_after(self.parent, 0, lambda: _notify(
                    self.parent, f"Remove liquidity error: {error_msg}", error=True))

        threading.Thread(target=_do_remove, daemon=True).start()


class EditPositionDialog:
    """Rebalance Position dialog for Aerodrome (Base) and Orca (Solana) LP positions.

    Shows current range / price, offers Auto-Recenter (±buffer around current price)
    or Custom Range (manual human prices → ticks), then runs the multi-TX rebalance
    flow via the venue's writer.rebalance() on confirmation.

    For HyperEVM / BSC positions the stub behaviour is preserved (rebalance is not
    yet implemented there).
    """

    def __init__(self, parent, position):
        self.parent = parent
        self.position = position

        pos_id = (position.position_id or "")
        self._is_base = pos_id.startswith("base:")
        self._is_solana = pos_id.startswith("solana:")

        # Stub fallback for venues that don't support rebalance yet
        if not (self._is_base or self._is_solana):
            self.win = ctk.CTkToplevel(parent.root)
            self.win.title("Edit Position")
            self.win.geometry("360x220")
            self.win.resizable(False, False)
            self.win.grab_set()
            ctk.CTkLabel(self.win, text="\u270e Edit Position",
                         font=ctk.CTkFont(size=18, weight="bold")).pack(pady=(30, 10))
            ctk.CTkLabel(self.win, text="Rebalance is available for Aerodrome (BASE) and Orca (Solana) positions",
                         font=ctk.CTkFont(size=12), text_color="gray70").pack(pady=5)
            ctk.CTkLabel(self.win,
                         text="HyperEVM and BSC position editing is planned for a future release.",
                         font=ctk.CTkFont(size=11), text_color="gray50").pack(pady=5)
            ctk.CTkButton(self.win, text="Close", width=100, height=30,
                          command=self.win.destroy).pack(pady=15)
            return

        # Raw position data populated by the adapter fetch
        raw = position.raw_data if isinstance(position.raw_data, dict) else {}
        self.tick_lower_old = raw.get("tick_lower")
        self.tick_upper_old = raw.get("tick_upper")
        self.tick_spacing = raw.get("tick_spacing", 60)
        self.decimals0 = raw.get("decimals0", 18)
        self.decimals1 = raw.get("decimals1", 6)

        # Gas token + writer key by venue
        if self._is_solana:
            self._gas_token = "SOL"
            self._writer_key = "orca"
            self._chain_name = "Solana"
        else:
            self._gas_token = "ETH"
            self._writer_key = "aerodrome"
            self._chain_name = "BASE"

        self.win = ctk.CTkToplevel(parent.root)
        self.win.title("Rebalance Position")
        self.win.geometry("480x680")
        self.win.resizable(False, False)
        self.win.grab_set()

        self._build_ui()
        self._update_range_estimate()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        """Build the rebalance dialog."""
        # Header
        header = ctk.CTkFrame(self.win, fg_color="transparent")
        header.pack(fill="x", padx=20, pady=(15, 5))
        ctk.CTkLabel(header, text="\u270e Rebalance Position",
                     font=ctk.CTkFont(size=18, weight="bold")).pack(side="left")
        ctk.CTkButton(header, text="\u2715", width=28, height=28,
                      fg_color="transparent", hover_color="gray20",
                      command=self.win.destroy).pack(side="right")

        # Position summary
        pair = self.position.pair or "Unknown Pair"
        status = "IN RANGE" if (self.position.position_in_range_pct is not None
                                and 0 <= self.position.position_in_range_pct <= 100) else "OUT OF RANGE"
        ctk.CTkLabel(self.win, text=f"{pair}  \u00b7  {self.position.position_id}  \u00b7  {status}",
                     font=ctk.CTkFont(size=11), text_color="gray70").pack(anchor="w", padx=20)

        # Current range / price panel
        info_frame = ctk.CTkFrame(self.win, fg_color="gray15", corner_radius=8)
        info_frame.pack(fill="x", padx=20, pady=10)
        ctk.CTkLabel(info_frame, text="Current Position",
                     font=ctk.CTkFont(size=11, weight="bold")).pack(anchor="w", padx=10, pady=(8, 2))

        range_txt = "—"
        if self.position.range_low is not None and self.position.range_high is not None:
            range_txt = f"Range: {self.position.range_low:g} \u2013 {self.position.range_high:g}"
        cur_txt = "—"
        if self.position.current_price is not None:
            cur_txt = f"Current price: {self.position.current_price:g}"
        ctk.CTkLabel(info_frame, text=range_txt + "\n" + cur_txt,
                     font=ctk.CTkFont(size=11), text_color="gray70",
                     justify="left").pack(anchor="w", padx=10, pady=(0, 8))

        # Mode toggle: Auto-Recenter vs Custom Range
        self.mode_var = ctk.StringVar(value="auto")
        mode_frame = ctk.CTkFrame(self.win, fg_color="transparent")
        mode_frame.pack(fill="x", padx=20, pady=(5, 5))
        ctk.CTkRadioButton(mode_frame, text="Auto-Recenter (± buffer around current price)",
                           variable=self.mode_var, value="auto",
                           font=ctk.CTkFont(size=11),
                           command=self._on_mode_change).pack(anchor="w")
        ctk.CTkRadioButton(mode_frame, text="Custom Range (enter prices manually)",
                           variable=self.mode_var, value="custom",
                           font=ctk.CTkFont(size=11),
                           command=self._on_mode_change).pack(anchor="w", pady=(4, 0))

        # Auto-recenter buffer input
        self.auto_frame = ctk.CTkFrame(self.win, fg_color="transparent")
        self.auto_frame.pack(fill="x", padx=20, pady=(5, 5))
        ctk.CTkLabel(self.auto_frame, text="Buffer (±%):",
                     font=ctk.CTkFont(size=11)).pack(side="left")
        self.buffer_entry = ctk.CTkEntry(self.auto_frame, width=70, placeholder_text="20")
        self.buffer_entry.insert(0, "20")
        self.buffer_entry.pack(side="left", padx=(5, 0))
        self.buffer_entry.bind("<KeyRelease>", lambda e: self._update_range_estimate())

        # Custom range input
        self.custom_frame = ctk.CTkFrame(self.win, fg_color="transparent")
        ctk.CTkLabel(self.custom_frame, text="New lower price:",
                     font=ctk.CTkFont(size=11)).pack(anchor="w")
        self.lower_entry = ctk.CTkEntry(self.custom_frame, placeholder_text="e.g. 2400")
        self.lower_entry.pack(fill="x", pady=(2, 6))
        self.lower_entry.bind("<KeyRelease>", lambda e: self._update_range_estimate())
        ctk.CTkLabel(self.custom_frame, text="New upper price:",
                     font=ctk.CTkFont(size=11)).pack(anchor="w")
        self.upper_entry = ctk.CTkEntry(self.custom_frame, placeholder_text="e.g. 3600")
        self.upper_entry.pack(fill="x", pady=(2, 6))
        self.upper_entry.bind("<KeyRelease>", lambda e: self._update_range_estimate())
        # hidden initially
        self.custom_frame.pack_forget()

        # New range estimate readout
        self.estimate_label = ctk.CTkLabel(self.win, text="",
                                            font=ctk.CTkFont(size=11), text_color="#51cf94",
                                            justify="left")
        self.estimate_label.pack(anchor="w", padx=20, pady=(5, 5))

        # Slippage
        slip_frame = ctk.CTkFrame(self.win, fg_color="transparent")
        slip_frame.pack(fill="x", padx=20, pady=(5, 5))
        ctk.CTkLabel(slip_frame, text="Slippage tolerance (%):",
                     font=ctk.CTkFont(size=11)).pack(side="left")
        self.slippage_entry = ctk.CTkEntry(slip_frame, width=70, placeholder_text="0")
        self.slippage_entry.insert(0, "0")
        self.slippage_entry.pack(side="left", padx=(5, 0))

        # Warnings
        ctk.CTkLabel(self.win,
                     text=f"\u26a0 This will submit multiple transactions on {self._chain_name}: close, collect, swap (if needed), approve/mint.",
                     font=ctk.CTkFont(size=10), text_color="#ffd43b",
                     wraplength=440, justify="left").pack(anchor="w", padx=20, pady=(10, 2))
        ctk.CTkLabel(self.win,
                     text=f"Ensure your wallet has {self._gas_token} on {self._chain_name} for gas. The key_manager_agent must be running and unlocked.",
                     font=ctk.CTkFont(size=10), text_color="gray60",
                     wraplength=440, justify="left").pack(anchor="w", padx=20, pady=(0, 10))

        # Buttons
        btn_row = ctk.CTkFrame(self.win, fg_color="transparent")
        btn_row.pack(fill="x", padx=20, pady=(5, 15))
        ctk.CTkButton(btn_row, text="Cancel", fg_color="gray30",
                      width=100, height=38, command=self.win.destroy).pack(side="left")
        self.confirm_btn = ctk.CTkButton(btn_row, text="CONFIRM REBALANCE",
                                          fg_color=("#6f42c1", "#5a32a3"),
                                          hover_color=("#5a32a3", "#42288a"),
                                          height=38, command=self._on_confirm)
        self.confirm_btn.pack(side="right", fill="x", expand=True, padx=(10, 0))

    # ------------------------------------------------------------------
    # Mode switching + range estimation
    # ------------------------------------------------------------------

    def _on_mode_change(self):
        """Toggle visibility of the auto vs custom input sections."""
        if self.mode_var.get() == "auto":
            self.custom_frame.pack_forget()
            self.auto_frame.pack(fill="x", padx=20, pady=(5, 5))
        else:
            self.auto_frame.pack_forget()
            self.custom_frame.pack(fill="x", padx=20, pady=(5, 5))
            # Prefill with current range if known
            if self.position.range_low is not None and not self.lower_entry.get():
                self.lower_entry.insert(0, f"{self.position.range_low:g}")
            if self.position.range_high is not None and not self.upper_entry.get():
                self.upper_entry.insert(0, f"{self.position.range_high:g}")
        self._update_range_estimate()

    def _price_to_tick(self, human_price: float) -> int:
        """Convert a human-readable price to a raw tick, snapped to tick_spacing."""
        import math as _math
        raw = human_price * (10 ** (self.decimals1 - self.decimals0))
        tick = int(_math.floor(_math.log(raw, 1.0001)))
        return (tick // self.tick_spacing) * self.tick_spacing

    def _tick_to_price(self, tick: int) -> float:
        """Convert a raw tick back to a human-readable price for display."""
        raw = 1.0001 ** tick
        return raw * (10 ** (self.decimals0 - self.decimals1))

    def _compute_new_range(self):
        """Return (tick_lower, tick_upper) based on the current mode, or (None, None) on error."""
        if self.mode_var.get() == "auto":
            # Auto-Recenter via StrategyEngine.compute_recentered_range
            try:
                buffer_pct = float(self.buffer_entry.get() or "20") / 100.0
            except ValueError:
                return None, None
            if buffer_pct <= 0 or buffer_pct >= 1:
                return None, None
            # Use the shared strategy engine on the parent GUI
            engine = getattr(getattr(self.parent, "gui", self.parent), "lp_engine", None)
            if engine is None:
                # Fallback: compute manually
                if self.position.current_price is None:
                    return None, None
                low_h = self.position.current_price * (1 - buffer_pct)
                high_h = self.position.current_price * (1 + buffer_pct)
                return self._price_to_tick(low_h), self._price_to_tick(high_h)
            lo, hi = engine.compute_recentered_range(
                self.position, buffer_pct=buffer_pct, tick_spacing=self.tick_spacing
            )
            return lo, hi
        # Custom range — user entered human prices; convert to ticks
        try:
            low_h = float(self.lower_entry.get() or "0")
            high_h = float(self.upper_entry.get() or "0")
        except ValueError:
            return None, None
        if low_h <= 0 or high_h <= 0 or low_h >= high_h:
            return None, None
        return self._price_to_tick(low_h), self._price_to_tick(high_h)

    def _update_range_estimate(self):
        """Recompute and show the new range estimate."""
        lo, hi = self._compute_new_range()
        if lo is None or hi is None:
            self.estimate_label.configure(text="New range: —", text_color="gray60")
            return
        lo_h = self._tick_to_price(lo)
        hi_h = self._tick_to_price(hi)
        self.estimate_label.configure(
            text=f"New range: {lo_h:g} \u2013 {hi_h:g}\n(ticks {lo} to {hi})",
            text_color="#51cf94")

    # ------------------------------------------------------------------
    # Confirm + execute
    # ------------------------------------------------------------------

    def _on_confirm(self):
        """Validate inputs, then run the rebalance flow in a background thread."""
        new_lower, new_upper = self._compute_new_range()
        if new_lower is None or new_upper is None:
            _notify(self.parent, "Invalid range — check inputs", error=True)
            return
        if new_lower >= new_upper:
            _notify(self.parent, "Lower tick must be below upper tick", error=True)
            return

        try:
            slippage = float(self.slippage_entry.get() or "0")
        except ValueError:
            _notify(self.parent, "Invalid slippage value", error=True)
            return
        if slippage < 0 or slippage > 50:
            _notify(self.parent, "Slippage must be between 0 and 50%", error=True)
            return

        # Resolve wallet address + vault account name
        if self._is_solana:
            # Solana positions are owned by the vault's Solana account — there is
            # no "wallet entry" widget flow like EVM. Find the vault account whose
            # addresses include a Solana entry (chain/coin containing 'solana' or 'sol').
            account_name = self._resolve_solana_account()
            wallet_address = ""  # not needed for Orca signing (agent resolves by account name)
            if not account_name:
                _notify(self.parent,
                        "Could not find a vault account with a Solana address. "
                        "Add a Solana key to your vault first.", error=True)
                return
        else:
            # EVM flow (Base) — use the LPTab helper which walks the address entry,
            # saved pools, and account list in order
            wallet_address = getattr(self.position, "wallet_address", "")
            account_name = ""
            resolver = getattr(self.parent, "_lp_resolve_wallet_for_position", None)
            if resolver:
                resolved_wallet, resolved_account = resolver(self.position)
                wallet_address = wallet_address or resolved_wallet
                account_name = resolved_account

            # Fallbacks if the LPTab helper is unavailable or returned partial results
            if not account_name and wallet_address:
                account_name = _resolve_account_name(self.parent, wallet_address)
                if not account_name:
                    # _resolve_account_name looks for parent.key_manager; the LPTab
                    # stores it on parent.gui. Try that explicitly.
                    gui = getattr(self.parent, "gui", None)
                    if gui is not None:
                        account_name = _resolve_account_name(gui, wallet_address)

            if not wallet_address:
                _notify(self.parent, "Could not resolve wallet address for this position", error=True)
                return
            if not account_name:
                _notify(self.parent, "Could not resolve vault account for this address", error=True)
                return

        # Final confirmation
        pair = self.position.pair or "this position"
        do_it = messagebox.askyesno(
            "Confirm Rebalance",
            f"Rebalance {pair} ({self.position.position_id}) to a new range?\n\n"
            f"New range: ticks {new_lower} to {new_upper}\n"
            f"Slippage tolerance: {slippage}%\n\n"
            f"This will broadcast multiple transactions on {self._chain_name}. "
            f"Ensure you have {self._gas_token} for gas.",
        )
        if not do_it:
            return

        self.confirm_btn.configure(state="disabled", text="REBALANCING...")
        _notify(self.parent, "Rebalancing position... (multi-TX operation)")

        def _do_rebalance():
            try:
                engine = getattr(getattr(self.parent, "gui", self.parent), "lp_engine", None)
                if engine is None:
                    _safe_after(self.parent, 0, lambda: _notify(
                        self.parent, "LP engine not available", error=True))
                    return
                password = getattr(getattr(self.parent, "gui", self.parent), "current_password", None)
                writer = engine.get_writer(self._writer_key, password)
                if writer is None:
                    _safe_after(self.parent, 0, lambda: _notify(
                        self.parent, f"{self._writer_key.capitalize()} writer not available", error=True))
                    return
                if not writer.is_available():
                    _safe_after(self.parent, 0, lambda: _notify(
                        self.parent, "Agent not running. Start key_manager_agent with --serve.", error=True))
                    return

                tx_hashes = writer.rebalance(RebalanceParams(
                    account=account_name,
                    position_id=self.position.position_id,
                    new_tick_lower=new_lower,
                    new_tick_upper=new_upper,
                    slippage_pct=slippage,
                ))

                if tx_hashes:
                    _safe_after(self.parent, 0, lambda: _notify(
                        self.parent,
                        f"Rebalance submitted: {len(tx_hashes)} TXs. Last: {tx_hashes[-1][:20]}..."))
                    # For solana positions the position_id changes after rebalance
                    # (new NFT mint) — a simple fee refresh won't find the new position;
                    # trigger a full wallet scan instead
                    if self._is_solana:
                        refetch = getattr(self.parent, "_lp_do_fetch", None)
                        if refetch:
                            _safe_after(self.parent, 10000, refetch)
                    else:
                        refresh = getattr(self.parent, "_lp_refresh_position_fees", None)
                        if refresh and wallet_address:
                            _safe_after(self.parent, 8000, lambda: refresh(self.position.position_id, wallet_address))
                    _safe_after(self.parent, 10000, self.win.destroy)
                else:
                    _safe_after(self.parent, 0, lambda: _notify(
                        self.parent, "Rebalance: no transactions submitted", error=True))
            except Exception as e:
                error_msg = str(e)
                print(f"[rebalance] error: {error_msg}")
                if "insufficient funds" in error_msg.lower() or "Insufficient gas" in error_msg:
                    error_msg = (f"Wallet has no {self._gas_token} for gas on {self._chain_name}. "
                                 f"Send {self._gas_token} to your wallet address. (Details: {error_msg})")
                elif "nonce too high" in error_msg.lower():
                    error_msg = f"Transaction rejected (nonce conflict). Wait and retry. (Details: {error_msg})"
                _safe_after(self.parent, 0, lambda: _notify(
                    self.parent, f"Rebalance error: {error_msg}", error=True))
                _safe_after(self.parent, 0, lambda: self.confirm_btn.configure(
                    state="normal", text="CONFIRM REBALANCE"))

        threading.Thread(target=_do_rebalance, daemon=True).start()

    def _resolve_solana_account(self) -> str:
        """Return the vault account name that owns this dialog's Solana position NFT.

        Delegates to the LPTab's NFT-ownership resolver (checks the address entry,
        last-fetched address, then on-chain ownership across vault accounts).
        Falls back to "first account with any Solana key" if the LPTab helper is
        unavailable (e.g. called from a context without the tab's position data).
        """
        resolver = getattr(self.parent, "_lp_resolve_solana_account_for_position", None)
        if resolver:
            result = resolver(self.position)
            if result:
                return result
        # Fallback: first account with any Solana address (legacy behavior)
        key_manager = getattr(getattr(self.parent, "gui", self.parent), "key_manager", None)
        if not key_manager:
            return ""
        accounts = key_manager.address_db.get("accounts", {})
        for acct, data in accounts.items():
            for addr in data.get("addresses", []):
                coin = (addr.get("coin", "") or "").lower()
                chain = (addr.get("chain", "") or "").lower()
                if "solana" in coin or "solana" in chain or coin == "sol" or chain == "sol":
                    return acct
        return ""


# -- Convenience functions for the main GUI to call --

def open_add_liquidity(parent, position, key_manager, price_engine, wallet_address):
    """Open the Add Liquidity dialog."""
    AddLiquidityDialog(parent, position, key_manager, price_engine, wallet_address)


def open_remove_liquidity(parent, position, key_manager, price_engine, wallet_address):
    """Open the Remove Liquidity dialog."""
    RemoveLiquidityDialog(parent, position, key_manager, price_engine, wallet_address)


def open_edit_position(parent, position):
    """Open the Edit Position stub dialog."""
    EditPositionDialog(parent, position)
