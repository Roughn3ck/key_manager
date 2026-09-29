"""Unit tests for v5.3.28 BSC writer ownership pre-check and close sequence.

No on-chain broadcasts — only calldata correctness, signer resolution, and
close-state gating.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from venue_adapters.bsc_writer import BSCWriter
from venue_adapters.bsc_adapter import (
    SELECTOR_COLLECT,
    SELECTOR_DECREASE_LIQUIDITY,
    PANCAKE_V3_POSITION_MANAGER,
)


class _FakeAgent:
    def __init__(self, accounts):
        self.calls = []
        self.accounts = accounts
        self.addresses = {
            name: next(
                (a.get("address") for a in data.get("addresses", []) if a.get("address", "").startswith("0x")),
                None,
            )
            for name, data in accounts.items()
        }

    def __call__(self, cmd, **params):
        self.calls.append((cmd, params))
        if cmd == "list_accounts":
            return self.accounts
        if cmd == "get_address":
            name = params.get("account", "")
            return {"address": self.addresses.get(name, "")}
        if cmd == "status":
            return {"unlocked": True}
        return {}


class _FakeRpc:
    def __init__(self):
        self.calls = []
        self.owner = "0x1111111111111111111111111111111111111111"
        self.position_manager = PANCAKE_V3_POSITION_MANAGER
        self.liquidity = 1_000_000
        self.tokens_owed0 = 5_000
        self.tokens_owed1 = 3_000
        self.token0 = "0x0000000000000000000000000000000000000001"
        self.token1 = "0x0000000000000000000000000000000000000002"
        self.fee = 500

    def __call__(self, method, params):
        self.calls.append((method, params))
        if method != "eth_call":
            return None
        tx = params[0]
        to = tx.get("to", "").lower()
        data = tx.get("data", "")
        # ownerOf
        if data.startswith("0x6352211e"):
            return "0x" + "0" * 24 + self.owner[2:]
        # positions(uint256)
        if data.startswith("0x99fbab88"):
            body = (
                "0" * 64  # nonce
                + "0" * 64  # operator
                + self.token0[2:].zfill(64)  # token0
                + self.token1[2:].zfill(64)  # token1
                + f"{self.fee:064x}"  # fee
                + "0" * 64  # tickLower
                + "0" * 64  # tickUpper
                + f"{self.liquidity:064x}"  # liquidity
                + f"{self.tokens_owed0:064x}"  # feeGrowthInside0LastX128
                + f"{self.tokens_owed1:064x}"  # feeGrowthInside1LastX128
                + f"{self.tokens_owed0:064x}"  # tokensOwed0
                + f"{self.tokens_owed1:064x}"  # tokensOwed1
            )
            return "0x" + body
        return "0x"


def _new_writer():
    writer = BSCWriter.__new__(BSCWriter)
    writer.agent_url = ""
    writer.chain_id = 56
    writer.rpc_url = ""
    writer._unlocked = True
    return writer


def _set_rpc_handler(rpc):
    """Patch the module-level _bsc_rpc_call that bsc_writer imported."""
    import venue_adapters.bsc_writer as bw_mod
    bw_mod._bsc_rpc_call = rpc


def _restore_bsc_rpc_call():
    import venue_adapters.bsc_writer as bw_mod
    import venue_adapters.bsc_adapter as bsc_adapter_mod
    if getattr(bsc_adapter_mod, "_bsc_rpc_call_backup", None) is not None:
        bw_mod._bsc_rpc_call = bsc_adapter_mod._bsc_rpc_call_backup


def test_resolve_owner_signer_matches_derivable_address():
    writer = _new_writer()
    accounts = {
        "B1": {"addresses": [{"chain": "EVM", "chain_id": 56, "address": "0x1111111111111111111111111111111111111111"}]},
        "B2": {"addresses": [{"chain": "EVM", "chain_id": 56, "address": "0x2222222222222222222222222222222222222222"}]},
    }
    agent = _FakeAgent(accounts)
    rpc = _FakeRpc()
    _set_rpc_handler(rpc)
    writer._agent_call = agent

    signer = writer._resolve_owner_signer(123, rpc.position_manager)
    _restore_bsc_rpc_call()
    assert signer == "B1", signer
    print("PASS test_resolve_owner_signer_matches_derivable_address")


def test_resolve_owner_signer_no_match_raises():
    writer = _new_writer()
    accounts = {
        "B1": {"addresses": [{"chain": "EVM", "chain_id": 56, "address": "0x2222222222222222222222222222222222222222"}]},
    }
    agent = _FakeAgent(accounts)
    writer._agent_call = agent
    rpc = _FakeRpc()

    _set_rpc_handler(rpc)

    try:
        writer._resolve_owner_signer(123, rpc.position_manager)
    except RuntimeError as e:
        _restore_bsc_rpc_call()
        msg = str(e)
        assert "position owner" in msg, msg
        assert "matches no account" in msg, msg
        assert "0x1111111111111111111111111111111111111111" in msg, msg
        print("PASS test_resolve_owner_signer_no_match_raises")
        return
    _restore_bsc_rpc_call()
    raise AssertionError("expected RuntimeError")


def test_get_account_address_passes_chain_id():
    writer = _new_writer()
    calls = []

    def agent(cmd, **params):
        calls.append((cmd, params))
        if cmd == "get_address":
            return {"address": "0xabc123"}
        return {}

    writer._agent_call = agent
    addr = writer._get_account_address("B1")
    assert addr == "0xabc123"
    assert any(c[0] == "get_address" and c[1].get("chain_id") == 56 for c in calls), calls
    print("PASS test_get_account_address_passes_chain_id")


def test_collect_fees_calldata():
    writer = _new_writer()
    writer._get_account_address = lambda a: "0x1111111111111111111111111111111111111111"
    broadcasts = []

    def broadcast(account, to, data, value=0):
        broadcasts.append((account, to, data))
        return "0x" + "1" * 64

    writer._broadcast = broadcast
    writer._verify_rpc_chain = lambda url, cid: None
    writer._find_position_manager = lambda tid: PANCAKE_V3_POSITION_MANAGER

    tx = writer.collect_fees(
        type("P", (), {"account": "B1", "position_id": "bsc:123", "recipient": ""})()
    )
    assert tx == "0x" + "1" * 64
    assert len(broadcasts) == 1
    account, to, data = broadcasts[0]
    assert account == "B1"
    assert to.lower() == PANCAKE_V3_POSITION_MANAGER.lower()
    assert data.startswith(SELECTOR_COLLECT)
    assert data[10:74].lower().endswith("7b")  # tokenId padded
    print("PASS test_collect_fees_calldata")


def test_close_position_sequence_with_burn():
    writer = _new_writer()
    writer._get_account_address = lambda a: "0x1111111111111111111111111111111111111111"
    receipts = {}
    global _burn_test_broadcasts
    _burn_test_broadcasts = []

    def broadcast(account, to, data, value=0):
        nonlocal receipts
        _burn_test_broadcasts.append(data)
        tx_hash = f"0x{len(receipts):064x}"
        receipts[tx_hash] = {"status": "0x1"}
        return tx_hash

    writer._broadcast = broadcast
    writer._wait_for_tx_receipt = lambda h, timeout=120: receipts.get(h)
    writer._verify_rpc_chain = lambda url, cid: None

    rpc = _FakeRpc()
    _set_rpc_handler(rpc)

    # Pin the position manager so all state reads route to the same contract.
    writer._find_position_manager = lambda tid: rpc.position_manager

    # First positions() read has liquidity; post-collect read returns zero state.
    original_liquidity = rpc.liquidity
    call_count = [0]

    def tracking_rpc(method, params):
        if method == "eth_call":
            data = params[0].get("data", "")
            if data.startswith("0x99fbab88"):
                call_count[0] += 1
                if call_count[0] == 2:
                    rpc.liquidity = 0
                    rpc.tokens_owed0 = 0
                    rpc.tokens_owed1 = 0
        return rpc(method, params)

    import venue_adapters.bsc_writer as bw_mod
    bw_mod._bsc_rpc_call = tracking_rpc
    writer._rpc_call = tracking_rpc

    tx_hashes = writer.close_position("bsc:123", "B1")
    _restore_bsc_rpc_call()

    assert len(tx_hashes) == 3, tx_hashes  # decrease + collect + burn

    # Calldata checks (broadcasted calldata, not eth_call reads)
    broadcast_data = [b[2] for b in receipts.values() if isinstance(b, tuple)]
    # receipts is a dict of tx_hash -> {"status": ...}; broadcasts were captured
    # via the side-effect lambda above. Extract from a separate capture list.
    assert any(d.startswith(SELECTOR_DECREASE_LIQUIDITY) for d in _burn_test_broadcasts), _burn_test_broadcasts
    dec = next(d for d in _burn_test_broadcasts if d.startswith(SELECTOR_DECREASE_LIQUIDITY))
    assert dec[10:74].lower().endswith("7b")  # tokenId
    assert int(dec[74:138], 16) == original_liquidity

    assert any(d.startswith(SELECTOR_COLLECT) for d in _burn_test_broadcasts), _burn_test_broadcasts
    assert any(d.startswith("0x42966c68") for d in _burn_test_broadcasts), _burn_test_broadcasts
    print("PASS test_close_position_sequence_with_burn")


def test_close_position_skips_burn_when_position_not_empty():
    writer = _new_writer()
    writer._get_account_address = lambda a: "0x1111111111111111111111111111111111111111"
    receipts = {}

    def broadcast(account, to, data, value=0):
        nonlocal receipts
        tx_hash = f"0x{len(receipts):064x}"
        receipts[tx_hash] = {"status": "0x1"}
        return tx_hash

    writer._broadcast = broadcast
    writer._wait_for_tx_receipt = lambda h, timeout=120: receipts.get(h)
    writer._verify_rpc_chain = lambda url, cid: None

    rpc = _FakeRpc()
    _set_rpc_handler(rpc)
    writer._find_position_manager = lambda tid: rpc.position_manager
    writer._rpc_call = rpc

    tx_hashes = writer.close_position("bsc:123", "B1")
    _restore_bsc_rpc_call()

    assert len(tx_hashes) == 2, tx_hashes  # decrease + collect, no burn
    burn_calls = [c for c in rpc.calls if c[0] == "eth_call" and c[1][0].get("data", "").startswith("0x42966c68")]
    assert not burn_calls
    print("PASS test_close_position_skips_burn_when_position_not_empty")


def main():
    test_resolve_owner_signer_matches_derivable_address()
    test_resolve_owner_signer_no_match_raises()
    test_get_account_address_passes_chain_id()
    test_collect_fees_calldata()
    test_close_position_sequence_with_burn()
    test_close_position_skips_burn_when_position_not_empty()
    print("ALL BSC WRITER TESTS PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
