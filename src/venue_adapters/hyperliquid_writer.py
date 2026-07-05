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

# Token decimals
WHYPE_DECIMALS = 18
UBTC_DECIMALS = 8

# ERC-20 / Uniswap V3 function selectors (keccak256 first 4 bytes)
SELECTOR_APPROVE = "0x095ea7b3"       # approve(address,uint256)
SELECTOR_TRANSFER = "0xa9059cbb"      # transfer(address,uint256)
SELECTOR_BALANCE_OF = "0x70a08231"    # balanceOf(address)
SELECTOR_ALLOWANCE = "0xdd62ed3e"     # allowance(address,address)
SELECTOR_WITHDRAW = "0x2e1a7d4d"      # withdraw(uint256) -- WETH style
SELECTOR_MINT = "0x88316456"          # mint(MintParams) -- NonfungiblePositionManager
SELECTOR_INCREASE_LIQUIDITY = "0xf517cdcd"  # increaseLiquidity(IncreaseLiquidityParams)
SELECTOR_DECREASE_LIQUIDITY = "0x2b35bfce"  # decreaseLiquidity(DecreaseLiquidityParams)
SELECTOR_COLLECT = "0x4060c58e"       # collect(CollectParams)
SELECTOR_POSITIONS = "0x99fbab88"     # positions(uint256)

# Default gas limit for LP operations (can be overridden)
DEFAULT_GAS_LIMIT = 300000
MAX_UINT256 = 2**256 - 1


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

    def _broadcast(self, account: str, to: str, data: str, value: str = "0") -> str:
        """Sign and broadcast a transaction via the agent. Returns tx hash.

        Args:
            account: The vault account name (e.g. 'G5').
            to: The recipient contract address (0x-prefixed).
            data: The calldata hex string (0x-prefixed).
            value: The value in wei as a decimal string (default '0').

        Returns:
            The transaction hash.
        """
        result = self._agent_call(
            "broadcast_tx",
            account=account,
            chain="EVM",
            to=to,
            data=data,
            value=value,
            chain_id=self.chain_id,
            rpc=self.rpc_url,
        )
        tx_hash = result.get("tx_hash", "")
        _log_action("broadcast", tx_hash=tx_hash, extra=f"to={to}")
        return tx_hash

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
        if not result or not isinstance(result, str) or len(result) < 2 + 32 * 7:
            return 0
        try:
            # liquidity is the 7th 32-byte word (offset 448-512 in the body)
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
        """Execute a single-pool exact-input swap.

        NOTE: The HyperEVM swap router address has not been confirmed yet.
        This method is stubbed out until the router contract is verified.

        Args:
            params: SwapParameters including token_in, token_out, amount_in.

        Raises:
            NotImplementedError: Always, until the swap router is confirmed.
        """
        raise NotImplementedError(
            "HyperEVM swap router address has not been confirmed yet. "
            "This functionality will be implemented once the router contract "
            "address is verified on HyperEVM."
        )

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
        # Use max uint256 to collect all fees
        data = (
            SELECTOR_COLLECT
            + _pad_uint256(token_id)
            + _pad_address(recipient)
            + _pad_uint256(MAX_UINT256)  # amount0Max
            + _pad_uint256(MAX_UINT256)  # amount1Max
        )

        tx_hash = self._broadcast(params.account, POSITION_MANAGER, data)
        _log_action("collect_fees", tx_hash=tx_hash, extra=f"token_id={token_id}")
        return tx_hash

    def close_position(self, position_id: str, account: str) -> List[str]:
        """Close a position completely: decreaseLiquidity(100%) + collect().

        Args:
            position_id: The position identifier (e.g. 'hyperevm:12345').
            account: The vault account name.

        Returns:
            A list of transaction hashes [decrease_tx, collect_tx].
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
        collect_tx = self.collect_fees(CollectFeesParams(
            account=account, position_id=position_id
        ))

        _log_action("close_position",
                     extra=f"token_id={token_id} "
                           f"decrease={decrease_tx} collect={collect_tx}")
        return [decrease_tx, collect_tx]

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