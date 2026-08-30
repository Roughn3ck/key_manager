"""Aerodrome Venue Writer - ColdStack LP Engine v5.2.5

Write operations for Aerodrome SlipStream V3 LP positions on BASE.
All signing is delegated to the key_manager_agent via HTTP — the writer
never touches private keys directly.

Version: v5.2.5 (August 2026) - collect_fees + close_position + compound + rebalance
"""
import json
import math
import time
import urllib.request
import urllib.error
from typing import Any, Dict, List, Optional, Tuple

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
# NOTE: Aerodrome SlipStream's SwapRouter does NOT expose a Uniswap-V3-compatible
# exactInputSingle((address,address,uint24,...)) selector (0x414bf389). The
# SlipStream struct uses int24 tickSpacing instead of uint24 fee, yielding a
# different selector (verified against aerodrome-finance/slipstream ISwapRouter.sol):
#   exactInputSingle((address,address,int24,address,uint256,uint256,uint256,uint160))
AERO_SWAP_ROUTER = "0xc5ed88eE30B3BbfE87E1B57Bf4632c69E45B26B7"
SELECTOR_EXACT_INPUT_SINGLE = "0xa026383e"
# SlipStream NonfungiblePositionManager.mint(MintParams) — MintParams includes an
# extra trailing uint160 sqrtPriceX96 field (pool creation price; 0 = pool exists).
# Selector verified against aerodrome-finance/slipstream INonfungiblePositionManager.sol:
#   mint((address,address,int24,int24,int24,uint256,uint256,uint256,uint256,address,uint256,uint160))
SELECTOR_MINT = "0xb5007d1f"
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
        """Get the EVM address for a vault account name on BASE."""
        result = self._agent_call("get_address", account=account, chain="EVM", chain_id=self.chain_id)
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
        # SlipStream positions() returns tickSpacing at this offset, not fee tier
        tick_spacing = int(body[256:320], 16)
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
        pool_address = _pool_for_token_ids(token0, token1, tick_spacing, position_manager)
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
        """Full rebalance: close current position → optional ratio swap → mint new position at new range.

        Steps:
          1. Read current position (token0, token1, tick_spacing, tick_lower, tick_upper, liquidity)
          2. decreaseLiquidity(100%) — withdraw all principal
          3. collect(MAX, MAX) — claim principal + accrued fees to wallet
          4. Read post-collect ERC-20 balances for token0 / token1
          5. Compute optimal swap for the new tick range; if non-zero, run exactInputSingle on the SlipStream router
          6. Approve NPM for token0 and token1 (exact post-swap balances)
          7. mint(MintParams) at the new tick range
          8. Return every broadcast TX hash, in order

        Slippage wiring:
          - Swap: amountOutMinimum = quoted_out * (1 - slippage_pct/100) (if slippage_pct == 0, min-out = 0)
          - Mint: amount0Min = amount0Desired * (1 - slippage_pct/100); same for amount1Min

        Mid-flow failure: each step appends to tx_hashes before raising, so callers can
        inspect partial progress. If close succeeds but swap/mint fails, the user is left
        holding both tokens with no LP position — the GUI surfaces the failed step.

        Returns a list of TX hashes (decrease, collect, [swap], approve0?, approve1?, mint).
        """
        token_id = self._parse_token_id(params.position_id)
        if token_id is None:
            raise ValueError(f"Invalid position_id: {params.position_id}")

        tx_hashes: List[str] = []
        recipient = self._get_account_address(params.account)
        position_manager = self._find_position_manager(token_id)
        deadline = int(time.time()) + params.deadline_seconds

        # ------------------------------------------------------------------
        # Step 1: Read current position data
        # ------------------------------------------------------------------
        data = SELECTOR_POSITIONS + _pad_int_to_64(token_id)
        result = _base_rpc_call("eth_call", [{"to": position_manager, "data": data}, "latest"])
        if not result or not isinstance(result, str) or len(result) < 2 + 32 * 13:
            raise RuntimeError(f"Could not read position {token_id}")

        body = result[2:]
        token0 = _decode_address(body[128:192]).lower()
        token1 = _decode_address(body[192:256]).lower()
        # SlipStream positions() returns tickSpacing at this offset — NOT a fee tier.
        tick_spacing = int(body[256:320], 16)
        # Original tick range retained only for logging; the new range comes from params.
        tick_lower_old = _decode_int24(body[320:384])
        tick_upper_old = _decode_int24(body[384:448])
        liquidity = int(body[448:512], 16)
        decimals0 = _get_token_decimals(token0)
        decimals1 = _get_token_decimals(token1)

        print(f"[aerodrome-rebalance] token_id={token_id} pair={token0}/{token1} "
              f"old_range=[{tick_lower_old},{tick_upper_old}] new_range=[{params.new_tick_lower},{params.new_tick_upper}] "
              f"tick_spacing={tick_spacing} liquidity={liquidity}")

        # ------------------------------------------------------------------
        # Step 2: Decrease liquidity to zero (if any)
        # ------------------------------------------------------------------
        if liquidity > 0:
            decrease_data = (
                SELECTOR_DECREASE_LIQUIDITY
                + _pad_int_to_64(token_id)
                + _pad_int_to_64(liquidity)
                + _pad_int_to_64(0)  # amount0Min
                + _pad_int_to_64(0)  # amount1Min
                + _pad_int_to_64(deadline)
            )
            tx1 = self._broadcast(params.account, position_manager, decrease_data,
                                  gas_check_address=recipient)
            tx_hashes.append(tx1)
            receipt = self._wait_for_tx_receipt(tx1, timeout=120)
            if receipt is None:
                raise RuntimeError(f"decreaseLiquidity TX not mined within timeout: {tx1}")
            if receipt.get("status") != "0x1":
                raise RuntimeError(f"decreaseLiquidity TX reverted: {tx1}")
            print(f"[aerodrome-rebalance] step 2 decrease ok: {tx1}")

        # ------------------------------------------------------------------
        # Step 3: Collect principal + fees to wallet
        # ------------------------------------------------------------------
        collect_data = (
            SELECTOR_COLLECT
            + _pad_int_to_64(token_id)
            + _pad_address(recipient)
            + _pad_int_to_64(MAX_UINT128)
            + _pad_int_to_64(MAX_UINT128)
        )
        tx2 = self._broadcast(params.account, position_manager, collect_data,
                              gas_check_address=recipient)
        tx_hashes.append(tx2)
        receipt = self._wait_for_tx_receipt(tx2, timeout=120)
        if receipt is None:
            raise RuntimeError(f"collect TX not mined within timeout: {tx2}. "
                               f"decrease already submitted ({len(tx_hashes)} TXs).")
        if receipt.get("status") != "0x1":
            raise RuntimeError(f"collect TX reverted: {tx2}. "
                               f"decrease already submitted ({len(tx_hashes)} TXs).")
        print(f"[aerodrome-rebalance] step 3 collect ok: {tx2}")

        # RPC consistency: some nodes return stale balanceOf immediately after mining
        time.sleep(2.0)

        # ------------------------------------------------------------------
        # Step 4: Read post-collect wallet balances
        # ------------------------------------------------------------------
        def _read_erc20_balance(token: str, wallet: str) -> int:
            payload = SELECTOR_BALANCE_OF_ERC20 + _pad_address(wallet)
            res = _base_rpc_call("eth_call", [{"to": token, "data": payload}, "latest"])
            if res and isinstance(res, str) and len(res) >= 66:
                return int(res[2:66], 16)
            return 0

        bal0 = _read_erc20_balance(token0, recipient)
        bal1 = _read_erc20_balance(token1, recipient)
        print(f"[aerodrome-rebalance] step 4 post-collect balances: bal0={bal0} bal1={bal1}")

        if bal0 == 0 and bal1 == 0:
            print("[aerodrome-rebalance] Nothing to re-add after close — aborting before swap/mint")
            return tx_hashes

        # ------------------------------------------------------------------
        # Step 5: Compute + execute ratio swap toward the new range
        # ------------------------------------------------------------------
        pool_address = _pool_for_token_ids(token0, token1, tick_spacing, position_manager)
        if not pool_address:
            raise RuntimeError(f"Could not resolve pool for {token0}/{token1} "
                               f"tickSpacing={tick_spacing}")

        _, current_tick, _, _, _, sqrtPriceX96 = _fetch_pool_state(pool_address)
        if sqrtPriceX96 is None or current_tick is None:
            raise RuntimeError(f"Could not read pool state for {pool_address}")

        swap_in, swap_out, swap_amt = self._compute_swap_amount(
            sqrt_price_x96=sqrtPriceX96,
            tick_lower=params.new_tick_lower,
            tick_upper=params.new_tick_upper,
            bal0=bal0,
            bal1=bal1,
            decimals0=decimals0,
            decimals1=decimals1,
        )

        if swap_in and swap_amt > 0:
            # Map the "token0"/"token1" literals returned by the helper to real addresses
            swap_in_addr = token0 if swap_in == "token0" else token1
            swap_out_addr = token1 if swap_out == "token1" else token0

            # Quoted out estimate (used for slippage min-out); computed from the same V3 math
            sqrt_price = sqrtPriceX96 / (2 ** 96)
            dec_in = decimals0 if swap_in == "token0" else decimals1
            dec_out = decimals1 if swap_in == "token0" else decimals0
            if swap_in == "token0":
                # selling token0 -> buying token1: out ≈ in * raw_price, decimal-adjusted
                raw_price = sqrt_price ** 2
                quote_out = int(swap_amt * raw_price * (10 ** (dec_out - dec_in)) * 0.997)
            else:
                # selling token1 -> buying token0: out ≈ in / raw_price, decimal-adjusted
                raw_price = sqrt_price ** 2
                quote_out = int(swap_amt / raw_price * (10 ** (dec_out - dec_in)) * 0.997)

            if params.slippage_pct > 0:
                amount_out_min = int(quote_out * (1 - params.slippage_pct / 100.0))
            else:
                amount_out_min = 0

            # Approve router to spend swap_in_addr
            approve_router_data = SELECTOR_APPROVE + _pad_address(AERO_SWAP_ROUTER) + _pad_int_to_64(swap_amt)
            tx_app = self._broadcast(params.account, swap_in_addr, approve_router_data,
                                     gas_check_address=recipient)
            tx_hashes.append(tx_app)
            self._wait_for_tx_receipt(tx_app, timeout=60)

            # exactInputSingle((address tokenIn, address tokenOut, int24 tickSpacing,
            #                   address recipient, uint256 deadline, uint256 amountIn,
            #                   uint256 amountOutMinimum, uint160 sqrtPriceLimitX96))
            swap_data = (
                SELECTOR_EXACT_INPUT_SINGLE
                + _pad_address(swap_in_addr)
                + _pad_address(swap_out_addr)
                + _pad_int_to_64(tick_spacing)
                + _pad_address(recipient)
                + _pad_int_to_64(deadline)
                + _pad_int_to_64(swap_amt)
                + _pad_int_to_64(amount_out_min)
                + _pad_int_to_64(0)  # sqrtPriceLimitX96 = 0 (no limit)
            )
            tx_swap = self._broadcast(params.account, AERO_SWAP_ROUTER, swap_data,
                                      gas_check_address=recipient)
            tx_hashes.append(tx_swap)
            receipt = self._wait_for_tx_receipt(tx_swap, timeout=120)
            if receipt is None:
                raise RuntimeError(f"swap TX not mined within timeout: {tx_swap}. "
                                   f"close+collate already submitted ({len(tx_hashes)-1} TXs before swap).")
            if receipt.get("status") != "0x1":
                raise RuntimeError(f"swap TX reverted: {tx_swap}. "
                                   f"close+collect already submitted ({len(tx_hashes)-1} TXs before swap).")
            print(f"[aerodrome-rebalance] step 5 swap ok: {tx_swap}")

            # Re-read balances post-swap
            time.sleep(2.0)
            bal0 = _read_erc20_balance(token0, recipient)
            bal1 = _read_erc20_balance(token1, recipient)
            print(f"[aerodrome-rebalance] post-swap balances: bal0={bal0} bal1={bal1}")
        else:
            print("[aerodrome-rebalance] step 5 no swap needed — balances already in correct ratio")

        # ------------------------------------------------------------------
        # Step 6: Approve NPM for both tokens (exact post-swap balances)
        # ------------------------------------------------------------------
        for token, amount in [(token0, bal0), (token1, bal1)]:
            if amount > 0:
                approve_data = SELECTOR_APPROVE + _pad_address(position_manager) + _pad_int_to_64(amount)
                tx_app = self._broadcast(params.account, token, approve_data,
                                         gas_check_address=recipient)
                tx_hashes.append(tx_app)
                self._wait_for_tx_receipt(tx_app, timeout=60)
                print(f"[aerodrome-rebalance] approved {token} for {amount}")

        # ------------------------------------------------------------------
        # Step 7: Mint new position at the new tick range
        # ------------------------------------------------------------------
        if params.slippage_pct > 0:
            amount0_min = int(bal0 * (1 - params.slippage_pct / 100.0))
            amount1_min = int(bal1 * (1 - params.slippage_pct / 100.0))
        else:
            amount0_min = 0
            amount1_min = 0

        # SlipStream MintParams:
        # (address token0, address token1, int24 tickSpacing, int24 tickLower, int24 tickUpper,
        #  uint256 amount0Desired, uint256 amount1Desired, uint256 amount0Min, uint256 amount1Min,
        #  address recipient, uint256 deadline, uint160 sqrtPriceX96)
        mint_data = (
            SELECTOR_MINT
            + _pad_address(token0)
            + _pad_address(token1)
            + _pad_int_to_64(tick_spacing)
            + _pad_int_to_64(params.new_tick_lower)
            + _pad_int_to_64(params.new_tick_upper)
            + _pad_int_to_64(bal0)
            + _pad_int_to_64(bal1)
            + _pad_int_to_64(amount0_min)
            + _pad_int_to_64(amount1_min)
            + _pad_address(recipient)
            + _pad_int_to_64(deadline)
            + _pad_int_to_64(0)  # sqrtPriceX96 = 0 (pool already exists — do not create)
        )
        tx_mint = self._broadcast(params.account, position_manager, mint_data,
                                  gas_check_address=recipient)
        tx_hashes.append(tx_mint)
        receipt = self._wait_for_tx_receipt(tx_mint, timeout=120)
        if receipt and receipt.get("status") != "0x1":
            raise RuntimeError(f"mint TX reverted: {tx_mint}. "
                               f"Prior TXs ({len(tx_hashes)-1}) already submitted — funds are now un-invested.")
        print(f"[aerodrome-rebalance] step 7 mint ok: {tx_mint}")

        return tx_hashes

    @staticmethod
    def _compute_swap_amount(
        sqrt_price_x96: int,
        tick_lower: int,
        tick_upper: int,
        bal0: int,
        bal1: int,
        decimals0: int,
        decimals1: int,
    ) -> Tuple[Optional[str], Optional[str], int]:
        """Compute which token to swap and how much, so post-swap balances match the new range's ratio.

        Uses the Uniswap V3 liquidity-from-amount formula (same math as compound_fees):
          L_from_0 = bal0_raw * (sqrtP * sqrtUpper) / (sqrtUpper - sqrtP) / 10^decimals0
          L_from_1 = bal1_raw / (sqrtP - sqrtLower) / 10^decimals1

        The binding constraint is min(L0, L1). The token with excess L is the one to sell.
        We compute how much of that token, if sold at current price, brings the two L values
        into balance, then return (token_in, token_out, amount_in_raw).

        Returns:
            (token_in, token_out, amount_in_raw). token_in/token_out are the string
            "token0" / "token1" literals; the caller maps them to addresses.
            If no swap is needed, returns (None, None, 0).
        """
        if sqrt_price_x96 <= 0:
            return None, None, 0

        sqrt_p = sqrt_price_x96 / (2 ** 96)
        sqrt_lower = 1.0001 ** (tick_lower / 2.0)
        sqrt_upper = 1.0001 ** (tick_upper / 2.0)

        # Handle out-of-range extremes: position will be 100% one token
        price_raw = sqrt_p ** 2  # token1 per token0, in raw units
        tick_current = int(round(math.log(price_raw, 1.0001))) if price_raw > 0 else 0

        if tick_current >= tick_upper:
            # New range is entirely below current price — position is 100% token0
            # Need to sell ALL token1 for token0
            if bal1 == 0:
                return None, None, 0
            return "token1", "token0", bal1
        if tick_current < tick_lower:
            # New range is entirely above current price — position is 100% token1
            # Need to sell ALL token0 for token1
            if bal0 == 0:
                return None, None, 0
            return "token0", "token1", bal0

        # In-range: compute the L implied by each balance
        # Convert raw balances to human units for the V3 math
        bal0_h = bal0 / (10 ** decimals0)
        bal1_h = bal1 / (10 ** decimals1)

        if sqrt_upper <= sqrt_p or sqrt_p <= sqrt_lower:
            return None, None, 0

        liq_from_0 = bal0_h * (sqrt_p * sqrt_upper) / (sqrt_upper - sqrt_p)
        liq_from_1 = bal1_h / (sqrt_p - sqrt_lower)

        if liq_from_0 <= 0 and liq_from_1 <= 0:
            return None, None, 0

        if abs(liq_from_0 - liq_from_1) / max(liq_from_0, liq_from_1) < 0.01:
            # Within 1% — skip swap to avoid dust round-trips
            return None, None, 0

        if liq_from_0 > liq_from_1:
            # Excess token0 — sell some token0 for token1
            # Solve: (bal0_h - x) * sqrtP*sqrtU/(sqrtU-sqrtP) == (bal1_h + x*raw_price) / (sqrtP-sqrtL)
            # => x = (liq_from_0 - liq_from_1) * (sqrtU-sqrtP) / (sqrtP*sqrtU  +  raw_price*(sqrtU-sqrtP)/(sqrtP-sqrtL))
            # Simplify numerically:
            numerator = (liq_from_0 - liq_from_1)
            coeff0 = (sqrt_p * sqrt_upper) / (sqrt_upper - sqrt_p)
            coeff1 = price_raw / (sqrt_p - sqrt_lower)
            x = numerator / (coeff0 + coeff1)
            if x <= 0 or x >= bal0_h:
                return None, None, 0
            amount_in_raw = int(x * (10 ** decimals0))
            return ("token0", "token1", amount_in_raw) if amount_in_raw > 0 else (None, None, 0)
        else:
            # Excess token1 — sell some token1 for token0
            numerator = (liq_from_1 - liq_from_0)
            coeff0 = 1.0 / (sqrt_p - sqrt_lower)
            coeff1 = (sqrt_p * sqrt_upper) / (sqrt_upper - sqrt_p) / price_raw
            x = numerator / (coeff0 + coeff1)
            if x <= 0 or x >= bal1_h:
                return None, None, 0
            amount_in_raw = int(x * (10 ** decimals1))
            return ("token1", "token0", amount_in_raw) if amount_in_raw > 0 else (None, None, 0)
