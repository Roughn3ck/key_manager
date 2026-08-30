"""ColdStack account management dialogs (extracted from gui_main_v5.py in v5.1.4)."""

import os
import threading
import tkinter as tk
import tkinter.ttk as ttk
from pathlib import Path
from tkinter import filedialog, messagebox
from typing import Any, Dict, List, Optional

import customtkinter as ctk

from derivation_engine import DerivationEngine
from chain_options import CHAIN_OPTIONS, DERIVATION_CHAINS


def confirm_delete_address(gui, account_name, addr_index):
    """Show a confirmation dialog before removing an address."""
    dialog = ctk.CTkToplevel(gui.root)
    dialog.title("Remove Address")
    dialog.geometry("400x180")
    dialog.transient(gui.root)
    dialog.grab_set()
    gui._center_dialog(dialog)

    ctk.CTkLabel(dialog, text="Remove this address?",
                 font=ctk.CTkFont(size=16, weight="bold")).pack(pady=(20, 10))
    ctk.CTkLabel(dialog, text="This action cannot be undone.",
                 font=ctk.CTkFont(size=12), text_color="orange").pack(pady=(0, 10))

    def do_delete():
        try:
            if gui.key_manager.delete_address(account_name, addr_index, gui.current_password):
                gui.show_notification("Address removed")
                dialog.destroy()
                gui.refresh_left_panel()
                # Refresh the current view if still viewing this account
                if gui.current_account == account_name:
                    gui.select_account(gui.current_pool or "Unassigned", account_name)
            else:
                gui.show_notification("Failed to remove address", error=True)
                dialog.destroy()
        except Exception as e:
            gui.show_notification(f"Error: {e}", error=True)
            dialog.destroy()

    btn_frame = ctk.CTkFrame(dialog, fg_color="transparent")
    btn_frame.pack(pady=15)
    ctk.CTkButton(btn_frame, text="Remove", command=do_delete, width=100,
                  fg_color=("#c53030", "#9b2c2c"),
                  hover_color=("#e53e3e", "#c53030")).pack(side="left", padx=10)
    ctk.CTkButton(btn_frame, text="Cancel", command=dialog.destroy, width=100,
                  fg_color="gray30").pack(side="left", padx=10)


def show_delete_private_key_dialog(gui, account_name: str, key_index: int):
    """Confirmation dialog for deleting a single private key from an account.

    Shows the key's metadata (chain, source, derivation path, derived address)
    but never the key value itself.  Requires password confirmation.

    Args:
        gui: ColdStackGUI instance.
        account_name: Name of the account containing the key.
        key_index: 0-based index into the account's private keys list.
    """
    keys = gui.key_manager.show_private_key(account_name)
    if key_index < 0 or key_index >= len(keys):
        gui.show_notification("Invalid key index", error=True)
        return

    entry = keys[key_index]
    chain = entry.get("chain", "") or "(no chain)"
    source = entry.get("source", "manual")
    dpath = entry.get("derivation_path", "")
    derived_addr = entry.get("derived_address", "")

    # Build metadata summary (never display the key value)
    meta_parts = [f"[{key_index}] {chain}"]
    if source == "derived":
        meta_parts.append("derived")
    if dpath:
        meta_parts.append(dpath)
    if derived_addr:
        meta_parts.append(f"addr: {derived_addr[:20]}...")
    key_meta = " | ".join(meta_parts)

    dialog = ctk.CTkToplevel(gui.root)
    dialog.title("Delete Private Key")
    dialog.geometry("460x300")
    dialog.transient(gui.root)
    dialog.grab_set()
    gui._center_dialog(dialog)

    ctk.CTkLabel(dialog, text="Delete Private Key",
                 font=ctk.CTkFont(size=16, weight="bold")).pack(pady=(20, 5))
    ctk.CTkLabel(dialog, text=f"Account: {account_name}",
                 font=ctk.CTkFont(size=12), text_color="gray60").pack(pady=(0, 5))
    ctk.CTkLabel(dialog, text=key_meta,
                 font=ctk.CTkFont(size=11), text_color="gray50",
                 wraplength=420).pack(pady=(0, 5))
    ctk.CTkLabel(dialog, text="This action is permanent and cannot be undone.",
                 font=ctk.CTkFont(size=12), text_color="orange").pack(pady=(0, 10))

    password_entry = ctk.CTkEntry(
        dialog, placeholder_text="Enter master password", show="\u2022",
        width=250, font=ctk.CTkFont(size=12))
    password_entry.pack(pady=(0, 5))
    password_entry.focus_set()

    status_label = ctk.CTkLabel(dialog, text="", font=ctk.CTkFont(size=11))
    status_label.pack(pady=5)

    def do_delete():
        password = password_entry.get()
        if not password:
            status_label.configure(text="Please enter password", text_color="red")
            return
        if password != gui.current_password:
            status_label.configure(text="Invalid password", text_color="red")
            return
        try:
            if gui.key_manager.delete_private_key(account_name, key_index, password):
                gui.show_notification(f"Private key {key_index} deleted from '{account_name}'")
                dialog.destroy()
                # Refresh the account view
                if gui.current_account == account_name:
                    gui.select_account(gui.current_pool or "Unassigned", account_name)
            else:
                status_label.configure(text="Failed to delete key", text_color="red")
        except Exception as e:
            status_label.configure(text=f"Error: {e}", text_color="red")

    password_entry.bind("<Return>", lambda e: do_delete())

    btn_frame = ctk.CTkFrame(dialog, fg_color="transparent")
    btn_frame.pack(pady=15)
    ctk.CTkButton(btn_frame, text="Delete Key", command=do_delete, width=120,
                  fg_color=("#c53030", "#9b2c2c"),
                  hover_color=("#e53e3e", "#c53030")).pack(side="left", padx=10)
    ctk.CTkButton(btn_frame, text="Cancel", command=dialog.destroy, width=100,
                  fg_color="gray30").pack(side="left", padx=10)


