"""Sui assets + Cetus adapter tests (v5.3.20).

Default suite is fully offline: Sui JSON-RPC is stubbed with recorded-shaped
fixtures. The live read-only E2E is gated behind COLDSATCK_E2E_RPC=1 (and needs
SUI_E2E_ADDRESS / SUI_E2E_POSITION_ID), so CI stays green offline.

Run:  python test_sui_cetus.py
"""
import os
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).parent / "src"))

import sui_assets  # noqa: E402
from sui_assets import parse_coin_type, fetch_sui_assets, load_registry  # noqa: E402

SUI = "0x2::sui::SUI"
LBTC = "0x3e8e9423d80e1774a7f542af02e243585b0dbf71c4d0f0b7d0b1e0e0e0e0e0e0::lbtc::LBTC"
DEEP = "0xdeeb7a4662eec9f2f3def03fb937a663ddcfc2115f74f0b0e2e0e0e0e0e0e0e0::deep::DEEP"


def _stub_sui_rpc(balances, metadata):
    """Return a fake sui_assets._rpc using recorded-shaped responses."""
    def _rpc(url, method, params, timeout=12):
        if method == "suix_getAllBalances":
            return [{"coinType": ct, "totalBalance": str(amt)} for ct, amt in balances]
        if method == "suix_getCoinMetadata":
            return metadata.get(params[0])
        raise AssertionError(f"unexpected method {method}")
    return _rpc


def test_parse_coin_type():
    assert parse_coin_type(SUI) == ("0x2", "sui", "SUI")
    assert parse_coin_type("0xabc::foo::BAR") == ("0xabc", "foo", "BAR")
    assert parse_coin_type("") == ("", "", "")
    print("✅ parse_coin_type")


def test_fetch_sui_assets_offline():
    sui_assets._META_CACHE.clear()
    real = sui_assets._rpc
    balances = [(SUI, 252540000000), (LBTC, 12345678), (DEEP, 5000000000), ("0xzz::x::ZERO", 0)]
    metadata = {
        LBTC: {"symbol": "LBTC", "decimals": 8, "name": "Lombard BTC"},
        DEEP: {"symbol": "DEEP", "decimals": 6, "name": "DeepBook"},
        "0xzz::x::ZERO": {"symbol": "ZERO", "decimals": 9, "name": "Zero"},
    }
    try:
        sui_assets._rpc = _stub_sui_rpc(balances, metadata)
        assets = fetch_sui_assets("0x" + "a" * 64)
    finally:
        sui_assets._rpc = real

    assert len(assets) == 3, assets
    assert assets[0]["symbol"] == "SUI" and abs(assets[0]["balance"] - 252.54) < 1e-9, assets[0]
    by_sym = {a["symbol"]: a for a in assets}
    assert abs(by_sym["LBTC"]["balance"] - 0.12345678) < 1e-12, by_sym["LBTC"]
    assert abs(by_sym["DEEP"]["balance"] - 5000.0) < 1e-9, by_sym["DEEP"]
    assert all(a["chain"] == "sui" for a in assets)
    print("✅ fetch_sui_assets (exhaustive, metadata-resolved, zero dropped)")


def test_registry_override():
    assert load_registry()["coins"][SUI]["symbol"] == "SUI"
    # Registry wins over metadata (no RPC call needed when an entry exists).
    sym, dec = sui_assets.resolve_symbol_decimals(SUI, registry=load_registry())
    assert (sym, dec) == ("SUI", 9), (sym, dec)
    print("✅ registry override")


def test_balance_engine_sui_hook():
    import balance_engine
    sui_assets._META_CACHE.clear()
    real = sui_assets._rpc
    balances = [(SUI, 1000000000), (LBTC, 100000000)]
    metadata = {LBTC: {"symbol": "LBTC", "decimals": 8, "name": "Lombard BTC"}}
    try:
        sui_assets._rpc = _stub_sui_rpc(balances, metadata)
        result = balance_engine.BalanceEngine().fetch_balance("0x" + "b" * 64, "SUI (Sui)")
    finally:
        sui_assets._rpc = real
    syms = {b["symbol"] for b in result["balances"]}
    assert result["error"] == "" and syms == {"SUI", "LBTC"}, result
    print("✅ balance_engine sui hook returns SUI + tokens")


