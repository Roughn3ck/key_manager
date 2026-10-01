"""Unit tests for v5.3.29 HyperEVM ownership pre-check and compound recording.

No on-chain broadcasts — only stubbed RPC reads, signer resolution, and the
compound recorder's atomic DB writes.
"""
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

import venue_adapters.hyperliquid_adapter as hl_mod
from coldtrack.compound_recorder import CompoundResult, CompoundLeg, record_compound_and_export
from coldtrack.db import ColdTrackDB


# ---------------------------------------------------------------------------
# Adapter ownerOf tests
# ---------------------------------------------------------------------------

def test_owner_of_helper_reads_project_x_manager():
    """owner_of(token_id) reads ownerOf on the Project X Position Manager."""
    owner = "0x8958bd96896de55bfe31b1a6eb2b280ebe098509"
    calls = []

    def stub(method, params):
        calls.append((method, params))
        if method == "eth_call":
            data = params[0].get("data", "")
            if data.startswith("0x6352211e"):  # ownerOf
                return "0x" + "0" * 24 + owner[2:]
        return None

    orig = hl_mod._evm_rpc_call
    hl_mod._evm_rpc_call = stub
    try:
        result = hl_mod.owner_of(545983)
        assert result == owner.lower(), result
        assert len(calls) == 1, calls
        assert calls[0][1][0]["to"].lower() == hl_mod.POSITION_MANAGER.lower(), calls[0]
        print("PASS test_owner_of_helper_reads_project_x_manager")
    finally:
        hl_mod._evm_rpc_call = orig


def test_owner_of_helper_returns_none_on_revert():
    """owner_of returns None when the call returns the zero address / no data."""
    def stub(method, params):
        if method == "eth_call":
            return "0x" + "0" * 64
        return None

    orig = hl_mod._evm_rpc_call
    hl_mod._evm_rpc_call = stub
    try:
        assert hl_mod.owner_of(545983) is None
        print("PASS test_owner_of_helper_returns_none_on_revert")
    finally:
        hl_mod._evm_rpc_call = orig


# ---------------------------------------------------------------------------
# Writer signer resolution tests
# ---------------------------------------------------------------------------

def _new_writer():
    from venue_adapters.hyperliquid_writer import HyperliquidWriter
    writer = HyperliquidWriter.__new__(HyperliquidWriter)
    writer.agent_url = ""
    writer.chain_id = 999
    writer.rpc_url = ""
    writer._unlocked = True
    return writer


def test_resolve_owner_signer_matches_derivable_address():
    writer = _new_writer()
    owner = "0x8958bd96896de55bfe31b1a6eb2b280ebe098509"

    def fake_agent_call(cmd, **params):
        if cmd == "list_accounts":
            return {
                "G5": {"addresses": [{"chain": "EVM", "address": owner}]},
                "G6": {"addresses": [{"chain": "EVM", "address": "0x2222222222222222222222222222222222222222"}]},
            }
        if cmd == "get_address":
            name = params.get("account", "")
            return {"address": owner if name == "G5" else "0x2222222222222222222222222222222222222222"}
        return {}

    writer._agent_call = fake_agent_call
    writer._get_position_owner = lambda tid: owner

    signer = writer._resolve_owner_signer(545983)
    assert signer == "G5", signer
    print("PASS test_resolve_owner_signer_matches_derivable_address")


def test_resolve_owner_signer_no_match_raises():
    writer = _new_writer()
    owner = "0x8958bd96896de55bfe31b1a6eb2b280ebe098509"

    def fake_agent_call(cmd, **params):
        if cmd == "list_accounts":
            return {
                "G5": {"addresses": [{"chain": "EVM", "address": "0x2222222222222222222222222222222222222222"}]},
            }
        return {}

    writer._agent_call = fake_agent_call
    writer._get_position_owner = lambda tid: owner

    try:
        writer._resolve_owner_signer(545983)
    except RuntimeError as e:
        msg = str(e)
        assert "position owner" in msg and "matches no account" in msg, msg
        print("PASS test_resolve_owner_signer_no_match_raises")
        return
    raise AssertionError("expected RuntimeError")


