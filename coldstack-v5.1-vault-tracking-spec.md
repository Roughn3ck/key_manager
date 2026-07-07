# ColdStack v5.1 Feature Spec — Hyperliquid Vault Tracking

**Author:** Slater (CTO)  
**Date:** 6 July 2026  
**Status:** ✅ Approved by Kimi (CFO) — ready for Cline  
**Version target:** ColdStack v5.1 (from v4.2/v5.0 baseline)  
**Repo:** `B:\Blockchain\coldstack\`  

---

## 1. Purpose & Scope

Add Hyperliquid vault positions to the ColdStack portfolio view.

- Vault positions are **not token balances**. They are deposits into Hyperliquid vault smart contracts that issue vault shares.
- A user should see their vault deposits, current value, unrealized P&L, and APR in the same dashboard as their token holdings and LP positions.
- This is a **feature addition**, not a rewrite. We extend the existing `balance_engine.py` / `lp_engine.py` patterns with a dedicated read-only Hyperliquid-specific vault tracker.
- **Out of scope for v5.1:** vault deposits, withdrawals, rebalancing, or any signed write action. This release is read-only.
- **No ABC / generic vault interface for v5.1** per product-owner decision; keep it Hyperliquid-specific.

---

## 2. Product Requirements

A user pastes (or has pre-saved) a Hyperliquid wallet address. ColdStack should display:

| Field | Required | Source / Notes |
|-------|----------|----------------|
| Vault name | ✅ | From `userVaultEquities` / `vaultDetails` (e.g., "HLP", "Growi HF", "Orbit Value Strategies"). |
| Deposited amount (USDC) | ✅ | `equity` field (string → float) from `userVaultEquities`. |
| Current value (USDC) | ✅ | `currentValue` field (string → float) from `userVaultEquities`. |
| Unrealized P&L (USDC + %) | ✅ | `unrealizedPnl` / `pnlPercent` from `userVaultEquities`; or computed as `currentValue - equity`. |
| Vault shares balance | ✅ | Number of vault shares issued to the user. |
| Share price (USDC) | ✅ | `vaultTotalValue / vaultTotalShares` from `vaultDetails`. |
| APR / APY | ✅ | From `vaultDetails.portfolio` if available; otherwise compute from `vaultDetails`. |
| Deposit history | ✅ | List of deposit/withdraw events. v5.1 shows summary only (count, total in, total out). Full history can wait for tax module. |
| Last updated | ✅ | Timestamp of the API call. |

Display location:
- A new section on the **Vault / Portfolio tab** (current v4.2/v5.0 "Vault" tab) called **"Hyperliquid Vaults"**.
- Rendered as cards, similar to existing address balance cards and LP position cards.
- Each card shows: vault name, deposited, current value, P&L, APR, share balance, share price.

---

## 3. Data Sources

### 3.1 Endpoint: `userVaultEquities`

- **URL:** `https://api.hyperliquid.xyz/info`
- **Method:** `POST`
- **Payload:**
  ```json
  {
    "type": "userVaultEquities",
    "user": "0x..."
  }
  ```
- **What it returns:** Array of the user's vault equity positions.
- **Known fields (confirmed by nktkas GitBook, Dwellir, QuickNode, Chainstack):**
  - `vaultAddress` — vault contract address
  - `vaultName` — human readable name
  - `shares` — user's vault share balance (string)
  - `equity` — **deposited amount in USDC** (string)
  - `currentValue` — current value of user's position in USDC (string)
  - `unrealizedPnl` — unrealized profit/loss in USDC (string)
  - `pnlPercent` — unrealized P&L as a percentage (string)
- **All values are strings** — parse with `float()`.
- **Important:** `equity` is the deposited amount, not current value. `currentValue` is the live value.

> **Note:** Field names confirmed via nktkas GitBook, Dwellir, QuickNode, and Chainstack docs. See `slater/memory/hyperliquid-vault-api-shape.md` for the full reference. The test wallet `0xbf0e7d5868479b3b2602fa929dec6661408edc71` has no vault positions yet (returns `[]`), so the first real API response will come when capital is deployed. Implementation must still be defensive: parse strings as floats, handle missing keys gracefully.

