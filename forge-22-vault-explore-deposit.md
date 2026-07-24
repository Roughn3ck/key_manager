# Forge-22: v5.1.2 — Vault Explore + Deposit

## Project
ColdStack (`/mnt/b/Blockchain/coldstack/`) — **v5.1.2**

## Context
The HL1 Vaults tab currently shows vault positions the user already has (read-only tracking). For v5.1.2, we need:
1. An "Explore Vaults" button that opens the Hyperliquid vaults page in the user's browser
2. A way for the user to paste a vault address they researched, and deposit USDC into it from within ColdStack

The deposit is signed locally via the key_manager_agent — higher security than a browser wallet.

## Architecture

### Vault Deposit Mechanism

Hyperliquid supports vault deposits via the **CoreWriter system contract** on HyperEVM (`0x3333333333333333333333333333333333333333`). This is an EVM transaction — no EIP-712 or L1 API signing needed. The agent's existing `broadcast_tx` handles everything.

From the Hyperliquid docs (Interacting with HyperCore):
- Action ID 2: Vault transfer
- Solidity types: `(address vault, bool isDeposit, uint64 usd)`
- Encoding: byte 1 = `0x01` (version), bytes 2-4 = action ID (big-endian), rest = ABI-encoded params
- Gas cost: ~47,000 gas

The `usd` value is in micro-USD (6 decimals): `int(amount * 1_000_000)`. For a $100 deposit → `usd = 100_000_000`.

The `to` address for the transaction is `0x3333333333333333333333333333333333333333` (CoreWriter).

**Note:** Vault transfers via CoreWriter are delayed on-chain for a few seconds (anti-latency measure). This is transparent to the user — the tx confirms normally, and the vault deposit appears a few seconds later.

### Source of Funds

The deposit draws from the user's **HL1 spot USDC balance**. The `vaultTransfer` action on Hyperliquid's system handles the ledger — it deducts from the user's spot USDC and credits to the vault. The user needs sufficient USDC in their HL1 spot balance.

## Implementation

### File 1: `src/gui_main_v5.py` (MODIFY — vault section only)

All changes are within the existing `create_vault_section` method and new methods in the vault section.

#### Change 1: Add "Explore Vaults" button to the header

In `create_vault_section`, in the `header_frame` section, add an "Explore Vaults" button next to the "Refresh Vaults" button:

```python
ctk.CTkButton(
    header_frame, text="Explore Vaults", width=120, height=28,
    font=ctk.CTkFont(size=12, weight="bold"),
    fg_color=("#6f42c1", "#5a32a3"),
    hover_color=("#5a32a3", "#42288a"),
    command=self._vault_explore,
).pack(side="right", padx=(5, 0))
```

Pack this BEFORE the Refresh button so the layout is: [Title on left] ... [Explore Vaults] [Refresh Vaults] on the right.

#### Change 2: Add "Deposit to Vault" button below the wallet selector bar

After the `wallet_bar` and before the offline banner, add a deposit bar:

```python
# Deposit to Vault bar
deposit_bar = ctk.CTkFrame(section, fg_color="transparent")
deposit_bar.pack(fill="x", padx=10, pady=(2, 5))

self._vault_widgets["vault_addr_entry"] = ctk.CTkEntry(
    deposit_bar, width=320,
    placeholder_text="Paste vault address (0x...)",
    font=ctk.CTkFont(size=11),
)

ctk.CTkButton(
    deposit_bar, text="Deposit to Vault", width=130, height=28,
    font=ctk.CTkFont(size=12, weight="bold"),
    fg_color=("#20c997", "#1aa179"),
    hover_color=("#1aa179", "#158f63"),
    command=self._vault_deposit_dialog,
).pack(side="right")
self._vault_widgets["vault_addr_entry"].pack(side="left", fill="x", expand=True, padx=(0, 5))
```

#### Change 3: Add new methods

```python
def _vault_explore(self):
    """Open the Hyperliquid vaults page in the user's default browser."""
    import webbrowser
    webbrowser.open("https://app.hyperliquid.xyz/vaults")

def _vault_deposit_dialog(self):
    """Open a deposit dialog for the vault address in the entry."""
    if not self.online_mode:
        self.show_notification("Offline - enable Online Mode in Settings", error=True)
        return
    entry = self._vault_widgets.get("vault_addr_entry")
    if not entry:
        return
    vault_address = entry.get().strip()
    if not vault_address or not vault_address.startswith("0x") or len(vault_address) != 42:
        self.show_notification("Enter a valid vault address (0x...)", error=True)
        return
    # Resolve the current wallet address and account name
    wallet_address = self._vault_get_current_wallet_address()
    if not wallet_address:
        self.show_notification("Select or enter a wallet address first", error=True)
        return
    account_name = self._vault_get_current_account_name()
    if not account_name:
        self.show_notification("Could not resolve vault account for this address", error=True)
        return
    # Open the deposit dialog
    from vault_deposit_dialog import VaultDepositDialog
    VaultDepositDialog(
        root=self.root,
        agent_url=self._get_agent_url() if hasattr(self, '_get_agent_url') else "http://127.0.0.1:8842",
        account_name=account_name,
        wallet_address=wallet_address,
        vault_address=vault_address,
        show_notification=self.show_notification,
    )
```

