"""
Vault Deposit Dialog for ColdStack v5.1.2.

Self-contained dialog for depositing USDC into a Hyperliquid vault
via the CoreWriter system contract on HyperEVM.

Deposit mechanism:
  - EVM transaction to CoreWriter (0x3333333333333333333333333333333333333333)
  - Action ID 2 (vault transfer): (address vault, bool isDeposit, uint64 usd)
  - usd = amount in micro-USD (6 decimals)
  - Source: user's HL1 spot USDC balance
"""

import json
import threading
import time
import urllib.error
import urllib.request
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Optional

import customtkinter as ctk


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

HL1_INFO_URL = "https://api.hyperliquid.xyz/info"
HL1_EXCHANGE_URL = "https://api.hyperliquid.xyz/exchange"
HYPEREVM_RPC = "https://rpc.hyperliquid.xyz/evm"
CORE_WRITER = "0x3333333333333333333333333333333333333333"
HYPE_SYSTEM_ADDRESS = "0x2222222222222222222222222222222222222222"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _agent_call(agent_url: str, cmd: str, **params) -> Dict[str, Any]:
    """Send a command to the key_manager_agent and return the response dict."""
    payload = {"cmd": cmd, **params}
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(agent_url, data=data, headers={
        "Content-Type": "application/json",
    })
    with urllib.request.urlopen(req, timeout=120) as resp:
        result = json.loads(resp.read().decode("utf-8"))
    if result.get("status") != "ok":
        raise RuntimeError(result.get("error", "Unknown agent error"))
    return result["result"]


def _rpc_call(url: str, method: str, params: list) -> Dict[str, Any]:
    """Make a JSON-RPC call and return the parsed response dict."""
    payload = json.dumps({
        "jsonrpc": "2.0",
        "id": 1,
        "method": method,
        "params": params,
    }).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers={
        "Content-Type": "application/json",
        "User-Agent": "ColdStack/5.1",
    })
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _hl1_info_call(body: Dict[str, Any]) -> Dict[str, Any]:
    """POST to the Hyperliquid L1 info API and return parsed JSON."""
    payload = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(HL1_INFO_URL, data=payload, headers={
        "Content-Type": "application/json",
        "User-Agent": "ColdStack/5.1",
    })
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _strip_hl_prefix(vault_address: str) -> str:
    """Strip the 'HL:' prefix used by Hyperliquid platform vault addresses."""
    if vault_address.startswith("HL:"):
        return vault_address[3:]
    return vault_address


def _format_usdc(value: float) -> str:
    """Format a USDC amount for display."""
    s = f"{value:.6f}"
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s


def _build_deposit_calldata(vault_address: str, usd_amount: int) -> str:
    """Build the CoreWriter vault deposit calldata.

    Encoding:
      - byte 1 = 0x01 (version)
      - bytes 2-4 = action ID 2 in big-endian
      - ABI-encoded (address vault, bool isDeposit=true, uint64 usd)
    """
    vault_address = _strip_hl_prefix(vault_address)
    version_byte = b"\x01"
    action_id = b"\x00\x00\x02"

    vault_padded = bytes.fromhex(vault_address[2:].lower().zfill(64))
    is_deposit_padded = (1).to_bytes(32, "big")
    usd_padded = usd_amount.to_bytes(32, "big")

    return "0x" + (version_byte + action_id + vault_padded + is_deposit_padded + usd_padded).hex()


