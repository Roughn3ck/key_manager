#!/usr/bin/env python3
"""Patch script to fix LP fee display using collect() eth_call in hyperliquid_adapter.py"""
import os

os.chdir(r"B:\Blockchain\coldstack")

path = "src/venue_adapters/hyperliquid_adapter.py"
with open(path, "r", encoding="utf-8") as f:
    content = f.read()

# 1. Add SELECTOR_COLLECT constant near the other selectors
content = content.replace(
    'SELECTOR_GET_POOL = "0x1698ee82"',
    'SELECTOR_GET_POOL = "0x1698ee82"\n'
    '# collect() is used as a read-only eth_call to get exact uncollected fees.\n'
    'SELECTOR_COLLECT = "0xfc6f7865"  # collect((uint256,address,uint128,uint128))',
)

# 2. Add _estimate_uncollected_fees helper before _decode_positions_response
helper_code = '''def _estimate_uncollected_fees(
    token_id: int, wallet_address: str, decimals0: int, decimals1: int
) -> Tuple[float, float]:
    """Estimate uncollected fees via a read-only collect() eth_call.

    The Project X PositionManager's collect() function can be called via
    eth_call (without sending a transaction) to read the exact uncollected
    fee amounts. This returns the real fees, unlike the feeGrowthGlobal
    delta which overcounts by ~100x on this fork.

    Args:
        token_id: NFT token ID of the position.
        wallet_address: The position owner's wallet address (used as `from`).
        decimals0: Decimals of token0.
        decimals1: Decimals of token1.

    Returns:
        (amount0_human, amount1_human) or (0.0, 0.0) on failure.
    """
    try:
        # ABI encode collect((uint256 tokenId, address recipient, uint128 amount0Max, uint128 amount1Max))
        # uint128 max = 0xffffffffffffffffffffffffffffffff
        uint128_max = (1 << 128) - 1
        data = (
            SELECTOR_COLLECT
            + _pad_int_to_64(token_id)  # tokenId (uint256)
            + _pad_address(wallet_address)  # recipient (address)
            + _pad_int_to_64(uint128_max)  # amount0Max (uint128 -> padded to 32 bytes)
            + _pad_int_to_64(uint128_max)  # amount1Max (uint128 -> padded to 32 bytes)
        )
        result = _evm_rpc_call(
            "eth_call",
            [{"to": POSITION_MANAGER, "data": data, "from": wallet_address}, "latest"],
        )
        if not result or not isinstance(result, str) or len(result) < 2 + 64:
            print(f"[fees] collect() eth_call returned no data for token {token_id}")
            return (0.0, 0.0)

        body = result[2:]
        # Return is (uint128 amount0, uint128 amount1) packed in two 32-byte words
        amount0_raw = int(body[0:64], 16)
        amount1_raw = int(body[64:128], 16)

        amount0_human = amount0_raw / (10 ** decimals0)
        amount1_human = amount1_raw / (10 ** decimals1)

        print(f"[fees] token {token_id}: raw0={amount0_raw} raw1={amount1_raw} "
              f"human0={amount0_human:.8f} human1={amount1_human:.8f}")

        return (amount0_human, amount1_human)
    except Exception as e:
        print(f"[fees] collect() eth_call failed for token {token_id}: {e}")
        return (0.0, 0.0)


'''

content = content.replace(
    "def _decode_positions_response(",
    helper_code + "def _decode_positions_response(",
)

# 3. Add wallet_address parameter to _decode_positions_response
content = content.replace(
    "def _decode_positions_response(\n    lp_data: str, token_id: int, price_engine: Optional[PriceEngine]\n) -> Optional[LPPosition]:",
    "def _decode_positions_response(\n    lp_data: str, token_id: int, price_engine: Optional[PriceEngine],\n    wallet_address: str = \"\"\n) -> Optional[LPPosition]:",
)

# 4. Replace the fee estimation block with collect() eth_call
# Find the old fee block and replace it
old_fee_block = '''    # v5.1: DISABLED — Manual feeGrowthInside calculation from RPC tick storage.
    # This process wasn't functioning correctly — it was returning either $0.04
    # or more than the pool value. Collection-based fee tracking is planned for a
    # future release. For now, fees_earned comes only from the checkpointed
    # tokens_owed0/1 values read directly from positions(tokenId).
    #
    # The helper functions (_keccak256, _read_tick_fee_growth_outside,
    # _compute_fee_growth_inside) are retained behind PROJECT_X_FEE_ESTIMATION_ENABLED
    # for potential reuse with other venues.
    if PROJECT_X_FEE_ESTIMATION_ENABLED and ('''
    
new_fee_block = '''    # v5.1: Estimate real uncollected fees via collect() eth_call (read-only).
    # The Project X PositionManager's collect() function returns exact uncollected
    # fee amounts when called via eth_call with the wallet address as `from`.
    # This replaces the broken feeGrowthGlobal delta which overcounted by ~100x.
    fees_note = "Collect fees to report on fee income"
    if wallet_address:'''

