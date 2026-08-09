"""Aerodrome Venue Writer - ColdStack LP Engine v5.2.2

Write operations for Aerodrome SlipStream V3 LP positions on BASE.
All signing is delegated to the key_manager_agent via HTTP — the writer
never touches private keys directly.

Version: v5.2.2 (August 2026) - collect_fees + close_position (minimal)
"""
import json
import time
import urllib.request
import urllib.error
from typing import Any, Dict, List, Optional

from venue_adapters.venue_writer import (
    VenueWriter, CollectFeesParams, CompoundFeesParams,
    DecreaseLiquidityParams, IncreaseLiquidityParams,
    OpenPositionParams, RebalanceParams, SwapParams,
)
from venue_adapters.aerodrome_adapter import (
    BASE_RPC_URL, BASE_RPC_FALLBACK, BASE_CHAIN_ID,
    AERO_SLIPSTREAM_POSITION_MANAGER, AERO_SLIPSTREAM_POSITION_MANAGER_2,
    V3_POSITION_MANAGERS,
    SELECTOR_COLLECT, SELECTOR_POSITIONS, SELECTOR_BALANCE_OF,
    SELECTOR_DECREASE_LIQUIDITY, SELECTOR_OWNER_OF,
    _pad_int_to_64, _pad_address, _base_rpc_call,
    _decode_address,
)


MAX_UINT128 = (1 << 128) - 1


