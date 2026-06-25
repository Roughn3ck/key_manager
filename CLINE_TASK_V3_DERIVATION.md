# Cline Task: Key Manager v3 — BIP39 Mnemonic Derivation Engine + Enhanced Private Key Management

## Context

Key Manager is at v2.4 (GUI with add dialogs + headless signing agent). This is a v3 feature release implementing BIP39 mnemonic-to-address derivation and improving private key management UX.

## Versioning Protocol (from .clinerules)

Before starting v3 work:

1. **Back up all v2 files** to `backups/` with `_v2` suffix:
   - `src/gui_main_v2.py` → `backups/gui_main_v2.py`
   - `build_gui_v2.py` → `backups/build_gui_v2.py`
   - `README.md` → `backups/README_v2.md`
   - `STATUS.md` → `backups/STATUS_v2.md`
   
2. **Create v3 files** with `_v3` suffix for all new/modified working files:
   - `src/gui_main_v3.py` (copy of v2, extended)
   - `build_gui_v3.py` (copy of v2 build script, updated entry point)
   
3. **Never edit v2 files directly** — v2 is the preserved baseline
4. **Shared infrastructure** (`crypto_engine.py`, `backup_engine.py`, `main.py`) is extended backward-compatibly — both v2 and v3 must work with the same core
5. Update `.clinerules`, `README.md`, and `STATUS.md` after v3 is complete
6. Back up the current `USB_DEPLOYMENT/key_manager_gui.exe` to `backups/key_manager_gui_v2.exe` before building v3

## Feature 1: Derivation Engine (`src/derivation_engine.py` — NEW)

Create a new module that derives addresses and private keys from BIP39 mnemonics using standard HD wallet paths.

### Dependencies

Add to `requirements.txt`:
```
hdwallet
mnemonic
```

Both are pure-Python, lightweight libraries. `hdwallet` wraps BIP32/BIP44 derivation. `mnemonic` handles BIP39 seed generation. `pycryptodomex` (already installed) provides keccak256 for EVM address derivation.

### API

```python
class DerivationEngine:
    """Derives addresses and private keys from BIP39 mnemonics."""
    
    SUPPORTED_CHAINS = {
        "EVM (Ethereum / Arbitrum / Base)": {
            "path": "m/44'/60'/0'/0/0",
            "address_type": "evm",
        },
        "BTC Taproot (bc1p)": {
            "path": "m/86'/0'/0'/0/0",
            "address_type": "btc_taproot",
        },
        "BTC SegWit (bc1q)": {
            "path": "m/84'/0'/0'/0/0",
            "address_type": "btc_segwit",
        },
        "BTC Legacy (1...)": {
            "path": "m/44'/0'/0'/0/0",
            "address_type": "btc_legacy",
        },
        "SOL (Solana)": {
            "path": "m/44'/501'/0'/0'",
            "address_type": "solana",
        },
        "DASH (Dash)": {
            "path": "m/44'/5'/0'/0/0",
            "address_type": "dash",
        },
        "SUI (Sui)": {
            "path": "m/44'/784'/0'/0'/0'",
            "address_type": "sui",
        },
    }
    
    def derive_from_mnemonic(mnemonic: str, chain: str, path: str = None, account_index: int = 0, address_index: int = 0) -> dict:
        """
        Derive address + private key from a mnemonic for a given chain.
        
        Args:
            mnemonic: BIP39 12/24-word mnemonic phrase
            chain: Key from SUPPORTED_CHAINS (e.g. "EVM (Ethereum / Arbitrum / Base)")
            path: Override derivation path (optional, defaults to chain's standard path)
            account_index: Account index in the path (default 0)
            address_index: Address index in the path (default 0)
            
        Returns:
            {
                "chain": chain,
                "path": "m/44'/60'/0'/0/0",
                "address": "0x8958Bd96896De55bFe31b1A6Eb2B280ebE098509",
                "private_key": "0xabc123...",  # hex string
                "public_key": "0xdef456...",  # hex string (if available)
            }
        """
    
    def derive_multiple(mnemonic: str, chain: str, count: int = 5) -> list[dict]:
        """Derive multiple addresses from the same mnemonic+chain (indices 0..count-1)."""
    
    def derive_all_chains(mnemonic: str) -> dict[str, dict]:
        """Derive the default address for every supported chain from one mnemonic."""
    
    def validate_mnemonic(mnemonic: str) -> bool:
        """Validate a BIP39 mnemonic checksum."""
    
    def generate_mnemonic(strength: int = 256) -> str:
        """Generate a new BIP39 mnemonic (256 bits = 24 words)."""
```

### Implementation Notes