### 3.2 Endpoint: `vaultDetails`

- **URL:** `https://api.hyperliquid.xyz/info`
- **Method:** `POST`
- **Payload:**
  ```json
  {
    "type": "vaultDetails",
    "vaultAddress": "0x...",
    "user": "0x..."
  }
  ```
- **What it returns:** Comprehensive vault metadata + performance history.
- **Use for:**
  - Vault total value
  - Total vault shares
  - Share price computation
  - APR / APY / portfolio history
  - Vault leader / strategy metadata
- **Known response shape (from QuickNode / Chainstack docs):**
  - `portfolio` — array of timeframes with `accountValue`, `pnl`, `liquidity`
  - `leader` / `follower` data
  - User-specific follower info: deposited amount, share balance, personal returns

### 3.3 Supporting prices

- Vault values are already denominated in USDC by Hyperliquid.
- No additional price-engine conversion is needed for vault-level USD values.
- If the UI wants to convert total portfolio value to AUD/CAD/etc., reuse the existing fiat conversion path in `price_engine.py`.

---

## 4. Architecture Approach

### 4.1 Design principles (same as existing codebase)

1. **Read-only.** No signing, no deposits, no withdrawals.
2. **Offline by default.** Methods must raise / return an empty state when `online_mode=False`.
3. **No new external dependencies.** Use `urllib.request` + `json`, exactly like `balance_engine.py` and `hyperliquid_adapter.py`.
4. **One module per concern.** Create a new `vault_tracker.py` module rather than stuffing logic into `balance_engine.py`.
5. **Defensive parsing.** Hyperliquid field names are not always stable across providers; try aliases and never crash on missing keys.
6. **Match existing card UI.** Reuse the same CustomTkinter card rendering used for address balances and LP positions.

### 4.2 Module placement

```
coldstack/src/
├── balance_engine.py          # existing (wallet balances)
├── lp_engine.py               # existing (LP positions)
├── price_engine.py            # existing
├── gui_main_v5.py             # existing (v5.0 LP tab + Vault tab)
├── vault_tracker.py           # NEW — core vault data model + fetcher
├── venue_adapters/
│   ├── __init__.py
│   └── hyperliquid_adapter.py # existing
└── tests/
    └── test_vault_tracker.py  # NEW (optional, Cline can skip if time-constrained)
```

### 4.3 Why a new module instead of extending `balance_engine.py`

- `balance_engine.py` is already large (48 KB) and owns EVM/non-EVM RPC balance fetching.
- Vault shares are not ERC-20 token balances; they are off-exchange accounting entries tracked by Hyperliquid's L1 API.
- The data model is different: shares, NAV, deposits, P&L, APR.
- A separate module keeps vault logic testable and avoids polluting the balance-engine cache.
- Future v5.2+ venues (Krystal, Beefy, Yearn) can follow the same `vault_tracker.py` pattern.

### 4.4 How it integrates with the portfolio view

```
┌─────────────────────────────────────────────┐
│  ColdStack GUI (gui_main_v5.py)             │
│  Vault tab                                  │
│  ├── Wallet balances (balance_engine)     │
│  ├── LP Positions (lp_engine)               │
│  └── Hyperliquid Vaults (vault_tracker)  NEW│
└────────────────┬────────────────────────────┘
                 │ constructs
                 ▼
┌─────────────────────────────────────────────┐
│  vault_tracker.py                             │
│  HyperliquidVaultTracker                      │
└────────────────┬────────────────────────────┘
                 │ POST /info
                 ▼
┌─────────────────────────────────────────────┐
│  https://api.hyperliquid.xyz/info             │
│  userVaultEquities + vaultDetails             │
└─────────────────────────────────────────────┘
```

---

## 5. Proposed Data Model

### 5.1 `VaultPosition` dataclass

