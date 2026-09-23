"""Aerodrome close-recorder test â€” atomic 4-table write on a COPY of the pack db.

Fixtures the pack portfolio's LP_POSITIONS row 7 (EURC/cbBTC, token_id '75255240')
with a synthetic Aerodrome CloseResult; asserts column-exact writes, idempotent
snapshot upsert, and the no-match â†’ pending path. Live DBs stay read-only.

Run:  python test_close_recorder_aerodrome.py
"""
import json
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).parent / "src"))

from coldtrack.close_recorder import (  # noqa: E402
    CloseLeg, CloseResult, CloseRecorder, retry_pending,
)
from coldtrack.db import ColdTrackDB  # noqa: E402

LIVE_PACK = Path(r"B:\OpenClaw\.openclaw\workspace\kimi\portfolios\the-pack-portfolio\coldtrack.db")


def _rows(conn, sql, params=()):
    c = conn.cursor()
    c.execute(sql, params)
    cols = [d[0] for d in c.description]
    return [dict(zip(cols, r)) for r in c.fetchall()]


def main():
    tmp = Path(tempfile.mkdtemp(prefix="aero_rec_"))
    try:
        db_path = tmp / "coldtrack.db"
        shutil.copy(LIVE_PACK, db_path)
        db = ColdTrackDB(db_path); db.init_schema()

        # Confirm the fixture row exists (pack db row 7: EURC/cbBTC Aerodrome).
        # v5.3.17: the live LP NFT id is 75255240; the saved row currently holds
        # the dead id 74933503. This test exercises the fallback platform+pool
        # match + TOKEN_ID sync path.
        cur = db._conn.cursor()
        hit = _rows(db._conn,
                    "SELECT ID, ACCOUNT_ID, TOKEN_ID, TOKEN_A, TOKEN_B FROM LP_POSITIONS WHERE TOKEN_ID=?",
                    ("74933503",))
        if not hit:
            # Row may have already been synced; fall back to the live id.
            hit = _rows(db._conn,
                        "SELECT ID, ACCOUNT_ID, TOKEN_ID, TOKEN_A, TOKEN_B FROM LP_POSITIONS WHERE TOKEN_ID=?",
                        ("75255240",))
        assert hit, "pack db must contain the EURC/cbBTC Aerodrome fixture row"
        pos_id = hit[0]["ID"]
        assert hit[0]["TOKEN_A"] == "EURC" and hit[0]["TOKEN_B"] == "cbBTC", hit[0]

        # Submit a CloseResult keyed to the LIVE NFT id.
        res = CloseResult(
            position_mint="75255240",
            platform="Aerodrome", chain="Base",
            legs=[
                CloseLeg(asset="EURC", amount=872.04, value_usd=940.0,
                         kind="liquidity", sig="0xdec"),
                CloseLeg(asset="cbBTC", amount=0.01323, value_usd=1268.0,
                         kind="liquidity", sig="0xdec"),
                CloseLeg(asset="EURC", amount=3.21, value_usd=3.50,
                         kind="fee", sig="0xcol"),
                CloseLeg(asset="cbBTC", amount=0.00011, value_usd=10.0,
                         kind="fee", sig="0xcol"),
            ],
            close_sig="0xcol", collect_sig="0xcol", decrease_sig="0xdec",
            block_time_iso="2026-09-22T07:30:00+00:00",
            gas={"0xdec": 0.0000009, "0xcol": 0.0000009},
            gas_asset="ETH",
            token_price_usd={"EURC": 1.078, "cbBTC": 96_000.0},
            final_amounts={"EURC": 872.04, "cbBTC": 0.01323},
        )

        out = CloseRecorder(db).record(res)
        assert out["ok"], out

        # LP_POSITIONS — closed + CLOSED_DATE, and TOKEN_ID synced to live id.
        pos = _rows(db._conn, "SELECT STATUS, CLOSED_DATE, TOKEN_ID, NOTES FROM LP_POSITIONS WHERE ID=?", (pos_id,))[0]
        assert pos["STATUS"] == "closed" and pos["CLOSED_DATE"] == "2026-09-22T07:30:00+00:00", pos
        assert pos["TOKEN_ID"] == "75255240", pos
        assert "identifier sync: TOKEN_ID 74933503 -> 75255240" in (pos["NOTES"] or ""), pos

        # TRANSACTIONS — liquidity legs carry the DECREASE sig ('0xdec'); fee legs the
        # COLLECT sig ('0xcol'); CHAIN='Base'; CATEGORY lp/yield; gas FEE_ASSET='ETH'.
        txs = _rows(db._conn, "SELECT * FROM TRANSACTIONS WHERE TX_HASH IN ('0xdec','0xcol') ORDER BY ID")
        assert len(txs) == 4, txs
        for t in txs:
            assert t["CHAIN"] == "Base"
            assert t["FEE_ASSET"] == "ETH" and t["FEE_AMOUNT"] == 0.0000009
        liq = [t for t in txs if t["TYPE"] == "lp_withdraw"]
        fees = [t for t in txs if t["TYPE"] == "yield"]
        assert len(liq) == 2 and len(fees) == 2
        assert all(t["TX_HASH"] == "0xdec" for t in liq), "liquidity legs attribute the decrease tx"
        assert all(t["TX_HASH"] == "0xcol" for t in fees), "fee legs attribute the collect tx"

        # FEE_EVENTS — SOURCE='HARVEST', TX_HASH=collect sig, A/B amounts in DB order
        # (token_a/b from the row: EURC/cbBTC). The live Pack DB may already have
        # a real collect fee row, so assert on the new ColdStack row specifically.
        fe = _rows(db._conn, "SELECT * FROM FEE_EVENTS WHERE POSITION_ID=? AND TX_HASH=?", (pos_id, "0xcol"))
        assert len(fe) == 1, fe
        assert fe[0]["SOURCE"] == "HARVEST"
        assert fe[0]["TOKEN_A_AMT"] == 3.21 and fe[0]["TOKEN_B_AMT"] == 0.00011

        # LP_SNAPSHOTS — one close row, sigs + CLOSED marker in NOTES.
        sn = _rows(db._conn, "SELECT * FROM LP_SNAPSHOTS WHERE LP_POSITION_ID=?", (pos_id,))
        assert len(sn) == 1
        assert "CLOSED via ColdStack" in (sn[0]["NOTES"] or "")
        assert "0xdec" in sn[0]["NOTES"] and "0xcol" in sn[0]["NOTES"]
        assert sn[0]["IN_RANGE"] == 0
        assert sn[0]["TOKEN_A_AMOUNT"] == 872.04 and sn[0]["TOKEN_B_AMOUNT"] == 0.01323
        db.close()

        # Idempotency — snapshot stays single-row; re-record with same live id ok.
        db2 = ColdTrackDB(db_path); db2.init_schema()
        out2 = CloseRecorder(db2).record(res)
        assert out2["ok"]
        ns = _rows(db2._conn, "SELECT COUNT(*) AS n FROM LP_SNAPSHOTS WHERE LP_POSITION_ID=?", (pos_id,))[0]["n"]
        assert ns == 1, "snapshot upsert must stay single-row per (position,date)"
        db2.close()

        # Direct TOKEN_ID match still works after the sync.
        db4 = ColdTrackDB(db_path); db4.init_schema()
        res2 = CloseResult(
            position_mint="75255240",
            platform="Aerodrome", chain="Base",
            legs=[CloseLeg(asset="EURC", amount=1.0, kind="fee", sig="0xcol")],
            collect_sig="0xcol", block_time_iso="2026-09-22T07:30:00+00:00",
        )
        out4 = CloseRecorder(db4).record(res2)
        # Position is already closed, but match should still succeed.
        assert out4.get("ok"), out4
        db4.close()

        # Already-closed row → NOTES-only append, no duplicate tx/fee rows.
        db5 = ColdTrackDB(db_path); db5.init_schema()
        before_tx = _rows(db5._conn, "SELECT COUNT(*) AS n FROM TRANSACTIONS")[0]["n"]
        before_fe = _rows(db5._conn, "SELECT COUNT(*) AS n FROM FEE_EVENTS")[0]["n"]
        replay = CloseResult(
            position_mint="75255240",
            platform="Aerodrome", chain="Base",
            legs=[CloseLeg(asset="EURC", amount=872.04, kind="liquidity", sig="0xdec2")],
            close_sig="0xdec2", collect_sig="0xcol2", decrease_sig="0xdec2",
            block_time_iso="2026-09-23T07:30:00+00:00",
        )
        out5 = CloseRecorder(db5).record(replay)
        assert out5.get("ok"), out5
        assert out5["transactions"] == 0 and out5["fee_events"] == 0
        after_tx = _rows(db5._conn, "SELECT COUNT(*) AS n FROM TRANSACTIONS")[0]["n"]
        after_fe = _rows(db5._conn, "SELECT COUNT(*) AS n FROM FEE_EVENTS")[0]["n"]
        assert after_tx == before_tx, "already-closed must not duplicate TRANSACTIONS"
        assert after_fe == before_fe, "already-closed must not duplicate FEE_EVENTS"
        notes5 = _rows(db5._conn, "SELECT NOTES FROM LP_POSITIONS WHERE ID=?", (pos_id,))[0]["NOTES"] or ""
        assert "ColdStack close replay sigs: 0xdec2, 0xcol2" in notes5, notes5
        db5.close()

        # No-match → pending, zero rows written.
        bad = CloseResult(position_mint="999999999", platform="Aerodrome", chain="Base",
                          legs=[CloseLeg(asset="EURC", amount=1.0, kind="fee", sig="0xcol")],
                          collect_sig="0xcol", block_time_iso="2026-09-22T07:30:00+00:00")
        db3 = ColdTrackDB(db_path); db3.init_schema()
        before = _rows(db3._conn, "SELECT COUNT(*) AS n FROM TRANSACTIONS")[0]["n"]
        out3 = CloseRecorder(db3).record(bad)
        # With multiple Aerodrome/Base active rows in the pack db, the fallback
        # returns ambiguous rather than no-match — both are valid "write nothing".
        assert not out3.get("ok")
        assert "no LP_POSITIONS row" in out3["error"] or "ambiguous" in out3["error"]
        after = _rows(db3._conn, "SELECT COUNT(*) AS n FROM TRANSACTIONS")[0]["n"]
        assert after == before, "no-match must write nothing"
        db3.close()

        print("✅ AERODROME CLOSE RECORDER TESTS PASS (stale-id sync + 4-table write + idempotent + pending)")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())