def show_delete_account_dialog(gui):
    """Dialog to delete an account and all its data."""
    if not gui.current_password:
        gui.show_notification("Vault not unlocked", error=True)
        return

    account_names = gui._get_all_account_names()
    if not account_names:
        gui.show_notification("No accounts to delete", error=True)
        return

    dialog = ctk.CTkToplevel(gui.root)
    dialog.title("Delete Account")
    dialog.geometry("420x280")
    dialog.transient(gui.root)
    dialog.grab_set()
    gui._center_dialog(dialog)

    ctk.CTkLabel(dialog, text="Delete Account",
                 font=ctk.CTkFont(size=16, weight="bold")).pack(pady=(20, 10))

    form = ctk.CTkFrame(dialog, fg_color="transparent")
    form.pack(pady=10, padx=20, fill="x")

    ctk.CTkLabel(form, text="Select Account:").pack(anchor="w")
    default_account = gui.current_account if gui.current_account else account_names[0]
    acct_var = ctk.StringVar(value=default_account)
    acct_menu = ctk.CTkOptionMenu(form, variable=acct_var, values=account_names, width=300)
    acct_menu.pack(fill="x", pady=(0, 10))

    ctk.CTkLabel(form,
                 text="\u26A0 This will permanently delete the account and ALL its\n"
                      "addresses, mnemonic, and private keys. This cannot be undone.",
                 font=ctk.CTkFont(size=10), text_color="orange").pack(anchor="w")

    status_label = ctk.CTkLabel(dialog, text="", font=ctk.CTkFont(size=11))
    status_label.pack()

    def do_delete():
        account = acct_var.get().strip()
        if not account:
            status_label.configure(text="Select an account to delete", text_color="red")
            return
        try:
            if gui.key_manager.delete_account(account, gui.current_password):
                gui.show_notification(f"Account '{account}' deleted")
                dialog.destroy()
                # Clear current account if it was the one deleted
                if gui.current_account == account:
                    gui.current_account = None
                    gui.current_pool = None
                    gui.right_panel_title.configure(
                        text="Select an account to view addresses"
                    )
                    gui.show_placeholder_view()
                gui.refresh_left_panel()
            else:
                status_label.configure(
                    text="Account not found in vault data. It may have already been removed.",
                    text_color="red"
                )
        except Exception as e:
            status_label.configure(text=f"Error: {e}", text_color="red")

    btn_frame = ctk.CTkFrame(dialog, fg_color="transparent")
    btn_frame.pack(pady=15)
    ctk.CTkButton(btn_frame, text="Delete", command=do_delete, width=100,
                  fg_color=("#dc3545", "#c82333")).pack(side="left", padx=10)
    ctk.CTkButton(btn_frame, text="Cancel", command=dialog.destroy, width=100,
                  fg_color="gray30").pack(side="left", padx=10)

def show_add_account_dialog(gui):
    """Dialog to create a new account, optionally in a pool."""
    if not gui.current_password:
        gui.show_notification("Vault not unlocked", error=True)
        return

    dialog = ctk.CTkToplevel(gui.root)
    dialog.title("Add New Account")
    dialog.geometry("420x300")
    dialog.transient(gui.root)
    dialog.grab_set()
    gui._center_dialog(dialog)

    ctk.CTkLabel(dialog, text="Add New Account",
                 font=ctk.CTkFont(size=16, weight="bold")).pack(pady=(20, 10))

    form = ctk.CTkFrame(dialog, fg_color="transparent")
    form.pack(pady=10, padx=20, fill="x")

    ctk.CTkLabel(form, text="Account Name:").pack(anchor="w")
    name_entry = ctk.CTkEntry(form, placeholder_text="e.g. Ledger_1", width=300)
    name_entry.pack(fill="x", pady=(0, 10))
    name_entry.focus_set()

    # Pool field — a ttk.Combobox that shows existing pools but also
    # allows the user to type a new pool name (auto-created on save).
    pools = list(gui.key_manager.address_db.get("pools", {}).keys())
    gui._style_combobox()
    import tkinter.ttk as ttk
    pool_values = ["(Unassigned)"] + pools
    ctk.CTkLabel(form, text="Pool (optional — type to create a new pool):").pack(anchor="w")
    pool_var = ctk.StringVar(value="(Unassigned)")
    pool_combo = ttk.Combobox(form, textvariable=pool_var, values=pool_values,
                              width=40, style="Dark.TCombobox")
    pool_combo.pack(fill="x", pady=(0, 10))

    status_label = ctk.CTkLabel(dialog, text="", font=ctk.CTkFont(size=11))
    status_label.pack()

    def do_add():
        name = name_entry.get().strip()
        if not name:
            status_label.configure(text="Account name required", text_color="red")
            return
        pool = pool_var.get().strip()
        pool = None if pool == "(Unassigned)" or pool == "" else pool
        try:
            if gui.key_manager.add_account(name, pool):
                gui.key_manager.save_encrypted_data(gui.current_password)
                gui.show_notification(f"Account '{name}' added")
                dialog.destroy()
                gui.refresh_left_panel()
            else:
                status_label.configure(text="Failed to add account", text_color="red")
        except Exception as e:
            status_label.configure(text=f"Error: {e}", text_color="red")

    name_entry.bind("<Return>", lambda e: do_add())
    btn_frame = ctk.CTkFrame(dialog, fg_color="transparent")
    btn_frame.pack(pady=15)
    ctk.CTkButton(btn_frame, text="Add Account", command=do_add, width=120).pack(side="left", padx=10)
    ctk.CTkButton(btn_frame, text="Cancel", command=dialog.destroy, width=100,
                  fg_color="gray30").pack(side="left", padx=10)

def show_add_address_dialog(gui):
    """Dialog to add a new address to an account."""
    if not gui.current_password:
        gui.show_notification("Vault not unlocked", error=True)
        return

    # Collect ALL account names (from pools + accounts_data)
    account_names = gui._get_all_account_names()
    if not account_names:
        gui.show_notification("Create an account first", error=True)
        return

    dialog = ctk.CTkToplevel(gui.root)
    dialog.title("Add New Address")
    dialog.geometry("540x620")
    dialog.minsize(540, 520)
    dialog.transient(gui.root)
    dialog.grab_set()
    gui._center_dialog(dialog)

    ctk.CTkLabel(dialog, text="Add New Address",
                 font=ctk.CTkFont(size=16, weight="bold")).pack(pady=(20, 10))

    # Scrollable form so all fields (and the Save button) remain
    # accessible even if the window/dialog is shorter than the content.
    form = ctk.CTkScrollableFrame(dialog, fg_color="transparent")
    form.pack(pady=10, padx=20, fill="both", expand=True)

    # Account dropdown - default to currently selected account
    ctk.CTkLabel(form, text="Account:").pack(anchor="w")
    default_account = gui.current_account if gui.current_account else account_names[0]
    acct_var = ctk.StringVar(value=default_account)
    acct_menu = ctk.CTkOptionMenu(form, variable=acct_var, values=account_names, width=420,
                                   font=ctk.CTkFont(size=13))
    acct_menu.pack(fill="x", pady=(0, 10))

    # Coin (user-defined, free text)
    ctk.CTkLabel(form, text="Coin (optional):").pack(anchor="w")
    coin_entry = ctk.CTkEntry(form, placeholder_text="e.g. Ethereum, Bitcoin, USDT", width=420)
    coin_entry.pack(fill="x", pady=(0, 10))

    # Standardized Type / Chain dropdown
    ctk.CTkLabel(form, text="Chain (optional):").pack(anchor="w")
    type_var = ctk.StringVar(value="(None)")
    chain_opts = ["(None)"] + CHAIN_OPTIONS
    gui._style_combobox()
    import tkinter.ttk as ttk
    type_combo = ttk.Combobox(form, textvariable=type_var, values=chain_opts,
                              state="readonly", width=55, style="Dark.TCombobox")
    type_combo.pack(fill="x", pady=(0, 10))

    # Custom chain name entry (hidden by default)
    custom_label = ctk.CTkLabel(form, text="Custom Chain Name:")
    custom_entry = ctk.CTkEntry(form, placeholder_text="e.g. KASPA, AVAX", width=420)

    def on_type_selected(event=None):
        if type_var.get() == "Custom...":
            custom_label.pack(anchor="w", pady=(0, 0))
            custom_entry.pack(fill="x", pady=(0, 10))
            custom_entry.configure(state="normal")
            custom_entry.focus_set()
        else:
            custom_label.pack_forget()
            custom_entry.pack_forget()
            custom_entry.delete(0, "end")
            custom_entry.configure(state="disabled")

    type_combo.bind("<<ComboboxSelected>>", on_type_selected)
    # Initialize hidden state
    on_type_selected()

    # Address entry
    ctk.CTkLabel(form, text="Address:").pack(anchor="w")
    address_entry = ctk.CTkEntry(form, placeholder_text="Wallet address", width=420)
    address_entry.pack(fill="x", pady=(0, 10))

    # Notes (optional)
    ctk.CTkLabel(form, text="Notes (optional):").pack(anchor="w")
    notes_entry = ctk.CTkEntry(form, placeholder_text="e.g. Hot wallet, Exchange deposit", width=420)
    notes_entry.pack(fill="x", pady=(0, 10))

    status_label = ctk.CTkLabel(dialog, text="", font=ctk.CTkFont(size=11))
    status_label.pack()

    def do_add():
        account = acct_var.get().strip()
        coin_name = coin_entry.get().strip()
        selected_chain = type_var.get().strip()
        if selected_chain == "Custom...":
            custom_chain = custom_entry.get().strip()
            if not custom_chain:
                status_label.configure(text="Custom chain name is required", text_color="red")
                return
            chain_type = custom_chain
        elif selected_chain == "(None)":
            chain_type = ""
        else:
            chain_type = selected_chain
        address = address_entry.get().strip()
        notes = notes_entry.get().strip()
        if not account or not address:
            status_label.configure(text="Account and address are required", text_color="red")
            return
        if not coin_name and not chain_type:
            status_label.configure(text="Either Coin or Chain must be specified", text_color="red")
            return
        try:
            # Store the standardized type as coin; chain left empty for unified display
            if gui.key_manager.add_address(account, coin_name, chain_type, address,
                                            gui.current_password, notes):
                gui.show_notification(f"Address added to '{account}'")
                dialog.destroy()
                gui.refresh_left_panel()
                # If currently viewing this account, refresh the view
                if gui.current_account == account:
                    gui.select_account(gui.current_pool or "Unassigned", account)
            else:
                status_label.configure(text="Failed to add address", text_color="red")
        except Exception as e:
            status_label.configure(text=f"Error: {e}", text_color="red")

    btn_frame = ctk.CTkFrame(dialog, fg_color="transparent")
    btn_frame.pack(pady=15)
    ctk.CTkButton(btn_frame, text="Save Entry", command=do_add, width=120).pack(side="left", padx=10)
    ctk.CTkButton(btn_frame, text="Cancel", command=dialog.destroy, width=100,
                  fg_color="gray30").pack(side="left", padx=10)