# ---------------------------------------------------------------------------
# lp_tab pre-check tests
# ---------------------------------------------------------------------------

def _make_lp_tab():
    from lp_tab import LPTab
    from unittest.mock import MagicMock
    gui = MagicMock()
    gui.key_manager = None
    tab = LPTab.__new__(LPTab)
    tab.gui = gui
    return tab


def test_lp_verify_hyperliquid_position_ownership_match():
    from lp_engine import LPPosition

    tab = _make_lp_tab()
    owner = "0x8958bd96896de55bfe31b1a6eb2b280ebe098509"

    orig = hl_mod._evm_rpc_call
    def stub(method, params):
        if method == "eth_call" and params[0].get("data", "").startswith("0x6352211e"):
            return "0x" + "0" * 24 + owner[2:]
        return None
    hl_mod._evm_rpc_call = stub

    try:
        from unittest.mock import MagicMock
        writer = MagicMock()
        writer.is_available.return_value = True
        writer._get_account_address.return_value = owner
        writer._resolve_owner_signer.return_value = "G5"

        pos = LPPosition(position_id="hyperevm:545983", venue="HyperEVM", chain="HyperEVM")
        account = tab._lp_verify_hyperliquid_position_ownership(pos, 545983, "G5", writer)
        assert account == "G5", account
        print("PASS test_lp_verify_hyperliquid_position_ownership_match")
    finally:
        hl_mod._evm_rpc_call = orig


def test_lp_verify_hyperliquid_position_ownership_mismatch():
    from lp_engine import LPPosition

    tab = _make_lp_tab()
    owner = "0x8958bd96896de55bfe31b1a6eb2b280ebe098509"

    orig = hl_mod._evm_rpc_call
    def stub(method, params):
        if method == "eth_call" and params[0].get("data", "").startswith("0x6352211e"):
            return "0x" + "0" * 24 + owner[2:]
        return None
    hl_mod._evm_rpc_call = stub

    try:
        from unittest.mock import MagicMock
        writer = MagicMock()
        writer.is_available.return_value = True
        writer._get_account_address.return_value = "0x2222222222222222222222222222222222222222"
        writer._resolve_owner_signer.side_effect = RuntimeError(
            "position owner 0x8958... matches no account in this vault for HyperEVM "
            "(derivable: 0x2222...)."
        )

        pos = LPPosition(position_id="hyperevm:545983", venue="HyperEVM", chain="HyperEVM")
        try:
            tab._lp_verify_hyperliquid_position_ownership(pos, 545983, "G5", writer)
        except RuntimeError as e:
            msg = str(e)
            assert "position owner" in msg or "matches no account" in msg or "refetch" in msg, msg
            print("PASS test_lp_verify_hyperliquid_position_ownership_mismatch")
            return
        raise AssertionError("expected RuntimeError")
    finally:
        hl_mod._evm_rpc_call = orig


def test_lp_verify_hyperliquid_position_ownership_stale_token():
    from lp_engine import LPPosition

    tab = _make_lp_tab()

    orig = hl_mod._evm_rpc_call
    def stub(method, params):
        if method == "eth_call" and params[0].get("data", "").startswith("0x6352211e"):
            return "0x" + "0" * 64  # zero address = not a live NFT
        return None
    hl_mod._evm_rpc_call = stub

    try:
        from unittest.mock import MagicMock
        writer = MagicMock()
        writer.is_available.return_value = True

        pos = LPPosition(position_id="hyperevm:545983", venue="HyperEVM", chain="HyperEVM")
        try:
            tab._lp_verify_hyperliquid_position_ownership(pos, 545983, "G5", writer)
        except RuntimeError as e:
            msg = str(e)
            assert "stale record" in msg or "not a live NFT" in msg or "refetch" in msg, msg
            print("PASS test_lp_verify_hyperliquid_position_ownership_stale_token")
            return
        raise AssertionError("expected RuntimeError")
    finally:
        hl_mod._evm_rpc_call = orig


# ---------------------------------------------------------------------------
# Compound recorder DB tests
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


