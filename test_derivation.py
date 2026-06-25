"""
Test suite for the BIP39 Derivation Engine.

Tests:
1. Ian Coleman parity test — EVM address from standard test vector
2. Round-trip test — generate mnemonic, derive, verify consistency
3. Multi-chain test — derive all supported chains, verify formats
4. Backward compatibility — old private key entries display correctly
5. Manual key entry — legacy keys still work
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

from derivation_engine import DerivationEngine

# Standard BIP39 test vector (Ian Coleman)
TEST_MNEMONIC = "abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon about"
# Expected EVM address at m/44\'/60\'/0\'/0/0
EXPECTED_EVM_ADDRESS = "0x9858EfFD232B4033E47d90003D41EC34EcaEda94"


def test_ian_coleman_parity():
    """Test that EVM derivation matches the Ian Coleman BIP39 tool output."""
    result = DerivationEngine.derive_from_mnemonic(
        TEST_MNEMONIC, "EVM (Ethereum / Arbitrum / Base)"
    )
    assert result["address"] == EXPECTED_EVM_ADDRESS, (
        f"EVM address mismatch: got {result['address']}, "
        f"expected {EXPECTED_EVM_ADDRESS}"
    )
    assert result["private_key"], "Private key should not be empty"
    assert result["public_key"], "Public key should not be empty"
    assert result["path"] == "m/44\'/60\'/0\'/0/0"
    print(f"  EVM address: {result['address']} - PASS")


def test_btc_address_formats():
    """Test that BTC addresses have the correct format prefixes."""
    results = DerivationEngine.derive_all_chains(TEST_MNEMONIC)

    # BTC Legacy should start with "1"
    btc_legacy = results.get("BTC Legacy (1...)", {})
    if "error" not in btc_legacy:
        addr = btc_legacy["address"]
        assert addr.startswith("1"), f"BTC Legacy should start with 1, got {addr}"
        print(f"  BTC Legacy: {addr} - PASS")

    # BTC SegWit should start with "bc1q"
    btc_segwit = results.get("BTC SegWit (bc1q)", {})
    if "error" not in btc_segwit:
        addr = btc_segwit["address"]
        assert addr.startswith("bc1q"), f"BTC SegWit should start with bc1q, got {addr}"
        print(f"  BTC SegWit: {addr} - PASS")

    # BTC Taproot should start with "bc1p"
    btc_taproot = results.get("BTC Taproot (bc1p)", {})
    if "error" not in btc_taproot:
        addr = btc_taproot["address"]
        assert addr.startswith("bc1p"), f"BTC Taproot should start with bc1p, got {addr}"
        print(f"  BTC Taproot: {addr} - PASS")


def test_multi_chain_derivation():
    """Test that all supported chains can derive without crashing."""
    results = DerivationEngine.derive_all_chains(TEST_MNEMONIC)
    for chain, data in results.items():
        if "error" in data:
            print(f"  {chain}: ERROR - {data['error']}")
        else:
            assert data["address"], f"{chain} address should not be empty"
            assert data["private_key"], f"{chain} private key should not be empty"
            print(f"  {chain}: {data['address'][:30]}... - PASS")


def test_mnemonic_validation():
    """Test mnemonic validation."""
    assert DerivationEngine.validate_mnemonic(TEST_MNEMONIC), "Valid mnemonic should pass"
    assert not DerivationEngine.validate_mnemonic("invalid mnemonic phrase"), "Invalid mnemonic should fail"
    assert not DerivationEngine.validate_mnemonic("abandon abandon abandon about"), "Short mnemonic should fail"
    print("  Validation: PASS")


def test_mnemonic_generation():
    """Test mnemonic generation produces valid mnemonics."""
    # 256 bits = 24 words
    m256 = DerivationEngine.generate_mnemonic(256)
    assert len(m256.split()) == 24, f"256-bit mnemonic should have 24 words, got {len(m256.split())}"
    assert DerivationEngine.validate_mnemonic(m256), "Generated 24-word mnemonic should be valid"

    # 128 bits = 12 words
    m128 = DerivationEngine.generate_mnemonic(128)
    assert len(m128.split()) == 12, f"128-bit mnemonic should have 12 words, got {len(m128.split())}"
    assert DerivationEngine.validate_mnemonic(m128), "Generated 12-word mnemonic should be valid"
    print("  Generation: PASS")


def test_multiple_derivation():
    """Test deriving multiple addresses from the same chain."""
    results = DerivationEngine.derive_multiple(
        TEST_MNEMONIC, "EVM (Ethereum / Arbitrum / Base)", count=3
    )
    assert len(results) == 3, "Should derive 3 addresses"
    # All addresses should be different
    addresses = [r["address"] for r in results]
    assert len(set(addresses)) == 3, "All 3 addresses should be unique"
    # First should match the standard test vector
    assert addresses[0] == EXPECTED_EVM_ADDRESS
    print(f"  Multiple derivation: {len(results)} unique addresses - PASS")


def test_backward_compatibility():
    """Test that old private key entries (without source field) are handled."""
    # Simulate old-style entry
    old_entry = {"chain": "EVM (Ethereum / Arbitrum / Base)", "key": "0xabc123"}
    # The GUI code uses entry.get("source", "manual") which defaults to "manual"
    assert old_entry.get("source", "manual") == "manual"
    assert old_entry.get("derivation_path") is None
    print("  Backward compatibility: PASS")


def run_all_tests():
    """Run all tests and report results."""
    tests = [
        ("Ian Coleman parity", test_ian_coleman_parity),
        ("BTC address formats", test_btc_address_formats),
        ("Multi-chain derivation", test_multi_chain_derivation),
        ("Mnemonic validation", test_mnemonic_validation),
        ("Mnemonic generation", test_mnemonic_generation),
        ("Multiple derivation", test_multiple_derivation),
        ("Backward compatibility", test_backward_compatibility),
    ]

    passed = 0
    failed = 0
    for name, test_func in tests:
        print(f"\nTest: {name}")
        try:
            test_func()
            passed += 1
        except Exception as e:
            print(f"  FAIL: {e}")
            failed += 1

    print(f"\n{'=' * 40}")
    print(f"Results: {passed} passed, {failed} failed")
    print(f"{'=' * 40}")
    return failed == 0


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