class AerodromeWriter(VenueWriter):
    """VenueWriter for BASE Aerodrome SlipStream V3 LP positions.

    Signs transactions via the key_manager_agent (same agent as HyperEVM/BSC,
    but with chain_id=8453 and BASE RPC URLs).
    """

    VENUE_KEY = "aerodrome"

    def __init__(self, agent_url: str = "http://127.0.0.1:8842"):
        self.agent_url = agent_url.rstrip("/")
        self.chain_id = BASE_CHAIN_ID  # 8453
        self.rpc_url = BASE_RPC_URL
        self._unlocked = False

    # ------------------------------------------------------------------
    # Agent communication
    # ------------------------------------------------------------------

    def _agent_call(self, cmd: str, **params) -> dict:
        """Send a command to the key_manager_agent and return the response."""
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

    def _rpc_call(self, method: str, params: list) -> Optional[Any]:
        """Make a single BASE JSON-RPC call. Returns the 'result' field."""
        return _base_rpc_call(method, params)

    def is_available(self) -> bool:
        """Check if the key_manager_agent is reachable and unlocked."""
        try:
            result = self._agent_call("status")
            unlocked = result.get("unlocked", False) if isinstance(result, dict) else False
            self._unlocked = unlocked
            return unlocked
        except Exception:
            self._unlocked = False
            return False

    def unlock(self, credentials: Dict[str, Any]) -> bool:
        """Unlock the writer. The agent handles password/vault unlocking."""
        password = credentials.get("password", "")
        if not password:
            return self.is_available()
        if self.is_available():
            return True
        return False

    def _get_account_address(self, account: str) -> str:
        """Get the EVM address for a vault account name."""
        result = self._agent_call("get_address", account=account, chain="EVM")
        if isinstance(result, list) and result:
            return result[0].get("address", "")
        if isinstance(result, dict):
            return result.get("address", "")
        return str(result)

    def _read_native_balance(self, address: str) -> int:
        """Read ETH balance (in wei) for an address on BASE."""
        result = self._rpc_call("eth_getBalance", [address, "latest"])
        if result and isinstance(result, str):
            return int(result, 16)
        return 0

    def _broadcast(self, account: str, to: str, data: str, value: str = "0") -> str:
        """Sign and broadcast a transaction on BASE via the agent. Returns tx hash."""
        vault_address = self._get_account_address(account)
        gas_balance = self._read_native_balance(vault_address)
        if gas_balance == 0:
            raise RuntimeError(
                f"Insufficient gas: wallet {vault_address} has 0 ETH. "
                f"Send ETH to this address to pay for transaction gas on BASE."
            )

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
        if isinstance(result, dict):
            tx_hash = result.get("tx_hash", "")
        else:
            tx_hash = str(result)
        if not tx_hash:
            raise RuntimeError("Agent returned empty tx hash")
        return tx_hash

    def _wait_for_tx_receipt(self, tx_hash: str, timeout: int = 120, poll_interval: float = 2.0) -> Optional[dict]:
        """Wait for a transaction to be mined. Returns the receipt dict or None."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            result = self._rpc_call("eth_getTransactionReceipt", [tx_hash])
            receipt: Optional[dict] = None
            if isinstance(result, dict):
                receipt = result
            elif isinstance(result, str):
                try:
                    parsed = json.loads(result)
                    if isinstance(parsed, dict):
                        receipt = parsed
                except (json.JSONDecodeError, TypeError):
                    pass
            if receipt and receipt.get("status") is not None:
                return receipt
            time.sleep(poll_interval)
        return None

    def _parse_token_id(self, position_id: str) -> Optional[int]:
        """Parse a token ID from a position_id string.

        Accepts formats:
          - 'base:12345' -> 12345
          - '12345' -> 12345
        """
        try:
            if position_id.startswith("base:"):
                return int(position_id.split(":", 1)[1])
            if position_id.startswith("hyperevm:"):
                return int(position_id.split(":", 1)[1])
            return int(position_id)
        except (ValueError, IndexError):
            return None

    def _find_position_manager(self, token_id: int) -> str:
        """Find which Position Manager holds this NFT on BASE."""
        for pm in V3_POSITION_MANAGERS:
            data = SELECTOR_POSITIONS + _pad_int_to_64(token_id)
            result = _base_rpc_call("eth_call", [{"to": pm, "data": data}, "latest"])
            if result and isinstance(result, str) and len(result) >= 2 + 32 * 13:
                body = result[2:]
                nonce = int(body[0:64], 16)
                liquidity = int(body[448:512], 16)
                if nonce > 0 or liquidity > 0:
                    return pm
        return AERO_SLIPSTREAM_POSITION_MANAGER

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    def collect_fees(self, params: CollectFeesParams) -> str:
        """Collect accrued fees for a BASE Aerodrome SlipStream V3 LP position.

        Args:
            params: CollectFeesParams with position_id (format: 'base:<token_id>'), account.

        Returns:
            The transaction hash.
        """
        token_id = self._parse_token_id(params.position_id)
        if token_id is None:
            raise ValueError(f"Invalid position_id: {params.position_id}")

        recipient = params.recipient
        if not recipient:
            recipient = self._get_account_address(params.account)

        position_manager = self._find_position_manager(token_id)

        data = (
            SELECTOR_COLLECT
            + _pad_int_to_64(token_id)
            + _pad_address(recipient)
            + _pad_int_to_64(MAX_UINT128)
            + _pad_int_to_64(MAX_UINT128)
        )

        tx_hash = self._broadcast(params.account, position_manager, data)
        print(f"[aerodrome-writer] collect_fees: token_id={token_id}, pm={position_manager}, tx={tx_hash}")
        return tx_hash

    def close_position(self, position_id: str, account: str) -> List[str]:
        """Close a BASE Aerodrome SlipStream V3 LP position: decrease liquidity (100%) + collect fees.

        Returns a list of transaction hashes.
        """
        token_id = self._parse_token_id(position_id)
        if token_id is None:
            raise ValueError(f"Invalid position_id: {position_id}")

        position_manager = self._find_position_manager(token_id)
        tx_hashes: List[str] = []

        data = SELECTOR_POSITIONS + _pad_int_to_64(token_id)
        result = _base_rpc_call("eth_call", [{"to": position_manager, "data": data}, "latest"])
        if not result or not isinstance(result, str) or len(result) < 2 + 32 * 13:
            raise RuntimeError(f"Could not read position {token_id}")

        body = result[2:]
        liquidity = int(body[448:512], 16)

        if liquidity > 0:
            decrease_data = (
                SELECTOR_DECREASE_LIQUIDITY
                + _pad_int_to_64(token_id)
                + _pad_int_to_64(liquidity)
                + _pad_int_to_64(0)
                + _pad_int_to_64(0)
                + _pad_int_to_64(int(time.time() + 1200))
            )
            tx1 = self._broadcast(account, position_manager, decrease_data)
            tx_hashes.append(tx1)
            self._wait_for_tx_receipt(tx1, timeout=60)

        collect_tx = self.collect_fees(CollectFeesParams(
            account=account,
            position_id=position_id,
        ))
        tx_hashes.append(collect_tx)

        return tx_hashes

    # ------------------------------------------------------------------
    # Stubs for operations not yet implemented on BASE
    # ------------------------------------------------------------------

    def wrap_native(self, account: str, amount: float) -> str:
        raise NotImplementedError("Aerodrome wrap_native not yet implemented")

    def unwrap_native(self, account: str, amount: float) -> str:
        raise NotImplementedError("Aerodrome unwrap_native not yet implemented")

    def approve(self, account: str, token: str, spender: str, amount: float) -> str:
        raise NotImplementedError("Aerodrome approve not yet implemented")

    def swap(self, params: SwapParams) -> str:
        raise NotImplementedError("Aerodrome swap not yet implemented")

    def open_position(self, params: OpenPositionParams) -> tuple[str, Optional[int]]:
        raise NotImplementedError("Aerodrome open_position not yet implemented")

    def increase_liquidity(self, params: IncreaseLiquidityParams) -> str:
        raise NotImplementedError("Aerodrome increase_liquidity not yet implemented")

    def decrease_liquidity(self, params: DecreaseLiquidityParams) -> str:
        raise NotImplementedError("Aerodrome decrease_liquidity not yet implemented")

    def compound_fees(self, params: CompoundFeesParams) -> List[str]:
        raise NotImplementedError("Aerodrome compound_fees not yet implemented (use collect_fees)")

    def rebalance(self, params: RebalanceParams) -> List[str]:
        raise NotImplementedError("Aerodrome rebalance not yet implemented")