def test_cetus_decode():
    from venue_adapters.cetus_adapter import decode_position, decode_pool, _compute_holdings, _tick_to_price

    pos = decode_position({
        "pool": "0x" + "c" * 64,
        "liquidity": "1000000",
        "tick_lower_index": {"bits": "60159"},
        "tick_upper_index": {"bits": "112276"},
        "coin_type_a": {"name": LBTC},
        "coin_type_b": {"name": SUI},
        "fee_owed_a": "12345",
        "fee_owed_b": "6789",
    })
    assert pos["pool"] == "0x" + "c" * 64
    assert pos["tick_lower"] == 60159 and pos["tick_upper"] == 112276
    assert pos["coin_type_a"] == LBTC and pos["coin_type_b"] == SUI
    assert pos["fee_owed_a"] == 12345 and pos["fee_owed_b"] == 6789

    # I32 two's complement (negative tick) + struct-wrapped variants.
    neg = decode_position({"tick_lower_index": {"fields": {"bits": str(2 ** 32 - 887272)}}})
    assert neg["tick_lower"] == -887272, neg

    pool = decode_pool({
        "current_sqrt_price": str(2 ** 64),
        "current_tick_index": {"bits": "106800"},
        "tick_spacing": "10",
        "coin_a": {"name": LBTC},
        "coin_b": {"name": SUI},
    })
    assert pool["current_sqrt_price"] == 2 ** 64
    assert pool["tick_current"] == 106800 and pool["tick_spacing"] == 10

    # Price / holdings sanity: tick 0 -> price 1 with equal decimals.
    assert abs(_tick_to_price(0, 9, 9) - 1.0) < 1e-12
    sqrt_at_50 = int(1.0001 ** (50 / 2.0) * 2 ** 64)
    amt_a, amt_b = _compute_holdings(1_000_000, sqrt_at_50, 0, 100, 50, 9, 9)
    assert amt_a > 0 and amt_b > 0, (amt_a, amt_b)
    print("✅ Cetus position/pool decode + Q64.64 math")


def test_cetus_adapter_position():
    import venue_adapters.cetus_adapter as ca

    pool_id = "0x" + "d" * 64
    obj_id = "0x" + "e" * 64
    pos_fields = {
        "pool": pool_id,
        "liquidity": "1234567890",
        "tick_lower_index": {"bits": "60159"},
        "tick_upper_index": {"bits": "112276"},
        "coin_type_a": {"name": LBTC},
        "coin_type_b": {"name": SUI},
        "fee_owed_a": "100000",
        "fee_owed_b": "200000",
    }
    pool_fields = {
        "current_sqrt_price": str(int(1.0001 ** (106800 / 2.0) * 2 ** 64)),
        "current_tick_index": {"bits": "106800"},
        "tick_spacing": "10",
        "coin_a": {"name": LBTC},
        "coin_b": {"name": SUI},
    }
    meta = {LBTC: {"symbol": "LBTC", "decimals": 8, "name": "Lombard"},
            SUI: {"symbol": "SUI", "decimals": 9, "name": "Sui"}}

    real_obj = ca._get_object_json
    real_meta = ca.get_coin_metadata
    try:
        ca._get_object_json = lambda oid: ("0xpool::pool::Pool", pool_fields)
        ca.get_coin_metadata = lambda ct, url=None: meta.get(ct, {})
        pos = ca.CetusAdapter()._position_from_fields(obj_id, pos_fields, None)
    finally:
        ca._get_object_json = real_obj
        ca.get_coin_metadata = real_meta

    assert pos.position_id == f"sui:{obj_id}", pos.position_id
    assert pos.venue == "Cetus" and pos.chain == "Sui", (pos.venue, pos.chain)
    assert pos.pair == "LBTC/SUI", pos.pair
    assert pos.range_low is not None and pos.range_high is not None
    assert pos.position_in_range_pct is not None
    # rangePct is sqrt-price-linear (sentinel convention, kp_monitor buildPosition)
    _sq = lambda t: 1.0001 ** (t / 2.0)
    expected = (_sq(106800) - _sq(60159)) / (_sq(112276) - _sq(60159)) * 100.0
    assert abs(pos.position_in_range_pct - expected) < 0.01, pos.position_in_range_pct
    assert pos.deposit_amounts, pos.deposit_amounts
    assert pos.raw_data["tick_current"] == 106800
    print(f"✅ CetusAdapter position (pair {pos.pair}, in-range {pos.position_in_range_pct:.1f}%)")


