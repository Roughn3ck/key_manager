"""ColdStack v5.3.21 - Cetus (Sui) CLMM writer.

Builds Programmable Transaction Blocks (PTBs) for Cetus collect, close and
compound operations, signs them through the key-manager agent's Sui path, and
broadcasts via Sui JSON-RPC.  Uses only the Python standard library.
"""
from __future__ import annotations

import base64
import json
import math
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

from venue_adapters.venue_writer import (
    VenueWriter, SwapParams, OpenPositionParams, IncreaseLiquidityParams,
    DecreaseLiquidityParams, CollectFeesParams, CompoundFeesParams, RebalanceParams,
)
from sui_ptb import (
    Argument, CallArg, make_move_call, make_transfer_objects,
    serialize_transaction_data_v1, sui_intent_bytes,
    get_shared_object_initial_version, get_object_ref, get_gas_coin_object,
    dry_run_transaction_block, execute_transaction_block, struct_type_tag,
    make_pure_u128, make_pure_u64, make_pure_bool, make_pure_address,
    make_shared_object_input, sui_rpc, sui_int, sui_i32,
)
from sui_assets import DEFAULT_SUI_RPC, get_coin_metadata
from coldtrack.close_recorder import CloseResult, CloseLeg

CETUS_PACKAGE = "0x1eabed72c53feb3805120a081dc15963c204dc8d091542592abaf7a35689b2fb"
CETUS_GLOBAL_CONFIG = "0xdaa46292632c3c4d8f31f23ea0f9b36a28ff3677e9684980e4438403a67a3d8f"
CETUS_REWARDER_VAULT = "0xce7bceef26d3ad1f6d9b6f13a953f053e6ed3ca77907516481ce99ae8e588f2b"
CLOCK_OBJECT = "0x0000000000000000000000000000000000000000000000000000000000000006"

AGENT_URL = "http://127.0.0.1:8842"


def _sui_type_tag(coin_type: str) -> Dict[str, Any]:
    """Convert a coin type string into a sui_ptb struct TypeTag dict."""
    parts = coin_type.split("::")
    if len(parts) != 3:
        raise ValueError(f"invalid coin type: {coin_type}")
    address, module, name = parts
    return struct_type_tag(address, module, name)


def _coin_symbol_decimals(coin_type: str) -> Tuple[str, int]:
    meta = get_coin_metadata(coin_type)
    parts = coin_type.split("::")
    symbol = meta.get("symbol") or (parts[2] if len(parts) == 3 else coin_type)
    decimals = int(meta.get("decimals", 9))
    return symbol, decimals


def _liquidity_from_amounts(amount_a: float, amount_b: float, sqrt_price: float,
                            tick_lower: int, tick_upper: int,
                            dec_a: int, dec_b: int) -> int:
    """Estimate the liquidity delta supported by (amount_a, amount_b) at current price.

    Uses the Uniswap-V3 / Cetus CLMM formula.  Amounts are in human units and
    converted to raw token amounts internally; the returned liquidity is an
    integer suitable for pool::add_liquidity.
    """
    sqrt_lo = math.sqrt(1.0001 ** tick_lower)
    sqrt_hi = math.sqrt(1.0001 ** tick_upper)
    sqrt_p = sqrt_price
    if sqrt_p <= sqrt_lo:
        # All liquidity is token A
        raw_a = int(amount_a * (10 ** dec_a))
        if sqrt_hi <= sqrt_lo or raw_a <= 0:
            return 0
        return int(raw_a * sqrt_lo * sqrt_hi / (sqrt_hi - sqrt_lo))
    elif sqrt_p >= sqrt_hi:
        # All liquidity is token B
        raw_b = int(amount_b * (10 ** dec_b))
        if sqrt_hi <= sqrt_lo or raw_b <= 0:
            return 0
        return int(raw_b / (sqrt_hi - sqrt_lo))
    else:
        raw_a = int(amount_a * (10 ** dec_a))
        raw_b = int(amount_b * (10 ** dec_b))
        if raw_a <= 0 or raw_b <= 0:
            return 0
        l_from_a = int(raw_a * sqrt_p * sqrt_hi / (sqrt_hi - sqrt_p))
        l_from_b = int(raw_b / (sqrt_p - sqrt_lo))
        return min(l_from_a, l_from_b)


