"""Headless export smoke test for the ColdTrack Sentinel bridge (port of
kimi/coldtax/export_strategy_view.py).

Builds two TEMP coldtrack DBs (the pack + kitandpaul), fixtures shaped to Kimi's
schema v3.0 seam — one Executive Mind G2 pool with a FEE_EVENTS row and a
position-tagged CAPITAL_EVENTS row, one kitandpaul KP2 position with an entry,
and a portfolio-level CAPITAL_EVENTS row — runs the ported exporter, and asserts
the merged strategy_view.json contract. Producer-side mirror of the sentinel's
coldtrack_view_smoke_test.js (the acceptance spec). Writes only temp dirs.

Run:  python test_sentinel_export.py
"""
import json
import shutil
import sys
import tempfile
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).parent / "src"))

from coldtrack.db import ColdTrackDB  # noqa: E402
from coldtrack.sentinel_export import SentinelExporter  # noqa: E402


def _pack_db(path: Path) -> None:
    """Pack DB: Executive Mind portfolio, G2 LP position, fee + capital events."""
    db = ColdTrackDB(path)
    db.init_schema()
    pf = db.upsert_portfolio("Executive Mind", display_name="Executive Mind",
                             reporting_currency="AUD", tax_jurisdiction="AU")
    acc = db.upsert_account(pf, "g2", "lp_position", display_name="G2",
                            chain="BASE",
                            address="0x40c33b69e7ab4b22eb8ec7d164e155f769f8c948",
                            platform="Aerodrome")
    lp = db.upsert_lp_position(
        acc, "ETH/cbBTC", "Aerodrome", "Base", "ETH", "cbBTC",
        "2026-02-09 19:23",
        amount_a_entry=0.32236, amount_b_entry=0.00398,
        range_low=0.0251521, range_high=0.0356919,
        total_value_usd_entry=936.69, fees_earned_usd=38.31, status="active",
        notes=json.dumps({"pool_group": "Genesis", "symbol": "ethusdt",
                          "base_symbol": "cbBTC",
                          "lp_address": "0x40c33b69e7ab4b22eb8ec7d164e155f769f8c948",
                          "season_id": 1,
                          "risk_settings": {"stop_loss_pct": 0.05,
                                            "rsi_alert_high": 80,
                                            "rsi_alert_low": 20}}),
    )
    db.insert_fee_event(lp, "2026-09-10T10:00:00+10:00", "HARVEST",
                        value_usd=38.31, token_a_amt=0.012, token_b_amt=0.0,
                        notes="claim 1")
    db.insert_capital_event(acc, "2026-09-01", "INJECTION", position_id=lp,
                            asset="ETH", amount=0.05, value_usd=150.0,
                            owner="Kris", notes="")
    # Portfolio-level capital event (no position) → owner attribution only.
    wallet = db.upsert_account(pf, "treasury", "wallet", display_name="Treasury",
                               chain="BASE", address="0xabc")
    db.insert_capital_event(wallet, "2026-09-02", "INJECTION", position_id=None,
                            asset="USDC", amount=500.0, value_usd=500.0,
                            owner="Kit", notes="")
    db.upsert_pool_group("Genesis", label="Genesis")
    db.upsert_pool_group("SafetyNET", label="SafetyNET")
    db.close()


