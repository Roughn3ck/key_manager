"""LP dispatch guard tests (v5.3.17).

No GUI is created; we instantiate LPTab with a stub gui and assert:
- A G1-style Aerodrome position resolves to aerodrome writer key + Base chain.
- A record with missing/ambiguous venue aborts with the required message.
- The deprecated _lp_get_chain_info no longer silently defaults to HyperEVM.
"""
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).parent / "src"))

from lp_engine import LPPosition
from lp_tab import LPTab


class StubKeyManager:
    address_db = {}


class StubEngine:
    def get_writer(self, venue_key, password):
        class W:
            chain_id = {"aerodrome": 8453, "hyperliquid": 999, "bsc": 56}.get(venue_key)
            is_available = lambda self: True
        return W()


class StubGui:
    online_mode = False
    key_manager = StubKeyManager()
    lp_engine = StubEngine()
    current_password = ""

    def show_notification(self, msg, error=False):
        self.last_note = (msg, error)


class StubRoot:
    def after(self, ms, fn):
        fn()


def build_tab():
    gui = StubGui()
    gui.root = StubRoot()
    tab = LPTab(gui)
    return tab, gui


def test_aerodrome_resolves_from_record():
    tab, _ = build_tab()
    pos = LPPosition(
        position_id="base:75255240",
        venue="Aerodrome",
        chain="Base",
        pair="EURC/cbBTC",
    )
    chain_name, gas_token, venue_key = tab._lp_resolve_venue_for_position(pos)
    assert venue_key == "aerodrome"
    assert chain_name == "BASE"
    assert gas_token == "ETH"


def test_record_takes_precedence_over_position_id():
    tab, _ = build_tab()
    # A HyperEVM-looking id but the record says Aerodrome — must trust the record.
    pos = LPPosition(
        position_id="hyperevm:75255240",
        venue="Aerodrome",
        chain="Base",
        pair="EURC/cbBTC",
    )
    chain_name, gas_token, venue_key = tab._lp_resolve_venue_for_position(pos)
    assert venue_key == "aerodrome"


def test_ambiguous_record_aborts():
    tab, _ = build_tab()
    pos = LPPosition(
        position_id="weird:123",
        venue="",
        chain="",
        pair="",
    )
    try:
        tab._lp_resolve_venue_for_position(pos)
    except RuntimeError as e:
        assert "could not determine venue" in str(e).lower()
        assert "refetch the position" in str(e).lower()
        return
    raise AssertionError("expected RuntimeError for ambiguous venue")


def test_get_chain_info_no_longer_defaults_hyperliquid():
    tab, _ = build_tab()
    chain_name, gas_token, venue_key = tab._lp_get_chain_info("unknown:123")
    assert venue_key == ""
    assert chain_name == "Unknown"


def test_hyperliquid_still_resolves():
    tab, _ = build_tab()
    pos = LPPosition(
        position_id="hyperevm:2242261",
        venue="Hyperliquid",
        chain="HyperEVM",
        pair="HYPE/WHYPE",
    )
    _, _, venue_key = tab._lp_resolve_venue_for_position(pos)
    assert venue_key == "hyperliquid"


def main():
    test_aerodrome_resolves_from_record()
    test_record_takes_precedence_over_position_id()
    test_ambiguous_record_aborts()
    test_get_chain_info_no_longer_defaults_hyperliquid()
    test_hyperliquid_still_resolves()
    print("✅ LP DISPATCH GUARD TESTS PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