def show_add_mnemonic_dialog(gui):
    """Dialog to add or update a mnemonic for an account."""
    if not gui.current_password:
        gui.show_notification("Vault not unlocked", error=True)
        return

    # Collect ALL account names (from pools + accounts_data)
    account_names = gui._get_all_account_names()
    if not account_names:
        gui.show_notification("Create an account first", error=True)
        return

    dialog = ctk.CTkToplevel(gui.root)
    dialog.title("Add / Update Mnemonic")
    dialog.geometry("500x380")
    dialog.transient(gui.root)
    dialog.grab_set()
    gui._center_dialog(dialog)

    ctk.CTkLabel(dialog, text="Add / Update Recovery Phrase",
                 font=ctk.CTkFont(size=16, weight="bold")).pack(pady=(20, 10))

    form = ctk.CTkFrame(dialog, fg_color="transparent")
    form.pack(pady=10, padx=20, fill="both", expand=True)

    # Account dropdown
    ctk.CTkLabel(form, text="Account:").pack(anchor="w")
    default_account = gui.current_account if gui.current_account else account_names[0]
    acct_var = ctk.StringVar(value=default_account)
    acct_menu = ctk.CTkOptionMenu(form, variable=acct_var, values=account_names, width=420)
    acct_menu.pack(fill="x", pady=(0, 10))

    # Mnemonic entry
    ctk.CTkLabel(form, text="24-Word Mnemonic Phrase:").pack(anchor="w")
    mnemonic_entry = ctk.CTkTextbox(form, height=80, font=ctk.CTkFont(size=12, family="monospace"))
    mnemonic_entry.pack(fill="x", pady=(0, 10))

    # Warning label
    ctk.CTkLabel(form,
                 text="\u26A0 This mnemonic will be encrypted. Keep your master password safe.",
                 font=ctk.CTkFont(size=10), text_color="orange").pack(anchor="w")

    status_label = ctk.CTkLabel(dialog, text="", font=ctk.CTkFont(size=11))
    status_label.pack()

    def do_add():
        account = acct_var.get().strip()
        mnemonic = mnemonic_entry.get("1.0", "end").strip()
        if not account or not mnemonic:
            status_label.configure(text="Account and mnemonic are required", text_color="red")
            return
        try:
            if gui.key_manager.add_mnemonic(account, mnemonic, gui.current_password):
                gui.show_notification(f"Mnemonic saved for '{account}'")
                dialog.destroy()
                # If currently viewing this account, refresh the view
                if gui.current_account == account:
                    gui.select_account(gui.current_pool or "Unassigned", account)
            else:
                status_label.configure(text="Failed to save mnemonic", text_color="red")
        except Exception as e:
            status_label.configure(text=f"Error: {e}", text_color="red")

    btn_frame = ctk.CTkFrame(dialog, fg_color="transparent")
    btn_frame.pack(pady=15)
    ctk.CTkButton(btn_frame, text="Save Mnemonic", command=do_add, width=140).pack(side="left", padx=10)
    ctk.CTkButton(btn_frame, text="Cancel", command=dialog.destroy, width=100,
                  fg_color="gray30").pack(side="left", padx=10)

