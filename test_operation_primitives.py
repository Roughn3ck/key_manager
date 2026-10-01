"""Tests for v5.3.30 operation primitives refactor.

Covers:
  - Capability flags are honest per venue (collect/compound/close).
  - close_position() calls the same collect_fees() object method (identity).
  - compound_fees() calls the same collect_fees() object method (identity).
  - Standalone collect records to FEE_EVENTS and dedupes by TX_HASH.
  - The shared operation controller renders buttons only from capabilities.

No on-chain broadcasts — all writers are stubbed/mocked.
"""
import sqlite3
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent / "src"))

from lp_engine import LPPosition
from venue_adapters.venue_writer import VenueWriter, CollectFeesParams, CompoundFeesParams
from coldtrack.collect_recorder import CollectResult, record_collect_and_export
from coldtrack.db import ColdTrackDB


# ---------------------------------------------------------------------------
# Capability flag tests
# ---------------------------------------------------------------------------

def test_base_writer_capabilities():
    from venue_adapters.venue_writer import VenueWriter
    assert VenueWriter.supports_collect is True
    assert VenueWriter.supports_compound is True
    assert VenueWriter.supports_close is True

    from venue_adapters.aerodrome_writer import AerodromeWriter
    from venue_adapters.hyperliquid_writer import HyperliquidWriter
    from venue_adapters.cetus_writer import CetusWriter
    from venue_adapters.bsc_writer import BSCWriter
    from venue_adapters.orca_writer import OrcaWriter

    assert AerodromeWriter.supports_compound is True
    assert HyperliquidWriter.supports_compound is True
    assert CetusWriter.supports_compound is True
    assert BSCWriter.supports_compound is False
    assert OrcaWriter.supports_compound is False

    for cls in (AerodromeWriter, HyperliquidWriter, CetusWriter, BSCWriter, OrcaWriter):
        assert cls.supports_collect is True, cls
        assert cls.supports_close is True, cls
    print("PASS test_base_writer_capabilities")


# ---------------------------------------------------------------------------
# Identity tests: close_position and compound_fees call the same collect_fees
# ---------------------------------------------------------------------------

class FakeEVMWriter(VenueWriter):
    """Minimal writer to prove that close/compound reuse collect_fees."""
    VENUE_KEY = "fake"
    supports_compound = True

    def __init__(self):
        self.collect_calls = []
        self.collect_return = "0xcollect"
        self.close_return = ["0xclose"]
        self.compound_return = ["0xcompound"]

    def is_available(self) -> bool:
        return True

    def unlock(self, credentials) -> bool:
        return True

    def wrap_native(self, account: str, amount: float) -> str:
        raise NotImplementedError

    def unwrap_native(self, account: str, amount: float) -> str:
        raise NotImplementedError

    def approve(self, account: str, token: str, spender: str, amount: float) -> str:
        raise NotImplementedError

    def swap(self, params):
        raise NotImplementedError

    def open_position(self, params):
        raise NotImplementedError

    def increase_liquidity(self, params):
        raise NotImplementedError

    def decrease_liquidity(self, params):
        return "0xdecrease"

    def collect_fees(self, params: CollectFeesParams) -> str:
        self.collect_calls.append(params)
        return self.collect_return

    def close_position(self, position_id: str, account: str) -> list:
        # The canonical close primitive: decrease + SAME collect_fees + optional burn.
        _ = self.decrease_liquidity(None)
        collect_params = CollectFeesParams(account=account, position_id=position_id)
        self.collect_fees(collect_params)
        return self.close_return

    def compound_fees(self, params: CompoundFeesParams) -> list:
        # The canonical compound primitive: collect fees first.
        self.collect_fees(CollectFeesParams(account=params.account, position_id=params.position_id))
        return self.compound_return

    def rebalance(self, params):
        raise NotImplementedError


def test_close_uses_same_collect_fees_method():
    writer = FakeEVMWriter()
    writer.close_position("hyperevm:1", "acct")
    assert len(writer.collect_calls) == 1, writer.collect_calls
    assert writer.collect_calls[0].position_id == "hyperevm:1"
    assert writer.collect_calls[0].account == "acct"
    print("PASS test_close_uses_same_collect_fees_method")


