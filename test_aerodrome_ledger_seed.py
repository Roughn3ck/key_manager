"""Tests for Aerodrome ledger-seeded position discovery (v5.3.25)."""
import sys
import tempfile
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).parent / "src"))

from lp_engine import LPPosition
from venue_adapters import aerodrome_adapter as adapter_mod
from venue_adapters.aerodrome_adapter import AerodromeAdapter

WALLET = "0x1111111111111111111111111111111111111111"
GAUGE = "0x2222222222222222222222222222222222222222"
POOL = "0x4444444444444444444444444444444444444444"
PM1 = "0x827922686190790b37229fd06084350E74485b72"


def _abi_uint256_arr(values: list) -> str:
    """Return ABI-encoded uint256[] hex string (no 0x prefix)."""
    parts = ["0" * 62 + "20", format(len(values), "064x")]
    for v in values:
        parts.append(format(v, "064x"))
    return "0x" + "".join(parts)


def _make_position(token_id: int, liquidity: int = 1000):
    return LPPosition(
        position_id=f"base:{token_id}",
        venue="Aerodrome",
        chain="BASE",
        pool_id=POOL,
        pair="WETH/USDC",
        token_0="WETH",
        token_1="USDC",
        raw_data={"liquidity": liquidity, "token0_address": "0x0", "token1_address": "0x1"},
        current_price=3000.0,
    )


def test_ledger_helper_skips_empty_token_ids():
    """Rows with empty TOKEN_ID are ignored; populated rows are returned."""
    from coldtrack.db import ColdTrackDB

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "coldtrack.db"
        db = ColdTrackDB(db_path)
        db.init_schema()
        portfolio_id = db.upsert_portfolio("Test Portfolio")
        account_id = db.upsert_account(portfolio_id, "Test", "wallet", chain="BASE")
        db.upsert_lp_position(
            account_id, "K&P Pool", "Aerodrome", "BASE", "WETH", "USDC",
            "2026-01-01", token_id="75269474"
        )
        db.upsert_lp_position(
            account_id, "Empty Pool", "Aerodrome", "BASE", "WETH", "USDC",
            "2026-01-01", token_id=""
        )
        db.upsert_lp_position(
            account_id, "Other Platform", "BSC", "BSC", "WETH", "USDC",
            "2026-01-01", token_id="99"
        )

        db._conn.close()
        result = adapter_mod._get_ledger_aerodrome_token_ids([db_path])
        assert result == [75269474], f"unexpected ledger IDs: {result}"


def _make_fake_rpc(staked_for_wallet: list, owner: str):
    """Return a fake _base_rpc_call for ledger-seed tests."""
    def fake_rpc(method, params):
        if method != "eth_call":
            return None
        data = params[0]["data"]
        to = params[0]["to"].lower()
        if data.startswith(adapter_mod.SELECTOR_OWNER_OF):
            return "0x" + "0" * 24 + owner[2:].lower()
        if data.startswith(adapter_mod.SELECTOR_POOL):
            return "0x" + "0" * 24 + POOL[2:].lower()
        if data.startswith(adapter_mod.SELECTOR_STAKED_TOKEN_IDS):
            return _abi_uint256_arr(staked_for_wallet)
        return None
    return fake_rpc


def test_ledger_seed_staked_position():
    """Ledger token owned by a gauge is returned annotated as staked."""
    adapter = AerodromeAdapter()

    original_rpc = adapter_mod._base_rpc_call
    original_discover = adapter_mod.discover_staked_positions
    original_get_ids = adapter_mod._get_ledger_aerodrome_token_ids
    try:
        adapter_mod._base_rpc_call = _make_fake_rpc([123], GAUGE)
        adapter_mod.discover_staked_positions = lambda *a, **k: ([], {"complete": True, "scanned_from": 0, "scanned_to": 0})
        adapter_mod.wallet_address_active = lambda w: True
        adapter_mod._get_ledger_aerodrome_token_ids = lambda *a, **k: [123]
        adapter._fetch_position_by_token_id = lambda tid, pe, wallet: _make_position(tid)

        positions = adapter.fetch_all_positions(WALLET, online_mode=True)
    finally:
        adapter_mod._base_rpc_call = original_rpc
        adapter_mod.discover_staked_positions = original_discover
        adapter_mod._get_ledger_aerodrome_token_ids = original_get_ids

    assert len(positions) == 1, positions
    pos = positions[0]
    assert pos.raw_data["is_staked"] is True
    assert pos.raw_data["gauge_address"] == GAUGE.lower()
    assert "staked via gauge" in pos.raw_data["owner_display"]