def show_add_private_key_dialog(gui):
    """Dialog to add a chain-specific private key to an account."""
    if not gui.current_password:
        gui.show_notification("Vault not unlocked", error=True)
        return

    # Collect ALL account names (from pools + accounts_data)
    account_names = gui._get_all_account_names()
    if not account_names:
        gui.show_notification("Create an account first", error=True)
        return

    dialog = ctk.CTkToplevel(gui.root)
    dialog.title("Add Private Key")
    dialog.geometry("500x620")
    dialog.transient(gui.root)
    dialog.grab_set()
    gui._center_dialog(dialog)

    ctk.CTkLabel(dialog, text="Add Private Key",
                 font=ctk.CTkFont(size=16, weight="bold")).pack(pady=(20, 10))

    form = ctk.CTkScrollableFrame(dialog, fg_color="transparent")
    form.pack(pady=10, padx=20, fill="both", expand=True)

    # Account dropdown - default to currently selected account
    ctk.CTkLabel(form, text="Account:").pack(anchor="w")
    default_account = gui.current_account if gui.current_account else account_names[0]
    acct_var = ctk.StringVar(value=default_account)
    acct_menu = ctk.CTkOptionMenu(form, variable=acct_var, values=account_names, width=420)
    acct_menu.pack(fill="x", pady=(0, 10))

    # Private Key Chain dropdown (includes Blank/None + standard chains + Custom)
    PK_CHAIN_OPTIONS = ["Blank/None"] + CHAIN_OPTIONS

    ctk.CTkLabel(form, text="Private Key Chain:").pack(anchor="w")
    pk_chain_var = ctk.StringVar(value=PK_CHAIN_OPTIONS[0])
    import tkinter.ttk as ttk
    gui._style_combobox()
    pk_chain_combo = ttk.Combobox(form, textvariable=pk_chain_var, values=PK_CHAIN_OPTIONS,
                                  state="readonly", width=55, style="Dark.TCombobox")
    pk_chain_combo.pack(fill="x", pady=(0, 10))

    # Custom chain name entry for private key (hidden by default)
    pk_custom_label = ctk.CTkLabel(form, text="Custom Chain Name:")
    pk_custom_entry = ctk.CTkEntry(form, placeholder_text="e.g. KASPA, AVAX", width=420)

    def on_pk_chain_selected(event=None):
        if pk_chain_var.get() == "Custom...":
            pk_custom_label.pack(anchor="w", pady=(0, 0))
            pk_custom_entry.pack(fill="x", pady=(0, 10))
            pk_custom_entry.configure(state="normal")
            pk_custom_entry.focus_set()
        else:
            pk_custom_label.pack_forget()
            pk_custom_entry.pack_forget()
            pk_custom_entry.delete(0, "end")
            pk_custom_entry.configure(state="disabled")

    pk_chain_combo.bind("<<ComboboxSelected>>", on_pk_chain_selected)
    on_pk_chain_selected()

    # Private Key entry (masked)
    pk_label_widget = ctk.CTkLabel(form, text="Private Key:")
    pk_label_widget.pack(anchor="w")
    pk_entry = ctk.CTkEntry(form, placeholder_text="Enter private key", show="\u2022",
                            width=420, font=ctk.CTkFont(size=12, family="monospace"))
    pk_entry.pack(fill="x", pady=(0, 10))

    # Show/hide toggle for the private key field
    def toggle_pk_visibility():
        if pk_entry.cget("show") == "\u2022":
            pk_entry.configure(show="")
            toggle_btn.configure(text="Hide")
        else:
            pk_entry.configure(show="\u2022")
            toggle_btn.configure(text="Show")

    toggle_btn = ctk.CTkButton(form, text="Show", command=toggle_pk_visibility,
                               width=80, height=25, fg_color="gray30")
    toggle_btn.pack(anchor="w", pady=(0, 10))

    # v3: Derive from Mnemonic checkbox
    derive_var = ctk.CTkCheckBox(form, text="Derive from Mnemonic (uses stored mnemonic)")
    derive_var.pack(anchor="w", pady=(0, 5))

    # v5.2.5: Custom mnemonic checkbox (mutually exclusive with vault derive)
    custom_mnemonic_var = ctk.CTkCheckBox(
        form, text="Use Custom Mnemonic (not from vault)")
    custom_mnemonic_var.pack(anchor="w", pady=(0, 5))

    # v3: Derivation fields (hidden by default)
    derive_fields_frame = ctk.CTkFrame(form, fg_color="transparent")

    ctk.CTkLabel(derive_fields_frame, text="Derivation Chain:").pack(anchor="w")
    deriv_chain_var = ctk.StringVar(value=DERIVATION_CHAINS[0])
    gui._style_combobox()
    import tkinter.ttk as ttk
    deriv_chain_combo = ttk.Combobox(derive_fields_frame, textvariable=deriv_chain_var,
                                     values=DERIVATION_CHAINS, state="readonly",
                                     width=55, style="Dark.TCombobox")
    deriv_chain_combo.pack(fill="x", pady=(0, 5))

    ctk.CTkLabel(derive_fields_frame, text="Address Index:").pack(anchor="w")
    deriv_index_entry = ctk.CTkEntry(derive_fields_frame, placeholder_text="0", width=100)
    deriv_index_entry.insert(0, "0")
    deriv_index_entry.pack(anchor="w", pady=(0, 5))

    derive_btn_pk = ctk.CTkButton(derive_fields_frame, text="Derive Key",
                                  command=lambda: None, width=120, height=30,
                                  fg_color=("#fd7e14", "#dc6602"))
    derive_btn_pk.pack(anchor="w", pady=(0, 5))

    derive_status = ctk.CTkLabel(derive_fields_frame, text="", font=ctk.CTkFont(size=10))
    derive_status.pack(anchor="w")

    # Store derived metadata for saving
    derived_meta = {"path": None, "index": None, "address": None, "private_key": None}

    # v5.2.5: Mnemonic info label (shows which mnemonic is being used)
    mnemonic_info_label = ctk.CTkLabel(form, text="", font=ctk.CTkFont(size=10),
                                        text_color="gray60")
    # Packed/unpacked dynamically in on_derive_toggle

    def on_derive_toggle():
        if derive_var.get():
            # Uncheck custom mnemonic if it's checked
            if custom_mnemonic_var.get():
                custom_mnemonic_var.deselect()
                on_custom_mnemonic_toggle()
            acct = acct_var.get().strip()
            mnemonic = gui.key_manager.show_mnemonic(acct)
            if not mnemonic:
                derive_status.configure(text="No mnemonic stored for this account. Add a mnemonic first.", text_color="orange")
                derive_var.deselect()
                return
            # Change the label to show this is mnemonic-derived, not a raw key
            pk_label_widget.configure(text="Private Key: (derived from mnemonic — read-only)")
            pk_entry.configure(state="normal")
            pk_entry.delete(0, "end")
            word_count = len(mnemonic.split())
            pk_entry.insert(0, f"{word_count}-word mnemonic (from vault for '{acct}')")
            pk_entry.configure(state="disabled", placeholder_text="")
            mnemonic_info_label.configure(
                text=f"Using stored mnemonic for '{acct}' ({word_count} words)")
            mnemonic_info_label.pack(anchor="w", pady=(0, 5))
            derive_fields_frame.pack(fill="x", pady=(0, 10))
            derive_status.configure(text="", text_color="gray60")
        else:
            pk_label_widget.configure(text="Private Key:")
            mnemonic_info_label.pack_forget()
            pk_entry.configure(state="normal", placeholder_text="Enter private key")
            pk_entry.delete(0, "end")
            derive_fields_frame.pack_forget()
            derived_meta["path"] = None
            derived_meta["index"] = None
            derived_meta["address"] = None
            derived_meta["private_key"] = None

    derive_var.configure(command=on_derive_toggle)

    def do_derive_pk():
        acct = acct_var.get().strip()
        mnemonic = gui.key_manager.show_mnemonic(acct)
        if not mnemonic:
            derive_status.configure(text="No mnemonic for this account", text_color="red")
            return
        chain = deriv_chain_var.get()
        try:
            idx = int(deriv_index_entry.get() or "0")
        except ValueError:
            idx = 0
        try:
            result = DerivationEngine.derive_from_mnemonic(mnemonic, chain, address_index=idx)
            pk_entry.configure(state="normal")
            pk_entry.delete(0, "end")
            pk_entry.insert(0, result["private_key"])
            pk_entry.configure(state="disabled")
            derived_meta["path"] = result["path"]
            derived_meta["index"] = idx
            derived_meta["address"] = result["address"]
            derived_meta["private_key"] = result["private_key"]
            # For Solana, show base58 private key too (matches Brave export format)
            if "sol" in chain.lower() or "SOL" in chain:
                from ed25519_utils import b58encode
                # Brave/Phantom export 64-byte keypair (seed + pubkey) as base58
                keypair = bytes.fromhex(result["private_key"]) + bytes.fromhex(result["public_key"])
                b58_key = b58encode(keypair)
                derive_status.configure(
                    text=f"Derived: {result['address'][:30]}... | Key (b58): {b58_key[:20]}...",
                    text_color="green")
            else:
                derive_status.configure(text=f"Derived: {result['address'][:30]}...", text_color="green")
        except Exception as e:
            derive_status.configure(text=f"Error: {e}", text_color="red")

    derive_btn_pk.configure(command=do_derive_pk)

    # v5.2.5: Custom mnemonic fields (hidden by default). Derives from a
    # manually-entered mnemonic rather than the vault's stored one — useful
    # when the vault mnemonic produces a different key than expected
    # (e.g. different derivation path used by Phantom vs Solflare).
    custom_mnemonic_frame = ctk.CTkFrame(form, fg_color="transparent")

    ctk.CTkLabel(custom_mnemonic_frame,
                 text="Mnemonic (12/24 words, space-separated):").pack(anchor="w")
    custom_mnemonic_entry = ctk.CTkEntry(
        custom_mnemonic_frame, placeholder_text="word1 word2 word3 ...",
        show="\u2022", width=420,
        font=ctk.CTkFont(size=11, family="monospace"))
    custom_mnemonic_entry.pack(fill="x", pady=(0, 5))

    ctk.CTkLabel(custom_mnemonic_frame,
                 text="Derivation Chain:").pack(anchor="w")
    custom_deriv_chain_var = ctk.StringVar(value=DERIVATION_CHAINS[0])
    gui._style_combobox()
    custom_deriv_chain_combo = ttk.Combobox(
        custom_mnemonic_frame, textvariable=custom_deriv_chain_var,
        values=DERIVATION_CHAINS, state="readonly",
        width=55, style="Dark.TCombobox")
    custom_deriv_chain_combo.pack(fill="x", pady=(0, 5))

    ctk.CTkLabel(custom_mnemonic_frame, text="Address Index:").pack(anchor="w")
    custom_deriv_index_entry = ctk.CTkEntry(
        custom_mnemonic_frame, placeholder_text="0", width=100)
    custom_deriv_index_entry.insert(0, "0")
    custom_deriv_index_entry.pack(anchor="w", pady=(0, 5))

    derive_custom_btn = ctk.CTkButton(
        custom_mnemonic_frame, text="Derive Key & Address",
        width=180, height=30, fg_color=("#fd7e14", "#dc6602"))
    derive_custom_btn.pack(anchor="w", pady=(0, 5))

    custom_derive_status = ctk.CTkLabel(
        custom_mnemonic_frame, text="", font=ctk.CTkFont(size=10))
    custom_derive_status.pack(anchor="w")

    # Store result for custom derivation
    custom_derived_meta = {"path": None, "index": None, "address": None, "private_key": None}

    def on_custom_mnemonic_toggle():
        if custom_mnemonic_var.get():
            # Uncheck vault-derive if it's checked
            if derive_var.get():
                derive_var.deselect()
                on_derive_toggle()  # Reset derive UI
            custom_mnemonic_frame.pack(fill="x", pady=(0, 10))
            pk_entry.configure(state="normal")
            pk_entry.delete(0, "end")
            pk_entry.configure(state="disabled")
            pk_label_widget.configure(
                text="Private Key: (derived from custom mnemonic — read-only)")
        else:
            custom_mnemonic_frame.pack_forget()
            pk_entry.configure(state="normal", placeholder_text="Enter private key")
            pk_label_widget.configure(text="Private Key:")
            custom_derived_meta["path"] = None
            custom_derived_meta["index"] = None
            custom_derived_meta["address"] = None
            custom_derived_meta["private_key"] = None

    custom_mnemonic_var.configure(command=on_custom_mnemonic_toggle)

    def do_derive_custom():
        mnemonic = custom_mnemonic_entry.get().strip()
        if not mnemonic:
            custom_derive_status.configure(
                text="Enter a mnemonic phrase", text_color="red")
            return
        chain = custom_deriv_chain_var.get()
        try:
            idx = int(custom_deriv_index_entry.get() or "0")
        except ValueError:
            idx = 0
        try:
            result = DerivationEngine.derive_from_mnemonic(
                mnemonic, chain, address_index=idx)
            pk_entry.configure(state="normal")
            pk_entry.delete(0, "end")
            pk_entry.insert(0, result["private_key"])
            pk_entry.configure(state="disabled")
            custom_derived_meta["path"] = result["path"]
            custom_derived_meta["index"] = idx
            custom_derived_meta["address"] = result["address"]
            custom_derived_meta["private_key"] = result["private_key"]
            # For Solana, show base58 private key too (matches Brave export format)
            if "sol" in chain.lower() or "SOL" in chain:
                from ed25519_utils import b58encode
                # Brave/Phantom export 64-byte keypair (seed + pubkey) as base58
                keypair = bytes.fromhex(result["private_key"]) + bytes.fromhex(result["public_key"])
                b58_key = b58encode(keypair)
                custom_derive_status.configure(
                    text=f"Derived: {result['address'][:44]}... | Key (b58): {b58_key[:20]}...",
                    text_color="green")
            else:
                custom_derive_status.configure(
                    text=f"Derived: {result['address'][:44]}...",
                    text_color="green")
        except Exception as e:
            custom_derive_status.configure(text=f"Error: {e}", text_color="red")

    derive_custom_btn.configure(command=do_derive_custom)

    # Warning label
    ctk.CTkLabel(form,
                 text="\u26A0 This private key will be encrypted. Keep your master password safe.",
                 font=ctk.CTkFont(size=10), text_color="orange").pack(anchor="w")

    status_label = ctk.CTkLabel(dialog, text="", font=ctk.CTkFont(size=11))
    status_label.pack()

    def do_add():
        account = acct_var.get().strip()
        selected_chain = pk_chain_var.get().strip()
        if selected_chain == "Custom...":
            custom_chain = pk_custom_entry.get().strip()
            if not custom_chain:
                status_label.configure(text="Custom chain name is required", text_color="red")
                return
            chain_label = custom_chain
        elif selected_chain == "Blank/None":
            chain_label = ""
        else:
            chain_label = selected_chain
        try:
            # v5.2.5: Custom mnemonic derivation — only the derived private
            # key is saved (encrypted); the custom mnemonic is never stored.
            if custom_mnemonic_var.get():
                if not custom_derived_meta["private_key"]:
                    status_label.configure(
                        text="Derive a key from your custom mnemonic first", text_color="red")
                    return
                private_key = custom_derived_meta["private_key"]
                success = gui.key_manager.add_private_key(
                    account, private_key, gui.current_password, chain_label,
                    source="derived",
                    derivation_path=custom_derived_meta["path"],
                    address_index=custom_derived_meta["index"],
                    derived_address=custom_derived_meta["address"])
            else:
                # v5.2.5: Vault-mnemonic derive must run "Derive Key" first —
                # the entry holds descriptive text until a key is derived.
                # Store/use the key in derived_meta (mirrors custom path) to
                # avoid CTkEntry placeholder bugs returning empty string.
                if derive_var.get():
                    if not derived_meta["private_key"]:
                        status_label.configure(
                            text="Click 'Derive Key' to derive the private key first",
                            text_color="red")
                        return
                    private_key = derived_meta["private_key"]
                    success = gui.key_manager.add_private_key(
                        account, private_key, gui.current_password, chain_label,
                        source="derived",
                        derivation_path=derived_meta["path"],
                        address_index=derived_meta["index"],
                        derived_address=derived_meta["address"])
                else:
                    # Raw private key path — read from the entry widget
                    private_key = pk_entry.get().strip()
                    if not account or not private_key:
                        status_label.configure(text="Account and private key are required", text_color="red")
                        return
                    success = gui.key_manager.add_private_key(
                        account, private_key, gui.current_password, chain_label)
            if success:
                gui.show_notification(f"Private key added to '{account}'")
                dialog.destroy()
                # If currently viewing this account, refresh the view
                if gui.current_account == account:
                    gui.select_account(gui.current_pool or "Unassigned", account)
            else:
                status_label.configure(text="Failed to add private key", text_color="red")
        except Exception as e:
            status_label.configure(text=f"Error: {e}", text_color="red")

    btn_frame = ctk.CTkFrame(dialog, fg_color="transparent")
    btn_frame.pack(pady=15)
    ctk.CTkButton(btn_frame, text="Save Private Key", command=do_add, width=140).pack(side="left", padx=10)
    ctk.CTkButton(btn_frame, text="Cancel", command=dialog.destroy, width=100,
                  fg_color="gray30").pack(side="left", padx=10)

