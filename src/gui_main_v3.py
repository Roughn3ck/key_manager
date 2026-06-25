"""
Key Manager GUI - Modern dark-themed interface for secure key management.
Built with CustomTkinter.
"""
import sys
import os

# Guard sys.stdout/stderr for windowed/compiled mode where they may be None
if sys.stdout is None:
    sys.stdout = open(os.devnull, 'w', encoding='utf-8', errors='replace')
if sys.stderr is None:
    sys.stderr = open(os.devnull, 'w', encoding='utf-8', errors='replace')

# Hide console window on Windows only when running as a frozen EXE.
# Calling FreeConsole() in script mode detaches stdout/stderr from the
# terminal, causing fatal "I/O operation on closed file" errors that crash
# the process when anything writes to stderr (e.g. after-login exceptions).
if sys.platform == 'win32' and getattr(sys, 'frozen', False):
    import ctypes
    try:
        ctypes.windll.kernel32.FreeConsole()
    except Exception:
        pass
    # After FreeConsole(), the original stdout/stderr handles are invalid.
    # Writing to them (print/traceback) raises OSError and crashes the app.
    # Redirect to a log file next to the EXE so errors remain diagnosable,
    # falling back to devnull if the log file cannot be opened.
    try:
        _log_path = os.path.join(os.path.dirname(sys.executable), 'gui_debug.log')
        _log_file = open(_log_path, 'a', encoding='utf-8', errors='replace')
        sys.stdout = _log_file
        sys.stderr = _log_file
    except Exception:
        sys.stdout = open(os.devnull, 'w', encoding='utf-8', errors='replace')
        sys.stderr = open(os.devnull, 'w', encoding='utf-8', errors='replace')

import tkinter as tk
import customtkinter as ctk
from datetime import datetime, timedelta
import threading
import time
import json
from pathlib import Path
from typing import Dict, List, Optional, Any
import pyperclip
import queue

# Add src directory to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from crypto_engine import CryptoEngine
from derivation_engine import DerivationEngine
# BackupEngine import removed — backups are deprecated; users copy
# key_vault.encrypted manually.  The BackupEngine created an unwanted
# "backups" subfolder on every login.

# Determine if we're running from EXE or script
if getattr(sys, 'frozen', False):
    # Running from EXE - use EXE directory
    base_dir = os.path.dirname(sys.executable)
else:
    # Running from script - use script directory
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Standardized chain options used across Add Address and Add Private Key dialogs
CHAIN_OPTIONS = [
    "BTC Taproot (bc1p)",
    "BTC SegWit (bc1q)",
    "BTC (Bitcoin)",
    "EVM (Ethereum / Arbitrum / Base)",
    "EVM Railgun",
    "SOL (Solana)",
    "ZEC (Zcash)",
    "ZEC Transparent",
    "ZEC Orchard",
    "XMR (Monero)",
    "DASH (Dash)",
    "RUNE (THORChain)",
    "SUI (Sui)",
    "TRON (Tron)",
    "ATOM (Cosmos)",
    "DOT (Polkadot)",
    "ADA (Cardano)",
    "XRP (Ripple)",
    "SCRT (Secret Network)",
    "Custom...",
]

# Chains supported by the DerivationEngine (subset of CHAIN_OPTIONS)
DERIVATION_CHAINS = list(DerivationEngine.SUPPORTED_CHAINS.keys())


class PortableKeyManager:
    """KeyManager that stores vault in the same directory as the executable.

    Delegates to main.KeyManager, which already handles frozen (EXE) vs
    script path resolution correctly via get_data_dir().
    """
    def __init__(self, data_dir: Optional[Path] = None):
        from main import KeyManager as OriginalKeyManager

        # Only override the data directory when running from a frozen EXE
        # (portable USB deployment). In script mode, fall back to the default
        # ~/.key_manager/ location handled by KeyManager itself so the GUI
        # uses the same vault as the CLI.
        if data_dir is None:
            if getattr(sys, 'frozen', False):
                data_dir = Path(base_dir)
            else:
                data_dir = None  # let KeyManager use its default (~/.key_manager)

        self.data_dir = data_dir if data_dir is not None else Path.home() / ".key_manager"
        self.data_dir.mkdir(parents=True, exist_ok=True)

        # Create the underlying KeyManager with the resolved data directory
        self._manager = OriginalKeyManager(data_dir=data_dir)

        # Copy key attributes directly
        self.data_file = self._manager.data_file
        self.address_db = self._manager.address_db
        self.session_file = self._manager.session_file

    def load_encrypted_data(self, password: str) -> bool:
        """Delegate to underlying KeyManager."""
        result = self._manager.load_encrypted_data(password)
        # Sync address_db after load
        self.address_db = self._manager.address_db
        return result

    def save_encrypted_data(self, password: str) -> bool:
        """Delegate to underlying KeyManager."""
        # Sync address_db before save
        self._manager.address_db = self.address_db
        return self._manager.save_encrypted_data(password)

    def add_mnemonic(self, account_name: str, mnemonic: str, password: str) -> bool:
        """Delegate to underlying KeyManager."""
        self._manager.address_db = self.address_db
        result = self._manager.add_mnemonic(account_name, mnemonic, password)
        self.address_db = self._manager.address_db
        return result

    def add_address(self, account: str, coin: str, chain: str, address: str, password: str,
                    notes: str = "",
                    derivation_path=None, derivation_index=None, source="manual") -> bool:
        """Delegate to underlying KeyManager."""
        self._manager.address_db = self.address_db
        result = self._manager.add_address(
            account, coin, chain, address, password, notes,
            derivation_path=derivation_path,
            derivation_index=derivation_index,
            source=source)
        self.address_db = self._manager.address_db
        return result

    def delete_address(self, account: str, index: int, password: str) -> bool:
        """Delegate to underlying KeyManager."""
        self._manager.address_db = self.address_db
        result = self._manager.delete_address(account, index, password)
        self.address_db = self._manager.address_db
        return result

    def is_session_active(self) -> bool:
        """Check if session file exists."""
        return self.session_file.exists() if hasattr(self, 'session_file') else False

    def create_session(self, password: str) -> None:
        """Create session file."""
        self._manager.create_session(password)

    def get_session_password(self) -> Optional[str]:
        """Get password from session file."""
        return self._manager.get_session_password()

    def end_session(self) -> None:
        """End session by removing session file."""
        self._manager.end_session()

    def add_account(self, account_name: str, pool: Optional[str] = None) -> bool:
        """Delegate to underlying KeyManager."""
        self._manager.address_db = self.address_db
        result = self._manager.add_account(account_name, pool)
        self.address_db = self._manager.address_db
        return result

    def add_private_key(self, account_name: str, private_key: str, password: str,
                        chain: str = "",
                        source: str = "manual",
                        derivation_path=None, address_index=None,
                        derived_address=None) -> bool:
        """Delegate to underlying KeyManager (supports chain-specific multi-key + v3 metadata)."""
        self._manager.address_db = self.address_db
        result = self._manager.add_private_key(
            account_name, private_key, password, chain,
            source=source,
            derivation_path=derivation_path,
            address_index=address_index,
            derived_address=derived_address)
        self.address_db = self._manager.address_db
        return result

    def show_private_key(self, account_name: str) -> List[Dict[str, str]]:
        """Get private keys list for an account (backward-compatible with legacy string)."""
        existing = self.address_db.get("private_keys", {}).get(account_name)
        if existing is None:
            return []
        if isinstance(existing, str):
            return [{"chain": "", "key": existing}]
        return existing

    def show_mnemonic(self, account_name: str) -> Optional[str]:
        """Get mnemonic for an account."""
        return self.address_db.get("mnemonics", {}).get(account_name)

    def initialize_vault(self, password: str) -> bool:
        """Initialize a new encrypted vault (delegate to underlying KeyManager)."""
        self._manager.address_db = self.address_db
        result = self._manager.initialize_vault(password)
        self.address_db = self._manager.address_db
        return result

    def change_password(self, old_password: str, new_password: str) -> bool:
        """Change the vault password."""
        result = self._manager.change_password(old_password, new_password)
        if result:
            self.address_db = self._manager.address_db
        return result

    def import_file(self, file_path: str, password: str) -> tuple:
        """Delegate to underlying KeyManager (handles CSV and Excel)."""
        self._manager.address_db = self.address_db
        result = self._manager.import_file(file_path, password)
        self.address_db = self._manager.address_db
        return result

    def delete_account(self, account_name: str, password: str) -> bool:
        """Delegate to underlying KeyManager (deletes account + all data)."""
        self._manager.address_db = self.address_db
        result = self._manager.delete_account(account_name, password)
        self.address_db = self._manager.address_db
        return result


