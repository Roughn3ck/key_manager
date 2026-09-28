"""Orca close 6005 self-heal flow test (v5.3.22).

Verifies that a ClosePositionNotEmpty (6005) error from the first closePosition
attempt is caught and the full empty-then-close sequence is retried with fresh
data.  The second attempt succeeds and produces a complete CloseResult.

No network calls; no on-chain broadcasts.
"""
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).parent / "src"))

from venue_adapters import orca_writer as ow  # noqa: E402
from coldtrack.close_recorder import CloseLeg, CloseResult  # noqa: E402

POS_MINT = "FbNHxe9VV797JWG7XH2msjwp5Rvb6dGzndwwkEXXXBKX"
WALLET = "HAgk14JpMQLgt6rVgv7cBQFJWFto5Dqxi472uT3DKpqk"


class _FakePosition:
    def __init__(self, liquidity=1_000_000, reward_owed=0):
        self.data = {
            "position_address": "F98SmNgmft21dRAwfXGPtWu95Kb1WcSm58WgaQzUZQQR",
            "position_mint": POS_MINT,
            "whirlpool": "CeaZcxBNLpJWtxzt58qQmfMBtJY8pQLvursXTJYGQpbN",
            "liquidity": liquidity,
            "tick_lower": -100,
            "tick_upper": 100,
            "fee_owed_a": 0,
            "fee_owed_b": 0,
            "reward_infos": [{"amount_owed": reward_owed}],
        }


class _FakePool:
    def __init__(self):
        self.data = {
            "token_mint_a": "So11111111111111111111111111111111111111112",
            "token_mint_b": "cbbtcf3aa214zXHbiAZQwf4122FBYbraNdFqgw4iMij",
            "token_vault_a": "DYkz5CCMUshPUso6Eg25f2kw5cnexYAKSrN6ZPSMAAAA",
            "token_vault_b": "7G26tTFk7VhpqhnRHqvgsWh7NhVeLj7cGNCXs9PWAAAT",
            "reward_infos": [{"initialized": True, "vault": "R1"}],
        }


def _mk_writer():
    w = ow.OrcaWriter.__new__(ow.OrcaWriter)
    w._token_program_cache = {}
    w._get_solana_address = lambda account: WALLET
    w._get_position_data = lambda mint: _FakePosition().data
    w._get_pool_data = lambda addr: _FakePool().data
    w._get_token_program = lambda mint: ow.SPL_TOKEN_PROGRAM_ID
    w._ensure_ata_ix = lambda payer, wallet, mint, token_program=None: (None, "ATA")
    w.price_engine = None
    return w


def test_6005_retry_succeeds():
    """First closePosition raises 6005; retry runs full sequence and succeeds."""
    w = _mk_writer()
    calls = {"decrease": 0, "collect": 0, "reward": 0, "close": 0}

    def _build_modify_liquidity_ix(wallet, pos, pool, liquidity_amount,
                                    token_limit_a, token_limit_b, is_increase):
        calls["decrease"] += 1
        return (b"decrease", [], b"")

    def _build_collect_fees_ix(wallet, pos, pool):
        calls["collect"] += 1
        return (b"collect", [], b"")

    def _build_collect_reward_ix(wallet, pos, pool, index):
        calls["reward"] += 1
        return (ow.SPL_TOKEN_PROGRAM_ID, [], b"", None)

    def _build_close_position_ix(wallet, pos):
        calls["close"] += 1
        return (b"close", [], b"")

    attempt = [0]
    def _sign_and_broadcast_single(account, ixs, extra_instructions=None):
        # First call is closePosition on the first attempt -> 6005.
        if calls["close"] == 1 and attempt[0] == 0:
            attempt[0] += 1
            raise RuntimeError("Transaction failed: {'InstructionError': [3, {'Custom': 6005}]}")
        # Return deterministic sigs per instruction type.
        ix_bytes = ixs[0][0] if ixs else b""
        if ix_bytes == b"decrease":
            return "DECREASE_SIG"
        if ix_bytes == b"collect":
            return "COLLECT_SIG"
        if ix_bytes == b"":
            return "REWARD_SIG"
        return "CLOSE_SIG"

    def _wait_for_confirmation(sig, timeout=30, poll_interval=2.0):
        if sig == "CLOSE_SIG" and calls["close"] == 1:
            raise RuntimeError("Transaction failed: {'InstructionError': [3, {'Custom': 6005}]}")
        return

    def _capture_close_result(wallet):
        return CloseResult(
            position_mint=POS_MINT, platform="Orca", chain="Solana",
            legs=[
                CloseLeg(asset="SOL", amount=1.0, value_usd=200.0, kind="liquidity", sig="DECREASE_SIG"),
                CloseLeg(asset="cbBTC", amount=0.001, value_usd=100.0, kind="liquidity", sig="DECREASE_SIG"),
                CloseLeg(asset="SOL", amount=0.1, value_usd=20.0, kind="fee", sig="COLLECT_SIG"),
            ],
            close_sig="CLOSE_SIG", collect_sig="COLLECT_SIG",
            decrease_sig="DECREASE_SIG", owner=wallet,
        )

    w._build_modify_liquidity_ix = _build_modify_liquidity_ix
    w._build_collect_fees_ix = _build_collect_fees_ix
    w._build_collect_reward_ix = _build_collect_reward_ix
    w._build_close_position_ix = _build_close_position_ix
    w._sign_and_broadcast_single = _sign_and_broadcast_single
    w._wait_for_confirmation = _wait_for_confirmation
    w._capture_close_result = _capture_close_result

    sigs = w.close_position(f"solana:{POS_MINT}", "N1")

    # The retry ran the full sequence a second time.
    assert calls["decrease"] == 2, calls
    assert calls["collect"] == 2, calls
    assert calls["close"] == 2, calls
    assert sigs == ["DECREASE_SIG", "COLLECT_SIG", "CLOSE_SIG"], sigs
    assert w.last_close_result is not None
    assert w.last_close_result.owner == WALLET
    print("✅ 6005 retry runs full empty-then-close sequence and succeeds")


def test_6005_final_failure():
    """If the retry also fails with 6005, the final error is raised."""
    w = _mk_writer()

    w._build_modify_liquidity_ix = lambda *a, **k: (b"decrease", [], b"")
    w._build_collect_fees_ix = lambda *a, **k: (b"collect", [], b"")
    w._build_collect_reward_ix = lambda *a, **k: (ow.SPL_TOKEN_PROGRAM_ID, [], b"", None)
    w._build_close_position_ix = lambda *a, **k: (b"close", [], b"")
    w._sign_and_broadcast_single = lambda *a, **k: "SIG"
    w._wait_for_confirmation = lambda sig, *a, **k: (_ for _ in ()).throw(
        RuntimeError("Transaction failed: {'InstructionError': [3, {'Custom': 6005}]}")
    )

    try:
        w.close_position(f"solana:{POS_MINT}", "N1")
        raise AssertionError("expected RuntimeError")
    except RuntimeError as e:
        assert "6005" in str(e), e
    print("✅ 6005 after retry is reported as final failure")


def main():
    test_6005_retry_succeeds()
    test_6005_final_failure()
    print("✅ ALL ORCA 6005 FLOW TESTS PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