def show_derivation_dialog(gui, account_name):
    """Open a dialog to derive addresses from the account's stored mnemonic."""
    mnemonic = gui.key_manager.show_mnemonic(account_name)
    if not mnemonic:
        gui.show_notification("No mnemonic stored for this account", error=True)
        return

    dialog = ctk.CTkToplevel(gui.root)
    dialog.title("Derive Addresses from Mnemonic")
    dialog.geometry("600x550")
    dialog.transient(gui.root)
    dialog.grab_set()
    gui._center_dialog(dialog)

    ctk.CTkLabel(dialog, text="Derive Addresses from Mnemonic",
                 font=ctk.CTkFont(size=16, weight="bold")).pack(pady=(20, 5))
    ctk.CTkLabel(dialog, text=f"Account: {account_name}",
                 font=ctk.CTkFont(size=12), text_color="gray60").pack(pady=(0, 10))

    form = ctk.CTkScrollableFrame(dialog, fg_color="transparent")
    form.pack(pady=10, padx=20, fill="both", expand=True)

    # Chain dropdown
    ctk.CTkLabel(form, text="Chain:").pack(anchor="w")
    chain_var = ctk.StringVar(value=DERIVATION_CHAINS[0])
    gui._style_combobox()
    import tkinter.ttk as ttk
    chain_combo = ttk.Combobox(form, textvariable=chain_var, values=DERIVATION_CHAINS,
                                state="readonly", width=55, style="Dark.TCombobox")
    chain_combo.pack(fill="x", pady=(0, 10))

    # Derivation path (auto-populated, editable)
    ctk.CTkLabel(form, text="Derivation Path:").pack(anchor="w")
    path_entry = ctk.CTkEntry(form, width=420, font=ctk.CTkFont(size=12, family="monospace"))
    path_entry.pack(fill="x", pady=(0, 5))
    path_hint = ctk.CTkLabel(
        form,
        text="Tip: If the derived address doesn't match your wallet, "
             "try a different Solana variant from the chain dropdown",
        font=ctk.CTkFont(size=10), text_color="gray60"
    )
    path_hint.pack(anchor="w", pady=(0, 5))

    def update_path(event=None):
        ch = chain_var.get()
        cfg = DerivationEngine.SUPPORTED_CHAINS.get(ch, {})
        path_entry.delete(0, "end")
        path_entry.insert(0, cfg.get("path", ""))

    chain_combo.bind("<<ComboboxSelected>>", update_path)
    update_path()

    # Address index
    ctk.CTkLabel(form, text="Address Index:").pack(anchor="w")
    index_entry = ctk.CTkEntry(form, placeholder_text="0", width=100)
    index_entry.insert(0, "0")
    index_entry.pack(anchor="w", pady=(0, 10))

    # Results frame
    result_frame = ctk.CTkFrame(form, fg_color="transparent")
    result_frame.pack(fill="x", pady=(10, 0))

    addr_label = ctk.CTkLabel(result_frame, text="", font=ctk.CTkFont(size=11), wraplength=500)
    addr_label.pack(anchor="w")
    # Private key label is intentionally masked for security — the derived
    # private key is never shown in the derivation dialog. It is saved
    # encrypted to the vault on "Save to Account". The user can reveal it
    # later from the account's Private Keys section (password-protected).
    pk_label = ctk.CTkLabel(result_frame, text="", font=ctk.CTkFont(size=11),
                            wraplength=500, text_color="gray60")
    pk_label.pack(anchor="w", pady=(5, 0))

    status_label = ctk.CTkLabel(dialog, text="", font=ctk.CTkFont(size=11))
    status_label.pack()

    last_result = {"data": None}

    def do_derive():
        ch = chain_var.get()
        try:
            idx = int(index_entry.get() or "0")
        except ValueError:
            idx = 0
        # v5.2.5: Read the custom derivation path if the user modified it.
        # The path is used for reporting and may also encode account/address
        # indices for Solana variants.
        custom_path = path_entry.get().strip() or None
        try:
            result = DerivationEngine.derive_from_mnemonic(
                mnemonic, ch, path=custom_path, address_index=idx
            )
            last_result["data"] = result
            addr_label.configure(text=f"Address: {result['address']}", text_color="#51cf94")
            # SECURITY: Never display the derived private key. Show a masked
            # placeholder so the user knows a key was derived, but the key
            # itself is only stored encrypted in the vault.
            pk_label.configure(text="Private Key: •••••••••••••••• (hidden for security)", text_color="gray60")
            status_label.configure(text="Derived successfully — use Save to Account to store the key encrypted", text_color="green")
        except Exception as e:
            status_label.configure(text=f"Error: {e}", text_color="red")

    def do_save():
        if not last_result["data"]:
            status_label.configure(text="Derive an address first", text_color="orange")
            return
        r = last_result["data"]
        try:
            gui.key_manager.add_address(
                account_name, r["chain"], r["chain"], r["address"],
                gui.current_password, notes="Derived",
                derivation_path=r["path"], source="derived")
            gui.key_manager.add_private_key(
                account_name, r["private_key"], gui.current_password, r["chain"],
                source="derived", derivation_path=r["path"],
                derived_address=r["address"])
            gui.show_notification(f"Derived address+key saved to '{account_name}'")
            status_label.configure(text="Saved!", text_color="green")
            gui.refresh_left_panel()
            if gui.current_account == account_name:
                gui.select_account(gui.current_pool or "Unassigned", account_name)
        except Exception as e:
            status_label.configure(text=f"Save error: {e}", text_color="red")

    def do_derive_another():
        try:
            idx = int(index_entry.get() or "0") + 1
        except ValueError:
            idx = 1
        index_entry.delete(0, "end")
        index_entry.insert(0, str(idx))
        do_derive()

    btn_frame = ctk.CTkFrame(dialog, fg_color="transparent")
    btn_frame.pack(pady=15)
    ctk.CTkButton(btn_frame, text="Derive", command=do_derive, width=100).pack(side="left", padx=5)
    ctk.CTkButton(btn_frame, text="Save to Account", command=do_save, width=140,
                  fg_color=("#28a745", "#1e7e34")).pack(side="left", padx=5)
    ctk.CTkButton(btn_frame, text="Derive Another", command=do_derive_another, width=120,
                  fg_color=("#007bff", "#0056b3")).pack(side="left", padx=5)
    ctk.CTkButton(btn_frame, text="Close", command=dialog.destroy, width=80,
                  fg_color="gray30").pack(side="left", padx=5)

    dialog.after(300, do_derive)