class CetusWriter(VenueWriter):
    """Write-capable Cetus CLMM adapter for Sui."""

    VENUE_KEY = "cetus"

    def __init__(self, agent_url: str = AGENT_URL, rpc_url: str = DEFAULT_SUI_RPC):
        self.agent_url = agent_url.rstrip("/")
        self.rpc_url = rpc_url
        self._unlocked = False
        self._shared_versions: Dict[str, int] = {}
        self.last_close_result: Optional[Dict[str, Any]] = None

    # ------------------------------------------------------------------
    # Agent communication
    # ------------------------------------------------------------------

    def _agent_call(self, cmd: str, **params: Any) -> dict:
        payload: Dict[str, Any] = {"cmd": cmd, **params}
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self.agent_url, data=data,
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
            raise RuntimeError(f"Agent error ({cmd}): {result.get('error', 'Unknown')}")
        return result["result"]

    def _get_sui_address(self, account: str) -> str:
        result = self._agent_call("get_sui_address", account=account)
        if isinstance(result, dict):
            return result.get("address", "")
        return str(result)

    def is_available(self) -> bool:
        try:
            result = self._agent_call("status")
            unlocked = result.get("unlocked", False) if isinstance(result, dict) else False
            self._unlocked = unlocked
            return unlocked
        except Exception:
            self._unlocked = False
            return False

    def unlock(self, credentials: Dict[str, Any]) -> bool:
        return self.is_available()

    def _sign_and_broadcast(self, account: str, tx_data: Dict[str, Any]) -> str:
        """Serialize, dry-run, sign and broadcast a PTB.  Returns the tx digest."""
        tx_bytes = serialize_transaction_data_v1(tx_data)
        tx_bytes_b64 = base64.b64encode(tx_bytes).decode()

        # v5.3.21: dry-run gate.  Never sign a PTB that does not simulate.
        dry = dry_run_transaction_block(self.rpc_url, tx_bytes_b64)
        if not isinstance(dry, dict) or dry.get("effects", {}).get("status", {}).get("status") != "success":
            err = dry.get("effects", {}).get("status", {}).get("error") if isinstance(dry, dict) else dry
            raise RuntimeError(f"Cetus PTB dry-run failed: {err}")

        result = self._agent_call(
            "sign_sui_ptb",
            account=account,
            tx_bytes_b64=tx_bytes_b64,
            broadcast=True,
            rpc_url=self.rpc_url,
        )
        tx_hash = result.get("tx_hash") or result.get("digest")
        if not tx_hash:
            raise RuntimeError(f"Sui broadcast returned no digest: {result}")
        return tx_hash

    # ------------------------------------------------------------------
    # Shared-object version cache
    # ------------------------------------------------------------------

    def _shared_version(self, object_id: str) -> int:
        if object_id not in self._shared_versions:
            self._shared_versions[object_id] = get_shared_object_initial_version(self.rpc_url, object_id)
        return self._shared_versions[object_id]

    # ------------------------------------------------------------------
    # Position / pool reading
    # ------------------------------------------------------------------

    def _get_position_data(self, position_id: str) -> Dict[str, Any]:
        """Fetch a Cetus Position object and return decoded fields."""
        result = sui_rpc(self.rpc_url, "sui_getObject",
                         [position_id, {"showType": True, "showContent": True}], timeout=20)
        data = (result or {}).get("data", {})
        content = data.get("content") or {}
        fields = content.get("fields") or {}
        type_str = data.get("type") or content.get("type") or ""

        # coin types come from the position type generics
        generics = []
        if "<" in type_str and type_str.endswith(">"):
            inner = type_str[type_str.index("<") + 1:-1]
            # Simple split on comma; Cetus positions have exactly two type args.
            generics = [g.strip() for g in inner.split(",")]

        pool_id = fields.get("pool")
        if isinstance(pool_id, dict):
            pool_id = pool_id.get("id") or str(pool_id)

        coin_a = generics[0] if len(generics) > 0 else ""
        coin_b = generics[1] if len(generics) > 1 else ""
        return {
            "object_id": position_id,
            "pool_id": pool_id,
            "coin_type_a": coin_a,
            "coin_type_b": coin_b,
            "liquidity": sui_int(fields.get("liquidity")) or 0,
            "tick_lower": sui_i32(fields.get("tick_lower_index")) or 0,
            "tick_upper": sui_i32(fields.get("tick_upper_index")) or 0,
            "fee_owed_a": sui_int(fields.get("fee_owed_a")) or 0,
            "fee_owed_b": sui_int(fields.get("fee_owed_b")) or 0,
        }

    def _get_pool_data(self, pool_id: str) -> Dict[str, Any]:
        result = sui_rpc(self.rpc_url, "sui_getObject",
                         [pool_id, {"showType": True, "showContent": True}], timeout=20)
        data = (result or {}).get("data", {})
        content = data.get("content") or {}
        fields = content.get("fields") or {}

        tick = fields.get("current_tick_index", fields.get("current_tick"))
        return {
            "object_id": pool_id,
            "current_sqrt_price": sui_int(fields.get("current_sqrt_price")) or 0,
            "tick_current": sui_i32(tick) or 0,
        }

    # ------------------------------------------------------------------
    # PTB helpers
    # ------------------------------------------------------------------

    def _common_inputs(self, sender: str, pool_id: str, position_id: str) -> List[Dict[str, Any]]:
        """Return the standard Cetus PTB inputs: GlobalConfig, Pool, Position, Clock, sender."""
        return [
            make_shared_object_input(CETUS_GLOBAL_CONFIG, self._shared_version(CETUS_GLOBAL_CONFIG), mutable=False),
            make_shared_object_input(pool_id, self._shared_version(pool_id), mutable=True),
            CallArg.object({"imm_or_owned": get_object_ref(self.rpc_url, position_id)}),
            make_shared_object_input(CLOCK_OBJECT, 1, mutable=False),
            make_pure_address(sender),
        ]

    def _type_args(self, pos: Dict[str, Any]) -> List[Dict[str, Any]]:
        return [_sui_type_tag(pos["coin_type_a"]), _sui_type_tag(pos["coin_type_b"])]

    def _coin_type_args(self, coin_a: str, coin_b: str) -> List[Dict[str, Any]]:
        return [_sui_type_tag(coin_a), _sui_type_tag(coin_b)]

    def _move_call(self, module: str, function: str, type_args: List[Dict[str, Any]],
                   args: List[Dict[str, Any]]) -> Dict[str, Any]:
        return make_move_call(CETUS_PACKAGE, module, function, type_args, args)

    def _std_move_call(self, module: str, function: str, type_args: List[Dict[str, Any]],
                       args: List[Dict[str, Any]]) -> Dict[str, Any]:
        return make_move_call("0x0000000000000000000000000000000000000000000000000000000000000002",
                              module, function, type_args, args)

    # ------------------------------------------------------------------
    # VenueWriter interface
    # ------------------------------------------------------------------

    def wrap_native(self, account: str, amount: float) -> str:
        raise NotImplementedError("wrap_native not needed for Sui")

    def unwrap_native(self, account: str, amount: float) -> str:
        raise NotImplementedError("unwrap_native not needed for Sui")

    def approve(self, account: str, token: str, spender: str, amount: float) -> str:
        raise NotImplementedError("approve not needed for Sui")

    def swap(self, params: SwapParams) -> str:
        raise NotImplementedError("swap not implemented for Cetus")

    def open_position(self, params: OpenPositionParams) -> Tuple[str, Optional[int]]:
        raise NotImplementedError("open_position not implemented for Cetus")

    def increase_liquidity(self, params: IncreaseLiquidityParams) -> str:
        raise NotImplementedError("use compound_fees() for Cetus liquidity increases")

    def decrease_liquidity(self, params: DecreaseLiquidityParams) -> str:
        raise NotImplementedError("use close_position() for Cetus liquidity removal")

    def collect_fees(self, params: CollectFeesParams) -> str:
        """Collect accrued Cetus fees for a position.  Returns the tx digest."""
        position_id = params.position_id.split(":", 1)[1] if ":" in params.position_id else params.position_id
        sender = self._get_sui_address(params.account)
        if not sender:
            raise RuntimeError(f"Could not derive Sui address for account {params.account}")

        pos = self._get_position_data(position_id)
        if not pos.get("coin_type_a"):
            raise RuntimeError(f"Could not decode Cetus position {position_id}")

        gas_coin = get_gas_coin_object(self.rpc_url, sender)
        inputs = self._common_inputs(sender, pos["pool_id"], position_id)
        inputs.append(make_pure_bool(False))  # bool arg for collect_fee
        # Override the transfer recipient input (index 4) if caller supplied one.
        recipient = (params.recipient or sender).strip()
        inputs[4] = make_pure_address(recipient)

        type_args = self._type_args(pos)
        commands: List[Dict[str, Any]] = [
            # 0: collect_fee -> (Balance<A>, Balance<B>)
            self._move_call("pool", "collect_fee", type_args,
                            [Argument.input(0), Argument.input(1), Argument.input(2), Argument.input(5)]),
            # 1/2: from_balance -> Coin<A>, Coin<B>
            self._std_move_call("coin", "from_balance", [type_args[0]],
                                [Argument.nested_result(0, 0)]),
            self._std_move_call("coin", "from_balance", [type_args[1]],
                                [Argument.nested_result(0, 1)]),
            # 3: transfer_objects to sender
            make_transfer_objects([Argument.result(1), Argument.result(2)], Argument.input(4)),
        ]

        tx_data = {
            "sender": sender,
            "programmable_transaction": {"inputs": inputs, "commands": commands},
            "gas_data": {
                "price": 1000,
                "owner": sender,
                "payment": [gas_coin],
                "budget": gas_coin.get("budget", 10_000_000),
            },
        }
        tx_hash = self._sign_and_broadcast(params.account, tx_data)
        print(f"[cetus-writer] collect_fees: position={position_id[:12]}... tx={tx_hash}")
        return tx_hash

    def close_position(self, position_id: str, account: str) -> List[str]:
        """Close a Cetus position: remove liquidity, collect fees, close, transfer.

        Returns a list of transaction digests (usually one PTB).
        """
        raw_id = position_id.split(":", 1)[1] if ":" in position_id else position_id
        sender = self._get_sui_address(account)
        if not sender:
            raise RuntimeError(f"Could not derive Sui address for account {account}")

        pos = self._get_position_data(raw_id)
        if not pos.get("coin_type_a"):
            raise RuntimeError(f"Could not decode Cetus position {raw_id}")

        sym_a, dec_a = _coin_symbol_decimals(pos["coin_type_a"])
        sym_b, dec_b = _coin_symbol_decimals(pos["coin_type_b"])

        gas_coin = get_gas_coin_object(self.rpc_url, sender)
        inputs = self._common_inputs(sender, pos["pool_id"], raw_id)
        inputs.append(make_pure_u128(pos["liquidity"]))
        inputs.append(make_pure_bool(False))  # collect_fee bool
        type_args = self._type_args(pos)

        commands: List[Dict[str, Any]] = []
        # 0: remove_liquidity(all) -> (Balance<A>, Balance<B>)
        commands.append(self._move_call(
            "pool", "remove_liquidity", type_args,
            [Argument.input(0), Argument.input(1), Argument.input(2), Argument.input(5), Argument.input(3)]))
        # 1: collect_fee -> (Balance<A>, Balance<B>)
        commands.append(self._move_call(
            "pool", "collect_fee", type_args,
            [Argument.input(0), Argument.input(1), Argument.input(2), Argument.input(6)]))
        # 2/3: from_balance principal -> Coin<A>, Coin<B>
        commands.append(self._std_move_call("coin", "from_balance", [type_args[0]],
                                            [Argument.nested_result(0, 0)]))
        commands.append(self._std_move_call("coin", "from_balance", [type_args[1]],
                                            [Argument.nested_result(0, 1)]))
        # 4/5: from_balance fees -> Coin<A>, Coin<B>
        commands.append(self._std_move_call("coin", "from_balance", [type_args[0]],
                                            [Argument.nested_result(1, 0)]))
        commands.append(self._std_move_call("coin", "from_balance", [type_args[1]],
                                            [Argument.nested_result(1, 1)]))
        # 6: close_position
        commands.append(self._move_call(
            "pool", "close_position", type_args,
            [Argument.input(0), Argument.input(1), Argument.input(2)]))
        # 7: transfer all coins to sender
        commands.append(make_transfer_objects(
            [Argument.result(2), Argument.result(3), Argument.result(4), Argument.result(5)],
            Argument.input(4)))

        tx_data = {
            "sender": sender,
            "programmable_transaction": {"inputs": inputs, "commands": commands},
            "gas_data": {
                "price": 1000,
                "owner": sender,
                "payment": [gas_coin],
                "budget": gas_coin.get("budget", 10_000_000),
            },
        }
        tx_hash = self._sign_and_broadcast(account, tx_data)
        print(f"[cetus-writer] close_position: position={raw_id[:12]}... tx={tx_hash}")

        # Capture a CloseResult for coldtrack recording.
        # Principal liquidity legs are approximated from the position liquidity;
        # fee legs are taken from the uncollected fees at close time.
        legs: List[CloseLeg] = []
        if pos["liquidity"] > 0:
            legs.append(CloseLeg(asset=sym_a, amount=pos["liquidity"] / (10 ** dec_a), kind="liquidity", sig=tx_hash))
            legs.append(CloseLeg(asset=sym_b, amount=pos["liquidity"] / (10 ** dec_b), kind="liquidity", sig=tx_hash))
        if pos["fee_owed_a"] > 0:
            legs.append(CloseLeg(asset=sym_a, amount=pos["fee_owed_a"] / (10 ** dec_a), kind="fee", sig=tx_hash))
        if pos["fee_owed_b"] > 0:
            legs.append(CloseLeg(asset=sym_b, amount=pos["fee_owed_b"] / (10 ** dec_b), kind="fee", sig=tx_hash))

        self.last_close_result = CloseResult(
            position_mint=raw_id,
            platform="Cetus",
            chain="Sui",
            legs=legs,
            close_sig=tx_hash,
            collect_sig=tx_hash,
            decrease_sig=tx_hash,
            gas={tx_hash: 0.0},
            gas_asset="SUI",
            final_amounts={},
        )
        return [tx_hash]

    def compound_fees(self, params: CompoundFeesParams) -> List[str]:
        """Collect fees and add them back as liquidity.

        Falls back to a plain collect if the fee amounts are not enough to add
        any material liquidity or if the dry-run fails.
        """
        position_id = params.position_id.split(":", 1)[1] if ":" in params.position_id else params.position_id
        sender = self._get_sui_address(params.account)
        if not sender:
            raise RuntimeError(f"Could not derive Sui address for account {params.account}")

        pos = self._get_position_data(position_id)
        if not pos.get("coin_type_a"):
            raise RuntimeError(f"Could not decode Cetus position {position_id}")
        pool = self._get_pool_data(pos["pool_id"])

        fee_a = pos["fee_owed_a"] / (10 ** _coin_symbol_decimals(pos["coin_type_a"])[1])
        fee_b = pos["fee_owed_b"] / (10 ** _coin_symbol_decimals(pos["coin_type_b"])[1])
        if fee_a <= 0 and fee_b <= 0:
            print(f"[cetus-writer] compound_fees: no fees to compound for {position_id[:12]}...")
            return []

        sqrt_price = (pool.get("current_sqrt_price") or 0) / (2 ** 64)
        if sqrt_price <= 0:
            raise RuntimeError("Pool sqrt price unavailable; cannot estimate compound liquidity")

        liquidity_delta = _liquidity_from_amounts(
            fee_a, fee_b, sqrt_price, pos["tick_lower"], pos["tick_upper"],
            _coin_symbol_decimals(pos["coin_type_a"])[1],
            _coin_symbol_decimals(pos["coin_type_b"])[1],
        )
        if liquidity_delta <= 0:
            print(f"[cetus-writer] compound_fees: fee amounts too small to add liquidity; collecting only")
            collect_tx = self.collect_fees(CollectFeesParams(account=params.account, position_id=params.position_id))
            return [collect_tx]

        gas_coin = get_gas_coin_object(self.rpc_url, sender)
        inputs = self._common_inputs(sender, pos["pool_id"], position_id)
        inputs.append(make_pure_u128(liquidity_delta))     # input 5
        inputs.append(make_pure_bool(False))               # input 6: collect_fee bool
        type_args = self._type_args(pos)

        commands: List[Dict[str, Any]] = [
            # 0: collect_fee -> (Balance<A>, Balance<B>)
            self._move_call("pool", "collect_fee", type_args,
                            [Argument.input(0), Argument.input(1), Argument.input(2), Argument.input(6)]),
            # 1: add_liquidity -> AddLiquidityReceipt
            self._move_call("pool", "add_liquidity", type_args,
                            [Argument.input(0), Argument.input(1), Argument.input(2), Argument.input(5), Argument.input(3)]),
            # 2: add_liquidity_pay_amount -> (u64, u64)
            self._move_call("pool", "add_liquidity_pay_amount", type_args, [Argument.result(1)]),
            # 3/4: split exact amounts from collected fee balances
            self._std_move_call("balance", "split", [type_args[0]],
                                [Argument.nested_result(0, 0), Argument.nested_result(2, 0)]),
            self._std_move_call("balance", "split", [type_args[1]],
                                [Argument.nested_result(0, 1), Argument.nested_result(2, 1)]),
            # 5: repay_add_liquidity
            self._move_call("pool", "repay_add_liquidity", type_args,
                            [Argument.input(0), Argument.input(1), Argument.result(3), Argument.result(4), Argument.result(1)]),
            # 6/7: from_balance on remaining fee balances -> coins
            self._std_move_call("coin", "from_balance", [type_args[0]], [Argument.nested_result(0, 0)]),
            self._std_move_call("coin", "from_balance", [type_args[1]], [Argument.nested_result(0, 1)]),
            # 8: transfer remaining coins to sender
            make_transfer_objects([Argument.result(6), Argument.result(7)], Argument.input(4)),
        ]

        tx_data = {
            "sender": sender,
            "programmable_transaction": {"inputs": inputs, "commands": commands},
            "gas_data": {
                "price": 1000,
                "owner": sender,
                "payment": [gas_coin],
                "budget": gas_coin.get("budget", 10_000_000),
            },
        }

        try:
            tx_hash = self._sign_and_broadcast(params.account, tx_data)
            print(f"[cetus-writer] compound_fees: position={position_id[:12]}... tx={tx_hash}")
            return [tx_hash]
        except RuntimeError as e:
            err_msg = str(e).lower()
            if "insufficient" in err_msg or "not enough" in err_msg or "dry-run failed" in err_msg:
                print(f"[cetus-writer] compound_fees dry-run/repay failed; falling back to collect: {e}")
                collect_tx = self.collect_fees(CollectFeesParams(account=params.account, position_id=params.position_id))
                return [collect_tx]
            raise

    def rebalance(self, params: RebalanceParams) -> List[str]:
        raise NotImplementedError("rebalance not implemented for Cetus")
