"""Unit tests for v5.3.28 staked Aerodrome gauge operations.

No on-chain broadcasts — only calldata correctness, state-machine gating,
pre-flight revert handling, idempotent resume paths, and a DB-copy recorder
row test.
"""
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from venue_adapters.aerodrome_adapter import (
    SELECTOR_CLAIM_EMISSIONS,
    SELECTOR_GAUGE_GET_REWARD_UINT,
    SELECTOR_GAUGE_WITHDRAW,
    AERO_TOKEN,
)
from venue_adapters.aerodrome_gauge_writer import (
    AerodromeGaugeWriter,
    GaugeStepResult,
)


class _FakeBaseWriter:
    def __init__(self):
        self.address = "0x40c33B69e7aB4B22Eb8ec7D164e155F769F8c948"
        self.broadcasts = []
        self.receipts = {}
        self.close_position_calls = []

    def _get_account_address(self, account: str):
        return self.address

    def _broadcast(self, account, to, data, gas_check_address=""):
        self.broadcasts.append((account, to, data, gas_check_address))
        tx_hash = f"0x{len(self.broadcasts):064x}"
        # Default receipt: success.
        self.receipts.setdefault(tx_hash, {"status": "0x1"})
        return tx_hash

    def _wait_for_tx_receipt(self, tx_hash, timeout=120, poll_interval=2.0):
        return self.receipts.get(tx_hash)

    def close_position(self, position_id, account):
        self.close_position_calls.append((position_id, account))
        return ["0x" + "c" * 64]


class _FakeRpc:
    def __init__(self):
        self.calls = []
        self.owner = "0x61E0B10423a0009C3f83ab4313813d29437d0817"  # gauge
        self.post_owner = None  # if set, returned by 2nd ownerOf call
        self.pool = "0x42d4a22cad0f5a49681a5715ce994af73a43b76b"
        self.preflight_should_revert = False
        self.position_manager = "0xe1f8cd9AC4e4A65F54f38a5CdAfCA44f6dD68b53"
        self.voter = "0x16613524e02ad97eDfeF371bC883F2F5d6C480A5"
        self._owner_calls = 0
        self.transfer_logs = []  # list of log dicts for eth_getLogs stake transfer

    def __call__(self, method, params):
        self.calls.append((method, params))
        if method == "eth_call":
            tx = params[0]
            data = tx.get("data", "")
            to = tx.get("to", "").lower()
            # ownerOf
            if data.startswith("0x6352211e"):
                self._owner_calls += 1
                owner = self.owner if self._owner_calls == 1 else (self.post_owner or self.owner)
                return "0x" + "0" * 24 + owner[2:]
            # positions(uint256)
            if data.startswith("0x99fbab88"):
                if to.lower() == self.position_manager.lower():
                    return "0x" + "1" + "0" * 831  # nonce>0
                return "0x" + "0" * 832
            # voter.gauges(address) -> gauge address
            if data.startswith("0x4b138fea"):
                return "0x" + "0" * 24 + self.owner[2:]
            # gauge.pool()
            if data == "0x16f0115b":
                return "0x" + "0" * 24 + self.pool[2:]
            # claim / withdraw pre-flight
            if self.preflight_should_revert:
                raise RuntimeError("execution reverted: NA")
            # A successful eth_call returns 0x for write functions.
            return "0x"
        if method == "eth_getLogs":
            return self.transfer_logs
        return None


class _FakeRpcWithClaimEmissions:
    """RPC stub where the gauge implementation exposes claimEmissions."""
    def __init__(self):
        self.calls = []

    def __call__(self, method, params):
        self.calls.append((method, params))
        if method == "eth_getCode":
            addr = params[0].lower()
            if addr == "0x1111111111111111111111111111111111111111":
                # Minimal EIP-1167 clone delegate marker containing claimEmissions only
                return ("0x363d3d373d3d3d363d73"
                        "2222222222222222222222222222222222222222"
                        "5af43d82803e903d91602b57fd5bf3")
            if addr == "0x2222222222222222222222222222222222222222":
                # Implementation bytecode with only claimEmissions selector
                return "0x" + SELECTOR_CLAIM_EMISSIONS[2:] + "00" * 100
        if method == "eth_call":
            return "0x"
        return None


def _new_writer():
    writer = AerodromeGaugeWriter.__new__(AerodromeGaugeWriter)
    writer.base_writer = _FakeBaseWriter()
    writer.price_engine = None
    writer.agent_url = ""
    return writer