def show_derive_all_chains_dialog(gui, account_name):
    """Derive addresses for all supported chains and show a summary."""
    mnemonic = gui.key_manager.show_mnemonic(account_name)
    if not mnemonic:
        gui.show_notification("No mnemonic stored for this account", error=True)
        return

    dialog = ctk.CTkToplevel(gui.root)
    dialog.title("Derive All Chains")
    dialog.geometry("650x600")
    dialog.transient(gui.root)
    dialog.grab_set()
    gui._center_dialog(dialog)

    ctk.CTkLabel(dialog, text="Derive All Chains from Mnemonic",
                 font=ctk.CTkFont(size=16, weight="bold")).pack(pady=(20, 5))
    ctk.CTkLabel(dialog, text=f"Account: {account_name}",
                 font=ctk.CTkFont(size=12), text_color="gray60").pack(pady=(0, 10))

    scroll = ctk.CTkScrollableFrame(dialog, fg_color="transparent")
    scroll.pack(pady=10, padx=20, fill="both", expand=True)

    status_label = ctk.CTkLabel(dialog, text="Deriving...", font=ctk.CTkFont(size=11), text_color="yellow")
    status_label.pack()

    results = {}

    def do_derive_all():
        results.clear()
        for widget in scroll.winfo_children():
            widget.destroy()
        chains = list(DerivationEngine.SUPPORTED_CHAINS.keys())
        total = len(chains)
        
        def derive_next(idx=0):
            if idx >= total:
                status_label.configure(text=f"Derived {len(results)} chains", text_color="green")
                return
            chain = chains[idx]
            status_label.configure(text=f"Deriving {idx+1}/{total}: {chain}...", text_color="yellow")
            try:
                data = DerivationEngine.derive_from_mnemonic(mnemonic, chain)
                results[chain] = data
                row = ctk.CTkFrame(scroll, corner_radius=8)
                row.pack(fill="x", pady=3, padx=5)
                info = ctk.CTkFrame(row, fg_color="transparent")
                info.pack(side="left", fill="both", expand=True, padx=10, pady=8)
                ctk.CTkLabel(info, text=chain, font=ctk.CTkFont(size=12, weight="bold")).pack(anchor="w")
                ctk.CTkLabel(info, text=data["address"], font=ctk.CTkFont(size=10), wraplength=400).pack(anchor="w")
                ctk.CTkButton(row, text="Save", width=70, height=28,
                              command=lambda c=chain, d=data: gui._save_derived_to_account(
                                  account_name, c, d, status_label)).pack(side="right", padx=10, pady=8)
            except Exception as e:
                ctk.CTkLabel(scroll, text=f"{chain}: ERROR - {e}",
                             font=ctk.CTkFont(size=11), text_color="red").pack(anchor="w", pady=2)
            dialog.after(50, lambda: derive_next(idx + 1))
        
        derive_next(0)

    btn_frame = ctk.CTkFrame(dialog, fg_color="transparent")
    btn_frame.pack(pady=15)
    ctk.CTkButton(btn_frame, text="Close", command=dialog.destroy, width=100,
                  fg_color="gray30").pack(side="left", padx=5)

    dialog.after(300, do_derive_all)

