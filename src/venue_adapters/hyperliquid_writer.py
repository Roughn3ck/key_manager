"""
Hyperliquid Writer -- VenueWriter implementation for HyperEVM (ColdStack LP Engine v5.0).

Write-side counterpart to hyperliquid_adapter.py. Implements the VenueWriter ABC
for HyperEVM concentrated-liquidity pools (Uniswap V3 style).

The writer does NOT hold private keys. All signing is delegated to the headless
key_manager_agent (localhost:8842) which has the vault unlocked and can sign
EVM transactions internally.

Security:
  - Every write operation is a tool for the user, not an autonomous agent.
  - The GUI must confirm each operation before calling the writer.
  - No private keys are ever loaded, stored, or returned by this module.
  - Operations are logged with timestamp, action, and tx hash (no private data).

Version: v5.0 (July 2026)
"""

import json
import time
import urllib.request
import urllib.error
from typing import Any, Dict, List, Optional, Tuple

from venue_adapters.venue_writer import (
    VenueWriter,
    SwapParams,
    OpenPositionParams,
    IncreaseLiquidityParams,
    DecreaseLiquidityParams,
    CollectFeesParams,
    CompoundFeesParams,
    RebalanceParams,
)

# ---------------------------------------------------------------------------
# Constants -- HyperEVM (Chain ID 999)
# ---------------------------------------------------------------------------

HYPEREVM_RPC_URL = "https://rpc.hyperliquid.xyz/evm"
HYPEREVM_CHAIN_ID = 999

WHYPE = "0x5555555555555555555555555555555555555555"
UBTC = "0x9FDBdA0A5e284c32744D2f17Ee5c74B284993463"
POSITION_MANAGER = "0xead19ae861c29bbb2101e834922b2feee69b9091"
POOL_FACTORY = "0xb1c0fa0b789320044a6f623cfe5ebda9562602e3"
WHYPE_UBTC_POOL_3000 = "0x3a36b04bcc1d5e2e303981ef643d2668e00b43e7"
SWAP_ROUTER = "0x1ebdfc75ffe3ba3de61e7138a3e8706ac841af9b"

# Token decimals
WHYPE_DECIMALS = 18
UBTC_DECIMALS = 8

# ERC-20 / Uniswap V3 function selectors (keccak256 first 4 bytes)
SELECTOR_APPROVE = "0x095ea7b3"       # approve(address,uint256)
SELECTOR_TRANSFER = "0xa9059cbb"      # transfer(address,uint256)
SELECTOR_BALANCE_OF = "0x70a08231"    # balanceOf(address)
SELECTOR_ALLOWANCE = "0xdd62ed3e"     # allowance(address,address)
SELECTOR_WITHDRAW = "0x2e1a7d4d"      # withdraw(uint256) -- WETH style
# Project X / HyperEVM PositionManager fork selectors — verified by bytecode scan
# and eth_call against 0xead19ae861c29bbb2101e834922b2feee69b9091.
SELECTOR_MINT = "0x88316456"          # mint(MintParams) — verified, unchanged
SELECTOR_INCREASE_LIQUIDITY = "0x219f5d17"  # increaseLiquidity((uint256,uint256,uint256,uint256,uint256,uint256)) — verified in bytecode
SELECTOR_DECREASE_LIQUIDITY = "0x0c49ccbe"  # decreaseLiquidity((uint256,uint128,uint256,uint256,uint256)) — verified by eth_call
SELECTOR_COLLECT = "0xfc6f7865"       # collect((uint256,address,uint128,uint128)) — verified by eth_call returning two uint128 amounts
SELECTOR_POSITIONS = "0x99fbab88"     # positions(uint256)
SELECTOR_EXACT_INPUT_SINGLE = "0x414bf389"  # exactInputSingle((address,address,uint24,address,uint256,uint256,uint256,uint160))     # positions(uint256) — verified, unchanged

# Default gas limit for LP operations (can be overridden)
DEFAULT_GAS_LIMIT = 300000
MAX_UINT256 = 2**256 - 1
MAX_UINT128 = (2 ** 128) - 1


# ---------------------------------------------------------------------------
# ABI encoding helpers
# ---------------------------------------------------------------------------

def _pad_uint256(value: int) -> str:
    """Encode an unsigned integer as a 32-byte hex word (no 0x prefix)."""
    if value < 0:
        raise ValueError("Cannot encode negative value as uint256")
    return format(value, "064x")


def _pad_address(address: str) -> str:
    """Pad a 20-byte EVM address to a 32-byte ABI word (no 0x prefix)."""
    clean = address.lower()
    if clean.startswith("0x"):
        clean = clean[2:]
    return ("0" * 24) + clean


def _to_wei(amount: float, decimals: int) -> int:
    """Convert a human-readable token amount to its raw integer representation."""
    return int(amount * (10 ** decimals))


def _from_wei(raw: int, decimals: int) -> float:
    """Convert a raw integer token amount to human-readable float."""
    return raw / (10 ** decimals)


def _get_token_decimals(token: str) -> int:
    """Return decimals for known tokens; default 18 for unknown."""
    lower = token.lower()
    if lower == WHYPE.lower():
        return WHYPE_DECIMALS
    if lower == UBTC.lower():
        return UBTC_DECIMALS
    return 18


def _log_action(action: str, tx_hash: str = "", extra: str = "") -> None:
    """Log a write operation to stdout (redirected to gui_debug.log in frozen mode).

    No private data is ever logged.
    """
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
    parts = [f"[{timestamp}]", f"action={action}"]
    if tx_hash:
        parts.append(f"tx={tx_hash}")
    if extra:
        parts.append(extra)
    print(" ".join(parts))


# ---------------------------------------------------------------------------
# HyperliquidWriter
# ---------------------------------------------------------------------------

