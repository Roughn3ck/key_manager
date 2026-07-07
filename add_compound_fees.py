#!/usr/bin/env python3
"""Patch script to add compound_fees to hyperliquid_writer.py"""
import os

os.chdir(r"B:\Blockchain\coldstack")

path = "src/venue_adapters/hyperliquid_writer.py"
with open(path, "r", encoding="utf-8") as f:
    content = f.read()

# 1. Add SWAP_ROUTER constant
content = content.replace(
    'WHYPE_UBTC_POOL_3000 = "0x3a36b04bcc1d5e2e303981ef643d2668e00b43e7"\n',
    'WHYPE_UBTC_POOL_3000 = "0x3a36b04bcc1d5e2e303981ef643d2668e00b43e7"\n'
    'SWAP_ROUTER = "0x1ebdfc75ffe3ba3de61e7138a3e8706ac841af9b"\n',
)

# 2. Add CompoundFeesParams to imports
content = content.replace(
    "    CollectFeesParams,\n    RebalanceParams,\n)",
    "    CollectFeesParams,\n    CompoundFeesParams,\n    RebalanceParams,\n)",
)

# 3. Add SELECTOR_EXACT_INPUT_SINGLE
content = content.replace(
    'SELECTOR_POSITIONS = "0x99fbab88"',
    'SELECTOR_POSITIONS = "0x99fbab88"     # positions(uint256)\n'
    'SELECTOR_EXACT_INPUT_SINGLE = "0x414bf389"  # exactInputSingle((address,address,uint24,address,uint256,uint256,uint256,uint160))',
)

