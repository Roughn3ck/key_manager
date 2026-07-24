"""
EVM ↔ HL1 Transfer Dialog for ColdStack.

Self-contained dialog module for transferring assets between HyperEVM and
HyperCore (HL1) Spot balances. Imported by gui_main_v5.py on demand.

Transfer methods:
- EVM → HL1: ERC-20 transfer to system address (USDC/UBTC) or native send (HYPE)
- HL1 → EVM: spotSend action via Hyperliquid L1 API (EIP-712 signed)
"""

import json
import threading
import time
import urllib.error
import urllib.request
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional

import customtkinter as ctk


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

HYPEREVM_RPC = "https://rpc.hyperliquid.xyz/evm"
HL1_API = "https://api.hyperliquid.xyz/info"
HL1_EXCHANGE_API = "https://api.hyperliquid.xyz/exchange"
HYPE_SYSTEM_ADDRESS = "0x2222222222222222222222222222222222222222"
USDC_EVM = "0xb88339CB7199b77E23DB6E890353E22632Ba630f"
UBTC_EVM = "0x9FDBdA0A5e284c32744D2f17Ee5c74B284993463"
EIP712_CHAIN_ID = 0x66eee  # 421483

TOKEN_DECIMALS = {
    "HYPE": 18,
    "USDC": 6,
    "UBTC": 8,
}

# WHYPE is the ERC-20 wrapped HYPE on HyperEVM.
# Users hold HYPE as WHYPE on HyperEVM; native gas HYPE is just for gas.
# For balance display and transfers, "HYPE" on the EVM side maps to WHYPE.
WHYPE_EVM = "0x5555555555555555555555555555555555555555"

# ERC-20 withdraw selector (WHYPE.withdraw(uint256) — unwrap WHYPE to native HYPE)
SELECTOR_WITHDRAW = "0x2e1a7d4d"

EVM_TOKEN_ADDRESSES = {
    "HYPE": WHYPE_EVM,  # HYPE on HyperEVM is held as WHYPE (ERC-20)
    "USDC": USDC_EVM,
    "UBTC": UBTC_EVM,
}