def test_cetus_owned_objects_fixture():
    """Fix 1: the exact on-chain position type filter returns the N1 position."""
    import venue_adapters.cetus_adapter as ca
    from venue_adapters.cetus_adapter import CetusAdapter, CETUS_POSITION_TYPE

    owner = "0x04887176a0791ac1837bc654533990820a33f2c289b8636c7066a1865191b314"
    obj_id = "0x18bee062d705e1e1fded90663b015b23f2c4b2ef57ada55d1f774e4999de4fad"
    pos_fields = {
        "pool": "0x" + "f" * 64,
        "liquidity": "1234567890",
        "tick_lower_index": {"bits": "60159"},
        "tick_upper_index": {"bits": "112276"},
        "coin_type_a": {"name": LBTC},
        "coin_type_b": {"name": SUI},
        "fee_owed_a": "0",
        "fee_owed_b": "0",
    }
    pool_fields = {
        "current_sqrt_price": str(int(1.0001 ** (106800 / 2.0) * 2 ** 64)),
        "current_tick_index": {"bits": "106800"},
        "tick_spacing": "10",
        "coin_a": {"name": LBTC},
        "coin_b": {"name": SUI},
    }
    seen = {}

    def fake_rpc(url, method, params, timeout=20):
        if method == "suix_getOwnedObjects":
            seen["params"] = params
            return {"data": [{"data": {
                "objectId": obj_id, "type": CETUS_POSITION_TYPE,
                "content": {"fields": pos_fields},
            }}], "hasNextPage": False, "nextCursor": None}
        if method == "sui_getObject":
            return {"data": {"objectId": params[0], "type": "0xpool::pool::Pool",
                             "content": {"fields": pool_fields}}}
        raise AssertionError(f"unexpected method {method}")

    real_rpc = ca._sui_rpc
    real_meta = ca.get_coin_metadata
    try:
        ca._sui_rpc = fake_rpc
        ca.get_coin_metadata = lambda ct, url=None: {
            LBTC: {"symbol": "LBTC", "decimals": 8, "name": "Lombard"},
            SUI: {"symbol": "SUI", "decimals": 9, "name": "Sui"},
        }.get(ct, {})
        positions = CetusAdapter().fetch_all_positions(owner, online_mode=True)
    finally:
        ca._sui_rpc = real_rpc
        ca.get_coin_metadata = real_meta

    assert len(positions) == 1, [p.position_id for p in positions]
    assert positions[0].position_id == f"sui:{obj_id}", positions[0].position_id
    assert positions[0].pair == "LBTC/SUI", positions[0].pair
    # RPC shape: StructType filter + limit as the 4th positional param (not in query).
    p = seen["params"]
    assert p[1]["filter"]["StructType"] == CETUS_POSITION_TYPE, p
    assert len(p) == 4 and p[3] == 50, p
    print("✅ Cetus owned-objects fixture -> 1 position (exact StructType filter)")


def test_cetus_price_as_mapping():
    """Part 1: LBTC is priced as BTC via the Sui token registry."""
    import venue_adapters.cetus_adapter as ca
    registry = {"price_as": {"LBTC": "BTC"}}
    assert ca._price_as_symbol("LBTC", registry) == "BTC"
    assert ca._price_as_symbol("deep", registry) == "deep"
    assert ca._price_as_symbol("LBTC", None) == "LBTC"

    class FakePriceEngine:
        def convert_balance_to_fiat(self, amount, symbol, currency="usd"):
            if symbol == "BTC":
                return amount * 84000.0
            if symbol == "SUI":
                return amount * 1.16
            return None

    # N1 ground truth: ~0.0106822 LBTC + 358.925 SUI -> ~$1313.07
    lbtc_amt = 0.0106822
    sui_amt = 358.925
    lbtc_usd = ca._usd(lbtc_amt, "LBTC", FakePriceEngine(), registry=registry)
    sui_usd = ca._usd(sui_amt, "SUI", FakePriceEngine(), registry=registry)
    total = (lbtc_usd or 0.0) + (sui_usd or 0.0)
    assert abs(total - 1313.07) < 5.0, total
    print(f"✅ LBTC price_as BTC -> Pool Value ~${total:.2f}")