def test_compound_recorder_writes_fee_event_and_basis():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "coldtrack.db"
        pos_id = _seed_db(db_path)

        result = CompoundResult(
            position_mint="545983",
            platform="HyperEVM",
            chain="HyperEVM",
            tx_hash="0xcompoundhash",
            collect_sig="0xcollecthash",
            legs=[
                CompoundLeg(asset="WHYPE", amount=1.5, value_usd=30.0, sig="0xcompoundhash"),
                CompoundLeg(asset="UBTC", amount=0.0001, value_usd=12.0, sig="0xcompoundhash"),
            ],
            block_time_iso="2026-09-30T12:00:00+00:00",
            token_price_usd={"WHYPE": 20.0, "UBTC": 120000.0},
            final_amounts={"WHYPE": 1.5, "UBTC": 0.0001},
            owner="0x8958bd96896de55bfe31b1a6eb2b280ebe098509",
        )

        out = record_compound_and_export(result, db_path, base_dir=Path(tmp))
        assert out.get("ok"), out
        assert out["position_id"] == pos_id, out

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row

        # FEE_EVENTS: one row, SOURCE=HARVEST, tx hash dedupe key.
        fee = conn.execute(
            "SELECT * FROM FEE_EVENTS WHERE TX_HASH=?", ("0xcompoundhash",)
        ).fetchone()
        assert fee is not None, "FEE_EVENTS row missing"
        assert fee["POSITION_ID"] == pos_id
        assert fee["SOURCE"] == "HARVEST"
        assert fee["NOTES"] == "auto-compound — fees reinvested"
        assert fee["TOKEN_A_AMT"] == 1.5
        assert fee["TOKEN_B_AMT"] == 0.0001
        assert fee["VALUE_USD"] == 42.0

        # LP_POSITIONS: entry basis and fee totals grew.
        pos = conn.execute("SELECT * FROM LP_POSITIONS WHERE ID=?", (pos_id,)).fetchone()
        assert pos["AMOUNT_A_ENTRY"] == 101.5, pos["AMOUNT_A_ENTRY"]
        assert pos["AMOUNT_B_ENTRY"] == 0.0051, pos["AMOUNT_B_ENTRY"]
        assert pos["TOTAL_VALUE_USD_ENTRY"] == 542.0, pos["TOTAL_VALUE_USD_ENTRY"]
        assert pos["FEES_CLAIMED_USD"] == 42.0, pos["FEES_CLAIMED_USD"]
        assert pos["FEES_EARNED_USD"] == 42.0, pos["FEES_EARNED_USD"]

        # LP_SNAPSHOTS: post-compound snapshot with tx hash in notes.
        snap = conn.execute(
            "SELECT * FROM LP_SNAPSHOTS WHERE LP_POSITION_ID=?", (pos_id,)
        ).fetchone()
        assert snap is not None, "LP_SNAPSHOTS row missing"
        assert snap["TOKEN_A_AMOUNT"] == 1.5
        assert snap["TOKEN_B_AMOUNT"] == 0.0001
        assert snap["TOKEN_A_PRICE_USD"] == 20.0
        assert snap["TOKEN_B_PRICE_USD"] == 120000.0
        assert snap["IN_RANGE"] == 1
        assert "auto-compound" in snap["NOTES"]
        assert "0xcompoundhash" in snap["NOTES"]

        # NO CAPITAL_EVENTS row for an internal reinvestment.
        cap = conn.execute(
            "SELECT COUNT(*) as n FROM CAPITAL_EVENTS WHERE POSITION_ID=?", (pos_id,)
        ).fetchone()
        assert cap["n"] == 0, cap

        conn.close()
        print("PASS test_compound_recorder_writes_fee_event_and_basis")