```python
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional


@dataclass
class VaultPosition:
    """Standardized view of a user's position in a single Hyperliquid vault."""

    position_id: str                 # e.g. "hyperliquid:<vault_address>"
    vault_address: str               # 0x... vault contract
    vault_name: str                  # Human readable name
    chain: str = "Hyperliquid"
    venue: str = "Hyperliquid Vault"

    # Share data
    shares: Optional[float] = None
    share_price_usd: Optional[float] = None
    total_vault_shares: Optional[float] = None

    # Value data
    deposited_usd: Optional[float] = None
    current_value_usd: Optional[float] = None
    unrealized_pnl_usd: Optional[float] = None
    unrealized_pnl_pct: Optional[float] = None

    # Yield / performance
    apr: Optional[float] = None      # annual percentage rate, if provided
    apy: Optional[float] = None      # annual percentage yield, if provided
    performance_history: List[Dict] = field(default_factory=list)

    # Deposit / withdrawal summary
    deposit_count: int = 0
    withdrawal_count: int = 0
    total_deposited_usd: Optional[float] = None
    total_withdrawn_usd: Optional[float] = None

    # Metadata
    vault_leader: Optional[str] = None
    vault_description: Optional[str] = None
    raw_data: Optional[Dict] = None
    error: Optional[str] = None
    last_updated: str = ""

    def __post_init__(self):
        if not self.last_updated:
            self.last_updated = datetime.now(timezone.utc).isoformat()
```

### 5.2 `HyperliquidVaultTracker` class

```python
class HyperliquidVaultTracker:
    """Read-only tracker for Hyperliquid vault positions."""

    INFO_URL = "https://api.hyperliquid.xyz/info"
    TIMEOUT = 15

    def __init__(self, online_mode: bool = False):
        self.online_mode = online_mode

    def fetch_positions(self, wallet_address: str) -> List[VaultPosition]:
        """Return all vault positions for a wallet."""
        if not self.online_mode:
            return []

        # 1. userVaultEquities: list of user's vaults
        equities = self._post({"type": "userVaultEquities", "user": wallet_address})
        if not isinstance(equities, list):
            return []

        positions = []
        for equity in equities:
            vault_address = self._extract_vault_address(equity)
            if not vault_address:
                continue

            # 2. vaultDetails: enrich with performance, share price, APR
            details = self._post({
                "type": "vaultDetails",
                "vaultAddress": vault_address,
                "user": wallet_address,
            })

            positions.append(self._build_position(equity, details, wallet_address))

        return positions

    def fetch_single_position(
        self, wallet_address: str, vault_address: str
    ) -> Optional[VaultPosition]:
        """Return a single vault position."""
        if not self.online_mode:
            return None
        details = self._post({
            "type": "vaultDetails",
            "vaultAddress": vault_address,
            "user": wallet_address,
        })
        return self._build_position({}, details, wallet_address)

    def _post(self, payload: Dict) -> Any: ...
    def _build_position(self, equity: Dict, details: Dict, wallet: str) -> VaultPosition:
        """Build a VaultPosition from userVaultEquities + vaultDetails."""
        vault_address = self._extract_vault_address(equity)
        if not vault_address and isinstance(details, dict):
            vault_address = self._extract_vault_address(details)
        if not vault_address:
            vault_address = wallet

        pos = VaultPosition(
            position_id=f"hyperliquid:{vault_address}",
            vault_address=vault_address,
            vault_name=self._get_str(equity, ["vaultName", "name"], default=vault_address),
            shares=self._get_float(equity, ["shares", "shareBalance"], default=None),
            deposited_usd=self._get_float(equity, ["equity"], default=None),
            current_value_usd=self._get_float(equity, ["currentValue"], default=None),
            unrealized_pnl_usd=self._get_float(equity, ["unrealizedPnl", "pnl"], default=None),
            unrealized_pnl_pct=self._get_float(equity, ["pnlPercent", "pnlPct"], default=None),
            raw_data={"equity": equity, "details": details},
        )

        # Enrich from vaultDetails when available
        if isinstance(details, dict):
            pos.vault_leader = self._get_str(details, ["leader", "leaderAddress", "manager"], default=None)
            pos.total_vault_shares = self._get_float(details, ["totalShares", "shares", "totalSupply"], default=None)
            pos.performance_history = self._get_list(details, ["portfolio"], default=[])
            pos.apr = self._compute_apr(pos.performance_history)

            # Share price: prefer vault-level, fallback to personal
            if pos.shares and pos.current_value_usd and pos.shares > 0:
                pos.share_price_usd = pos.current_value_usd / pos.shares
            elif pos.total_vault_shares:
                total_value = self._get_float(details, ["totalValue", "equity", "vaultEquity"], default=None)
                if total_value and pos.total_vault_shares > 0:
                    pos.share_price_usd = total_value / pos.total_vault_shares

            # Fallback P&L if not provided by userVaultEquities
            if pos.unrealized_pnl_usd is None and pos.current_value_usd is not None and pos.deposited_usd is not None:
                pos.unrealized_pnl_usd = pos.current_value_usd - pos.deposited_usd
            if pos.unrealized_pnl_pct is None and pos.unrealized_pnl_usd is not None and pos.deposited_usd:
                pos.unrealized_pnl_pct = (pos.unrealized_pnl_usd / pos.deposited_usd) * 100.0

        return pos
```