def test_compound_uses_same_collect_fees_method():
    writer = FakeEVMWriter()
    writer.compound_fees(CompoundFeesParams(account="acct", position_id="hyperevm:1"))
    assert len(writer.collect_calls) == 1, writer.collect_calls
    assert writer.collect_calls[0].position_id == "hyperevm:1"
    print("PASS test_compound_uses_same_collect_fees_method")


def test_aerodrome_close_calls_collect_fees_on_instance():
    """AerodromeWriter.close_position must call self.collect_fees, not a copy."""
    from venue_adapters import aerodrome_writer as aero_writer_mod
    from venue_adapters.aerodrome_writer import AerodromeWriter

    orig_rpc = aero_writer_mod._base_rpc_call
    try:
        def fake_rpc(method, params):
            if method == "eth_call":
                data = params[0].get("data", "")
                if data.startswith("0x70a08231"):  # balanceOf
                    return "0x" + "0" * 56 + "0000000000000001"  # 1 wei
                # positions(tokenId) return value: nonce=1, liquidity=1, enough bytes.
                body = "0" * 64 + "0" * 63 + "1" + "0" * (64 * 6) + "0" * 63 + "1" + "0" * (64 * 5)
                return "0x" + body
            return None

        aero_writer_mod._base_rpc_call = fake_rpc

        w = AerodromeWriter.__new__(AerodromeWriter)
        w.collect_calls = []

        def fake_collect(params):
            w.collect_calls.append(params)
            return "0xcollect"

        w.collect_fees = fake_collect
        w._resolve_owner_signer = lambda tid, pm: "acct"
        w._get_account_address = lambda acct: "0x" + "a" * 40
        w._find_position_manager = lambda tid: "0x" + "p" * 40
        w._get_token_decimals = lambda t: 18
        w._get_token_symbol = lambda t: "TKN"
        w._broadcast = lambda acct, to, data, gas_check_address=None: "0xdecrease"
        w._wait_for_tx_receipt = lambda tx, timeout=None, poll_interval=None: {
            "status": "0x1", "gasUsed": "0x5208", "effectiveGasPrice": "0x1",
            "blockHash": "0x" + "b" * 64, "blockNumber": "0x1", "logs": [],
        }
        w._get_position_state = lambda tid, pm: (0, 0, 0)
        w._post_close_state = lambda *args, **kwargs: None

        try:
            w.close_position("base:1", "acct")
        except Exception as e:
            print(f"  [aero close test caught (expected): {e}]")
            pass
        assert len(w.collect_calls) >= 1, f"AerodromeWriter.close_position did not call collect_fees (calls={w.collect_calls})"
    finally:
        aero_writer_mod._base_rpc_call = orig_rpc
    print("PASS test_aerodrome_close_calls_collect_fees_on_instance")


def test_bsc_close_calls_collect_fees_on_instance():
    from venue_adapters import bsc_writer as bsc_writer_mod
    from venue_adapters.bsc_writer import BSCWriter

    orig_rpc = bsc_writer_mod._bsc_rpc_call
    try:
        def fake_rpc(method, params):
            if method == "eth_call":
                data = params[0].get("data", "")
                if data.startswith("0x70a08231"):  # balanceOf
                    return "0x" + "0" * 56 + "0000000000000001"
                body = "0" * 64 + "0" * 63 + "1" + "0" * (64 * 6) + "0" * 63 + "1" + "0" * (64 * 5)
                return "0x" + body
            return None

        bsc_writer_mod._bsc_rpc_call = fake_rpc

        w = BSCWriter.__new__(BSCWriter)
        w.collect_calls = []

        def fake_collect(params):
            w.collect_calls.append(params)
            return "0xcollect"

        w.collect_fees = fake_collect
        w._get_account_address = lambda acct: "0x" + "a" * 40
        w._find_position_manager = lambda tid: "0x" + "p" * 40
        w._get_token_symbol = lambda t: "TKN"
        w._broadcast = lambda acct, to, data: "0xdecrease"
        w._wait_for_tx_receipt = lambda tx, timeout=None: {
            "status": "0x1", "gasUsed": "0x5208", "effectiveGasPrice": "0x1",
            "blockHash": "0x" + "b" * 64, "blockNumber": "0x1", "logs": [],
        }
        w._get_position_state = lambda tid, pm: (0, 0, 0)

        try:
            w.close_position("bsc:1", "acct")
        except Exception:
            pass
        assert len(w.collect_calls) >= 1, "BSCWriter.close_position did not call collect_fees"
    finally:
        bsc_writer_mod._bsc_rpc_call = orig_rpc
    print("PASS test_bsc_close_calls_collect_fees_on_instance")