- Use `hdwallet` library for derivation. It supports multiple chains out of the box.
- For EVM addresses: derive private key → compute public key → keccak256(public_key)[12:] → prefix with "0x"
- For BTC addresses: use the appropriate script type (P2TR for Taproot, P2WPKH for SegWit, P2PKH for Legacy). The `hdwallet` library handles this.
- For Solana: use Ed25519 derivation. The `hdwallet` Solana support may require the `solders` or `solana` library. If `hdwallet` doesn't support Solana natively, use `solders` directly: `from solders.keypair import Keypair` with the derived seed.
- For SUI: similar to Solana (Ed25519). Use `pysui` if needed, or derive manually.
- **Validation is critical**: verify that the EVM address derived from a known mnemonic matches Ian Coleman's BIP39 tool output. Test mnemonic: use the standard test vector `abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon about` which should produce EVM address `0x9858EfD232B4033E47C90089cA2c2C6aB6e1Aef8` at `m/44'/60'/0'/0/0`.

## Feature 2: GUI Integration — "Derive from Mnemonic" Button

In `gui_main_v3.py`:

### 2a. Add "Derive Addresses" button in the Mnemonic section

When an account HAS a stored mnemonic, show a "Derive Addresses" button in the mnemonic section (below the mnemonic reveal area, same frame).

Clicking it opens a **Derivation Dialog**:

```
┌─────────────────────────────────────────────┐
│   Derive Addresses from Mnemonic            │
│                                             │
│   Account: KP Investments                   │
│                                             │
│   Chain: [EVM (Ethereum / Arbitrum / Base)▼]│
│                                             │
│   Derivation Path: m/44'/60'/0'/0/0        │
│   (auto-populates based on chain, editable) │
│                                             │
│   Index: [0]  (address index)               │
│                                             │
│   ┌───────────────────────────────────────┐ │
│   │ Derived Address:                       │ │
│   │ 0x8958Bd96896De55bFe31b1A6Eb2B280eb... │ │
│   │                                        │ │
│   │ Private Key (click to reveal):         │ │
│   │ ••••••••••••••••••••••••••••••         │ │
│   └───────────────────────────────────────┘ │
│                                             │
│   [Save Address to Account]  [Derive Another]  [Close]│
└─────────────────────────────────────────────┘
```

- **Chain dropdown**: uses `DerivationEngine.SUPPORTED_CHAINS` keys (same chain labels as the existing `CHAIN_OPTIONS` list in the GUI). Map the selected chain label to the derivation config.
- **Path field**: auto-populates when chain changes, but is editable for custom paths.
- **Index**: defaults to 0. Increment to derive multiple addresses from the same chain.
- **Save Address to Account**: calls `key_manager.add_address(account, coin_label, chain_label, derived_address, notes)` and optionally stores the derived private key via `key_manager.add_private_key(account, derived_private_key, password, chain_label)`.
- **Derive Another**: increments index, derives next address without closing dialog.

### 2b. Improve Private Key section — chain-aware display

Currently private keys show as:
```
[EVM (Ethereum / Arbitrum / Base)] Key: 0x____...
[DASH (Dash)] Key: X____...
```

Enhance to:
- Show the derivation path if known (stored alongside the key): `[EVM (Ethereum / Arbitrum / Base)] m/44'/60'/0'/0/0 | Key: 0x____...`
- Add a "Source" indicator: whether the key was manually entered or derived from mnemonic
- If the key was derived from a mnemonic that's stored in the same account, show a 🔗 link icon

### 2c. Private Key dialog enhancement — "Derive from Mnemonic" option

In the existing "Add Private Key" dialog (`show_add_private_key_dialog`), add a checkbox or toggle:

```
☑ Derive from Mnemonic
```

When checked:
- The private key input field becomes read-only
- A chain dropdown appears (same as derivation dialog)
- A path field appears (auto-populated)
- An index field appears
- A "Derive" button populates the private key field with the derived key
- The mnemonic is pulled from the account's stored mnemonic (if available)
- If no mnemonic is stored for the account, show a warning: "No mnemonic stored for this account. Add a mnemonic first."

When unchecked: the dialog behaves exactly as v2 (manual private key entry). This preserves backward compatibility for accounts like FIAT24 that only have a raw private key with no mnemonic.

### 2d. Mnemonic section — "Derive All Chains" shortcut

Add a secondary button next to "Derive Addresses":

```
[Derive Addresses]  [Derive All Chains]
```

"Derive All Chains" calls `DerivationEngine.derive_all_chains(mnemonic)` and shows a summary dialog with all derived addresses (EVM, BTC Taproot, BTC SegWit, SOL, DASH, SUI), each with a "Save to Account" button.

## Feature 3: CLI Support (optional, lower priority)

Extend `main.py` with new Click commands:

