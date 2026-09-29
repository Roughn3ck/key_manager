"""Tests for Aerodrome staked-position discovery helper (v5.3.25)."""
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).parent / "src"))

from lp_engine import LPPosition
from venue_adapters.aerodrome_staked import (
    discover_staked_positions,
    annotate_staked_position,
    wallet_address_active,
    _is_gauge,
    _reconstruct_token_ownership,
    _parse_transfer_log,
    _RpcState,
    _load_cache,
    _save_cache,
    CACHE_FILE,
    StakedScanError,
    clear_scan_cache,
)

WALLET = "0x1111111111111111111111111111111111111111"
GAUGE = "0x2222222222222222222222222222222222222222"
EOA = "0x3333333333333333333333333333333333333333"
PM1 = "0x827922686190790b37229fd06084350E74485b72"
PM2 = "0xe1f8cd9AC4e4A65F54f38a5CdAfCA44f6dD68b53"
POOL = "0x4444444444444444444444444444444444444444"

TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
SELECTOR_OWNER_OF = "0x6352211e"
SELECTOR_POOL = "0x16f0115b"

_ORIGINAL_CACHE_FILE = CACHE_FILE


def _use_temp_cache():
    """Redirect the on-disk scan cache to a temp file for the current test."""
    import venue_adapters.aerodrome_staked as _mod
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    tmp.close()
    _mod.CACHE_FILE = Path(tmp.name)
    clear_scan_cache()


def _restore_cache():
    import venue_adapters.aerodrome_staked as _mod
    _mod.CACHE_FILE = _ORIGINAL_CACHE_FILE


def _topic_address(addr: str) -> str:
    return "0x" + "0" * 24 + addr[2:].lower()


def _topic_token_id(token_id: int) -> str:
    return "0x" + format(token_id, "064x")


def _address_result(addr: str) -> str:
    return "0x" + "0" * 24 + addr[2:].lower()


def _make_log(token_id: int, from_addr: str, to_addr: str, block: int, log_index: int, address: str = PM1):
    return {
        "address": address,
        "blockNumber": hex(block),
        "logIndex": hex(log_index),
        "topics": [
            TRANSFER_TOPIC,
            _topic_address(from_addr),
            _topic_address(to_addr),
            _topic_token_id(token_id),
        ],
    }


def _state_from_callable(rpc_call):
    return _RpcState(rpc_call)


def test_is_gauge_returns_pool():
    def rpc(method, params):
        if method == "eth_call" and params[0]["data"].startswith(SELECTOR_POOL):
            return _address_result("0x4444444444444444444444444444444444444444")
        return None

    state = _state_from_callable(rpc)
    assert _is_gauge(GAUGE, state) == "0x4444444444444444444444444444444444444444"


def test_is_gauge_rejects_zero_address():
    def rpc(method, params):
        if method == "eth_call" and params[0]["data"].startswith(SELECTOR_POOL):
            return "0x" + "0" * 64
        return None

    state = _state_from_callable(rpc)
    assert _is_gauge(GAUGE, state) is None


def test_parse_transfer_log():
    log = _make_log(7088644, WALLET, GAUGE, 12345, 7)
    parsed = _parse_transfer_log(log, WALLET.lower())
    assert parsed["token_id"] == 7088644
    assert parsed["from"] == WALLET.lower()
    assert parsed["to"] == GAUGE.lower()
    assert parsed["block_number"] == 12345
    assert parsed["log_index"] == 7


def test_reconstruct_ownership_held_vs_sent():
    transfers = [
        {"token_id": 1, "from": EOA.lower(), "to": WALLET.lower(), "block_number": 100, "log_index": 0, "position_manager": PM1, "_wallet": WALLET.lower()},
        {"token_id": 2, "from": WALLET.lower(), "to": GAUGE.lower(), "block_number": 200, "log_index": 0, "position_manager": PM1, "_wallet": WALLET.lower()},
        {"token_id": 3, "from": WALLET.lower(), "to": GAUGE.lower(), "block_number": 300, "log_index": 0, "position_manager": PM1, "_wallet": WALLET.lower()},
        {"token_id": 3, "from": GAUGE.lower(), "to": WALLET.lower(), "block_number": 400, "log_index": 0, "position_manager": PM1, "_wallet": WALLET.lower()},
    ]
    held, sent = _reconstruct_token_ownership(transfers)
    assert 1 in held
    assert 2 in sent
    assert 3 in held
    assert 3 not in sent


