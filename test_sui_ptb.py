"""Sui PTB/BCS serializer tests (v5.3.21).

Offline unit tests for the pure-stdlib serializer in src/sui_ptb.py.
"""
import os
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).parent / "src"))

import sui_ptb  # noqa: E402
from sui_ptb import (
    BcsWriter, serialize_transaction_data_v1, sui_intent_bytes,
    Argument, CallArg, make_move_call, make_transfer_objects,
    make_pure_u64, make_pure_bool, make_pure_address, make_shared_object_input,
    struct_type_tag, sui_int, sui_i32, encode_i32_bits,
)


def test_bcs_writer_integers():
    w = BcsWriter()
    w.u8(0x01).u16(0x0203).u32(0x04050607).u64(0x08090A0B0C0D0E0F)
    w.u128((0x0102030405060708 << 64) | 0x090A0B0C0D0E0F10)
    expected = (
        bytes([0x01]) +
        bytes([0x03, 0x02]) +
        bytes([0x07, 0x06, 0x05, 0x04]) +
        bytes([0x0F, 0x0E, 0x0D, 0x0C, 0x0B, 0x0A, 0x09, 0x08]) +
        bytes([0x10, 0x0F, 0x0E, 0x0D, 0x0C, 0x0B, 0x0A, 0x09,
               0x08, 0x07, 0x06, 0x05, 0x04, 0x03, 0x02, 0x01])
    )
    assert w.bytes() == expected, w.bytes().hex()
    print("✅ BcsWriter integer serialization")


def test_bcs_writer_uleb128():
    cases = [
        (0, bytes([0x00])),
        (127, bytes([0x7F])),
        (128, bytes([0x80, 0x01])),
        (16383, bytes([0xFF, 0x7F])),
        (16384, bytes([0x80, 0x80, 0x01])),
    ]
    for value, expected in cases:
        w = BcsWriter()
        w.uleb128(value)
        assert w.bytes() == expected, (value, w.bytes().hex())
    print("✅ BcsWriter ULEB128")


def test_bcs_writer_string():
    w = BcsWriter()
    w.string("abc")
    assert w.bytes() == bytes([0x03, 0x61, 0x62, 0x63]), w.bytes().hex()
    print("✅ BcsWriter string")


def test_pure_call_args():
    assert make_pure_u64(42)["pure"] == bytes([0x2A, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00])
    assert make_pure_bool(True)["pure"] == bytes([0x01])
    addr = "0x" + "a" * 64
    assert make_pure_address(addr)["pure"] == bytes.fromhex("a" * 64)
    print("✅ pure call args")


def test_serialize_transaction_data_v1_smoke():
    """Serialize a minimal PTB and verify it produces non-empty bytes."""
    sender = "0x" + "a" * 64
    gas_coin = {
        "object_id": "0x" + "b" * 64,
        "version": 123,
        "digest": "0x" + "c" * 64,
    }
    inputs = [
        make_shared_object_input("0x" + "d" * 64, 5, mutable=True),
        make_pure_u64(100),
        make_pure_address(sender),
    ]
    commands = [
        make_move_call(
            "0x" + "e" * 64,
            "module",
            "func",
            type_arguments=[struct_type_tag("0x2", "sui", "SUI")],
            arguments=[Argument.input(0), Argument.input(1)],
        ),
        make_transfer_objects([Argument.result(0)], Argument.input(2)),
    ]
    tx = {
        "sender": sender,
        "programmable_transaction": {"inputs": inputs, "commands": commands},
        "gas_data": {"price": 1000, "owner": sender, "payment": [gas_coin]},
    }
    bcs = serialize_transaction_data_v1(tx)
    assert isinstance(bcs, bytes) and len(bcs) > 50, len(bcs)
    # First byte: TransactionData enum variant V1 = 0
    assert bcs[0] == 0, bcs[0]
    # Second byte: TransactionKind enum variant ProgrammableTransaction = 0
    assert bcs[1] == 0, bcs[1]
    print("✅ serialize_transaction_data_v1 smoke")


def test_sui_intent_bytes():
    assert sui_intent_bytes() == bytes([0, 0, 0])
    print("✅ Sui intent bytes")


def test_object_ref_hex_digest():
    w = BcsWriter()
    sui_ptb.serialize_object_ref(w, {
        "object_id": "0x" + "b" * 64,
        "version": 1,
        "digest": "0x" + "c" * 64,
    })
    data = w.bytes()
    # ObjectDigest is serialized as Vec<u8>: 32 bytes id + 8 bytes version + uleb length + digest bytes.
    assert len(data) == 32 + 8 + 1 + 32, len(data)
    print("✅ object ref with hex digest")


def test_type_tag_vector():
    tag = {"vector": {"struct": {
        "address": "0x2", "module": "coin", "name": "Coin",
        "type_args": [{"struct": {"address": "0x2", "module": "sui", "name": "SUI", "type_args": []}}],
    }}}
    w = BcsWriter()
    sui_ptb.serialize_type_tag(w, tag)
    assert len(w.bytes()) > 10
    print("✅ vector<struct> type tag")


def test_i32_roundtrip():
    # Positive ticks (N1 ground truth)
    assert sui_i32({"type": "0x1::i32::I32", "fields": {"bits": 133080}}) == 133080
    assert sui_i32({"type": "0x1::i32::I32", "fields": {"bits": 139320}}) == 139320
    # Negative tick via two's complement bits
    assert sui_i32({"fields": {"bits": 2 ** 32 - 100}}) == -100
    # Plain values
    assert sui_i32(42) == 42
    assert sui_i32("123") == 123
    # Encode round-trip
    for tick in (-887272, -100, 0, 100, 139320):
        bits = encode_i32_bits(tick)
        assert sui_i32({"fields": {"bits": bits}}) == tick
    # Bad values (type string) must not crash
    assert sui_int({"type": "0x1::i32::I32", "fields": {"bits": 133080}}) is not None
    assert sui_int({"type": "0x1::i32::I32", "fields": {"bits": "not a number"}}) is None
    print("✅ I32 decode/encode round-trip")


def main():
    test_bcs_writer_integers()
    test_bcs_writer_uleb128()
    test_bcs_writer_string()
    test_pure_call_args()
    test_serialize_transaction_data_v1_smoke()
    test_sui_intent_bytes()
    test_object_ref_hex_digest()
    test_type_tag_vector()
    test_i32_roundtrip()
    print("✅ ALL SUI PTB TESTS PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
