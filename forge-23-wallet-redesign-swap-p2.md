# Forge-23: Prompt 2 of 3 — Swap Dialog Module

## Project
ColdStack (`/mnt/b/Blockchain/coldstack/`)

## Context
The wallet card buttons are redesigned in Prompt 1. Now we build the swap module — a self-contained `swap_dialog.py` that handles token swaps on HyperEVM, including unwrap WHYPE → HYPE.

## File: `src/swap_dialog.py` (NEW)

### What This Module Does

A self-contained CustomTkinter dialog that lets the user swap between tokens on HyperEVM. It also handles:
- Wrapping HYPE → WHYPE (deposit native HYPE to WHYPE contract)
- Unwrapping WHYPE → HYPE (withdraw from WHYPE contract)
- EVM ↔ HL1 transfers (import and use the existing evm_transfer_dialog)

### Architecture

The swap dialog has two modes, selected via a tab or toggle at the top:
1. **Swap** — token-to-token swap via Uniswap V3 SwapRouter on HyperEVM
2. **Bridge** — EVM ↔ HL1 transfer (reuses evm_transfer_dialog logic)

### Constants

```python
HYPEREVM_RPC = "https://rpc.hyperliquid.xyz/evm"
HL1_API = "https://api.hyperliquid.xyz/info"
SWAP_ROUTER = "0x1ebdfc75ffe3ba3de61e7138a3e8706ac841af9b"
POOL_FACTORY = "0xb1c0fa0b789320044a6f623cfe5ebda9562602e3"
WHYPE = "0x5555555555555555555555555555555555555555"
UBTC = "0x9FDBdA0A5e284c32744D2f17Ee5c74B284993463"
USDC = "0xb88339CB7199b77E23DB6E890353E22632Ba630f"
HYPE_SYSTEM_ADDRESS = "0x2222222222222222222222222222222222222222"
POSITION_MANAGER = "0xead19ae861c29bbb2101e834922b2feee69b9091"

# Known pools (verified on-chain)
KNOWN_POOLS = {
    ("WHYPE", "USDC", 500):   "0x264a1f3b9eb574a3e7be869ac415dc5430dcf571",
    ("WHYPE", "USDC", 3000):  "0xe712d505572b3f84c1b4deb99e1beab9dd0e23c9",
    ("WHYPE", "UBTC", 500):   "0xbbcf8523811060e1c112a8459284a48a4b17661f",
    ("WHYPE", "UBTC", 3000):  "0x3a36b04bcc1d5e2e303981ef643d2668e00b43e7",
    ("WHYPE", "UBTC", 10000): "0xb2eb6d459759936160a57297e4a03e067dbbe5cb",
    ("UBTC", "USDC", 3000):   "0x7bfa94fa95c06528e68e6526adc81c99dcf93533",
}

TOKEN_DECIMALS = {"HYPE": 18, "WHYPE": 18, "USDC": 6, "UBTC": 8}
TOKEN_ADDRESSES = {"WHYPE": WHYPE, "USDC": USDC, "UBTC": UBTC}

# ERC-20 selectors
SELECTOR_BALANCE_OF = "0x70a08231"
SELECTOR_TRANSFER = "0xa9059cbb"
SELECTOR_APPROVE = "0x095ea7b3"
SELECTOR_WITHDRAW = "0x2e1a7d4d"   # WHYPE.withdraw(uint256)
SELECTOR_EXACT_INPUT_SINGLE = "0x414bf389"
SELECTOR_SLOT0 = "0x3850c7bd"
SELECTOR_GET_POOL = "0x1698ee82"

MAX_UINT256 = 2**256 - 1
```

### Token Options

The dialog supports these tokens:
- **HYPE** — native gas token on HyperEVM (not an ERC-20)
- **WHYPE** — wrapped HYPE (ERC-20 at 0x555...5555)
- **USDC** — USD Coin (ERC-20)
- **UBTC** — Bitcoin (ERC-20)

### Swap Routing Logic

```
def _route_swap(token_in, token_out, amount):
    if token_in == token_out:
        return "same_token"
    
    # HYPE native handling
    if token_in == "HYPE" and token_out == "WHYPE":
        return "wrap"        # deposit native HYPE to WHYPE contract
    if token_in == "WHYPE" and token_out == "HYPE":
        return "unwrap"      # WHYPE.withdraw(amount)
    
    # HYPE → ERC-20: wrap to WHYPE first, then swap WHYPE → token_out
    if token_in == "HYPE":
        return "wrap_and_swap"
    # ERC-20 → HYPE: swap token_in → WHYPE, then unwrap to native HYPE
    if token_out == "HYPE":
        return "swap_and_unwrap"
    
    # ERC-20 → ERC-20: direct pool swap
    return "direct_swap"
```

### Swap Execution Paths

**Direct swap (ERC-20 → ERC-20):**
1. Approve SwapRouter for token_in
2. Call SwapRouter.exactInputSingle(tokenIn, tokenOut, fee, recipient, deadline, amountIn, amountOutMin, sqrtPriceLimitX96)
3. Wait for receipt

**Wrap (HYPE → WHYPE):**
1. Send native HYPE value to WHYPE contract address (simple value transfer)
2. Done — WHYPE is minted to sender

