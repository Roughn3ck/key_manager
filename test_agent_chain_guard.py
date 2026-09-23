"""Key Manager Agent chain-identity guard tests (v5.3.17).

Stubs get_chain_id and get_nonce so these tests need no network. Verifies:
- sign_tx aborts when RPC chain_id != expected chain_id.
- broadcast_tx aborts with the same guard (via sign_tx first).
- sign_tx proceeds when chain_id matches the stub.
"""
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).parent / "src"))

import key_manager_agent as kma
from key_manager_agent import KeyManagerAgent


class DummyAgent(KeyManagerAgent):
    def __init__(self):
        # Bypass the real vault init.
        self.vault_path = Path("/dev/null")
        self.password = ""
        self.session_timeout = 600
        self.last_activity = 1 << 30
        self.unlocked = True
        self.vault_data = {"accounts": {}, "private_keys": {}}
        self.crypto = None

    def _check_session(self):
        pass

    def _get_private_key(self, account, chain="EVM", chain_id=None):
        # A dummy 32-byte private key (value 1) that private_key_to_address accepts.
        return "0" * 63 + "1"


_original_get_chain_id = kma.get_chain_id
_original_get_nonce = kma.get_nonce


def install_stub(rpc_to_chain, nonce=0):
    def stub_get_chain_id(rpc):
        return rpc_to_chain.get(rpc, 0)

    def stub_get_nonce(rpc, addr):
        return nonce

    kma.get_chain_id = stub_get_chain_id
    kma.get_nonce = stub_get_nonce


def restore_stub():
    kma.get_chain_id = _original_get_chain_id
    kma.get_nonce = _original_get_nonce


def test_sign_tx_rejects_wrong_chain():
    install_stub({"https://bad.io": 10, "https://base.io": 8453})
    agent = DummyAgent()
    res = agent.sign_tx(
        account="test", to="0x0000000000000000000000000000000000000001",
        data="0x", value="0", chain_id=8453, rpc="https://bad.io",
        gas_limit=200000, gas_price=1_000_000_000,
    )
    assert res["status"] == "error", res
    assert "RPC https://bad.io serves chain 10, expected 8453" in res["error"]
    assert "refusing to sign/broadcast" in res["error"]


def test_sign_tx_accepts_matching_chain():
    install_stub({"https://base.io": 8453})
    agent = DummyAgent()
    res = agent.sign_tx(
        account="test", to="0x0000000000000000000000000000000000000001",
        data="0x", value="0", chain_id=8453, rpc="https://base.io",
        gas_limit=200000, gas_price=1_000_000_000,
    )
    assert res["status"] == "ok", res
    assert res["result"]["chain_id"] == 8453


def test_broadcast_tx_rejects_wrong_chain():
    install_stub({"https://bad.io": 10})
    agent = DummyAgent()
    res = agent.broadcast_tx(
        account="test", to="0x0000000000000000000000000000000000000001",
        data="0x", value="0", chain_id=8453, rpc="https://bad.io",
        gas_limit=200000, gas_price=1_000_000_000,
    )
    assert res["status"] == "error", res
    assert "RPC https://bad.io serves chain 10, expected 8453" in res["error"]


def main():
    try:
        test_sign_tx_rejects_wrong_chain()
        test_sign_tx_accepts_matching_chain()
        test_broadcast_tx_rejects_wrong_chain()
    finally:
        restore_stub()
    print("✅ AGENT CHAIN GUARD TESTS PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