def test_cetus_honest_unpriced_total():
    """Part 1: unpriced legs are not silently dropped from USD totals."""
    import venue_adapters.cetus_adapter as ca
    registry = {"price_as": {}}

    class FakePriceEngine:
        def convert_balance_to_fiat(self, amount, symbol, currency="usd"):
            if symbol == "SUI":
                return amount * 1.16
            return None

    raw_fields = {
        "pool": "0x" + "c" * 64,
        "liquidity": "1234567890",
        "tick_lower_index": {"bits": "60159"},
        "tick_upper_index": {"bits": "112276"},
        "coin_type_a": {"name": "0x3::lbtc::LBTC"},
        "coin_type_b": {"name": "0x2::sui::SUI"},
        "fee_owed_a": "100000",
        "fee_owed_b": "200000",
    }
    raw_pool_fields = {
        "current_sqrt_price": str(int(1.0001 ** (106800 / 2.0) * 2 ** 64)),
        "current_tick_index": {"bits": "106800"},
        "tick_spacing": "10",
    }
    real_obj = ca._get_object_json
    real_meta = ca.get_coin_metadata
    try:
        ca._get_object_json = lambda oid: ("0xpkg::pool::Pool", raw_pool_fields)
        ca.get_coin_metadata = lambda ct, url=None: {
            "0x3::lbtc::LBTC": {"symbol": "LBTC", "decimals": 8},
            "0x2::sui::SUI": {"symbol": "SUI", "decimals": 9},
        }.get(ct, {})
        lp = ca.CetusAdapter()._position_from_fields("0x" + "e" * 64, raw_fields, FakePriceEngine())
    finally:
        ca._get_object_json = real_obj
        ca.get_coin_metadata = real_meta

    assert lp.current_value_usd is not None and lp.current_value_usd > 0, lp.current_value_usd
    assert lp.fees_note and "unpriced" in lp.fees_note, lp.fees_note
    assert "LBTC" in lp.fees_note, lp.fees_note
    assert "1 leg unpriced" in lp.fees_note, lp.fees_note
    assert lp.raw_data.get("unpriced_count") == 1, lp.raw_data
    print("✅ unpriced legs flagged with count suffix")


def test_cetus_writer_construction():
    from venue_adapters.cetus_writer import CetusWriter
    from venue_adapters.cetus_adapter import CetusAdapter

    # Writer is now real; it reports unavailable when the agent is not running.
    w = CetusWriter()
    assert w.VENUE_KEY == "cetus"

    # Adapter exposes the writer.
    adapter = CetusAdapter()
    assert adapter.can_write() is True
    writer = adapter.get_writer()
    assert isinstance(writer, CetusWriter)
    print("✅ CetusWriter construction + adapter can_write")