def _default_discovery_rpc(logs):
    """Return a test RPC callable for the happy-path staked-discovery tests."""
    calls = {"owner": 0, "pool": 0}

    wallet_topic = _topic_address(WALLET)

    def rpc(method, params):
        if method == "eth_blockNumber":
            # Use a low latest block so the fallback window (latest - max_blocks) covers block 1000.
            return {"result": "0x10000"}
        if method == "eth_getTransactionCount":
            # Force fallback to max_blocks (first-activity discovery not implemented here).
            return None
        if method == "eth_getLogs":
            address = params[0]["address"]
            topic = params[0]["topics"]
            # Match both directions: topic[1]=wallet for sends, topic[2]=wallet for receives.
            if address.lower() == PM1.lower() and (
                topic[1] == wallet_topic or topic[2] == wallet_topic
            ):
                return {"result": logs}
            return {"result": []}
        if method == "eth_call":
            data = params[0]["data"]
            to = params[0]["to"].lower()
            if data.startswith(SELECTOR_OWNER_OF) and to == PM1.lower():
                calls["owner"] += 1
                return _address_result(GAUGE)
            if data.startswith(SELECTOR_POOL) and to == GAUGE.lower():
                calls["pool"] += 1
                return _address_result("0x4444444444444444444444444444444444444444")
        return None

    return rpc, calls


def test_discover_staked_positions_finds_staked_id():
    """Full flow: token sent to gauge, ownerOf returns gauge, gauge.pool() returns pool."""
    rpc, calls = _default_discovery_rpc([_make_log(7088644, WALLET, GAUGE, 1000, 1, PM1)])

    _use_temp_cache()
    try:
        result = discover_staked_positions(WALLET, rpc_call=rpc, max_blocks=1_000_000, chunk_size=100_000)
    finally:
        _restore_cache()

    assert len(result) == 1
    token_id, pm, gauge = result[0]
    assert token_id == 7088644
    assert pm.lower() == PM1.lower()
    assert gauge.lower() == GAUGE.lower()
    assert calls["owner"] >= 1
    assert calls["pool"] >= 1


def test_discover_staked_positions_skips_eoa_recipient():
    rpc, _ = _default_discovery_rpc([_make_log(99, WALLET, EOA, 1000, 1, PM1)])

    def rpc2(method, params):
        if method == "eth_call":
            data = params[0]["data"]
            to = params[0]["to"].lower()
            if data.startswith(SELECTOR_OWNER_OF) and to == PM1.lower():
                return _address_result(EOA)
            if data.startswith(SELECTOR_POOL) and to == EOA.lower():
                return "0x" + "0" * 64
        return rpc(method, params)

    _use_temp_cache()
    try:
        result = discover_staked_positions(WALLET, rpc_call=rpc2)
    finally:
        _restore_cache()
    assert result == []


def test_discover_staked_positions_returns_to_wallet_not_staked():
    """Token sent to gauge but later returned to wallet must not be reported staked."""
    def rpc(method, params):
        if method == "eth_blockNumber":
            return {"result": "0x200000"}
        if method == "eth_getTransactionCount":
            return None
        if method == "eth_getLogs":
            address = params[0]["address"]
            topic_from = params[0]["topics"][1]
            topic_to = params[0]["topics"][2]
            logs = []
            if address.lower() == PM1.lower():
                if topic_from == _topic_address(WALLET):
                    logs.append(_make_log(5, WALLET, GAUGE, 1000, 1, PM1))
                if topic_to == _topic_address(WALLET):
                    logs.append(_make_log(5, GAUGE, WALLET, 2000, 1, PM1))
            return {"result": logs}
        if method == "eth_call" and params[0]["data"].startswith(SELECTOR_OWNER_OF):
            return _address_result(WALLET)
        return None

    _use_temp_cache()
    try:
        result = discover_staked_positions(WALLET, rpc_call=rpc)
    finally:
        _restore_cache()
    assert result == []


def test_annotate_staked_position():
    pos = LPPosition(
        position_id="base:7088644",
        venue="Aerodrome",
        chain="BASE",
    )
    annotate_staked_position(pos, GAUGE, WALLET)
    assert pos.raw_data["is_staked"] is True
    assert pos.raw_data["gauge_address"] == GAUGE
    assert WALLET in pos.raw_data["owner_display"]
    assert "staked via gauge" in pos.raw_data["owner_display"]


