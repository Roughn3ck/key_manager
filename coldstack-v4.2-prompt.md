# Coldstack v4.2 — Customizable RPC Endpoints

## Overview

Move from hardcoded RPC URLs in `balance_engine.py` to a user-editable JSON config file. Add a "Standard / Advanced" mode toggle in Settings. Advanced mode exposes RPC endpoint configuration and API key management.

## Design Decisions (from CFO)

### 1. Config File Location
`rpc_endpoints.json` lives in the same directory as `coldstack.exe` and `key_vault.encrypted` — the app's runtime directory. This is the portable model: everything co-located whether on USB or in a workspace folder.

### 2. What Goes Where — Security Boundary

| Data | Storage | Encrypted? | Reason |
|------|---------|------------|--------|
| Public RPC URLs | `rpc_endpoints.json` | No | Public info, safe unencrypted |
| API keys (Helius, etc.) | `key_vault.encrypted` → `config.api_keys` | Yes | Secrets — must be password-protected |
| Mnemonics, private keys | `key_vault.encrypted` | Yes | Secrets — existing behavior |
| Online mode, currency | `key_vault.encrypted` → `config` | Yes | Existing behavior, unchanged |

**Rationale:** Public RPC URLs (like `https://eth.llamarpc.com`) are not secrets. But API keys (like a Helius API key) ARE secrets and must stay in the encrypted vault. The unencrypted JSON file contains ONLY public URLs — no keys, no tokens, no secrets.

### 3. Schema Versioning — Backward Compatible

The encrypted vault's internal data (`address_db`) gets a `schema_version` field added to `config`. This is additive — old vaults without it default to `1` (v4.1 behavior). New vaults get `schema_version: 2`.

```
address_db = {
    "accounts": [...],
    "pools": [...],
    "mnemonics": [...],
    "private_keys": [...],
    "config": {
        "schema_version": 2,          # NEW — defaults to 1 if missing
        "online_mode": false,
        "display_currency": "none",
        "app_mode": "standard",       # NEW — "standard" or "advanced"
        "api_keys": {                 # NEW — only present in advanced mode
            "helius": "",
            "infura": "",
            "alchemy": "",
            "quicknode": ""
        }
    }
}
```

**Backward compatibility rules:**
- Old vaults (no `schema_version`) → treated as v1, all features work
- New field `app_mode` defaults to `"standard"` if missing
- New field `api_keys` defaults to empty dict if missing
- `rpc_endpoints.json` missing → fall back to hardcoded defaults (current behavior)
- NEVER break old vaults. Additive changes only.

### 4. Standard vs Advanced Mode

**Standard mode (default):**
- Identical to v4.1 UX
- Uses hardcoded default RPC endpoints (or `rpc_endpoints.json` if present)
- No RPC configuration UI visible
- No API key fields visible
- Settings dialog shows: Go Online toggle, Currency selector, "Switch to Advanced" button

**Advanced mode:**
- All Standard features PLUS:
- RPC endpoint editor in Settings (table of chain → URL)
- API key fields for services that need them (Helius, Infura, Alchemy, Quicknode)
- "Reset to Defaults" button for RPC endpoints
- "Switch to Standard" button to hide advanced features
- Balance engine uses custom RPC URLs + API keys from config

**Mode switching:**
- Standard → Advanced: Show a brief explanation dialog ("Advanced mode unlocks custom RPC endpoints and API key configuration. Your existing data is safe.")
- Advanced → Standard: Hide advanced UI. Custom RPC URLs in `rpc_endpoints.json` are preserved but not editable. API keys remain in vault but are not displayed.
- Mode persists in encrypted vault config

### 5. API Key Handling

For now, API keys are stored in the encrypted vault config. They are NOT in `rpc_endpoints.json`.

When a chain has an API key configured, the balance engine uses it:
- **EVM chains:** Add `?api_key=KEY` to URL or use `Authorization: Bearer KEY` header
- **Solana (Helius):** `https://mainnet.helius-rpc.com/?api-key=KEY` replaces the default public endpoint
- **Others:** Pattern depends on the provider

The `rpc_endpoints.json` file can include a `headers` or `auth` field that references which API key to use, but the key VALUE is never written to the unencrypted file — only a reference like `"auth": "helius"` which tells the engine to look up `config.api_keys.helius`.

### 6. rpc_endpoints.json Format