def _save_derived_to_account(gui, account_name, chain, data, status_label):
    """Save a derived address+key to the account."""
    try:
        gui.key_manager.add_address(
            account_name, chain, chain, data["address"],
            gui.current_password, notes="Derived",
            derivation_path=data["path"], source="derived")
        gui.key_manager.add_private_key(
            account_name, data["private_key"], gui.current_password, chain,
            source="derived", derivation_path=data["path"],
            derived_address=data["address"])
        gui.show_notification(f"Saved {chain} to '{account_name}'")
        status_label.configure(text=f"Saved {chain}", text_color="green")
        gui.refresh_left_panel()
        if gui.current_account == account_name:
            gui.select_account(gui.current_pool or "Unassigned", account_name)
    except Exception as e:
        status_label.configure(text=f"Error: {e}", text_color="red")

def show_init_vault_dialog(gui):
    """Dialog to initialize a new vault from the GUI."""
    dialog = ctk.CTkToplevel(gui.root)
    dialog.title("Initialize New Vault")
    dialog.geometry("450x300")
    dialog.transient(gui.root)
    dialog.grab_set()
    gui._center_dialog(dialog)

    ctk.CTkLabel(dialog, text="Initialize New Vault",
                 font=ctk.CTkFont(size=16, weight="bold")).pack(pady=(20, 10))

    form = ctk.CTkFrame(dialog, fg_color="transparent")
    form.pack(pady=10, padx=20, fill="x")

    ctk.CTkLabel(form, text="Create a Master Password:").pack(anchor="w")
    pw_entry = ctk.CTkEntry(form, placeholder_text="Enter new master password", show="\u2022",
                            width=380, font=ctk.CTkFont(size=13))
    pw_entry.pack(fill="x", pady=(0, 10))
    pw_entry.focus_set()

    ctk.CTkLabel(form, text="Confirm Password:").pack(anchor="w")
    pw_confirm = ctk.CTkEntry(form, placeholder_text="Re-enter master password", show="\u2022",
                              width=380, font=ctk.CTkFont(size=13))
    pw_confirm.pack(fill="x", pady=(0, 10))

    ctk.CTkLabel(form,
                 text="\u26A0 Choose a strong password. There is NO recovery if you lose it.",
                 font=ctk.CTkFont(size=10), text_color="orange").pack(anchor="w")

    status_label = ctk.CTkLabel(dialog, text="", font=ctk.CTkFont(size=11))
    status_label.pack()

    def do_init():
        pw = pw_entry.get()
        pw2 = pw_confirm.get()
        if not pw:
            status_label.configure(text="Password is required", text_color="red")
            return
        if pw != pw2:
            status_label.configure(text="Passwords do not match", text_color="red")
            return
        try:
            if gui.key_manager.initialize_vault(pw):
                gui.current_password = pw
                gui.key_manager.create_session(pw)
                gui.show_notification("Vault initialized successfully")
                dialog.destroy()
                gui.root.after(500, gui.create_main_dashboard)
            else:
                status_label.configure(text="Failed to initialize vault", text_color="red")
        except Exception as e:
            status_label.configure(text=f"Error: {e}", text_color="red")

    pw_confirm.bind("<Return>", lambda e: do_init())
    btn_frame = ctk.CTkFrame(dialog, fg_color="transparent")
    btn_frame.pack(pady=15)
    ctk.CTkButton(btn_frame, text="Create Vault", command=do_init, width=120,
                  fg_color=("#28a745", "#1e7e34")).pack(side="left", padx=10)
    ctk.CTkButton(btn_frame, text="Cancel", command=dialog.destroy, width=100,
                  fg_color="gray30").pack(side="left", padx=10)