def test_annotate_staked_position_preserves_existing_raw_data():
    pos = LPPosition(
        position_id="base:7088644",
        venue="Aerodrome",
        chain="BASE",
        raw_data={"liquidity": 123},
    )
    annotate_staked_position(pos, GAUGE, WALLET)
    assert pos.raw_data["liquidity"] == 123
    assert pos.raw_data["is_staked"] is True


def test_adaptive_chunking_halves_on_failure():
    """Simulate an RPC that rejects chunks > 50,000; discovery should adapt and succeed."""
    call_log = []

    def rpc(method, params):
        if method == "eth_blockNumber":
            return {"result": "0x100000"}  # 1,048,576
        if method == "eth_getTransactionCount":
            return None
        if method == "eth_getLogs":
            from_block = int(params[0]["fromBlock"], 16)
            to_block = int(params[0]["toBlock"], 16)
            call_log.append((from_block, to_block))
            if to_block - from_block + 1 > 50_000:
                # Simulate a range-too-large response.
                return {"error": {"message": "eth_getLogs is limited to 50000 blocks"}}
            address = params[0]["address"]
            topic = params[0]["topics"]
            wallet_topic = _topic_address(WALLET)
            if address.lower() == PM1.lower() and (
                topic[1] == wallet_topic or topic[2] == wallet_topic
            ):
                return {"result": [_make_log(42, WALLET, GAUGE, 500_000, 1, PM1)]}
            return {"result": []}
        if method == "eth_call":
            data = params[0]["data"]
            to = params[0]["to"].lower()
            if data.startswith(SELECTOR_OWNER_OF) and to == PM1.lower():
                return _address_result(GAUGE)
            if data.startswith(SELECTOR_POOL) and to == GAUGE.lower():
                return _address_result("0x4444444444444444444444444444444444444444")
        return None

    _use_temp_cache()
    try:
        result = discover_staked_positions(
            WALLET, rpc_call=rpc, max_blocks=1_000_000, chunk_size=200_000
        )
    finally:
        _restore_cache()

    assert len(result) == 1
    assert result[0][0] == 42
    # The first attempt should have been the full 200k; it failed, then halved.
    assert any(to_block - from_block + 1 > 50_000 for from_block, to_block in call_log)
    # Eventually a <=50k request succeeded and found the log.
    assert any(to_block - from_block + 1 <= 50_000 for from_block, to_block in call_log)


def test_all_rpc_failure_raises_staked_scan_error():
    """When every RPC fails at the minimum chunk size, a real error is raised."""
    def rpc(method, params):
        if method == "eth_blockNumber":
            return {"result": "0x100000"}
        if method == "eth_getTransactionCount":
            return None
        if method == "eth_getLogs":
            return {"error": {"message": "eth_getLogs is limited to 50 blocks"}}
        return None

    _use_temp_cache()
    try:
        try:
            discover_staked_positions(WALLET, rpc_call=rpc, max_blocks=100_000, chunk_size=100_000)
        except StakedScanError as exc:
            assert "RPC limits hit" in str(exc)
            assert "deposit ID" in str(exc)
            return
        raise AssertionError("expected StakedScanError")
    finally:
        _restore_cache()


