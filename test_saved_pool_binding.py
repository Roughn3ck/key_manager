"""Saved-pool account binding tests (v5.3.18).

No GUI; tests saved_pools CRUD and lp_tab binding resolution / self-heal path.
"""
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).parent / "src"))

from saved_pools import save_pool, load_saved_pools, update_saved_pool_binding
from lp_engine import LPPosition
from lp_tab import LPTab


class StubKeyManager:
    address_db = {"accounts": {
        "G1": {"addresses": [
            {"address": "0xAe8E5FDb8857736C2218532Fd9D68430aAbAC6ae", "chain": "EVM (Base)"},
        ]},
        "G2": {"addresses": [
            {"address": "0x40c3d6b6eF71dA2A2CE6dE9B6A9A3F5B0B2c1D2e", "chain": "EVM (Base)"},
        ]},
    }, "private_keys": {}}

    def save_encrypted_data(self, password):
        return True


class StubEngine:
    def get_writer(self, venue_key, password):
        accounts = self._accounts
        class W:
            chain_id = 8453
            is_available = lambda self: True
            def _get_account_address(self, account):
                return {
                    "G1": "0xAe8E5FDb8857736C2218532Fd9D68430aAbAC6ae",
                    "G2": "0x40c3d6b6eF71dA2A2CE6dE9B6A9A3F5B0B2c1D2e",
                }.get(account, "")
            def _resolve_owner_signer(self, token_id, pm):
                # owner from the live check is 0xAe8E...AC6ae
                owner = "0xAe8E5FDb8857736C2218532Fd9D68430aAbAC6ae"
                for name, data in accounts.items():
                    for addr in data.get("addresses", []):
                        if addr.get("address", "").lower() == owner.lower():
                            return name
                raise RuntimeError(
                    f"position owner {owner} matches no account in this vault for Base "
                    "(derivable: )."
                )
        return W()
    @property
    def _accounts(self):
        return self.gui.key_manager.address_db.get("accounts", {})

    def __init__(self):
        self.gui = None


class StubGui:
    online_mode = False
    key_manager = StubKeyManager()
    current_password = "testpass"
    last_note = None

    def __init__(self):
        self.lp_engine = StubEngine()
        self.lp_engine.gui = self

    def show_notification(self, msg, error=False):
        self.last_note = (msg, error)


class StubRoot:
    def after(self, ms, fn):
        fn()


def build_tab():
    gui = StubGui()
    gui.root = StubRoot()
    tab = LPTab(gui)
    tab._lp_widgets["selector_menu"] = type("M", (), {"get": lambda self: "Account"})()
    return tab, gui


def test_save_pool_stores_binding():
    db = {}
    ok = save_pool(
        db, "0xAe8E5FDb8857736C2218532Fd9D68430aAbAC6ae", 75255240, "Aerodrome",
        "0xpool", "EURC/cbBTC",
        account_name="G1", account_chain="base",
    )
    assert ok
    entry = db["saved_pools"][0]
    assert entry["account_name"] == "G1"
    assert entry["account_chain"] == "base"


def test_resolve_uses_binding_not_selector():
    tab, gui = build_tab()
    gui.key_manager.address_db["saved_pools"] = [{
        "token_id": 75255240,
        "venue": "Aerodrome",
        "wallet_address": "0xAe8E5FDb8857736C2218532Fd9D68430aAbAC6ae",
        "account_name": "G1",
        "account_address": "0xAe8E5FDb8857736C2218532Fd9D68430aAbAC6ae",
        "account_chain": "base",
        "pair": "EURC/cbBTC",
    }]
    # Top-bar selector is not set, but the record is bound to G1.
    pos = LPPosition(position_id="base:75255240", venue="Aerodrome", chain="Base", pair="EURC/cbBTC")
    wallet, account = tab._lp_resolve_wallet_for_position(pos)
    assert wallet.lower() == "0xAe8E5FDb8857736C2218532Fd9D68430aAbAC6ae".lower()
    assert account == "G1"


def test_legacy_self_heal_persists_binding():
    tab, gui = build_tab()
    gui.key_manager.address_db["saved_pools"] = [{
        "token_id": 75255240,
        "venue": "Aerodrome",
        "wallet_address": "0xAe8E5FDb8857736C2218532Fd9D68430aAbAC6ae",
        "pair": "EURC/cbBTC",
    }]
    # Stub vault has G1 matching the live owner; self-heal should bind G1.
    pos = LPPosition(position_id="base:75255240", venue="Aerodrome", chain="Base", pair="EURC/cbBTC")
    account = tab._lp_verify_evm_position_ownership(pos, "G2", "aerodrome")
    assert account == "G1"
    entry = gui.key_manager.address_db["saved_pools"][0]
    assert entry.get("account_name") == "G1"
    assert entry.get("account_address", "").lower() == "0xAe8E5FDb8857736C2218532Fd9D68430aAbAC6ae".lower()


def test_bound_mismatch_error():
    tab, gui = build_tab()
    gui.key_manager.address_db["saved_pools"] = [{
        "token_id": 75255240,
        "venue": "Aerodrome",
        "wallet_address": "0x40c3d6b6eF71dA2A2CE6dE9B6A9A3F5B0B2c1D2e",
        "account_name": "G2",
        "account_address": "0x40c3d6b6eF71dA2A2CE6dE9B6A9A3F5B0B2c1D2e",
        "account_chain": "base",
        "pair": "EURC/cbBTC",
    }]
    pos = LPPosition(position_id="base:75255240", venue="Aerodrome", chain="Base", pair="EURC/cbBTC")
    try:
        tab._lp_verify_evm_position_ownership(pos, "G2", "aerodrome")
    except RuntimeError as e:
        msg = str(e)
        assert "record is bound to account 'G2'" in msg, msg
        assert "ownerOf(75255240)" in msg, msg
        return
    raise AssertionError("expected binding mismatch error")


def test_owner_not_in_vault_error():
    tab, gui = build_tab()
    # Empty accounts
    gui.key_manager.address_db["accounts"] = {}
    gui.key_manager.address_db["saved_pools"] = []
    pos = LPPosition(position_id="base:75255240", venue="Aerodrome", chain="Base", pair="EURC/cbBTC")
    try:
        tab._lp_verify_evm_position_ownership(pos, "", "aerodrome")
    except RuntimeError as e:
        msg = str(e)
        assert "position owner" in msg and "matches no vault account" in msg, msg
        return
    raise AssertionError("expected no-vault-account error")


def main():
    test_save_pool_stores_binding()
    test_resolve_uses_binding_not_selector()
    test_legacy_self_heal_persists_binding()
    test_bound_mismatch_error()
    test_owner_not_in_vault_error()
    print("✅ SAVED-POOL BINDING TESTS PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