```json
{
  "version": 1,
  "updated": "2026-07-04",
  "endpoints": {
    "ethereum": {
      "url": "https://eth.llamarpc.com",
      "auth": null,
      "fallback": "https://rpc.ankr.com/eth"
    },
    "arbitrum": {
      "url": "https://arb1.arbitrum.io/rpc",
      "auth": null,
      "fallback": "https://rpc.ankr.com/arbitrum"
    },
    "base": {
      "url": "https://mainnet.base.org",
      "auth": null,
      "fallback": "https://base.llamarpc.com"
    },
    "bsc": {
      "url": "https://bsc-dataseed.binance.org",
      "auth": null,
      "fallback": "https://bsc-dataseed1.binance.org"
    },
    "polygon": {
      "url": "https://polygon-rpc.com",
      "auth": null,
      "fallback": "https://rpc.ankr.com/polygon"
    },
    "optimism": {
      "url": "https://mainnet.optimism.io",
      "auth": null,
      "fallback": "https://rpc.ankr.com/optimism"
    },
    "hyperliquid_evm": {
      "url": "https://rpc.hyperliquid.xyz/evm",
      "auth": null,
      "fallback": null
    },
    "bitcoin": {
      "url": "https://blockstream.info/api/address/{address}",
      "auth": null,
      "fallback": "https://mempool.space/api/address/{address}"
    },
    "solana": {
      "url": "https://api.mainnet-beta.solana.com",
      "auth": "helius",
      "fallback": "https://solana-api.projectserum.com"
    },
    "dash": {
      "url": "https://insight.dash.org/insight-api/addr/{address}",
      "auth": null,
      "fallback": null
    },
    "sui": {
      "url": "https://fullnode.mainnet.sui.io",
      "auth": null,
      "fallback": null
    },
    "hyperliquid_l1": {
      "url": "https://api.hyperliquid.xyz/info",
      "auth": null,
      "fallback": null
    },
    "zcash": {
      "url": "https://api.blockchair.com/zcash/dashboards/address/{address}",
      "auth": null,
      "fallback": null
    },
    "ripple": {
      "url": "https://s1.ripple.com:51234",
      "auth": null,
      "fallback": "https://s2.ripple.com:51234"
    },
    "cardano": {
      "url": "https://api.koios.rest/api/v1/address_info",
      "auth": null,
      "fallback": null
    },
    "cosmos": {
      "url": "https://rest.lavenderfive.com:443/cosmoshub/cosmos/bank/v1beta1/balances/{address}",
      "auth": null,
      "fallback": null
    },
    "secret": {
      "url": "https://rest.lavenderfive.com:443/secretnetwork/cosmos/bank/v1beta1/balances/{address}",
      "auth": null,
      "fallback": null
    },
    "thorchain": {
      "url": "https://thornode.thorchain.ninja/cosmos/bank/v1beta1/balances/{address}",
      "auth": null,
      "fallback": null
    }
  }
}
```

## Technical Implementation

### Files to Create
1. **`rpc_endpoints.json`** — Default config file in repo root (shipped with distro)
2. **`src/rpc_config.py`** (NEW) — RPC config loader: reads JSON, merges with API keys from vault, provides unified endpoint dict to BalanceEngine

### Files to Modify
1. **`src/balance_engine.py`** — Refactor module-level constants into instance attributes. Accept full endpoint config dict (URL + auth + fallback per chain). Use fallback on failure.
2. **`src/gui_main_v4.py`** — Add `app_mode` to config. Add Advanced Settings panel with RPC editor + API key fields. Pass RPC config to BalanceEngine. Add mode switch button.
3. **`build_gui_v4.py`** — Add `rpc_endpoints.json` as a data file in PyInstaller build
4. **`.clinerules`** — Update versioning table, project structure
5. **`README.md`** — Document v4.2 features
6. **`STATUS.md`** — Add v4.2 changelog

### BalanceEngine Changes

The engine currently uses module-level constants (`DEFAULT_RPC_ENDPOINTS`, `BTC_API`, `SOLANA_RPC`, etc.) and only accepts EVM RPC overrides. v4.2 needs:

```python
class BalanceEngine:
    def __init__(self, rpc_config: Optional[Dict] = None, api_keys: Optional[Dict] = None):
        """
        Args:
            rpc_config: Full endpoint config from rpc_endpoints.json.
                        Format: {chain_id: {url, auth, fallback}}
            api_keys: API keys from vault config.
                      Format: {provider: "key_string"}
        """
        self.rpc_config = rpc_config or DEFAULT_RPC_CONFIG
        self.api_keys = api_keys or {}
    
    def _get_url(self, chain_id: str) -> Optional[str]:
        """Get the effective URL for a chain, with API key injected if configured."""
        endpoint = self.rpc_config.get(chain_id, {})
        url = endpoint.get("url")
        if not url:
            return None
        auth_provider = endpoint.get("auth")
        if auth_provider and auth_provider in self.api_keys:
            key = self.api_keys[auth_provider]
            # Inject API key based on provider pattern
            if auth_provider == "helius":
                url = url.replace("api.mainnet-beta.solana.com", "mainnet.helius-rpc.com")
                url += f"?api-key={key}"
            # Add more provider patterns as needed
        return url
    
    def _get_fallback(self, chain_id: str) -> Optional[str]:
        """Get fallback URL for a chain."""
        endpoint = self.rpc_config.get(chain_id, {})
        return endpoint.get("fallback")
```