# Use the portable version
KeyManager = PortableKeyManager


# Configure CustomTkinter appearance
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("dark-blue")


class KeyManagerGUI:
    """Main GUI application for Key Manager."""

    def __init__(self):
        self.root = ctk.CTk()
        self.root.title("Key Manager - Secure Crypto Vault")
        self.root.geometry("1200x800")

        # Initialize components
        self.crypto = CryptoEngine()
        self.key_manager = None

        # Session management
        self.session_start_time = None
        self.session_timeout = 300  # 5 minutes in seconds
        self.session_timer = None
        self.revealed_mnemonics = {}

        self.current_account = None
        self.current_pool = None

        # Password for current session
        self.current_password = None

        # Notification timer
        self._notification_timer = None
        self._notification_label = None

        # Create login screen
        self.create_login_screen()

    def create_login_screen(self):
        """Create the secure password-only login screen."""
        # Clear any existing widgets
        for widget in self.root.winfo_children():
            widget.destroy()

        # Main frame
        main_frame = ctk.CTkFrame(self.root, corner_radius=15)
        main_frame.pack(pady=50, padx=50, fill="both", expand=True)

        # Title
        title_label = ctk.CTkLabel(
            main_frame,
            text="\U0001F510 Key Manager",
            font=ctk.CTkFont(size=32, weight="bold")
        )
        title_label.pack(pady=(40, 20))

        # Subtitle
        subtitle_label = ctk.CTkLabel(
            main_frame,
            text="Secure Crypto Key Storage",
            font=ctk.CTkFont(size=16)
        )
        subtitle_label.pack(pady=(0, 10))

        version_label = ctk.CTkLabel(
            main_frame,
            text="v3.0 - BIP39 Derivation Engine",
            font=ctk.CTkFont(size=11),
            text_color="gray60"
        )
        version_label.pack(pady=(0, 30))

        # Password entry
        password_frame = ctk.CTkFrame(main_frame, fg_color="transparent")
        password_frame.pack(pady=20)

        password_label = ctk.CTkLabel(
            password_frame,
            text="Master Password:",
            font=ctk.CTkFont(size=14)
        )
        password_label.grid(row=0, column=0, padx=(0, 10))

        self.password_entry = ctk.CTkEntry(
            password_frame,
            placeholder_text="Enter your master password",
            show="\u2022",
            width=300,
            font=ctk.CTkFont(size=14)
        )
        self.password_entry.grid(row=0, column=1)
        self.password_entry.bind("<Return>", lambda e: self.attempt_login())

        # Login button
        login_button = ctk.CTkButton(
            main_frame,
            text="Unlock Vault",
            command=self.attempt_login,
            font=ctk.CTkFont(size=14, weight="bold"),
            height=40,
            width=200
        )
        login_button.pack(pady=30)

        # Status label
        self.login_status_label = ctk.CTkLabel(
            main_frame,
            text="",
            font=ctk.CTkFont(size=12)
        )
        self.login_status_label.pack(pady=10)

        # Vault info
        self.vault_info_label = ctk.CTkLabel(
            main_frame,
            text="",
            font=ctk.CTkFont(size=12)
        )
        self.vault_info_label.pack(pady=10)

        # Store reference for init button placement
        self.login_main_frame = main_frame

        # Check for existing vault
        self.check_vault_exists()

    def check_vault_exists(self):
        """Check if vault exists and update UI."""
        # Initialize KeyManager to check vault
        self.key_manager = KeyManager()
        vault_exists = self.key_manager.data_file.exists()

        if vault_exists:
            self.vault_info_label.configure(
                text=f"Vault found at: {self.key_manager.data_file}",
                text_color="green"
            )
        else:
            self.vault_info_label.configure(
                text="No vault found. Use the button below to create one.",
                text_color="orange"
            )
            if hasattr(self, 'login_main_frame'):
                init_btn = ctk.CTkButton(
                    self.login_main_frame,
                    text="Initialize New Vault",
                    command=self.show_init_vault_dialog,
                    font=ctk.CTkFont(size=14, weight="bold"),
                    height=35,
                    width=200,
                    fg_color=("#28a745", "#1e7e34"),
                    hover_color=("#218838", "#1c7430")
                )
                init_btn.pack(pady=10)

    def attempt_login(self):
        """Attempt to login with entered password."""
        password = self.password_entry.get()
        if not password:
            self.login_status_label.configure(
                text="Please enter a password",
                text_color="red"
            )
            return

        # Initialize KeyManager if not already done
        if not self.key_manager:
            self.key_manager = KeyManager()

        # Show loading state
        self.login_status_label.configure(
            text="Verifying password...",
            text_color="yellow"
        )
        self.root.update()

        try:
            # Try to load the vault with the password
            if self.key_manager.load_encrypted_data(password):
                # Success - store password for session
                self.current_password = password

                # Create session file for CLI interoperability
                self.key_manager.create_session(password)

                # Update UI
                self.login_status_label.configure(
                    text="\u2713 Vault unlocked successfully",
                    text_color="green"
                )

                # Force UI update
                self.root.update()

                # Create main dashboard after short delay
                self.root.after(500, self.create_main_dashboard)
            else:
                self.login_status_label.configure(
                    text="\u2717 Invalid password or vault doesn't exist",
                    text_color="red"
                )
        except Exception as e:
            self.login_status_label.configure(
                text=f"Error: {str(e)}",
                text_color="red"
            )
            print(f"Login error: {e}")
            import traceback
            traceback.print_exc()

    def create_main_dashboard(self):
        """Create the main dashboard after successful login."""
        try:
            # Clear login screen (destroys all root children including
            # the notification label, so we must reset those references)
            for widget in self.root.winfo_children():
                widget.destroy()
            self._notification_label = None
            self._notification_timer = None

            # Create main layout first (creates status bar with session_timer_label)
            self.create_main_layout()

            # Start session timer only after status bar exists
            self.start_session_timer()
        except Exception as e:
            print(f"Dashboard creation error: {e}")
            import traceback
            traceback.print_exc()
            err_label = ctk.CTkLabel(
                self.root,
                text=f"Error loading dashboard: {e}",
                text_color="red",
                font=ctk.CTkFont(size=14)
            )
            err_label.pack(pady=50)

    def create_main_layout(self):
        """Create the main dashboard layout with left and right panels."""
        # Main container
        main_container = ctk.CTkFrame(self.root, corner_radius=0)
        main_container.pack(fill="both", expand=True)

        # Left panel - Accounts list
        self.create_left_panel(main_container)

        # Right panel - Chain view
        self.create_right_panel(main_container)

        # Status bar
        self.create_status_bar()

    def create_left_panel(self, parent):
        """Create left panel with scrollable account list organized by pool."""
        left_panel = ctk.CTkFrame(parent, width=300, corner_radius=0)
        left_panel.pack(side="left", fill="y", padx=(0, 1))
        left_panel.pack_propagate(False)

        # Panel title
        panel_title = ctk.CTkLabel(
            left_panel,
            text="Accounts",
            font=ctk.CTkFont(size=18, weight="bold")
        )
        panel_title.pack(pady=20)

        # Scrollable frame for accounts (store reference for refresh)
        self.left_scrollable_frame = ctk.CTkScrollableFrame(left_panel)
        self.left_scrollable_frame.pack(fill="both", expand=True, padx=20, pady=(0, 10))

        # Populate account list
        self.refresh_left_panel()

        # Action buttons frame at bottom of left panel
        action_frame = ctk.CTkFrame(left_panel, fg_color="transparent")
        action_frame.pack(fill="x", padx=20, pady=(0, 20))

        add_account_btn = ctk.CTkButton(
            action_frame,
            text="+ Add Account",
            command=self.show_add_account_dialog,
            width=220,
            height=30,
            corner_radius=10,
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color=("#28a745", "#1e7e34"),
            hover_color=("#218838", "#1c7430")
        )
        add_account_btn.pack(pady=2)

        add_address_btn = ctk.CTkButton(
            action_frame,
            text="+ Add Address",
            command=self.show_add_address_dialog,
            width=220,
            height=30,
            corner_radius=10,
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color=("#007bff", "#0056b3"),
            hover_color=("#0069d9", "#004a99")
        )
        add_address_btn.pack(pady=2)

        add_mnemonic_btn = ctk.CTkButton(
            action_frame,
            text="+ Add Mnemonic",
            command=self.show_add_mnemonic_dialog,
            width=220,
            height=30,
            corner_radius=10,
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color=("#6f42c1", "#5a32a3"),
            hover_color=("#5e3a9e", "#4c2a85")
        )
        add_mnemonic_btn.pack(pady=2)

        add_pk_btn = ctk.CTkButton(
            action_frame,
            text="+ Add Private Key",
            command=self.show_add_private_key_dialog,
            width=220,
            height=30,
            corner_radius=10,
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color=("#fd7e14", "#dc6602"),
            hover_color=("#e6750c", "#c25802")
        )
        add_pk_btn.pack(pady=2)

        import_btn = ctk.CTkButton(
            action_frame,
            text="\U0001F4E5 Import Addresses",
            command=self.show_import_dialog,
            width=220,
            height=30,
            corner_radius=10,
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color=("#17a2b8", "#138496"),
            hover_color=("#138496", "#117a8b")
        )
        import_btn.pack(pady=2)

        delete_account_btn = ctk.CTkButton(
            action_frame,
            text="\u2716 Delete Account",
            command=self.show_delete_account_dialog,
            width=220,
            height=30,
            corner_radius=10,
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color=("#dc3545", "#c82333"),
            hover_color=("#c82333", "#a71d2a")
        )
        delete_account_btn.pack(pady=2)

    def create_right_panel(self, parent):
        """Create right panel with chain view and addresses."""
        right_panel = ctk.CTkFrame(parent, corner_radius=0)
        right_panel.pack(side="right", fill="both", expand=True)

        # Panel title
        self.right_panel_title = ctk.CTkLabel(
            right_panel,
            text="Select an account to view addresses",
            font=ctk.CTkFont(size=18, weight="bold")
        )
        self.right_panel_title.pack(pady=20)

        # Chain view container
        self.chain_view_container = ctk.CTkScrollableFrame(right_panel)
        self.chain_view_container.pack(fill="both", expand=True, padx=20, pady=(0, 20))

        # Initially show placeholder
        self.show_placeholder_view()

    def create_status_bar(self):
        """Create status bar at bottom."""
        status_bar = ctk.CTkFrame(self.root, height=30, corner_radius=0)
        status_bar.pack(side="bottom", fill="x")

        # Session timer
        self.session_timer_label = ctk.CTkLabel(
            status_bar,
            text="Session: 5:00",
            font=ctk.CTkFont(size=12)
        )
        self.session_timer_label.pack(side="left", padx=20)

        # Change password button
        chg_pw_btn = ctk.CTkButton(
            status_bar,
            text="Change PW",
            command=self.show_change_password_dialog,
            width=90,
            height=25,
            font=ctk.CTkFont(size=12)
        )
        chg_pw_btn.pack(side="right", padx=20)

        # Logout button
        logout_btn = ctk.CTkButton(
            status_bar,
            text="Lock",
            command=self.lock_session,
            width=80,
            height=25,
            font=ctk.CTkFont(size=12)
        )
        logout_btn.pack(side="right", padx=20)

    def show_placeholder_view(self):
        """Show placeholder when no account is selected."""
        for widget in self.chain_view_container.winfo_children():
            widget.destroy()

        placeholder = ctk.CTkLabel(
            self.chain_view_container,
            text="Select an account from the left panel to view addresses",
            font=ctk.CTkFont(size=14)
        )
        placeholder.pack(pady=50)

    def select_account(self, pool_name, account_name):
        """Handle account selection."""
        self.current_pool = pool_name
        self.current_account = account_name

        # Reset session timer on activity
        self.session_start_time = datetime.now()

        # Update panel title
        self.right_panel_title.configure(
            text=f"{pool_name} > {account_name}"
        )

        # Clear chain view
        for widget in self.chain_view_container.winfo_children():
            widget.destroy()

        # Show addresses for selected account
        self.show_account_addresses(account_name)

    def show_account_addresses(self, account_name):
        """Display addresses, private keys, and mnemonic for the selected account.

        Each section is rendered independently -- an account with a mnemonic
        but no addresses still shows the Recovery Phrase section.
        """
        # -- Addresses section --
        accounts_data = self.key_manager.address_db.get("accounts", {})

        if account_name not in accounts_data:
            no_data_label = ctk.CTkLabel(
                self.chain_view_container,
                text=f"No addresses stored for account '{account_name}'",
                font=ctk.CTkFont(size=14)
            )
            no_data_label.pack(pady=20)
        else:
            addresses = accounts_data[account_name].get("addresses", [])
            if not addresses:
                no_data_label = ctk.CTkLabel(
                    self.chain_view_container,
                    text=f"No addresses stored for account '{account_name}'",
                    font=ctk.CTkFont(size=14)
                )
                no_data_label.pack(pady=20)
            else:
                for idx, addr in enumerate(addresses):
                    self.create_address_card(addr, idx, account_name)

        # -- Private Keys section (always shown) --
        self.create_private_key_section(account_name)

        # -- Mnemonic section (always shown) --
        self.create_mnemonic_section(account_name)

    def create_address_card(self, address_data, addr_index, account_name):
        """Create a card for displaying an address with copy and delete functionality."""
        card = ctk.CTkFrame(self.chain_view_container, corner_radius=10)
        card.pack(fill="x", pady=5, padx=10)

        # Address info
        info_frame = ctk.CTkFrame(card, fg_color="transparent")
        info_frame.pack(side="left", fill="both", expand=True, padx=10, pady=10)

        coin_label = ctk.CTkLabel(
            info_frame,
            text=address_data.get("coin", ""),
            font=ctk.CTkFont(size=14, weight="bold")
        )
        coin_label.pack(anchor="w")

        # Only show chain label if it has a value (backward compatible with
        # the standardized Type/Chain dropdown which stores chain as "")
        chain_value = address_data.get("chain", "")
        if chain_value:
            chain_label = ctk.CTkLabel(
                info_frame,
                text=chain_value,
                font=ctk.CTkFont(size=12)
            )
            chain_label.pack(anchor="w")

        address_label = ctk.CTkLabel(
            info_frame,
            text=address_data.get("address", ""),
            font=ctk.CTkFont(size=11),
            wraplength=400
        )
        address_label.pack(anchor="w", pady=(5, 0))

        # Notes if present
        notes = address_data.get("notes", "")
        if notes:
            notes_label = ctk.CTkLabel(
                info_frame,
                text=f"\U0001F4DD {notes}",
                font=ctk.CTkFont(size=10),
                text_color="yellow"
            )
            notes_label.pack(anchor="w", pady=(2, 0))

        # Buttons
        button_frame = ctk.CTkFrame(card, fg_color="transparent")
        button_frame.pack(side="right", padx=10, pady=10)

        copy_btn = ctk.CTkButton(
            button_frame,
            text="Copy",
            command=lambda a=address_data["address"]: self.copy_to_clipboard(a),
            width=80,
            height=28
        )
        copy_btn.pack(pady=2)