Also add these helper methods (if they don't already exist):

```python
def _vault_get_current_wallet_address(self):
    """Get the wallet address currently shown in the vault tab."""
    entry = self._vault_widgets.get("address_entry")
    if entry:
        addr = entry.get().strip()
        if addr:
            return addr
    # Try resolving from account dropdown
    selector = self._vault_widgets.get("selector_menu")
    if selector and selector.get() == "Account":
        account_menu = self._vault_widgets.get("account_menu")
        if account_menu:
            acct = account_menu.get()
            if acct and acct != "(no accounts)":
                return self._vault_resolve_account_address(acct)
    return ""

def _vault_get_current_account_name(self):
    """Get the vault account name for the current wallet."""
    selector = self._vault_widgets.get("selector_menu")
    if selector and selector.get() == "Account":
        account_menu = self._vault_widgets.get("account_menu")
        if account_menu:
            acct = account_menu.get()
            if acct and acct != "(no accounts)":
                return acct
    # Try to find account name from address
    addr = self._vault_get_current_wallet_address()
    if addr and self.key_manager:
        for acct_name, acct_data in self.key_manager.address_db.get("accounts", {}).items():
            for a in acct_data.get("addresses", []):
                if a.get("address", "").lower() == addr.lower():
                    return acct_name
    return ""
```

### File 2: `src/vault_deposit_dialog.py` (NEW)

A self-contained dialog for depositing USDC into a Hyperliquid vault.

```python
"""
Vault Deposit Dialog for ColdStack v5.1.2.

Self-contained dialog for depositing USDC into a Hyperliquid vault
via the CoreWriter system contract on HyperEVM.

Deposit mechanism:
  - EVM transaction to CoreWriter (0x3333333333333333333333333333333333333333)
  - Action ID 2 (vault transfer): (address vault, bool isDeposit, uint64 usd)
  - usd = amount in micro-USD (6 decimals)
  - Source: user's HL1 spot USDC balance
"""
```

The dialog contains:

1. **Title:** "Deposit to Vault" with the vault address shown below
2. **HL1 USDC Balance:** Show the user's available USDC spot balance (fetched from `spotClearinghouseState`)
3. **Amount input:** Text entry with "MAX" button to auto-fill the available balance
4. **Deposit button:** Large teal button
5. **Status label:** Shows "Submitting...", "Deposit complete", "Error: ..."
6. **Warning note:** "Deposits draw from your HL1 Spot USDC balance. Vault transfers via CoreWriter may take a few seconds to settle."

**Balance query:**
POST to `https://api.hyperliquid.xyz/info` with `{"type": "spotClearinghouseState", "user": wallet_address}`. Parse the response for USDC balance: find the entry where `coin == "USDC"`, get `total` minus `hold` = available.

**Deposit execution:**
Build the CoreWriter calldata:
```python
CORE_WRITER = "0x3333333333333333333333333333333333333333"

# Action encoding
version_byte = b'\x01'
action_id = b'\x00\x00\x02'  # Action ID 2 = vault transfer

# ABI-encode (address, bool, uint64)
vault_padded = bytes.fromhex(vault_address[2:].zfill(64))  # 32 bytes
is_deposit_padded = (1).to_bytes(32, 'big')  # bool → uint256
usd_amount = int(amount * 1_000_000)  # micro-USD
usd_padded = usd_amount.to_bytes(32, 'big')  # uint64 → uint256 for ABI

calldata = '0x' + (version_byte + action_id + vault_padded + is_deposit_padded + usd_padded).hex()
```

Then call the agent's `broadcast_tx`:
```python
agent_call("broadcast_tx", account=account_name, to=CORE_WRITER, data=calldata, value="0",
           chain_id=999, rpc="https://rpc.hyperliquid.xyz/evm", chain="EVM")
```

Wait for the tx receipt (poll `eth_getTransactionReceipt`). Show success/failure.

**Dialog visual design:**
Match ColdStack dark theme. Same style as other dialogs.

### Key Constants

```python
HL1_INFO_URL = "https://api.hyperliquid.xyz/info"
HL1_EXCHANGE_URL = "https://api.hyperliquid.xyz/exchange"
HYPEREVM_RPC = "https://rpc.hyperliquid.xyz/evm"
CORE_WRITER = "0x3333333333333333333333333333333333333333"
HYPE_SYSTEM_ADDRESS = "0x2222222222222222222222222222222222222222"
```

### Vault Address Prefix Handling

The user might paste the vault address with an "HL:" prefix (as shown on the Hyperliquid platform, e.g. `HL:0xd6e56265890b76413d1d527eb9b75e334c0c5b42`). The dialog should strip the `HL:` prefix:

```python
if vault_address.startswith("HL:"):
    vault_address = vault_address[3:]
```

## Verification

After making the changes:
1. Run `python -m py_compile src/vault_deposit_dialog.py` — verify syntax
2. Run `python -m py_compile src/gui_main_v5.py` — verify syntax
3. Do NOT rebuild the EXE — Kris will test from source first

## Constraints
- Create `src/vault_deposit_dialog.py` (NEW)
- Modify `src/gui_main_v5.py` (vault section only — add Explore button, deposit bar, and new methods)
- Do NOT rebuild the EXE
- Do NOT update README.md, STATUS.md, or any documentation
- Do NOT push to git
- Do NOT touch any files outside the vault section in gui_main_v5.py
- Use `urllib.request` for HTTP calls (no new dependencies)
- The dialog must be self-contained — all deposit logic lives in `vault_deposit_dialog.py`
- The GUI only adds buttons and a small method to open the dialog