# 4. Add compound_fees method + helpers before close_position
compound_code = '''    def compound_fees(self, params: "CompoundFeesParams") -> List[str]:
        """Collect fees, swap to optimal ratio, and increase liquidity.

        Multi-TX flow:
          1. Collect all accrued fees to the wallet
          2. Read wallet balances of token0 and token1
          3. Compute optimal swap to match position range
          4. Approve swap router and execute swap if needed
          5. Approve PositionManager for both tokens
          6. Call increaseLiquidity with actual post-swap balances

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
            + _pad_uint256(MAX_UINT256)
            + _pad_uint256(MAX_UINT256)
        )
        collect_tx = self._broadcast(params.account, POSITION_MANAGER, collect_data)
        tx_hashes.append(collect_tx)
        _log_action("compound_fees_collect", tx_hash=collect_tx, extra=f"token_id={token_id}")

        # Step 2: Read wallet balances after collection
        bal0_raw = self._read_balance(account_address, WHYPE)
        bal1_raw = self._read_balance(account_address, UBTC)

        if bal0_raw == 0 and bal1_raw == 0:
            _log_action("compound_fees_skip", extra="no fees collected")
            return tx_hashes

        # Step 3: Read position data for tick range
        pos_data = self._read_position_data(token_id)
        if pos_data is None:
            _log_action("compound_fees_skip", extra="could not read position data")
            return tx_hashes

        tick_lower, tick_upper, fee = pos_data

        # Read current sqrtPrice from pool slot0
        sqrt_price_x96 = self._read_sqrt_price_x96(WHYPE_UBTC_POOL_3000)
        if sqrt_price_x96 is None or sqrt_price_x96 == 0:
            _log_action("compound_fees_skip", extra="could not read pool price")
            return tx_hashes

        sqrt_price = sqrt_price_x96 / (2 ** 96)
        sqrt_lower = 1.0001 ** (tick_lower / 2.0)
        sqrt_upper = 1.0001 ** (tick_upper / 2.0)

        # Compute optimal swap
        bal0 = bal0_raw / (10 ** WHYPE_DECIMALS)
        bal1 = bal1_raw / (10 ** UBTC_DECIMALS)
        price = sqrt_price ** 2
        human_price = price * (10 ** (WHYPE_DECIMALS - UBTC_DECIMALS))

        swap_needed = False
        swap_token_in = ""
        swap_token_out = ""
        swap_amount_raw = 0

        current_tick = int(_math.floor(_math.log(sqrt_price ** 2, 1.0001)))

        if current_tick >= tick_upper:
            swap_needed = bal1_raw > 0
            swap_token_in = UBTC
            swap_token_out = WHYPE
            swap_amount_raw = bal1_raw
        elif current_tick < tick_lower:
            swap_needed = bal0_raw > 0
            swap_token_in = WHYPE
            swap_token_out = UBTC
            swap_amount_raw = bal0_raw
        else:
            value_per_L = (human_price * (sqrt_upper - sqrt_price) / (sqrt_price * sqrt_upper)
                          + (sqrt_price - sqrt_lower))
            if value_per_L > 0 and (bal0 * human_price + bal1) > 0:
                L = (bal0 * human_price + bal1) / value_per_L
                target0 = L * (sqrt_upper - sqrt_price) / (sqrt_price * sqrt_upper)
                target1 = L * (sqrt_price - sqrt_lower)
                target0_raw = int(target0 * (10 ** WHYPE_DECIMALS))
                target1_raw = int(target1 * (10 ** UBTC_DECIMALS))
                if bal0_raw > target0_raw:
                    swap_needed = True
                    swap_token_in = WHYPE
                    swap_token_out = UBTC
                    swap_amount_raw = bal0_raw - target0_raw
                elif bal1_raw > target1_raw:
                    swap_needed = True
                    swap_token_in = UBTC
                    swap_token_out = WHYPE
                    swap_amount_raw = bal1_raw - target1_raw

        # Step 4: Execute swap if needed
        if swap_needed and swap_amount_raw > 0:
            _log_action("compound_fees_step2", extra=f"swapping {swap_token_in} -> {swap_token_out}")
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
                + _pad_uint256(0)  # amountOutMinimum
                + _pad_uint256(0)  # sqrtPriceLimitX96
            )
            swap_tx = self._broadcast(params.account, SWAP_ROUTER, swap_data)
            tx_hashes.append(swap_tx)
            _log_action("compound_fees_swap", tx_hash=swap_tx,
                       extra=f"in={swap_token_in} out={swap_token_out}")
            bal0_raw = self._read_balance(account_address, WHYPE)
            bal1_raw = self._read_balance(account_address, UBTC)

        # Step 5: Approve PositionManager for both tokens
        self._ensure_approval(params.account, account_address,
                              WHYPE, POSITION_MANAGER, MAX_UINT256)
        self._ensure_approval(params.account, account_address,
                              UBTC, POSITION_MANAGER, MAX_UINT256)

        # Step 6: Increase liquidity with actual post-swap balances
        if bal0_raw > 0 or bal1_raw > 0:
            _log_action("compound_fees_step3", extra="increasing liquidity")
            increase_data = (
                SELECTOR_INCREASE_LIQUIDITY
                + _pad_uint256(token_id)
                + _pad_uint256(bal0_raw)
                + _pad_uint256(bal1_raw)
                + _pad_uint256(0)
                + _pad_uint256(0)
                + _pad_uint256(deadline)
            )
            increase_tx = self._broadcast(params.account, POSITION_MANAGER, increase_data)
            tx_hashes.append(increase_tx)
            _log_action("compound_fees_increase", tx_hash=increase_tx,
                       extra=f"token_id={token_id}")

        _log_action("compound_fees_done", extra=f"txs={len(tx_hashes)}")
        return tx_hashes

    def _read_balance(self, address: str, token: str) -> int:
        """Read ERC-20 balanceOf for an address."""
        data = SELECTOR_BALANCE_OF + _pad_address(address)
        result = self._rpc_call("eth_call", [{"to": token, "data": data}, "latest"])
        if not result or not isinstance(result, str) or len(result) < 66:
            return 0
        try:
            return int(result[2:66], 16)
        except (ValueError, IndexError):
            return 0

    def _read_position_data(self, token_id: int) -> Optional[Tuple[int, int, int]]:
        """Read tickLower, tickUpper, fee from positions(tokenId)."""
        data = SELECTOR_POSITIONS + _pad_uint256(token_id)
        result = self._rpc_call("eth_call", [{"to": POSITION_MANAGER, "data": data}, "latest"])
        if not result or not isinstance(result, str) or len(result) < 2 + 32 * 7:
            return None
        try:
            body = result[2:]
            fee = int(body[128:192], 16)
            tick_lower = int(body[192:256], 16)
            if tick_lower >= 2 ** 255:
                tick_lower -= 2 ** 256
            tick_upper = int(body[256:320], 16)
            if tick_upper >= 2 ** 255:
                tick_upper -= 2 ** 256
            return (tick_lower, tick_upper, fee)
        except (ValueError, IndexError):
            return None

    def _read_sqrt_price_x96(self, pool_address: str) -> Optional[int]:
        """Read sqrtPriceX96 from pool slot0."""
        result = self._rpc_call("eth_call", [{"to": pool_address, "data": "0x3850c7bd"}, "latest"])
        if not result or not isinstance(result, str) or len(result) < 66:
            return None
        try:
            return int(result[2:66], 16)
        except (ValueError, IndexError):
            return None

'''

content = content.replace(
    "    def close_position(self, position_id: str, account: str) -> List[str]:",
    compound_code + "\n    def close_position(self, position_id: str, account: str) -> List[str]:",
)

with open(path, "w", encoding="utf-8") as f:
    f.write(content)

print("Done - compound_fees added to hyperliquid_writer.py")