```bash
# Derive an address from a stored mnemonic
python src/main.py derive-address --account "KP Investments" --chain "EVM (Ethereum / Arbitrum / Base)" --index 0

# Generate a new mnemonic
python src/main.py generate-mnemonic --strength 256

# Validate a mnemonic
python src/main.py validate-mnemonic --account "KP Investments"
```

These commands use the same `DerivationEngine` and `KeyManager` classes.

## Data Schema Changes

The vault data structure needs to track derivation metadata. Extend the `private_keys` list format:

**Current (v2.2):**
```json
{
  "private_keys": {
    "KP Investments": [
      {"chain": "EVM (Ethereum / Arbitrum / Base)", "key": "0xabc..."}
    ]
  }
}
```

**Proposed (v3):**
```json
{
  "private_keys": {
    "KP Investments": [
      {
        "chain": "EVM (Ethereum / Arbitrum / Base)",
        "key": "0xabc...",
        "source": "derived",          // "manual" | "derived"
        "derivation_path": "m/44'/60'/0'/0/0",  // null if manual
        "address_index": 0,           // null if manual
        "derived_address": "0x8958...",  // the corresponding public address
      }
    ]
  }
}
```

This is **backward compatible**: existing entries without `source`, `derivation_path`, etc. are treated as `"source": "manual"` on load. The migration happens automatically when the vault is first opened in v3 — add the missing fields with defaults if not present.

Similarly, addresses can optionally store derivation metadata:

```json
{
  "accounts": {
    "KP Investments": {
      "addresses": [
        {
          "coin": "ETH",
          "chain": "EVM (Ethereum / Arbitrum / Base)",
          "address": "0x8958Bd96896De55bFe31b1A6Eb2B280ebE098509",
          "notes": "",
          "derivation_path": "m/44'/60'/0'/0/0",
          "derivation_index": 0,
          "source": "derived"
        }
      ]
    }
  }
}
```

## Testing Requirements

1. **Ian Coleman parity test**: derive the EVM address from mnemonic `abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon about` at path `m/44'/60'/0'/0/0`. Must produce `0x9858EfD232B4033E47C90089cA2c2C6aB6e1Aef8`. Add this as `test_derivation.py`.

2. **Round-trip test**: generate a mnemonic → derive address → store in vault → unlock vault → verify stored address matches derived.

3. **Multi-chain test**: derive addresses for all supported chains from the same test mnemonic. Verify no crashes and addresses look correct (correct prefix/format).

4. **Backward compatibility**: open an existing v2 vault in v3 without migration errors. Old private key entries (no `source` field) should display correctly.

5. **Manual key entry still works**: FIAT24 account with only a raw private key (no mnemonic) must still work exactly as v2.

## Security Rules (from .clinerules)

- Never log or print decrypted mnemonics or private keys in production code
- Derived private keys are stored in the encrypted vault — same AES-256-GCM protection as manual keys
- The derivation engine works in-memory only — no intermediate files
- Mnemonic is read from the vault (already decrypted in memory during session), passed to DerivationEngine, result is stored back encrypted
- On session lock: clear all in-memory derived keys, same as existing mnemonic clearing

## Build

After all features are complete and tested:
1. `python build_gui_v3.py` → produces `dist/key_manager_gui.exe`
2. Back up current `USB_DEPLOYMENT/key_manager_gui.exe` to `backups/key_manager_gui_v2.exe`
3. Copy new EXE to `USB_DEPLOYMENT/key_manager_gui.exe`
4. Update `STATUS.md` with v3 release notes
5. Update `README.md` with v3 features
6. Update `.clinerules` with v3 version info

## File Checklist

- [ ] `backups/gui_main_v2.py` (backup of v2 source)
- [ ] `backups/build_gui_v2.py` (backup of v2 build script)
- [ ] `backups/README_v2.md` (backup of v2 README)
- [ ] `backups/STATUS_v2.md` (backup of v2 STATUS)
- [ ] `backups/key_manager_gui_v2.exe` (backup of v2 EXE)
- [ ] `src/derivation_engine.py` (NEW — derivation logic)
- [ ] `src/gui_main_v3.py` (NEW — v3 GUI with derivation integration)
- [ ] `build_gui_v3.py` (NEW — v3 build script)
- [ ] `test_derivation.py` (NEW — Ian Coleman parity tests)
- [ ] `requirements.txt` (UPDATED — add hdwallet, mnemonic)
- [ ] `STATUS.md` (UPDATED — v3 release)
- [ ] `README.md` (UPDATED — v3 features)
- [ ] `.clinerules` (UPDATED — v3 version info)
- [ ] `src/main.py` (EXTENDED — add CLI derive commands, backward compatible)
- [ ] `USB_DEPLOYMENT/key_manager_gui.exe` (REBUILT — v3 EXE)