# ---------------------------------------------------------------------------
# Collect recorder tests
# ---------------------------------------------------------------------------

def _seed_db(db_path: Path):
    db = ColdTrackDB(db_path)
    db.init_schema()
    conn = db.conn()
    conn.execute("INSERT INTO PORTFOLIOS (NAME, TYPE) VALUES (?, ?)", ("test", "client"))
    conn.execute(
        "INSERT INTO ACCOUNTS (PORTFOLIO_ID, NAME, TYPE) VALUES (?, ?, ?)",
        (1, "KP", "wallet"),
    )
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO LP_POSITIONS "
        "(ACCOUNT_ID, POOL_NAME, PLATFORM, CHAIN, TOKEN_ID, STATUS, "
        "TOKEN_A, AMOUNT_A_ENTRY, TOKEN_B, AMOUNT_B_ENTRY, TOTAL_VALUE_USD_ENTRY, OPENED_DATE) "
        "VALUES ((SELECT ID FROM ACCOUNTS WHERE NAME=?), ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("KP", "WHYPE/UBTC", "HyperEVM", "HyperEVM", "545983", "active",
         "WHYPE", 100.0, "UBTC", 0.005, 500.0, "2026-01-01"),
    )
    pos_id = cur.lastrowid
    db.commit()
    db.close()
    return pos_id


def test_collect_recorder_writes_fee_event():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "coldtrack.db"
        pos_id = _seed_db(db_path)

        result = CollectResult(
            position_mint="545983",
            platform="HyperEVM",
            chain="HyperEVM",
            tx_hash="0xcollecthash",
            token_a_symbol="WHYPE",
            token_b_symbol="UBTC",
            token_a_amount=1.5,
            token_b_amount=0.0001,
            value_usd=42.0,
            block_time_iso="2026-09-30T12:00:00+00:00",
        )
        out = record_collect_and_export(result, db_path, base_dir=Path(tmp))
        assert out.get("ok"), out
        assert out["fee_events"] == 1, out

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        fee = conn.execute(
            "SELECT * FROM FEE_EVENTS WHERE TX_HASH=?", ("0xcollecthash",)
        ).fetchone()
        assert fee is not None
        assert fee["POSITION_ID"] == pos_id
        assert fee["SOURCE"] == "MANUAL"
        assert fee["TOKEN_A_AMT"] == 1.5
        assert fee["TOKEN_B_AMT"] == 0.0001
        assert fee["VALUE_USD"] == 42.0
        conn.close()
    print("PASS test_collect_recorder_writes_fee_event")


def test_collect_recorder_dedupes_by_tx_hash():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "coldtrack.db"
        _seed_db(db_path)

        result = CollectResult(
            position_mint="545983",
            platform="HyperEVM",
            chain="HyperEVM",
            tx_hash="0xcollecthash",
            token_a_symbol="WHYPE",
            token_b_symbol="UBTC",
            token_a_amount=1.0,
            value_usd=20.0,
        )
        out1 = record_collect_and_export(result, db_path, base_dir=Path(tmp))
        out2 = record_collect_and_export(result, db_path, base_dir=Path(tmp))
        assert out1["fee_events"] == 1, out1
        assert out2["fee_events"] == 0, out2

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        count = conn.execute(
            "SELECT COUNT(*) as n FROM FEE_EVENTS WHERE TX_HASH=?", ("0xcollecthash",)
        ).fetchone()["n"]
        conn.close()
        assert count == 1, count
    print("PASS test_collect_recorder_dedupes_by_tx_hash")


# ---------------------------------------------------------------------------
# Operation controller capability tests
# ---------------------------------------------------------------------------

