# Forge-23: Wallet Card Redesign + Swap Module

## Project
ColdStack (`/mnt/b/Blockchain/coldstack/`)

## Overview
This is a multi-prompt workflow. Do NOT rebuild the EXE, update README.md, STATUS.md, or push to git until the FINAL prompt says so.

---

## Prompt 1 of 3: Wallet Card Button Redesign

### File: `src/gui_main_v5.py`

### Context
The address cards on the Wallet tab have too many buttons stacked vertically with inconsistent widths and block colors. The user wants a cleaner, more compact layout.

### Current State
Each address card (`create_address_card` method, ~line 1172) has a vertical button stack on the right:
1. "Check Balance" — blue, width=100
2. "Copy" — blue, width=80
3. "Delete" — red, width=80
4. "EVM ↔ HL1" — blue, width=80 (only on Hyperliquid addresses)

### Required Changes

**1. Move Copy to inline icon next to address**

Replace the "Copy" button with a small inline copy icon (📋 or use unicode ⧉) placed immediately after the address text. Clicking it copies the address to clipboard.

Find the address label creation (approximately line 1200):
```python
address_label = ctk.CTkLabel(
    info_frame,
    text=address_data.get("address", ""),
    font=ctk.CTkFont(size=11),
    wraplength=400
)
address_label.pack(anchor="w", pady=(2, 0))
```

Replace with an address row that includes the copy icon:
```python
address_row = ctk.CTkFrame(info_frame, fg_color="transparent")
address_row.pack(anchor="w", pady=(2, 0))

address_label = ctk.CTkLabel(
    address_row,
    text=address_data.get("address", ""),
    font=ctk.CTkFont(size=11),
    wraplength=400
)
address_label.pack(side="left")

copy_icon = ctk.CTkLabel(
    address_row,
    text="\u2398",  # ⧉ copy icon
    font=ctk.CTkFont(size=12),
    text_color="gray60",
    cursor="hand2",
    width=20,
)
copy_icon.pack(side="left", padx=(4, 0))
copy_icon.bind("<Button-1>", lambda e, a=address_data["address"]: self.copy_to_clipboard(a))
copy_icon.bind("<Enter>", lambda e: copy_icon.configure(text_color="gray80"))
copy_icon.bind("<Leave>", lambda e: copy_icon.configure(text_color="gray60"))
```

Remove the old "Copy" button from the button frame entirely.

**2. Rename "Delete" to "Remove Chain"**

Change the delete button text from "Delete" to "Remove".
Update the confirmation dialog title from "Delete Address" to "Remove Address" and the message from "Delete this address?" to "Remove this address?".

**3. Remove "EVM ↔ HL1" button from address cards**

The EVM↔HL1 transfer is moving into the Swap module. Remove the EVM↔HL1 button from `create_address_card` entirely. Also remove the `_open_evm_transfer` method — it will be replaced by the swap dialog opener.

**4. Add "Swap" button**

Add a "Swap" button to every address card that has balance support (same check as Check Balance). The Swap button opens the swap dialog (which will be created in Prompt 2).

```python
# Swap button — opens the swap dialog
chain_str_check = (address_data.get("chain", "") + " " + address_data.get("coin", "")).lower()
swap_supported = ("hype" in chain_str_check or "hyperliquid" in chain_str_check or
                  "evm" in chain_str_check or "btc" in chain_str_check or
                  "eth" in chain_str_check or "sol" in chain_str_check)
if swap_supported and self.online_mode:
    ctk.CTkButton(
        button_frame,
        text="Swap",
        width=100, height=28,
        font=ctk.CTkFont(size=11),
        fg_color=("#6f42c1", "#5a32a3"),
        hover_color=("#5a32a3", "#42288a"),
        command=lambda addr=address_data["address"], acct=account_name: self._open_swap_dialog(addr, acct)
    ).pack(pady=2)
```

Add a stub method for now (Prompt 2 will create the actual swap module):
```python
def _open_swap_dialog(self, wallet_address, account_name):
    """Open the swap dialog."""
    from swap_dialog import SwapDialog
    SwapDialog(
        root=self.root,
        agent_url=self._get_agent_url(),
        account_name=account_name,
        wallet_address=wallet_address,
        show_notification=self.show_notification,
        price_engine=self.price_engine,
    )
```

**5. Uniform button widths and styling**

All buttons in the button frame should be the same width (100px). Update the remaining buttons:

- "Check Balance": width=100 (already 100, keep it)
- "Remove": width=100 (was 80, change to 100)
- "Swap": width=100

**6. Button styling improvements**

Make buttons more interactive with hover effects. Use slightly less saturated colors with visible hover state changes:

- Check Balance: `fg_color=("#2b6cb0", "#2c5282")`, `hover_color=("#3182ce", "#4299e1")`
- Remove: `fg_color=("#c53030", "#9b2c2c")`, `hover_color=("#e53e3e", "#c53030")`, text="Remove"
- Swap: `fg_color=("#6b46c1", "#553c9a")`, `hover_color=("#805ad5", "#6b46c1")`

### Final button stack (top to bottom):
1. "Check Balance" — blue, width=100
2. "Swap" — purple, width=100 (only for supported chains, online mode)
3. "Remove" — red, width=100

### Copy is now inline icon next to address text.

### Verification
Run `python -m py_compile src/gui_main_v5.py` to verify syntax.

### Constraints
- Only modify `src/gui_main_v5.py`
- Do NOT rebuild the EXE
- Do NOT update any documentation
- Do NOT push to git
- Do NOT touch any other methods except `create_address_card` and `confirm_delete_address`