def test_claim_emissions_calldata_deployed_generation():
    """G2 generation gauge uses getReward(uint256)."""
    writer = _new_writer()
    gauge = "0x61E0B10423a0009C3f83ab4313813d29437d0817"
    data = writer._claim_emissions_calldata(
        "0x40c33B69e7aB4B22Eb8ec7D164e155F769F8c948",
        "0x40c33B69e7aB4B22Eb8ec7D164e155F769F8c948",
        7088644,
        gauge,
    )
    assert data.startswith(SELECTOR_GAUGE_GET_REWARD_UINT), data
    assert len(data) == 74, f"length {len(data)}"
    assert int(data[10:], 16) == 7088644
    print("PASS test_claim_emissions_calldata_deployed_generation")


def test_claim_emissions_calldata_alt_generation():
    """Alternate generation with claimEmissions(address,address,uint256[])."""
    import venue_adapters.aerodrome_adapter as aero_mod
    orig = aero_mod._base_rpc_call
    aero_mod._base_rpc_call = _FakeRpcWithClaimEmissions()
    try:
        writer = _new_writer()
        gauge = "0x1111111111111111111111111111111111111111"
        data = writer._claim_emissions_calldata(
            "0x40c33B69e7aB4B22Eb8ec7D164e155F769F8c948",
            "0x40c33B69e7aB4B22Eb8ec7D164e155F769F8c948",
            7088644,
            gauge,
        )
        assert data.startswith(SELECTOR_CLAIM_EMISSIONS), data
        assert len(data) == 330, f"length {len(data)}"
    finally:
        aero_mod._base_rpc_call = orig
    print("PASS test_claim_emissions_calldata_alt_generation")


def test_withdraw_calldata():
    writer = _new_writer()
    gauge = "0x61E0B10423a0009C3f83ab4313813d29437d0817"
    data = writer._withdraw_calldata(7088644, gauge)
    assert data.startswith(SELECTOR_GAUGE_WITHDRAW), data
    assert len(data) == 74, f"length {len(data)}"
    assert int(data[10:], 16) == 7088644
    print("PASS test_withdraw_calldata")


def test_unstake_happy_path():
    writer = _new_writer()
    rpc = _FakeRpc()
    rpc.post_owner = writer.base_writer.address
    writer._rpc_call = rpc
    writer._find_position_manager = lambda tid: rpc.position_manager

    result = writer.unstake(7088644, "G2")
    assert result.step == "unstake"
    assert result.tx_hash is not None
    assert result.error is None
    assert not result.skipped
    assert any(c[0] == "eth_call" and c[1][0]["data"].startswith(SELECTOR_GAUGE_WITHDRAW)
               for c in rpc.calls)
    print("PASS test_unstake_happy_path")


def test_unstake_idempotent_when_already_unstaked():
    writer = _new_writer()
    rpc = _FakeRpc()
    rpc.owner = writer.base_writer.address  # wallet already owns NFT
    writer._rpc_call = rpc
    writer._find_position_manager = lambda tid: rpc.position_manager

    result = writer.unstake(7088644, "G2")
    assert result.skipped
    assert "already unstaked" in (result.error or "")
    assert result.tx_hash is None
    print("PASS test_unstake_idempotent_when_already_unstaked")


def test_preflight_revert_aborts_before_broadcast():
    writer = _new_writer()
    rpc = _FakeRpc()
    rpc.preflight_should_revert = True
    writer._rpc_call = rpc
    writer._find_position_manager = lambda tid: rpc.position_manager

    result = writer.claim_emissions(7088644, "G2")
    assert result.error is not None
    assert "Pre-flight revert" in result.error
    assert len(writer.base_writer.broadcasts) == 0, "broadcast should not happen"
    print("PASS test_preflight_revert_aborts_before_broadcast")


def test_guided_close_state_machine():
    writer = _new_writer()
    rpc = _FakeRpc()
    rpc.post_owner = writer.base_writer.address
    writer._rpc_call = rpc
    writer._find_position_manager = lambda tid: rpc.position_manager

    result = writer.close_staked_position("base:7088644", "G2", db_path=None)
    assert len(result.steps) == 2
    assert result.steps[0].step == "claim"
    assert result.steps[1].step == "unstake"
    assert len(result.close_tx_hashes) == 1
    assert result.close_tx_hashes == ["0x" + "c" * 64]
    assert result.error is None
    # The close flow used the bound account, not the gauge address.
    assert writer.base_writer.close_position_calls == [("base:7088644", "G2")]
    print("PASS test_guided_close_state_machine")


