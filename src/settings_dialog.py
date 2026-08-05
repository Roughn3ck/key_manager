"""ColdStack settings dialog (extracted from gui_main_v5.py in v5.1.4).

This module contains the settings dialog that was previously embedded in the
main GUI file.  It is intentionally self-contained: it receives the
ColdStackGUI instance and builds/operates the dialog without touching internal
private state directly.
"""
import tkinter as tk
import tkinter.ttk as ttk
from tkinter import messagebox

import customtkinter as ctk

from price_engine import DISPLAY_CURRENCY_OPTIONS
from rpc_config import load_rpc_config, save_rpc_config, get_default_for_chain


def _center_dialog(gui, dialog: tk.Toplevel) -> None:
    """Center a dialog over the main window."""
    dialog.update_idletasks()
    width = dialog.winfo_width()
    height = dialog.winfo_height()
    x = (gui.root.winfo_width() // 2) - (width // 2) + gui.root.winfo_x()
    y = (gui.root.winfo_height() // 2) - (height // 2) + gui.root.winfo_y()
    dialog.geometry(f"{width}x{height}+{x}+{y}")


def _style_combobox(style_name: str = "Dark.TCombobox") -> None:
    """Apply theme to ttk Combobox widgets — adapts to current appearance mode."""
    mode = ctk.get_appearance_mode().lower()
    if mode == "light":
        bg = "#f0f0f0"
        fg = "#1a1a1a"
        arrow = "#333333"
    else:
        bg = "#2b2b2b"
        fg = "white"
        arrow = "white"
    style = ttk.Style()
    style.theme_use("default")
    style.configure(
        style_name,
        fieldbackground=bg,
        background=bg,
        foreground=fg,
        arrowcolor=arrow,
        borderwidth=1,
        relief="flat",
        padding=3,
    )
    style.map(
        style_name,
        fieldbackground=[("readonly", bg)],
        selectbackground=[("readonly", bg)],
        selectforeground=[("readonly", fg)],
    )


def open_settings_dialog(gui) -> None:
    """Open the ColdStack settings dialog.

    Args:
        gui: The ColdStackGUI instance.  All state and callbacks are accessed
            through this object so the dialog can remain independent.
    """
    dialog = ctk.CTkToplevel(gui.root)
    dialog.title("Settings")
    dialog.transient(gui.root)
    dialog.grab_set()
    _center_dialog(gui, dialog)

    # Set dialog size based on mode
    if gui.app_mode == "advanced":
        dialog.geometry("600x700")
    else:
        dialog.geometry("480x450")

    ctk.CTkLabel(dialog, text="Settings",
                 font=ctk.CTkFont(size=18, weight="bold")).pack(pady=(20, 10))

    # Use a scrollable form for advanced mode
    form = ctk.CTkScrollableFrame(dialog, fg_color="transparent")
    form.pack(pady=5, padx=20, fill="both", expand=True)

    # --- Go Online toggle (both modes) ---
    online_frame = ctk.CTkFrame(form, fg_color="transparent")
    online_frame.pack(fill="x", pady=(0, 15))

    ctk.CTkLabel(online_frame, text="Go Online",
                 font=ctk.CTkFont(size=14, weight="bold")).pack(anchor="w")

    ctk.CTkLabel(online_frame,
        text="When ON: enables read-only balance fetching and price feeds.\n"
             "Only public addresses are queried. Private keys NEVER leave the vault.",
        font=ctk.CTkFont(size=10), text_color="gray60", justify="left").pack(
            anchor="w", pady=(2, 5))

    online_switch = ctk.CTkSwitch(online_frame, text="Online Mode",
                                  command=gui._on_online_toggle)
    if gui.online_mode:
        online_switch.select()
    online_switch.pack(anchor="w")

    # --- Appearance mode (v5.2.1) — both modes ---
    appearance_frame = ctk.CTkFrame(form, fg_color="transparent")
    appearance_frame.pack(fill="x", pady=(0, 15))

    ctk.CTkLabel(appearance_frame, text="Appearance",
                 font=ctk.CTkFont(size=14, weight="bold")).pack(anchor="w")
    ctk.CTkLabel(appearance_frame,
        text="Switch between light and dark interface.",
        font=ctk.CTkFont(size=10), text_color="gray60").pack(anchor="w", pady=(2, 5))

    appearance_var = ctk.StringVar(value=gui.appearance_mode.capitalize())

    appearance_seg = ctk.CTkSegmentedButton(
        appearance_frame,
        values=["Dark", "Light", "System"],
        variable=appearance_var,
        command=lambda v: ctk.set_appearance_mode(v.lower()),
    )
    appearance_seg.pack(anchor="w", pady=(0, 5))

    # --- Display currency selection (both modes) ---
    currency_frame = ctk.CTkFrame(form, fg_color="transparent")
    currency_frame.pack(fill="x", pady=(0, 15))

    ctk.CTkLabel(currency_frame, text="Default Currency",
                 font=ctk.CTkFont(size=14, weight="bold")).pack(anchor="w")
    ctk.CTkLabel(currency_frame,
        text="Show fiat equivalent alongside native balance.",
        font=ctk.CTkFont(size=10), text_color="gray60").pack(anchor="w", pady=(2, 5))

    currency_values = [label for label, _ in DISPLAY_CURRENCY_OPTIONS]
    default_curr_label = "None (native only)"
    for label, val in DISPLAY_CURRENCY_OPTIONS:
        if val == gui.display_currency:
            default_curr_label = label
            break
    curr_var = ctk.StringVar(value=default_curr_label)

    _style_combobox()
    curr_combo = ttk.Combobox(currency_frame, textvariable=curr_var,
                              values=currency_values, state="readonly",
                              width=45, style="Dark.TCombobox")
    curr_combo.pack(anchor="w", pady=(0, 5))

    # --- Advanced mode sections ---
    # Store references for saving
    rpc_url_vars = {}
    api_key_vars = {}

    if gui.app_mode == "advanced":
        # --- RPC Endpoints section ---
        rpc_section = ctk.CTkFrame(form, fg_color="transparent")
        rpc_section.pack(fill="x", pady=(10, 5))

        ctk.CTkLabel(rpc_section, text="--- RPC Endpoints ---",
                     font=ctk.CTkFont(size=14, weight="bold")).pack(anchor="w")
        ctk.CTkLabel(rpc_section,
            text="Customize public RPC URLs for each chain. These are NOT secrets.\n"
                 "API keys are stored separately in the encrypted vault.",
            font=ctk.CTkFont(size=10), text_color="gray60").pack(
                anchor="w", pady=(2, 8))

        # Scrollable list of chain -> URL entries
        rpc_list_frame = ctk.CTkFrame(rpc_section, fg_color="transparent")
        rpc_list_frame.pack(fill="x", pady=(0, 5))

        if gui.rpc_config is None:
            gui.rpc_config = load_rpc_config(gui.base_dir)

        for chain_id in sorted(gui.rpc_config.keys()):
            entry = gui.rpc_config[chain_id]
            url = entry.get("url", "")
            row = ctk.CTkFrame(rpc_list_frame, fg_color="transparent")
            row.pack(fill="x", pady=2)

            ctk.CTkLabel(row, text=chain_id, width=120, anchor="w",
                         font=ctk.CTkFont(size=11)).pack(side="left", padx=(0, 5))

            url_var = ctk.StringVar(value=url)
            rpc_url_vars[chain_id] = url_var
            url_entry = ctk.CTkEntry(row, textvariable=url_var, width=380,
                                     font=ctk.CTkFont(size=10))
            url_entry.pack(side="left", fill="x", expand=True, padx=(0, 5))

            def make_reset_fn(cid, uvar):
                def do_reset():
                    default = get_default_for_chain(cid)
                    if default:
                        uvar.set(default.get("url", ""))
                return do_reset

            reset_btn = ctk.CTkButton(row, text="Reset", width=50, height=22,
                                      command=make_reset_fn(chain_id, url_var),
                                      font=ctk.CTkFont(size=9), fg_color="gray30")
            reset_btn.pack(side="left")

        # Reset All to Defaults button
        def reset_all_rpc():
            for cid, uvar in rpc_url_vars.items():
                default = get_default_for_chain(cid)
                if default:
                    uvar.set(default.get("url", ""))

        ctk.CTkButton(rpc_section, text="Reset All to Defaults", command=reset_all_rpc,
                      width=150, height=28, fg_color="gray30",
                      font=ctk.CTkFont(size=11)).pack(pady=(5, 10))

        # --- API Keys section ---
        api_section = ctk.CTkFrame(form, fg_color="transparent")
        api_section.pack(fill="x", pady=(10, 5))

        ctk.CTkLabel(api_section, text="--- API Keys (encrypted in vault) ---",
                     font=ctk.CTkFont(size=14, weight="bold")).pack(anchor="w")
        ctk.CTkLabel(api_section,
            text="API keys for premium RPC providers. Stored in the encrypted vault,\n"
                 "never written to rpc_endpoints.json.",
            font=ctk.CTkFont(size=10), text_color="gray60").pack(
                anchor="w", pady=(2, 8))

        api_key_providers = ["helius", "infura", "alchemy", "quicknode"]
        api_key_labels = {
            "helius": "Helius (Solana)",
            "infura": "Infura (EVM)",
            "alchemy": "Alchemy (EVM)",
            "quicknode": "Quicknode (Multi-chain)",
        }

        for provider in api_key_providers:
            row = ctk.CTkFrame(api_section, fg_color="transparent")
            row.pack(fill="x", pady=3)

            ctk.CTkLabel(row, text=api_key_labels[provider], width=150, anchor="w",
                         font=ctk.CTkFont(size=11)).pack(side="left", padx=(0, 5))

            current_key = gui.api_keys.get(provider, "")
            key_var = ctk.StringVar(value=current_key)
            api_key_vars[provider] = key_var
            key_entry = ctk.CTkEntry(row, textvariable=key_var, width=300,
                                     show="*", font=ctk.CTkFont(size=10))
            key_entry.pack(side="left", fill="x", expand=True, padx=(0, 5))

            def make_toggle_fn2(kentry, kbtn_ref):
                def toggle():
                    if kentry.cget("show") == "*":
                        kentry.configure(show="")
                        kbtn_ref.configure(text="Hide")
                    else:
                        kentry.configure(show="*")
                        kbtn_ref.configure(text="Show")
                return toggle

            show_btn = ctk.CTkButton(row, text="Show", width=50, height=22,
                                     font=ctk.CTkFont(size=9), fg_color="gray30",
                                     command=make_toggle_fn2(key_entry, None))
            show_btn.configure(command=make_toggle_fn2(key_entry, show_btn))
            show_btn.pack(side="left")

        # Switch to Standard button
        def switch_to_standard():
            confirm = messagebox.askyesno(
                "Switch to Standard Mode",
                "Standard mode hides RPC endpoint editing and API key fields.\n\n"
                "Your custom RPC URLs in rpc_endpoints.json will be preserved.\n"
                "API keys remain stored in the encrypted vault.\n\n"
                "Continue?",
                parent=dialog
            )
            if confirm:
                gui.app_mode = "standard"
                gui._save_vault_config()
                dialog.destroy()
                gui.show_settings_dialog()
                # Re-render LP cards to show/hide advanced buttons
                if hasattr(gui, '_lp_rerender_cards'):
                    gui._lp_rerender_cards()

        ctk.CTkButton(form, text="Switch to Standard", command=switch_to_standard,
                      width=160, height=30, fg_color="gray30",
                      font=ctk.CTkFont(size=12)).pack(pady=(15, 5))

    else:
        # Standard mode: Switch to Advanced button
        def switch_to_advanced():
            confirm = messagebox.askyesno(
                "Switch to Advanced Mode",
                "Advanced mode unlocks custom RPC endpoints and API key configuration.\n\n"
                "Your existing data is safe. This only reveals additional settings.\n\n"
                "Continue?",
                parent=dialog
            )
            if confirm:
                gui.app_mode = "advanced"
                gui._save_vault_config()
                dialog.destroy()
                gui.show_settings_dialog()
                # Re-render LP cards to show/hide advanced buttons
                if hasattr(gui, '_lp_rerender_cards'):
                    gui._lp_rerender_cards()

        ctk.CTkButton(form, text="Switch to Advanced", command=switch_to_advanced,
                      width=160, height=30, fg_color=("#007bff", "#0056b3"),
                      font=ctk.CTkFont(size=12)).pack(pady=(15, 5))

    # --- Save / Cancel buttons (both modes) ---
    status_label = ctk.CTkLabel(dialog, text="", font=ctk.CTkFont(size=11))
    status_label.pack()

    def do_save():
        # Save currency
        for label, val in DISPLAY_CURRENCY_OPTIONS:
            if curr_var.get() == label:
                gui.display_currency = val
                break

        # v5.2.1: Save appearance mode
        gui.appearance_mode = appearance_var.get().lower()

        # v4.2: If advanced mode, save RPC URLs and API keys
        if gui.app_mode == "advanced":
            # Update rpc_config with edited URLs
            if gui.rpc_config is None:
                gui.rpc_config = load_rpc_config(gui.base_dir)
            for chain_id, url_var in rpc_url_vars.items():
                if chain_id in gui.rpc_config:
                    gui.rpc_config[chain_id]["url"] = url_var.get()

            # Save RPC config to file
            save_rpc_config(gui.rpc_config, gui.base_dir)

            # Save API keys to vault
            for provider, key_var in api_key_vars.items():
                gui.api_keys[provider] = key_var.get()

            # Rebuild balance engine with new config
            gui._rebuild_balance_engine()

        gui._save_vault_config()
        gui._update_online_indicator()
        gui.show_notification("Settings saved")
        dialog.destroy()
        # Re-render current account view to update Check Balance button states
        if gui.current_account:
            gui.select_account(gui.current_pool or "Unassigned", gui.current_account)

    btn_frame = ctk.CTkFrame(dialog, fg_color="transparent")
    btn_frame.pack(pady=10)
    ctk.CTkButton(btn_frame, text="Save", command=do_save, width=100,
                  fg_color=("#28a745", "#1e7e34")).pack(side="left", padx=10)
    ctk.CTkButton(btn_frame, text="Cancel", command=dialog.destroy, width=100,
                  fg_color="gray30").pack(side="left", padx=10)