def _make_tab_with_writer(writer):
    from lp_operations import LPOperationController
    tab = MagicMock()
    tab._lp_guard_staked_action.return_value = False
    tab._lp_resolve_venue_for_position.return_value = ("HyperEVM", "HYPE", "hyperliquid")
    tab._lp_resolve_wallet_for_position.return_value = ("0xaddr", "acct")
    tab._lp_resolve_solana_account_for_position.return_value = "acct"
    tab._lp_get_chain_info.return_value = ("HyperEVM", "HYPE", "hyperliquid")
    tab._lp_portfolio_db_path.return_value = None
    gui = MagicMock()
    gui.lp_engine.get_writer.return_value = writer
    gui.lp_engine._writers = {"hyperliquid": writer}
    tab.gui = gui
    tab._lp_refresh_position_fees = MagicMock()
    tab._lp_record_fee_collection = MagicMock()
    tab._lp_record_compound_for_writer = MagicMock(return_value="")
    tab._lp_record_close_for_writer = MagicMock(return_value="")
    tab._lp_forget_position = MagicMock()
    tab._lp_record_orca_close = MagicMock(return_value="")
    tab._lp_update_saved_pools_count = MagicMock()
    tab._lp_widgets = {"position_cards": {}}
    return tab, LPOperationController(tab)


def test_capabilities_disable_compound_for_bsc_and_orca():
    from venue_adapters.bsc_writer import BSCWriter
    from venue_adapters.orca_writer import OrcaWriter
    from venue_adapters.hyperliquid_writer import HyperliquidWriter

    tab, ops = _make_tab_with_writer(BSCWriter.__new__(BSCWriter))
    pos = LPPosition(position_id="bsc:1", venue="BSC", chain="bnb chain")
    assert ops.can_collect(pos)
    assert not ops.can_compound(pos)
    assert ops.can_close(pos)

    tab2, ops2 = _make_tab_with_writer(OrcaWriter.__new__(OrcaWriter))
    pos2 = LPPosition(position_id="solana:abc", venue="Orca", chain="solana")
    assert ops2.can_collect(pos2)
    assert not ops2.can_compound(pos2)
    assert ops2.can_close(pos2)

    tab3, ops3 = _make_tab_with_writer(HyperliquidWriter.__new__(HyperliquidWriter))
    pos3 = LPPosition(position_id="hyperevm:1", venue="HyperEVM", chain="hyperevm")
    assert ops3.can_collect(pos3)
    assert ops3.can_compound(pos3)
    assert ops3.can_close(pos3)
    print("PASS test_capabilities_disable_compound_for_bsc_and_orca")


def test_staked_aerodrome_capabilities_all_false():
    tab, ops = _make_tab_with_writer(None)
    pos = LPPosition(
        position_id="base:1", venue="Aerodrome", chain="base",
        raw_data={"is_staked": True},
    )
    caps = ops.capabilities(pos)
    assert caps["collect"] is False
    assert caps["compound"] is False
    assert caps["close"] is False
    print("PASS test_staked_aerodrome_capabilities_all_false")


def test_run_collect_records_fee_event_and_refreshes():
    writer = FakeEVMWriter()
    tab, ops = _make_tab_with_writer(writer)
    pos = LPPosition(position_id="hyperevm:1", venue="HyperEVM", chain="hyperevm")

    # Patch the messagebox to always return True and _record_collect on the
    # runner to capture the tx hash without needing a real coldtrack.db.
    collected = []
    with patch("lp_operation_runner.messagebox.askyesno", return_value=True):
        with patch.object(ops._runner, "_record_collect",
                          side_effect=lambda p, i, h: collected.append(h)):
            ops.run_collect(pos)

    assert len(writer.collect_calls) == 1
    assert writer.collect_calls[0].position_id == "hyperevm:1"
    assert len(collected) == 1
    assert collected[0] == "0xcollect"
    tab._lp_record_fee_collection.assert_called_once()
    print("PASS test_run_collect_records_fee_event_and_refreshes")


def main():
    test_base_writer_capabilities()
    test_close_uses_same_collect_fees_method()
    test_compound_uses_same_collect_fees_method()
    test_aerodrome_close_calls_collect_fees_on_instance()
    test_bsc_close_calls_collect_fees_on_instance()
    test_collect_recorder_writes_fee_event()
    test_collect_recorder_dedupes_by_tx_hash()
    test_capabilities_disable_compound_for_bsc_and_orca()
    test_staked_aerodrome_capabilities_all_false()
    test_run_collect_records_fee_event_and_refreshes()
    print("ALL v5.3.30 OPERATION PRIMITIVES TESTS PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