def test_cetus_writer_ptb_shapes():
    """Offline PTB shape test: verify collect/close build valid transaction bytes."""
    import sui_ptb
    import venue_adapters.cetus_writer as cw
    from venue_adapters.cetus_writer import CetusWriter
    from venue_adapters.venue_writer import CollectFeesParams

    pos_id = "0x" + "e" * 64
    pool_id = "0x" + "d" * 64
    sender = "0x" + "a" * 64
    coin_a = "0x" + "f" * 64 + "::coin::A"
    coin_b = "0x2::sui::SUI"

    pos_fields = {
        "pool": pool_id,
        "liquidity": "1234567890",
        "tick_lower_index": {"bits": "60159"},
        "tick_upper_index": {"bits": "112276"},
        "coin_type_a": {"name": coin_a},
        "coin_type_b": {"name": coin_b},
        "fee_owed_a": "100000",
        "fee_owed_b": "200000",
    }
    pool_fields = {
        "current_sqrt_price": str(int(1.0001 ** (106800 / 2.0) * 2 ** 64)),
        "current_tick_index": {"bits": "106800"},
    }

    gas_coin = {"object_id": "0x" + "b" * 64, "version": 1, "digest": "0x" + "c" * 64}

    real_get_obj_cw = cw.sui_rpc
    real_get_obj_sp = sui_ptb.sui_rpc
    real_get_gas = cw.get_gas_coin_object
    real_get_shared = cw.get_shared_object_initial_version
    real_get_ref = cw.get_object_ref
    real_agent_call = CetusWriter._agent_call
    real_serialize = cw.serialize_transaction_data_v1
    real_dry = cw.dry_run_transaction_block

    def fake_rpc(url, method, params, timeout=30):
        if method == "sui_getObject" and params[0] == pos_id:
            return {"data": {
                "objectId": pos_id,
                "type": f"0x1eabed72c53feb3805120a081dc15963c204dc8d091542592abaf7a35689b2fb::position::Position<{coin_a}, {coin_b}>",
                "content": {"fields": pos_fields},
            }}
        if method == "sui_getObject" and params[0] == pool_id:
            return {"data": {"objectId": pool_id, "type": "0xpkg::pool::Pool", "content": {"fields": pool_fields}}}
        raise AssertionError(f"unexpected RPC {method} {params}")

    call_log: List[dict] = []

    def fake_agent_call(self, cmd, **params):
        call_log.append({"cmd": cmd, **params})
        if cmd == "get_sui_address":
            return {"address": sender}
        if cmd == "sign_sui_ptb":
            # _agent_call normally unwraps result["result"]; mimic that.
            return {"tx_hash": "0x" + "1" * 64, "signature_b64": "dummy"}
        raise AssertionError(f"unexpected agent cmd {cmd}")

    def fake_serialize(tx):
        # Capture the built PTB and return dummy bytes.
        call_log.append({"_ptb": tx})
        return b"\x00" * 32

    try:
        cw.sui_rpc = fake_rpc
        sui_ptb.sui_rpc = fake_rpc
        cw.get_gas_coin_object = lambda url, sender, amount_mist=50_000_000: gas_coin
        cw.get_shared_object_initial_version = lambda url, oid: 1
        cw.get_object_ref = lambda url, oid: {"object_id": oid, "version": 1, "digest": "0x" + "c" * 64}
        CetusWriter._agent_call = fake_agent_call
        cw.serialize_transaction_data_v1 = fake_serialize
        cw.dry_run_transaction_block = lambda url, tx_b64: {"effects": {"status": {"status": "success"}}}

        writer = CetusWriter()
        tx_hash = writer.collect_fees(CollectFeesParams(account="N1", position_id=f"sui:{pos_id}"))
        assert tx_hash == "0x" + "1" * 64, tx_hash
        ptb = [c for c in call_log if "_ptb" in c][0]["_ptb"]
        commands = ptb["programmable_transaction"]["commands"]
        assert commands[0]["move_call"]["function"] == "collect_fee"
        assert commands[-1]["kind"] == "transfer_objects"
        call_log.clear()

        tx_hashes = writer.close_position(f"sui:{pos_id}", "N1")
        assert len(tx_hashes) == 1
        ptb = [c for c in call_log if "_ptb" in c][0]["_ptb"]
        commands = ptb["programmable_transaction"]["commands"]
        funcs = [c["move_call"]["function"] for c in commands if c.get("kind") == "move_call"]
        assert "remove_liquidity" in funcs
        assert "collect_fee" in funcs
        assert "close_position" in funcs
    finally:
        cw.sui_rpc = real_get_obj_cw
        sui_ptb.sui_rpc = real_get_obj_sp
        cw.get_gas_coin_object = real_get_gas
        cw.get_shared_object_initial_version = real_get_shared
        cw.get_object_ref = real_get_ref
        CetusWriter._agent_call = real_agent_call
        cw.serialize_transaction_data_v1 = real_serialize
        cw.dry_run_transaction_block = real_dry

    print("✅ CetusWriter collect/close PTB shapes")


def test_cetus_can_handle():
    from venue_adapters.cetus_adapter import CetusAdapter
    a = CetusAdapter()
    assert a.can_handle("0x" + "a" * 64) is True
    assert a.can_handle("sui:0x" + "a" * 64) is True
    assert a.can_handle("0x" + "a" * 40) is False          # EVM address
    assert a.can_handle("545983") is False
    assert a.can_handle("anything", chain_hint="cetus") is True
    print("✅ CetusAdapter.can_handle")


