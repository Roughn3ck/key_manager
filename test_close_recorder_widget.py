"""Widget smoke for the close-completion ledger states (success vs write-failed).

Asserts the two user-facing notes surfaced by _lp_record_orca_close, using stub
writer/price/db. No real GUI widgets needed beyond string content — the notes are
delivered through the existing show_notification error textbox (copyable, untruncated).

Run:  python test_close_recorder_widget.py
"""
import sys
import tempfile
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).parent / "src"))

from coldtrack.close_recorder import CloseLeg, CloseResult  # noqa: E402
import coldtrack.close_recorder as cr  # noqa: E402
import lp_tab as lt  # noqa: E402


class _W:
    def __init__(self, result):
        self.last_close_result = result


class _GUI:
    price_engine = None


def _res(mint="FbNHxe9VV797JWG7XH2msjwp5Rvb6dGzndwwkEXXXBKX"):
    return CloseResult(
        position_mint=mint, platform="Orca", chain="Solana",
        legs=[CloseLeg(asset="cbBTC", amount=0.01, value_usd=167.0, kind="fee", sig="CS")],
        close_sig="CLOSE", collect_sig="CS", decrease_sig=None,
        block_time_iso="2026-09-22T01:24:00+00:00", gas={}, gas_asset="SOL",
        token_price_usd={}, final_amounts={})


def main():
    tab = type("T", (), {})()
    tab.gui = _GUI()
    tab._lp_record_close_for_writer = lt.LPTab.__dict__["_lp_record_close_for_writer"].__get__(tab)

    # 1) writer produced no CloseResult (capture failed non-fatally) → plain note
    rec = lt.LPTab.__dict__["_lp_record_orca_close"]
    note = rec(tab, _W(None), None, "")
    assert "close confirmed on-chain" in note and "ledger" not in note

    # 2) capture present but no portfolio db → skipped note
    tab._lp_portfolio_db_path = lambda: None
    note = rec(tab, _W(_res()), None, "")
    assert "ledger write skipped" in note

    # 3) position not mapped → pending note
    tmp = Path(tempfile.mkdtemp(prefix="close_widget_"))
    dbp = tmp / "coldtrack.db"
    import shutil
    shutil.copy(Path(r"B:\OpenClaw\.openclaw\workspace\kimi\portfolios\kitandpaul\coldtrack.db"), dbp)
    tab._lp_portfolio_db_path = lambda: dbp
    note = rec(tab, _W(_res(mint="NoSuchMintXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX")), None, "")
    assert "position not mapped" in note or "pending" in note, note

    print("✅ CLOSE COMPLETION WIDGET SMOKE PASS (success / skipped / pending notes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