**Unwrap (WHYPE → HYPE):**
1. Call WHYPE.withdraw(amount) — selector 0x2e1a7d4d + amount
2. Done — native HYPE returned to sender

**Wrap and swap (HYPE → ERC-20):**
1. Wrap HYPE → WHYPE (send value to WHYPE contract)
2. Wait for receipt
3. Approve SwapRouter for WHYPE
4. Swap WHYPE → token_out via exactInputSingle
5. Wait for receipt

**Swap and unwrap (ERC-20 → HYPE):**
1. Approve SwapRouter for token_in
2. Swap token_in → WHYPE via exactInputSingle (recipient = self)
3. Wait for receipt
4. Unwrap WHYPE → HYPE (WHYPE.withdraw)
5. Wait for receipt

### Pool Selection

For direct swaps, pick the pool with the lowest fee tier:
```python
def _best_pool(token_in, token_out):
    # Sort tokens (Uniswap convention: token0 < token1)
    t0, t1 = sorted([token_in, token_out])
    for fee in [500, 3000, 10000]:  # prefer lowest fee
        key = (t0, t1, fee)
        if key in KNOWN_POOLS:
            return KNOWN_POOLS[key], fee
    return None, None
```

### Price Quote

Read the pool's slot0 to get sqrtPriceX96 and compute the expected output:
```python
def _get_quote(token_in, token_out, amount_in, fee):
    pool = KNOWN_POOLS.get((sorted_pair, fee))
    if not pool:
        return None
    # Read slot0
    result = rpc_call("eth_call", [{"to": pool, "data": SELECTOR_SLOT0}, "latest"])
    sqrt_price = int(result[2:66], 16) / (2**96)
    price = sqrt_price ** 2
    # Adjust for token order and decimals
    # ... (same logic as hyperliquid_writer.get_swap_quote)
    # Subtract fee
    fee_fraction = fee / 1_000_000
    return estimated_out * (1 - fee_fraction)
```

### Balance Fetching

Same pattern as evm_transfer_dialog:
- ERC-20 tokens: `balanceOf` via eth_call
- HYPE native: `eth_getBalance`
- Show balances next to token selectors with MAX buttons

### UI Layout

```
┌─────────────────────────────────────┐
│  Swap                               │  ← Title
│  Swap tokens on HyperEVM            │  ← Subtitle
│                                     │
│  [Swap]  [Bridge]                   │  ← Tab/toggle: Swap mode vs Bridge mode
│                                     │
│  From: [HYPE    ▼]   Balance: 0.04  │
│  Amount: [0.0    ]   MAX            │
│                                     │
│           ↓                         │  ← Direction indicator
│                                     │
│  To:   [WHYPE   ▼]   Balance: 76.08 │
│  Expected: 76.04 WHYPE              │  ← Quote output
│  Fee: 0.05%  ·  Gas: ~$0.001        │  ← Fee + gas estimate
│                                     │
│  [        Swap         ]            │  ← Execute button (teal, full width)
│                                     │
│  Status: Ready                       │
│  ⚠ Slippage: 1% (configurable)      │
└─────────────────────────────────────┘
```

When **Bridge** tab is selected, show the EVM ↔ HL1 transfer UI (reuse the logic from evm_transfer_dialog — import it or duplicate the UI with the same balance fetching and transfer execution).

### Dialog Constructor

```python
class SwapDialog:
    def __init__(self, root, agent_url, account_name, wallet_address, show_notification, price_engine=None):
        # Same pattern as EVMTransferDialog
        # agent_url: localhost:8842
        # account_name: vault account for signing
        # wallet_address: the EVM address
        # price_engine: optional, for USD display
```

### Agent Calls

Use the same `_agent_call` pattern as evm_transfer_dialog:
```python
def _agent_call(agent_url, cmd, **params) -> dict:
    payload = {"cmd": cmd, **params}
    # POST to agent_url, return result
```

Use `broadcast_tx` for all EVM transactions (wrap, unwrap, approve, swap).

### Slippage Protection

Default 1% slippage. Compute `amountOutMin = expected_output * (1 - slippage_pct/100)` and pass it as the `amountOutMinimum` field in the exactInputSingle call (currently 0 in the existing writer — this is an improvement).

### Gas Estimate

Show an estimated gas cost:
- Swap: ~150,000 gas × 0.1 Gwei = ~0.015 HYPE = ~$0.001
- Wrap: ~28,000 gas
- Unwrap: ~28,000 gas
- Display as "Gas: ~$0.001" in the dialog

### Helper Functions

Include all helpers in the module (self-contained, same as evm_transfer_dialog):
- `_rpc_call` — JSON-RPC call to HyperEVM
- `_hl1_info_call` — POST to Hyperliquid info API
- `_agent_call` — POST to key_manager_agent
- `_pad_address`, `_pad_uint256`, `_to_wei`, `_format_amount`

### Verification
Run `python -m py_compile src/swap_dialog.py` to verify syntax.

### Constraints
- Create `src/swap_dialog.py` (NEW)
- Do NOT modify any other files
- Do NOT rebuild the EXE
- Do NOT update any documentation
- Do NOT push to git
- Use `urllib.request` only (no new dependencies)
- Self-contained — all logic in swap_dialog.py