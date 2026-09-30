"""Key Manager Agent gas-estimation guard tests (v5.3.28).

Verifies:
- sign_tx passes gas_limit=None to the agent and estimates + 20% buffers it.
- sign_tx aborts (does not fall back) when eth_estimateGas reverts or returns 0.
- broadcast_tx reports the real estimate error back to the caller.
- RPC rotation surfaces a real revert reason when the primary strips it.
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
        return "0" * 63 + "1"


_original_get_chain_id = kma.get_chain_id
_original_get_nonce = kma.get_nonce
_original_get_gas_price = kma.get_gas_price
_original_rpc_call = kma.rpc_call


def _install_chain_nonce_stub():
    kma.get_chain_id = lambda rpc: 56
    kma.get_nonce = lambda rpc, addr: 0


def _restore_all():
    kma.get_chain_id = _original_get_chain_id
    kma.get_nonce = _original_get_nonce
    kma.get_gas_price = _original_get_gas_price
    kma.rpc_call = _original_rpc_call


def test_sign_tx_estimates_and_buffers_20_percent():
    _install_chain_nonce_stub()

    estimated = []

    def stub_rpc_call(rpc_url, method, params):
        if method == "eth_estimateGas":
            estimated.append(rpc_url)
            return {"result": "0x5208"}  # 21000
        if method == "eth_gasPrice":
            return {"result": "0x1"}
        raise RuntimeError(f"unexpected {method}")

    kma.get_gas_price = lambda rpc: {"gasPrice": 1, "maxFeePerGas": 1, "maxPriorityFeePerGas": 0}
    kma.rpc_call = stub_rpc_call

    agent = DummyAgent()
    res = agent.sign_tx(
        account="test",
        to="0x0000000000000000000000000000000000000001",
        data="0x",
        value="0",
        chain_id=56,
        rpc="https://bsc.io",
        gas_limit=None,
        gas_price=1,
    )
    assert res["status"] == "ok", res
    # 21000 * 1.2 = 25200
    assert res["result"]["gas_limit"] == 25200, res["result"]
    assert estimated == ["https://bsc.io"], estimated
    print("PASS test_sign_tx_estimates_and_buffers_20_percent")


def test_sign_tx_aborts_on_estimate_revert():
    _install_chain_nonce_stub()

    calls = []

    def stub_rpc_call(rpc_url, method, params):
        calls.append((rpc_url, method))
        if method == "eth_estimateGas":
            return {"error": {"message": "execution reverted: BAD_POSITION"}}
        if method == "eth_gasPrice":
            return {"result": "0x1"}
        raise RuntimeError(f"unexpected {method}")

    kma.get_gas_price = lambda rpc: {"gasPrice": 1, "maxFeePerGas": 1, "maxPriorityFeePerGas": 0}
    kma.rpc_call = stub_rpc_call

    agent = DummyAgent()
    res = agent.sign_tx(
        account="test",
        to="0x0000000000000000000000000000000000000001",
        data="0x",
        value="0",
        chain_id=56,
        rpc="https://bsc.io",
        gas_limit=None,
        gas_price=1,
    )
    assert res["status"] == "error", res
    assert "gas estimation failed" in res["error"], res["error"]
    assert "BAD_POSITION" in res["error"], res["error"]
    assert "not broadcasting" in res["error"], res["error"]
    print("PASS test_sign_tx_aborts_on_estimate_revert")


def test_sign_tx_aborts_on_zero_estimate():
    _install_chain_nonce_stub()

    def stub_rpc_call(rpc_url, method, params):
        if method == "eth_estimateGas":
            return {"result": "0x0"}
        if method == "eth_gasPrice":
            return {"result": "0x1"}
        raise RuntimeError(f"unexpected {method}")

    kma.get_gas_price = lambda rpc: {"gasPrice": 1, "maxFeePerGas": 1, "maxPriorityFeePerGas": 0}
    kma.rpc_call = stub_rpc_call

    agent = DummyAgent()
    res = agent.sign_tx(
        account="test",
        to="0x0000000000000000000000000000000000000001",
        data="0x",
        value="0",
        chain_id=56,
        rpc="https://bsc.io",
        gas_limit=None,
        gas_price=1,
    )
    assert res["status"] == "error", res
    assert "gas estimation failed" in res["error"], res["error"]
    assert "not broadcasting" in res["error"], res["error"]
    print("PASS test_sign_tx_aborts_on_zero_estimate")


def test_broadcast_tx_aborts_when_sign_tx_estimation_fails():
    _install_chain_nonce_stub()

    def stub_rpc_call(rpc_url, method, params):
        if method == "eth_estimateGas":
            return {"error": {"message": "execution reverted: LIQUIDITY_ZERO"}}
        if method == "eth_gasPrice":
            return {"result": "0x1"}
        raise RuntimeError(f"unexpected {method}")

    kma.get_gas_price = lambda rpc: {"gasPrice": 1, "maxFeePerGas": 1, "maxPriorityFeePerGas": 0}
    kma.rpc_call = stub_rpc_call

    agent = DummyAgent()
    res = agent.broadcast_tx(
        account="test",
        to="0x0000000000000000000000000000000000000001",
        data="0x",
        value="0",
        chain_id=56,
        rpc="https://bsc.io",
        gas_limit=None,
    )
    assert res["status"] == "error", res
    assert "gas estimation failed" in res["error"], res["error"]
    assert "LIQUIDITY_ZERO" in res["error"], res["error"]
    print("PASS test_broadcast_tx_aborts_when_sign_tx_estimation_fails")


def test_estimate_gas_rotates_to_fallback_for_real_revert():
    _install_chain_nonce_stub()

    calls = []

    def stub_rpc_call(rpc_url, method, params):
        calls.append(rpc_url)
        if method == "eth_estimateGas":
            if "stripping" in rpc_url:
                # Primary strips revert data to a generic short message.
                return {"error": {"message": "execution reverted"}}
            # Fallback returns the real reason.
            return {"error": {"message": "execution reverted: EXPIRED_DEADLINE"}}
        if method == "eth_gasPrice":
            return {"result": "0x1"}
        raise RuntimeError(f"unexpected {method}")

    kma.get_gas_price = lambda rpc: {"gasPrice": 1, "maxFeePerGas": 1, "maxPriorityFeePerGas": 0}
    kma.rpc_call = stub_rpc_call

    agent = DummyAgent()
    # Use BSC chain_id so fallbacks are looked up; primary is fake stripping endpoint.
    res = agent.sign_tx(
        account="test",
        to="0x0000000000000000000000000000000000000001",
        data="0x",
        value="0",
        chain_id=56,
        rpc="https://stripping.bsc.io",
        gas_limit=None,
        gas_price=1,
    )
    assert res["status"] == "error", res
    assert "EXPIRED_DEADLINE" in res["error"], res["error"]
    # Should have hit the stripping primary plus at least one fallback.
    assert len(calls) >= 2, calls
    print("PASS test_estimate_gas_rotates_to_fallback_for_real_revert")


def main():
    try:
        test_sign_tx_estimates_and_buffers_20_percent()
        test_sign_tx_aborts_on_estimate_revert()
        test_sign_tx_aborts_on_zero_estimate()
        test_broadcast_tx_aborts_when_sign_tx_estimation_fails()
        test_estimate_gas_rotates_to_fallback_for_real_revert()
    finally:
        _restore_all()
    print("✅ GAS ESTIMATE GUARD TESTS PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
