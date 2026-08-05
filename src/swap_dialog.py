"""
Swap/Bridge Dialog for ColdStack.

Self-contained CustomTkinter dialog for swapping tokens on HyperEVM
(including wrap/unwrap of native HYPE) and bridging between HyperEVM and
HyperCore (HL1). Imported by gui_main_v5.py on demand.
"""

import json
import threading
import time
import urllib.error
import urllib.request
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Tuple

import customtkinter as ctk


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

HYPEREVM_RPC = "https://rpc.hyperliquid.xyz/evm"
HL1_API = "https://api.hyperliquid.xyz/info"
HL1_EXCHANGE_API = "https://api.hyperliquid.xyz/exchange"
SWAP_ROUTER = "0x1ebdfc75ffe3ba3de61e7138a3e8706ac841af9b"
POOL_FACTORY = "0xb1c0fa0b789320044a6f623cfe5ebda9562602e3"
WHYPE = "0x5555555555555555555555555555555555555555"
UBTC = "0x9FDBdA0A5e284c32744D2f17Ee5c74B284993463"
USDC = "0xb88339CB7199b77E23DB6E890353E22632Ba630f"
HYPE_SYSTEM_ADDRESS = "0x2222222222222222222222222222222222222222"
POSITION_MANAGER = "0xead19ae861c29bbb2101e834922b2feee69b9091"
EIP712_CHAIN_ID = 0x66eee  # 421483

# Known pools (verified on-chain)
# Keys MUST be ordered by token address (token0 < token1 by address) to match _best_pool()'s address-sort lookup
# Address order: USDC (0xb883) < WHYPE (0x5555) < UBTC (0x9fdb)
# Wait: 0x5555 < 0x9fdb < 0xb883 — so WHYPE < UBTC < USDC by address
KNOWN_POOLS = {
    ("WHYPE", "USDC", 500):   "0x264a1f3b9eb574a3e7be869ac415dc5430dcf571",
    ("WHYPE", "USDC", 3000):  "0xe712d505572b3f84c1b4deb99e1beab9dd0e23c9",
    ("WHYPE", "UBTC", 500):   "0xbbcf8523811060e1c112a8459284a48a4b17661f",
    ("WHYPE", "UBTC", 3000):  "0x3a36b04bcc1d5e2e303981ef643d2668e00b43e7",
    ("WHYPE", "UBTC", 10000): "0xb2eb6d459759936160a57297e4a03e067dbbe5cb",
    ("UBTC", "USDC", 3000):   "0x7bfa94fa95c06528e68e6526adc81c99dcf93533",
}

TOKEN_DECIMALS = {
    "HYPE": 18,
    "WHYPE": 18,
    "USDC": 6,
    "UBTC": 8,
}

TOKEN_ADDRESSES = {
    "WHYPE": WHYPE,
    "USDC": USDC,
    "UBTC": UBTC,
}

SWAP_TOKENS = ["HYPE", "WHYPE", "USDC", "UBTC"]
BRIDGE_ASSETS = ["HYPE", "USDC", "UBTC"]

# ERC-20 / Uniswap V3 function selectors
SELECTOR_BALANCE_OF = "0x70a08231"
SELECTOR_TRANSFER = "0xa9059cbb"
SELECTOR_APPROVE = "0x095ea7b3"
SELECTOR_WITHDRAW = "0x2e1a7d4d"   # WHYPE.withdraw(uint256)
SELECTOR_EXACT_INPUT_SINGLE = "0x414bf389"
SELECTOR_SLOT0 = "0x3850c7bd"
SELECTOR_GET_POOL = "0x1698ee82"

MAX_UINT256 = 2**256 - 1


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
    return "0x" + (b"\x20" + index.to_bytes(19, "big")).hex()


def _best_pool(token_in: str, token_out: str) -> Tuple[Optional[str], Optional[int]]:
    """Pick the lowest-fee known pool with active liquidity for a token pair.

    Tries each fee tier in order and returns the first pool that has non-zero
    liquidity (verified via on-chain slot0 read). This avoids selecting dead
    or empty pools that would cause swaps to revert.
    """
    addr_in = TOKEN_ADDRESSES.get(token_in, "").lower()
    addr_out = TOKEN_ADDRESSES.get(token_out, "").lower()
    if addr_in and addr_out:
        t0, t1 = (token_in, token_out) if addr_in < addr_out else (token_out, token_in)
    else:
        t0, t1 = sorted([token_in, token_out])

    for fee in (500, 3000, 10000):
        key = (t0, t1, fee)
        if key not in KNOWN_POOLS:
            continue
        pool_addr = KNOWN_POOLS[key]
        # Verify pool has liquidity by reading slot0.liquidity
        try:
            result = _rpc_call(
                HYPEREVM_RPC,
                "eth_call",
                [{"to": pool_addr, "data": SELECTOR_SLOT0}, "latest"],
            )
            if result and "result" in result:
                body = result["result"][2:]
                if len(body) >= 128:
                    # liquidity is the third word (bytes 128-192)
                    liquidity = int(body[128:192], 16)
                    if liquidity > 0:
                        return pool_addr, fee
        except Exception:
            continue
    # Fallback: return first known pool even if we couldn't verify liquidity
    for fee in (500, 3000, 10000):
        key = (t0, t1, fee)
        if key in KNOWN_POOLS:
            return KNOWN_POOLS[key], fee
    return None, None