def show_change_password_dialog(gui):
    """Dialog to change the vault master password."""
    if not gui.current_password:
        gui.show_notification("Vault not unlocked", error=True)
        return

    dialog = ctk.CTkToplevel(gui.root)
    dialog.title("Change Password")
    dialog.geometry("450x350")
    dialog.transient(gui.root)
    dialog.grab_set()
    gui._center_dialog(dialog)

    ctk.CTkLabel(dialog, text="Change Master Password",
                 font=ctk.CTkFont(size=16, weight="bold")).pack(pady=(20, 10))

    form = ctk.CTkFrame(dialog, fg_color="transparent")
    form.pack(pady=10, padx=20, fill="x")

    ctk.CTkLabel(form, text="Current Password:").pack(anchor="w")
    old_entry = ctk.CTkEntry(form, placeholder_text="Enter current password", show="\u2022",
                             width=380, font=ctk.CTkFont(size=13))
    old_entry.pack(fill="x", pady=(0, 10))
    old_entry.focus_set()

    ctk.CTkLabel(form, text="New Password:").pack(anchor="w")
    new_entry = ctk.CTkEntry(form, placeholder_text="Enter new password", show="\u2022",
                            width=380, font=ctk.CTkFont(size=13))
    new_entry.pack(fill="x", pady=(0, 10))

    ctk.CTkLabel(form, text="Confirm New Password:").pack(anchor="w")
    confirm_entry = ctk.CTkEntry(form, placeholder_text="Re-enter new password", show="\u2022",
                                 width=380, font=ctk.CTkFont(size=13))
    confirm_entry.pack(fill="x", pady=(0, 10))

    status_label = ctk.CTkLabel(dialog, text="", font=ctk.CTkFont(size=11))
    status_label.pack()

    def do_change():
        old_pw = old_entry.get()
        new_pw = new_entry.get()
        confirm_pw = confirm_entry.get()
        if not old_pw or not new_pw:
            status_label.configure(text="All fields are required", text_color="red")
            return
        if old_pw != gui.current_password:
            status_label.configure(text="Current password is incorrect", text_color="red")
            return
        if new_pw != confirm_pw:
            status_label.configure(text="New passwords do not match", text_color="red")
            return
        try:
            if gui.key_manager.change_password(old_pw, new_pw):
                gui.current_password = new_pw
                gui.show_notification("Password changed successfully")
                dialog.destroy()
            else:
                status_label.configure(text="Failed to change password", text_color="red")
        except Exception as e:
            status_label.configure(text=f"Error: {e}", text_color="red")

    btn_frame = ctk.CTkFrame(dialog, fg_color="transparent")
    btn_frame.pack(pady=15)
    ctk.CTkButton(btn_frame, text="Change Password", command=do_change, width=130).pack(side="left", padx=10)
    ctk.CTkButton(btn_frame, text="Cancel", command=dialog.destroy, width=100,
                  fg_color="gray30").pack(side="left", padx=10)

def show_import_dialog(gui):
    """Dialog to import addresses from CSV or Excel."""
    if not gui.current_password:
        gui.show_notification("Vault not unlocked", error=True)
        return

    from tkinter import filedialog

    file_path = filedialog.askopenfilename(
        title="Select CSV or Excel file to import",
        filetypes=[
            ("CSV and Excel files", "*.csv *.xlsx *.xls"),
            ("CSV files", "*.csv"),
            ("Excel files", "*.xlsx *.xls"),
            ("All files", "*.*"),
        ]
    )
    if not file_path:
        return

    dialog = ctk.CTkToplevel(gui.root)
    dialog.title("Import Addresses")
    dialog.geometry("520x420")
    dialog.transient(gui.root)
    dialog.grab_set()
    gui._center_dialog(dialog)

    ctk.CTkLabel(dialog, text="Import Addresses",
                 font=ctk.CTkFont(size=16, weight="bold")).pack(pady=(20, 10))

    ctk.CTkLabel(dialog, text=f"Selected: {file_path}",
                 font=ctk.CTkFont(size=11), text_color="gray60").pack(pady=(0, 5))

    # Formatting guide
    guide_text = (
        "\nRequired columns: Account, Address\n"
        "Optional: Coin, Chain, Notes\n\n"
        "Valid Chain values (match Chain dropdown exactly):\n"
        "  BTC Taproot (bc1p), BTC SegWit (bc1q), BTC (Bitcoin)\n"
        "  EVM (Ethereum / Arbitrum / Base), EVM Railgun\n"
        "  SOL (Solana), ZEC (Zcash), ZEC Transparent, ZEC Orchard\n"
        "  XMR (Monero), DASH (Dash), RUNE (THORChain)\n"
        "  SUI (Sui), TRON (Tron), ATOM (Cosmos), DOT (Polkadot)\n"
        "  ADA (Cardano), XRP (Ripple), SCRT (Secret Network)\n"
        "  Or any custom chain name"
    )
    guide_label = ctk.CTkLabel(dialog, text=guide_text,
                               font=ctk.CTkFont(size=10), text_color="gray70",
                               justify="left", anchor="w")
    guide_label.pack(pady=5, padx=20, fill="x")

    status_label = ctk.CTkLabel(dialog, text="", font=ctk.CTkFont(size=11))
    status_label.pack()

    def do_import():
        try:
            added, skipped, errors = gui.key_manager.import_file(file_path, gui.current_password)
            msg = f"Imported {added} addresses"
            if skipped > 0:
                msg += f", skipped {skipped}"
            gui.show_notification(msg)
            if errors:
                status_label.configure(text=f"Errors: {len(errors)}", text_color="red")
            else:
                status_label.configure(text=msg, text_color="green")
            dialog.destroy()
            gui.refresh_left_panel()
            if gui.current_account:
                gui.select_account(gui.current_pool or "Unassigned", gui.current_account)
        except Exception as e:
            status_label.configure(text=f"Error: {e}", text_color="red")

    btn_frame = ctk.CTkFrame(dialog, fg_color="transparent")
    btn_frame.pack(pady=15)
    ctk.CTkButton(btn_frame, text="Import Now", command=do_import, width=120).pack(side="left", padx=10)
    ctk.CTkButton(btn_frame, text="Cancel", command=dialog.destroy, width=100,
                  fg_color="gray30").pack(side="left", padx=10)
