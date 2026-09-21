"""Headless export smoke test for the ColdTrack Sentinel bridge.

Builds a TEMP coldtrack.db with fixtures (one G-pool, one KP position with an
entry, one fee_harvest, one position deposit, one portfolio-level deposit),
runs the exporter, and asserts the strategy_view.json contract shape — the
producer-side mirror of the sentinel's coldtrack_view_smoke_test.js (the
acceptance spec). Writes only a temp dir; production coldtrack.db and the
live strategy_view.json are untouched.

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


def _build_fixture_db(db_path: Path) -> ColdTrackDB:
    """Create a temp DB with one portfolio, owner-attributed accounts, a G-pool
    with entry + fee + injection, a KP position with entry, and a portfolio-level
    deposit."""
    db = ColdTrackDB(db_path)
    db.init_schema()

    pf = db.upsert_portfolio(
        "Executive Mind", display_name="Executive Mind", type="internal",
        reporting_currency="AUD", tax_jurisdiction="AU",
    )
    # G2's LP account (owner Kris) — maps to pool G2 via the registry.
    g2_acc = db.upsert_account(
        pf, "G2 ETH/cbBTC", "lp_position", display_name="Kris",
        chain="BASE", address="0x40c33b69e7ab4b22eb8ec7d164e155f769f8c948",
        platform="Aerodrome",
    )
    # KP1's LP account — maps to pool KP1.
    kp1_acc = db.upsert_account(
        pf, "KP1 Project X", "lp_position", display_name="Kit",
        chain="HyperEVM", address="0x8958Bd96896De55bFe31b1A6Eb2B280ebE098509",
        platform="Project X",
    )
    # A portfolio-level wallet account (no sentinel pool) for the untagged deposit.
    wallet_acc = db.upsert_account(
        pf, "Treasury", "wallet", display_name="Kit", chain="BASE",
        address="0xabc",
    )

    # G2 LP position entry data.
    db.upsert_lp_position(
        g2_acc, "ETH/cbBTC", "Aerodrome", "Base", "ETH", "cbBTC",
        "2026-02-09 19:23",
        amount_a_entry=0.32236, amount_b_entry=0.00398,
        range_low=0.0251521, range_high=0.0356919,
        total_value_usd_entry=936.69, status="active",
    )
    # KP1 LP position entry data (KP entry source).
    db.upsert_lp_position(
        kp1_acc, "WHYPE/UBTC", "Project X", "HyperEVM", "WHYPE", "UBTC",
        "2026-08-01",
        amount_a_entry=100.0, amount_b_entry=0.05,
        total_value_usd_entry=7000.0, fees_claimed_usd=50.0, status="active",
    )

    # Registry: G2 pool + KP container + KP1 position.
    db.upsert_sentinel_pool(
        "G2", account_id=g2_acc, group_name="Genesis", position_type="LP_POOL",
        season=1,
        position_config=json.dumps({
            "pool_group": "Genesis", "type": "LP_POOL",
            "quote_asset": "ETH", "base_asset": "cbBTC",
            "symbol": "ethusdt", "base_symbol": "cbBTC",
            "lp_address": "0x40c33b69e7ab4b22eb8ec7d164e155f769f8c948",
            "status": "LP Live",
            "current_strategy": {"network": "Base", "venue": "Aerodrome"},
            "risk_settings": {"stop_loss_pct": 0.05, "rsi_alert_high": 80,
                               "rsi_alert_low": 20},
        }),
    )
    db.upsert_sentinel_pool(
        "KP", position_config=json.dumps({
            "label": "K&P",
            "wallet": "0x8958Bd96896De55bFe31b1A6Eb2B280ebE098509",
        }),
    )
    db.upsert_sentinel_pool(
        "KP1", account_id=kp1_acc, position_type="LP_POOL",
        position_config=json.dumps({
            "id": "KP1", "label": "Project X #545983", "venue": "Project X",
            "protocol": "projectx", "chain": "HyperEVM",
            "position_id": "545983",
            "pool": "0x0d6ecb912b6ee160e95bc198b618acc1bcb92525",
            "rpcs": ["https://rpc.hyperliquid.xyz/evm"],
            "token0": "WHYPE", "token0_decimals": 18,
            "token1": "UBTC", "token1_decimals": 8, "fees": True,
        }),
    )

    # One fee_harvest against G2's account (position-tagged).
    db.insert_transaction(
        g2_acc, "2026-09-10T10:00:00+10:00", "fee_harvest", "ETH", 0.012,
        value_usd=38.31, counterparty_amount=0.0, category="MANUAL", notes="claim 1",
    )
    # One position deposit (G2) and one portfolio-level deposit (no pool).
    db.insert_transaction(
        g2_acc, "2026-09-01", "deposit", "ETH", 0.05, value_usd=150.0,
    )
    db.insert_transaction(
        wallet_acc, "2026-09-02", "deposit", "USDC", 500.0, value_usd=500.0,
    )
    return db


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="coldtrack_export_test_"))
    try:
        db = _build_fixture_db(tmp / "coldtrack.db")
        out = tmp / "strategy_view.json"
        result = SentinelExporter(db, out).export()
        db.close()

        view = json.loads(out.read_text(encoding="utf-8"))

        # Header
        assert view["view_version"] == 1, "view_version pinned to 1"
        assert view["generated_by"].startswith("ColdTrack Sentinel Export"), "generated_by"
        assert view["source_db"] == "coldtrack.db", "source_db"
        assert view["generated_at"], "generated_at present"

        # G2 pool record — verbatim config + refreshed entry + fee snapshot Σ.
        g2 = view["G2"]
        assert g2["pool_group"] == "Genesis" and g2["type"] == "LP_POOL"
        assert g2["symbol"] == "ethusdt" and g2["base_symbol"] == "cbBTC"
        assert g2["lp_address"].endswith("f8c948")
        assert g2["status"] == "LP Live"
        assert g2["risk_settings"]["stop_loss_pct"] == 0.05
        cs = g2["current_strategy"]
        assert cs["season_id"] == 1 and cs["network"] == "Base" and cs["venue"] == "Aerodrome"
        assert cs["lp_range_low"] == 0.0251521 and cs["lp_range_high"] == 0.0356919
        assert cs["initial_investment_quote"] == 0.32236
        assert cs["initial_investment_base"] == 0.00398
        assert cs["total_usd_value_at_entry"] == 936.69
        assert cs["start_date"] == "2026-02-09 19:23"
        assert g2["fee_snapshot"]["fees_earned_usd"] == 38.31, "fee Σ cumulative"
        assert g2["fee_snapshot"]["check_time"] == "2026-09-10T10:00:00+10:00"

        # KP container.
        kp = view["KP"]
        assert kp["label"] == "K&P"
        assert kp["wallet"] == "0x8958Bd96896De55bFe31b1A6Eb2B280ebE098509"
        kp1 = kp["positions"][0]
        assert kp1["id"] == "KP1" and kp1["protocol"] == "projectx"
        assert kp1["pool"] == "0x0d6ecb912b6ee160e95bc198b618acc1bcb92525"
        assert kp1["entry"]["usd"] == 7000.0
        assert kp1["entry"]["date"] == "2026-08-01"
        assert kp1["entry"]["token0_amt"] == 100.0 and kp1["entry"]["token1_amt"] == 0.05
        assert kp1["entry"]["fees_claimed_usd"] == 50.0

        # fee_events — one row, position-tagged, snake_case contract.
        fe = view["fee_events"]
        assert len(fe) == 1, "one fee event"
        assert fe[0]["position_id"] == "G2"
        assert fe[0]["value_usd"] == 38.31
        assert fe[0]["token0_amt"] == 0.012 and fe[0]["token1_amt"] == 0.0
        assert fe[0]["source"] == "MANUAL" and fe[0]["tx_hash"] is None

        # capital_events — position-tagged + portfolio-level (null position_id).
        ce = view["capital_events"]
        assert len(ce) == 2, "two capital events"
        pos = next(e for e in ce if e["position_id"] == "G2")
        assert pos["type"] == "INJECTION" and pos["value_usd"] == 150.0
        assert pos["portfolio"] == "Executive Mind" and pos["owner"] == "Kris"
        port = next(e for e in ce if e["position_id"] is None)
        assert port["owner"] == "Kit" and port["value_usd"] == 500.0

        # Reserved keys are the only non-pool top-level keys.
        reserved = {"view_version", "generated_by", "generated_at", "source_db",
                    "fee_events", "capital_events"}
        pool_keys = [k for k in view.keys() if k not in reserved]
        assert sorted(pool_keys) == ["G2", "KP"], f"top-level pool keys: {pool_keys}"

        # Atomic write left no tmp litter.
        assert not (tmp / "strategy_view.json.tmp").exists(), "no tmp file left"

        assert result["pools"] == 3, "registry rows counted"

        # Graceful empty: empty registry → header + empty arrays, never throws.
        empty_db = ColdTrackDB(tmp / "empty.db")
        empty_db.init_schema()
        empty_out = tmp / "empty_view.json"
        SentinelExporter(empty_db, empty_out).export()
        empty_db.close()
        ev = json.loads(empty_out.read_text(encoding="utf-8"))
        assert ev["fee_events"] == [] and ev["capital_events"] == []
        assert all(k in reserved for k in ev.keys()), "empty view: reserved keys only"

        print("✅ ALL SENTINEL EXPORT SMOKE TESTS PASSED")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