All the non-EVM methods (`fetch_btc_balance`, `fetch_solana_balance`, etc.) need to use `self._get_url()` instead of module-level constants.

### GUI Changes — Settings Dialog

The Settings dialog grows from ~400px to ~600px height. New sections:

**Standard Mode Settings (existing):**
- Go Online toggle
- Currency selector
- [Switch to Advanced] button

**Advanced Mode Settings (new, shown when app_mode == "advanced"):**
- Go Online toggle (same)
- Currency selector (same)
- ─── RPC Endpoints ───
- Scrollable list of chain → URL entries (editable)
- Per-chain "Reset to Default" button
- "Reset All to Defaults" button at bottom
- ─── API Keys ───
- Helius API Key field (masked, show/hide toggle)
- Infura API Key field
- Alchemy API Key field
- Quicknode API Key field
- [Switch to Standard] button

### Backward Compatibility Checklist

- [ ] Old vaults (no `schema_version`) open without migration
- [ ] Old vaults (no `app_mode`) default to "standard"
- [ ] Old vaults (no `api_keys`) default to empty
- [ ] `rpc_endpoints.json` missing → use hardcoded defaults
- [ ] `rpc_endpoints.json` malformed → log warning, use hardcoded defaults
- [ ] Standard mode: no RPC UI, no API key UI, uses defaults or existing config file
- [ ] Advanced mode: all features available
- [ ] Switching modes doesn't lose data
- [ ] Existing v4.1 EXE behavior unchanged when config file absent

### Build Changes

In `build_gui_v4.py`, add `rpc_endpoints.json` as a data file:

```python
'--add-data=rpc_endpoints.json:.',
```

This bundles the default config into the EXE. At runtime, the app checks for `rpc_endpoints.json` in the EXE directory first (user-customized), falls back to the bundled default.

### Testing Checklist

- [ ] Fresh install: no `rpc_endpoints.json` → uses hardcoded defaults, works
- [ ] Fresh install: create vault → `schema_version: 2` in config
- [ ] Old v4.1 vault: opens without migration, `schema_version` defaults to 1
- [ ] Standard mode: Settings shows only Go Online + Currency + "Switch to Advanced"
- [ ] Advanced mode: Settings shows RPC editor + API keys
- [ ] Edit RPC URL → balance fetch uses new URL
- [ ] Add Helius API key → Solana balance uses Helius
- [ ] "Reset to Defaults" restores original URLs
- [ ] Switch Advanced → Standard: RPC editor hidden, custom URLs preserved
- [ ] Switch Standard → Advanced: RPC editor shows current URLs
- [ ] Malformed `rpc_endpoints.json` → graceful fallback, no crash
- [ ] Build EXE: `python build_gui_v4.py` succeeds
- [ ] EXE runs on clean Windows (no Python)
- [ ] `rpc_endpoints.json` editable in notepad, changes picked up on next balance fetch

## Important Notes

1. **Don't change the crypto engine.** `crypto_engine.py` is shared and stable. The vault encryption envelope (`version: "1.0"`) is about the encryption format, not the data schema. Don't touch it.

2. **Don't change the derivation engine.** `derivation_engine.py` is unrelated to this feature.

3. **The `config` key in `address_db` already exists** (added in v4.1 for `online_mode` and `display_currency`). We're adding fields to it, not creating a new structure.

4. **API keys are stored in the encrypted vault, not in `rpc_endpoints.json`.** The JSON file only contains a reference like `"auth": "helius"` — the actual key value is looked up from the vault at runtime.

5. **The `rpc_endpoints.json` in the repo root is the DEFAULT.** Users can edit their local copy. The repo copy is the shipped default. Git-track the default, not user customizations.

6. **After ANY source change, rebuild the EXE.** `python build_gui_v4.py` from repo root. Test the EXE, not just the script.

7. **Preserve all existing files.** Create backups before modifying. Previous version files (`gui_main_v3_1.py`, etc.) are left untouched.

## Reference: Current State

- **Repo:** `B:\Blockchain\coldstack\`
- **Balance engine:** `src/balance_engine.py` — `DEFAULT_RPC_ENDPOINTS` dict (line 16), module-level constants for non-EVM chains (lines 27-37), `BalanceEngine.__init__` accepts `rpc_endpoints` dict (line 123)
- **GUI:** `src/gui_main_v4.py` — `BalanceEngine()` with no args (line 269), `_load_vault_config()` (line 2741), `_save_vault_config()` (line 2747), Settings dialog (line 2763)
- **Build:** `build_gui_v4.py` — PyInstaller onefile, outputs to `USB_DEPLOYMENT/coldstack.exe`
- **Vault schema:** `address_db.config` has `online_mode` and `display_currency`. No `schema_version` yet.
- **CFO's RPC registry:** `B:\OpenClaw\.openclaw\workspace\kimi\rpc-registry.json` — master reference for all endpoints
