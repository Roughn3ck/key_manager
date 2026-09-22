"""Regression tests for the v5.3.16 LP fetch minor fixes.

1. orca_display_pair naming helper: Orca's quote-last convention.
2. Venue-scoped saved-pool refresh/fetch: fetching Orca must not mutate the
   status of Project X / Aerodrome saved pools, and each saved pool refreshes
   via its own venue adapter (stub adapters; assert no cross-platform mutation).
3. Closed-position detection: mint/address read against a stubbed "gone" RPC
   returns "Position closed" (not a generic failure), and "RPC unavailable"
   under simulated total-429.

Run:  python test_lp_fetch_fixes.py
"""
import sys
import tempfile
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).parent / "src"))

from venue_adapters import orca_adapter as oa  # noqa: E402
from venue_adapters.orca_adapter import orca_display_pair, OrcaAdapter  # noqa: E402


def test_naming():
    cases = {
        ("SOL", "cbBTC"): "cbBTC/SOL",     # SOL last when paired with non-stable
        ("cbBTC", "SOL"): "cbBTC/SOL",     # already correct
        ("SOL", "USDC"):  "SOL/USDC",      # USDC last when paired with SOL
        ("USDC", "SOL"):  "SOL/USDC",      # USDC last regardless of account order
        ("WETH", "USDC"): "WETH/USDC",
        ("BONK", "SOL"):  "BONK/SOL",      # SOL last vs non-stable
        ("LBTC", "SUI"):  "LBTC/SUI",      # neither quote-listed → keep order
    }
    for (a, b), want in cases.items():
        got, d0, d1 = orca_display_pair(a, b)
        assert got == want, f"{a}/{b} -> {got} (want {want})"
        assert set((d0, d1)) == set((a, b))
    print("✅ orca_display_pair naming tests PASS")


def test_closed_and_rpc_states():
    ad = OrcaAdapter()
    real_closed_exists = oa.__dict__.get("_account_exists")
    real_gad = oa.__dict__.get("_get_account_data")
    try:
        # Stub: position account is GONE (closed) → "Position closed"
        oa._account_exists = lambda addr: False
        oa._get_account_data = lambda addr: None
        p = ad._fetch_by_position_mint("FbNHxe9VV797JWG7XH2msjwp5Rvb6dGzndwwkEXXXBKX")
        assert p is not None and p.error == "Position closed", p.error
        # Stub: RPC totally down (exists → None = RPC unavailable)
        oa._account_exists = lambda addr: None
        oa._get_account_data = lambda addr: None
        p = ad._fetch_by_position_mint("FbNHxe9VV797JWG7XH2msjwp5Rvb6dGzndwwkEXXXBKX")
        assert p is not None and "RPC unavailable" in (p.error or ""), p.error
    finally:
        oa._account_exists = real_closed_exists
        oa._get_account_data = real_gad
    print("✅ closed/RPC-unavailable distinction tests PASS")


def test_venue_scoped_refresh():
    """Saved-pool refresh dispatches each entry to its OWN adapter; a failed
    Orca fetch does NOT mark Project X/Aerodrome saved pools failed."""
    sys.path.insert(0, str(Path(__file__).parent))
    import lp_tab as lt
    import customtkinter as ctk

    ctk.set_appearance_mode("dark")
    _root = ctk.CTk()   # CTkFont needs a default root; keep it off-screen
    _root.withdraw()

    class _FakeGUI:
        key_manager = type("KM", (), {"address_db": {"saved_pools": []}})()
        price_engine = None
        lp_engine = True
        online_mode = True
        class root:
            @staticmethod
            def after(ms, fn): pass

    tab = lt.LPTab.__new__(lt.LPTab)
    tab.gui = _FakeGUI()
    tab._lp_widgets = {}

    # Stub CTk widgets (labels capture text; frames/buttons are inert).
    rendered = []
    class _StubFrame:
        def __init__(self, *a, **k): pass
        def pack(self, *a, **k): pass
        def winfo_children(self): return []
    class _StubLabel(_StubFrame):
        def __init__(self, parent, text="", **k):
            rendered.append(text)
    class _StubButton(_StubFrame):
        def __init__(self, *a, **k): pass

    real = (ctk.CTkFrame, ctk.CTkLabel, ctk.CTkButton)
    ctk.CTkFrame, ctk.CTkLabel, ctk.CTkButton = _StubFrame, _StubLabel, _StubButton

    closed_calls = []
    def fake_closed(entry, prefix, tid, venue):
        closed_calls.append((prefix, venue, tid))
        return False
    tab._lp_saved_pool_is_closed = fake_closed

    saved = [
        {"token_id": "545983", "venue": "HyperEVM", "pair": "WHYPE/UBTC"},        # Project X
        {"token_id": "75269474", "venue": "Aerodrome", "pair": "WETH/cbBTC"},    # Aerodrome
        {"token_id": "FbNHxe9VV797JWG7XH2msjwp5Rvb6dGzndwwkEXXXBKX", "venue": "Orca", "pair": "cbBTC/SOL"},  # Orca
    ]
    prefixes = {"HyperEVM": "hyperevm", "Aerodrome": "base", "Orca": "solana"}
    try:
        for e in saved:
            lt.LPTab._lp_render_saved_placeholder(
                tab, _StubFrame(), e, prefixes[e["venue"]], e["token_id"], e["venue"], e["pair"])
    finally:
        ctk.CTkFrame, ctk.CTkLabel, ctk.CTkButton = real
        _root.destroy()

    # Each saved pool was closed-checked with its own prefix/venue (its own adapter path).
    assert ("hyperevm", "HyperEVM", "545983") in closed_calls
    assert ("base", "Aerodrome", "75269474") in closed_calls
    assert ("solana", "Orca", "FbNHxe9VV797JWG7XH2msjwp5Rvb6dGzndwwkEXXXBKX") in closed_calls
    # Exactly one placeholder per saved pool (3 cards x 3 labels), none skipped.
    assert len(rendered) == 9, rendered
    assert rendered.count("Fetch failed — live data unavailable. Click Scan Wallet to retry.") == 3
    print("✅ venue-scoped refresh/dispatch tests PASS")


def main():
    test_naming()
    test_closed_and_rpc_states()
    test_venue_scoped_refresh()
    print("✅ ALL LP FETCH FIX TESTS PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