---

## 6. API Field Mapping (defensive)

Because Hyperliquid field names vary slightly between docs/providers, the implementation must try a prioritized list of aliases.

### 6.1 `userVaultEquities` field aliases (CONFIRMED)

| Concept | Primary key | Fallback keys |
|---------|-------------|---------------|
| Vault address | `vaultAddress` ✅ | `vault`, `address` |
| Vault name | `vaultName` ✅ | `name`, `vaultId`, `vaultAddress` (truncated) |
| User shares | `shares` ✅ | `shareBalance`, `vaultShares`, `sharesBalance` |
| Current value | `currentValue` ✅ | `equity`, `value`, `totalValue` |
| Deposited amount | `equity` ✅ | `deposited`, `depositedAmount`, `totalDeposited`, `principal` |
| Unrealized P&L USD | `unrealizedPnl` ✅ | `pnl`, `profit`, `unrealizedProfit` |
| Unrealized P&L % | `pnlPercent` ✅ | `unrealizedPnlPercent`, `roi`, `returnPercent` |

**CRITICAL:** All numeric values are strings from the API. Parse with `float()`. The `equity` field in `userVaultEquities` is the user's deposited amount (not current value — that's `currentValue`).

### 6.2 `vaultDetails` field aliases (CONFIRMED)

| Concept | Primary key | Fallback keys |
|---------|-------------|---------------|
| Vault total value | `portfolio[].accountValue` ✅ | `totalValue`, `equity`, `vaultEquity` |
| Total vault shares | `totalShares` | `shares`, `totalSupply`, `vaultShares` |
| APR | **COMPUTED** from portfolio history | Not a direct field — calculate from `accountValue` change over time. If insufficient data, show "APR: see Hyperliquid". Do NOT fabricate. |
| APY | **COMPUTED** | Same as APR — compound from portfolio returns. If insufficient data, leave `None`. |
| Portfolio history | `portfolio` ✅ | `performance`, `history`, `returns` |
| Leader name | `leader` ✅ | `leaderAddress`, `manager` |
| User equity | `follower.equity` ✅ | `followerEquity`, `userEquity` |
| User shares | `follower.shares` ✅ | `followerShares`, `userShares` |
| User P&L | `follower.pnl` ✅ | `followerPnl`, `userPnl` |

### 6.3 Computed fields

- `share_price_usd` = `current_value_usd / shares` if both present; else `vault_total_value / vault_total_shares` from `vaultDetails`; else `None`.
- `unrealized_pnl_usd` = `unrealizedPnl` from API (preferred, already computed by Hyperliquid); fallback: `current_value_usd - deposited_usd`.
- `unrealized_pnl_pct` = `pnlPercent` from API (preferred); fallback: `(pnl / deposited) * 100` if deposited > 0.
- `apr` = COMPUTED from `vaultDetails.portfolio` — take the most recent timeframe's `accountValue` change, annualize it. If the portfolio array does not contain enough data points or timestamps, set `apr = None` and the UI must show "APR: see Hyperliquid". Do NOT fabricate APR from single-point P&L.
- `apy` = Leave `None` for v5.1; same computation path as APR if data exists, otherwise show nothing.

---

## 7. GUI Integration

### 7.1 Where to show it

