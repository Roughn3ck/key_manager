"""BSC Venue Writer - ColdStack LP Engine v5.2.1

Write operations for BSC V3 LP positions (Uniswap V3 + PancakeSwap V3 on BNB Chain).
All signing is delegated to the key_manager_agent via HTTP — the writer
never touches private keys directly.

Version: v5.2.1 (August 2026) - collect_fees + close_position (minimal)
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
from venue_adapters.bsc_adapter import (
    BSC_RPC_URL, BSC_CHAIN_ID,
    PANCAKE_V3_POSITION_MANAGER,
    V3_POSITION_MANAGERS,
    SELECTOR_COLLECT, SELECTOR_POSITIONS,
    SELECTOR_DECREASE_LIQUIDITY, SELECTOR_OWNER_OF,
    _pad_int_to_64, _pad_address, _bsc_rpc_call,
    _decode_address, _get_token_symbol,
)

SELECTOR_BURN = "0x42966c68"  # burn(uint256 tokenId)



MAX_UINT128 = (1 << 128) - 1


class BSCWriter(VenueWriter):
    """VenueWriter for BSC V3 LP positions.

    Signs transactions via the key_manager_agent (same agent as HyperEVM,
    but with chain_id=56 and BSC RPC URLs).
    """

    VENUE_KEY = "bsc"

    def __init__(self, agent_url: str = "http://127.0.0.1:8842"):
        self.agent_url = agent_url.rstrip("/")
        self.chain_id = BSC_CHAIN_ID  # 56
        # v5.3.28: rpc_url is resolved dynamically from the rotated config list
        # via _bsc_rpc_urls(); this remains a backward-compatible default.
        self.rpc_url = BSC_RPC_URL
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
        """Make a single BSC JSON-RPC call. Returns the 'result' field."""
        return _bsc_rpc_call(method, params)

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
        """Get the EVM address for a vault account name on BSC (chain_id 56)."""
        result = self._agent_call("get_address", account=account, chain="EVM", chain_id=self.chain_id)
        if isinstance(result, list) and result:
            return result[0].get("address", "")
        if isinstance(result, dict):
            return result.get("address", "")
        return str(result)

    def _get_position_owner(self, token_id: int, position_manager: str) -> Optional[str]:
        """Read ownerOf(tokenId) from the BSC position manager (read-only)."""
        data = SELECTOR_OWNER_OF + _pad_int_to_64(token_id)
        result = _bsc_rpc_call("eth_call", [{"to": position_manager, "data": data}, "latest"])
        if result and isinstance(result, str) and len(result) >= 66:
            return "0x" + result[-40:]
        return None

    def _resolve_owner_signer(self, token_id: int, position_manager: str) -> str:
        """Resolve the vault account that signs this BSC close by matching
        ownerOf(tokenId) to a derivable BSC address — never a default/selected
        account."""
        owner = self._get_position_owner(token_id, position_manager)
        if not owner:
            raise RuntimeError(
                f"Could not read ownerOf({token_id}) on {position_manager} — "
                "cannot resolve the close signer."
            )
        owner_l = owner.lower()
        accounts = self._agent_call("list_accounts")
        accounts = accounts.get("result", accounts) if isinstance(accounts, dict) else accounts
        derivable = []
        if isinstance(accounts, dict):
            for name, data in accounts.items():
                for addr in (data or {}).get("addresses", []):
                    a = (addr.get("address") or "").lower()
                    if a.startswith("0x"):
                        derivable.append((name, a))
                        if a == owner_l:
                            return name
        addrs = ", ".join(sorted({a for _, a in derivable})) or "(none)"
        raise RuntimeError(
            f"position owner {owner} matches no account in this vault for BSC "
            f"(derivable: {addrs})."
        )

    def _read_native_balance(self, address: str) -> int:
        """Read BNB balance (in wei) for an address on BSC."""
        result = self._rpc_call("eth_getBalance", [address, "latest"])
        if result and isinstance(result, str):
            return int(result, 16)
        return 0

    def _broadcast(self, account: str, to: str, data: str, value: str = "0") -> str:
        """Sign and broadcast a transaction on BSC via the agent. Returns tx hash."""
        # v5.3.28: pick a healthy BSC RPC from the rotated config list. If the
        # primary fails during estimate/broadcast, the agent rotates internally;
        # we still verify the one we hand to the agent.
        from venue_adapters.bsc_adapter import _bsc_rpc_urls
        bsc_rpc_urls = _bsc_rpc_urls()
        rpc_url = bsc_rpc_urls[0] if bsc_rpc_urls else self.rpc_url

        # v5.3.17: writer-side chain guard. Gas-balance read and agent broadcast
        # must use the same chain-verified RPC.
        self._verify_rpc_chain(rpc_url, self.chain_id)

        # Pre-flight: check BNB gas balance
        vault_address = self._get_account_address(account)
        gas_balance = self._read_native_balance(vault_address)
        if gas_balance == 0:
            raise RuntimeError(
                f"Insufficient gas: wallet {vault_address} has 0 BNB. "
                f"Send BNB to this address to pay for transaction gas on BSC."
            )

        # Re-verify before the agent broadcast.
        self._verify_rpc_chain(rpc_url, self.chain_id)

        result = self._agent_call(
            "broadcast_tx",
            account=account,
            chain="EVM",
            to=to,
            data=data,
            value=value,
            chain_id=self.chain_id,
            rpc=rpc_url,
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
          - 'bsc:12345' -> 12345
          - '12345' -> 12345
        """
        try:
            if position_id.startswith("bsc:"):
                return int(position_id.split(":", 1)[1])
            if position_id.startswith("hyperevm:"):
                return int(position_id.split(":", 1)[1])
            return int(position_id)
        except (ValueError, IndexError):
            return None

    def _find_position_manager(self, token_id: int) -> str:
        """Find which Position Manager holds this NFT on BSC."""
        for pm in V3_POSITION_MANAGERS:
            data = SELECTOR_POSITIONS + _pad_int_to_64(token_id)
            result = _bsc_rpc_call("eth_call", [{"to": pm, "data": data}, "latest"])
            if result and isinstance(result, str) and len(result) >= 2 + 32 * 13:
                body = result[2:]
                nonce = int(body[0:64], 16)
                liquidity = int(body[448:512], 16)
                if nonce > 0 or liquidity > 0:
                    return pm
        # Default to PancakeSwap V3 if not found
        return PANCAKE_V3_POSITION_MANAGER

    def _get_position_liquidity(self, token_id: int) -> int:
        """Read the current liquidity of a position from its Position Manager.

        Args:
            token_id: The NFT token ID of the position.

        Returns:
            The liquidity amount as an integer, or 0 if unreadable.
        """
        position_manager = self._find_position_manager(token_id)
        data = SELECTOR_POSITIONS + _pad_int_to_64(token_id)
        result = _bsc_rpc_call("eth_call", [{"to": position_manager, "data": data}, "latest"])
        if not result or not isinstance(result, str) or len(result) < 2 + 32 * 13:
            return 0
        try:
            body = result[2:]
            return int(body[448:512], 16)
        except (ValueError, IndexError):
            return 0

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    def collect_fees(self, params: CollectFeesParams) -> str:
        """Collect accrued fees for a BSC V3 LP position.

        Args:
            params: CollectFeesParams with position_id (format: 'bsc:<token_id>'), account.

        Returns:
            The transaction hash.
        """
        token_id = self._parse_token_id(params.position_id)
        if token_id is None:
            raise ValueError(f"Invalid position_id: {params.position_id}")

        recipient = params.recipient
        if not recipient:
            recipient = self._get_account_address(params.account)

        # Find which Position Manager holds this NFT
        position_manager = self._find_position_manager(token_id)

        # Encode collect((uint256,address,uint128,uint128))
        data = (
            SELECTOR_COLLECT
            + _pad_int_to_64(token_id)
            + _pad_address(recipient)
            + _pad_int_to_64(MAX_UINT128)  # amount0Max
            + _pad_int_to_64(MAX_UINT128)  # amount1Max
        )

        tx_hash = self._broadcast(params.account, position_manager, data)
        print(f"[bsc-writer] collect_fees: token_id={token_id}, pm={position_manager}, tx={tx_hash}")
        return tx_hash

    def _get_position_state(self, token_id: int, position_manager: str) -> tuple:
        """Read current liquidity and tokensOwed from positions(tokenId)."""
        data = SELECTOR_POSITIONS + _pad_int_to_64(token_id)
        result = _bsc_rpc_call("eth_call", [{"to": position_manager, "data": data}, "latest"])
        if not result or not isinstance(result, str) or len(result) < 2 + 32 * 13:
            return (0, 0, 0)
        body = result[2:]
        liquidity = int(body[448:512], 16)
        owed0 = int(body[512:576], 16) if len(body) >= 576 else 0
        owed1 = int(body[576:640], 16) if len(body) >= 640 else 0
        return (liquidity, owed0, owed1)

    def close_position(self, position_id: str, account: str) -> List[str]:
        """Close a BSC V3 LP position: decrease liquidity (100%) + collect fees.

        v5.3.28: after decrease+collect, read the position back. If liquidity
        and tokensOwed are both zero, burn the empty NFT and report the
        confirmed end state. Signing is delegated to the key_manager_agent.

        Returns a list of transaction hashes.
        """
        token_id = self._parse_token_id(position_id)
        if token_id is None:
            raise ValueError(f"Invalid position_id: {position_id}")

        position_manager = self._find_position_manager(token_id)
        tx_hashes: List[str] = []

        # Read full position state once for tokens/decimals.
        data = SELECTOR_POSITIONS + _pad_int_to_64(token_id)
        result = _bsc_rpc_call("eth_call", [{"to": position_manager, "data": data}, "latest"])
        if not result or not isinstance(result, str) or len(result) < 2 + 32 * 13:
            raise RuntimeError(f"Could not read position {token_id}")

        body = result[2:]
        token0 = _decode_address(body[128:192]).lower()
        token1 = _decode_address(body[192:256]).lower()
        fee = int(body[256:320], 16)
        liquidity = int(body[448:512], 16)

        recipient = self._get_account_address(account)
        if not recipient:
            raise RuntimeError(f"Could not resolve BSC address for account '{account}'")

        # v5.3.28: pace consecutive close-sequence calls to the same endpoint.
        _step_delay = 1.0

        if liquidity > 0:
            time.sleep(_step_delay)
            decrease_data = (
                SELECTOR_DECREASE_LIQUIDITY
                + _pad_int_to_64(token_id)
                + _pad_int_to_64(liquidity)
                + _pad_int_to_64(0)  # amount0Min
                + _pad_int_to_64(0)  # amount1Min
                + _pad_int_to_64(int(time.time() + 1200))  # deadline
            )
            tx1 = self._broadcast(account, position_manager, decrease_data)
            tx_hashes.append(tx1)
            self._wait_for_tx_receipt(tx1, timeout=60)

        time.sleep(_step_delay)
        collect_tx = self.collect_fees(CollectFeesParams(
            account=account,
            position_id=position_id,
        ))
        tx_hashes.append(collect_tx)
        self._wait_for_tx_receipt(collect_tx, timeout=60)

        # Burn the empty NFT if the position is now fully drained.
        burn_sig = None
        try:
            time.sleep(_step_delay)
            post_liq, post_owed0, post_owed1 = self._get_position_state(token_id, position_manager)
            if post_liq == 0 and post_owed0 == 0 and post_owed1 == 0:
                time.sleep(_step_delay)
                burn_data = SELECTOR_BURN + _pad_int_to_64(token_id)
                burn_sig = self._broadcast(account, position_manager, burn_data)
                tx_hashes.append(burn_sig)
                self._wait_for_tx_receipt(burn_sig, timeout=60)
                print(f"[bsc-writer] burn ok: token_id={token_id}, tx={burn_sig}")
            else:
                print(
                    f"[bsc-writer] skip burn: token_id={token_id}, "
                    f"liq={post_liq}, owed0={post_owed0}, owed1={post_owed1}"
                )
        except Exception as e:
            print(f"[bsc-writer] burn failed (non-fatal): {e}")

        print(
            f"[bsc-writer] close_position: token_id={token_id}, pm={position_manager}, "
            f"token0={_get_token_symbol(token0)}({token0}), token1={_get_token_symbol(token1)}({token1}), "
            f"fee={fee}, txs={tx_hashes}, end_state={'burned' if burn_sig else 'open'}"
        )
        return tx_hashes


    # ------------------------------------------------------------------
    # Stubs for operations not yet implemented on BSC
    # ------------------------------------------------------------------

    def wrap_native(self, account: str, amount: float) -> str:
        raise NotImplementedError("BSC wrap_native not yet implemented")

    def unwrap_native(self, account: str, amount: float) -> str:
        raise NotImplementedError("BSC unwrap_native not yet implemented")

    def approve(self, account: str, token: str, spender: str, amount: float) -> str:
        raise NotImplementedError("BSC approve not yet implemented")

    def swap(self, params: SwapParams) -> str:
        raise NotImplementedError("BSC swap not yet implemented")

    def open_position(self, params: OpenPositionParams) -> tuple[str, Optional[int]]:
        raise NotImplementedError("BSC open_position not yet implemented")

    def increase_liquidity(self, params: IncreaseLiquidityParams) -> str:
        raise NotImplementedError("BSC increase_liquidity not yet implemented")

    def decrease_liquidity(self, params: DecreaseLiquidityParams) -> str:
        raise NotImplementedError("BSC decrease_liquidity not yet implemented")

    def compound_fees(self, params: CompoundFeesParams) -> List[str]:
        raise NotImplementedError("BSC compound_fees not yet implemented (use collect_fees)")

    def rebalance(self, params: RebalanceParams) -> List[str]:
        raise NotImplementedError("BSC rebalance not yet implemented")