ASSET_OPTIONS = ["HYPE", "USDC", "UBTC"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _rpc_call(url: str, method: str, params: list) -> dict:
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


def _hl1_info_call(body: dict) -> dict:
    """POST to the Hyperliquid L1 info API and return parsed JSON."""
    payload = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(HL1_API, data=payload, headers={
        "Content-Type": "application/json",
        "User-Agent": "ColdStack/5.1",
    })
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _agent_call(agent_url: str, cmd: str, **params) -> dict:
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


def _pad_address(address: str) -> str:
    """Pad a 20-byte EVM address to a 32-byte ABI word (with 0x prefix)."""
    clean = address.lower()
    if clean.startswith("0x"):
        clean = clean[2:]
    return "0x" + ("0" * 24) + clean


def _pad_uint256(value: int) -> str:
    """Encode an unsigned integer as a 32-byte hex word (with 0x prefix)."""
    return "0x" + format(value, "064x")


def _to_wei(amount: str, decimals: int) -> int:
    """Convert a human-readable amount string to raw integer units."""
    return int(Decimal(amount) * (10 ** decimals))


def _format_amount(value: float, decimals: int) -> str:
    """Format a float amount for display, trimming trailing zeros."""
    s = f"{value:.{decimals}f}"
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s


def _lookup_token_index(symbol: str) -> Optional[int]:
    """Query Hyperliquid spotMeta and return the token index for a symbol."""
    try:
        meta = _hl1_info_call({"type": "spotMeta"})
        tokens = meta.get("tokens", [])
        for group in tokens:
            for token in group.get("tokens", []):
                if token.get("name") == symbol:
                    return token.get("index")
        # Fallback: universe mapping
        universe = meta.get("universe", [])
        for entry in universe:
            if entry.get("name") == symbol:
                token_ids = entry.get("tokens", [])
                if token_ids:
                    return token_ids[0]
    except Exception:
        pass
    return None


def _system_address_for_token(symbol: str) -> Optional[str]:
    """Compute the Hyperliquid system address for a spot token."""
    index = _lookup_token_index(symbol)
    if index is None:
        return None
    # First byte 0x20, remaining 19 bytes big-endian of the index.
    return "0x" + (b"\x20" + index.to_bytes(19, "big")).hex()


# ---------------------------------------------------------------------------
# Dialog
# ---------------------------------------------------------------------------

class EVMTransferDialog:
    """Self-contained dialog for transferring assets between HyperEVM and HL1."""

    def __init__(
        self,
        root: ctk.CTk,
        agent_url: str,
        account_name: str,
        wallet_address: str,
        show_notification,
    ):
        self.root = root
        self.agent_url = agent_url.rstrip("/")
        self.account_name = account_name
        self.wallet_address = wallet_address
        self.show_notification = show_notification

        self.direction = "evm_to_hl1"  # or "hl1_to_evm"
        self.selected_asset = "HYPE"
        self.balances: Dict[str, Dict[str, Any]] = {}
        self.available_balances: Dict[str, float] = {}
        self.is_fetching = False

        self.dialog = ctk.CTkToplevel(root)
        self.dialog.title("EVM Transfer")
        self.dialog.geometry("420x420")
        self.dialog.transient(root)
        self.dialog.grab_set()
        self._center_dialog()

        self._build_ui()
        self._fetch_balances()

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
            text="EVM Transfer",
            font=ctk.CTkFont(size=20, weight="bold"),
            text_color="white",
        ).pack(pady=(20, 5))

        # Subtitle
        ctk.CTkLabel(
            self.dialog,
            text="Transfer assets between your HyperEVM and HyperCore Spot balances.",
            font=ctk.CTkFont(size=11),
            text_color="gray70",
            wraplength=380,
        ).pack(pady=(0, 15))

        # Direction toggle
        self.direction_btn = ctk.CTkButton(
            self.dialog,
            text="EVM \u2194 Spot",
            width=140,
            height=32,
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color=("gray30", "gray25"),
            hover_color=("gray40", "gray35"),
            command=self._toggle_direction,
        )
        self.direction_btn.pack(pady=(0, 15))

        # Source / destination labels
        self.path_label = ctk.CTkLabel(
            self.dialog,
            text="From HyperEVM  \u2192  To HyperCore (HL1)",
            font=ctk.CTkFont(size=12),
            text_color="#20c997",
        )
        self.path_label.pack(pady=(0, 15))

        # Asset dropdown
        asset_frame = ctk.CTkFrame(self.dialog, fg_color="transparent")
        asset_frame.pack(fill="x", padx=30, pady=5)
        ctk.CTkLabel(
            asset_frame,
            text="Asset:",
            font=ctk.CTkFont(size=12),
            width=70,
        ).pack(side="left")
        self.asset_menu = ctk.CTkOptionMenu(
            asset_frame,
            values=ASSET_OPTIONS,
            command=self._on_asset_change,
            width=120,
        )
        self.asset_menu.set("HYPE")
        self.asset_menu.pack(side="left", padx=10)

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
            text="MAX: --",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color="#20c997",
        )
        self.max_label.pack(side="left")
        self.max_label.bind("<Button-1>", lambda e: self._set_max_amount())
        self.max_label.bind("<Enter>", lambda e: self.max_label.configure(cursor="hand2"))
        self.max_label.bind("<Leave>", lambda e: self.max_label.configure(cursor=""))

        # Transfer button
        self.transfer_btn = ctk.CTkButton(
            self.dialog,
            text="Transfer",
            width=360,
            height=40,
            font=ctk.CTkFont(size=14, weight="bold"),
            fg_color="#20c997",
            hover_color="#1ba87e",
            text_color="black",
            command=self._on_transfer,
        )
        self.transfer_btn.pack(pady=(20, 10), padx=30)

        # Status label
        self.status_label = ctk.CTkLabel(
            self.dialog,
            text="",
            font=ctk.CTkFont(size=11),
            text_color="gray60",
            wraplength=380,
        )
        self.status_label.pack(pady=(5, 15))

    def _toggle_direction(self):
        """Swap transfer direction and refresh the available max."""
        if self.direction == "evm_to_hl1":
            self.direction = "hl1_to_evm"
            self.path_label.configure(
                text="From HyperCore (HL1)  \u2192  To HyperEVM"
            )
        else:
            self.direction = "evm_to_hl1"
            self.path_label.configure(
                text="From HyperEVM  \u2192  To HyperCore (HL1)"
            )
        self._update_max_label()

    def _on_asset_change(self, value: str):
        """Handle asset selection change."""
        self.selected_asset = value
        self._update_max_label()

    def _set_max_amount(self):
        """Fill the amount entry with the max available balance."""
        max_val = self._get_max_for_selection()
        if max_val is not None:
            decimals = TOKEN_DECIMALS.get(self.selected_asset, 18)
            self.amount_entry.delete(0, "end")
            self.amount_entry.insert(0, _format_amount(max_val, decimals))

    def _get_max_for_selection(self) -> Optional[float]:
        """Return the available balance for the current direction + asset."""
        if self.direction == "evm_to_hl1":
            return self.available_balances.get(f"evm_{self.selected_asset}")
        return self.available_balances.get(f"hl1_{self.selected_asset}")

    def _update_max_label(self):
        """Update the MAX label from fetched balances."""
        max_val = self._get_max_for_selection()
        if max_val is None:
            self.max_label.configure(text="MAX: --")
        else:
            decimals = TOKEN_DECIMALS.get(self.selected_asset, 18)
            self.max_label.configure(
                text=f"MAX: {_format_amount(max_val, decimals)} {self.selected_asset}"
            )

    def _set_status(self, text: str, color: str = "gray60"):
        """Update the status label on the UI thread."""
        self.dialog.after(0, lambda: self.status_label.configure(text=text, text_color=color))

    def _fetch_balances(self):
        """Query EVM and HL1 balances in a background thread."""
        if self.is_fetching:
            return
        self.is_fetching = True
        self._set_status("Fetching balances...", "gray60")

        def _do_fetch():
            try:
                balances = {}
                # EVM balances — all assets are ERC-20 tokens on HyperEVM.
                # HYPE on HyperEVM is held as WHYPE (wrapped), not native gas.
                # Native gas HYPE is only used for paying gas fees.
                for symbol, contract in EVM_TOKEN_ADDRESSES.items():
                    try:
                        padded = _pad_address(self.wallet_address)[2:]
                        call_data = "0x70a08231" + padded
                        result = _rpc_call(
                            HYPEREVM_RPC,
                            "eth_call",
                            [{"to": contract, "data": call_data}, "latest"],
                        )
                        if result and "result" in result:
                            raw = int(result["result"], 16)
                            balances[f"evm_{symbol}"] = raw / (10 ** TOKEN_DECIMALS[symbol])
                    except Exception as e:
                        balances[f"evm_{symbol}"] = None
                        print(f"[evm_transfer] EVM {symbol} balance error: {e}")

                # HL1 spot balances
                try:
                    state = _hl1_info_call({
                        "type": "spotClearinghouseState",
                        "user": self.wallet_address,
                    })
                    for b in state.get("balances", []):
                        coin = b.get("coin", "")
                        total = b.get("total", "0")
                        hold = b.get("hold", "0")
                        # Store all HL1 spot balances; the asset dropdown
                        # shows supported ones (HYPE, USDC, UBTC) and the
                        # MAX label looks up by coin name.
                        try:
                            available = float(total) - float(hold)
                            if available > 0:
                                balances[f"hl1_{coin}"] = available
                        except (ValueError, TypeError):
                            pass
                except Exception as e:
                    print(f"[evm_transfer] HL1 balance error: {e}")

                self.balances = balances
                self.available_balances = {
                    k: v for k, v in balances.items()
                    if v is not None and v > 0
                }
                self.dialog.after(0, self._update_max_label)
                self._set_status("Balances loaded", "gray60")
            except Exception as e:
                self._set_status(f"Balance fetch failed: {e}", "#ff6b6b")
            finally:
                self.is_fetching = False

        threading.Thread(target=_do_fetch, daemon=True).start()

    def _on_transfer(self):
        """Validate inputs and start the transfer in a background thread."""
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

        max_val = self._get_max_for_selection()
        if max_val is not None and float(amount_dec) > max_val:
            self._set_status("Amount exceeds available balance", "#ff6b6b")
            return

        self.transfer_btn.configure(state="disabled")
        self._set_status("Submitting...", "gray60")

        if self.direction == "evm_to_hl1":
            threading.Thread(
                target=self._do_evm_to_hl1,
                args=(self.selected_asset, amount_str),
                daemon=True,
            ).start()
        else:
            threading.Thread(
                target=self._do_hl1_to_evm,
                args=(self.selected_asset, amount_str),
                daemon=True,
            ).start()

    def _wait_for_tx(self, tx_hash: str, timeout: int = 120):
        """Wait for a transaction to be mined by polling for its receipt."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                result = _rpc_call(HYPEREVM_RPC, "eth_getTransactionReceipt", [tx_hash])
                if result and isinstance(result, dict) and result.get("status"):
                    if result["status"] == "0x1":
                        return result
                    elif result["status"] == "0x0":
                        raise RuntimeError(f"Transaction {tx_hash[:20]}... reverted")
            except RuntimeError:
                raise
            except Exception:
                pass
            time.sleep(2.0)
        return None

    def _do_evm_to_hl1(self, asset: str, amount_str: str):
        """Execute an EVM → HL1 transfer via the agent.

        For HYPE: unwrap WHYPE → native HYPE, then send to 0x222...2222.
        For USDC/UBTC: ERC-20 transfer to the token's system address.
        """
        try:
            if asset == "HYPE":
                # HYPE on HyperEVM is held as WHYPE (ERC-20).
                # 1. Unwrap WHYPE → native HYPE (WHYPE.withdraw(amount))
                # 2. Send native HYPE to 0x222...2222 (bridge to HL1)
                amount_wei = _to_wei(amount_str, 18)

                # Step 1: Unwrap WHYPE
                withdraw_data = "0x" + SELECTOR_WITHDRAW + _pad_uint256(amount_wei)[2:]
                unwrap_result = _agent_call(
                    self.agent_url,
                    "broadcast_tx",
                    account=self.account_name,
                    to=WHYPE_EVM,
                    data=withdraw_data,
                    value="0x0",
                    chain_id=999,
                    rpc=HYPEREVM_RPC,
                )
                unwrap_tx = unwrap_result.get("tx_hash", "")
                self._set_status(f"WHYPE unwrapped. Bridging to HL1...", "gray60")

                # Wait for unwrap tx to be mined before sending native HYPE
                self._wait_for_tx(unwrap_tx, timeout=60)

                # Step 2: Send native HYPE to the bridge address
                result = _agent_call(
                    self.agent_url,
                    "broadcast_tx",
                    account=self.account_name,
                    to=HYPE_SYSTEM_ADDRESS,
                    data="0x",
                    value=hex(amount_wei),
                    chain_id=999,
                    rpc=HYPEREVM_RPC,
                )
            else:
                token_address = EVM_TOKEN_ADDRESSES.get(asset)
                if not token_address:
                    raise RuntimeError(f"Unsupported asset: {asset}")
                system_addr = _system_address_for_token(asset)
                if not system_addr:
                    raise RuntimeError(f"Could not resolve HL1 system address for {asset}")
                amount_raw = _to_wei(amount_str, TOKEN_DECIMALS[asset])
                data = (
                    "0xa9059cbb"
                    + _pad_address(system_addr)[2:]
                    + _pad_uint256(amount_raw)[2:]
                )
                result = _agent_call(
                    self.agent_url,
                    "broadcast_tx",
                    account=self.account_name,
                    to=token_address,
                    data=data,
                    value="0x0",
                    chain_id=999,
                    rpc=HYPEREVM_RPC,
                )

            tx_hash = result.get("tx_hash", "")
            self._set_status(f"Transfer complete. TX: {tx_hash[:20]}...", "#51cf94")
            self.dialog.after(
                0,
                lambda: self.show_notification(
                    f"EVM\u2192HL1 {asset} transfer submitted: {tx_hash[:20]}..."
                ),
            )
            self._fetch_balances()
        except Exception as e:
            self._set_status(f"Transfer failed: {e}", "#ff6b6b")
            self.dialog.after(
                0,
                lambda: self.show_notification(f"EVM\u2192HL1 transfer failed: {e}", error=True),
            )
        finally:
            self.dialog.after(0, lambda: self.transfer_btn.configure(state="normal"))

    def _do_hl1_to_evm(self, asset: str, amount_str: str):
        """Execute an HL1 → EVM spotSend transfer via the agent + exchange API."""
        try:
            timestamp_ms = int(time.time() * 1000)
            action = {
                "destination": self.wallet_address.lower(),
                "amount": amount_str,
                "token": asset,
                "time": timestamp_ms,
                "type": "spotSend",
                "signatureChainId": "0x66eee",
                "hyperliquidChain": "Mainnet",
            }
            sign_message = {
                "hyperliquidChain": "Mainnet",
                "destination": self.wallet_address.lower(),
                "token": asset,
                "amount": amount_str,
                "time": timestamp_ms,
            }
            domain = {
                "name": "HyperliquidSignTransaction",
                "version": "1",
                "chainId": EIP712_CHAIN_ID,
                "verifyingContract": "0x0000000000000000000000000000000000000000",
            }
            types = {
                "SpotSend": [
                    {"name": "hyperliquidChain", "type": "string"},
                    {"name": "destination", "type": "string"},
                    {"name": "token", "type": "string"},
                    {"name": "amount", "type": "string"},
                    {"name": "time", "type": "uint64"},
                ],
                "EIP712Domain": [
                    {"name": "name", "type": "string"},
                    {"name": "version", "type": "string"},
                    {"name": "chainId", "type": "uint256"},
                    {"name": "verifyingContract", "type": "address"},
                ],
            }

            sig = _agent_call(
                self.agent_url,
                "sign_typed_data",
                account=self.account_name,
                domain=domain,
                types=types,
                message=sign_message,
                chain="EVM",
            )

            body = {
                "action": action,
                "nonce": timestamp_ms,
                "signature": {
                    "r": sig["r"],
                    "s": sig["s"],
                    "v": sig["v"],
                },
            }

            payload = json.dumps(body).encode("utf-8")
            req = urllib.request.Request(HL1_EXCHANGE_API, data=payload, headers={
                "Content-Type": "application/json",
                "User-Agent": "ColdStack/5.1",
            })
            with urllib.request.urlopen(req, timeout=60) as resp:
                exchange_result = json.loads(resp.read().decode("utf-8"))

            status = exchange_result.get("status", "unknown")
            if exchange_result.get("status") != "ok":
                raise RuntimeError(f"Exchange error: {exchange_result}")

            self._set_status(f"Transfer complete. Status: {status}", "#51cf94")
            self.dialog.after(
                0,
                lambda: self.show_notification(
                    f"HL1\u2192EVM {asset} transfer submitted: status={status}"
                ),
            )
            self._fetch_balances()
        except Exception as e:
            self._set_status(f"Transfer failed: {e}", "#ff6b6b")
            self.dialog.after(
                0,
                lambda: self.show_notification(f"HL1\u2192EVM transfer failed: {e}", error=True),
            )
        finally:
            self.dialog.after(0, lambda: self.transfer_btn.configure(state="normal"))

    def close(self):
        """Close the dialog."""
        try:
            self.dialog.destroy()
        except Exception:
            pass