- Add a new section titled **"Hyperliquid Vaults"** inside the existing **Vault tab** (the first/main tab), below wallet balances.
- Alternatively, if the Vault tab is too crowded, add a dedicated **"Vaults"** tab next to **LP Positions**.
  - **Recommendation:** start with a section inside the Vault tab to keep v5.1 small. If the user has many vaults, use a scrollable card list.

### 7.2 Card layout

```
┌────────────────────────────────────────────────────────────┐
│ 🏛️ HLP  ·  Hyperliquid Vault                                │
│ Deposited: $10,000.00  ·  Current: $10,450.00              │
│ P&L: +$450.00 (+4.50%)  ·  APR: 12.4%                      │
│ Shares: 9,847.31  ·  Share price: $1.0612                 │
│ [Refresh]  [Copy address]  [View raw]                      │
└────────────────────────────────────────────────────────────┘
```

### 7.3 Controls

- **Refresh button** per-section, disabled when `online_mode=False`.
- **Copy vault address** button per card.
- **View raw** button per card (optional for v5.1; can reuse LP card's "View Raw" pattern if it exists).

### 7.4 Threading

- Network calls run in a daemon thread; results posted back with `self.root.after(0, callback)`, same pattern as `_lp_do_fetch`.

### 7.5 Offline state

- When `online_mode=False`, show: "🔒 Offline — enable Online Mode in Settings to fetch Hyperliquid vaults."

---

## 8. Cline Implementation Prompt (copy/paste ready)

> **Context:** You are implementing ColdStack v5.1. The repo is at `B:\Blockchain\coldstack\`. Current baseline is v4.2 balance engine + v5.0 LP engine. Use only the existing patterns. API field names have been confirmed by Kimi via nktkas GitBook, Dwellir, and QuickNode docs.

```
Implement Hyperliquid vault tracking for ColdStack v5.1.

Goal: Display a user's Hyperliquid vault positions (HLP, Growi HF, Orbit Value Strategies, etc.) in the ColdStack GUI alongside wallet balances and LP positions.

Scope: READ-ONLY. No deposits, withdrawals, or signed transactions.

API Reference: Confirmed field names from nktkas GitBook / Dwellir / QuickNode:
- userVaultEquities returns: vaultAddress, vaultName, equity (deposited USD), shares, currentValue, unrealizedPnl, pnlPercent
- ALL numeric values are strings — parse with float()
- vaultDetails returns: portfolio (array of timeframes), leader, follower (equity, shares, pnl)
- APR is NOT a direct field — compute from portfolio accountValue change over time
- Test wallet 0xbf0e7d5868479b3b2602fa929dec6661408edc71 has no vaults yet (returns []) — so also test against a wallet known to hold HLP/Growi/Orbit positions, or use recorded mock data if available.

Files to create:
1. src/vault_tracker.py
   - HyperliquidVaultTracker class
   - VaultPosition dataclass
   - fetch_positions(wallet_address) -> List[VaultPosition]
   - Uses urllib.request to call https://api.hyperliquid.xyz/info
   - Calls:
       a) {"type": "userVaultEquities", "user": wallet}
       b) {"type": "vaultDetails", "vaultAddress": vault_addr, "user": wallet}
   - Defensive field parsing with aliases (see spec aliases table)
   - Computes share price, P&L, P&L % where not provided
   - Returns empty list / None when online_mode=False
   - No external dependencies (stdlib only)

2. Update src/gui_main_v5.py
   - Add a "Hyperliquid Vaults" section to the Vault tab (below wallet balances).
   - Render VaultPosition cards matching existing balance/LP card style.
   - Each card shows: vault name, deposited, current value, P&L (+/-), APR, shares, share price.
   - Add a section-level Refresh button, disabled when online_mode=False.
   - Fetch runs in a daemon thread; update UI via root.after().
   - Show offline message when online_mode=False.

3. Update build_gui_v5.py hidden imports if needed
   - Ensure vault_tracker.py is included in PyInstaller build (if the build script uses explicit imports or collect_submodules).

