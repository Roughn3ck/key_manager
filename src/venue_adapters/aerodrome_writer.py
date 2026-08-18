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
    SELECTOR_GET_REWARD,
    SELECTOR_INCREASE_LIQUIDITY,
    SELECTOR_TOKEN0, SELECTOR_TOKEN1, SELECTOR_SLOT0,
    SELECTOR_DECIMALS, SELECTOR_SYMBOL,
    _pad_int_to_64, _pad_address, _base_rpc_call,
    _decode_address, _decode_int24,
    _get_gauge_address_for_position,
    _get_token_decimals, _get_token_symbol,
    _fetch_pool_state,
    _pool_for_token_ids,
    AERO_TOKEN,
)


MAX_UINT128 = (1 << 128) - 1

# SlipStream SwapRouter on BASE
AERO_SWAP_ROUTER = "0xc5ed88eE30B3BbfE87E1B57Bf4632c69E45B26B7"
SELECTOR_EXACT_INPUT_SINGLE = "0x414bf389"
SELECTOR_APPROVE = "0x095ea7b3"
SELECTOR_BALANCE_OF_ERC20 = "0x70a08231"
SELECTOR_MULTICALL = "0xac9650d8"


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

    def _broadcast(self, account: str, to: str, data: str, value: str = "0",
                   gas_check_address: str = "") -> str:
        """Sign and broadcast a transaction on BASE via the agent. Returns tx hash.

        Args:
            account: Vault account name for signing.
            to: Contract address to call.
            data: Encoded transaction data.
            value: ETH value to send (hex string).
            gas_check_address: Optional wallet address to check for gas balance.
                If provided, uses this address instead of resolving from account.
                This is important when the account has multiple EVM addresses
                (e.g. one for Ethereum mainnet, one for Base) — we need to check
                the Base address specifically.
        """
        if gas_check_address:
            vault_address = gas_check_address
        else:
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

        For UNSTAKED positions: calls collect() on the NonfungiblePositionManager.
        For STAKED positions: calls getReward(tokenId) on the CL gauge to claim
        AERO emissions. Trading fees on staked positions go to veAERO voters
        and cannot be claimed by the staker.

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

        # Check if position is staked (NFT owned by a gauge)
        gauge_address = _get_gauge_address_for_position(
            token_id, position_manager, recipient
        )

        if gauge_address:
            # STAKED: Call getReward(uint256 tokenId) on the gauge
            # This claims AERO emissions to the caller (wallet)
            data = SELECTOR_GET_REWARD + _pad_int_to_64(token_id)
            tx_hash = self._broadcast(params.account, gauge_address, data,
                                      gas_check_address=recipient)
            print(f"[aerodrome-writer] collect_fees (staked): token_id={token_id}, "
                  f"gauge={gauge_address}, tx={tx_hash}")
            return tx_hash
        else:
            # UNSTAKED: Call collect() on the PositionManager
            data = (
                SELECTOR_COLLECT
                + _pad_int_to_64(token_id)
                + _pad_address(recipient)
                + _pad_int_to_64(MAX_UINT128)
                + _pad_int_to_64(MAX_UINT128)
            )
            tx_hash = self._broadcast(params.account, position_manager, data,
                                      gas_check_address=recipient)
            print(f"[aerodrome-writer] collect_fees (unstaked): token_id={token_id}, "
                  f"pm={position_manager}, tx={tx_hash}")
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

        # Resolve recipient address once for gas checks in this flow
        recipient = self._get_account_address(account)

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
            tx1 = self._broadcast(account, position_manager, decrease_data,
                                  gas_check_address=recipient)
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
        """Compound fees for a BASE Aerodrome SlipStream V3 LP position.

        For STAKED positions:
          1. Call getReward(tokenId) on gauge to claim AERO emissions
          2. (Optional future: swap AERO → token0/token1)
          3. Increase liquidity with the token balances held by the wallet

        For UNSTAKED positions:
          1. Call collect() on PositionManager to claim trading fees
          2. Increase liquidity with the collected amounts

        Returns:
            List of transaction hashes for all transactions submitted.
        """
        import math as _math
        tx_hashes: List[str] = []
        token_id = self._parse_token_id(params.position_id)
        if token_id is None:
            raise ValueError(f"Invalid position_id: {params.position_id}")

        account_address = self._get_account_address(params.account)
        position_manager = self._find_position_manager(token_id)
        deadline = int(time.time()) + params.deadline_seconds

        # Read position data
        data = SELECTOR_POSITIONS + _pad_int_to_64(token_id)
        result = _base_rpc_call("eth_call", [{"to": position_manager, "data": data}, "latest"])
        if not result or not isinstance(result, str) or len(result) < 2 + 32 * 13:
            raise RuntimeError(f"Could not read position {token_id}")

        body = result[2:]
        token0 = _decode_address(body[128:192]).lower()
        token1 = _decode_address(body[192:256]).lower()
        fee_tier = int(body[256:320], 16)
        tick_lower = _decode_int24(body[320:384])
        tick_upper = _decode_int24(body[384:448])
        liquidity = int(body[448:512], 16)
        decimals0 = _get_token_decimals(token0)
        decimals1 = _get_token_decimals(token1)
        symbol0 = _get_token_symbol(token0)
        symbol1 = _get_token_symbol(token1)

        # Check if staked
        gauge_address = _get_gauge_address_for_position(
            token_id, position_manager, account_address
        )

        # Step 1: Collect fees / claim rewards
        if gauge_address:
            # STAKED: Call getReward(tokenId) on gauge
            print(f"[aerodrome-compound] Step 1: getReward on gauge {gauge_address}")
            collect_data = SELECTOR_GET_REWARD + _pad_int_to_64(token_id)
            collect_tx = self._broadcast(params.account, gauge_address, collect_data,
                                       gas_check_address=account_address)
            tx_hashes.append(collect_tx)
        else:
            # UNSTAKED: Call collect() on PositionManager
            print(f"[aerodrome-compound] Step 1: collect on PositionManager")
            collect_data = (
                SELECTOR_COLLECT
                + _pad_int_to_64(token_id)
                + _pad_address(account_address)
                + _pad_int_to_64(MAX_UINT128)
                + _pad_int_to_64(MAX_UINT128)
            )
            collect_tx = self._broadcast(params.account, position_manager, collect_data,
                                       gas_check_address=account_address)
            tx_hashes.append(collect_tx)

        # Wait for collect TX to be mined
        receipt = self._wait_for_tx_receipt(collect_tx, timeout=120, poll_interval=2.0)
        if receipt is None:
            print(f"[aerodrome-compound] Collect TX not mined within timeout")
            return tx_hashes

        # Step 2: Read post-collect token balances
        time.sleep(2.0)  # Wait for RPC consistency

        def _read_erc20_balance(token: str, wallet: str) -> int:
            data = SELECTOR_BALANCE_OF_ERC20 + _pad_address(wallet)
            result = _base_rpc_call("eth_call", [{"to": token, "data": data}, "latest"])
            if result and isinstance(result, str) and len(result) >= 66:
                return int(result[2:66], 16)
            return 0

        bal0 = _read_erc20_balance(token0, account_address)
        bal1 = _read_erc20_balance(token1, account_address)

        if gauge_address:
            aero_bal = _read_erc20_balance(AERO_TOKEN, account_address)
            print(f"[aerodrome-compound] Post-collect: {symbol0}={bal0/(10**decimals0):.8f} "
                  f"{symbol1}={bal1/(10**decimals1):.8f} AERO={aero_bal/1e18:.8f}")
            # Future: swap AERO to token0/token1 before increasing liquidity.
            # For now, we compound whatever token0/token1 balances are available.
        else:
            print(f"[aerodrome-compound] Post-collect: {symbol0}={bal0/(10**decimals0):.8f} "
                  f"{symbol1}={bal1/(10**decimals1):.8f}")

        if bal0 == 0 and bal1 == 0:
            print(f"[aerodrome-compound] No balances to compound")
            return tx_hashes

        # Step 3: Read pool state for liquidity computation
        pool_address = _pool_for_token_ids(token0, token1, fee_tier, position_manager)
        if not pool_address:
            print(f"[aerodrome-compound] Could not resolve pool address")
            return tx_hashes

        _, current_tick, _, _, _, sqrtPriceX96 = _fetch_pool_state(pool_address)
        if sqrtPriceX96 is None or current_tick is None:
            print(f"[aerodrome-compound] Could not read pool state")
            return tx_hashes

        sqrt_price = sqrtPriceX96 / (2**96)
        sqrt_lower = 1.0001 ** (tick_lower / 2.0)
        sqrt_upper = 1.0001 ** (tick_upper / 2.0)

        # Compute liquidity delta from token amounts
        if tick_lower <= current_tick < tick_upper:
            liq0 = bal0 * sqrt_price * sqrt_upper / (sqrt_upper - sqrt_price) / (10**decimals0)
            liq1 = bal1 / (sqrt_price - sqrt_lower) / (10**decimals1)
            if liq0 > 0 and liq1 > 0:
                liquidity_delta = int(min(liq0, liq1))
            else:
                liquidity_delta = int(max(liq0, liq1))
        elif current_tick < tick_lower:
            liquidity_delta = int(bal0 * sqrt_price * sqrt_upper / (sqrt_upper - sqrt_lower) / (10**decimals0))
        else:
            liquidity_delta = int(bal1 / (sqrt_upper - sqrt_lower) / (10**decimals1))

        if liquidity_delta <= 0:
            print(f"[aerodrome-compound] Computed liquidity delta is 0")
            return tx_hashes

        # Step 4: Approve PositionManager for token0 and token1
        for token, balance in [(token0, bal0), (token1, bal1)]:
            if balance > 0:
                approve_data = (
                    SELECTOR_APPROVE
                    + _pad_address(position_manager)
                    + _pad_int_to_64(balance)
                )
                approve_tx = self._broadcast(params.account, token, approve_data,
                                             gas_check_address=account_address)
                tx_hashes.append(approve_tx)
                self._wait_for_tx_receipt(approve_tx, timeout=60)
                print(f"[aerodrome-compound] Approved {token} for {balance}")

        # Step 5: Increase liquidity
        increase_data = (
            SELECTOR_INCREASE_LIQUIDITY
            + _pad_int_to_64(token_id)
            + _pad_int_to_64(liquidity_delta)  # liquidityDelta
            + _pad_int_to_64(bal0)  # amount0Desired
            + _pad_int_to_64(bal1)  # amount1Desired
            + _pad_int_to_64(0)  # amount0Min
            + _pad_int_to_64(0)  # amount1Min
            + _pad_int_to_64(deadline)  # deadline
        )
        increase_tx = self._broadcast(params.account, position_manager, increase_data,
                                      gas_check_address=account_address)
        tx_hashes.append(increase_tx)
        print(f"[aerodrome-compound] Step 5: increaseLiquidity liq={liquidity_delta} tx={increase_tx}")

        return tx_hashes

    def rebalance(self, params: RebalanceParams) -> List[str]:
        raise NotImplementedError("Aerodrome rebalance not yet implemented")