def test_compound_recorder_dedupes_by_tx_hash():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "coldtrack.db"
        _seed_db(db_path)

        result = CompoundResult(
            position_mint="545983",
            platform="HyperEVM",
            chain="HyperEVM",
            tx_hash="0xcompoundhash",
            legs=[CompoundLeg(asset="WHYPE", amount=1.0, value_usd=20.0, sig="0xcompoundhash")],
            block_time_iso="2026-09-30T12:00:00+00:00",
        )

        out1 = record_compound_and_export(result, db_path, base_dir=Path(tmp))
        assert out1.get("fee_events") == 1, out1
        out2 = record_compound_and_export(result, db_path, base_dir=Path(tmp))
        assert out2.get("fee_events") == 0, out2

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        count = conn.execute(
            "SELECT COUNT(*) as n FROM FEE_EVENTS WHERE TX_HASH=?", ("0xcompoundhash",)
        ).fetchone()["n"]
        conn.close()
        assert count == 1, count
        print("PASS test_compound_recorder_dedupes_by_tx_hash")


def test_compound_recorder_already_closed_appends_notes():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "coldtrack.db"
        _seed_db(db_path)

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("UPDATE LP_POSITIONS SET STATUS='closed' WHERE TOKEN_ID=?", ("545983",))
        pos_id = conn.execute("SELECT ID FROM LP_POSITIONS WHERE TOKEN_ID=?", ("545983",)).fetchone()["ID"]
        conn.commit()
        conn.close()

        result = CompoundResult(
            position_mint="545983",
            platform="HyperEVM",
            chain="HyperEVM",
            tx_hash="0xcompoundhash",
            legs=[CompoundLeg(asset="WHYPE", amount=1.0, value_usd=20.0, sig="0xcompoundhash")],
            block_time_iso="2026-09-30T12:00:00+00:00",
        )

        out = record_compound_and_export(result, db_path, base_dir=Path(tmp))
        assert out.get("ok"), out
        assert out.get("fee_events") == 0, out
        assert "already closed" in out.get("note", ""), out

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        pos = conn.execute("SELECT NOTES FROM LP_POSITIONS WHERE ID=?", (pos_id,)).fetchone()
        conn.close()
        assert "ColdStack compound replay sigs" in (pos["NOTES"] or ""), pos
        print("PASS test_compound_recorder_already_closed_appends_notes")


# ---------------------------------------------------------------------------
# CLI test
# ---------------------------------------------------------------------------

def test_cli_compound_subcommand_records_to_db():
    from coldtrack.__main__ import _cmd_compound
    import shutil

    tmp = Path(tempfile.mkdtemp())
    try:
        db_path = tmp / "coldtrack.db"
        pos_id = _seed_db(db_path)

        ret = _cmd_compound([
            "--db", str(db_path),
            "--token-id", "545983",
            "--tx-hash", "0xclihash",
            "--collect-hash", "0xcollecthash",
            "--amount-a", "2.0",
            "--amount-b", "0.0002",
            "--value-usd", "52.0",
            "--price-a", "20.0",
            "--price-b", "120000.0",
            "--date", "2026-09-30T12:00:00+00:00",
        ])
        assert ret == 0, ret

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        fee = conn.execute(
            "SELECT * FROM FEE_EVENTS WHERE TX_HASH=?", ("0xclihash",)
        ).fetchone()
        assert fee is not None
        # Prices provided => VALUE_USD is sum of price-derived leg values.
        assert fee["VALUE_USD"] == 64.0, fee["VALUE_USD"]
        pos = conn.execute("SELECT * FROM LP_POSITIONS WHERE ID=?", (pos_id,)).fetchone()
        assert pos["TOTAL_VALUE_USD_ENTRY"] == 564.0, pos["TOTAL_VALUE_USD_ENTRY"]
        conn.close()
        print("PASS test_cli_compound_subcommand_records_to_db")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    test_owner_of_helper_reads_project_x_manager()
    test_owner_of_helper_returns_none_on_revert()
    test_resolve_owner_signer_matches_derivable_address()
    test_resolve_owner_signer_no_match_raises()
    test_lp_verify_hyperliquid_position_ownership_match()
    test_lp_verify_hyperliquid_position_ownership_mismatch()
    test_lp_verify_hyperliquid_position_ownership_stale_token()
    test_compound_recorder_writes_fee_event_and_basis()
    test_compound_recorder_dedupes_by_tx_hash()
    test_compound_recorder_already_closed_appends_notes()
    test_cli_compound_subcommand_records_to_db()
    print("ALL HYPEREVM PRECHECK/COMPOUND TESTS PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
