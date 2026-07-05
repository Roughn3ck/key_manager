# Coldstack v5.0 — LP Engine Integration (Hyperliquid / Project X)

## Overview

Integrate the existing LP Engine (`lp_engine.py`, `hyperliquid_adapter.py`, `lp_gui_poc.py`) into the main Coldstack GUI. Add the Hyperliquid Writer for position management. First target: WHYPE/UBTC pool on Project X (HyperEVM).

## What Already Exists (DO NOT REWRITE)

The LP engine foundation is solid and working. These files are the starting point:

### `src/lp_engine.py` (340 lines) — KEEP
- `LPPosition` dataclass with all fields
- `VenueAdapter` ABC with `can_handle()`, `fetch_position()`, `fetch_all_positions()`, `fetch_fees_earned()`
- `StrategyEngine` with `analyze()`, `should_rebalance()`, `compute_recentered_range()`
- `LPEngine` facade with `fetch_position()`, `fetch_all_positions()`, `should_rebalance()`
- Adapter registry (`@register_adapter` decorator, `get_adapter()`, `detect_venue()`)
- `OfflineError` exception

### `src/venue_adapters/__init__.py` — KEEP
- Imports `HyperliquidAdapter` to trigger registration

### `src/venue_adapters/hyperliquid_adapter.py` (900+ lines) — KEEP, minor additions only
- Full HyperEVM LP position reads via NFT Position Manager
- L1 perp/spot position reads
- WHYPE/UBTC pool state queries
- Batch RPC with HyperEVM rate-limit handling (max 2 calls per batch)
- Known token-id hints for G5 wallet (`0xbf0e7d5868479b3b2602fa929dec6661408edc71` → `[496329, 453338]`)
- Contract addresses already defined: `WHYPE`, `UBTC`, `POOL_FACTORY`, `POSITION_MANAGER`, `WHYPE_UBTC_POOL_3000`
- `can_write()` returns `True`, `get_writer()` imports `HyperliquidWriter` (which doesn't exist yet)

### `src/venue_adapters/venue_writer.py` (170 lines) — KEEP
- `VenueWriter` ABC with full interface: `wrap_native`, `unwrap_native`, `approve`, `swap`, `open_position`, `increase_liquidity`, `decrease_liquidity`, `collect_fees`, `close_position`, `rebalance`
- Dataclasses: `SwapParams`, `OpenPositionParams`, `IncreaseLiquidityParams`, `DecreaseLiquidityParams`, `CollectFeesParams`, `RebalanceParams`

### `src/lp_gui_poc.py` (250 lines) — REFERENCE, merge into main GUI
- `build_lp_tab_content()` function that builds a standalone LP tab
- Card rendering matching v4.1 address card style
- Address entry, Refresh button, offline banner
- This is a PoC — the logic is correct but it needs to be integrated into the main GUI class

## What Needs to Be Built

### Step 1: Create `src/venue_adapters/hyperliquid_writer.py`

Implement `VenueWriter` for HyperEVM. This is the write-side counterpart to the read-only adapter.

**Key design decisions:**
- The writer does NOT hold private keys. It calls the vault's headless agent (`key_manager_agent.py`) for signing.
- The writer communicates with the agent via HTTP to `localhost:8842` using the `sign_tx` and `broadcast_tx` commands.
- The vault must be unlocked and the agent running. The writer checks `is_available()` by pinging the agent's status endpoint.
- `unlock()` accepts `{"password": "..."}` and sends it to the agent.

**Contract addresses (HyperEVM, Chain ID 999):**
```
WHYPE = "0x5555555555555555555555555555555555555555"
UBTC = "0x9FDBdA0A5e284c32744D2f17Ee5c74B284993463"
POSITION_MANAGER = "0xead19ae861c29bbb2101e834922b2feee69b9091"
POOL_FACTORY = "0xb1c0fa0b789320044a6f623cfe5ebda9562602e3"
WHYPE_UBTC_POOL_3000 = "0x3a36b04bcc1d5e2e303981ef643d2668e00b43e7"
SWAP_ROUTER = "0x..."  # Research: find the HyperEVM swap router address
```

**Methods to implement:**

```python
class HyperliquidWriter(VenueWriter):
    VENUE_KEY = "hyperliquid"
    
    def __init__(self, agent_url="http://127.0.0.1:8842"):
        # Store agent URL, vault account name, chain config
    
    def is_available(self) -> bool:
        # Ping agent status endpoint, return True if unlocked
    
    def unlock(self, credentials: dict) -> bool:
        # Send password to agent, store session
    
    def wrap_native(self, account: str, amount: float) -> str:
        # Deposit HYPE to WHYPE contract (WHYPE is the wrapped native token)
        # tx: send {amount} HYPE to WHYPE contract address with empty data
        # Returns tx hash
    
    def unwrap_native(self, account: str, amount: float) -> str:
        # Call WHYPE.withdraw(amount)
        # Returns tx hash
    
    def approve(self, account: str, token: str, spender: str, amount: float) -> str:
        # ERC-20 approve(spender, amount)
        # Returns tx hash
    
    def swap(self, params: SwapParams) -> str:
        # Single-pool exact-input swap via swap router
        # Returns tx hash
    
    def open_position(self, params: OpenPositionParams) -> Tuple[str, Optional[int]]:
        # 1. Approve token0 and token1 for Position Manager
        # 2. Call PositionManager.mint(positionParams)
        # 3. Parse NewPosition event to get tokenId
        # Returns (tx_hash, new_position_id)
    
    def increase_liquidity(self, params: IncreaseLiquidityParams) -> str:
        # 1. Approve tokens
        # 2. Call PositionManager.increaseLiquidity(tokenId, amount0Desired, amount1Desired, ...)
        # Returns tx hash
    
    def decrease_liquidity(self, params: DecreaseLiquidityParams) -> str:
        # Call PositionManager.decreaseLiquidity(tokenId, liquidity, ...)
        # Returns tx hash
    
    def collect_fees(self, params: CollectFeesParams) -> str:
        # Call PositionManager.collect(tokenId, recipient)
        # Returns tx hash
    
    def close_position(self, position_id: str, account: str) -> List[str]:
        # 1. decreaseLiquidity(100%)
        # 2. collect()
        # Returns list of tx hashes
    
    def rebalance(self, params: RebalanceParams) -> List[str]:
        # 1. Read current position to get tokenId
        # 2. decreaseLiquidity(100%) + collect
        # 3. If token ratios need swapping, execute swap
        # 4. open_position with new tick range
        # Returns list of tx hashes
```

**Agent communication pattern:**
```python
def _agent_call(self, cmd: str, **params) -> dict:
    """Send a command to the key_manager_agent."""
    payload = {"cmd": cmd, **params}
    req = urllib.request.Request(
        self.agent_url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())
```

**For sign_tx / broadcast_tx, the writer sends:**
```json
{
  "cmd": "broadcast_tx",
  "account": "G5",
  "chain": "EVM",
  "to": "0x...",
  "data": "0x...",
  "value": "0",
  "chain_id": 999,
  "rpc": "https://rpc.hyperliquid.xyz/evm"
}
```

**Important:** The writer is a TOOL for the user, not an autonomous agent. Every write operation requires explicit user confirmation in the GUI. The writer just provides the capability — the GUI adds the confirmation step.

### Step 2: Integrate LP Tab into Main GUI

The current GUI (`gui_main_v4.py`) is a two-panel layout: left panel (accounts) + right panel (addresses). We need to add LP positions as a third view.

**Option A: Add a CTkTabview at the top level** — tabs for "Vault" (current two-panel view) and "LP Positions" (new LP tab). This is cleaner but requires restructuring the main layout.

**Option B: Add "LP Positions" as a button in the left panel** that switches the right panel to show LP positions instead of addresses. Simpler but less discoverable.

**Recommendation: Option A** — CTkTabview with two tabs. This is the right UX for v5 and sets up future tabs (portfolio dashboard, etc.).

**Implementation:**
1. In `create_main_dashboard()`, wrap the main container in a `CTkTabview`
2. Tab 1: "Vault" — existing two-panel layout (accounts + addresses)
3. Tab 2: "LP Positions" — LP tab content from `lp_gui_poc.py`
4. The LP tab reuses the existing `self.balance_engine`, `self.price_engine`, `self.online_mode`
5. LP tab gets its own `LPEngine` instance
6. Address entry pre-fills from the currently selected account's first EVM address
7. Refresh button fetches all LP positions for the entered address
8. Cards render with health emoji, pair, range, fees, value, suggestion
9. "Copy" button copies position ID to clipboard
10. Offline banner when Go Online is OFF

**LP tab needs access to:**
- `self.online_mode` — to enable/disable refresh
- `self.price_engine` — for USD value calculations
- `self.rpc_config` — for custom RPC endpoints (HyperEVM)
- `self.api_keys` — for any API keys
- Selected account's addresses — to pre-fill the wallet address field

### Step 3: Create `build_gui_v5.py`

New build script for v5.0. Copy `build_gui_v4.py` and modify:

```python
# Changes from build_gui_v4.py:
# 1. Source file: src/gui_main_v5.py (or gui_main_v4.py if we modify in-place)
# 2. Add hidden imports: lp_engine, venue_adapters, venue_adapters.hyperliquid_adapter, venue_adapters.venue_writer, venue_adapters.hyperliquid_writer
# 3. Add data files: rpc_endpoints.json, venue_adapters/ (if needed)
# 4. Update version strings to v5.0
# 5. Backup previous EXE as coldstack_v4_2.exe
```

**Decision: Create `gui_main_v5.py`** per versioning rules. Copy `gui_main_v4.py` → `gui_main_v5.py`, then modify. This preserves v4.2 untouched.

### Step 4: Update Documentation

- **`.clinerules`**: Update version table, add v5.0, update project structure, update known issues
- **`README.md`**: Add v5.0 changelog
- **`STATUS.md`**: Add v5.0 section

### Step 5: Compile and Test

```bash
cd B:\Blockchain\coldstack
python -m py_compile src/lp_engine.py
python -m py_compile src/venue_adapters/hyperliquid_adapter.py
python -m py_compile src/venue_adapters/hyperliquid_writer.py
python -m py_compile src/gui_main_v5.py
python build_gui_v5.py
```

## Architecture Diagram (v5.0)

```
ColdStack v5.0
├── GUI (gui_main_v5.py)
│   ├── Tab: "Vault" (existing two-panel layout)
│   │   ├── Left: Accounts list
│   │   └── Right: Address cards + balances
│   └── Tab: "LP Positions" (NEW)
│       ├── Wallet address entry (pre-filled from selected account)
│       ├── Refresh button (disabled when offline)
│       ├── Scrollable LP position cards
│       │   ├── Health emoji + pair + venue
│       │   ├── Range / current price / position %
│       │   ├── Fees earned (USD + native)
│       │   ├── Value / PnL
│       │   ├── Strategy suggestion
│       │   └── Action buttons (Copy, Refresh)
│       └── Status bar
│
├── LP Engine (lp_engine.py)
│   ├── LPEngine facade
│   ├── StrategyEngine (read-only analysis)
│   └── Adapter registry
│
├── Venue Adapters (venue_adapters/)
│   ├── hyperliquid_adapter.py (READ - existing, working)
│   │   ├── HyperEVM LP positions (NFT Position Manager)
│   │   ├── L1 perp/spot positions
│   │   └── Pool state queries
│   ├── hyperliquid_writer.py (WRITE - NEW)
│   │   ├── wrap/unwrap HYPE↔WHYPE
│   │   ├── approve tokens
│   │   ├── open/increase/decrease/collect positions
│   │   ├── swap (exact-input single-pool)
│   │   └── rebalance (close → swap → open)
│   └── venue_writer.py (ABC - existing)
│
├── Key Manager Agent (key_manager_agent.py)
│   ├── sign_tx (EVM EIP-1559)
│   ├── broadcast_tx
│   └── localhost:8842
│
└── Shared Engines
    ├── Balance Engine (balance_engine.py)
    ├── Price Engine (price_engine.py)
    ├── RPC Config (rpc_config.py)
    └── Crypto Engine (crypto_engine.py)
```

## Security Rules (v5.0)

1. **LP Engine is read-only by default.** The writer is only accessible when the vault is unlocked AND the user explicitly confirms each write operation.
2. **Writer never holds private keys.** All signing goes through the headless agent.
3. **Every write operation requires GUI confirmation.** "You are about to [action] on [venue]. This will spend gas. Continue?"
4. **Offline mode blocks all LP operations** — both reads and writes.
5. **No auto-trading, no bots, no autonomous rebalancing.** The strategy engine SUGGESTS actions. The user DECIDES.
6. **Writer operations are logged** to `gui_debug.log` with timestamp, action, and tx hash (no private data).

## WHYPE/UBTC Pool — First Target

This is the pool for the K&P investment rotation. The adapter already knows:

| Field | Value |
|-------|-------|
| Pool | WHYPE/UBTC 0.3% fee tier |
| Pool address | `0x3a36b04bcc1d5e2e303981ef643d2668e00b43e7` |
| WHYPE | `0x5555555555555555555555555555555555555555` |
| UBTC | `0x9FDBdA0A5e284c32744D2f17Ee5c74B284993463` |
| Position Manager | `0xead19ae861c29bbb2101e834922b2feee69b9091` |
| Factory | `0xb1c0fa0b789320044a6f623cfe5ebda9562602e3` |
| Chain ID | 999 (HyperEVM) |
| RPC | `https://rpc.hyperliquid.xyz/evm` |
| G5 wallet | `0xbf0e7d5868479b3b2602fa929dec6661408edc71` |
| Known token IDs | 496329, 453338 |

**Token dynamics:**
- WHYPE is wrapped HYPE (1:1, 18 decimals)
- UBTC is bridged BTC (8 decimals)
- Pool is Uniswap V3 style concentrated liquidity
- Fee tier: 3000 (0.3%)
- Tick spacing: 60

## Important Notes

1. **Don't touch the crypto engine, derivation engine, or balance engine.** They're stable and unrelated.
2. **Don't rewrite the adapter.** The HyperliquidAdapter is 900+ lines of working code. Only add what's needed.
3. **The writer is the main new code.** Everything else is integration of existing components.
4. **Versioning rules apply:** Create `gui_main_v5.py` (copy of v4), modify it, leave v4 untouched.
5. **Backward compatible:** v4.2 vaults open in v5.0 without migration. LP tab is additive.
6. **After ANY source change, rebuild the EXE.** `python build_gui_v5.py`
7. **Test the EXE, not just the script.** PyInstaller can have different behavior than script mode.
8. **The swap router address on HyperEVM needs research.** The writer's `swap()` method depends on finding the correct router contract. If it doesn't exist yet (HyperEVM is new), stub it out with a clear error message and implement later.
9. **The writer's `open_position` needs the exact Position Manager ABI.** The adapter already calls `positions(tokenId)` — the writer needs `mint(params)` and `increaseLiquidity(params)`. Research the exact function signatures from the HyperEVM Position Manager contract.
10. **G5 wallet is hardcoded in the adapter as a known-hint.** This is fine for now — it speeds up scanning. In future, we can make this configurable.

## File Change Summary

| File | Action | Scope |
|------|--------|-------|
| `src/venue_adapters/hyperliquid_writer.py` | **CREATE** | Full VenueWriter implementation for HyperEVM |
| `src/gui_main_v5.py` | **CREATE** | Copy of v4 + CTkTabview + LP tab integration |
| `build_gui_v5.py` | **CREATE** | PyInstaller build for v5.0 |
| `src/venue_adapters/hyperliquid_adapter.py` | **MINOR** | Only if writer needs new adapter methods |
| `.clinerules` | **MODIFY** | v5.0 version table, structure, known issues |
| `README.md` | **MODIFY** | v5.0 changelog |
| `STATUS.md` | **MODIFY** | v5.0 section |

## Testing Checklist

- [ ] `python -m py_compile` passes for all new/modified files
- [ ] v4.2 vault opens in v5.0 without errors
- [ ] Vault tab works identically to v4.2 (accounts, addresses, balances, settings)
- [ ] LP Positions tab appears as second tab
- [ ] LP tab shows offline banner when Go Online is OFF
- [ ] LP tab Refresh button is disabled when offline
- [ ] Entering G5 address + Refresh shows LP positions
- [ ] LP cards render with correct pair, range, fees, health emoji
- [ ] Strategy suggestions display correctly
- [ ] Copy button copies position ID
- [ ] Switching between Vault and LP tabs preserves state
- [ ] Writer: `is_available()` returns True when agent is running
- [ ] Writer: `unlock()` succeeds with correct password
- [ ] Writer: `wrap_native()` sends correct tx to agent
- [ ] Writer: `approve()` sends correct ERC-20 approve tx
- [ ] Writer: `open_position()` flow works end-to-end (approve + mint)
- [ ] Writer: `collect_fees()` works
- [ ] Writer: `rebalance()` flow works (close → collect → open)
- [ ] All write operations show confirmation dialog in GUI
- [ ] `build_gui_v5.py` produces working `coldstack.exe`
- [ ] EXE runs on clean Windows (no Python)
- [ ] Previous `coldstack.exe` backed up before overwrite