# Delete button
        delete_btn = ctk.CTkButton(
            button_frame,
            text="Delete",
            command=lambda idx=addr_index, acct=account_name: self.confirm_delete_address(acct, idx),
            width=80,
            height=28,
            fg_color=("#dc3545", "#c82333"),
            hover_color=("#c82333", "#a71d2a")
        )
        delete_btn.pack(pady=2)

    def confirm_delete_address(self, account_name, addr_index):
        """Show a confirmation dialog before deleting an address."""
        dialog = ctk.CTkToplevel(self.root)
        dialog.title("Delete Address")
        dialog.geometry("400x180")
        dialog.transient(self.root)
        dialog.grab_set()
        self._center_dialog(dialog)

        ctk.CTkLabel(dialog, text="Delete this address?",
                     font=ctk.CTkFont(size=16, weight="bold")).pack(pady=(20, 10))
        ctk.CTkLabel(dialog, text="This action cannot be undone.",
                     font=ctk.CTkFont(size=12), text_color="orange").pack(pady=(0, 10))

        def do_delete():
            try:
                if self.key_manager.delete_address(account_name, addr_index, self.current_password):
                    self.show_notification("Address deleted")
                    dialog.destroy()
                    self.refresh_left_panel()
                    # Refresh the current view if still viewing this account
                    if self.current_account == account_name:
                        self.select_account(self.current_pool or "Unassigned", account_name)
                else:
                    self.show_notification("Failed to delete address", error=True)
                    dialog.destroy()
            except Exception as e:
                self.show_notification(f"Error: {e}", error=True)
                dialog.destroy()

        btn_frame = ctk.CTkFrame(dialog, fg_color="transparent")
        btn_frame.pack(pady=15)
        ctk.CTkButton(btn_frame, text="Delete", command=do_delete, width=100,
                      fg_color=("#dc3545", "#c82333")).pack(side="left", padx=10)
        ctk.CTkButton(btn_frame, text="Cancel", command=dialog.destroy, width=100,
                      fg_color="gray30").pack(side="left", padx=10)

    def copy_to_clipboard(self, address):
        """Copy address to clipboard."""
        try:
            pyperclip.copy(address)
            self.show_notification(f"Copied: {address[:20]}...")
        except Exception as e:
            self.show_notification(f"Failed to copy: {str(e)}", error=True)

    def show_notification(self, message, error=False):
        """Show a temporary toast notification overlay."""
        # Cancel any existing notification timer
        if self._notification_timer is not None:
            self.root.after_cancel(self._notification_timer)
            self._notification_timer = None

        # Create notification label if it doesn't exist
        if self._notification_label is None:
            self._notification_label = ctk.CTkLabel(
                self.root,
                text="",
                font=ctk.CTkFont(size=12),
                corner_radius=8,
                height=30,
                padx=15
            )

        # Style based on error or success
        if error:
            self._notification_label.configure(
                text=f"\u2717 {message}",
                text_color="#ff6b6b",
                fg_color="#3a1a1a"
            )
        else:
            self._notification_label.configure(
                text=f"\u2713 {message}",
                text_color="#51cf94",
                fg_color="#1a3a2a"
            )

        # Place notification at bottom center, above status bar
        self._notification_label.place(relx=0.5, rely=0.93, anchor="center")
        self._notification_label.lift()

        # Auto-dismiss after 3 seconds
        self._notification_timer = self.root.after(3000, self._dismiss_notification)

    def _dismiss_notification(self):
        """Dismiss the current notification."""
        if self._notification_label is not None:
            self._notification_label.place_forget()
        self._notification_timer = None

    def start_session_timer(self):
        """Start the 5-minute session timer."""
        self.session_start_time = datetime.now()
        self.update_session_timer()

    def update_session_timer(self):
        """Update the session timer display."""
        if self.session_start_time and hasattr(self, 'session_timer_label') and self.session_timer_label is not None:
            try:
                elapsed = (datetime.now() - self.session_start_time).seconds
                remaining = max(0, self.session_timeout - elapsed)

                minutes = remaining // 60
                seconds = remaining % 60

                self.session_timer_label.configure(
                    text=f"Session: {minutes}:{seconds:02d}"
                )
            except Exception:
                pass

            # Check if session expired
            if remaining <= 0:
                self.lock_session()
            else:
                # Schedule next update
                self.root.after(1000, self.update_session_timer)

    def create_private_key_section(self, account_name):
        """Create a section displaying all chain-specific private keys for an account.

        Supports multiple keys per account (multi-key schema).  If no keys
        are stored, shows a placeholder message instead of hiding the section.
        """
        # Separator
        separator = ctk.CTkFrame(self.chain_view_container, height=2, fg_color="gray30")
        separator.pack(fill="x", pady=20, padx=10)

        # Private key section frame
        pk_frame = ctk.CTkFrame(self.chain_view_container, corner_radius=10)
        pk_frame.pack(fill="x", pady=10, padx=10)

        # Title
        pk_title = ctk.CTkLabel(
            pk_frame,
            text="\U0001F511 Private Keys",
            font=ctk.CTkFont(size=16, weight="bold")
        )
        pk_title.pack(pady=(15, 10))

        # Fetch keys (backward-compatible with legacy string format)
        keys = self.key_manager.show_private_key(account_name)

        if not keys:
            no_pk_label = ctk.CTkLabel(
                pk_frame,
                text="No private keys stored for this account",
                font=ctk.CTkFont(size=12),
                text_color="gray60"
            )
            no_pk_label.pack(pady=(0, 15))
            return

        # Status label
        self.pk_status_label = ctk.CTkLabel(
            pk_frame,
            text="Private keys are hidden for security",
            font=ctk.CTkFont(size=12),
            text_color="orange"
        )
        self.pk_status_label.pack(pady=5)

        # Build a display string: one line per key with chain label + derivation metadata
        display_lines = []
        for entry in keys:
            chain = entry.get("chain", "")
            key_val = entry.get("key", "")
            source = entry.get("source", "manual")
            dpath = entry.get("derivation_path")
            meta_parts = []
            if dpath:
                meta_parts.append(dpath)
            if source == "derived":
                meta_parts.append("derived")
            meta = f" | {' '.join(meta_parts)}" if meta_parts else ""
            link = " \U0001F517" if (source == "derived" and account_name in self.key_manager.address_db.get("mnemonics", {})) else ""
            if chain:
                display_lines.append(f"[{chain}]{meta}{link} Key: {key_val}")
            else:
                display_lines.append(f"[No Chain Specified]{meta}{link} Key: {key_val}")
        full_display = "\n".join(display_lines)

        # Store for reveal/hide/copy operations
        self._pk_display_text = full_display

        # Private key display (initially hidden)
        self.pk_display = ctk.CTkTextbox(
            pk_frame,
            height=max(60, 25 * len(keys) + 20),
            font=ctk.CTkFont(size=12, family="monospace"),
            state="disabled"
        )
        self.pk_display.pack(fill="x", padx=20, pady=10)
        # Insert masked placeholder
        self.pk_display.configure(state="normal")
        self.pk_display.insert("1.0", "\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022")
        self.pk_display.configure(state="disabled")

        # Button frame
        button_frame = ctk.CTkFrame(pk_frame, fg_color="transparent")
        button_frame.pack(pady=(0, 15))

        # Reveal button
        self.pk_reveal_button = ctk.CTkButton(
            button_frame,
            text="Reveal Keys",
            command=lambda: self.reveal_private_key(account_name),
            width=150,
            height=35,
            font=ctk.CTkFont(size=12, weight="bold")
        )
        self.pk_reveal_button.pack(side="left", padx=5)

        # Copy button (initially disabled)
        self.pk_copy_button = ctk.CTkButton(
            button_frame,
            text="Copy",
            command=lambda: self.copy_private_key_to_clipboard(account_name),
            width=100,
            height=35,
            state="disabled"
        )
        self.pk_copy_button.pack(side="left", padx=5)

        # Hide button (initially disabled)
        self.pk_hide_button = ctk.CTkButton(
            button_frame,
            text="Hide",
            command=lambda: self.hide_private_key(account_name),
            width=100,
            height=35,
            state="disabled",
            fg_color="gray30"
        )
        self.pk_hide_button.pack(side="left", padx=5)

    def reveal_private_key(self, account_name):
        """Reveal all private keys with password re-entry."""
        dialog = ctk.CTkToplevel(self.root)
        dialog.title("Re-enter Master Password")
        dialog.geometry("400x200")
        dialog.transient(self.root)
        dialog.grab_set()

        dialog.update_idletasks()
        x = self.root.winfo_x() + (self.root.winfo_width() - dialog.winfo_width()) // 2
        y = self.root.winfo_y() + (self.root.winfo_height() - dialog.winfo_height()) // 2
        dialog.geometry(f"+{x}+{y}")

        title_label = ctk.CTkLabel(dialog, text="Re-enter Master Password", font=ctk.CTkFont(size=16, weight="bold"))
        title_label.pack(pady=20)

        password_entry = ctk.CTkEntry(dialog, placeholder_text="Enter master password", show="\u2022", width=250, font=ctk.CTkFont(size=12))
        password_entry.pack(pady=10)
        password_entry.focus_set()

        status_label = ctk.CTkLabel(dialog, text="", font=ctk.CTkFont(size=11))
        status_label.pack(pady=5)

        def verify_password():
            password = password_entry.get()
            if not password:
                status_label.configure(text="Please enter password", text_color="red")
                return
            if password == self.current_password:
                display_text = getattr(self, '_pk_display_text', '')
                self.pk_display.configure(state="normal")
                self.pk_display.delete("1.0", "end")
                self.pk_display.insert("1.0", display_text)
                self.pk_display.configure(state="disabled")
                self.pk_reveal_button.configure(state="disabled")
                self.pk_copy_button.configure(state="normal")
                self.pk_hide_button.configure(state="normal")
                self.pk_status_label.configure(text="Private keys revealed - will auto-hide in 5 minutes", text_color="green")
                dialog.destroy()
                self.show_notification("Private keys revealed")
                self.root.after(300000, lambda: self.auto_hide_private_key(account_name))
            else:
                status_label.configure(text="Invalid password", text_color="red")

        password_entry.bind("<Return>", lambda e: verify_password())

        btn_frame = ctk.CTkFrame(dialog, fg_color="transparent")
        btn_frame.pack(pady=20)
        ctk.CTkButton(btn_frame, text="Verify", command=verify_password, width=100).pack(side="left", padx=10)
        ctk.CTkButton(btn_frame, text="Cancel", command=dialog.destroy, width=100, fg_color="gray30").pack(side="left", padx=10)

    def hide_private_key(self, account_name):
        """Hide the revealed private keys."""
        self.pk_display.configure(state="normal")
        self.pk_display.delete("1.0", "end")
        self.pk_display.insert("1.0", "\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022")
        self.pk_display.configure(state="disabled")
        self.pk_reveal_button.configure(state="normal")
        self.pk_copy_button.configure(state="disabled")
        self.pk_hide_button.configure(state="disabled")
        self.pk_status_label.configure(text="Private keys are hidden for security", text_color="orange")
        self.show_notification("Private keys hidden")

    def auto_hide_private_key(self, account_name):
        """Auto-hide private keys after 5 minutes."""
        self.hide_private_key(account_name)
        self.show_notification("Private keys auto-hidden after 5 minutes")

    def copy_private_key_to_clipboard(self, account_name):
        """Copy all private keys to clipboard."""
        display_text = getattr(self, '_pk_display_text', '')
        if display_text:
            try:
                pyperclip.copy(display_text)
                self.show_notification("Private keys copied to clipboard")
            except Exception as e:
                self.show_notification(f"Failed to copy: {str(e)}", error=True)

    def create_mnemonic_section(self, account_name):
        """Create a section for mnemonic display and reveal.

        Always shown -- if no mnemonic is stored, displays a placeholder
        message so the user knows the section exists.
        """
        has_mnemonic = account_name in self.key_manager.address_db.get("mnemonics", {})

        # Separator
        separator = ctk.CTkFrame(self.chain_view_container, height=2, fg_color="gray30")
        separator.pack(fill="x", pady=20, padx=10)

        # Mnemonic section
        mnemonic_frame = ctk.CTkFrame(self.chain_view_container, corner_radius=10)
        mnemonic_frame.pack(fill="x", pady=10, padx=10)

        # Title
        mnemonic_title = ctk.CTkLabel(
            mnemonic_frame,
            text="\U0001F512 Recovery Phrase (24 words)",
            font=ctk.CTkFont(size=16, weight="bold")
        )
        mnemonic_title.pack(pady=(15, 10))

        if not has_mnemonic:
            no_mnemonic_label = ctk.CTkLabel(
                mnemonic_frame,
                text="No mnemonic stored for this account",
                font=ctk.CTkFont(size=12),
                text_color="gray60"
            )
            no_mnemonic_label.pack(pady=(0, 15))
            return

        # Status label
        self.mnemonic_status_label = ctk.CTkLabel(
            mnemonic_frame,
            text="Mnemonic is hidden for security",
            font=ctk.CTkFont(size=12),
            text_color="orange"
        )
        self.mnemonic_status_label.pack(pady=5)

        # Mnemonic display (initially hidden)
        self.mnemonic_display = ctk.CTkTextbox(
            mnemonic_frame,
            height=100,
            font=ctk.CTkFont(size=12, family="monospace"),
            state="disabled"
        )
        self.mnemonic_display.pack(fill="x", padx=20, pady=10)

        # Button frame
        button_frame = ctk.CTkFrame(mnemonic_frame, fg_color="transparent")
        button_frame.pack(pady=(0, 15))

        # Reveal button
        self.reveal_button = ctk.CTkButton(
            button_frame,
            text="Reveal Mnemonic",
            command=lambda: self.reveal_mnemonic(account_name),
            width=150,
            height=35,
            font=ctk.CTkFont(size=12, weight="bold")
        )
        self.reveal_button.pack(side="left", padx=5)

        # Copy button (initially disabled)
        self.copy_mnemonic_button = ctk.CTkButton(
            button_frame,
            text="Copy",
            command=lambda: self.copy_mnemonic_to_clipboard(account_name),
            width=100,
            height=35,
            state="disabled"
        )
        self.copy_mnemonic_button.pack(side="left", padx=5)

        # Hide button (initially disabled)
        self.hide_mnemonic_button = ctk.CTkButton(
            button_frame,
            text="Hide",
            command=lambda: self.hide_mnemonic(account_name),
            width=100,
            height=35,
            state="disabled",
            fg_color="gray30"
        )
        self.hide_mnemonic_button.pack(side="left", padx=5)

        # v3: Derivation buttons
        derive_frame = ctk.CTkFrame(mnemonic_frame, fg_color="transparent")
        derive_frame.pack(pady=(0, 15))

        derive_btn = ctk.CTkButton(
            derive_frame,
            text="Derive Addresses",
            command=lambda a=account_name: self.show_derivation_dialog(a),
            width=150,
            height=35,
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color=("#fd7e14", "#dc6602"),
            hover_color=("#e6750c", "#c25802")
        )
        derive_btn.pack(side="left", padx=5)

        derive_all_btn = ctk.CTkButton(
            derive_frame,
            text="Derive All Chains",
            command=lambda a=account_name: self.show_derive_all_chains_dialog(a),
            width=150,
            height=35,
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color=("#17a2b8", "#138496"),
            hover_color=("#138496", "#117a8b")
        )
        derive_all_btn.pack(side="left", padx=5)

    def reveal_mnemonic(self, account_name):
        """Reveal mnemonic with password re-entry."""
        # Create password dialog
        dialog = ctk.CTkToplevel(self.root)
        dialog.title("Re-enter Master Password")
        dialog.geometry("400x200")
        dialog.transient(self.root)
        dialog.grab_set()

        # Center the dialog
        dialog.update_idletasks()
        x = self.root.winfo_x() + (self.root.winfo_width() - dialog.winfo_width()) // 2
        y = self.root.winfo_y() + (self.root.winfo_height() - dialog.winfo_height()) // 2
        dialog.geometry(f"+{x}+{y}")

        # Dialog content
        title_label = ctk.CTkLabel(
            dialog,
            text="Re-enter Master Password",
            font=ctk.CTkFont(size=16, weight="bold")
        )
        title_label.pack(pady=20)

        password_label = ctk.CTkLabel(
            dialog,
            text="Password:",
            font=ctk.CTkFont(size=12)
        )
        password_label.pack()

        password_entry = ctk.CTkEntry(
            dialog,
            placeholder_text="Enter master password",
            show="\u2022",
            width=250,
            font=ctk.CTkFont(size=12)
        )
        password_entry.pack(pady=10)
        password_entry.focus_set()

        status_label = ctk.CTkLabel(
            dialog,
            text="",
            font=ctk.CTkFont(size=11)
        )
        status_label.pack(pady=5)

        def verify_password():
            password = password_entry.get()
            if not password:
                status_label.configure(text="Please enter password", text_color="red")
                return

            # Verify password against current session password
            if password == self.current_password:
                # Success - reveal mnemonic
                mnemonic = self.key_manager.address_db["mnemonics"][account_name]

                # Update display
                self.mnemonic_display.configure(state="normal")
                self.mnemonic_display.delete("1.0", "end")
                self.mnemonic_display.insert("1.0", mnemonic)
                self.mnemonic_display.configure(state="disabled")

                # Update buttons
                self.reveal_button.configure(state="disabled")
                self.copy_mnemonic_button.configure(state="normal")
                self.hide_mnemonic_button.configure(state="normal")

                # Update status
                self.mnemonic_status_label.configure(
                    text=f"Mnemonic revealed - will auto-hide in 5 minutes",
                    text_color="green"
                )

                # Store revealed mnemonic with expiry time
                self.revealed_mnemonics[account_name] = datetime.now()

                # Schedule auto-hide
                self.root.after(300000, lambda: self.auto_hide_mnemonic(account_name))

                # Close dialog
                dialog.destroy()

                # Show notification
                self.show_notification("Mnemonic revealed successfully")
            else:
                status_label.configure(text="Invalid password", text_color="red")

        # Bind Enter key
        password_entry.bind("<Return>", lambda e: verify_password())

        # Buttons
        button_frame = ctk.CTkFrame(dialog, fg_color="transparent")
        button_frame.pack(pady=20)

        verify_btn = ctk.CTkButton(
            button_frame,
            text="Verify",
            command=verify_password,
            width=100
        )
        verify_btn.pack(side="left", padx=10)

        cancel_btn = ctk.CTkButton(
            button_frame,
            text="Cancel",
            command=dialog.destroy,
            width=100,
            fg_color="gray30"
        )
        cancel_btn.pack(side="left", padx=10)

    def hide_mnemonic(self, account_name):
        """Hide the revealed mnemonic."""
        self.mnemonic_display.configure(state="normal")
        self.mnemonic_display.delete("1.0", "end")
        self.mnemonic_display.insert("1.0", "\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022")
        self.mnemonic_display.configure(state="disabled")

        # Update buttons
        self.reveal_button.configure(state="normal")
        self.copy_mnemonic_button.configure(state="disabled")
        self.hide_mnemonic_button.configure(state="disabled")

        # Update status
        self.mnemonic_status_label.configure(
            text="Mnemonic is hidden for security",
            text_color="orange"
        )

        # Remove from revealed mnemonics
        if account_name in self.revealed_mnemonics:
            del self.revealed_mnemonics[account_name]

        self.show_notification("Mnemonic hidden")

    def auto_hide_mnemonic(self, account_name):
        """Automatically hide mnemonic after 5 minutes."""
        if account_name in self.revealed_mnemonics:
            reveal_time = self.revealed_mnemonics[account_name]
            if (datetime.now() - reveal_time).seconds >= 300:  # 5 minutes
                self.hide_mnemonic(account_name)
                self.show_notification("Mnemonic auto-hidden after 5 minutes")

    def copy_mnemonic_to_clipboard(self, account_name):
        """Copy mnemonic to clipboard."""
        if account_name in self.key_manager.address_db.get("mnemonics", {}):
            mnemonic = self.key_manager.address_db["mnemonics"][account_name]
            try:
                pyperclip.copy(mnemonic)
                self.show_notification("Mnemonic copied to clipboard")
            except Exception as e:
                self.show_notification(f"Failed to copy: {str(e)}", error=True)

    # --- v2: Add Address / Mnemonic / Account / Private Key dialogs ---

    def _get_all_account_names(self):
        """Collect all account names from both accounts_data and pool definitions.

        This ensures accounts that exist in pools but have no address entries
        still appear in dropdowns.
        """
        all_accounts = set(self.key_manager.address_db.get("accounts", {}).keys())
        for pool_data in self.key_manager.address_db.get("pools", {}).values():
            all_accounts.update(pool_data.get("accounts", []))
        return sorted(all_accounts)

    def _style_combobox(self, style_name="Dark.TCombobox"):
        """Apply dark-theme styling to a ttk Combobox with a readable font size.

        Returns the style object so the caller can use it.
        """
        import tkinter.ttk as ttk
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure(style_name,
                        fieldbackground="#2b2b2b",
                        background="#3b3b3b",
                        foreground="#dce4ee",
                        arrowcolor="#dce4ee",
                        bordercolor="#565b73",
                        lightcolor="#565b73",
                        darkcolor="#565b73",
                        font=("Segoe UI", 13),
                        padding=6)
        style.map(style_name,
                  fieldbackground=[("readonly", "#2b2b2b")],
                  foreground=[("readonly", "#dce4ee")],
                  background=[("active", "#4a4f63")])
        return style

    def refresh_left_panel(self):
        """Rebuild the scrollable account list in the left panel from vault data."""
        if not hasattr(self, 'left_scrollable_frame'):
            return
        # Clear existing widgets
        for widget in self.left_scrollable_frame.winfo_children():
            widget.destroy()

        pools = self.key_manager.address_db.get("pools", {})
        accounts_data = self.key_manager.address_db.get("accounts", {})

        for pool_name, pool_data in pools.items():
            pool_header = ctk.CTkLabel(
                self.left_scrollable_frame,
                text=f"-- {pool_name} --",
                font=ctk.CTkFont(size=14, weight="bold")
            )
            pool_header.pack(pady=(10, 2), anchor="w")

            for account in pool_data.get("accounts", []):
                addr_count = len(accounts_data.get(account, {}).get("addresses", []))
                label = f"{account} ({addr_count})" if addr_count > 0 else account
                account_btn = ctk.CTkButton(
                    self.left_scrollable_frame,
                    text=label,
                    command=lambda p=pool_name, a=account: self.select_account(p, a),
                    width=220, height=35, corner_radius=10,
                    fg_color="transparent",
                    hover_color=("gray70", "gray30"),
                    border_width=1,
                    border_color=("gray60", "gray40")
                )
                account_btn.pack(pady=2)

        # Show unassigned accounts
        all_pool_accounts = set()
        for pool_data in pools.values():
            all_pool_accounts.update(pool_data.get("accounts", []))
        unassigned = set(accounts_data.keys()) - all_pool_accounts
        if unassigned:
            unassigned_header = ctk.CTkLabel(
                self.left_scrollable_frame,
                text="-- Unassigned --",
                font=ctk.CTkFont(size=14, weight="bold")
            )
            unassigned_header.pack(pady=(10, 2), anchor="w")
            for account in sorted(unassigned):
                addr_count = len(accounts_data[account].get("addresses", []))
                label = f"{account} ({addr_count})" if addr_count > 0 else account
                account_btn = ctk.CTkButton(
                    self.left_scrollable_frame,
                    text=label,
                    command=lambda a=account: self.select_account("Unassigned", a),
                    width=220, height=35, corner_radius=10,
                    fg_color="transparent",
                    hover_color=("gray70", "gray30"),
                    border_width=1,
                    border_color=("gray60", "gray40")
                )
                account_btn.pack(pady=2)

    def _center_dialog(self, dialog):
        """Center a Toplevel dialog over the main window."""
        dialog.update_idletasks()
        x = self.root.winfo_x() + (self.root.winfo_width() - dialog.winfo_width()) // 2
        y = self.root.winfo_y() + (self.root.winfo_height() - dialog.winfo_height()) // 2
        dialog.geometry(f"+{x}+{y}")

    def show_delete_account_dialog(self):
        """Dialog to delete an account and all its data."""
        if not self.current_password:
            self.show_notification("Vault not unlocked", error=True)
            return

        account_names = self._get_all_account_names()
        if not account_names:
            self.show_notification("No accounts to delete", error=True)
            return

        dialog = ctk.CTkToplevel(self.root)
        dialog.title("Delete Account")
        dialog.geometry("420x280")
        dialog.transient(self.root)
        dialog.grab_set()
        self._center_dialog(dialog)

        ctk.CTkLabel(dialog, text="Delete Account",
                     font=ctk.CTkFont(size=16, weight="bold")).pack(pady=(20, 10))

        form = ctk.CTkFrame(dialog, fg_color="transparent")
        form.pack(pady=10, padx=20, fill="x")

        ctk.CTkLabel(form, text="Select Account:").pack(anchor="w")
        default_account = self.current_account if self.current_account else account_names[0]
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
                if self.key_manager.delete_account(account, self.current_password):
                    self.show_notification(f"Account '{account}' deleted")
                    dialog.destroy()
                    # Clear current account if it was the one deleted
                    if self.current_account == account:
                        self.current_account = None
                        self.current_pool = None
                        self.right_panel_title.configure(
                            text="Select an account to view addresses"
                        )
                        self.show_placeholder_view()
                    self.refresh_left_panel()
                else:
                    status_label.configure(text="Failed to delete account", text_color="red")
            except Exception as e:
                status_label.configure(text=f"Error: {e}", text_color="red")

        btn_frame = ctk.CTkFrame(dialog, fg_color="transparent")
        btn_frame.pack(pady=15)
        ctk.CTkButton(btn_frame, text="Delete", command=do_delete, width=100,
                      fg_color=("#dc3545", "#c82333")).pack(side="left", padx=10)
        ctk.CTkButton(btn_frame, text="Cancel", command=dialog.destroy, width=100,
                      fg_color="gray30").pack(side="left", padx=10)

    def show_add_account_dialog(self):
        """Dialog to create a new account, optionally in a pool."""
        if not self.current_password:
            self.show_notification("Vault not unlocked", error=True)
            return

        dialog = ctk.CTkToplevel(self.root)
        dialog.title("Add New Account")
        dialog.geometry("420x300")
        dialog.transient(self.root)
        dialog.grab_set()
        self._center_dialog(dialog)

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
        pools = list(self.key_manager.address_db.get("pools", {}).keys())
        self._style_combobox()
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
                if self.key_manager.add_account(name, pool):
                    self.key_manager.save_encrypted_data(self.current_password)
                    self.show_notification(f"Account '{name}' added")
                    dialog.destroy()
                    self.refresh_left_panel()
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

    def show_add_address_dialog(self):
        """Dialog to add a new address to an account."""
        if not self.current_password:
            self.show_notification("Vault not unlocked", error=True)
            return

        # Collect ALL account names (from pools + accounts_data)
        account_names = self._get_all_account_names()
        if not account_names:
            self.show_notification("Create an account first", error=True)
            return

        dialog = ctk.CTkToplevel(self.root)
        dialog.title("Add New Address")
        dialog.geometry("540x620")
        dialog.minsize(540, 520)
        dialog.transient(self.root)
        dialog.grab_set()
        self._center_dialog(dialog)

        ctk.CTkLabel(dialog, text="Add New Address",
                     font=ctk.CTkFont(size=16, weight="bold")).pack(pady=(20, 10))

        # Scrollable form so all fields (and the Save button) remain
        # accessible even if the window/dialog is shorter than the content.
        form = ctk.CTkScrollableFrame(dialog, fg_color="transparent")
        form.pack(pady=10, padx=20, fill="both", expand=True)

        # Account dropdown - default to currently selected account
        ctk.CTkLabel(form, text="Account:").pack(anchor="w")
        default_account = self.current_account if self.current_account else account_names[0]
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
        self._style_combobox()
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
                if self.key_manager.add_address(account, coin_name, chain_type, address,
                                                self.current_password, notes):
                    self.show_notification(f"Address added to '{account}'")
                    dialog.destroy()
                    self.refresh_left_panel()
                    # If currently viewing this account, refresh the view
                    if self.current_account == account:
                        self.select_account(self.current_pool or "Unassigned", account)
                else:
                    status_label.configure(text="Failed to add address", text_color="red")
            except Exception as e:
                status_label.configure(text=f"Error: {e}", text_color="red")

        btn_frame = ctk.CTkFrame(dialog, fg_color="transparent")
        btn_frame.pack(pady=15)
        ctk.CTkButton(btn_frame, text="Save Entry", command=do_add, width=120).pack(side="left", padx=10)
        ctk.CTkButton(btn_frame, text="Cancel", command=dialog.destroy, width=100,
                      fg_color="gray30").pack(side="left", padx=10)

    def show_add_mnemonic_dialog(self):
        """Dialog to add or update a mnemonic for an account."""
        if not self.current_password:
            self.show_notification("Vault not unlocked", error=True)
            return

        # Collect ALL account names (from pools + accounts_data)
        account_names = self._get_all_account_names()
        if not account_names:
            self.show_notification("Create an account first", error=True)
            return

        dialog = ctk.CTkToplevel(self.root)
        dialog.title("Add / Update Mnemonic")
        dialog.geometry("500x380")
        dialog.transient(self.root)
        dialog.grab_set()
        self._center_dialog(dialog)

        ctk.CTkLabel(dialog, text="Add / Update Recovery Phrase",
                     font=ctk.CTkFont(size=16, weight="bold")).pack(pady=(20, 10))

        form = ctk.CTkFrame(dialog, fg_color="transparent")
        form.pack(pady=10, padx=20, fill="both", expand=True)

        # Account dropdown
        ctk.CTkLabel(form, text="Account:").pack(anchor="w")
        default_account = self.current_account if self.current_account else account_names[0]
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
                if self.key_manager.add_mnemonic(account, mnemonic, self.current_password):
                    self.show_notification(f"Mnemonic saved for '{account}'")
                    dialog.destroy()
                    # If currently viewing this account, refresh the view
                    if self.current_account == account:
                        self.select_account(self.current_pool or "Unassigned", account)
                else:
                    status_label.configure(text="Failed to save mnemonic", text_color="red")
            except Exception as e:
                status_label.configure(text=f"Error: {e}", text_color="red")

        btn_frame = ctk.CTkFrame(dialog, fg_color="transparent")
        btn_frame.pack(pady=15)
        ctk.CTkButton(btn_frame, text="Save Mnemonic", command=do_add, width=140).pack(side="left", padx=10)
        ctk.CTkButton(btn_frame, text="Cancel", command=dialog.destroy, width=100,
                      fg_color="gray30").pack(side="left", padx=10)

    def show_add_private_key_dialog(self):
        """Dialog to add a chain-specific private key to an account."""
        if not self.current_password:
            self.show_notification("Vault not unlocked", error=True)
            return

        # Collect ALL account names (from pools + accounts_data)
        account_names = self._get_all_account_names()
        if not account_names:
            self.show_notification("Create an account first", error=True)
            return

        dialog = ctk.CTkToplevel(self.root)
        dialog.title("Add Private Key")
        dialog.geometry("500x470")
        dialog.transient(self.root)
        dialog.grab_set()
        self._center_dialog(dialog)

        ctk.CTkLabel(dialog, text="Add Private Key",
                     font=ctk.CTkFont(size=16, weight="bold")).pack(pady=(20, 10))

        form = ctk.CTkFrame(dialog, fg_color="transparent")
        form.pack(pady=10, padx=20, fill="both", expand=True)

        # Account dropdown - default to currently selected account
        ctk.CTkLabel(form, text="Account:").pack(anchor="w")
        default_account = self.current_account if self.current_account else account_names[0]
        acct_var = ctk.StringVar(value=default_account)
        acct_menu = ctk.CTkOptionMenu(form, variable=acct_var, values=account_names, width=420)
        acct_menu.pack(fill="x", pady=(0, 10))

        # Private Key Chain dropdown (includes Blank/None + standard chains + Custom)
        PK_CHAIN_OPTIONS = ["Blank/None"] + CHAIN_OPTIONS

        ctk.CTkLabel(form, text="Private Key Chain:").pack(anchor="w")
        pk_chain_var = ctk.StringVar(value=PK_CHAIN_OPTIONS[0])
        import tkinter.ttk as ttk
        self._style_combobox()
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
        ctk.CTkLabel(form, text="Private Key:").pack(anchor="w")
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
        derive_var = ctk.CTkCheckBox(form, text="Derive from Mnemonic")
        derive_var.pack(anchor="w", pady=(0, 5))

        # v3: Derivation fields (hidden by default)
        derive_fields_frame = ctk.CTkFrame(form, fg_color="transparent")

        ctk.CTkLabel(derive_fields_frame, text="Derivation Chain:").pack(anchor="w")
        deriv_chain_var = ctk.StringVar(value=DERIVATION_CHAINS[0])
        self._style_combobox()
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
        derived_meta = {"path": None, "index": None, "address": None}

        def on_derive_toggle():
            if derive_var.get():
                acct = acct_var.get().strip()
                mnemonic = self.key_manager.show_mnemonic(acct)
                if not mnemonic:
                    derive_status.configure(text="No mnemonic stored for this account. Add a mnemonic first.", text_color="orange")
                    derive_var.deselect()
                    return
                derive_fields_frame.pack(fill="x", pady=(0, 10))
                pk_entry.configure(state="disabled")
                derive_status.configure(text="", text_color="gray60")
            else:
                derive_fields_frame.pack_forget()
                pk_entry.configure(state="normal")
                derived_meta["path"] = None
                derived_meta["index"] = None
                derived_meta["address"] = None

        derive_var.configure(command=on_derive_toggle)

        def do_derive_pk():
            acct = acct_var.get().strip()
            mnemonic = self.key_manager.show_mnemonic(acct)
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
                derive_status.configure(text=f"Derived: {result['address'][:30]}...", text_color="green")
            except Exception as e:
                derive_status.configure(text=f"Error: {e}", text_color="red")

        derive_btn_pk.configure(command=do_derive_pk)

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
            private_key = pk_entry.get().strip()
            if not account or not private_key:
                status_label.configure(text="Account and private key are required", text_color="red")
                return
            try:
                # v3: Include derivation metadata if derived from mnemonic
                if derive_var.get() and derived_meta["path"]:
                    success = self.key_manager.add_private_key(
                        account, private_key, self.current_password, chain_label,
                        source="derived",
                        derivation_path=derived_meta["path"],
                        address_index=derived_meta["index"],
                        derived_address=derived_meta["address"])
                else:
                    success = self.key_manager.add_private_key(
                        account, private_key, self.current_password, chain_label)
                if success:
                    self.show_notification(f"Private key added to '{account}'")
                    dialog.destroy()
                    # If currently viewing this account, refresh the view
                    if self.current_account == account:
                        self.select_account(self.current_pool or "Unassigned", account)
                else:
                    status_label.configure(text="Failed to add private key", text_color="red")
            except Exception as e:
                status_label.configure(text=f"Error: {e}", text_color="red")

        btn_frame = ctk.CTkFrame(dialog, fg_color="transparent")
        btn_frame.pack(pady=15)
        ctk.CTkButton(btn_frame, text="Save Private Key", command=do_add, width=140).pack(side="left", padx=10)
        ctk.CTkButton(btn_frame, text="Cancel", command=dialog.destroy, width=100,
                      fg_color="gray30").pack(side="left", padx=10)

    def show_derivation_dialog(self, account_name):
        """Open a dialog to derive addresses from the account's stored mnemonic."""
        mnemonic = self.key_manager.show_mnemonic(account_name)
        if not mnemonic:
            self.show_notification("No mnemonic stored for this account", error=True)
            return

        dialog = ctk.CTkToplevel(self.root)
        dialog.title("Derive Addresses from Mnemonic")
        dialog.geometry("600x550")
        dialog.transient(self.root)
        dialog.grab_set()
        self._center_dialog(dialog)

        ctk.CTkLabel(dialog, text="Derive Addresses from Mnemonic",
                     font=ctk.CTkFont(size=16, weight="bold")).pack(pady=(20, 5))
        ctk.CTkLabel(dialog, text=f"Account: {account_name}",
                     font=ctk.CTkFont(size=12), text_color="gray60").pack(pady=(0, 10))

        form = ctk.CTkScrollableFrame(dialog, fg_color="transparent")
        form.pack(pady=10, padx=20, fill="both", expand=True)

        # Chain dropdown
        ctk.CTkLabel(form, text="Chain:").pack(anchor="w")
        chain_var = ctk.StringVar(value=DERIVATION_CHAINS[0])
        self._style_combobox()
        import tkinter.ttk as ttk
        chain_combo = ttk.Combobox(form, textvariable=chain_var, values=DERIVATION_CHAINS,
                                    state="readonly", width=55, style="Dark.TCombobox")
        chain_combo.pack(fill="x", pady=(0, 10))

        # Derivation path (auto-populated, editable)
        ctk.CTkLabel(form, text="Derivation Path:").pack(anchor="w")
        path_entry = ctk.CTkEntry(form, width=420, font=ctk.CTkFont(size=12, family="monospace"))
        path_entry.pack(fill="x", pady=(0, 10))

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
        pk_label = ctk.CTkLabel(result_frame, text="", font=ctk.CTkFont(size=11, family="monospace"),
                                wraplength=500)
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
            try:
                result = DerivationEngine.derive_from_mnemonic(mnemonic, ch, address_index=idx)
                last_result["data"] = result
                addr_label.configure(text=f"Address: {result['address']}", text_color="#51cf94")
                pk_label.configure(text=f"Private Key: {result['private_key'][:40]}...", text_color="gray70")
                status_label.configure(text="Derived successfully", text_color="green")
            except Exception as e:
                status_label.configure(text=f"Error: {e}", text_color="red")

        def do_save():
            if not last_result["data"]:
                status_label.configure(text="Derive an address first", text_color="orange")
                return
            r = last_result["data"]
            try:
                self.key_manager.add_address(
                    account_name, r["chain"], r["chain"], r["address"],
                    self.current_password, notes="Derived",
                    derivation_path=r["path"], source="derived")
                self.key_manager.add_private_key(
                    account_name, r["private_key"], self.current_password, r["chain"],
                    source="derived", derivation_path=r["path"],
                    derived_address=r["address"])
                self.show_notification(f"Derived address+key saved to '{account_name}'")
                status_label.configure(text="Saved!", text_color="green")
                self.refresh_left_panel()
                if self.current_account == account_name:
                    self.select_account(self.current_pool or "Unassigned", account_name)
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

    def show_derive_all_chains_dialog(self, account_name):
        """Derive addresses for all supported chains and show a summary."""
        mnemonic = self.key_manager.show_mnemonic(account_name)
        if not mnemonic:
            self.show_notification("No mnemonic stored for this account", error=True)
            return

        dialog = ctk.CTkToplevel(self.root)
        dialog.title("Derive All Chains")
        dialog.geometry("650x600")
        dialog.transient(self.root)
        dialog.grab_set()
        self._center_dialog(dialog)

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
                                  command=lambda c=chain, d=data: self._save_derived_to_account(
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

    def _save_derived_to_account(self, account_name, chain, data, status_label):
        """Save a derived address+key to the account."""
        try:
            self.key_manager.add_address(
                account_name, chain, chain, data["address"],
                self.current_password, notes="Derived",
                derivation_path=data["path"], source="derived")
            self.key_manager.add_private_key(
                account_name, data["private_key"], self.current_password, chain,
                source="derived", derivation_path=data["path"],
                derived_address=data["address"])
            self.show_notification(f"Saved {chain} to '{account_name}'")
            status_label.configure(text=f"Saved {chain}", text_color="green")
            self.refresh_left_panel()
            if self.current_account == account_name:
                self.select_account(self.current_pool or "Unassigned", account_name)
        except Exception as e:
            status_label.configure(text=f"Error: {e}", text_color="red")

    def show_init_vault_dialog(self):
        """Dialog to initialize a new vault from the GUI."""
        dialog = ctk.CTkToplevel(self.root)
        dialog.title("Initialize New Vault")
        dialog.geometry("450x300")
        dialog.transient(self.root)
        dialog.grab_set()
        self._center_dialog(dialog)

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
                if self.key_manager.initialize_vault(pw):
                    self.current_password = pw
                    self.key_manager.create_session(pw)
                    self.show_notification("Vault initialized successfully")
                    dialog.destroy()
                    self.root.after(500, self.create_main_dashboard)
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

    def show_change_password_dialog(self):
        """Dialog to change the vault master password."""
        if not self.current_password:
            self.show_notification("Vault not unlocked", error=True)
            return

        dialog = ctk.CTkToplevel(self.root)
        dialog.title("Change Password")
        dialog.geometry("450x350")
        dialog.transient(self.root)
        dialog.grab_set()
        self._center_dialog(dialog)

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
            if old_pw != self.current_password:
                status_label.configure(text="Current password is incorrect", text_color="red")
                return
            if new_pw != confirm_pw:
                status_label.configure(text="New passwords do not match", text_color="red")
                return
            try:
                if self.key_manager.change_password(old_pw, new_pw):
                    self.current_password = new_pw
                    self.show_notification("Password changed successfully")
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

    def show_import_dialog(self):
        """Dialog to import addresses from CSV or Excel."""
        if not self.current_password:
            self.show_notification("Vault not unlocked", error=True)
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

        dialog = ctk.CTkToplevel(self.root)
        dialog.title("Import Addresses")
        dialog.geometry("520x420")
        dialog.transient(self.root)
        dialog.grab_set()
        self._center_dialog(dialog)

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
                added, skipped, errors = self.key_manager.import_file(file_path, self.current_password)
                msg = f"Imported {added} addresses"
                if skipped > 0:
                    msg += f", skipped {skipped}"
                self.show_notification(msg)
                if errors:
                    status_label.configure(text=f"Errors: {len(errors)}", text_color="red")
                else:
                    status_label.configure(text=msg, text_color="green")
                dialog.destroy()
                self.refresh_left_panel()
                if self.current_account:
                    self.select_account(self.current_pool or "Unassigned", self.current_account)
            except Exception as e:
                status_label.configure(text=f"Error: {e}", text_color="red")

        btn_frame = ctk.CTkFrame(dialog, fg_color="transparent")
        btn_frame.pack(pady=15)
        ctk.CTkButton(btn_frame, text="Import Now", command=do_import, width=120).pack(side="left", padx=10)
        ctk.CTkButton(btn_frame, text="Cancel", command=dialog.destroy, width=100,
                      fg_color="gray30").pack(side="left", padx=10)

        # --- End v2 dialogs ---

    def lock_session(self):
        """Lock the session and return to login screen."""
        # Clear sensitive data
        self.current_password = None
        self.session_start_time = None
        self.revealed_mnemonics.clear()

        # End session file
        if self.key_manager:
            self.key_manager.end_session()

        # Dismiss any notification
        self._dismiss_notification()

        # Return to login screen
        self.create_login_screen()

    def run(self):
        """Run the GUI application."""
        self.root.mainloop()


def main():
    """Main entry point for GUI application."""
    app = KeyManagerGUI()
    app.run()


if __name__ == "__main__":
    main()