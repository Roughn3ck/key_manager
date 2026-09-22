"""Integration test: Orca close → recorder + auto-export wiring (mocked tx layer).

Simulates a completed close (tx layer mocked) and asserts the GUI completion hook
invokes the ledger recorder + auto-export, and that a forced DB-lock failure still
reports close success while persisting a pending record + retry path.

Run:  python test_close_recorder_integration.py
"""
import sqlite3
import json
import shutil
import sys
import tempfile
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).parent / "src"))

from coldtrack.close_recorder import CloseLeg, CloseResult  # noqa: E402
import coldtrack.close_recorder as cr  # noqa: E402

LIVE_KP = Path(r"B:\OpenClaw\.openclaw\workspace\kimi\portfolios\kitandpaul\coldtrack.db")


class _FakeWriter:
    """Stands in for OrcaWriter post-close: last_close_result pre-populated."""
    def __init__(self, result):
        self.last_close_result = result


class _FakeGUI:
    def __init__(self):
        self.notes = []
        self.price_engine = None  # legs come pre-priced in the fixture

    class root:
        @staticmethod
        def after(ms, fn):  # run inline in tests
            fn()


class _FakeLPTab:
    """Reuses LPTab._lp_record_orca_close + its generic delegate, without a GUI."""
    def __init__(self, gui, db_path):
        self.gui = gui
        self._db_path = db_path

    def _lp_portfolio_db_path(self):
        return self._db_path

def _close_result() -> CloseResult:
    return CloseResult(
        position_mint="FbNHxe9VV797JWG7XH2msjwp5Rvb6dGzndwwkEXXXBKX",
        platform="Orca", chain="Solana",
        legs=[
            CloseLeg(asset="cbBTC", amount=0.01011925, value_usd=167.84,
                     kind="fee", sig="CS"),
            CloseLeg(asset="SOL", amount=9.20164718, value_usd=1900.0,
                     kind="fee", sig="CS"),
        ],
        close_sig="CLOSE", collect_sig="CS", decrease_sig=None,
        block_time_iso="2026-09-22T01:24:00+00:00",
        gas={"CS": 5e-06, "CLOSE": 5e-06}, gas_asset="SOL",
        token_price_usd={"cbBTC": 96000.0, "SOL": 210.0}, final_amounts={},
    )


def main():
    import lp_tab as lt
    tmp = Path(tempfile.mkdtemp(prefix="close_integ_"))
    record_fn = lt.LPTab.__dict__["_lp_record_orca_close"]
    _FakeLPTab._lp_record_close_for_writer = lt.LPTab.__dict__["_lp_record_close_for_writer"]
    try:
        db_path = tmp / "coldtrack.db"
        shutil.copy(LIVE_KP, db_path)
        gui = _FakeGUI()
        tab = _FakeLPTab(gui, db_path)

        # _lp_portfolio_db_path on the fake resolves the copied DB via its ctor.
        # Track export invocations.
        exported = {"n": 0}
        from coldtrack import sentinel_export as se
        real_export = se.SentinelExporter.export
        se.SentinelExporter.export = lambda self: exported.__setitem__("n", exported["n"] + 1) or {"ok": True}

        record_fn = lt.LPTab.__dict__["_lp_record_orca_close"]
        note = record_fn(tab, _FakeWriter(_close_result()), None, "")
        se.SentinelExporter.export = real_export
        print("note:", note)
        assert "ledger recorded" in note, note
        assert exported["n"] == 1, "auto-export must fire after a successful record"

        # DB-row proof
        n = sqlite3.connect(str(db_path))
        cnt = n.execute("SELECT COUNT(*) FROM TRANSACTIONS WHERE TX_HASH='CS'").fetchone()[0]
        assert cnt == 2, cnt
        n.close()

        # --- Forced lock failure → pending file + close still reported ok ---
        gui2 = _FakeGUI()
        tab2 = _FakeLPTab(gui2, db_path)
        class _Boom(cr.ColdTrackDB):
            def begin_immediate(self, busy_timeout_ms=5000):
                raise RuntimeError("database is locked")
        orig = cr.ColdTrackDB
        cr.ColdTrackDB = _Boom
        try:
            note2 = record_fn(tab2, _FakeWriter(_close_result()), None, "")
        finally:
            cr.ColdTrackDB = orig
        print("failure note:", note2)
        assert "close confirmed on-chain" in note2 and "ledger write failed" in note2
        # Pending path: lives next to the portfolio DB (base_dir = db.parent).
        pend = list((tmp / "coldstack_pending_records").glob("pending_close_*.json"))
        assert any("FbNHxe9V" in p.name for p in pend), pend
        blob = json.loads(pend[0].read_text())
        assert blob["close_result"]["position_mint"].startswith("FbNHxe9V")
        print("✅ INTEGRATION PASS — recorder invoked + auto-export fired; lock-failure "
              "-> close still reported success + pending file persisted")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
