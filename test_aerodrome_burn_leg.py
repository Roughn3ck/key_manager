"""Aerodrome close burn-leg ordering test (v5.3.18).

Verifies that a burn transaction hash is included in the CloseResult gas map
and close_sig (for NOTES) but NEVER appears as a withdraw or yield leg.
No network calls.
"""
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).parent / "src"))

from venue_adapters.aerodrome_writer import AerodromeWriter, SELECTOR_BURN
from coldtrack.close_recorder import CloseResult


def make_writer():
    w = AerodromeWriter(agent_url="http://127.0.0.1:8842")
    w._close_capture = {
        "position_id_str": "75255240",
        "token0": "0x60a3e35cCcdB7a81A7AFD9bEaEc088ca1E5adb42",  # EURC
        "token1": "0xcbb7c0000aB88B473b1f5afd9ef808440eed33bf",  # cbBTC
        "dec0": 6,
        "dec1": 8,
        "recipient": "0xAe8E5FDb8857736C2218532Fd9D68430aAbAC6ae",
        "decrease_sig": "0xdec",
        "collect_sig": "0xcol",
    }
    w.price_engine = None
    return w


def test_burn_sig_not_a_leg():
    w = make_writer()

    # Mock _rpc_call: all three receipts succeed; burn has no Transfer logs.
    def fake_rpc(method, params):
        sig = params[0]
        if method == "eth_getTransactionReceipt":
            return {
                "status": "0x1",
                "gasUsed": "0x5208",
                "effectiveGasPrice": "0x1e8480",
                "blockHash": "0x" + "b" * 64,
                "blockNumber": "0x1234",
                "logs": [] if sig == "0xburn" else [
                    {
                        "topics": [
                            "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef",
                            "0x" + "0" * 24 + "pool",
                            "0x" + "0" * 24 + "Ae8E5FDb8857736C2218532Fd9D68430aAbAC6ae",
                        ],
                        "address": "0x60a3e35cCcdB7a81A7AFD9bEaEc088ca1E5adb42",
                        "data": "0x" + "0" * 56 + "0000000000002710",  # 10000 EURC
                    },
                ],
            }
        if method == "eth_getBlockByNumber":
            return {"timestamp": "0x66f00000"}
        return None

    with patch.object(w, "_rpc_call", fake_rpc):
        result = w._post_close_state(
            w._close_capture["recipient"],
            w._close_capture["token0"],
            w._close_capture["token1"],
            w._close_capture["dec0"],
            w._close_capture["dec1"],
            burn_sig="0xburn",
        )
    assert isinstance(result, CloseResult)
    # Burn is the close_sig (last successful funds-out-ish, but for snapshot notes)
    assert result.close_sig == "0xburn"
    # Burn gas is captured
    assert result.gas.get("0xburn") is not None
    # No leg carries the burn sig
    for leg in result.legs:
        assert leg.sig != "0xburn", leg
    # Only the decrease leg (one transfer) exists; collect mocked with zero transfer
    assert any(leg.sig == "0xdec" for leg in result.legs)
    print("✅ AERODROME BURN LEG ORDERING TEST PASS")
    return 0


if __name__ == "__main__":
    sys.exit(test_burn_sig_not_a_leg())
