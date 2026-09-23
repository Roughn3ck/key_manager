"""Writer-side chain guard unit test (v5.3.17).

Verifies that VenueWriter._verify_rpc_chain raises when the RPC returns an
unexpected chainId, and that AerodromeWriter._broadcast catches it before any
agent call or gas-balance read.
"""
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).parent / "src"))

import urllib.request
from unittest.mock import patch

from venue_adapters.aerodrome_writer import AerodromeWriter


def _json_response(obj):
    import json
    return json.dumps(obj).encode()


def test_aerodrome_broadcast_rejects_wrong_chain():
    writer = AerodromeWriter(agent_url="http://127.0.0.1:8842")
    writer._unlocked = True

    # Stub the RPC to claim chainId 999 (HyperEVM) instead of the expected 8453 (Base).
    def fake_urlopen(req, **kwargs):
        class Resp:
            def read(self):
                return _json_response({"jsonrpc": "2.0", "id": 1, "result": "0x3e7"})
            def __enter__(self): return self
            def __exit__(self, *args): return False
        return Resp()

    with patch.object(urllib.request, "urlopen", fake_urlopen):
        try:
            writer._broadcast("test", "0x0000000000000000000000000000000000000001", "0x")
        except RuntimeError as e:
            msg = str(e)
            assert "serves chain 999, expected 8453" in msg, msg
            assert "refusing gas/broadcast check" in msg, msg
            return
    raise AssertionError("expected RuntimeError for wrong-chain RPC")


def test_aerodrome_broadcast_accepts_base_chain():
    writer = AerodromeWriter(agent_url="http://127.0.0.1:8842")
    writer._unlocked = True

    calls = []

    def fake_urlopen(req, **kwargs):
        calls.append(req.full_url)
        class Resp:
            def read(self):
                if req.full_url == writer.rpc_url:
                    return _json_response({"jsonrpc": "2.0", "id": 1, "result": "0x2105"})
                # Agent call: status then broadcast_tx
                return _json_response({"status": "ok", "result": {"unlocked": True}})
            def __enter__(self): return self
            def __exit__(self, *args): return False
        return Resp()

    with patch.object(urllib.request, "urlopen", fake_urlopen):
        try:
            writer._broadcast("test", "0x0000000000000000000000000000000000000001", "0x")
        except Exception as e:
            msg = str(e)
            # Either an empty-balance error OR an empty-tx-hash error is fine:
            # the important thing is the chain guard did NOT fire before those.
            assert "serves chain" not in msg, msg
            # The chain check should have made at least two calls (pre + post guard).
            assert calls.count(writer.rpc_url) >= 2, calls
            return
    raise AssertionError("expected an exception, not success")


def main():
    test_aerodrome_broadcast_rejects_wrong_chain()
    test_aerodrome_broadcast_accepts_base_chain()
    print("✅ WRITER CHAIN GUARD TESTS PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