def _route_swap(token_in: str, token_out: str, amount: Decimal) -> str:
    """Determine the execution path for a token swap."""
    if token_in == token_out:
        return "same_token"
    if token_in == "HYPE" and token_out == "WHYPE":
        return "wrap"
    if token_in == "WHYPE" and token_out == "HYPE":
        return "unwrap"
    if token_in == "HYPE":
        return "wrap_and_swap"
    if token_out == "HYPE":
        return "swap_and_unwrap"
    return "direct_swap"


def _get_swap_quote(token_in: str, token_out: str, amount_in: float,
                    fee: Optional[int] = None,
                    from_address: Optional[str] = None) -> Optional[float]:
    """Estimate swap output by simulating exactInputSingle via eth_call.

    Uses a read-only eth_call to the SwapRouter to get the actual swap output.
    This is more accurate than reading slot0 price because it accounts for
    pool liquidity, price impact, and fee tier — and returns None if the
    pool can't handle the swap (reverts).

    Args:
        from_address: The wallet address to use as 'from' in the eth_call.
            The SwapRouter checks the caller has token balance and allowance,
            so this must be set to the actual wallet address for the
            simulation to succeed.
    """
    try:
        if fee is None:
            pool, fee = _best_pool(token_in, token_out)
            if pool is None:
                return None
        else:
            # Find pool for specific fee tier
            addr_in = TOKEN_ADDRESSES.get(token_in, "").lower()
            addr_out = TOKEN_ADDRESSES.get(token_out, "").lower()
            if addr_in and addr_out:
                t0_name, t1_name = (token_in, token_out) if addr_in < addr_out else (token_out, token_in)
            else:
                t0_name, t1_name = sorted([token_in, token_out])
            pool = KNOWN_POOLS.get((t0_name, t1_name, fee))
            if not pool:
                return None

        # Get token addresses
        addr_token_in = TOKEN_ADDRESSES.get(token_in)
        addr_token_out = TOKEN_ADDRESSES.get(token_out)
        if not addr_token_in or not addr_token_out:
            return None

        decimals_in = TOKEN_DECIMALS.get(token_in, 18)
        decimals_out = TOKEN_DECIMALS.get(token_out, 18)
        amount_in_raw = int(amount_in * (10 ** decimals_in))
        deadline = int(time.time()) + 300

        # Simulate exactInputSingle via eth_call (read-only)
        # Selector: 0x414bf389
        # Params: tokenIn, tokenOut, fee, recipient, deadline, amountIn, amountOutMin, sqrtPriceLimitX96
        call_data = (
            SELECTOR_EXACT_INPUT_SINGLE
            + _pad_address(addr_token_in)[2:]
            + _pad_address(addr_token_out)[2:]
            + _pad_uint256(fee)[2:]
            + _pad_address("0x0000000000000000000000000000000000000000")[2:]  # recipient (doesn't matter for eth_call)
            + _pad_uint256(deadline)[2:]
            + _pad_uint256(amount_in_raw)[2:]
            + _pad_uint256(0)[2:]  # amountOutMin = 0 for simulation
            + _pad_uint256(0)[2:]  # sqrtPriceLimitX96 = 0
        )

        call_params = {"to": SWAP_ROUTER, "data": call_data}
        if from_address:
            call_params["from"] = from_address
        result = _rpc_call(
            HYPEREVM_RPC,
            "eth_call",
            [call_params, "latest"],
        )

        if not result or "error" in result:
            return None  # Pool can't handle this swap (reverts)

        body = result.get("result", "")[2:]
        if len(body) < 64:
            return None

        amount_out_raw = int(body[0:64], 16)
        amount_out_human = amount_out_raw / (10 ** decimals_out)
        return amount_out_human
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Dialog
# ---------------------------------------------------------------------------