def test_cetus_numeric_registry_id_message():
    """Fix 4: numeric Cetus registry ids get a clear explanatory error."""
    from venue_adapters.cetus_adapter import CetusAdapter
    pos = CetusAdapter().fetch_position("24184", online_mode=True)
    assert pos.error and "registry pool id" in pos.error, pos.error
    assert "paste the position or pool object id" in pos.error, pos.error
    print("✅ Cetus numeric registry id message")


def test_cetus_pool_object_message():
    """Fix 3: pasting a Pool object id explains the difference vs a Position."""
    import venue_adapters.cetus_adapter as ca
    pool_id = "0x" + "d" * 64

    def fake_rpc(url, method, params, timeout=20):
        if method == "sui_getObject" and params[0] == pool_id:
            return {"data": {"objectId": pool_id, "type": "0x1eabed72c53feb3805120a081dc15963c204dc8d091542592abaf7a35689b2fb::pool::Pool<0x2::sui::SUI, 0x3::lbtc::LBTC>", "content": {"fields": {}}}}
        raise AssertionError(f"unexpected {method} {params}")

    real_rpc = ca._sui_rpc
    try:
        ca._sui_rpc = fake_rpc
        pos = ca.CetusAdapter().fetch_position(pool_id, online_mode=True)
    finally:
        ca._sui_rpc = real_rpc

    assert pos.error and "POOL object" in pos.error, pos.error
    assert "POSITION object id" in pos.error, pos.error
    print("✅ Cetus pool object message")


def test_live_sui_cetus_e2e():
    """Read-only live check — gated so the default suite is offline-green.

    Defaults to the N1 wallet/position (verified 2026-09-26); override with
    SUI_E2E_ADDRESS / SUI_E2E_POSITION_ID.
    """
    if not (os.environ.get("COLDSATCK_E2E_RPC") or os.environ.get("COLDSTACK_E2E_RPC")):
        print("⏭️  SKIP live Sui/Cetus e2e — set COLDSATCK_E2E_RPC=1 to run.")
        return
    n1_wallet = "0x04887176a0791ac1837bc654533990820a33f2c289b8636c7066a1865191b314"
    n1_pos = "0x18bee062d705e1e1fded90663b015b23f2c4b2ef57ada55d1f774e4999de4fad"
    addr = os.environ.get("SUI_E2E_ADDRESS", "") or n1_wallet
    pos_id = os.environ.get("SUI_E2E_POSITION_ID", "") or n1_pos

    assets = fetch_sui_assets(addr)
    syms = {a["symbol"] for a in assets}
    print(f"   live Sui assets for {addr[:12]}...: "
          f"{[(a['symbol'], round(a['balance'], 6)) for a in assets]}")
    assert "SUI" in syms, assets

    from venue_adapters.cetus_adapter import CetusAdapter
    positions = CetusAdapter().fetch_all_positions(addr, online_mode=True)
    ids = [p.position_id for p in positions]
    print(f"   live Cetus scan -> {ids}")
    assert f"sui:{pos_id}" in ids, ids
    p = CetusAdapter().fetch_position(pos_id, online_mode=True)
    print(f"   live Cetus: pair={p.pair} range={p.range_low}-{p.range_high} "
          f"range%={p.position_in_range_pct} amounts={p.deposit_amounts} error={p.error}")
    assert not p.error, p.error
    assert p.pair == "LBTC/SUI", p.pair


def main():
    test_parse_coin_type()
    test_fetch_sui_assets_offline()
    test_registry_override()
    test_balance_engine_sui_hook()
    test_cetus_decode()
    test_cetus_adapter_position()
    test_cetus_owned_objects_fixture()
    test_cetus_price_as_mapping()
    test_cetus_honest_unpriced_total()
    test_cetus_writer_construction()
    test_cetus_writer_ptb_shapes()
    test_cetus_can_handle()
    test_cetus_numeric_registry_id_message()
    test_cetus_pool_object_message()
    test_live_sui_cetus_e2e()
    print("✅ ALL SUI + CETUS TESTS PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