def test_close_staked_position_records_claim():
    """Guided close should write a FEE_EVENTS row for the AERO claim."""
    import coldtrack.db as db_mod

    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "coldtrack.db"
        db = db_mod.ColdTrackDB(db_path)
        db.init_schema()
        conn = db.conn()
        conn.execute(
            "INSERT INTO PORTFOLIOS (NAME, TYPE) VALUES (?, ?)",
            ("test", "internal"),
        )
        conn.execute(
            "INSERT INTO ACCOUNTS (PORTFOLIO_ID, NAME, TYPE) VALUES (?, ?, ?)",
            (1, "G2", "wallet"),
        )
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO LP_POSITIONS (ACCOUNT_ID, POOL_NAME, PLATFORM, CHAIN, TOKEN_ID, STATUS, TOKEN_A, TOKEN_B, OPENED_DATE) "
            "VALUES ((SELECT ID FROM ACCOUNTS WHERE NAME=?), ?, ?, ?, ?, ?, ?, ?, ?)",
            ("G2", "ETH/cbBTC", "Aerodrome", "Base", "7088644", "active", "ETH", "cbBTC", "2026-01-01"),
        )
        pos_id = cur.lastrowid
        db.commit()
        db.close()

        writer = _new_writer()
        rpc = _FakeRpc()
        rpc.post_owner = writer.base_writer.address
        writer._rpc_call = rpc
        writer._find_position_manager = lambda tid: rpc.position_manager
        writer._read_erc20_balance = lambda token, wallet: 1_000 * (10 ** 18)

        result = writer.close_staked_position("base:7088644", "G2", db_path=db_path, base_dir=Path(tmp))
        assert result.error is None, result.error

        conn2 = sqlite3.connect(db_path)
        conn2.row_factory = sqlite3.Row
        row = conn2.execute(
            "SELECT * FROM FEE_EVENTS WHERE POSITION_ID = ?", (pos_id,)
        ).fetchone()
        assert row is not None, "FEE_EVENTS row missing"
        assert row["SOURCE"] == "HARVEST", row["SOURCE"]
        assert "gauge" in row["NOTES"].lower(), row["NOTES"]
        conn2.close()
    print("PASS test_close_staked_position_records_claim")


def test_claim_recorded_as_fee_event():
    """A claim should write a FEE_EVENTS row with SOURCE='HARVEST'."""
    import coldtrack.db as db_mod

    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "coldtrack.db"
        db = db_mod.ColdTrackDB(db_path)
        db.init_schema()
        conn = db.conn()
        conn.execute(
            "INSERT INTO PORTFOLIOS (NAME, TYPE) VALUES (?, ?)",
            ("test", "internal"),
        )
        conn.execute(
            "INSERT INTO ACCOUNTS (PORTFOLIO_ID, NAME, TYPE) VALUES (?, ?, ?)",
            (1, "G2", "wallet"),
        )
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO LP_POSITIONS (ACCOUNT_ID, POOL_NAME, PLATFORM, CHAIN, TOKEN_ID, STATUS, TOKEN_A, TOKEN_B, OPENED_DATE) "
            "VALUES ((SELECT ID FROM ACCOUNTS WHERE NAME=?), ?, ?, ?, ?, ?, ?, ?, ?)",
            ("G2", "ETH/cbBTC", "Aerodrome", "Base", "7088644", "active", "ETH", "cbBTC", "2026-01-01"),
        )
        pos_id = cur.lastrowid
        db.commit()
        db.close()

        writer = _new_writer()
        rpc = _FakeRpc()
        writer._rpc_call = rpc
        writer._find_position_manager = lambda tid: rpc.position_manager
        writer._read_erc20_balance = lambda token, wallet: 1_000 * (10 ** 18)

        result = writer.claim_and_record(7088644, "G2", pos_id, db_path)
        assert result.error is None, result.error

        # Re-open and verify FEE_EVENTS row.
        conn2 = sqlite3.connect(db_path)
        conn2.row_factory = sqlite3.Row
        row = conn2.execute(
            "SELECT * FROM FEE_EVENTS WHERE POSITION_ID = ?", (pos_id,)
        ).fetchone()
        assert row is not None, "FEE_EVENTS row missing"
        assert row["SOURCE"] == "HARVEST", row["SOURCE"]
        assert "gauge" in row["NOTES"].lower(), row["NOTES"]
        assert row["TOKEN_A_AMT"] is not None
        conn2.close()
    print("PASS test_claim_recorded_as_fee_event")


def main():
    test_claim_emissions_calldata_deployed_generation()
    test_claim_emissions_calldata_alt_generation()
    test_withdraw_calldata()
    test_unstake_happy_path()
    test_unstake_idempotent_when_already_unstaked()
    test_preflight_revert_aborts_before_broadcast()
    test_guided_close_state_machine()
    test_close_staked_position_records_claim()
    test_claim_recorded_as_fee_event()
    print("ALL AERODROME GAUGE WRITER TESTS PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
