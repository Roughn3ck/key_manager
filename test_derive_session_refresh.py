"""Regression tests for v5.3.27c: Derive Addresses must refresh the live session.

Covers:
- KeyManagerAgent.reload_vault() re-reads a changed vault file.
- GUI.refresh_key_manager_session() sends a reload_vault command to the agent.
- A Solana key derived+saved without a lock/unlock cycle becomes visible to the
  agent's get_solana_address.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent / "src"))

from key_manager_agent import KeyManagerAgent, CryptoEngine


def _make_vault(password: str, data: dict) -> str:
    """Create a temp encrypted vault file and return its path."""
    import tempfile
    crypto = CryptoEngine()
    path = Path(tempfile.mkdtemp()) / "vault.encrypted"
    path.write_text(crypto.encrypt_json(data, password))
    return str(path)


def test_agent_reload_vault_picks_up_new_key():
    password = "secret"
    vault_data = {
        "accounts": {"G3": {"addresses": []}},
        "mnemonics": {},
        "private_keys": {},
    }
    path = _make_vault(password, vault_data)

    agent = KeyManagerAgent(path, password)
    assert agent.unlocked

    # Simulate the GUI deriving and saving a new Solana key.
    privkey_hex = "aa" * 32  # dummy 32-byte ed25519 seed
    from key_manager_agent import _solana_pubkey_from_ed25519
    sol_address = _solana_pubkey_from_ed25519(privkey_hex)
    vault_data["accounts"]["G3"]["addresses"].append({
        "coin": "SOL", "chain": "Solana",
        "address": sol_address,
        "source": "derived", "derivation_path": "m/44'/501'/0'/0'",
    })
    vault_data["private_keys"]["G3"] = [{
        "chain": "SOL (Solana)",
        "key": privkey_hex,
        "source": "derived",
        "derivation_path": "m/44'/501'/0'/0'",
    }]
    Path(path).write_text(CryptoEngine().encrypt_json(vault_data, password))

    # Before reload the agent still sees no keys.
    before = agent.get_solana_address("G3")
    assert before.get("status") == "error"

    # After reload the new key is visible.
    result = agent.reload_vault(password)
    assert result["status"] == "ok"

    after = agent.get_solana_address("G3")
    assert after["status"] == "ok"
    assert after["result"]["address"] == sol_address
    print("PASS test_agent_reload_vault_picks_up_new_key")


def test_legacy_solana_key_labels_are_recognized():
    """Old chain labels like 'Solana', 'SOL', or empty must still be found."""
    password = "secret"
    # Key stored with a legacy label only.
    vault_data = {
        "accounts": {"G3": {"addresses": []}},
        "mnemonics": {},
        "private_keys": {
            "G3": [{"chain": "Solana", "key": "bb" * 32, "source": "manual"}]
        },
    }
    path = _make_vault(password, vault_data)
    agent = KeyManagerAgent(path, password)
    result = agent.get_solana_address("G3")
    assert result["status"] == "ok", result
    print("PASS test_legacy_solana_key_labels_are_recognized")


def test_sui_uses_solana_key_when_no_sui_entry():
    """A Solana-derived key can satisfy Sui address requests too."""
    password = "secret"
    vault_data = {
        "accounts": {"N1": {"addresses": []}},
        "mnemonics": {},
        "private_keys": {
            "N1": [{"chain": "SOL (Solana)", "key": "cc" * 32, "source": "derived"}]
        },
    }
    path = _make_vault(password, vault_data)
    agent = KeyManagerAgent(path, password)
    result = agent.get_sui_address("N1")
    assert result["status"] == "ok", result
    print("PASS test_sui_uses_solana_key_when_no_sui_entry")


def test_gui_refresh_key_manager_session_sends_reload():
    """GUI helper posts reload_vault to the agent and reloads address_db."""
    import json
    import urllib.request

    # Stub a KeyManager that returns fresh data on reload.
    fresh_db = {"accounts": {"G3": {"addresses": [
        {"chain": "Solana", "address": "GHsXL7LM15Y1XueUK1Rx8PS8CS6RS1qcaKEp9rHdZfam"}
    ]}}}
    km = MagicMock()
    km.load_encrypted_data.return_value = True
    km.address_db = fresh_db

    captured = {}

    class _FakeResponse:
        def read(self):
            return json.dumps({"status": "ok", "result": "Vault reloaded"}).encode()
        def __enter__(self): return self
        def __exit__(self, *a): pass

    real_urlopen = urllib.request.urlopen
    def _fake_urlopen(req, timeout=None):
        captured["request"] = req
        return _FakeResponse()

    gui = MagicMock()
    gui.current_password = "secret"
    gui.key_manager = km
    gui._get_agent_url.return_value = "http://127.0.0.1:8842"

    with patch.object(urllib.request, "urlopen", _fake_urlopen):
        import gui_main_v5 as gm
        gm.ColdStackGUI.refresh_key_manager_session(gui)

    km.load_encrypted_data.assert_called_with("secret")
    assert gui.key_manager.address_db == fresh_db
    assert "request" in captured
    body = captured["request"].data.decode()
    cmd = json.loads(body)
    assert cmd["cmd"] == "reload_vault"
    assert cmd["password"] == "secret"
    print("PASS test_gui_refresh_key_manager_session_sends_reload")


def main():
    test_agent_reload_vault_picks_up_new_key()
    test_legacy_solana_key_labels_are_recognized()
    test_sui_uses_solana_key_when_no_sui_entry()
    test_gui_refresh_key_manager_session_sends_reload()
    print("ALL DERIVE-SESSION-REFRESH TESTS PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