def test_incremental_cache_uses_last_scanned_block():
    """Second scan should resume near the cached last-scanned block, not from genesis."""
    logs = [_make_log(7, WALLET, GAUGE, 150_000, 1, PM1)]

    call_log = []

    def rpc(method, params):
        if method == "eth_blockNumber":
            return {"result": "0x30000"}  # 196,608
        if method == "eth_getTransactionCount":
            return None
        if method == "eth_getLogs":
            from_block = int(params[0]["fromBlock"], 16)
            to_block = int(params[0]["toBlock"], 16)
            call_log.append((from_block, to_block))
            address = params[0]["address"]
            topic = params[0]["topics"]
            wallet_topic = _topic_address(WALLET)
            if (
                address.lower() == PM1.lower()
                and (topic[1] == wallet_topic or topic[2] == wallet_topic)
                and from_block <= 150_000 <= to_block
            ):
                return {"result": logs}
            return {"result": []}
        if method == "eth_call":
            data = params[0]["data"]
            to = params[0]["to"].lower()
            if data.startswith(SELECTOR_OWNER_OF) and to == PM1.lower():
                return _address_result(GAUGE)
            if data.startswith(SELECTOR_POOL) and to == GAUGE.lower():
                return _address_result("0x4444444444444444444444444444444444444444")
        return None

    _use_temp_cache()
    try:
        # First scan: discovers token at block 150,000 and caches last_scanned_block.
        result1 = discover_staked_positions(
            WALLET, rpc_call=rpc, max_blocks=200_000, chunk_size=50_000
        )
        assert len(result1) == 1
        cache = _load_cache()
        cached_last = cache[WALLET.lower()]["last_scanned_block"]
        assert cached_last == 0x30000

        # Second scan: latest is only slightly higher; it should start near the cache.
        def rpc2(method, params):
            if method == "eth_blockNumber":
                return {"result": "0x30100"}  # +256 blocks
            return rpc(method, params)

        call_log.clear()
        discover_staked_positions(
            WALLET, rpc_call=rpc2, max_blocks=200_000, chunk_size=50_000
        )
        # The second scan should cover only the small incremental window near the
        # cached block (plus one probe chunk).  A full re-scan from genesis would
        # produce many chunks starting near zero.
        assert len(call_log) <= 4, f"second scan was too large: {len(call_log)} chunks"
        min_from = min(fb for fb, _ in call_log)
        assert min_from >= 140_000, f"second scan started too early: {min_from}"
    finally:
        _restore_cache()


def test_rate_limit_backoff_retries_same_chunk():
    """HTTP 429 should retry the same chunk (not split it) and eventually succeed."""
    logs = [_make_log(55, WALLET, GAUGE, 1000, 1, PM1)]
    calls = []

    def rpc(method, params):
        if method == "eth_blockNumber":
            return {"result": "0x10000"}
        if method == "eth_getTransactionCount":
            return None
        if method == "eth_getLogs":
            calls.append((int(params[0]["fromBlock"], 16), int(params[0]["toBlock"], 16)))
            if len(calls) < 3:
                return {"error": {"message": "over rate limit (HTTP 429)"}}
            return {"result": logs}
        if method == "eth_call":
            data = params[0]["data"]
            to = params[0]["to"].lower()
            if data.startswith(SELECTOR_OWNER_OF) and to == PM1.lower():
                return _address_result(GAUGE)
            if data.startswith(SELECTOR_POOL) and to == GAUGE.lower():
                return _address_result(POOL)
        return None

    _use_temp_cache()
    try:
        result = discover_staked_positions(
            WALLET, rpc_call=rpc, max_blocks=1_000_000, chunk_size=100_000, max_seconds=30
        )
    finally:
        _restore_cache()

    assert len(result) == 1
    assert result[0][0] == 55
    # All three attempts were against the same chunk window.
    assert len(set(calls)) == 1, f"chunk changed between retries: {calls}"


def test_hard_budget_reports_coverage():
    """When the scan hits max_seconds, return_info reports the scanned range."""
    def rpc(method, params):
        if method == "eth_blockNumber":
            return {"result": "0x100000"}
        if method == "eth_getTransactionCount":
            return None
        if method == "eth_getLogs":
            time.sleep(0.1)
            return {"result": []}
        return None

    _use_temp_cache()
    try:
        discoveries, info = discover_staked_positions(
            WALLET,
            rpc_call=rpc,
            max_blocks=1_000_000,
            chunk_size=200_000,
            max_seconds=0.05,
            return_info=True,
        )
    finally:
        _restore_cache()

    assert info["complete"] is False
    assert info["scanned_from"] < info["scanned_to"]
    assert discoveries == []


def test_wallet_address_active():
    def rpc(method, params):
        if method == "eth_getTransactionCount":
            return "0x5"
        return None

    assert wallet_address_active(WALLET, rpc_call=rpc) is True

    def rpc_zero(method, params):
        if method == "eth_getTransactionCount":
            return "0x0"
        return None

    assert wallet_address_active(WALLET, rpc_call=rpc_zero) is False


def test_guard_helper_blocks_staked_position():
    """Minimal coverage for the lp_tab staked-action guard helper."""
    from lp_tab import LPTab

    gui = MagicMock()
    tab = LPTab.__new__(LPTab)
    tab.gui = gui

    pos = LPPosition(
        position_id="hyperevm:7088644",
        venue="Project X",
        chain="Hyperliquid",
        raw_data={"is_staked": True},
    )
    assert tab._lp_guard_staked_action(pos, "Close") is True
    gui.show_notification.assert_called_once()
    assert "Unstake" in gui.show_notification.call_args[0][0]