def test_ledger_seed_unstaked_position():
    """Ledger token owned by the wallet is returned as a normal position."""
    adapter = AerodromeAdapter()

    original_rpc = adapter_mod._base_rpc_call
    original_discover = adapter_mod.discover_staked_positions
    original_get_ids = adapter_mod._get_ledger_aerodrome_token_ids
    try:
        adapter_mod._base_rpc_call = _make_fake_rpc([], WALLET)
        adapter_mod.discover_staked_positions = lambda *a, **k: ([], {"complete": True, "scanned_from": 0, "scanned_to": 0})
        adapter_mod.wallet_address_active = lambda w: True
        adapter_mod._get_ledger_aerodrome_token_ids = lambda *a, **k: [123]
        adapter._fetch_position_by_token_id = lambda tid, pe, wallet: _make_position(tid)

        positions = adapter.fetch_all_positions(WALLET, online_mode=True)
    finally:
        adapter_mod._base_rpc_call = original_rpc
        adapter_mod.discover_staked_positions = original_discover
        adapter_mod._get_ledger_aerodrome_token_ids = original_get_ids

    assert len(positions) == 1, positions
    assert not positions[0].raw_data.get("is_staked")
    assert "gauge_address" not in positions[0].raw_data


def test_wallet_scoped_stake_attribution():
    """A gauge-held token only resolves as staked for wallets that actually staked it.

    Token 123 is held by GAUGE but stakedTokenIds(OTHER_WALLET) is empty, so
    resolving it for OTHER_WALLET must not fabricate a position.
    """
    adapter = AerodromeAdapter()
    other_wallet = "0x5555555555555555555555555555555555555555"

    original_rpc = adapter_mod._base_rpc_call
    original_discover = adapter_mod.discover_staked_positions
    original_get_ids = adapter_mod._get_ledger_aerodrome_token_ids
    original_active = adapter_mod.wallet_address_active
    try:
        adapter_mod._base_rpc_call = _make_fake_rpc([], GAUGE)
        adapter_mod.discover_staked_positions = lambda *a, **k: ([], {"complete": True, "scanned_from": 0, "scanned_to": 0})
        adapter_mod.wallet_address_active = lambda w: True
        adapter_mod._get_ledger_aerodrome_token_ids = lambda *a, **k: [123]
        adapter._fetch_position_by_token_id = lambda tid, pe, wallet: _make_position(tid)

        positions = adapter.fetch_all_positions(other_wallet, online_mode=True)
    finally:
        adapter_mod._base_rpc_call = original_rpc
        adapter_mod.discover_staked_positions = original_discover
        adapter_mod._get_ledger_aerodrome_token_ids = original_get_ids
        adapter_mod.wallet_address_active = original_active

    # No positions and no fabricated staked position.
    assert all(p.error is not None for p in positions), positions
    assert not any(p.raw_data and p.raw_data.get("is_staked") for p in positions if p.raw_data)


def test_empty_token_id_falls_through():
    """When the ledger has no usable Aerodrome TOKEN_IDs, the ledger path adds
    nothing and the fallback scan runs. With an inactive wallet the final UX
    shows the manual deposit-ID hint rather than fabricating a position."""
    adapter = AerodromeAdapter()

    original_get_ids = adapter_mod._get_ledger_aerodrome_token_ids
    original_discover = adapter_mod.discover_staked_positions
    original_active = adapter_mod.wallet_address_active
    try:
        adapter_mod._get_ledger_aerodrome_token_ids = lambda *a, **k: []
        adapter_mod.discover_staked_positions = lambda *a, **k: ([], {"complete": True, "scanned_from": 0, "scanned_to": 0})
        adapter_mod.wallet_address_active = lambda w: True

        positions = adapter.fetch_all_positions(WALLET, online_mode=True)
    finally:
        adapter_mod._get_ledger_aerodrome_token_ids = original_get_ids
        adapter_mod.discover_staked_positions = original_discover
        adapter_mod.wallet_address_active = original_active

    assert len(positions) == 1
    assert "Deposit ID" in positions[0].error


if __name__ == "__main__":
    tests = [
        test_ledger_helper_skips_empty_token_ids,
        test_ledger_seed_staked_position,
        test_ledger_seed_unstaked_position,
        test_wallet_scoped_stake_attribution,
        test_empty_token_id_falls_through,
    ]
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except Exception as exc:
            failed += 1
            print(f"FAIL {fn.__name__}: {exc}")
            import traceback
            traceback.print_exc()
    if failed:
        sys.exit(1)
    print("ALL PASS")
