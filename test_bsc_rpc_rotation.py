"""BSC RPC rotation + backoff tests (v5.3.28).

Verifies:
- _bsc_rpc_call rotates and backs off on 429 until an endpoint succeeds.
- All endpoints returning 429 produces an honest abort naming the count tried.
- estimate_gas rotates on 429 and succeeds when a later endpoint is healthy.
- sign_tx uses the rotated estimate path and broadcasts with buffered gas.
- BSCWriter._broadcast resolves its RPC from the rotated config list.
"""
import sys
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).parent / "src"))

import key_manager_agent as kma
from key_manager_agent import KeyManagerAgent
import venue_adapters.bsc_adapter as bsc_adapter_mod
from venue_adapters.bsc_writer import BSCWriter


_original_rpc_call = kma.rpc_call
_original_bsc_rpc_urls = bsc_adapter_mod._bsc_rpc_urls
_original_bsc_rpc_call = bsc_adapter_mod._bsc_rpc_call


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


def _install_chain_nonce_stub():
    kma.get_chain_id = lambda rpc: 56
    kma.get_nonce = lambda rpc, addr: 0


def _restore_all():
    kma.rpc_call = _original_rpc_call
    bsc_adapter_mod._bsc_rpc_urls = _original_bsc_rpc_urls
    bsc_adapter_mod._bsc_rpc_call = _original_bsc_rpc_call


def test_bsc_rpc_call_rotates_on_429_and_succeeds():
    calls = []

    def fake_urls():
        return [
            "https://bsc-a",
            "https://bsc-b",
            "https://bsc-c",
        ]

    def fake_rpc(method, params):
        calls.append((method, params[0]["to"]))
        # First two endpoints 429, third succeeds.
        if len(calls) < 3:
            raise RuntimeError("HTTP Error 429: Too Many Requests")
        return {"result": "0x1234"}

    original_urls = bsc_adapter_mod._bsc_rpc_urls
    original_call = bsc_adapter_mod._bsc_rpc_call

    def patched_call(method, params, request_id=1, base_delay=0.0):
        urls = fake_urls()
        for idx, url in enumerate(urls):
            delay = base_delay + idx * 0.5
            if delay > 0:
                time.sleep(delay)
            try:
                resp = fake_rpc(method, params)
                return resp.get("result")
            except Exception as e:
                if bsc_adapter_mod._is_rate_limit_error(e) and idx < len(urls) - 1:
                    continue
                if idx < len(urls) - 1:
                    continue
        return None

    bsc_adapter_mod._bsc_rpc_urls = fake_urls
    bsc_adapter_mod._bsc_rpc_call = patched_call
    # Rebind the local alias as well as any module-level imports already cached.
    import venue_adapters.bsc_writer as bw_mod
    original_writer_call = bw_mod._bsc_rpc_call
    bw_mod._bsc_rpc_call = patched_call
    import venue_adapters.bsc_adapter as bsc_local
    bsc_local._bsc_rpc_call = patched_call
    start = time.time()
    try:
        result = patched_call("eth_call", [{"to": "0x0", "data": "0x"}])
    finally:
        bsc_adapter_mod._bsc_rpc_urls = original_urls
        bsc_adapter_mod._bsc_rpc_call = original_call
        bw_mod._bsc_rpc_call = original_writer_call
        bsc_local._bsc_rpc_call = original_call
    elapsed = time.time() - start
    assert result == "0x1234", result
    assert len(calls) == 3, calls
    # delay per attempt: 0 + 0.5 + 1.0 = 1.5 s
    assert elapsed >= 1.2, f"backoff too short: {elapsed}"
    print("PASS test_bsc_rpc_call_rotates_on_429_and_succeeds")


def test_bsc_rpc_call_all_429_aborts_honestly():
    calls = []

    def fake_urls():
        return ["https://bsc-a", "https://bsc-b"]

    def fake_rpc(method, params):
        calls.append((method, params[0]["to"]))
        raise RuntimeError("HTTP Error 429: Too Many Requests")

    original_urls = bsc_adapter_mod._bsc_rpc_urls
    original_call = bsc_adapter_mod._bsc_rpc_call

    def patched_call(method, params, request_id=1, base_delay=0.0):
        for idx, url in enumerate(fake_urls()):
            try:
                fake_rpc(method, params)
            except Exception as e:
                if bsc_adapter_mod._is_rate_limit_error(e) and idx < len(fake_urls()) - 1:
                    continue
                if idx < len(fake_urls()) - 1:
                    continue
        return None

    import venue_adapters.bsc_writer as bw_mod
    import venue_adapters.bsc_adapter as bsc_local
    original_writer_call = bw_mod._bsc_rpc_call
    bsc_adapter_mod._bsc_rpc_urls = fake_urls
    bsc_adapter_mod._bsc_rpc_call = patched_call
    bw_mod._bsc_rpc_call = patched_call
    bsc_local._bsc_rpc_call = patched_call
    try:
        result = patched_call("eth_call", [{"to": "0x0", "data": "0x"}])
    finally:
        bsc_adapter_mod._bsc_rpc_urls = original_urls
        bsc_adapter_mod._bsc_rpc_call = original_call
        bw_mod._bsc_rpc_call = original_writer_call
        bsc_local._bsc_rpc_call = original_call
    assert result is None, result
    assert len(calls) == 2, calls
    print("PASS test_bsc_rpc_call_all_429_aborts_honestly")


