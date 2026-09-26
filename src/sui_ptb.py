"""ColdStack v5.3.21 - Sui Programmable Transaction Block (PTB) serializer.

Pure-stdlib BCS writer for the subset of Sui transaction data needed by
ColdStack writers (Cetus collect / close / compound).  Produces the BCS bytes
that the key-manager agent signs with the Sui intent prefix.
"""
from __future__ import annotations

import struct
from typing import Any, Dict, List, Optional, Tuple

try:
    from ed25519_utils import b58decode as _b58decode
except Exception:  # pragma: no cover
    _b58decode = None  # type: ignore


# ---------------------------------------------------------------------------
# Sui JSON scalar helpers
# ---------------------------------------------------------------------------

def sui_int(value: Any) -> Optional[int]:
    """Coerce a Sui JSON scalar (number/string) or Move struct wrapper to int.

    Handles ``{"bits": ...}``, ``{"fields": {"bits": ...}}`` and single-key
    struct values.  Returns ``None`` for non-numeric values (e.g. type strings).
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    if isinstance(value, dict):
        for key in ("bits", "value"):
            if key in value:
                return sui_int(value[key])
        inner = value.get("fields")
        if isinstance(inner, dict):
            return sui_int(inner)
        if len(value) == 1:
            return sui_int(next(iter(value.values())))
    return None


def sui_i32(value: Any) -> Optional[int]:
    """Decode a Sui ``i32::I32`` (two's-complement ``bits`` u32) to signed int."""
    n = sui_int(value)
    if n is None:
        return None
    if n >= 2 ** 31:
        n -= 2 ** 32
    return n


def encode_i32_bits(value: int) -> int:
    """Encode a signed tick/value as a two's-complement u32 bit pattern."""
    return (value + 2 ** 32) & 0xFFFFFFFF


# ---------------------------------------------------------------------------
# BCS primitives
# ---------------------------------------------------------------------------

class BcsWriter:
    """Incremental BCS serializer."""

    def __init__(self) -> None:
        self._buf: bytearray = bytearray()

    def bytes(self) -> bytes:
        return bytes(self._buf)

    def u8(self, value: int) -> "BcsWriter":
        self._buf.append(value & 0xFF)
        return self

    def u16(self, value: int) -> "BcsWriter":
        self._buf.extend(struct.pack("<H", value & 0xFFFF))
        return self

    def u32(self, value: int) -> "BcsWriter":
        self._buf.extend(struct.pack("<I", value & 0xFFFFFFFF))
        return self

    def u64(self, value: int) -> "BcsWriter":
        self._buf.extend(struct.pack("<Q", value & 0xFFFFFFFFFFFFFFFF))
        return self

    def u128(self, value: int) -> "BcsWriter":
        self._buf.extend(struct.pack("<QQ", value & 0xFFFFFFFFFFFFFFFF, (value >> 64) & 0xFFFFFFFFFFFFFFFF))
        return self

    def u256(self, value: int) -> "BcsWriter":
        low = value & 0xFFFFFFFFFFFFFFFF
        mid1 = (value >> 64) & 0xFFFFFFFFFFFFFFFF
        mid2 = (value >> 128) & 0xFFFFFFFFFFFFFFFF
        high = (value >> 192) & 0xFFFFFFFFFFFFFFFF
        self._buf.extend(struct.pack("<QQQQ", low, mid1, mid2, high))
        return self

    def bool(self, value: bool) -> "BcsWriter":
        self._buf.append(1 if value else 0)
        return self

    def write_bytes(self, data: bytes) -> "BcsWriter":
        """BCS "bytes" type: uleb length + raw bytes."""
        self.uleb128(len(data))
        self._buf.extend(data)
        return self

    def uleb128(self, value: int) -> "BcsWriter":
        while True:
            byte = value & 0x7F
            value >>= 7
            if value:
                self._buf.append(byte | 0x80)
            else:
                self._buf.append(byte)
                break
        return self

    def string(self, value: str) -> "BcsWriter":
        data = value.encode("utf-8")
        self.uleb128(len(data))
        self._buf.extend(data)
        return self

    def fixed_bytes(self, data: bytes, length: Optional[int] = None) -> "BcsWriter":
        if length is not None and len(data) != length:
            raise ValueError(f"expected {length} bytes, got {len(data)}")
        self._buf.extend(data)
        return self

    def option(self, present: bool, serializer: "BcsWriter") -> "BcsWriter":
        self.bool(present)
        if present:
            self._buf.extend(serializer.bytes())
        return self


def _normalize_address(addr: str) -> bytes:
    """Convert a 0x-prefixed Sui address/object-id to 32 raw bytes.

    Short canonical addresses (e.g. ``0x2``) are left-padded with zeros.
    """
    addr = addr.strip().lower()
    if addr.startswith("0x"):
        addr = addr[2:]
    if len(addr) > 64:
        raise ValueError(f"address must be <= 0x + 64 hex chars: {addr[:20]}...")
    try:
        return bytes.fromhex(addr.rjust(64, "0"))
    except ValueError as e:
        raise ValueError(f"invalid hex address: {addr[:20]}...") from e


# ---------------------------------------------------------------------------
# TypeTag / StructTag
# ---------------------------------------------------------------------------

TYPE_TAG_BOOL = 0
TYPE_TAG_U8 = 1
TYPE_TAG_U64 = 2
TYPE_TAG_U128 = 3
TYPE_TAG_ADDRESS = 4
TYPE_TAG_SIGNER = 5
TYPE_TAG_VECTOR = 6
TYPE_TAG_STRUCT = 7
TYPE_TAG_U16 = 8
TYPE_TAG_U32 = 9
TYPE_TAG_U256 = 10


def serialize_type_tag(writer: BcsWriter, type_tag: Dict[str, Any]) -> None:
    """Serialize a TypeTag dict.

    Supported shapes:
      {"bool": True}
      {"u8": True} / {"u16": True} / {"u32": True} / {"u64": True}
      {"u128": True} / {"u256": True}
      {"address": True}
      {"signer": True}
      {"vector": <inner_type_tag>}
      {"struct": <struct_tag>}
    """
    if type_tag.get("bool"):
        writer.u8(TYPE_TAG_BOOL)
    elif type_tag.get("u8"):
        writer.u8(TYPE_TAG_U8)
    elif type_tag.get("u16"):
        writer.u8(TYPE_TAG_U16)
    elif type_tag.get("u32"):
        writer.u8(TYPE_TAG_U32)
    elif type_tag.get("u64"):
        writer.u8(TYPE_TAG_U64)
    elif type_tag.get("u128"):
        writer.u8(TYPE_TAG_U128)
    elif type_tag.get("u256"):
        writer.u8(TYPE_TAG_U256)
    elif type_tag.get("address"):
        writer.u8(TYPE_TAG_ADDRESS)
    elif type_tag.get("signer"):
        writer.u8(TYPE_TAG_SIGNER)
    elif "vector" in type_tag:
        writer.u8(TYPE_TAG_VECTOR)
        serialize_type_tag(writer, type_tag["vector"])
    elif "struct" in type_tag:
        writer.u8(TYPE_TAG_STRUCT)
        serialize_struct_tag(writer, type_tag["struct"])
    else:
        raise ValueError(f"unsupported type_tag shape: {type_tag}")


def serialize_struct_tag(writer: BcsWriter, struct: Dict[str, Any]) -> None:
    """Serialize a StructTag: address, module, name, type_args."""
    writer.fixed_bytes(_normalize_address(struct["address"]), 32)
    writer.string(struct["module"])
    writer.string(struct["name"])
    type_args = struct.get("type_args", [])
    writer.uleb128(len(type_args))
    for ta in type_args:
        serialize_type_tag(writer, ta)


# ---------------------------------------------------------------------------
# Transaction inputs / arguments / commands
# ---------------------------------------------------------------------------

class Argument:
    """PTB argument reference."""

    @staticmethod
    def gas_coin() -> Dict[str, Any]:
        return {"gas_coin": True}

    @staticmethod
    def input(index: int) -> Dict[str, Any]:
        return {"input": index}

    @staticmethod
    def result(index: int) -> Dict[str, Any]:
        return {"result": index}

    @staticmethod
    def nested_result(index: int, sub_index: int) -> Dict[str, Any]:
        return {"nested_result": (index, sub_index)}


def serialize_argument(writer: BcsWriter, arg: Dict[str, Any]) -> None:
    if arg.get("gas_coin"):
        writer.u8(0)
    elif "input" in arg:
        writer.u8(1)
        writer.u16(arg["input"])
    elif "result" in arg:
        writer.u8(2)
        writer.u16(arg["result"])
    elif "nested_result" in arg:
        writer.u8(3)
        idx, sub = arg["nested_result"]
        writer.u16(idx)
        writer.u16(sub)
    else:
        raise ValueError(f"unsupported argument: {arg}")


class CallArg:
    """PTB transaction input."""

    @staticmethod
    def pure(value: bytes) -> Dict[str, Any]:
        return {"pure": value}

    @staticmethod
    def object(object_arg: Dict[str, Any]) -> Dict[str, Any]:
        return {"object": object_arg}


def serialize_call_arg(writer: BcsWriter, call_arg: Dict[str, Any]) -> None:
    if "pure" in call_arg:
        writer.u8(0)
        writer.write_bytes(call_arg["pure"])
    elif "object" in call_arg:
        writer.u8(1)
        serialize_object_arg(writer, call_arg["object"])
    else:
        raise ValueError(f"unsupported call_arg: {call_arg}")


def serialize_object_ref(writer: BcsWriter, obj_ref: Dict[str, Any]) -> None:
    writer.fixed_bytes(_normalize_address(obj_ref["object_id"]), 32)
    writer.u64(int(obj_ref["version"]))
    digest = obj_ref["digest"]
    if isinstance(digest, str):
        if digest.startswith("0x"):
            digest = bytes.fromhex(digest[2:])
        elif _b58decode is not None and len(digest) >= 32:
            # Sui RPC returns object digests as base58 (32 raw bytes).
            digest = _b58decode(digest)
        else:
            digest = base64.b64decode(digest)
    # ObjectDigest is serialized as a Vec<u8> (uleb length + 32 bytes).
    writer.write_bytes(digest)


def serialize_object_arg(writer: BcsWriter, obj_arg: Dict[str, Any]) -> None:
    if "imm_or_owned" in obj_arg:
        writer.u8(0)
        serialize_object_ref(writer, obj_arg["imm_or_owned"])
    elif "shared" in obj_arg:
        writer.u8(1)
        shared = obj_arg["shared"]
        writer.fixed_bytes(_normalize_address(shared["object_id"]), 32)
        writer.u64(int(shared["initial_shared_version"]))
        writer.bool(bool(shared.get("mutable", True)))
    elif "receiving" in obj_arg:
        writer.u8(2)
        serialize_object_ref(writer, obj_arg["receiving"])
    else:
        raise ValueError(f"unsupported object_arg: {obj_arg}")


def _serialize_type_tag_list(writer: BcsWriter, type_tags: List[Dict[str, Any]]) -> None:
    writer.uleb128(len(type_tags))
    for ta in type_tags:
        serialize_type_tag(writer, ta)


def serialize_command(writer: BcsWriter, command: Dict[str, Any]) -> None:
    """Serialize one PTB command."""
    kind = command.get("kind")
    if kind == "move_call":
        writer.u8(0)
        mc = command["move_call"]
        writer.fixed_bytes(_normalize_address(mc["package"]), 32)
        writer.string(mc["module"])
        writer.string(mc["function"])
        _serialize_type_tag_list(writer, mc.get("type_arguments", []))
        args = mc.get("arguments", [])
        writer.uleb128(len(args))
        for a in args:
            serialize_argument(writer, a)
    elif kind == "transfer_objects":
        writer.u8(1)
        objs = command["objects"]
        writer.uleb128(len(objs))
        for o in objs:
            serialize_argument(writer, o)
        serialize_argument(writer, command["address"])
    elif kind == "split_coins":
        writer.u8(2)
        serialize_argument(writer, command["coin"])
        amounts = command["amounts"]
        writer.uleb128(len(amounts))
        for a in amounts:
            serialize_argument(writer, a)
    elif kind == "merge_coins":
        writer.u8(3)
        serialize_argument(writer, command["destination"])
        sources = command["sources"]
        writer.uleb128(len(sources))
        for s in sources:
            serialize_argument(writer, s)
    elif kind == "publish":
        writer.u8(4)
        modules = command["modules"]
        writer.uleb128(len(modules))
        for m in modules:
            writer.write_bytes(m)
        deps = command["dependencies"]
        writer.uleb128(len(deps))
        for d in deps:
            writer.fixed_bytes(_normalize_address(d), 32)
    elif kind == "make_move_vec":
        writer.u8(5)
        opt_type = command.get("type")
        if opt_type:
            writer.bool(True)
            serialize_type_tag(writer, opt_type)
        else:
            writer.bool(False)
        elems = command["elements"]
        writer.uleb128(len(elems))
        for e in elems:
            serialize_argument(writer, e)
    elif kind == "upgrade":
        writer.u8(6)
        modules = command["modules"]
        writer.uleb128(len(modules))
        for m in modules:
            writer.write_bytes(m)
        deps = command["dependencies"]
        writer.uleb128(len(deps))
        for d in deps:
            writer.fixed_bytes(_normalize_address(d), 32)
        writer.fixed_bytes(_normalize_address(command["package_id"]), 32)
        serialize_argument(writer, command["ticket"])
    else:
        raise ValueError(f"unsupported command kind: {kind}")


# ---------------------------------------------------------------------------
# Transaction data
# ---------------------------------------------------------------------------

def serialize_programmable_transaction(writer: BcsWriter, pt: Dict[str, Any]) -> None:
    inputs = pt.get("inputs", [])
    writer.uleb128(len(inputs))
    for inp in inputs:
        serialize_call_arg(writer, inp)
    commands = pt.get("commands", [])
    writer.uleb128(len(commands))
    for c in commands:
        serialize_command(writer, c)


def serialize_gas_data(writer: BcsWriter, gas_data: Dict[str, Any]) -> None:
    """Serialize Sui GasData.

    On-chain GasData layout (payment, owner, price, budget) as returned by
    sui_getTransactionBlock JSON-RPC.  The budget is the max gas units the
    transaction may consume; it defaults to the payment coin's balance.
    """
    payment = gas_data["payment"]
    writer.uleb128(len(payment))
    for p in payment:
        serialize_object_ref(writer, p)
    writer.fixed_bytes(_normalize_address(gas_data["owner"]), 32)
    writer.u64(int(gas_data["price"]))
    writer.u64(int(gas_data.get("budget", 0)))


def serialize_transaction_data_v1(tx: Dict[str, Any]) -> bytes:
    """Serialize TransactionData::V1 for a Programmable Transaction.

    tx fields:
      sender: 0x-address
      programmable_transaction: {"inputs": [...], "commands": [...]}
      gas_data: {"price": int, "owner": address, "payment": [ObjectRef, ...]}
      expiration: optional epoch int (None if omitted)
    """
    writer = BcsWriter()
    # TransactionData enum variant V1 = 0
    writer.u8(0)
    # TransactionKind enum variant ProgrammableTransaction = 0
    writer.u8(0)
    serialize_programmable_transaction(writer, tx["programmable_transaction"])
    writer.fixed_bytes(_normalize_address(tx["sender"]), 32)
    serialize_gas_data(writer, tx["gas_data"])
    # TransactionExpiration enum
    if "expiration" in tx and tx["expiration"] is not None:
        writer.u8(1)
        writer.u64(int(tx["expiration"]))
    else:
        writer.u8(0)
    return writer.bytes()


def sui_intent_bytes() -> bytes:
    """Sui intent for a TransactionData: scope=TransactionData, version=0, app_id=Sui."""
    return bytes([0, 0, 0])


# ---------------------------------------------------------------------------
# Convenience builders
# ---------------------------------------------------------------------------

def make_pure_u8(value: int) -> Dict[str, Any]:
    return CallArg.pure(BcsWriter().u8(value & 0xFF).bytes())


def make_pure_u16(value: int) -> Dict[str, Any]:
    return CallArg.pure(BcsWriter().u16(value & 0xFFFF).bytes())


def make_pure_u32(value: int) -> Dict[str, Any]:
    return CallArg.pure(BcsWriter().u32(value & 0xFFFFFFFF).bytes())


def make_pure_u64(value: int) -> Dict[str, Any]:
    return CallArg.pure(BcsWriter().u64(value & 0xFFFFFFFFFFFFFFFF).bytes())


def make_pure_u128(value: int) -> Dict[str, Any]:
    return CallArg.pure(BcsWriter().u128(value).bytes())


def make_pure_bool(value: bool) -> Dict[str, Any]:
    return CallArg.pure(BcsWriter().bool(value).bytes())


def make_pure_address(address: str) -> Dict[str, Any]:
    return CallArg.pure(_normalize_address(address))


def make_shared_object_input(object_id: str, initial_shared_version: int, mutable: bool = True) -> Dict[str, Any]:
    return CallArg.object({
        "shared": {
            "object_id": object_id,
            "initial_shared_version": initial_shared_version,
            "mutable": mutable,
        }
    })


def make_move_call(package: str, module: str, function: str,
                   type_arguments: Optional[List[Dict[str, Any]]] = None,
                   arguments: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    return {
        "kind": "move_call",
        "move_call": {
            "package": package,
            "module": module,
            "function": function,
            "type_arguments": type_arguments or [],
            "arguments": arguments or [],
        }
    }


def make_transfer_objects(objects: List[Dict[str, Any]], address_arg: Dict[str, Any]) -> Dict[str, Any]:
    return {"kind": "transfer_objects", "objects": objects, "address": address_arg}


def make_split_coins(coin: Dict[str, Any], amounts: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {"kind": "split_coins", "coin": coin, "amounts": amounts}


def make_merge_coins(destination: Dict[str, Any], sources: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {"kind": "merge_coins", "destination": destination, "sources": sources}


def struct_type_tag(address: str, module: str, name: str,
                    type_args: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    return {"struct": {
        "address": address,
        "module": module,
        "name": name,
        "type_args": type_args or [],
    }}


# ---------------------------------------------------------------------------
# RPC helpers used by writers
# ---------------------------------------------------------------------------

import base64  # noqa: E402
import json  # noqa: E402
import urllib.request  # noqa: E402


def sui_rpc(url: str, method: str, params: list, timeout: int = 30) -> Any:
    """POST a Sui JSON-RPC call.  Raises RuntimeError on JSON-RPC error."""
    payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode("utf-8")
    req = urllib.request.Request(
        url, data=payload,
        headers={"Content-Type": "application/json", "User-Agent": "ColdStack/5.3.21"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if not isinstance(data, dict) or data.get("error"):
        err = data.get("error") if isinstance(data, dict) else data
        raise RuntimeError(f"{method}: {json.dumps(err)[:500]}")
    return data.get("result")


def get_shared_object_initial_version(url: str, object_id: str) -> int:
    """Fetch a shared object's initialSharedVersion."""
    result = sui_rpc(url, "sui_getObject", [object_id, {"showOwner": True}], timeout=20)
    owner = (result or {}).get("data", {}).get("owner", {})
    shared = owner.get("Shared", {})
    return int(shared.get("initial_shared_version", 0))


def get_object_ref(url: str, object_id: str) -> Dict[str, Any]:
    """Fetch a full ObjectRef {object_id, version, digest} for an owned object."""
    result = sui_rpc(url, "sui_getObject", [object_id, {"showContent": False}], timeout=20)
    data = (result or {}).get("data", {})
    return {
        "object_id": data.get("objectId", object_id),
        "version": int(data.get("version", 0)),
        "digest": data.get("digest", ""),
    }


def get_gas_coin_object(url: str, sender: str, amount_mist: int = 50_000_000,
                        budget_mist: Optional[int] = None) -> Dict[str, Any]:
    """Pick a SUI gas coin owned by sender with at least ``amount_mist`` balance.

    Uses suix_getCoins and returns an ObjectRef plus a ``budget`` field for
    GasData.  Raises if no suitable coin is found.
    """
    result = sui_rpc(url, "suix_getCoins", [sender, None, None, 10], timeout=20)
    coins = (result or {}).get("data", [])
    for coin in coins:
        bal = int(coin.get("balance", 0))
        if bal >= amount_mist:
            return {
                "object_id": coin["coinObjectId"],
                "version": int(coin["version"]),
                "digest": coin["digest"],
                "budget": budget_mist or bal,
            }
    raise RuntimeError(f"no SUI gas coin >= {amount_mist} MIST for {sender}")


def dry_run_transaction_block(url: str, tx_bytes_b64: str) -> Any:
    """Dry-run an unsigned PTB; returns the result dict."""
    return sui_rpc(url, "sui_dryRunTransactionBlock", [tx_bytes_b64], timeout=45)


def execute_transaction_block(url: str, tx_bytes_b64: str, signature_b64: str,
                              options: Optional[Dict[str, Any]] = None) -> Any:
    """Broadcast a signed PTB.  Returns the RPC result."""
    opts = options or {
        "showEffects": True,
        "showEvents": True,
        "showObjectChanges": True,
        "showBalanceChanges": True,
    }
    return sui_rpc(url, "sui_executeTransactionBlock",
                   [tx_bytes_b64, [signature_b64], opts], timeout=60)