def test_guard_helper_allows_staked_aerodrome():
    """Staked Aerodrome cards have their own dedicated gauge buttons."""
    from lp_tab import LPTab

    gui = MagicMock()
    tab = LPTab.__new__(LPTab)
    tab.gui = gui

    pos = LPPosition(
        position_id="base:7088644",
        venue="Aerodrome",
        chain="BASE",
        raw_data={"is_staked": True},
    )
    assert tab._lp_guard_staked_action(pos, "Close") is False
    gui.show_notification.assert_not_called()


def _make_simulation_rpc(gauge: str, authorized: set, non_auth_reason="NA", preconditions=None):
    """Return a fake _base_rpc_call that supports gauge simulation."""
    preconditions = preconditions or {}

    def _fake_base_rpc(method, params):
        if method == "eth_call":
            data = params[0]["data"]
            to = params[0].get("to", "").lower()
            from_addr = params[0].get("from", "").lower()
            if data.startswith("0x6352211e"):
                return "0x" + "0" * 24 + gauge[2:]
            if data == "0x16f0115b":
                return "0x" + "0" * 24 + "42d4a22cad0f5a49681a5715ce994af73a43b76b"
            if data.startswith("0x28c55f69") and to == gauge.lower():
                if from_addr in {a.lower() for a in authorized}:
                    # Non-auth precondition overrides success if configured.
                    reason = preconditions.get(from_addr)
                    if reason:
                        raise RuntimeError(f"execution reverted: {reason}")
                    return "0x"
                raise RuntimeError(f"execution reverted: {non_auth_reason}")
        if method == "eth_getLogs":
            return []
        return None

    return _fake_base_rpc


def test_staked_signer_resolution_unsaved():
    """Unsaved staked position resolves signer by simulating gauge.withdraw."""
    from lp_tab import LPTab
    from lp_engine import LPPosition
    from unittest.mock import MagicMock, patch

    gui = MagicMock()
    tab = LPTab.__new__(LPTab)
    tab.gui = gui

    staker_address = "0x40c33B69e7aB4B22Eb8ec7D164e155F769F8c948"
    gauge = "0x61E0B10423a0009C3f83ab4313813d29437d0817"

    pos = LPPosition(
        position_id="base:7088644",
        venue="Aerodrome",
        chain="BASE",
        raw_data={"is_staked": True},
    )

    writer = MagicMock()
    writer.is_available.return_value = True
    writer._get_account_address.return_value = staker_address
    gui.lp_engine.get_writer.return_value = writer
    gui.key_manager.address_db.get.return_value = {
        "G2": {"addresses": [{"address": staker_address}]}
    }

    fake_rpc = _make_simulation_rpc(gauge, {staker_address})
    with patch("venue_adapters.aerodrome_adapter._base_rpc_call", fake_rpc):
        acct = tab._lp_verify_evm_position_ownership(pos, "G2", "aerodrome")
    assert acct == "G2", acct


def test_staked_signer_resolution_no_vault_match():
    """If no vault account simulates clean, error says no account is authorized."""
    from lp_tab import LPTab
    from lp_engine import LPPosition
    from unittest.mock import MagicMock, patch

    gui = MagicMock()
    tab = LPTab.__new__(LPTab)
    tab.gui = gui

    gauge = "0x61E0B10423a0009C3f83ab4313813d29437d0817"

    pos = LPPosition(
        position_id="base:7088644",
        venue="Aerodrome",
        chain="BASE",
        raw_data={"is_staked": True},
    )

    writer = MagicMock()
    writer.is_available.return_value = True
    writer._get_account_address.return_value = "0x0000000000000000000000000000000000000001"
    gui.lp_engine.get_writer.return_value = writer
    gui.key_manager = None

    fake_rpc = _make_simulation_rpc(gauge, set())
    try:
        with patch("venue_adapters.aerodrome_adapter._base_rpc_call", fake_rpc):
            tab._lp_verify_evm_position_ownership(pos, "NOBODY", "aerodrome")
    except RuntimeError as e:
        msg = str(e)
        assert "staked position #7088644" in msg, msg
        assert "no vault account is authorized" in msg, msg
        return
    raise AssertionError("expected RuntimeError")