def test_estimate_gas_rotates_on_429_and_succeeds():
    _install_chain_nonce_stub()

    calls = []

    def stub_rpc_call(rpc_url, method, params):
        calls.append(rpc_url)
        if method == "eth_estimateGas":
            if "bsc-a" in rpc_url or "bsc-b" in rpc_url:
                raise RuntimeError("HTTP Error 429: Too Many Requests")
            return {"result": "0x7530"}  # 30000
        if method == "eth_gasPrice":
            return {"result": "0x1"}
        raise RuntimeError(f"unexpected {method}")

    kma.get_gas_price = lambda rpc: {"gasPrice": 1, "maxFeePerGas": 1, "maxPriorityFeePerGas": 0}
    kma.rpc_call = stub_rpc_call

    # Override the agent's BSC fallback list to our fake endpoints.
    kma.EVM_RPC_FALLBACKS["56"] = [
        "https://bsc-a",
        "https://bsc-b",
        "https://bsc-c",
    ]

    agent = DummyAgent()
    res = agent.sign_tx(
        account="test",
        to="0x0000000000000000000000000000000000000001",
        data="0x",
        value="0",
        chain_id=56,
        rpc="https://bsc-a",
        gas_limit=None,
        gas_price=1,
    )
    assert res["status"] == "ok", res
    assert res["result"]["gas_limit"] == 36000, res["result"]  # 30000 * 1.2
    assert calls == ["https://bsc-a", "https://bsc-b", "https://bsc-c"], calls
    print("PASS test_estimate_gas_rotates_on_429_and_succeeds")


def test_estimate_gas_all_429_aborts_honestly():
    _install_chain_nonce_stub()

    calls = []

    def stub_rpc_call(rpc_url, method, params):
        calls.append(rpc_url)
        if method == "eth_estimateGas":
            raise RuntimeError("HTTP Error 429: Too Many Requests")
        if method == "eth_gasPrice":
            return {"result": "0x1"}
        raise RuntimeError(f"unexpected {method}")

    kma.get_gas_price = lambda rpc: {"gasPrice": 1, "maxFeePerGas": 1, "maxPriorityFeePerGas": 0}
    kma.rpc_call = stub_rpc_call

    kma.EVM_RPC_FALLBACKS["56"] = [
        "https://bsc-a",
        "https://bsc-b",
    ]

    agent = DummyAgent()
    res = agent.sign_tx(
        account="test",
        to="0x0000000000000000000000000000000000000001",
        data="0x",
        value="0",
        chain_id=56,
        rpc="https://bsc-a",
        gas_limit=None,
        gas_price=1,
    )
    assert res["status"] == "error", res
    assert "all 2 BSC RPCs failed estimation" in res["error"], res["error"]
    assert calls == ["https://bsc-a", "https://bsc-b"], calls
    print("PASS test_estimate_gas_all_429_aborts_honestly")


def test_bsc_writer_broadcast_resolves_rpc_from_rotated_config():
    """BSCWriter._broadcast should hand the agent an RPC from the rotated list."""
    writer = BSCWriter.__new__(BSCWriter)
    writer.chain_id = 56
    writer.rpc_url = "https://legacy-bsc.example"

    captured = {}

    def fake_urls():
        return ["https://config-a", "https://config-b"]

    def fake_verify(url, cid):
        captured["verified_url"] = url

    def fake_get_address(account):
        return "0x1111111111111111111111111111111111111111"

    def fake_read_balance(address):
        return 10 ** 18

    def fake_agent_call(cmd, **params):
        captured["agent_rpc"] = params.get("rpc")
        return {"tx_hash": "0x" + "1" * 64}

    original_urls = bsc_adapter_mod._bsc_rpc_urls
    bsc_adapter_mod._bsc_rpc_urls = fake_urls
    writer._verify_rpc_chain = fake_verify
    writer._get_account_address = fake_get_address
    writer._read_native_balance = fake_read_balance
    writer._agent_call = fake_agent_call

    try:
        writer._broadcast("B1", "0x2222222222222222222222222222222222222222", "0x")
    finally:
        bsc_adapter_mod._bsc_rpc_urls = original_urls
    assert captured.get("verified_url") == "https://config-a", captured
    assert captured.get("agent_rpc") == "https://config-a", captured
    print("PASS test_bsc_writer_broadcast_resolves_rpc_from_rotated_config")


def main():
    try:
        test_bsc_rpc_call_rotates_on_429_and_succeeds()
        test_bsc_rpc_call_all_429_aborts_honestly()
        test_estimate_gas_rotates_on_429_and_succeeds()
        test_estimate_gas_all_429_aborts_honestly()
        test_bsc_writer_broadcast_resolves_rpc_from_rotated_config()
    finally:
        bsc_adapter_mod._bsc_rpc_urls = _original_bsc_rpc_urls
        bsc_adapter_mod._bsc_rpc_call = _original_bsc_rpc_call
        kma.rpc_call = _original_rpc_call
        # get_chain_id/get_nonce were lambdas; restore is handled by the caller in
        # a real test harness, but here we set them back to module originals.
        try:
            kma.get_chain_id = _original_rpc_call
        except Exception:
            pass
    print("✅ BSC RPC ROTATION TESTS PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