def _kp_db(path: Path) -> None:
    """K&P DB: Kit & Paul portfolio, one active kitandpaul LP position (KP2)."""
    db = ColdTrackDB(path)
    db.init_schema()
    pf = db.upsert_portfolio("Kit & Paul", display_name="Kit & Paul",
                             reporting_currency="CAD", tax_jurisdiction="CA")
    acc = db.upsert_account(pf, "kitandpaul", "lp_position",
                            chain="Base",
                            address="0x8958Bd96896De55bFe31b1A6Eb2B280ebE098509",
                            platform="Aerodrome")
    db.upsert_lp_position(
        acc, "ETH/cbBTC", "Aerodrome", "Base", "ETH", "cbBTC",
        "2026-08-01",
        position_id_type="numeric_id", token_id="75269474",
        pool_address="0x70acdf2ad0bf2402c957154f944c19ef4e1cbae1",
        amount_a_entry=1.0, amount_b_entry=0.005,
        tick_lower=-265244.0, tick_upper=-264156.0,
        total_value_usd_entry=7000.0, fees_claimed_usd=50.0, status="active",
        notes=json.dumps({"rpcs": ["https://mainnet.base.org"], "fees": False,
                          "staked": True}),
    )
    db.upsert_pool_group("K&P", label="K&P")
    db.close()


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="coldtrack_export_test_"))
    try:
        pack = tmp / "pack.db"
        kp = tmp / "kp.db"
        _pack_db(pack)
        _kp_db(kp)

        out = tmp / "strategy_view.json"
        result = SentinelExporter([pack, kp], out).export()
        view = json.loads(out.read_text(encoding="utf-8"))

        # Header
        assert view["view_version"] == 1, "view_version pinned to int 1"
        assert "port of kimi/coldtax" in view["generated_by"]
        assert view["source_db"] == "coldtrack.db"
        assert view["generated_at"].endswith("Z") or "+" in view["generated_at"]

        # pool_groups passthrough (merged, deduped)
        names = [g["NAME"] for g in view["pool_groups"]]
        assert "Genesis" in names and "K&P" in names, f"pool_groups: {names}"

        # G2 pool record — byte-compatible with strategy.json shape.
        g2 = view["G2"]
        assert g2["pool_group"] == "Genesis" and g2["type"] == "LP_POOL"
        assert g2["quote_asset"] == "ETH" and g2["base_asset"] == "cbBTC"
        assert g2["symbol"] == "ethusdt" and g2["base_symbol"] == "cbBTC"
        assert g2["status"] == "LP Live"
        assert g2["risk_settings"]["rsi_alert_high"] == 80
        cs = g2["current_strategy"]
        assert cs["season_id"] == 1 and cs["venue"] == "Aerodrome" and cs["network"] == "Base"
        assert cs["lp_range_low"] == 0.0251521 and cs["lp_range_high"] == 0.0356919
        assert cs["initial_investment_quote"] == 0.32236
        assert cs["total_usd_value_at_entry"] == 936.69
        assert g2["fee_snapshot"]["fees_earned_usd"] == 38.31

        # KP container with entry (lights up K&P Net P&L).
        kp = view["KP"]
        assert kp["label"] == "K&P"
        assert kp["wallet"] == "0x8958Bd96896De55bFe31b1A6Eb2B280ebE098509"
        kp2 = kp["positions"][0]
        assert kp2["id"] == "KP1", "single K&P position takes index KP1"
        assert kp2["protocol"] == "aerodrome"
        assert kp2["pool"] == "0x70acdf2ad0bf2402c957154f944c19ef4e1cbae1"
        assert kp2["staked"] is True
        assert kp2["entry"]["usd"] == 7000.0
        assert kp2["entry"]["date"] == "2026-08-01"
        assert kp2["entry"]["token0_amt"] == 1.0
        assert kp2["entry"]["fees_claimed_usd"] == 50.0
        assert kp2["tick_lower"] == -265244.0

        # fee_events — snake_case + position_id STRING ('G2').
        fe = view["fee_events"]
        assert len(fe) == 1, f"fee_events: {fe}"
        assert fe[0]["position_id"] == "G2", "position_id mapped to pool-id string"
        assert fe[0]["value_usd"] == 38.31
        assert fe[0]["token0_amt"] == 0.012 and fe[0]["token1_amt"] == 0.0
        assert fe[0]["source"] == "HARVEST" and fe[0]["tx_hash"] is None

        # capital_events — position-tagged + portfolio-level (null position_id).
        ce = view["capital_events"]
        assert len(ce) == 2, f"capital_events: {ce}"
        pos = next(e for e in ce if e["position_id"] == "G2")
        assert pos["type"] == "INJECTION" and pos["value_usd"] == 150.0
        assert pos["portfolio"] == "Executive Mind" and pos["owner"] == "Kris"
        pf_lvl = next(e for e in ce if e["position_id"] is None)
        assert pf_lvl["owner"] == "Kit" and pf_lvl["value_usd"] == 500.0

        # Reserved keys are the only non-pool top-level keys.
        reserved = {"view_version", "generated_by", "generated_at", "source_db",
                    "fee_events", "capital_events", "pool_groups"}
        pool_keys = [k for k in view if k not in reserved]
        assert sorted(pool_keys) == ["G2", "KP"], f"top-level pool keys: {pool_keys}"

        # Atomic write — no tmp litter.
        assert not (tmp / "strategy_view.json.tmp").exists()
        assert result["pools"] == 1 and result["kp_positions"] == 1

        # Graceful empty: unwritable/missing DBs → header + empty arrays, no throw.
        empty_out = tmp / "empty.json"
        SentinelExporter([tmp / "missing-a.db", tmp / "missing-b.db"], empty_out).export()
        ev = json.loads(empty_out.read_text(encoding="utf-8"))
        assert ev["fee_events"] == [] and ev["capital_events"] == []
        assert all(k in reserved for k in ev.keys())

        print("✅ ALL SENTINEL EXPORT SMOKE TESTS PASSED (port)")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