class HyperliquidWriter(VenueWriter):
    """VenueWriter implementation for HyperEVM concentrated-liquidity pools.

    All transaction signing is delegated to the key_manager_agent via HTTP.
    The writer never touches private keys directly.
    """

    VENUE_KEY = "hyperliquid"

    def __init__(self, agent_url: str = "http://127.0.0.1:8842"):
        """Initialize the writer with the agent's HTTP endpoint.

        Args:
            agent_url: The base URL of the key_manager_agent (default localhost:8842).
        """
        self.agent_url = agent_url.rstrip("/")
        self.chain_id = HYPEREVM_CHAIN_ID
        self.rpc_url = HYPEREVM_RPC_URL
        self._unlocked = False

    # ------------------------------------------------------------------
    # Agent communication
    # ------------------------------------------------------------------

    def _agent_call(self, cmd: str, **params) -> dict:
        """Send a command to the key_manager_agent and return the response.

        Args:
            cmd: The command name (e.g. 'broadcast_tx', 'status').
            **params: Command parameters.

        Returns:
            The parsed JSON response dict from the agent.

        Raises:
            RuntimeError: If the agent returns an error or is unreachable.
        """
        payload = {"cmd": cmd, **params}
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self.agent_url,
            data=data,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                result = json.loads(resp.read().decode("utf-8"))
        except urllib.error.URLError as e:
            raise RuntimeError(
                f"Cannot reach key_manager_agent at {self.agent_url}. "
                f"Is it running? Error: {e}"
            ) from e

        if result.get("status") != "ok":
            error_msg = result.get("error", "Unknown agent error")
            raise RuntimeError(f"Agent error ({cmd}): {error_msg}")
        return result["result"]

    def _wait_for_tx_receipt(self, tx_hash: str, timeout: int = 120, poll_interval: float = 2.0) -> Optional[dict]:
        """Wait for a transaction to be mined. Returns the receipt dict or None on timeout.

        Args:
            tx_hash: The transaction hash to wait for.
            timeout: Maximum seconds to wait (default 120).
            poll_interval: Seconds between polls (default 2.0).

        Returns:
            The transaction receipt dict if mined (with 'status' field), or None on timeout.
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            receipt = self._rpc_call("eth_getTransactionReceipt", [tx_hash])
            if receipt and isinstance(receipt, dict):
                status = receipt.get("status", "")
                if status:
                    # status is hex string: "0x1" = success, "0x0" = revert
                    if status == "0x1":
                        _log_action("tx_confirmed", tx_hash=tx_hash, extra="success")
                        return receipt
                    elif status == "0x0":
                        _log_action("tx_confirmed", tx_hash=tx_hash, extra="REVERTED")
                        raise RuntimeError(f"Transaction {tx_hash} reverted on-chain")
            time.sleep(poll_interval)
        _log_action("tx_timeout", tx_hash=tx_hash, extra=f"no receipt after {timeout}s")
        return None

    def _broadcast(self, account: str, to: str, data: str, value: str = "0", nonce: Optional[int] = None) -> str:
        """Sign and broadcast a transaction via the agent. Returns tx hash.

        Args:
            account: The vault account name (e.g. 'G5').
            to: The recipient contract address (0x-prefixed).
            data: The calldata hex string (0x-prefixed).
            value: The value in wei as a decimal string (default '0').
            nonce: Optional nonce override. If None, agent will fetch from chain.

        Returns:
            The transaction hash.

        Raises:
            RuntimeError: If the agent returns an error or is unreachable.
        """
        # Pre-flight: check signer's gas balance before attempting broadcast
        vault_address = self._get_account_address(account)
        gas_balance = self._read_native_balance(vault_address)
        if gas_balance == 0:
            raise RuntimeError(
                f"Insufficient gas: wallet {vault_address} has 0 HYPE. "
                f"Send HYPE to this address to pay for transaction gas."
            )

        # If nonce is provided (e.g. from compound_fees), verify it matches
        # the chain nonce for the vault address. If the key-derived address
        # differs from the vault address, the agent will use its own fetched
        # nonce — but if an explicit nonce was passed, it may be wrong.
        if nonce is not None:
            chain_nonce = self._rpc_call(
                "eth_getTransactionCount", [vault_address, "latest"]
            )
            if chain_nonce is not None:
                chain_nonce_int = int(chain_nonce, 16) if isinstance(chain_nonce, str) else int(chain_nonce)
                if nonce > chain_nonce_int:
                    print(f"[broadcast WARNING] Provided nonce {nonce} > chain nonce "
                          f"{chain_nonce_int} for {vault_address}. Resetting to chain nonce.")
                    nonce = chain_nonce_int

        params = dict(
            cmd="broadcast_tx",
            account=account,
            chain="EVM",
            to=to,
            data=data,
            value=value,
            chain_id=self.chain_id,
            rpc=self.rpc_url,
        )
        if nonce is not None:
            params["nonce"] = nonce
        result = self._agent_call(**params)
        tx_hash = result.get("tx_hash", "")
        signer = result.get("from", "unknown")
        used_nonce = result.get("nonce", "unknown")
        _log_action("broadcast", tx_hash=tx_hash,
                    extra=f"to={to} from={signer} nonce={used_nonce}")
        return tx_hash

    def _read_native_balance(self, address: str) -> int:
        """Read the native HYPE balance of an address via RPC.

        Args:
            address: The EVM address to check.

        Returns:
            The balance in wei as an integer (0 if RPC fails).
        """
        result = self._rpc_call("eth_getBalance", [address, "latest"])
        if not result or not isinstance(result, str):
            return 0
        try:
            return int(result, 16)
        except (ValueError, TypeError):
            return 0

    def _rpc_call(self, method: str, params: list) -> Optional[Any]:
        """Make a direct JSON-RPC call to HyperEVM (for reads like allowance).

        Args:
            method: The JSON-RPC method name.
            params: The method parameters.

        Returns:
            The 'result' field from the RPC response, or None on error.
        """
        payload = json.dumps({
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
            "id": 1,
        }).encode("utf-8")
        req = urllib.request.Request(
            self.rpc_url,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "User-Agent": "ColdStack/5.0",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data.get("result")
        except Exception:
            return None

    def _get_position_liquidity(self, token_id: int) -> int:
        """Read the current liquidity of a position from the Position Manager.

        Args:
            token_id: The NFT token ID of the position.

        Returns:
            The liquidity amount as an integer, or 0 if unreadable.
        """
        data = SELECTOR_POSITIONS + _pad_uint256(token_id)
        result = self._rpc_call(
            "eth_call",
            [{"to": POSITION_MANAGER, "data": data}, "latest"],
        )
        if not result or not isinstance(result, str) or len(result) < 2 + 32 * 12:
            return 0
        try:
            # positions() returns 12 32-byte fields. Liquidity is the 8th field
            # at body offset 448-512 (after nonce, operator, token0, token1,
            # fee, tickLower, tickUpper).
            body = result[2:]
            return int(body[448:512], 16)
        except (ValueError, IndexError):
            return 0

    def _check_allowance(self, owner: str, token: str, spender: str) -> int:
        """Check the current ERC-20 allowance of owner for spender.

        Args:
            owner: The token owner address.
            token: The ERC-20 token contract address.
            spender: The spender contract address.

        Returns:
            The allowance amount as an integer (0 if no allowance).
        """
        data = SELECTOR_ALLOWANCE + _pad_address(owner) + _pad_address(spender)
        result = self._rpc_call(
            "eth_call",
            [{"to": token, "data": data}, "latest"],
        )
        if not result or not isinstance(result, str) or len(result) < 66:
            return 0
        try:
            return int(result[2:66], 16)
        except (ValueError, IndexError):
            return 0

    def _ensure_approval(self, account: str, account_address: str,
                         token: str, spender: str, amount: int) -> Optional[str]:
        """Ensure the spender has sufficient allowance; approve if needed.

        Args:
            account: The vault account name.
            account_address: The EVM address derived from the account.
            token: The ERC-20 token address.
            spender: The spender contract address.
            amount: The required allowance amount (raw integer).

        Returns:
            The tx hash if an approval was sent, None if already approved.
        """
        current = self._check_allowance(account_address, token, spender)
        if current >= amount:
            return None
        # Approve max uint256 to avoid repeated approvals
        data = SELECTOR_APPROVE + _pad_address(spender) + _pad_uint256(MAX_UINT256)
        tx_hash = self._broadcast(account, token, data)
        _log_action("approve", tx_hash=tx_hash, extra=f"token={token} spender={spender}")
        return tx_hash

    def _get_account_address(self, account: str) -> str:
        """Get the EVM address for a vault account from the agent.

        Args:
            account: The vault account name (e.g. 'G5').

        Returns:
            The checksummed EVM address.

        Raises:
            RuntimeError: If the agent cannot return the address.
        """
        result = self._agent_call("get_address", account=account, chain="EVM")
        if isinstance(result, list) and result:
            return result[0].get("address", "")
        if isinstance(result, dict):
            return result.get("address", "")
        raise RuntimeError(f"Could not get EVM address for account '{account}'")

    def _get_token0_token1_for_pool(self, pool_address: str) -> Tuple[str, str]:
        """Read token0 and token1 from a pool contract.

        Args:
            pool_address: The pool contract address.

        Returns:
            A tuple of (token0_address, token1_address). Empty strings on failure.
        """
        t0_result = self._rpc_call(
            "eth_call",
            [{"to": pool_address, "data": "0x0dfe1681"}, "latest"],
        )
        t1_result = self._rpc_call(
            "eth_call",
            [{"to": pool_address, "data": "0xd21220a7"}, "latest"],
        )
        token0 = ""
        token1 = ""
        if t0_result and isinstance(t0_result, str) and len(t0_result) >= 66:
            token0 = "0x" + t0_result[2:66][-40:]
        if t1_result and isinstance(t1_result, str) and len(t1_result) >= 66:
            token1 = "0x" + t1_result[2:66][-40:]
        return token0, token1

    # ------------------------------------------------------------------
    # VenueWriter interface
    # ------------------------------------------------------------------

    def is_available(self) -> bool:
        """Return True if the agent is running and the vault is unlocked."""
        try:
            result = self._agent_call("status")
            unlocked = result.get("unlocked", False) if isinstance(result, dict) else False
            self._unlocked = unlocked
            return unlocked
        except Exception:
            self._unlocked = False
            return False

    def unlock(self, credentials: Dict[str, Any]) -> bool:
        """Unlock the signer via the agent.

        The agent typically auto-unlocks at startup with a password from env
        or CLI. This method sends an unlock command as a fallback if the
        agent was started without a password.

        Args:
            credentials: A dict with 'password' key.

        Returns:
            True if the agent is now unlocked.
        """
        password = credentials.get("password")
        if not password:
            return self.is_available()
        # The agent's unlock command uses the password it was started with.
        # If the agent is already unlocked, this is a no-op status check.
        if self.is_available():
            return True
        # If not unlocked, we cannot remotely provide a password (security design).
        # The agent must be restarted with the password.
        return False

    def wrap_native(self, account: str, amount: float) -> str:
        """Wrap HYPE into WHYPE by depositing native HYPE to the WHYPE contract.

        Args:
            account: The vault account name.
            amount: The amount of HYPE to wrap (human-readable, 18 decimals).

        Returns:
            The transaction hash.
        """
        value_wei = _to_wei(amount, WHYPE_DECIMALS)
        tx_hash = self._broadcast(
            account=account,
            to=WHYPE,
            data="0x",
            value=str(value_wei),
        )
        _log_action("wrap_native", tx_hash=tx_hash, extra=f"amount={amount} HYPE")
        return tx_hash

    def unwrap_native(self, account: str, amount: float) -> str:
        """Unwrap WHYPE back to HYPE by calling WHYPE.withdraw(amount).

        Args:
            account: The vault account name.
            amount: The amount of WHYPE to unwrap (human-readable, 18 decimals).

        Returns:
            The transaction hash.
        """
        value_wei = _to_wei(amount, WHYPE_DECIMALS)
        data = SELECTOR_WITHDRAW + _pad_uint256(value_wei)
        tx_hash = self._broadcast(account, WHYPE, data)
        _log_action("unwrap_native", tx_hash=tx_hash, extra=f"amount={amount} WHYPE")
        return tx_hash

    def approve(self, account: str, token: str, spender: str, amount: float) -> str:
        """Approve a spender to pull tokens from the account.

        Args:
            account: The vault account name.
            token: The ERC-20 token contract address.
            spender: The spender contract address.
            amount: The amount to approve (human-readable). Uses max uint256 internally.

        Returns:
            The transaction hash.
        """
        account_address = self._get_account_address(account)
        decimals = _get_token_decimals(token)
        raw_amount = _to_wei(amount, decimals)
        tx_hash = self._ensure_approval(account, account_address, token, spender, raw_amount)
        if tx_hash is None:
            _log_action("approve", extra=f"already_approved token={token}")
            return ""
        return tx_hash

    def swap(self, params: SwapParams) -> str:
        """Execute a single-pool exact-input swap on HyperEVM.

        Uses Uniswap V3 SwapRouter.exactInputSingle.

        Args:
            params: SwapParams with token_in, token_out, amount_in, fee, recipient.

        Returns:
            The swap transaction hash.
        """
        account_address = self._get_account_address(params.account)
        decimals_in = _get_token_decimals(params.token_in)
        amount_in_raw = _to_wei(params.amount_in, decimals_in)

        # Approve token_in for the Swap Router
        self._ensure_approval(params.account, account_address,
                              params.token_in, SWAP_ROUTER, amount_in_raw)

        deadline = int(time.time()) + params.deadline_seconds

        # Encode exactInputSingle(ExactInputSingleParams)
        # struct: tokenIn, tokenOut, fee, recipient, deadline,
        #         amountIn, amountOutMinimum, sqrtPriceLimitX96
        data = (
            SELECTOR_EXACT_INPUT_SINGLE
            + _pad_address(params.token_in)
            + _pad_address(params.token_out)
            + _pad_uint256(params.fee)
            + _pad_address(account_address)
            + _pad_uint256(deadline)
            + _pad_uint256(amount_in_raw)
            + _pad_uint256(0)  # amountOutMinimum -- TODO: slippage protection
            + _pad_uint256(0)  # sqrtPriceLimitX96
        )

        tx_hash = self._broadcast(params.account, SWAP_ROUTER, data)
        _log_action("swap", tx_hash=tx_hash,
                    extra=f"in={params.token_in} out={params.token_out} amount={amount_in_raw}")
        return tx_hash

    def get_swap_quote(self, token_in: str, token_out: str, amount_in: float,
                       fee: int = 3000) -> Optional[float]:
        """Estimate swap output amount via read-only pool price query.

        Reads the pool's slot0 to get sqrtPriceX96 and computes
        a simplified output estimate. Not exact (doesn't account for
        liquidity depth beyond the current tick) but sufficient for UI display.

        Args:
            token_in: Input token address.
            token_out: Output token address.
            amount_in: Input amount (human-readable).
            fee: Pool fee tier (default 3000).

        Returns:
            Estimated output amount (human-readable), or None if query fails.
        """
        try:
            from venue_adapters.hyperliquid_adapter import _evm_rpc_call

            # Ensure token_in < token_out for pool query (Uniswap sorts tokens)
            token0 = min(token_in, token_out)
            token1 = max(token_in, token_out)

            # Call factory.getPool(token0, token1, fee)
            FACTORY = "0xb1c0fa0b789320044a6f623cfe5ebda9562602e3"
            factory_data = ("0x1698ee82"
                            + _pad_address(token0)
                            + _pad_address(token1)
                            + _pad_uint256(fee))
            pool_result = _evm_rpc_call("eth_call", [{"to": FACTORY, "data": factory_data}, "latest"])
            if not pool_result or len(pool_result) < 66:
                return None

            pool_address = "0x" + pool_result[2:][24:64]  # address is last 20 bytes of 32-byte word

            # Read slot0 (sqrtPriceX96, tick, protocolFee, ...)
            # slot0() selector = 0x3850c7bd
            slot0_data = "0x3850c7bd"
            slot0_result = _evm_rpc_call("eth_call", [{"to": pool_address, "data": slot0_data}, "latest"])
            if not slot0_result or len(slot0_result) < 66:
                return None

            body = slot0_result[2:]
            sqrt_price_x96 = int(body[0:64], 16)

            # Compute price: (sqrtPriceX96 / 2^96)^2 = price token1/token0
            price_ratio = (sqrt_price_x96 / (2 ** 96)) ** 2

            # Adjust for token order and decimals
            decimals_in = _get_token_decimals(token_in)
            decimals_out = _get_token_decimals(token_out)

            # price_ratio is in terms of token1/token0
            if token_in == token0:
                # amount_out = amount_in * price_ratio * (10^decimals_out / 10^decimals_in)
                raw_out = amount_in * price_ratio * (10 ** decimals_out) / (10 ** decimals_in)
            else:
                # Inverted: amount_out = amount_in / price_ratio * (10^decimals_out / 10^decimals_in)
                raw_out = amount_in / price_ratio * (10 ** decimals_out) / (10 ** decimals_in)

            # Subtract fee (0.3% = fee/1e6)
            fee_fraction = fee / 1_000_000
            estimated_out = raw_out * (1 - fee_fraction)

            return estimated_out
        except Exception as e:
            print(f"[get_swap_quote] error: {e}")
            return None

    def open_position(self, params: OpenPositionParams) -> Tuple[str, Optional[int]]:
        """Open a new concentrated-liquidity LP position.

        Flow:
          1. Approve token0 and token1 for the Position Manager
          2. Call PositionManager.mint(MintParams)
          3. Parse the transaction receipt for the new token ID

        Args:
            params: OpenPositionParams with token0, token1, fee, ticks, amounts.

        Returns:
            A tuple of (tx_hash, new_token_id). token_id may be None if parsing fails.
        """
        account_address = self._get_account_address(params.account)

        decimals0 = _get_token_decimals(params.token0)
        decimals1 = _get_token_decimals(params.token1)
        amount0_raw = _to_wei(params.amount0, decimals0)
        amount1_raw = _to_wei(params.amount1, decimals1)

        # 1. Approve both tokens for the Position Manager
        self._ensure_approval(params.account, account_address,
                              params.token0, POSITION_MANAGER, amount0_raw)
        self._ensure_approval(params.account, account_address,
                              params.token1, POSITION_MANAGER, amount1_raw)

        # 2. Encode mint(MintParams)
        # MintParams struct:
        #   token0, token1, fee, tickLower, tickUpper,
        #   amount0Desired, amount1Desired,
        #   amount0Min, amount1Min, recipient, deadline
        deadline = int(time.time()) + params.deadline_seconds
        # Slippage: default 0 minimums (user accepts any price impact)
        # In production, these should be computed from current pool price
        amount0_min = 0
        amount1_min = 0

        # Encode the MintParams struct as a tuple
        mint_data = (
            SELECTOR_MINT
            + _pad_address(params.token0)
            + _pad_address(params.token1)
            + _pad_uint256(params.fee)
            + _pad_uint256(params.tick_lower)
            + _pad_uint256(params.tick_upper)
            + _pad_uint256(amount0_raw)
            + _pad_uint256(amount1_raw)
            + _pad_uint256(amount0_min)
            + _pad_uint256(amount1_min)
            + _pad_address(account_address)
            + _pad_uint256(deadline)
        )

        tx_hash = self._broadcast(params.account, POSITION_MANAGER, mint_data)
        _log_action("open_position", tx_hash=tx_hash,
                     extra=f"token0={params.token0} token1={params.token1} "
                           f"fee={params.fee} ticks=[{params.tick_lower},{params.tick_upper}]")

        # 3. Parse the transaction receipt for the token ID
        token_id = self._parse_mint_token_id(tx_hash)
        return tx_hash, token_id

    def _parse_mint_token_id(self, tx_hash: str) -> Optional[int]:
        """Attempt to extract the new position NFT token ID from a mint tx receipt.

        Looks for the Transfer event (ERC-721) from the Position Manager.
        Transfer(address,address,uint256) topic:
          0xd3d44d5b1e3e4f5e6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0

        Args:
            tx_hash: The mint transaction hash.

        Returns:
            The token ID as an integer, or None if parsing fails.
        """
        try:
            receipt = self._rpc_call("eth_getTransactionReceipt", [tx_hash])
            if not receipt or not isinstance(receipt, dict):
                return None
            logs = receipt.get("logs", [])
            # ERC-721 Transfer event topic
            transfer_topic = (
                "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b4ef"
            )
            for log_entry in logs:
                topics = log_entry.get("topics", [])
                if not topics:
                    continue
                if topics[0].lower() == transfer_topic:
                    # The token ID is in the third topic (topics[3])
                    if len(topics) >= 4:
                        token_id_hex = topics[3]
                        return int(token_id_hex, 16)
            return None
        except Exception:
            return None

    def increase_liquidity(self, params: IncreaseLiquidityParams) -> str:
        """Add liquidity to an existing LP position.

        Flow:
          1. Approve tokens for the Position Manager
          2. Call PositionManager.increaseLiquidity(IncreaseLiquidityParams)

        Args:
            params: IncreaseLiquidityParams with position_id, amounts.

        Returns:
            The transaction hash.
        """
        # Parse token_id from position_id (format: "hyperevm:<token_id>")
        token_id = self._parse_token_id(params.position_id)
        if token_id is None:
            raise ValueError(f"Invalid position_id: {params.position_id}")

        # We need token0/token1 to approve; read from the position
        # For simplicity, approve both WHYPE and UBTC (the known pool tokens)
        # In a more general implementation, we'd read positions() to get token0/token1
        account_address = self._get_account_address(params.account)

        decimals0 = WHYPE_DECIMALS
        decimals1 = UBTC_DECIMALS
        amount0_raw = _to_wei(params.amount0, decimals0)
        amount1_raw = _to_wei(params.amount1, decimals1)

        # Approve both known tokens for the Position Manager
        self._ensure_approval(params.account, account_address,
                              WHYPE, POSITION_MANAGER, amount0_raw)
        self._ensure_approval(params.account, account_address,
                              UBTC, POSITION_MANAGER, amount1_raw)

        deadline = int(time.time()) + params.deadline_seconds

        # Encode increaseLiquidity(IncreaseLiquidityParams)
        # IncreaseLiquidityParams struct:
        #   tokenId, amount0Desired, amount1Desired, amount0Min, amount1Min, deadline
        data = (
            SELECTOR_INCREASE_LIQUIDITY
            + _pad_uint256(token_id)
            + _pad_uint256(amount0_raw)
            + _pad_uint256(amount1_raw)
            + _pad_uint256(0)  # amount0Min
            + _pad_uint256(0)  # amount1Min
            + _pad_uint256(deadline)
        )

        tx_hash = self._broadcast(params.account, POSITION_MANAGER, data)
        _log_action("increase_liquidity", tx_hash=tx_hash,
                     extra=f"token_id={token_id}")
        return tx_hash

    def decrease_liquidity(self, params: DecreaseLiquidityParams) -> str:
        """Remove liquidity from an existing LP position.

        Args:
            params: DecreaseLiquidityParams with position_id, liquidity, slippage.

        Returns:
            The transaction hash.
        """
        token_id = self._parse_token_id(params.position_id)
        if token_id is None:
            raise ValueError(f"Invalid position_id: {params.position_id}")

        deadline = int(time.time()) + params.deadline_seconds

        # Encode decreaseLiquidity(DecreaseLiquidityParams)
        # DecreaseLiquidityParams struct:
        #   tokenId, liquidity, amount0Min, amount1Min, deadline
        data = (
            SELECTOR_DECREASE_LIQUIDITY
            + _pad_uint256(token_id)
            + _pad_uint256(params.liquidity)
            + _pad_uint256(0)  # amount0Min
            + _pad_uint256(0)  # amount1Min
            + _pad_uint256(deadline)
        )

        tx_hash = self._broadcast(params.account, POSITION_MANAGER, data)
        _log_action("decrease_liquidity", tx_hash=tx_hash,
                     extra=f"token_id={token_id} liquidity={params.liquidity}")
        return tx_hash

    def collect_fees(self, params: CollectFeesParams) -> str:
        """Collect accrued fees for a position.

        Args:
            params: CollectFeesParams with position_id, recipient.

        Returns:
            The transaction hash.
        """
        token_id = self._parse_token_id(params.position_id)
        if token_id is None:
            raise ValueError(f"Invalid position_id: {params.position_id}")

        recipient = params.recipient
        if not recipient:
            recipient = self._get_account_address(params.account)

        # Encode collect(CollectParams)
        # CollectParams struct: tokenId, recipient, amount0Max, amount1Max
        # amount0Max/amount1Max are uint128, so use MAX_UINT128 not MAX_UINT256
        data = (
            SELECTOR_COLLECT
            + _pad_uint256(token_id)
            + _pad_address(recipient)
            + _pad_uint256(MAX_UINT128)  # amount0Max (uint128)
            + _pad_uint256(MAX_UINT128)  # amount1Max (uint128)
        )

        tx_hash = self._broadcast(params.account, POSITION_MANAGER, data)
        _log_action("collect_fees", tx_hash=tx_hash, extra=f"token_id={token_id}")
        return tx_hash

    def compound_fees(self, params: "CompoundFeesParams") -> List[str]:
        """Collect fees, swap to optimal ratio, and increase liquidity.

        Multi-TX flow:
          1. Collect all accrued fees to the wallet
          2. Wait for collect TX to be mined
          3. Parse fee amounts from the collect TX receipt (Transfer events)
          4. Fallback: if receipt parsing returns 0, use balance diff with retries
          5. Read position data for tick range (with retry)
          6. Read current sqrtPrice from pool (with retry)
          7. Compute optimal swap based on position range:
             - Below range: swap UBTC fees -> WHYPE (position is all WHYPE)
             - Above range: swap WHYPE fees -> UBTC (position is all UBTC)
             - In range: swap to optimal ratio for current tick
          8. Execute swap if needed (approve + swap)
          9. Wait for swap TX
          10. Read post-swap balances
          11. Approve PositionManager for both tokens
          12. Call increaseLiquidity with fee amounts

        Returns:
            A list of transaction hashes for all transactions submitted.
        """
        import math as _math
        tx_hashes: List[str] = []
        token_id = self._parse_token_id(params.position_id)
        if token_id is None:
            raise ValueError(f"Invalid position_id: {params.position_id}")

        account_address = self._get_account_address(params.account)
        deadline = int(time.time()) + params.deadline_seconds

        # Step 1: Collect all fees
        _log_action("compound_fees_step1", extra="collecting fees")
        collect_data = (
            SELECTOR_COLLECT
            + _pad_uint256(token_id)
            + _pad_address(account_address)
            + _pad_uint256(MAX_UINT128)   # amount0Max
            + _pad_uint256(MAX_UINT128)   # amount1Max
        )
        collect_tx = self._broadcast(params.account, POSITION_MANAGER, collect_data)
        tx_hashes.append(collect_tx)
        _log_action("compound_fees_collect", tx_hash=collect_tx, extra=f"token_id={token_id}")

        try:
            # Step 2: Wait for collect TX to be mined
            receipt = self._wait_for_tx_receipt(collect_tx, timeout=120, poll_interval=2.0)
            if receipt is None:
                _log_action("compound_fees_fail", extra="collect TX not mined within timeout")
                return tx_hashes

            # Step 3: Parse fee amounts from the collect TX receipt (Transfer events)
            fee0_raw, fee1_raw = self._parse_fees_from_receipt(receipt, account_address)
            _log_action("compound_fees_parsed", extra=f"fee0={fee0_raw} fee1={fee1_raw} (from receipt)")

            # Step 4: Fallback to balance diff if receipt parsing returned zeros
            if fee0_raw <= 0 and fee1_raw <= 0:
                _log_action("compound_fees_fallback", extra="receipt parsing returned 0, trying balance diff")
                try:
                    bal0_before = self._read_balance(account_address, WHYPE)
                    bal1_before = self._read_balance(account_address, UBTC)
                    # We already have the receipt, so the state is updated.
                    # But add a small delay for RPC consistency.
                    time.sleep(1.0)
                    bal0_after = self._read_balance(account_address, WHYPE)
                    bal1_after = self._read_balance(account_address, UBTC)
                    fee0_raw = bal0_after - bal0_before
                    fee1_raw = bal1_after - bal1_before
                except Exception as e:
                    _log_action("compound_fees_warn", extra=f"balance read error in fallback: {e}")
                    fee0_raw = 0
                    fee1_raw = 0
                _log_action("compound_fees_balancediff", extra=f"fee0={fee0_raw} fee1={fee1_raw} (from balance diff)")

            if fee0_raw <= 0 and fee1_raw <= 0:
                _log_action("compound_fees_skip", extra=f"no fees collected (both methods returned 0) fee0={fee0_raw} fee1={fee1_raw}")
                return tx_hashes

            # Step 5: Read position data for tick range (with retry)
            pos_data = self._read_position_data(token_id)
            if pos_data is None:
                _log_action("compound_fees_skip", extra=f"could not read position data after retries token_id={token_id}")
                return tx_hashes

            tick_lower, tick_upper, fee = pos_data

            # Step 6: Read current sqrtPrice from pool (with retry)
            sqrt_price_x96 = self._read_sqrt_price_x96(WHYPE_UBTC_POOL_3000)
            if sqrt_price_x96 is None or sqrt_price_x96 == 0:
                _log_action("compound_fees_skip", extra=f"could not read pool price after retries pool={WHYPE_UBTC_POOL_3000}")
                return tx_hashes

            sqrt_price = sqrt_price_x96 / (2 ** 96)
            sqrt_lower = 1.0001 ** (tick_lower / 2.0)
            sqrt_upper = 1.0001 ** (tick_upper / 2.0)

            # Step 7: Compute optimal swap
            fee0 = fee0_raw / (10 ** WHYPE_DECIMALS)
            fee1 = fee1_raw / (10 ** UBTC_DECIMALS)
            price = sqrt_price ** 2
            human_price = price * (10 ** (WHYPE_DECIMALS - UBTC_DECIMALS))

            swap_needed = False
            swap_token_in = ""
            swap_token_out = ""
            swap_amount_raw = 0

            current_tick = int(_math.floor(_math.log(sqrt_price ** 2, 1.0001)))

            if current_tick >= tick_upper:
                # Price above range: position is 100% token1 (UBTC).
                # Need only UBTC for increaseLiquidity. Swap WHYPE fees -> UBTC.
                swap_needed = fee0_raw > 0
                swap_token_in = WHYPE
                swap_token_out = UBTC
                swap_amount_raw = fee0_raw
            elif current_tick < tick_lower:
                # Price below range: position is 100% token0 (WHYPE).
                # Need only WHYPE for increaseLiquidity. Swap UBTC fees -> WHYPE.
                swap_needed = fee1_raw > 0
                swap_token_in = UBTC
                swap_token_out = WHYPE
                swap_amount_raw = fee1_raw
            else:
                # In range: compute optimal ratio
                value_per_L = (human_price * (sqrt_upper - sqrt_price) / (sqrt_price * sqrt_upper)
                              + (sqrt_price - sqrt_lower))
                if value_per_L > 0 and (fee0 * human_price + fee1) > 0:
                    L = (fee0 * human_price + fee1) / value_per_L
                    target0 = L * (sqrt_upper - sqrt_price) / (sqrt_price * sqrt_upper)
                    target1 = L * (sqrt_price - sqrt_lower)
                    target0_raw = int(target0 * (10 ** WHYPE_DECIMALS))
                    target1_raw = int(target1 * (10 ** UBTC_DECIMALS))
                    if fee0_raw > target0_raw:
                        swap_needed = True
                        swap_token_in = WHYPE
                        swap_token_out = UBTC
                        swap_amount_raw = fee0_raw - target0_raw
                    elif fee1_raw > target1_raw:
                        swap_needed = True
                        swap_token_in = UBTC
                        swap_token_out = WHYPE
                        swap_amount_raw = fee1_raw - target1_raw

            _log_action("compound_fees_position", extra=f"tick_lower={tick_lower} tick_upper={tick_upper} fee={fee} current_tick={current_tick}")
            _log_action("compound_fees_swap_plan", extra=f"swap_needed={swap_needed} token_in={swap_token_in} token_out={swap_token_out} amount_raw={swap_amount_raw}")

            # Step 8: Execute swap if needed
            if swap_needed and swap_amount_raw > 0:
                _log_action("compound_fees_step2", extra=f"swapping {swap_token_in} -> {swap_token_out} amount_raw={swap_amount_raw}")
                self._ensure_approval(params.account, account_address,
                                      swap_token_in, SWAP_ROUTER, swap_amount_raw)

                swap_data = (
                    SELECTOR_EXACT_INPUT_SINGLE
                    + _pad_address(swap_token_in)
                    + _pad_address(swap_token_out)
                    + _pad_uint256(fee)
                    + _pad_address(account_address)
                    + _pad_uint256(deadline)
                    + _pad_uint256(swap_amount_raw)
                    + _pad_uint256(0)  # amountOutMinimum -- TODO: add slippage protection
                    + _pad_uint256(0)  # sqrtPriceLimitX96
                )
                swap_tx = self._broadcast(params.account, SWAP_ROUTER, swap_data)
                tx_hashes.append(swap_tx)
                _log_action("compound_fees_swap", tx_hash=swap_tx,
                           extra=f"in={swap_token_in} out={swap_token_out} amount={swap_amount_raw}")

                # Step 9: Wait for swap TX
                swap_receipt = self._wait_for_tx_receipt(swap_tx, timeout=120, poll_interval=2.0)
                if swap_receipt is None:
                    _log_action("compound_fees_warn", extra="swap TX not mined within timeout")
                    return tx_hashes
                if swap_receipt.get("status") != "0x1":
                    _log_action("compound_fees_warn", extra="swap TX reverted -- continuing with pre-swap balances")
                else:
                    _log_action("compound_fees_swap_confirmed", tx_hash=swap_tx)
            else:
                _log_action("compound_fees_noswap", extra="no swap needed or amount too small")

            # Step 10: Read post-swap balances and cap add amounts by actual wallet balance
            try:
                bal0_total = self._read_balance(account_address, WHYPE)
                bal1_total = self._read_balance(account_address, UBTC)
            except Exception as e:
                _log_action("compound_fees_warn", extra=f"post-swap balance read error: {e}")
                bal0_total = 0
                bal1_total = 0

            if not swap_needed or swap_amount_raw == 0:
                # No swap happened — add the collected fee amounts, capped by actual balance
                add0 = min(fee0_raw, bal0_total)
                add1 = min(fee1_raw, bal1_total)
            else:
                # Swap happened — use actual post-swap wallet balances.
                # The swap already converted fees to the optimal token ratio.
                # Cap by the total fee value to avoid adding pre-existing wallet funds:
                #   fee0_raw + fee1_raw (in raw units) is the total collected.
                #   After swap, the wallet has the swapped amounts. Just use actual balances
                #   but cap each side by the sum of original fees in that token's decimals.
                if current_tick >= tick_upper:
                    # Above range: all UBTC. Add 0 WHYPE, all available UBTC (capped by fee total)
                    total_fee_ubtc_equiv = fee1_raw + fee0_raw  # all fees converted to UBTC
                    add0 = 0
                    add1 = min(total_fee_ubtc_equiv, bal1_total)
                elif current_tick < tick_lower:
                    # Below range: all WHYPE. Add all available WHYPE (capped by fee total), 0 UBTC
                    total_fee_hype_equiv = fee0_raw + fee1_raw  # all fees converted to WHYPE
                    add0 = min(total_fee_hype_equiv, bal0_total)
                    add1 = 0
                else:
                    # In range: add actual post-swap balances (swap already optimized ratio)
                    add0 = min(fee0_raw + fee1_raw, bal0_total)  # generous cap
                    add1 = min(fee1_raw + fee0_raw, bal1_total)  # generous cap

            _log_action("compound_fees_increase_plan", extra=f"add0={add0} add1={add1}")

            if add0 <= 0 and add1 <= 0:
                _log_action("compound_fees_skip", extra=f"no balances to add after swap add0={add0} add1={add1}")
                return tx_hashes

            # Step 11: Approve PositionManager for both tokens
            for token, amount in [(WHYPE, add0), (UBTC, add1)]:
                if amount <= 0:
                    continue
                existing = self._check_allowance(account_address, token, POSITION_MANAGER)
                if existing < amount:
                    approve_data = SELECTOR_APPROVE + _pad_address(POSITION_MANAGER) + _pad_uint256(MAX_UINT256)
                    approve_tx = self._broadcast(params.account, token, approve_data)
                    tx_hashes.append(approve_tx)
                    _log_action("compound_fees_approve", tx_hash=approve_tx, extra=f"token={token}")
                    self._wait_for_tx_receipt(approve_tx, timeout=120, poll_interval=2.0)

            # Step 12: Increase liquidity
            if add0 > 0 or add1 > 0:
                _log_action("compound_fees_step3", extra=f"increasing liquidity add0={add0} add1={add1}")
                increase_data = (
                    SELECTOR_INCREASE_LIQUIDITY
                    + _pad_uint256(token_id)
                    + _pad_uint256(add0)
                    + _pad_uint256(add1)
                    + _pad_uint256(0)  # amount0Min
                    + _pad_uint256(0)  # amount1Min
                    + _pad_uint256(deadline)
                )
                increase_tx = self._broadcast(params.account, POSITION_MANAGER, increase_data)
                tx_hashes.append(increase_tx)
                _log_action("compound_fees_increase", tx_hash=increase_tx,
                           extra=f"token_id={token_id} add0={add0} add1={add1}")

                increase_receipt = self._wait_for_tx_receipt(increase_tx, timeout=120, poll_interval=2.0)
                if increase_receipt:
                    if increase_receipt.get("status") == "0x1":
                        _log_action("compound_fees_increase_confirmed", tx_hash=increase_tx)
                    else:
                        _log_action("compound_fees_increase_reverted", tx_hash=increase_tx)

            _log_action("compound_fees_done", extra=f"txs={len(tx_hashes)}")
        except Exception as e:
            _log_action("compound_fees_error", extra=f"post-collect error: {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()

        return tx_hashes

    def _parse_fees_from_receipt(self, receipt: dict, wallet_address: str) -> Tuple[int, int]:
        """Parse Transfer events from a collect tx receipt to get exact fee amounts.

        Args:
            receipt: The transaction receipt dict (contains 'logs').
            wallet_address: The recipient wallet address (lowercase hex).

        Returns:
            (fee0_raw, fee1_raw) where fee0 = WHYPE amount in wei, fee1 = UBTC amount in satoshi.
            Returns (0, 0) if no matching Transfer events are found.
        """
        TRANSFER_SIG = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
        wallet_lower = wallet_address.lower()
        fee0_raw = 0
        fee1_raw = 0

        logs = receipt.get("logs", [])
        for log in logs:
            topics = log.get("topics", [])
            if len(topics) < 3:
                continue
            if topics[0].lower() != TRANSFER_SIG:
                continue
            # topic[2] is the recipient (padded address)
            recipient = topics[2][26:].lower()  # strip 24-char zero pad
            if recipient != wallet_lower[2:]:  # compare without 0x prefix
                continue
            # This is a Transfer to our wallet
            token = log.get("address", "").lower()
            amount = int(log.get("data", "0x0"), 16)
            if token == WHYPE.lower():
                fee0_raw = amount
            elif token == UBTC.lower():
                fee1_raw = amount

        return fee0_raw, fee1_raw

    def _read_balance(self, address: str, token: str) -> int:
        """Read ERC-20 balanceOf for an address (with retry)."""
        data = SELECTOR_BALANCE_OF + _pad_address(address)
        for attempt in range(3):
            result = self._rpc_call("eth_call", [{"to": token, "data": data}, "latest"])
            if result and isinstance(result, str) and len(result) >= 66:
                try:
                    return int(result[2:66], 16)
                except (ValueError, IndexError):
                    pass
            if attempt < 2:
                time.sleep(1.0)
        _log_action("read_balance_failed", extra=f"token={token} address={address}")
        return 0

    def _read_position_data(self, token_id: int) -> Optional[Tuple[int, int, int]]:
        """Read tickLower, tickUpper, fee from positions(tokenId) (with retry)."""
        data = SELECTOR_POSITIONS + _pad_uint256(token_id)
        for attempt in range(3):
            result = self._rpc_call("eth_call", [{"to": POSITION_MANAGER, "data": data}, "latest"])
            if result and isinstance(result, str) and len(result) >= 2 + 32 * 12:
                try:
                    body = result[2:]
                    # positions() returns: nonce, operator, token0, token1,
                    # fee, tickLower, tickUpper, liquidity, ...
                    # Each field is 32 bytes (64 hex chars) in ABI encoding.
                    fee = int(body[256:320], 16)
                    tick_lower = int(body[320:384], 16)
                    if tick_lower >= 2 ** 255:
                        tick_lower -= 2 ** 256
                    tick_upper = int(body[384:448], 16)
                    if tick_upper >= 2 ** 255:
                        tick_upper -= 2 ** 256
                    return (tick_lower, tick_upper, fee)
                except (ValueError, IndexError):
                    pass
            if attempt < 2:
                time.sleep(1.0)
        _log_action("read_position_failed", extra=f"token_id={token_id}")
        return None

    def _read_sqrt_price_x96(self, pool_address: str) -> Optional[int]:
        """Read sqrtPriceX96 from pool slot0 (with retry)."""
        for attempt in range(3):
            result = self._rpc_call("eth_call", [{"to": pool_address, "data": "0x3850c7bd"}, "latest"])
            if result and isinstance(result, str) and len(result) >= 66:
                try:
                    val = int(result[2:66], 16)
                    if val > 0:
                        return val
                except (ValueError, IndexError):
                    pass
            if attempt < 2:
                time.sleep(1.0)
        _log_action("read_sqrt_price_failed", extra=f"pool={pool_address}")
        return None


    def close_position(self, position_id: str, account: str) -> List[str]:
        """Close a position completely: decreaseLiquidity(100%) + collect().

        Args:
            position_id: The position identifier (e.g. 'hyperevm:12345').
            account: The vault account name.

        Returns:
            A list of transaction hashes.
            - [decrease_tx, collect_tx] on full success
            - [decrease_tx] if decrease succeeded but collect failed (partial close)
            - [collect_tx] if liquidity was already 0 (just fees to collect)
        """
        token_id = self._parse_token_id(position_id)
        if token_id is None:
            raise ValueError(f"Invalid position_id: {position_id}")

        # Read current liquidity from the position
        liquidity = self._get_position_liquidity(token_id)
        if liquidity == 0:
            # Position may already be closed; just collect any remaining fees
            collect_tx = self.collect_fees(CollectFeesParams(
                account=account, position_id=position_id
            ))
            return [collect_tx]

        # 1. Decrease liquidity by 100%
        decrease_tx = self.decrease_liquidity(DecreaseLiquidityParams(
            account=account,
            position_id=position_id,
            liquidity=liquidity,
        ))

        # 2. Collect all tokens (fees + withdrawn liquidity)
        # If this fails after decreaseLiquidity succeeded, the position is
        # half-closed — liquidity removed but funds still owed by the NFT.
        # Return partial result so the caller knows to retry collect separately.
        try:
            collect_tx = self.collect_fees(CollectFeesParams(
                account=account, position_id=position_id
            ))
            _log_action("close_position",
                         extra=f"token_id={token_id} "
                               f"decrease={decrease_tx} collect={collect_tx}")
            return [decrease_tx, collect_tx]
        except Exception as e:
            _log_action("close_position_partial",
                         extra=f"token_id={token_id} decrease={decrease_tx} "
                               f"collect_failed={e}")
            print(f"[close_position] WARNING: decreaseLiquidity succeeded (tx={decrease_tx}) "
                  f"but collect failed: {e}. Liquidity removed but funds still in position. "
                  f"Retry 'Collect Fees' to withdraw remaining funds.")
            return [decrease_tx]

    def rebalance(self, params: RebalanceParams) -> List[str]:
        """Full rebalance flow: close -> optional swap -> open.

        Flow:
          1. Close the existing position (decreaseLiquidity + collect)
          2. If token ratios need swapping, execute swap (STUB for now)
          3. Open a new position with the new tick range

        Args:
            params: RebalanceParams with position_id, new ticks, account.

        Returns:
            A list of transaction hashes.
        """
        tx_hashes: List[str] = []

        # 1. Close the existing position
        close_txs = self.close_position(params.position_id, params.account)
        tx_hashes.extend(close_txs)

        # 2. Swap if needed -- currently stubbed
        # In a full implementation, we would:
        #   - Read the token balances after closing
        #   - Compare to the desired ratio for the new range
        #   - Swap the excess token for the deficit token
        # For now, we assume the user has the right token ratio

        # 3. Open a new position with the new range
        # We need token0, token1, and fee from the old position
        # For the WHYPE/UBTC pool, we use the known constants
        open_tx, new_token_id = self.open_position(OpenPositionParams(
            account=params.account,
            token0=WHYPE,
            token1=UBTC,
            fee=3000,
            tick_lower=params.new_tick_lower,
            tick_upper=params.new_tick_upper,
            amount0=0,  # Amounts would need to be computed from closed position
            amount1=0,
            slippage_pct=params.slippage_pct,
            deadline_seconds=params.deadline_seconds,
        ))
        tx_hashes.append(open_tx)

        _log_action("rebalance",
                     extra=f"old_token_id={self._parse_token_id(params.position_id)} "
                           f"new_token_id={new_token_id}")
        return tx_hashes

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _parse_token_id(self, position_id: str) -> Optional[int]:
        """Parse a token ID from a position_id string.

        Accepts formats:
          - 'hyperevm:12345' -> 12345
          - '12345' -> 12345

        Args:
            position_id: The position identifier string.

        Returns:
            The token ID as an integer, or None if unparseable.
        """
        try:
            if position_id.startswith("hyperevm:"):
                return int(position_id.split(":", 1)[1])
            return int(position_id)
        except (ValueError, IndexError):
            return None