def _wait_for_receipt(rpc_url: str, tx_hash: str, timeout: int = 120, poll_interval: float = 2.0) -> Optional[Dict[str, Any]]:
    """Poll for an EVM transaction receipt."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            result = _rpc_call(rpc_url, "eth_getTransactionReceipt", [tx_hash])
            if result and result.get("result"):
                return result["result"]
        except Exception:
            pass
        time.sleep(poll_interval)
    return None


# ---------------------------------------------------------------------------
# Dialog
# ---------------------------------------------------------------------------

class VaultDepositDialog:
    """Self-contained dialog for depositing USDC into a Hyperliquid vault."""

    def __init__(
        self,
        root: ctk.CTk,
        agent_url: str,
        account_name: str,
        wallet_address: str,
        vault_address: str,
        show_notification,
    ):
        self.root = root
        self.agent_url = agent_url.rstrip("/")
        self.account_name = account_name
        self.wallet_address = wallet_address
        self.vault_address = _strip_hl_prefix(vault_address)
        self.show_notification = show_notification

        self.available_usdc: Optional[float] = None
        self.is_fetching = False

        self.dialog = ctk.CTkToplevel(root)
        self.dialog.title("Deposit to Vault")
        self.dialog.geometry("420x420")
        self.dialog.transient(root)
        self.dialog.grab_set()
        self._center_dialog()

        self._build_ui()
        self._fetch_balance()

    def _center_dialog(self):
        """Center the dialog over the parent window."""
        self.dialog.update_idletasks()
        parent_x = self.root.winfo_x()
        parent_y = self.root.winfo_y()
        parent_w = self.root.winfo_width()
        parent_h = self.root.winfo_height()
        dialog_w = self.dialog.winfo_width()
        dialog_h = self.dialog.winfo_height()
        x = parent_x + (parent_w - dialog_w) // 2
        y = parent_y + (parent_h - dialog_h) // 2
        self.dialog.geometry(f"+{x}+{y}")

    def _build_ui(self):
        """Create the dialog widgets."""
        self.dialog.configure(fg_color="#1a1a1a")

        # Title
        ctk.CTkLabel(
            self.dialog,
            text="Deposit to Vault",
            font=ctk.CTkFont(size=20, weight="bold"),
            text_color="white",
        ).pack(pady=(20, 5))

        # Vault address display
        ctk.CTkLabel(
            self.dialog,
            text=f"Vault: {self.vault_address[:10]}...{self.vault_address[-8:]}",
            font=ctk.CTkFont(size=11),
            text_color="gray70",
        ).pack(pady=(0, 10))

        # HL1 USDC balance
        self.balance_label = ctk.CTkLabel(
            self.dialog,
            text="HL1 Spot USDC: loading...",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color="#20c997",
        )
        self.balance_label.pack(pady=(5, 15))

        # Amount entry + MAX label
        amount_frame = ctk.CTkFrame(self.dialog, fg_color="transparent")
        amount_frame.pack(fill="x", padx=30, pady=10)
        ctk.CTkLabel(
            amount_frame,
            text="Amount:",
            font=ctk.CTkFont(size=12),
            width=70,
        ).pack(side="left")
        self.amount_entry = ctk.CTkEntry(
            amount_frame,
            width=140,
            placeholder_text="0.0",
        )
        self.amount_entry.pack(side="left", padx=10)
        self.max_label = ctk.CTkLabel(
            amount_frame,
            text="MAX",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color="#20c997",
        )
        self.max_label.pack(side="left")
        self.max_label.bind("<Button-1>", lambda e: self._set_max_amount())
        self.max_label.bind("<Enter>", lambda e: self.max_label.configure(cursor="hand2"))
        self.max_label.bind("<Leave>", lambda e: self.max_label.configure(cursor=""))

        # Deposit button
        self.deposit_btn = ctk.CTkButton(
            self.dialog,
            text="Deposit USDC",
            width=360,
            height=40,
            font=ctk.CTkFont(size=14, weight="bold"),
            fg_color="#20c997",
            hover_color="#1aa179",
            text_color="black",
            command=self._on_deposit,
        )
        self.deposit_btn.pack(pady=(20, 10), padx=30)

        # Status label
        self.status_label = ctk.CTkLabel(
            self.dialog,
            text="",
            font=ctk.CTkFont(size=11),
            text_color="gray60",
            wraplength=380,
        )
        self.status_label.pack(pady=(5, 15))

        # Warning note
        ctk.CTkLabel(
            self.dialog,
            text=(
                "Deposits draw from your HL1 Spot USDC balance. "
                "Vault transfers via CoreWriter may take a few seconds to settle."
            ),
            font=ctk.CTkFont(size=10),
            text_color="gray60",
            wraplength=380,
        ).pack(pady=(0, 10))

    def _set_status(self, text: str, color: str = "gray60"):
        """Update the status label on the UI thread."""
        self.dialog.after(0, lambda: self.status_label.configure(text=text, text_color=color))

    def _set_max_amount(self):
        """Fill the amount entry with the max available USDC."""
        if self.available_usdc is not None:
            self.amount_entry.delete(0, "end")
            self.amount_entry.insert(0, _format_usdc(self.available_usdc))

    def _fetch_balance(self):
        """Query HL1 spot USDC balance in a background thread."""
        if self.is_fetching:
            return
        self.is_fetching = True

        def _do_fetch():
            try:
                data = _hl1_info_call({
                    "type": "spotClearinghouseState",
                    "user": self.wallet_address,
                })
                balances = data.get("balances", [])
                for b in balances:
                    if b.get("coin") == "USDC":
                        total = float(b.get("total", "0"))
                        hold = float(b.get("hold", "0"))
                        self.available_usdc = max(0.0, total - hold)
                        break
                else:
                    self.available_usdc = 0.0

                self.dialog.after(
                    0,
                    lambda: self.balance_label.configure(
                        text=f"HL1 Spot USDC: {_format_usdc(self.available_usdc)} USDC",
                        text_color="#20c997",
                    ),
                )
            except Exception as e:
                self.available_usdc = None
                self.dialog.after(
                    0,
                    lambda: self.balance_label.configure(
                        text=f"HL1 Spot USDC: unavailable ({e})",
                        text_color="#ff6b6b",
                    ),
                )
            finally:
                self.is_fetching = False

        threading.Thread(target=_do_fetch, daemon=True).start()

    def _on_deposit(self):
        """Validate inputs and start the deposit in a background thread."""
        amount_str = self.amount_entry.get().strip()
        if not amount_str:
            self._set_status("Enter an amount", "#ff6b6b")
            return
        try:
            amount_dec = Decimal(amount_str)
        except InvalidOperation:
            self._set_status("Invalid amount", "#ff6b6b")
            return
        if amount_dec <= 0:
            self._set_status("Amount must be greater than zero", "#ff6b6b")
            return
        if self.available_usdc is not None and float(amount_dec) > self.available_usdc:
            self._set_status("Amount exceeds available HL1 Spot USDC", "#ff6b6b")
            return

        self.deposit_btn.configure(state="disabled")
        self._set_status("Submitting deposit...", "gray60")

        threading.Thread(
            target=self._do_deposit,
            args=(str(amount_dec),),
            daemon=True,
        ).start()

    def _do_deposit(self, amount_str: str):
        """Execute the vault deposit via the agent and wait for receipt."""
        try:
            usd_amount = int(Decimal(amount_str) * Decimal("1_000_000"))
            calldata = _build_deposit_calldata(self.vault_address, usd_amount)

            result = _agent_call(
                self.agent_url,
                "broadcast_tx",
                account=self.account_name,
                to=CORE_WRITER,
                data=calldata,
                value="0",
                chain_id=999,
                rpc=HYPEREVM_RPC,
            )

            tx_hash = result.get("tx_hash", "")
            if not tx_hash:
                raise RuntimeError("No transaction hash returned")

            self._set_status(f"Waiting for receipt: {tx_hash[:20]}...", "gray60")
            receipt = _wait_for_receipt(HYPEREVM_RPC, tx_hash)

            if receipt and receipt.get("status") == "0x1":
                self._set_status(f"Deposit complete. TX: {tx_hash[:20]}...", "#51cf94")
                self.dialog.after(
                    0,
                    lambda: self.show_notification(
                        f"Vault deposit submitted: {tx_hash[:20]}..."
                    ),
                )
            elif receipt:
                raise RuntimeError(f"Transaction failed on-chain (status={receipt.get('status')})")
            else:
                self._set_status(f"Submitted but receipt not found. TX: {tx_hash[:20]}...", "#ffa94d")
                self.dialog.after(
                    0,
                    lambda: self.show_notification(
                        f"Vault deposit submitted (receipt pending): {tx_hash[:20]}..."
                    ),
                )

            self._fetch_balance()
        except Exception as e:
            self._set_status(f"Deposit failed: {e}", "#ff6b6b")
            self.dialog.after(
                0,
                lambda: self.show_notification(f"Vault deposit failed: {e}", error=True),
            )
        finally:
            self.dialog.after(0, lambda: self.deposit_btn.configure(state="normal"))

    def close(self):
        """Close the dialog."""
        try:
            self.dialog.destroy()
        except Exception:
            pass
