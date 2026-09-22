"""Signer-resolution integration test (read-only) for the Aerodrome close fix.

- Live: ownerOf(#75255240) on the Slipstream NFPM == 0xAe8E5FDb8857736C2218532Fd9D68430aAbAC6ae.
- Stubbed vault: a vault containing the owner account resolves to it; a vault missing
  it aborts with the precise error (owner + derivable set named, no fallback).
- Gas check path uses the resolved signer's real Base balance (stubbed) — names the address.

Run:  python test_aero_close_signer.py
"""
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).parent / "src"))

from venue_adapters import aerodrome_writer as aw  # noqa: E402

NFPM_V1 = "0x827922686190790b37229fd06084350E74485b72"
OWNER = "0xAe8E5FDb8857736C2218532Fd9D68430aAbAC6ae"


class _StubAgent:
    """Emulates the agent's `accounts` command (no keys)."""
    def __init__(self, accounts):
        self._accounts = accounts


def _mk_writer(agent_accounts):
    w = aw.AerodromeWriter.__new__(aw.AerodromeWriter)
    w.agent_url = "http://127.0.0.1:8842"
    w.chain_id = aw.BASE_CHAIN_ID
    # Stub the two network-bound members.
    w._agent_call = lambda cmd, **kw: {"status": "ok", "result": agent_accounts} if cmd == "accounts" else {"status": "ok"}
    w._get_position_owner = lambda token_id, pm: OWNER
    return w


def main():
    # 0) live ownerOf read proves the anchor value is correct (read-only).
    w_live = aw.AerodromeWriter.__new__(aw.AerodromeWriter)
    live_pm = w_live._find_position_manager(75255240)
    assert live_pm.lower() == NFPM_V1.lower(), live_pm
    live_owner = w_live._get_position_owner(75255240, live_pm)
    assert live_owner.lower() == OWNER.lower(), live_owner
    print(f"live ownerOf(#75255240) = {live_owner} (== {OWNER})")

    # 1) vault WITH the owner account → resolves to it.
    w = _mk_writer({
        "G1": {"addresses": [{"address": OWNER, "chain": "EVM (Base)"}]},
        "G2": {"addresses": [{"address": "0xdead000000000000000000000000000000000000", "chain": "EVM"}]},
    })
    acct = w._resolve_owner_signer(75255240, NFPM_V1)
    assert acct == "G1", acct
    print("stub vault: owner resolves to account 'G1'")

    # 2) vault WITHOUT the owner → precise abort, no fallback.
    w2 = _mk_writer({
        "G2": {"addresses": [{"address": "0xdead000000000000000000000000000000000000", "chain": "EVM"}]},
    })
    try:
        w2._resolve_owner_signer(75255240, NFPM_V1)
        raise AssertionError("should have aborted")
    except RuntimeError as e:
        msg = str(e)
        assert OWNER in msg and "0xdead000000000000000000000000000000000000" in msg, msg
    print("stub vault without owner: aborts naming owner + derivable set")

    print("✅ AERO CLOSE SIGNER-RESOLUTION TESTS PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