content = content.replace(old_fee_block, new_fee_block)

# Replace the body of the old fee block with the new collect() call
# The old block continues with conditions and calculations - we need to replace all of it
# up to the fees_earned line
old_block_body = '''        liquidity > 0
        and pool_address
        and current_tick is not None
        and fee_growth_inside0_last_x128 is not None
        and fee_growth_inside1_last_x128 is not None
    ):
        try:
            fg_out_lower0, fg_out_lower1 = _read_tick_fee_growth_outside(
                pool_address, tick_lower
            )
            fg_out_upper0, fg_out_upper1 = _read_tick_fee_growth_outside(
                pool_address, tick_upper
            )

            # Fallback: if all outside values are zero, storage reads failed
            # or ticks are uninitialized. Keep checkpointed tokens_owed values.
            if fg_out_lower0 or fg_out_lower1 or fg_out_upper0 or fg_out_upper1:
                fg_inside0, fg_inside1 = _compute_fee_growth_inside(
                    current_tick,
                    tick_lower,
                    tick_upper,
                    fee_growth_global0,
                    fee_growth_global1,
                    fg_out_lower0,
                    fg_out_lower1,
                    fg_out_upper0,
                    fg_out_upper1,
                )

                delta0 = (fg_inside0 - fee_growth_inside0_last_x128) & ((1 << 256) - 1)
                delta1 = (fg_inside1 - fee_growth_inside1_last_x128) & ((1 << 256) - 1)

                # Only add positive deltas (skip wraparound artifacts).
                if 0 < delta0 < (1 << 255):
                    uncollected0 = (liquidity * delta0) // (2 ** 128)
                    owed0_h += uncollected0 / (10 ** decimals0)
                if 0 < delta1 < (1 << 255):
                    uncollected1 = (liquidity * delta1) // (2 ** 128)
                    owed1_h += uncollected1 / (10 ** decimals1)
        except Exception:
            pass

    fees_earned = {symbol0: owed0_h, symbol1: owed1_h}
    fees_earned_usd = (
        _usd_value(owed0_h, symbol0, price_engine) or 0.0
    ) + (_usd_value(owed1_h, symbol1, price_engine) or 0.0)

    # v5.1: Mark HyperEVM positions with a fee note so the GUI can display
    # "Collect fees to report on fee income" instead of a misleading number.
    fees_note = (
        "Collect fees to report on fee income"
        if not PROJECT_X_FEE_ESTIMATION_ENABLED
        else None
    )'''

new_block_body = '''        real_fee0, real_fee1 = _estimate_uncollected_fees(
            token_id, wallet_address, decimals0, decimals1
        )
        if real_fee0 > 0 or real_fee1 > 0:
            owed0_h = real_fee0
            owed1_h = real_fee1
            fees_note = None  # We have real fees, no need for the note
        # Fallback: if collect() returns 0, keep checkpointed tokens_owed values

    fees_earned = {symbol0: owed0_h, symbol1: owed1_h}
    fees_earned_usd = (
        _usd_value(owed0_h, symbol0, price_engine) or 0.0
    ) + (_usd_value(owed1_h, symbol1, price_engine) or 0.0)'''

content = content.replace(old_block_body, new_block_body)

# 5. Add wallet_address parameter to fetch_evm_position_by_token_id
content = content.replace(
    "    def fetch_evm_position_by_token_id(\n        self, token_id: int, price_engine: Optional[PriceEngine] = None\n    ) -> LPPosition:",
    "    def fetch_evm_position_by_token_id(\n        self, token_id: int, price_engine: Optional[PriceEngine] = None,\n        wallet_address: str = \"\"\n    ) -> LPPosition:",
)

# 6. Pass wallet_address to _decode_positions_response in fetch_evm_position_by_token_id
content = content.replace(
    "        pos = _decode_positions_response(result, token_id, price_engine)",
    "        pos = _decode_positions_response(result, token_id, price_engine, wallet_address)",
)

# 7. Pass wallet_address in fetch_evm_lp_positions where it calls fetch_evm_position_by_token_id
content = content.replace(
    "                    pos = self.fetch_evm_position_by_token_id(tid, price_engine)",
    "                    pos = self.fetch_evm_position_by_token_id(tid, price_engine, wallet_address)",
)

# 8. Pass wallet_address in the owned_ids loop
content = content.replace(
    "            pos = self.fetch_evm_position_by_token_id(token_id, price_engine)",
    "            pos = self.fetch_evm_position_by_token_id(token_id, price_engine, wallet_address)",
)

# 9. Pass wallet_address in fetch_position (single position fetch)
content = content.replace(
    "            return self.fetch_evm_position_by_token_id(numeric_id, price_engine)",
    "            return self.fetch_evm_position_by_token_id(numeric_id, price_engine, \"\")",
)

with open(path, "w", encoding="utf-8") as f:
    f.write(content)

print("Done - LP fee display fixed with collect() eth_call")