Rules:
- Hyperliquid-specific tracker only (no ABC for v5.1 per Kimi decision)
- Read-only only. No signing, no private keys, no key_manager_agent calls.
- Match existing code style in balance_engine.py, lp_engine.py, and gui_main_v5.py.
- Use urllib.request + json (no requests, no web3.py, no new pip dependencies).
- Handle API errors gracefully: return VaultPosition with .error set; never crash the GUI thread.
- Test wallet for development: 0xbf0e7d5868479b3b2602fa929dec6661408edc71
- Run py_compile on all modified files when done.
- Do not modify balance_engine.py core logic; vaults are separate from token balances.

Deliverables:
- src/vault_tracker.py
- Updated src/gui_main_v5.py
- Updated build_gui_v5.py (if required)
- Brief test notes: what API responses looked like and what fields were actually present.
```

---

## 9. Dependencies & Prerequisites

| Item | Status | Notes |
|------|--------|-------|
| Hyperliquid `/info` endpoint access | ✅ Available | Public endpoint, no API key. |
| `userVaultEquities` + `vaultDetails` docs | ✅ Confirmed | Kimi confirmed field names via nktkas GitBook, Dwellir, QuickNode. Test wallet returns `[]` until capital is deployed. |
| Test wallet | ✅ Known | `0xbf0e7d5868479b3b2602fa929dec6661408edc71` — Kris's Hyperliquid/HyperEVM wallet. |
| v5.0 GUI baseline | ✅ Exists | `gui_main_v5.py` has LP tab and online-mode patterns ready to mirror. |
| No new Python dependencies | ✅ Enforced | Stdlib only, matching `balance_engine.py` / `hyperliquid_adapter.py`. |
| Vault write actions | ❌ Out of scope | Deposit/withdraw will be a future spec. |

### 9.1 Live API verification (recommended but optional now that field names are confirmed)

Run this probe against the test wallet to see live JSON:

```bash
curl -X POST https://api.hyperliquid.xyz/info \
  -H "Content-Type: application/json" \
  -d '{"type":"userVaultEquities","user":"0xbf0e7d5868479b3b2602fa929dec6661408edc71"}'
```

If any vault addresses are returned, run for each:

```bash
curl -X POST https://api.hyperliquid.xyz/info \
  -H "Content-Type: application/json" \
  -d '{"type":"vaultDetails","vaultAddress":"<VAULT_ADDRESS>","user":"0xbf0e7d5868479b3b2602fa929dec6661408edc71"}'
```

Save the actual JSON shapes into `slater/memory/hyperliquid-vault-api-shape.md` for future reference.

---

## 10. Open Questions — Kimi's Decisions

1. **Tab placement:** ✅ Inside the existing Vault tab as a "Hyperliquid Vaults" section. Keep v5.1 small. If the section gets crowded with 3+ vaults, we can promote to its own tab in v5.2.
2. **Terminology:** ✅ Call it "Hyperliquid Vaults" — matches the platform UI. ColdStack's own vault is the "Key Vault" — distinct enough. No confusion.
3. **APR display:** ✅ Show "APR: computing…" on first load, then compute from portfolio history. If portfolio data is insufficient, show "APR: see Hyperliquid" — don't fabricate. Defer full APY analytics to v5.2.
4. **Deposit history:** ✅ Summary only for v5.1 (count + total in/out). Full drill-down is a tax/analytics feature — v5.2 or later.
5. **ABC design:** ✅ Keep it Hyperliquid-specific for v5.1. Don't over-engineer. When we add Krystal/Beefy, we refactor to an ABC — but that's premature abstraction right now. The `VaultPosition` dataclass is already venue-agnostic enough to reuse.

---

## 11. Acceptance Criteria

- [ ] `vault_tracker.py` compiles and imports cleanly.
- [ ] `HyperliquidVaultTracker.fetch_positions()` returns a list of `VaultPosition` objects for the test wallet when online.
- [ ] Offline mode returns empty list / shows disabled state in GUI.
- [ ] GUI renders vault cards with name, deposited, current value, P&L, APR, shares, share price.
- [ ] No new pip dependencies added.
- [ ] No private keys or signing methods introduced.
- [ ] `build_gui_v5.py` still produces a working `USB_DEPLOYMENT/coldstack.exe` after changes.

---

**🌊 Slater — one wave at a time.**