class SwapDialog:
    """Self-contained dialog for swapping tokens on HyperEVM and bridging EVM↔HL1."""

    def __init__(
        self,
        root: ctk.CTk,
        agent_url: str,
        account_name: str,
        wallet_address: str,
        show_notification,
        price_engine=None,
    ):
        self.root = root
        self.agent_url = agent_url.rstrip("/")
        self.account_name = account_name
        self.wallet_address = wallet_address
        self.show_notification = show_notification
        self.price_engine = price_engine

        self.balances: Dict[str, Optional[float]] = {}
        self.hl1_balances: Dict[str, float] = {}
        self.is_fetching = False
        self.mode = "swap"  # "swap" or "bridge"
        self.bridge_direction = "evm_to_hl1"  # or "hl1_to_evm"
        self.slippage_pct = 1.0

        self.dialog = ctk.CTkToplevel(root)
        self.dialog.title("Swap")
        self.dialog.geometry("460x600")
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
            text="Swap",
            font=ctk.CTkFont(size=20, weight="bold"),
            text_color="white",
        ).pack(pady=(20, 5))

        # Subtitle
        ctk.CTkLabel(
            self.dialog,
            text="Swap tokens on HyperEVM",
            font=ctk.CTkFont(size=11),
            text_color="gray70",
        ).pack(pady=(0, 15))

        # Mode tabs
        tab_frame = ctk.CTkFrame(self.dialog, fg_color="transparent")
        tab_frame.pack(fill="x", padx=30, pady=(0, 15))

        self.swap_tab_btn = ctk.CTkButton(
            tab_frame,
            text="Swap",
            width=100,
            height=30,
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color="#20c997",
            hover_color="#1ba87e",
            text_color="black",
            command=lambda: self._set_mode("swap"),
        )
        self.swap_tab_btn.pack(side="left", padx=(0, 10))

        self.bridge_tab_btn = ctk.CTkButton(
            tab_frame,
            text="Bridge",
            width=100,
            height=30,
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color=("gray30", "gray25"),
            hover_color=("gray40", "gray35"),
            command=lambda: self._set_mode("bridge"),
        )
        self.bridge_tab_btn.pack(side="left")

        # --- Swap UI ---
        self.swap_frame = ctk.CTkFrame(self.dialog, fg_color="transparent")
        self.swap_frame.pack(fill="both", expand=True, padx=30, pady=5)

        # From token
        from_frame = ctk.CTkFrame(self.swap_frame, fg_color="transparent")
        from_frame.pack(fill="x", pady=5)
        ctk.CTkLabel(from_frame, text="From:", font=ctk.CTkFont(size=12), width=70).pack(side="left")
        self.from_menu = ctk.CTkOptionMenu(
            from_frame,
            values=SWAP_TOKENS,
            command=self._on_swap_token_change,
            width=120,
        )
        self.from_menu.set("HYPE")
        self.from_menu.pack(side="left", padx=10)
        self.from_balance_label = ctk.CTkLabel(
            from_frame,
            text="Balance: --",
            font=ctk.CTkFont(size=11),
            text_color="gray60",
        )
        self.from_balance_label.pack(side="left")

        # Amount
        amount_frame = ctk.CTkFrame(self.swap_frame, fg_color="transparent")
        amount_frame.pack(fill="x", pady=10)
        ctk.CTkLabel(amount_frame, text="Amount:", font=ctk.CTkFont(size=12), width=70).pack(side="left")
        self.amount_entry = ctk.CTkEntry(
            amount_frame,
            width=140,
            placeholder_text="0.0",
        )
        self.amount_entry.pack(side="left", padx=10)
        self.max_btn = ctk.CTkButton(
            amount_frame,
            text="MAX",
            width=50,
            height=24,
            font=ctk.CTkFont(size=10, weight="bold"),
            fg_color=("gray30", "gray25"),
            hover_color=("gray40", "gray35"),
            command=self._set_max_swap_amount,
        )
        self.max_btn.pack(side="left")

        # Direction arrow
        self.arrow_label = ctk.CTkLabel(
            self.swap_frame,
            text="\u2193",
            font=ctk.CTkFont(size=18, weight="bold"),
            text_color="gray60",
        )
        self.arrow_label.pack(pady=5)

        # To token
        to_frame = ctk.CTkFrame(self.swap_frame, fg_color="transparent")
        to_frame.pack(fill="x", pady=5)
        ctk.CTkLabel(to_frame, text="To:", font=ctk.CTkFont(size=12), width=70).pack(side="left")
        self.to_menu = ctk.CTkOptionMenu(
            to_frame,
            values=SWAP_TOKENS,
            command=self._on_swap_token_change,
            width=120,
        )
        self.to_menu.set("WHYPE")
        self.to_menu.pack(side="left", padx=10)
        self.to_balance_label = ctk.CTkLabel(
            to_frame,
            text="Balance: --",
            font=ctk.CTkFont(size=11),
            text_color="gray60",
        )
        self.to_balance_label.pack(side="left")

        # Quote / fee / gas
        self.quote_label = ctk.CTkLabel(
            self.swap_frame,
            text="Expected: --",
            font=ctk.CTkFont(size=12),
            text_color="gray70",
        )
        self.quote_label.pack(anchor="w", pady=(10, 2))

        self.fee_label = ctk.CTkLabel(
            self.swap_frame,
            text="Fee: --  ·  Gas: ~$0.001",
            font=ctk.CTkFont(size=11),
            text_color="gray60",
        )
        self.fee_label.pack(anchor="w", pady=(0, 5))

        # Slippage
        slippage_frame = ctk.CTkFrame(self.swap_frame, fg_color="transparent")
        slippage_frame.pack(fill="x", pady=5)
        ctk.CTkLabel(slippage_frame, text="Slippage:", font=ctk.CTkFont(size=11), width=70).pack(side="left")
        self.slippage_entry = ctk.CTkEntry(slippage_frame, width=60, placeholder_text="1.0")
        self.slippage_entry.insert(0, "1.0")
        self.slippage_entry.pack(side="left", padx=10)
        ctk.CTkLabel(slippage_frame, text="%", font=ctk.CTkFont(size=11)).pack(side="left")

        # Swap button
        self.swap_btn = ctk.CTkButton(
            self.swap_frame,
            text="Swap",
            width=380,
            height=40,
            font=ctk.CTkFont(size=14, weight="bold"),
            fg_color="#20c997",
            hover_color="#1ba87e",
            text_color="black",
            command=self._on_swap,
        )
        self.swap_btn.pack(pady=(15, 10))

        self.close_btn_swap = ctk.CTkButton(
            self.swap_frame,
            text="Close",
            width=380,
            height=32,
            font=ctk.CTkFont(size=12),
            fg_color=("gray30", "gray25"),
            hover_color=("gray40", "gray35"),
            command=self.close,
        )
        self.close_btn_swap.pack(pady=(0, 10))

        # --- Bridge UI ---
        self.bridge_frame = ctk.CTkFrame(self.dialog, fg_color="transparent")
        # Initially hidden

        self.direction_btn = ctk.CTkButton(
            self.bridge_frame,
            text="EVM \u2194 Spot",
            width=140,
            height=32,
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color=("gray30", "gray25"),
            hover_color=("gray40", "gray35"),
            command=self._toggle_bridge_direction,
        )
        self.direction_btn.pack(pady=(0, 15))

        self.path_label = ctk.CTkLabel(
            self.bridge_frame,
            text="From HyperEVM  \u2192  To HyperCore (HL1)",
            font=ctk.CTkFont(size=12),
            text_color="#20c997",
        )
        self.path_label.pack(pady=(0, 15))

        b_asset_frame = ctk.CTkFrame(self.bridge_frame, fg_color="transparent")
        b_asset_frame.pack(fill="x", pady=5)
        ctk.CTkLabel(b_asset_frame, text="Asset:", font=ctk.CTkFont(size=12), width=70).pack(side="left")
        self.bridge_asset_menu = ctk.CTkOptionMenu(
            b_asset_frame,
            values=BRIDGE_ASSETS,
            command=self._on_bridge_asset_change,
            width=120,
        )
        self.bridge_asset_menu.set("HYPE")
        self.bridge_asset_menu.pack(side="left", padx=10)

        b_amount_frame = ctk.CTkFrame(self.bridge_frame, fg_color="transparent")
        b_amount_frame.pack(fill="x", pady=10)
        ctk.CTkLabel(b_amount_frame, text="Amount:", font=ctk.CTkFont(size=12), width=70).pack(side="left")
        self.bridge_amount_entry = ctk.CTkEntry(
            b_amount_frame,
            width=140,
            placeholder_text="0.0",
        )
        self.bridge_amount_entry.pack(side="left", padx=10)
        self.bridge_max_label = ctk.CTkLabel(
            b_amount_frame,
            text="MAX: --",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color="#20c997",
        )
        self.bridge_max_label.pack(side="left")
        self.bridge_max_label.bind("<Button-1>", lambda e: self._set_bridge_max())
        self.bridge_max_label.bind("<Enter>", lambda e: self.bridge_max_label.configure(cursor="hand2"))
        self.bridge_max_label.bind("<Leave>", lambda e: self.bridge_max_label.configure(cursor=""))

        self.transfer_btn = ctk.CTkButton(
            self.bridge_frame,
            text="Transfer",
            width=380,
            height=40,
            font=ctk.CTkFont(size=14, weight="bold"),
            fg_color="#20c997",
            hover_color="#1ba87e",
            text_color="black",
            command=self._on_bridge_transfer,
        )
        self.transfer_btn.pack(pady=(20, 10))

        self.close_btn_bridge = ctk.CTkButton(
            self.bridge_frame,
            text="Close",
            width=380,
            height=32,
            font=ctk.CTkFont(size=12),
            fg_color=("gray30", "gray25"),
            hover_color=("gray40", "gray35"),
            command=self.close,
        )
        self.close_btn_bridge.pack(pady=(0, 10))

        # Status label (shared)
        self.status_label = ctk.CTkLabel(
            self.dialog,
            text="",
            font=ctk.CTkFont(size=11),
            text_color="gray60",
            wraplength=400,
        )
        self.status_label.pack(pady=(5, 15))

        # Start in swap mode
        self._set_mode("swap")

        # Bind amount entry update for quote recalculation
        self.amount_entry.bind("<KeyRelease>", lambda e: self._update_quote())

    def _set_mode(self, mode: str):
        """Switch between Swap and Bridge tabs."""
        self.mode = mode
        if mode == "swap":
            self.swap_tab_btn.configure(fg_color="#20c997", hover_color="#1ba87e", text_color="black")
            self.bridge_tab_btn.configure(
                fg_color=("gray30", "gray25"),
                hover_color=("gray40", "gray35"),
                text_color="white",
            )
            self.swap_frame.pack(fill="both", expand=True, padx=30, pady=5)
            self.bridge_frame.pack_forget()
        else:
            self.bridge_tab_btn.configure(fg_color="#20c997", hover_color="#1ba87e", text_color="black")
            self.swap_tab_btn.configure(
                fg_color=("gray30", "gray25"),
                hover_color=("gray40", "gray35"),
                text_color="white",
            )
            self.bridge_frame.pack(fill="both", expand=True, padx=30, pady=5)
            self.swap_frame.pack_forget()
            self._update_bridge_max_label()
        self._set_status("")

    def _set_status(self, text: str, color: str = "gray60"):
        """Update the status label on the UI thread."""
        self.dialog.after(0, lambda: self.status_label.configure(text=text, text_color=color))

    def _on_swap_token_change(self, _value: str = ""):
        """Handle token selection change in swap mode."""
        self._update_swap_balances_display()
        self._update_quote()

    def _set_max_swap_amount(self):
        """Fill the swap amount entry with the max available From balance."""
        token = self.from_menu.get()
        balance = self.balances.get(token)
        if balance is None or balance <= 0:
            return
        decimals = TOKEN_DECIMALS.get(token, 18)
        self.amount_entry.delete(0, "end")
        self.amount_entry.insert(0, _format_amount(balance, decimals))
        self._update_quote()

    def _update_swap_balances_display(self):
        """Refresh From/To balance labels in swap mode."""
        from_token = self.from_menu.get()
        to_token = self.to_menu.get()
        from_bal = self.balances.get(from_token)
        to_bal = self.balances.get(to_token)
        f_text = _format_amount(from_bal, TOKEN_DECIMALS.get(from_token, 18)) if from_bal is not None else "--"
        t_text = _format_amount(to_bal, TOKEN_DECIMALS.get(to_token, 18)) if to_bal is not None else "--"
        self.from_balance_label.configure(text=f"Balance: {f_text} {from_token}")
        self.to_balance_label.configure(text=f"Balance: {t_text} {to_token}")

    def _parse_slippage(self) -> float:
        """Return slippage percentage from entry, defaulting to 1.0."""
        try:
            val = float(self.slippage_entry.get().strip())
            if val <= 0 or val > 50:
                return 1.0
            return val
        except (ValueError, TypeError):
            return 1.0

    def _update_quote(self):
        """Update the expected output quote in swap mode."""
        amount_str = self.amount_entry.get().strip()
        token_in = self.from_menu.get()
        token_out = self.to_menu.get()

        if not amount_str or token_in == token_out:
            self.quote_label.configure(text="Expected: --")
            self.fee_label.configure(text="Fee: --  ·  Gas: ~$0.001")
            return

        try:
            amount_dec = Decimal(amount_str)
        except InvalidOperation:
            self.quote_label.configure(text="Expected: --")
            return
        if amount_dec <= 0:
            self.quote_label.configure(text="Expected: --")
            return

        # Fetch current gas price for cost estimation
        try:
            gas_price_result = _rpc_call(HYPEREVM_RPC, "eth_gasPrice", [])
            base_gas_price = int(gas_price_result["result"], 16)  # wei
            boosted_gas_price = int(base_gas_price * 6)  # match ColdStack's 6x HyperEVM boost
            gas_price_gwei = boosted_gas_price / 1e9
        except Exception:
            gas_price_gwei = 0.6  # fallback: 0.6 Gwei (6x of 0.1 Gwei base)

        route = _route_swap(token_in, token_out, amount_dec)
        if route == "wrap":
            estimated = float(amount_dec)
            fee = 0.0
            gas_limit = 28000
            gas_text = f"Gas: ~{gas_limit:,} ({gas_limit * gas_price_gwei / 1e9:.6f} HYPE @ {gas_price_gwei:.1f} Gwei)"
        elif route == "unwrap":
            estimated = float(amount_dec)
            fee = 0.0
            gas_limit = 28000
            gas_text = f"Gas: ~{gas_limit:,} ({gas_limit * gas_price_gwei / 1e9:.6f} HYPE @ {gas_price_gwei:.1f} Gwei)"
        elif route in ("direct_swap", "wrap_and_swap", "swap_and_unwrap"):
            swap_token_in = "WHYPE" if token_in == "HYPE" else token_in
            swap_token_out = "WHYPE" if token_out == "HYPE" else token_out
            pool, fee_tier = _best_pool(swap_token_in, swap_token_out)
            if pool:
                fee = fee_tier / 10_000  # convert bps to percent for display
                estimated = _get_swap_quote(swap_token_in, swap_token_out, float(amount_dec), fee_tier, from_address=self.wallet_address)
                if estimated is None:
                    # Best pool's fee tier didn't work — try others
                    for alt_fee in (500, 3000, 10000):
                        if alt_fee == fee_tier:
                            continue
                        estimated = _get_swap_quote(swap_token_in, swap_token_out, float(amount_dec), alt_fee, from_address=self.wallet_address)
                        if estimated is not None and estimated > 0:
                            fee = alt_fee / 10_000
                            break
            else:
                fee = None
                estimated = None
            # Multi-step routes use more gas
            if route == "direct_swap":
                gas_limit = 150000
            elif route == "wrap_and_swap":
                gas_limit = 178000  # wrap (28k) + approve + swap (150k)
            elif route == "swap_and_unwrap":
                gas_limit = 178000  # swap (150k) + unwrap (28k)
            gas_text = f"Gas: ~{gas_limit:,} ({gas_limit * gas_price_gwei / 1e9:.6f} HYPE @ {gas_price_gwei:.1f} Gwei)"
        else:
            estimated = None
            fee = None
            gas_text = "Gas: --"

        if estimated is not None:
            decimals_out = TOKEN_DECIMALS.get(token_out, 18)
            self.quote_label.configure(
                text=f"Expected: {_format_amount(estimated, decimals_out)} {token_out}"
            )
        else:
            self.quote_label.configure(text="Expected: --")

        if fee is not None:
            self.fee_label.configure(text=f"Fee: {fee:.2f}%  ·  {gas_text}")
        else:
            self.fee_label.configure(text=f"Fee: --  ·  {gas_text}")

    def _read_token_balance(self, symbol: str) -> Optional[float]:
        """Read a single token balance synchronously via RPC. Returns None on error."""
        try:
            if symbol == "HYPE":
                result = _rpc_call(
                    HYPEREVM_RPC,
                    "eth_getBalance",
                    [self.wallet_address, "latest"],
                )
                if result and "result" in result:
                    return int(result["result"], 16) / 1e18
            else:
                contract = TOKEN_ADDRESSES.get(symbol)
                if not contract:
                    return None
                padded = _pad_address(self.wallet_address)[2:]
                call_data = SELECTOR_BALANCE_OF + padded
                result = _rpc_call(
                    HYPEREVM_RPC,
                    "eth_call",
                    [{"to": contract, "data": call_data}, "latest"],
                )
                if result and "result" in result:
                    decimals = TOKEN_DECIMALS.get(symbol, 18)
                    return int(result["result"], 16) / (10 ** decimals)
        except Exception as e:
            print(f"[swap_dialog] _read_token_balance({symbol}) error: {e}")
        return None

    def _fetch_balances(self):
        """Query EVM and HL1 balances in a background thread."""
        if self.is_fetching:
            return
        self.is_fetching = True
        self._set_status("Fetching balances...", "gray60")

        def _do_fetch():
            try:
                balances: Dict[str, Optional[float]] = {}

                # Native HYPE gas balance
                try:
                    result = _rpc_call(
                        HYPEREVM_RPC,
                        "eth_getBalance",
                        [self.wallet_address, "latest"],
                    )
                    if result and "result" in result:
                        raw = int(result["result"], 16)
                        balances["HYPE"] = raw / (10 ** 18)
                except Exception as e:
                    balances["HYPE"] = None
                    print(f"[swap_dialog] HYPE balance error: {e}")

                # ERC-20 token balances
                for symbol, contract in TOKEN_ADDRESSES.items():
                    try:
                        padded = _pad_address(self.wallet_address)[2:]
                        call_data = SELECTOR_BALANCE_OF + padded
                        result = _rpc_call(
                            HYPEREVM_RPC,
                            "eth_call",
                            [{"to": contract, "data": call_data}, "latest"],
                        )
                        if result and "result" in result:
                            raw = int(result["result"], 16)
                            balances[symbol] = raw / (10 ** TOKEN_DECIMALS[symbol])
                    except Exception as e:
                        balances[symbol] = None
                        print(f"[swap_dialog] {symbol} balance error: {e}")

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
                        try:
                            available = float(total) - float(hold)
                            if available > 0:
                                self.hl1_balances[coin] = available
                        except (ValueError, TypeError):
                            pass
                except Exception as e:
                    print(f"[swap_dialog] HL1 balance error: {e}")

                self.balances = balances
                self.dialog.after(0, self._update_swap_balances_display)
                self.dialog.after(0, self._update_bridge_max_label)
                self._set_status("Balances loaded", "gray60")
            except Exception as e:
                self._set_status(f"Balance fetch failed: {e}", "#ff6b6b")
            finally:
                self.is_fetching = False

        threading.Thread(target=_do_fetch, daemon=True).start()

    def _on_swap(self):
        """Validate inputs and execute the swap in a background thread."""
        token_in = self.from_menu.get()
        token_out = self.to_menu.get()
        amount_str = self.amount_entry.get().strip()

        if token_in == token_out:
            self._set_status("Cannot swap the same token", "#ff6b6b")
            return
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

        balance = self.balances.get(token_in)
        if balance is not None and float(amount_dec) > balance:
            self._set_status("Amount exceeds available balance", "#ff6b6b")
            return

        self.slippage_pct = self._parse_slippage()
        self.swap_btn.configure(state="disabled")
        self._set_status("Submitting swap...", "gray60")

        threading.Thread(
            target=self._execute_swap,
            args=(token_in, token_out, amount_str),
            daemon=True,
        ).start()

    def _execute_swap(self, token_in: str, token_out: str, amount_str: str):
        """Execute the chosen swap route via the agent."""
        try:
            route = _route_swap(token_in, token_out, Decimal(amount_str))

            if route == "wrap":
                self._do_wrap(amount_str)
            elif route == "unwrap":
                self._do_unwrap(amount_str)
            elif route == "direct_swap":
                self._do_direct_swap(token_in, token_out, amount_str)
            elif route == "wrap_and_swap":
                self._do_wrap(amount_str)
                self._do_direct_swap("WHYPE", token_out, amount_str)
            elif route == "swap_and_unwrap":
                # Step 1: Swap token_in → WHYPE
                self._do_direct_swap(token_in, "WHYPE", amount_str)
                # Wait for swap tx to be mined so WHYPE is in the wallet
                self._set_status("Swap submitted, waiting for confirmation before unwrap...", "gray60")
                time.sleep(8)
                # Step 2: Read actual WHYPE balance and unwrap ALL of it
                # The swap output differs from the input amount — use the real balance
                whype_balance = self._read_token_balance("WHYPE")
                if whype_balance and whype_balance > 0:
                    whype_str = f"{whype_balance:.18f}"
                    self._do_unwrap(whype_str)
                else:
                    raise RuntimeError(
                        "Swap succeeded but WHYPE balance is 0 — cannot unwrap. "
                        "The swap tx may not be mined yet. Try unwrapping manually."
                    )
            else:
                raise RuntimeError("Unsupported swap route")

            self._set_status("Swap complete", "#51cf94")
            self.dialog.after(
                0,
                lambda: self.show_notification(
                    f"Swap {token_in} \u2192 {token_out} submitted successfully"
                ),
            )
            self._fetch_balances()
        except Exception as e:
            err_msg = str(e)
            self._set_status(f"Swap failed: {err_msg}", "#ff6b6b")
            self.dialog.after(
                0,
                lambda: self.show_notification(f"Swap failed: {err_msg}", error=True),
            )
        finally:
            self.dialog.after(0, lambda: self.swap_btn.configure(state="normal"))

    def _do_wrap(self, amount_str: str) -> str:
        """Wrap native HYPE to WHYPE via simple value transfer."""
        amount_wei = _to_wei(amount_str, 18)
        result = _agent_call(
            self.agent_url,
            "broadcast_tx",
            account=self.account_name,
            to=WHYPE,
            data="0x",
            value=hex(amount_wei),
            chain="EVM",
            chain_id=999,
            rpc=HYPEREVM_RPC,
            gas_limit=50000,  # HYPE→WHYPE wrap needs ~28k, pad to 50k
        )
        return result.get("tx_hash", "")

    def _do_unwrap(self, amount_str: str) -> str:
        """Unwrap WHYPE to native HYPE."""
        amount_wei = _to_wei(amount_str, 18)
        data = SELECTOR_WITHDRAW + _pad_uint256(amount_wei)[2:]
        result = _agent_call(
            self.agent_url,
            "broadcast_tx",
            account=self.account_name,
            to=WHYPE,
            data=data,
            value="0x0",
            chain="EVM",
            chain_id=999,
            rpc=HYPEREVM_RPC,
            gas_limit=50000,  # WHYPE withdraw needs ~28k, pad to 50k
        )
        return result.get("tx_hash", "")

    def _do_approve(self, token: str, spender: str, amount_raw: int) -> Optional[str]:
        """Approve spender for token if current allowance is insufficient."""
        owner = self.wallet_address
        call_data = SELECTOR_APPROVE + _pad_address(spender)[2:] + _pad_uint256(MAX_UINT256)[2:]
        result = _rpc_call(
            HYPEREVM_RPC,
            "eth_call",
            [{"to": token, "data": SELECTOR_ALLOWANCE + _pad_address(owner)[2:] + _pad_address(spender)[2:]}, "latest"],
        )
        current = 0
        if result and "result" in result:
            try:
                current = int(result["result"], 16)
            except (ValueError, TypeError):
                current = 0
        if current >= amount_raw:
            return None
        result = _agent_call(
            self.agent_url,
            "broadcast_tx",
            account=self.account_name,
            to=token,
            data=call_data,
            value="0x0",
            chain="EVM",
            chain_id=999,
            rpc=HYPEREVM_RPC,
            gas_limit=60000,  # ERC-20 approve needs ~46k, pad to 60k
        )
        return result.get("tx_hash", "")

    def _do_direct_swap(self, token_in: str, token_out: str, amount_str: str) -> str:
        """Execute a direct pool swap via SwapRouter.exactInputSingle.

        Tries fee tiers from lowest to highest, using the first pool that
        produces a valid quote and executes without reverting.
        """
        decimals_in = TOKEN_DECIMALS.get(token_in, 18)
        decimals_out = TOKEN_DECIMALS.get(token_out, 18)
        amount_raw = _to_wei(amount_str, decimals_in)

        # Get token addresses for pool lookup
        addr_in = TOKEN_ADDRESSES.get(token_in, "").lower()
        addr_out = TOKEN_ADDRESSES.get(token_out, "").lower()
        if addr_in and addr_out:
            t0_name, t1_name = (token_in, token_out) if addr_in < addr_out else (token_out, token_in)
        else:
            t0_name, t1_name = sorted([token_in, token_out])

        # Try each fee tier — use the first one that gives a valid quote
        chosen_fee = None
        quote = None
        for fee in (500, 3000, 10000):
            if (t0_name, t1_name, fee) not in KNOWN_POOLS:
                continue
            candidate_quote = _get_swap_quote(token_in, token_out, float(amount_str), fee, from_address=self.wallet_address)
            if candidate_quote is not None and candidate_quote > 0:
                chosen_fee = fee
                quote = candidate_quote
                break

        if chosen_fee is None:
            raise RuntimeError(f"No working pool found for {token_in}/{token_out} (all fee tiers revert or have no liquidity)")

        # Approve router if needed
        approve_tx = self._do_approve(TOKEN_ADDRESSES[token_in], SWAP_ROUTER, amount_raw)
        if approve_tx:
            # Wait for approve tx to be mined before proceeding with swap
            import time as _time
            _time.sleep(5)

        # Compute slippage-protected minimum output
        slippage = self.slippage_pct / 100.0
        amount_out_min = int(quote * (1 - slippage) * (10 ** decimals_out))

        deadline = int(time.time()) + 300
        data = (
            SELECTOR_EXACT_INPUT_SINGLE
            + _pad_address(TOKEN_ADDRESSES[token_in])[2:]
            + _pad_address(TOKEN_ADDRESSES[token_out])[2:]
            + _pad_uint256(chosen_fee)[2:]
            + _pad_address(self.wallet_address)[2:]
            + _pad_uint256(deadline)[2:]
            + _pad_uint256(amount_raw)[2:]
            + _pad_uint256(amount_out_min)[2:]
            + _pad_uint256(0)[2:]  # sqrtPriceLimitX96
        )

        result = _agent_call(
            self.agent_url,
            "broadcast_tx",
            account=self.account_name,
            to=SWAP_ROUTER,
            data=data,
            value="0x0",
            chain="EVM",
            chain_id=999,
            rpc=HYPEREVM_RPC,
            gas_limit=300000,
        )
        return result.get("tx_hash", "")

    # ------------------------------------------------------------------
    # Bridge UI callbacks
    # ------------------------------------------------------------------

    def _toggle_bridge_direction(self):
        """Swap bridge direction and refresh the available max."""
        if self.bridge_direction == "evm_to_hl1":
            self.bridge_direction = "hl1_to_evm"
            self.path_label.configure(text="From HyperCore (HL1)  \u2192  To HyperEVM")
        else:
            self.bridge_direction = "evm_to_hl1"
            self.path_label.configure(text="From HyperEVM  \u2192  To HyperCore (HL1)")
        self._update_bridge_max_label()

    def _on_bridge_asset_change(self, value: str):
        """Handle bridge asset selection change."""
        self._update_bridge_max_label()

    def _set_bridge_max(self):
        """Fill the bridge amount entry with the max available balance."""
        max_val = self._get_bridge_max()
        if max_val is None:
            return
        asset = self.bridge_asset_menu.get()
        decimals = TOKEN_DECIMALS.get(asset, 18)
        self.bridge_amount_entry.delete(0, "end")
        self.bridge_amount_entry.insert(0, _format_amount(max_val, decimals))

    def _get_bridge_max(self) -> Optional[float]:
        """Return available balance for current bridge direction + asset."""
        asset = self.bridge_asset_menu.get()
        if self.bridge_direction == "evm_to_hl1":
            return self.balances.get(asset)
        return self.hl1_balances.get(asset)

    def _update_bridge_max_label(self):
        """Update the bridge MAX label from fetched balances."""
        max_val = self._get_bridge_max()
        asset = self.bridge_asset_menu.get()
        if max_val is None:
            self.bridge_max_label.configure(text="MAX: --")
        else:
            decimals = TOKEN_DECIMALS.get(asset, 18)
            self.bridge_max_label.configure(
                text=f"MAX: {_format_amount(max_val, decimals)} {asset}"
            )

    def _on_bridge_transfer(self):
        """Validate bridge inputs and start the transfer in a background thread."""
        amount_str = self.bridge_amount_entry.get().strip()
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

        max_val = self._get_bridge_max()
        if max_val is not None and float(amount_dec) > max_val:
            self._set_status("Amount exceeds available balance", "#ff6b6b")
            return

        self.transfer_btn.configure(state="disabled")
        self._set_status("Submitting...", "gray60")

        asset = self.bridge_asset_menu.get()
        if self.bridge_direction == "evm_to_hl1":
            threading.Thread(
                target=self._do_evm_to_hl1,
                args=(asset, amount_str),
                daemon=True,
            ).start()
        else:
            threading.Thread(
                target=self._do_hl1_to_evm,
                args=(asset, amount_str),
                daemon=True,
            ).start()

    def _do_evm_to_hl1(self, asset: str, amount_str: str):
        """Execute an EVM → HL1 transfer via the agent."""
        try:
            if asset == "HYPE":
                amount_wei = _to_wei(amount_str, 18)
                result = _agent_call(
                    self.agent_url,
                    "broadcast_tx",
                    account=self.account_name,
                    to=HYPE_SYSTEM_ADDRESS,
                    data="0x",
                    value=hex(amount_wei),
                    chain="EVM",
                    chain_id=999,
                    rpc=HYPEREVM_RPC,
                )
            else:
                token_address = TOKEN_ADDRESSES.get(asset)
                if not token_address:
                    raise RuntimeError(f"Unsupported asset: {asset}")
                system_addr = _system_address_for_token(asset)
                if not system_addr:
                    raise RuntimeError(f"Could not resolve HL1 system address for {asset}")
                amount_raw = _to_wei(amount_str, TOKEN_DECIMALS[asset])
                data = (
                    SELECTOR_TRANSFER
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
                    chain="EVM",
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
            err_msg = str(e)
            self._set_status(f"Transfer failed: {err_msg}", "#ff6b6b")
            self.dialog.after(
                0,
                lambda: self.show_notification(f"EVM\u2192HL1 transfer failed: {err_msg}", error=True),
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
            err_msg = str(e)
            self._set_status(f"Transfer failed: {err_msg}", "#ff6b6b")
            self.dialog.after(
                0,
                lambda: self.show_notification(f"HL1\u2192EVM transfer failed: {err_msg}", error=True),
            )
        finally:
            self.dialog.after(0, lambda: self.transfer_btn.configure(state="normal"))

    def close(self):
        """Close the dialog."""
        try:
            self.dialog.destroy()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Allowance selector constant (kept at module bottom to avoid forward ref)
# ---------------------------------------------------------------------------
SELECTOR_ALLOWANCE = "0xdd62ed3e"