def test_staked_signer_resolution_auth_revert_then_next_candidate():
    """A candidate that reverts with 'NA' is skipped; the next clean candidate wins."""
    from lp_tab import LPTab
    from lp_engine import LPPosition
    from unittest.mock import MagicMock, patch

    gui = MagicMock()
    tab = LPTab.__new__(LPTab)
    tab.gui = gui

    wrong_addr = "0x0000000000000000000000000000000000000001"
    right_addr = "0x40c33B69e7aB4B22Eb8ec7D164e155F769F8c948"
    gauge = "0x61E0B10423a0009C3f83ab4313813d29437d0817"

    pos = LPPosition(
        position_id="base:7088644",
        venue="Aerodrome",
        chain="BASE",
        raw_data={"is_staked": True},
    )

    writer = MagicMock()
    writer.is_available.return_value = True

    def _derive(account):
        return {"G1": wrong_addr, "G2": right_addr}.get(account, "")

    writer._get_account_address.side_effect = _derive
    gui.lp_engine.get_writer.return_value = writer
    gui.key_manager.address_db.get.return_value = {
        "G1": {"addresses": [{"address": wrong_addr}]},
        "G2": {"addresses": [{"address": right_addr}]},
    }

    fake_rpc = _make_simulation_rpc(gauge, {right_addr})
    with patch("venue_adapters.aerodrome_adapter._base_rpc_call", fake_rpc):
        acct = tab._lp_verify_evm_position_ownership(pos, "G1", "aerodrome")
    assert acct == "G2", acct


def test_staked_signer_resolution_non_auth_revert_validates():
    """A candidate that reverts for a non-auth reason is treated as the staker."""
    from lp_tab import LPTab
    from lp_engine import LPPosition
    from unittest.mock import MagicMock, patch

    gui = MagicMock()
    tab = LPTab.__new__(LPTab)
    tab.gui = gui

    staker_address = "0x40c33B69e7aB4B22Eb8ec7D164e155F769F8c948"
    gauge = "0x61E0B10423a0009C3f83ab4313813d29437d0817"

    pos = LPPosition(
        position_id="base:7088644",
        venue="Aerodrome",
        chain="BASE",
        raw_data={"is_staked": True},
    )

    writer = MagicMock()
    writer.is_available.return_value = True
    writer._get_account_address.return_value = staker_address
    gui.lp_engine.get_writer.return_value = writer
    gui.key_manager.address_db.get.return_value = {
        "G2": {"addresses": [{"address": staker_address}]}
    }

    fake_rpc = _make_simulation_rpc(
        gauge, {staker_address},
        preconditions={staker_address.lower(): "ZA"},
    )
    with patch("venue_adapters.aerodrome_adapter._base_rpc_call", fake_rpc):
        acct = tab._lp_verify_evm_position_ownership(pos, "G2", "aerodrome")
    assert acct == "G2", acct


def test_guard_helper_allows_unstaked_position():
    from lp_tab import LPTab

    gui = MagicMock()
    tab = LPTab.__new__(LPTab)
    tab.gui = gui

    pos = LPPosition(
        position_id="base:7088644",
        venue="Aerodrome",
        chain="BASE",
        raw_data={},
    )
    assert tab._lp_guard_staked_action(pos, "Close") is False
    gui.show_notification.assert_not_called()


if __name__ == "__main__":
    tests = [
        test_is_gauge_returns_pool,
        test_is_gauge_rejects_zero_address,
        test_parse_transfer_log,
        test_reconstruct_ownership_held_vs_sent,
        test_discover_staked_positions_finds_staked_id,
        test_discover_staked_positions_skips_eoa_recipient,
        test_discover_staked_positions_returns_to_wallet_not_staked,
        test_annotate_staked_position,
        test_annotate_staked_position_preserves_existing_raw_data,
        test_adaptive_chunking_halves_on_failure,
        test_all_rpc_failure_raises_staked_scan_error,
        test_incremental_cache_uses_last_scanned_block,
        test_rate_limit_backoff_retries_same_chunk,
        test_hard_budget_reports_coverage,
        test_wallet_address_active,
        test_guard_helper_blocks_staked_position,
        test_guard_helper_allows_staked_aerodrome,
        test_staked_signer_resolution_unsaved,
        test_staked_signer_resolution_no_vault_match,
        test_staked_signer_resolution_auth_revert_then_next_candidate,
        test_staked_signer_resolution_non_auth_revert_validates,
        test_guard_helper_allows_unstaked_position,
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
        print(f"{failed} test(s) failed")
        sys.exit(1)
    print("ALL PASS")
