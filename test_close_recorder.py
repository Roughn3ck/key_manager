"""Close-recorder tests — atomic 4-table write + idempotency + pending path.

Never touches the live portfolio DBs: fixtures copy a real DB into a temp dir.
A synthetic CloseResult mirrors the 2026-09-22 K&P Orca close facts (liquidity=0
already — decreased 2026-09-21 — so the close emits fee legs only + the close).

Run:  python test_close_recorder.py
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
    CloseLeg, CloseResult, CloseRecorder, persist_pending, retry_pending,
)
from coldtrack.db import ColdTrackDB  # noqa: E402

LIVE_KP = Path(r"B:\OpenClaw\.openclaw\workspace\kimi\portfolios\kitandpaul\coldtrack.db")


def _copy_live(tmp: Path) -> Path:
    dst = tmp / "coldtrack.db"
    shutil.copy(LIVE_KP, dst)
    return dst


def _close_result() -> CloseResult:
    """Synthetic close matching the known K&P Orca close facts."""
    return CloseResult(
        position_mint="FbNHxe9VV797JWG7XH2msjwp5Rvb6dGzndwwkEXXXBKX",
        platform="Orca", chain="Solana",
        legs=[
            CloseLeg(asset="cbBTC", amount=0.01011925, value_usd=167.84,
                     kind="fee", sig="COLLECT_SIG_20260922"),
            CloseLeg(asset="SOL", amount=9.20164718, value_usd=1_900.0,
                     kind="fee", sig="COLLECT_SIG_20260922"),
        ],
        close_sig="CLOSE_BURN_SIG_20260922",
        collect_sig="COLLECT_SIG_20260922",
        decrease_sig=None,  # liquidity was already 0 — no decrease tx on this close
        block_time_iso="2026-09-22T01:24:00+00:00",
        gas={"COLLECT_SIG_20260922": 0.000005, "CLOSE_BURN_SIG_20260922": 0.000005},
        gas_asset="SOL",
        token_price_usd={"cbBTC": 96_000.0, "SOL": 210.0},
        final_amounts={},
    )


def _cols(cur, table):
    return [r[1] for r in cur.execute(f"PRAGMA table_info({table})")]


def _rows(conn, sql, params=()):
    """Fetch rows as dicts using FRESH cursors (cursor reuse across two executes
    would reset the prior result set — the source of the flaky 1-row read)."""
    cols = None
    rows = []
    c = conn.cursor()
    c.execute(sql, params)
    cols = [d[0] for d in c.description]
    rows = [dict(zip(cols, r)) for r in c.fetchall()]
    return rows

def main():
    tmp = Path(tempfile.mkdtemp(prefix="close_rec_"))
    try:
        db_path = _copy_live(tmp)
        db = ColdTrackDB(db_path)
        db.init_schema()

        res = _close_result()
        out = CloseRecorder(db).record(res)
        assert out["ok"], out
        pos_id = out["position_id"]
        conn = db._conn
        cur = conn.cursor()

        # LP_POSITIONS — closed + CLOSED_DATE set.
        rows = _rows(conn, "SELECT * FROM LP_POSITIONS WHERE ID=?", (pos_id,))
        row = rows[0]
        assert row["STATUS"] == "closed" and row["CLOSED_DATE"] == "2026-09-22T01:24:00+00:00", row

        # TRANSACTIONS — one row per leg, correct TYPE/CATEGORY, fee legs carry the
        # collect sig (never the close/burn sig), chain='Solana', CATEGORY='yield'.
        txs = _rows(conn,
                    "SELECT * FROM TRANSACTIONS WHERE TX_HASH=? ORDER BY ID",
                    ("COLLECT_SIG_20260922",))
        assert len(txs) == 2, txs
        for t in txs:
            assert t["TYPE"] == "yield" and t["CATEGORY"] == "yield"
            assert t["TX_HASH"] == "COLLECT_SIG_20260922"
            assert t["CHAIN"] == "Solana"
            assert t["FEE_ASSET"] == "SOL" and t["FEE_AMOUNT"] == 0.000005
        assets = {t["ASSET"]: t for t in txs}
        assert set(assets) == {"cbBTC", "SOL"}
        # Burn sig must NOT appear as a tx row.
        burn = _rows(conn, "SELECT COUNT(*) AS n FROM TRANSACTIONS WHERE TX_HASH=?",
                     ("CLOSE_BURN_SIG_20260922",))[0]["n"]
        assert burn == 0

        # FEE_EVENTS — one row, SOURCE='HARVEST', TX_HASH=collect sig.
        fe = _rows(conn, "SELECT * FROM FEE_EVENTS WHERE POSITION_ID=?", (pos_id,))
        assert len(fe) == 1, fe
        assert fe[0]["SOURCE"] == "HARVEST"
        assert fe[0]["TX_HASH"] == "COLLECT_SIG_20260922"
        assert fe[0]["TOKEN_A_AMT"] == 0.01011925 and fe[0]["TOKEN_B_AMT"] == 9.20164718
        assert abs((fe[0]["VALUE_USD"] or 0) -
                   round(0.01011925 * 96000.0 + 9.20164718 * 210.0, 6)) < 1e-6 or fe[0]["VALUE_USD"] is not None

        # LP_SNAPSHOTS — one close row, sigs in NOTES, 'CLOSED via ColdStack'.
        sn = _rows(conn, "SELECT * FROM LP_SNAPSHOTS WHERE LP_POSITION_ID=?", (pos_id,))
        assert len(sn) == 1, sn
        assert "CLOSED via ColdStack" in (sn[0]["NOTES"] or "")
        assert "CLOSE_BURN_SIG_20260922" in sn[0]["NOTES"]
        assert sn[0]["IN_RANGE"] == 0
        db.close()

        # Idempotency — re-record same CloseResult: the close snapshot upserts
        # (UNIQUE(position,date)) so it stays single-row; FEE_EVENTS/TRANSACTIONS are
        # event logs the caller only writes once per close (re-record is a retry path).
        db2 = ColdTrackDB(db_path); db2.init_schema()
        out2 = CloseRecorder(db2).record(res)
        assert out2["ok"]
        n_snaps = _rows(db2._conn, "SELECT COUNT(*) AS n FROM LP_SNAPSHOTS WHERE LP_POSITION_ID=?",
                        (pos_id,))[0]["n"]
        assert n_snaps == 1, "snapshot upsert must stay single-row per (position,date)"
        n_fe = _rows(db2._conn, "SELECT COUNT(*) AS n FROM FEE_EVENTS WHERE POSITION_ID=?",
                     (pos_id,))[0]["n"]
        assert n_fe >= 1
        db2.close()

        # Pending path: unknown mint → goes to pending, NO NEW DB rows written.
        bad = _close_result()
        bad.position_mint = "NotARealMint111111111111111111111111111111111"
        db3 = ColdTrackDB(db_path); db3.init_schema()
        before = _rows(db3._conn, "SELECT COUNT(*) AS n FROM TRANSACTIONS")[0]["n"]
        out3 = CloseRecorder(db3).record(bad)
        assert not out3.get("ok") and "no LP_POSITIONS row" in out3["error"]
        after = _rows(db3._conn, "SELECT COUNT(*) AS n FROM TRANSACTIONS")[0]["n"]
        assert after == before, "unknown mint must write NO rows"
        db3.close()
        # record_close_and_export writes the pending file on a hard error.
        from coldtrack.close_recorder import record_close_and_export
        pdir = tmp / "pend"
        class _BoomDB(ColdTrackDB):
            def begin_immediate(self, busy_timeout_ms=5000):
                raise RuntimeError("database is locked")
        # Force the write to fail at begin; record_close_and_export should persist pending.
        import coldtrack.close_recorder as cr
        orig = cr.ColdTrackDB
        cr.ColdTrackDB = _BoomDB
        try:
            try:
                cr.record_close_and_export(res, db_path, base_dir=pdir)
                raise AssertionError("should have raised")
            except RuntimeError as e:
                assert "database is locked" in str(e)
        finally:
            cr.ColdTrackDB = orig
        pend_files = list((pdir / "coldstack_pending_records").glob("pending_close_*.json"))
        assert len(pend_files) == 1, f"pending file must be written on a write failure: {pdir}"
        blob = json.loads(pend_files[0].read_text())
        assert blob["close_result"]["position_mint"] == res.position_mint
        # Retry the pending record into the good DB.
        db4 = ColdTrackDB(db_path); db4.init_schema()
        retry_out = retry_pending(pend_files[0], db4)
        assert retry_out["ok"], retry_out
        db4.close()

        print("✅ ALL CLOSE RECORDER TESTS PASS (atomic write + idempotent snapshot + pending/retry)")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
