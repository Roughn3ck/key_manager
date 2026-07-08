"""
ColdStack GUI - Modern dark-themed interface for secure offline crypto key management.
Built with CustomTkinter.

Version: v5.1 (July 2026) - Vault Tracking + HyperEVM ERC-20 + Password Toggle
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
from datetime import datetime, timedelta, timezone
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
from balance_engine import BalanceEngine, is_balance_supported
from price_engine import PriceEngine, DISPLAY_CURRENCY_OPTIONS
from rpc_config import load_rpc_config, save_rpc_config, get_default_endpoints, get_default_for_chain
# v5.0: LP Engine imports
from lp_engine import LPEngine, OfflineError
from saved_pools import load_saved_pools, save_pool, is_pool_saved, migrate_saved_pools_json
from venue_adapters.venue_writer import (
    CollectFeesParams, CompoundFeesParams, DecreaseLiquidityParams, RebalanceParams,
)
# v5.1: Vault tracker imports
from vault_tracker import HyperliquidVaultTracker, VaultPosition
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
    "Hyperliquid (HL1 & HyperEVM)",
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

        # Always use base_dir (project root in script mode or EXE directory
        # in frozen mode) so the vault lives alongside the application, not
        # in ~/.key_manager/.  This keeps the database co-located with the
        # GUI for easy backup and portability.
        if data_dir is None:
            data_dir = Path(base_dir)

        self.data_dir = data_dir
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


class ColdStackGUI:
    """Main GUI application for ColdStack — secure offline crypto key vault."""

    def __init__(self):
        self.root = ctk.CTk()
        self.root.title("ColdStack - Secure Crypto Key Vault")
        self.root.geometry("1200x800")

        # Initialize components
        self.crypto = CryptoEngine()
        self.key_manager = None

        # v4.0: Balance and price engines
        self.balance_engine = BalanceEngine()
        self.price_engine = PriceEngine()

        # v4.0: Online mode (OFF by default - offline-first security principle)
        self.online_mode = False
        self.display_currency = "none"
        self._balance_labels = {}
        self._last_balance_refresh = None

        # v4.2: Standard/Advanced mode + customizable RPC + API keys
        self.app_mode = "standard"
        self.api_keys: Dict[str, str] = {}
        self.rpc_config: Optional[Dict[str, Dict[str, Any]]] = None

        # v5.0: LP Engine instance (created after login)
        self.lp_engine: Optional[LPEngine] = None
        self._lp_widgets: Dict[str, Any] = {}
        self._lp_auto_fetched = False  # v5.1: Auto-fetch saved pools once after login

        # v5.1: Vault tracker instance (created after login)
        self.vault_tracker: Optional[HyperliquidVaultTracker] = None
        self._vault_widgets: Dict[str, Any] = {}

        # Session management
        self.session_start_time = None
        self.session_timeout = 300  # 5 minutes in seconds
        self.session_timer = None
        self.no_autolock = False  # v5.1: Disable autolock when checked
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
            text="\U0001F510 ColdStack",
            font=ctk.CTkFont(size=32, weight="bold")
        )
        title_label.pack(pady=(40, 20))

        # Subtitle
        subtitle_label = ctk.CTkLabel(
            main_frame,
            text="Secure Offline Crypto Key Vault",
            font=ctk.CTkFont(size=16)
        )
        subtitle_label.pack(pady=(0, 10))

        version_label = ctk.CTkLabel(
            main_frame,
            text="v5.1 - ColdStack | Vault Tracking + HyperEVM ERC-20",
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

        # v5.1: Show/hide password toggle button (eye icon)
        def toggle_login_password():
            if self.password_entry.cget("show") == "\u2022":
                self.password_entry.configure(show="")
                show_pw_btn.configure(text="\U0001F441")
            else:
                self.password_entry.configure(show="\u2022")
                show_pw_btn.configure(text="\U0001F576")

        show_pw_btn = ctk.CTkButton(
            password_frame,
            text="\U0001F576",
            command=toggle_login_password,
            width=30,
            height=30,
            font=ctk.CTkFont(size=14),
            fg_color="gray30",
            hover_color="gray40"
        )
        show_pw_btn.grid(row=0, column=2, padx=(5, 0))

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

                # v4.0: Load vault config (online_mode, display_currency)
                self._load_vault_config()

                # v5.1: One-time migration of saved_pools.json into the encrypted vault
                if migrate_saved_pools_json(self.key_manager.address_db, base_dir):
                    self.key_manager.save_encrypted_data(self.current_password)

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
        """Create the main dashboard layout with CTkTabview (v5.0).

        Tab 1: Vault -- existing two-panel layout (accounts + addresses)
        Tab 2: LP Positions -- LP engine position viewer + write operations
        """
        # v5.0: Create LPEngine now that online_mode is loaded
        self.lp_engine = LPEngine(
            online_mode=self.online_mode,
            price_engine=self.price_engine,
        )

        # v5.1: Create Vault tracker
        self.vault_tracker = HyperliquidVaultTracker(
            online_mode=self.online_mode,
        )

        # Top-level tabview
        self.tabview = ctk.CTkTabview(self.root, corner_radius=0)
        self.tabview.pack(fill="both", expand=True)

        vault_tab = self.tabview.add("Wallet")
        vaults_tab = self.tabview.add("HL1 Vaults")
        lp_tab = self.tabview.add("LP Positions")

        # Vault tab: existing two-panel layout
        main_container = ctk.CTkFrame(vault_tab, corner_radius=0)
        main_container.pack(fill="both", expand=True)

        # Left panel - Accounts list
        self.create_left_panel(main_container)

        # Right panel - Chain view
        self.create_right_panel(main_container)

        # v5.1: Hyperliquid Vaults section (in its own tab)
        self.create_vault_section(vaults_tab)

        # LP tab: LP Positions content
        self.create_lp_tab(lp_tab)

        # v5.1: Tab change callback for auto-fetching saved pools
        self.tabview.configure(command=self._on_tab_changed)

        # Status bar (stays at root level, below tabs)
        self.create_status_bar()

    def _on_tab_changed(self):
        """Handle tab change — auto-fetch saved pools when LP tab is first shown."""
        if not hasattr(self, 'tabview'):
            return
        try:
            current_tab = self.tabview.get()
        except Exception:
            return
        if current_tab == "LP Positions":
            if self.online_mode and not self._lp_auto_fetched:
                entry = self._lp_widgets.get("address_entry")
                addr = entry.get().strip() if entry else ""
                if addr and addr.startswith("0x") and len(addr) == 42:
                    self._lp_auto_fetched = True
                    self.root.after(500, self._lp_do_fetch)

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

        # v5.1: "Do not autolock" checkbox
        self.no_autolock_var = ctk.StringVar(value="off")
        def on_no_autolock_toggle():
            self.no_autolock = bool(self.no_autolock_checkbox.get())
        self.no_autolock_checkbox = ctk.CTkCheckBox(
            status_bar,
            text="Do not autolock",
            command=on_no_autolock_toggle,
            font=ctk.CTkFont(size=11),
            height=20
        )
        self.no_autolock_checkbox.pack(side="left", padx=(10, 0))

        # Settings button (v4.0)
        settings_btn = ctk.CTkButton(
            status_bar,
            text="Settings",
            command=self.show_settings_dialog,
            width=90,
            height=25,
            font=ctk.CTkFont(size=12)
        )
        settings_btn.pack(side="right", padx=20)

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

        # Check for Updates button (user-initiated, offline by default)
        update_btn = ctk.CTkButton(
            status_bar,
            text="Check for Updates",
            command=self.check_for_updates,
            width=130,
            height=25,
            font=ctk.CTkFont(size=12)
        )
        update_btn.pack(side="right", padx=20)

        # v4.0: Online/Offline indicator + last refresh timestamp
        self.online_status_label = ctk.CTkLabel(
            status_bar,
            text="● Offline",
            font=ctk.CTkFont(size=11),
            text_color="gray60"
        )
        self.online_status_label.pack(side="left", padx=(20, 5))

        self.last_refresh_label = ctk.CTkLabel(
            status_bar,
            text="",
            font=ctk.CTkFont(size=10),
            text_color="gray50"
        )
        self.last_refresh_label.pack(side="left", padx=5)

        # v4.0: Update online indicator based on loaded config
        self._update_online_indicator()

    def check_for_updates(self):
        """Check GitHub for a newer release.

        User-initiated only — no background polling.  ColdStack is offline by
        default; this is the *only* feature that makes a network request, and
        only after the user explicitly consents.
        """
        from tkinter import messagebox

        # Confirm dialog — this makes a network request
        confirm = messagebox.askyesno(
            "Check for Updates",
            "ColdStack will make a single network request to GitHub to check for updates.\n\n"
            "No other data is sent. Your vault, keys, and passwords never leave your computer.\n\n"
            "Continue?",
            parent=self.root
        )
        if not confirm:
            return

        # Run the network request in a background thread so the Tkinter
        # event loop (and the GUI) stays responsive while we wait.
        def _do_check():
            try:
                import urllib.request
                import urllib.error
                import json as json_module

                url = "https://api.github.com/repos/Roughn3ck/key_manager/releases/latest"
                req = urllib.request.Request(url, headers={
                    "Accept": "application/vnd.github.v3+json",
                    "User-Agent": "ColdStack/5.0"
                })

                with urllib.request.urlopen(req, timeout=10) as response:
                    data = json_module.loads(response.read().decode('utf-8'))

                latest_tag = data.get("tag_name", "v0")
                release_url = data.get("html_url", "https://github.com/Roughn3ck/key_manager/releases")
                release_name = data.get("name", "Latest Release")

                current_version = "5.1"
                latest_version = latest_tag.lstrip("v")

                # Simple version comparison (handles major.minor[.patch])
                def parse_version(v):
                    parts = []
                    for p in v.split("."):
                        try:
                            parts.append(int(p))
                        except ValueError:
                            parts.append(0)
                    return parts

                if parse_version(latest_version) > parse_version(current_version):
                    self.root.after(0, lambda: messagebox.showinfo(
                        "Update Available",
                        f"A new version is available!\n\n"
                        f"Current: v{current_version}\n"
                        f"Latest: {latest_tag} — {release_name}\n\n"
                        f"Download: {release_url}",
                        parent=self.root
                    ))
                else:
                    self.root.after(0, lambda: messagebox.showinfo(
                        "Up to Date",
                        f"ColdStack v{current_version} is up to date.\n\n"
                        f"Latest release: {latest_tag}",
                        parent=self.root
                    ))
            except urllib.error.URLError:
                self.root.after(0, lambda: messagebox.showerror(
                    "Connection Error",
                    "Could not connect to GitHub. Check your internet connection and try again.\n\n"
                    "ColdStack is offline by default — this is the only feature that requires internet.",
                    parent=self.root
                ))
            except Exception as e:
                self.root.after(0, lambda: messagebox.showerror(
                    "Update Check Failed",
                    f"An error occurred while checking for updates:\n{str(e)}",
                    parent=self.root
                ))

        threading.Thread(target=_do_check, daemon=True).start()

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

        # v5.0: Pre-fill LP tab address entry with first EVM address
        self._lp_prefill_address(account_name)

        # v5.1: Pre-fill vault section address entry
        self._vault_prefill_address(account_name)

    def show_account_addresses(self, account_name):
        """Display addresses, private keys, and mnemonic for the selected account.

        Each section is rendered independently -- an account with a mnemonic
        but no addresses still shows the Recovery Phrase section.
        """
        # -- Addresses section --
        accounts_data = self.key_manager.address_db.get("accounts", {})
        self._balance_labels = {}

        # Offline hint banner
        if not self.online_mode:
            offline_banner = ctk.CTkLabel(
                self.chain_view_container,
                text="\U0001F512 Offline -- Enable Online Mode in Settings to check balances",
                font=ctk.CTkFont(size=11),
                text_color="gray50"
            )
            offline_banner.pack(pady=(5, 5))

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
                # Separate BTC and non-BTC addresses
                btc_addresses = []
                other_addresses = []
                for i, addr in enumerate(addresses):
                    coin = addr.get("coin", "")
                    if "btc" in coin.lower() or "bitcoin" in coin.lower():
                        btc_addresses.append((i, addr))
                    else:
                        other_addresses.append((i, addr))

                # Render BTC group card if any BTC addresses
                if btc_addresses:
                    btc_card = ctk.CTkFrame(self.chain_view_container, corner_radius=10)
                    btc_card.pack(fill="x", pady=3, padx=10)

                    btc_info = ctk.CTkFrame(btc_card, fg_color="transparent")
                    btc_info.pack(side="left", fill="both", expand=True, padx=10, pady=6)

                    btc_header = ctk.CTkLabel(btc_info, text="BTC", font=ctk.CTkFont(size=14, weight="bold"))
                    btc_header.pack(anchor="w")

                    for idx, addr_data in btc_addresses:
                        addr = addr_data.get("address", "")
                        coin = addr_data.get("coin", "")

                        if "taproot" in coin.lower() or "bc1p" in coin.lower():
                            type_label = "P2TR (Taproot - bc1p)"
                        elif "segwit" in coin.lower() or "bc1q" in coin.lower():
                            type_label = "P2WPKH (SegWit - bc1q)"
                        elif "legacy" in coin.lower() or "bitcoin" in coin.lower():
                            type_label = "P2PKH (Legacy)"
                        else:
                            type_label = coin

                        addr_line = ctk.CTkFrame(btc_info, fg_color="transparent")
                        addr_line.pack(fill="x", anchor="w", pady=(2, 0))

                        type_addr_label = ctk.CTkLabel(
                            addr_line,
                            text=f"{type_label}: {addr}",
                            font=ctk.CTkFont(size=11),
                            wraplength=400
                        )
                        type_addr_label.pack(side="left", anchor="w")

                        # Small spacer between address and balance
                        btc_spacer = ctk.CTkLabel(addr_line, text="", height=4)
                        btc_spacer.pack(anchor="w")

                        balance_label = ctk.CTkLabel(
                            addr_line,
                            text="",
                            font=ctk.CTkFont(size=11, weight="bold"),
                            text_color="gray60",
                            wraplength=400
                        )
                        balance_label.pack(anchor="w", pady=(2, 0))
                        self._balance_labels[addr] = balance_label

                        btn_frame = ctk.CTkFrame(btc_info, fg_color="transparent")
                        btn_frame.pack(fill="x", anchor="w", pady=(1, 2))

                        check_btn = ctk.CTkButton(
                            btn_frame,
                            text="Check Balance",
                            command=lambda a=addr_data, l=balance_label: self.check_single_balance(a, l),
                            width=90, height=22,
                            font=ctk.CTkFont(size=10)
                        )
                        btc_chain = addr_data.get("chain", "")
                        btc_supported = is_balance_supported(coin) or is_balance_supported(btc_chain) or is_balance_supported(coin + " " + btc_chain)
                        if not self.online_mode or not btc_supported:
                            check_btn.configure(state="disabled")
                        check_btn.pack(side="left", padx=(0, 5))

                        copy_btn = ctk.CTkButton(
                            btn_frame,
                            text="Copy",
                            command=lambda a=addr: self.copy_to_clipboard(a),
                            width=60, height=22,
                            font=ctk.CTkFont(size=10)
                        )
                        copy_btn.pack(side="left", padx=5)

                        delete_btn = ctk.CTkButton(
                            btn_frame,
                            text="Delete",
                            command=lambda i=idx, acct=account_name: self.confirm_delete_address(acct, i),
                            width=60, height=22,
                            font=ctk.CTkFont(size=10),
                            fg_color=("#dc3545", "#c82333"),
                            hover_color=("#c82333", "#a71d2a")
                        )
                        delete_btn.pack(side="left", padx=5)

                    sep = ctk.CTkFrame(self.chain_view_container, height=1, fg_color="gray25")
                    sep.pack(fill="x", pady=5, padx=20)

                # Render non-BTC addresses as normal cards
                for idx, addr in other_addresses:
                    self.create_address_card(addr, idx, account_name)

        # -- Private Keys section (always shown) --
        self.create_private_key_section(account_name)

        # -- Mnemonic section (always shown) --
        self.create_mnemonic_section(account_name)

    def create_address_card(self, address_data, addr_index, account_name):
        """Create a compact card for displaying an address."""
        card = ctk.CTkFrame(self.chain_view_container, corner_radius=10)
        card.pack(fill="x", pady=3, padx=10)

        info_frame = ctk.CTkFrame(card, fg_color="transparent")
        info_frame.pack(side="left", fill="both", expand=True, padx=10, pady=6)

        # Line 1: Coin + chain + derived (all on one line, separated by " · ")
        # Filter out empty strings so a chain-only entry (no coin) does not
        # render with a leading separator (e.g. "· HYPE (Hyperliquid)").
        line1_parts = []
        coin_value = address_data.get("coin", "")
        if coin_value:
            line1_parts.append(coin_value)
        chain_value = address_data.get("chain", "")
        if chain_value:
            line1_parts.append(chain_value)
        if address_data.get("source") == "derived":
            line1_parts.append("derived")

        line1_text = " \u00b7 ".join(line1_parts)
        line1_label = ctk.CTkLabel(
            info_frame,
            text=line1_text,
            font=ctk.CTkFont(size=14, weight="bold")
        )
        line1_label.pack(anchor="w")

        # Line 2: Address
        address_label = ctk.CTkLabel(
            info_frame,
            text=address_data.get("address", ""),
            font=ctk.CTkFont(size=11),
            wraplength=400
        )
        address_label.pack(anchor="w", pady=(2, 0))

        # Line 3: Balance (hidden by default, shown after fetch)
        balance_label = ctk.CTkLabel(
            info_frame,
            text="",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color="gray60",
            wraplength=500,
            justify="left"
        )
        # Don't pack yet - only show when balance is fetched
        addr = address_data.get("address", "")
        self._balance_labels[addr] = balance_label

        # Line 4: Notes (only if present)
        notes = address_data.get("notes", "")
        if notes:
            notes_label = ctk.CTkLabel(
                info_frame,
                text=f"\U0001F4DD {notes}",
                font=ctk.CTkFont(size=10),
                text_color="yellow"
            )
            notes_label.pack(anchor="w", pady=(2, 0))

        # Buttons: Check Balance | Copy | Delete
        button_frame = ctk.CTkFrame(card, fg_color="transparent")
        button_frame.pack(side="right", padx=10, pady=6)

        check_btn = ctk.CTkButton(
            button_frame,
            text="Check Balance",
            command=lambda a=address_data, l=balance_label: self.check_single_balance(a, l),
            width=100,
            height=28,
            font=ctk.CTkFont(size=11)
        )
        chain = address_data.get("chain", "")
        coin = address_data.get("coin", "")
        balance_supported = is_balance_supported(chain) or is_balance_supported(coin) or is_balance_supported(chain + " " + coin)
        if not self.online_mode or not balance_supported:
            check_btn.configure(state="disabled")
        check_btn.pack(pady=2)

        copy_btn = ctk.CTkButton(
            button_frame,
            text="Copy",
            command=lambda a=address_data["address"]: self.copy_to_clipboard(a),
            width=80,
            height=28
        )
        copy_btn.pack(pady=2)

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
                if not self.no_autolock:
                    self.lock_session()
                    return
                else:
                    # Keep the session open - show "No timeout" instead of counting
                    self.session_timer_label.configure(text="Session: No timeout")
            # Schedule next update (always, even when expired with no_autolock)
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
            try:
                result = DerivationEngine.derive_from_mnemonic(mnemonic, ch, address_index=idx)
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

    # --- v4.0: Online mode, balances, prices, settings ---

    def _load_vault_config(self):
        """Load config from vault: online_mode, display_currency, app_mode, api_keys."""
        config = self.key_manager.address_db.get("config", {})
        self.online_mode = config.get("online_mode", False)
        self.display_currency = config.get("display_currency", "none")
        # v4.2: New config fields (backward-compatible defaults)
        self.app_mode = config.get("app_mode", "standard")
        self.api_keys = config.get("api_keys", {})
        if not isinstance(self.api_keys, dict):
            self.api_keys = {}
        # v5.1: Vault tab selector state
        self.vault_selector_mode = config.get("vault_selector_mode", "address")
        self.vault_selected_account = config.get("vault_selected_account", "")
        # v4.2: Load RPC config from file and rebuild balance engine
        self.rpc_config = load_rpc_config(base_dir)
        self._rebuild_balance_engine()

    def _save_vault_config(self):
        """Save config to vault: online_mode, display_currency, app_mode, api_keys."""
        cfg = self.key_manager.address_db.setdefault("config", {})
        cfg["online_mode"] = self.online_mode
        cfg["display_currency"] = self.display_currency
        # v4.2: Save new config fields
        cfg["schema_version"] = 2
        cfg["app_mode"] = self.app_mode
        cfg["api_keys"] = self.api_keys
        # v5.1: Vault tab selector state
        cfg["vault_selector_mode"] = self.vault_selector_mode
        cfg["vault_selected_account"] = self.vault_selected_account
        self.key_manager.save_encrypted_data(self.current_password)

    def _rebuild_balance_engine(self):
        """Rebuild the BalanceEngine with current RPC config and API keys."""
        self.balance_engine = BalanceEngine(
            rpc_config=self.rpc_config,
            api_keys=self.api_keys
        )

    def _update_online_indicator(self):
        """Update the online/offline status label in the status bar."""
        if not hasattr(self, 'online_status_label'):
            return
        if self.online_mode:
            self.online_status_label.configure(text="\u25CF Online", text_color="#51cf94")
        else:
            self.online_status_label.configure(text="\u25CF Offline", text_color="gray60")

    def show_settings_dialog(self):
        """Settings dialog with Go Online toggle, currency, and Standard/Advanced mode (v4.2)."""
        from tkinter import messagebox

        dialog = ctk.CTkToplevel(self.root)
        dialog.title("Settings")
        dialog.transient(self.root)
        dialog.grab_set()
        self._center_dialog(dialog)

        # Set dialog size based on mode
        if self.app_mode == "advanced":
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

        online_label = ctk.CTkLabel(online_frame, text="Go Online",
                                    font=ctk.CTkFont(size=14, weight="bold"))
        online_label.pack(anchor="w")

        online_desc = ctk.CTkLabel(online_frame,
            text="When ON: enables read-only balance fetching and price feeds.\n"
                 "Only public addresses are queried. Private keys NEVER leave the vault.",
            font=ctk.CTkFont(size=10), text_color="gray60", justify="left")
        online_desc.pack(anchor="w", pady=(2, 5))

        online_switch = ctk.CTkSwitch(online_frame, text="Online Mode",
                                      command=self._on_online_toggle)
        if self.online_mode:
            online_switch.select()
        online_switch.pack(anchor="w")

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
            if val == self.display_currency:
                default_curr_label = label
                break
        curr_var = ctk.StringVar(value=default_curr_label)

        self._style_combobox()
        import tkinter.ttk as ttk
        curr_combo = ttk.Combobox(currency_frame, textvariable=curr_var,
                                  values=currency_values, state="readonly",
                                  width=45, style="Dark.TCombobox")
        curr_combo.pack(anchor="w", pady=(0, 5))

        # --- Advanced mode sections ---
        # Store references for saving
        rpc_url_vars = {}
        api_key_vars = {}

        if self.app_mode == "advanced":
            # --- RPC Endpoints section ---
            rpc_section = ctk.CTkFrame(form, fg_color="transparent")
            rpc_section.pack(fill="x", pady=(10, 5))

            ctk.CTkLabel(rpc_section, text="--- RPC Endpoints ---",
                         font=ctk.CTkFont(size=14, weight="bold")).pack(anchor="w")
            ctk.CTkLabel(rpc_section,
                text="Customize public RPC URLs for each chain. These are NOT secrets.\n"
                     "API keys are stored separately in the encrypted vault.",
                font=ctk.CTkFont(size=10), text_color="gray60").pack(anchor="w", pady=(2, 8))

            # Scrollable list of chain -> URL entries
            rpc_list_frame = ctk.CTkFrame(rpc_section, fg_color="transparent")
            rpc_list_frame.pack(fill="x", pady=(0, 5))

            if self.rpc_config is None:
                self.rpc_config = load_rpc_config(base_dir)

            for chain_id in sorted(self.rpc_config.keys()):
                entry = self.rpc_config[chain_id]
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

                # Per-chain reset button
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
                font=ctk.CTkFont(size=10), text_color="gray60").pack(anchor="w", pady=(2, 8))

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

                current_key = self.api_keys.get(provider, "")
                key_var = ctk.StringVar(value=current_key)
                api_key_vars[provider] = key_var
                key_entry = ctk.CTkEntry(row, textvariable=key_var, width=300,
                                         show="*", font=ctk.CTkFont(size=10))
                key_entry.pack(side="left", fill="x", expand=True, padx=(0, 5))

                # Show/hide toggle
                def make_toggle_fn(kentry, kbtn):
                    def toggle():
                        if kentry.cget("show") == "*":
                            kentry.configure(show="")
                            kbtn.configure(text="Hide")
                        else:
                            kentry.configure(show="*")
                            kbtn.configure(text="Show")
                    return toggle

                show_btn = ctk.CTkButton(row, text="Show", width=50, height=22,
                                         font=ctk.CTkFont(size=9), fg_color="gray30",
                                         command=make_toggle_fn(key_entry, None))
                # Fix: we need the button reference inside the toggle
                def make_toggle_fn2(kentry, kbtn_ref):
                    def toggle():
                        if kentry.cget("show") == "*":
                            kentry.configure(show="")
                            kbtn_ref.configure(text="Hide")
                        else:
                            kentry.configure(show="*")
                            kbtn_ref.configure(text="Show")
                    return toggle

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
                    self.app_mode = "standard"
                    self._save_vault_config()
                    dialog.destroy()
                    self.show_settings_dialog()

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
                    self.app_mode = "advanced"
                    self._save_vault_config()
                    dialog.destroy()
                    self.show_settings_dialog()

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
                    self.display_currency = val
                    break

            # v4.2: If advanced mode, save RPC URLs and API keys
            if self.app_mode == "advanced":
                # Update rpc_config with edited URLs
                if self.rpc_config is None:
                    self.rpc_config = load_rpc_config(base_dir)
                for chain_id, url_var in rpc_url_vars.items():
                    if chain_id in self.rpc_config:
                        self.rpc_config[chain_id]["url"] = url_var.get()

                # Save RPC config to file
                save_rpc_config(self.rpc_config, base_dir)

                # Save API keys to vault
                for provider, key_var in api_key_vars.items():
                    self.api_keys[provider] = key_var.get()

                # Rebuild balance engine with new config
                self._rebuild_balance_engine()

            self._save_vault_config()
            self._update_online_indicator()
            self.show_notification("Settings saved")
            dialog.destroy()
            # Re-render current account view to update Check Balance button states
            if self.current_account:
                self.select_account(self.current_pool or "Unassigned", self.current_account)

        btn_frame = ctk.CTkFrame(dialog, fg_color="transparent")
        btn_frame.pack(pady=10)
        ctk.CTkButton(btn_frame, text="Save", command=do_save, width=100,
                      fg_color=("#28a745", "#1e7e34")).pack(side="left", padx=10)
        ctk.CTkButton(btn_frame, text="Cancel", command=dialog.destroy, width=100,
                      fg_color="gray30").pack(side="left", padx=10)

    def _on_online_toggle(self):
        """Handle the Go Online switch toggle."""
        from tkinter import messagebox

        if not self.online_mode:
            confirm = messagebox.askyesno(
                "Go Online",
                "Going online will make network requests to public RPC endpoints and price APIs.\n\n"
                "Your private keys and mnemonics NEVER leave the vault.\n"
                "Only public addresses will be queried.\n\n"
                "Continue?",
                parent=self.root
            )
            if confirm:
                self.online_mode = True
                self._save_vault_config()
                self._update_online_indicator()
                self.show_notification("Online mode enabled")
                # Re-render current account view (full rebuild) to update
                # Check Balance button states — select_account clears and
                # repopulates the container so widgets are recreated.
                if self.current_account:
                    self.select_account(self.current_pool or "Unassigned", self.current_account)
        else:
            self.online_mode = False
            self._save_vault_config()
            self._update_online_indicator()
            self.show_notification("Offline mode - no network requests")
            # Re-render current account view (full rebuild) to update
            # Check Balance button states.
            if self.current_account:
                self.select_account(self.current_pool or "Unassigned", self.current_account)

        # v5.0: Update LP engine online state + refresh LP tab widgets
        if self.lp_engine:
            self.lp_engine.online_mode = self.online_mode
        self._lp_update_online_state()

        # v5.1: Update vault tracker online state
        if self.vault_tracker:
            self.vault_tracker.online_mode = self.online_mode
        self._vault_update_online_state()

    def refresh_balances(self):
        """Fetch balances for all addresses of the currently selected account."""
        if not self.online_mode:
            self.show_notification("Offline - enable Online Mode in Settings", error=True)
            return

        if not self.current_account:
            self.show_notification("Select an account first", error=True)
            return

        accounts_data = self.key_manager.address_db.get("accounts", {})
        addresses = accounts_data.get(self.current_account, {}).get("addresses", [])
        if not addresses:
            self.show_notification("No addresses to check", error=True)
            return

        self.show_notification("Fetching balances...")

        addr_list = []
        for addr in addresses:
            address = addr.get("address", "")
            chain = addr.get("coin", "") or addr.get("chain", "")
            if address and is_balance_supported(chain):
                addr_list.append({"address": address, "chain_type": chain})

        if not addr_list:
            self.show_notification("No balance-fetchable addresses", error=True)
            return

        if self.display_currency != "none":
            self.price_engine.fetch_prices(["usd", "aud"])

        def _do_fetch():
            results = self.balance_engine.fetch_balances_batch(addr_list)
            self.root.after(0, lambda: self._display_balances(results))

        threading.Thread(target=_do_fetch, daemon=True).start()

    def _display_balances(self, results):
        """Display fetched balances inline on the address cards."""
        self._last_balance_refresh = datetime.now()

        if hasattr(self, 'last_refresh_label'):
            self.last_refresh_label.configure(
                text=f"Updated: {self._last_balance_refresh.strftime('%H:%M:%S')}"
            )

        for addr_info, balance_data in results.items():
            if addr_info in self._balance_labels:
                label = self._balance_labels[addr_info]
                balances = balance_data.get("balances", [])
                error = balance_data.get("error", "")

                if error:
                    label.configure(text=f"\u26A0 {error}", text_color="#ff6b6b")
                elif balances:
                    parts = []
                    multi_chain = len(balances) > 1
                    for b in balances:
                        bal = b["balance"]
                        sym = b["symbol"]
                        chain_name = b.get("chain", "")
                        # Use "HL1" for Hyperliquid L1 spot balances
                        if chain_name == "hyperliquid_l1":
                            chain_display = "HL1"
                        else:
                            chain_display = chain_name.capitalize() if chain_name else ""

                        if bal < 0.001:
                            bal_str = f"{bal:.8f}".rstrip('0').rstrip('.')
                        elif bal < 1:
                            bal_str = f"{bal:.4f}"
                        else:
                            bal_str = f"{bal:.2f}"

                        if self.display_currency != "none":
                            fiat = self.price_engine.convert_balance_to_fiat(bal, sym, self.display_currency)
                            if fiat is not None:
                                curr_sym = self.display_currency.upper()
                                if multi_chain and chain_display:
                                    parts.append(f"{chain_display}: {bal_str} {sym} (${fiat:.2f} {curr_sym})")
                                else:
                                    parts.append(f"{bal_str} {sym} (${fiat:.2f} {curr_sym})")
                            else:
                                if multi_chain and chain_display:
                                    parts.append(f"{chain_display}: {bal_str} {sym}")
                                else:
                                    parts.append(f"{bal_str} {sym}")
                        else:
                            if multi_chain and chain_display:
                                parts.append(f"{chain_display}: {bal_str} {sym}")
                            else:
                                parts.append(f"{bal_str} {sym}")

                    label.configure(text="\n".join(parts), text_color="#51cf94")
                else:
                    label.configure(text="0 (no balance found)", text_color="gray60")

        self.show_notification("Balances updated")

    def check_single_balance(self, address_data, balance_label):
        """Fetch balance for a single address and update its label."""
        if not self.online_mode:
            self.show_notification("Offline - enable Online Mode in Settings", error=True)
            return

        addr = address_data.get("address", "")
        chain = address_data.get("chain", "")
        coin = address_data.get("coin", "")

        # Check if balance is supported using EITHER coin or chain
        balance_supported = is_balance_supported(chain) or is_balance_supported(coin) or is_balance_supported(chain + " " + coin)
        if not addr or not balance_supported:
            balance_label.configure(text="Unsupported chain", text_color="gray60")
            return

        balance_label.configure(text="Fetching...", text_color="gray60")
        try:
            balance_label.pack(anchor="w", pady=(2, 0))
        except Exception:
            pass

        def _do_fetch():
            def progress_cb(msg):
                self.root.after(0, lambda: balance_label.configure(text=msg, text_color="gray60"))
            result = self.balance_engine.fetch_balance(addr, chain, coin, progress_callback=progress_cb)
            self.root.after(0, lambda: self._update_single_balance(balance_label, result))

        threading.Thread(target=_do_fetch, daemon=True).start()

    def _update_single_balance(self, label, result):
        """Update a single balance label with fetch result."""
        # Pack the label now so it's visible
        try:
            label.pack(anchor="w", pady=(2, 0))
        except Exception:
            pass  # may already be packed

        balances = result.get("balances", [])
        error = result.get("error", "")

        if error:
            label.configure(text=f"\u26A0 {error}", text_color="#ff6b6b")
        elif balances:
            parts = []
            multi_chain = len(balances) > 1
            for b in balances:
                bal = b["balance"]
                sym = b["symbol"]
                chain_name = b.get("chain", "")
                # Use "HL1" for Hyperliquid L1 spot balances
                if chain_name == "hyperliquid_l1":
                    chain_display = "HL1"
                else:
                    chain_display = chain_name.capitalize() if chain_name else ""

                if bal < 0.001:
                    bal_str = f"{bal:.8f}".rstrip('0').rstrip('.')
                elif bal < 1:
                    bal_str = f"{bal:.4f}"
                else:
                    bal_str = f"{bal:.2f}"

                if self.display_currency != "none":
                    fiat = self.price_engine.convert_balance_to_fiat(bal, sym, self.display_currency)
                    if fiat is not None:
                        curr_sym = self.display_currency.upper()
                        if multi_chain and chain_display:
                            parts.append(f"{chain_display}: {bal_str} {sym} (${fiat:.2f} {curr_sym})")
                        else:
                            parts.append(f"{bal_str} {sym} (${fiat:.2f} {curr_sym})")
                    else:
                        if multi_chain and chain_display:
                            parts.append(f"{chain_display}: {bal_str} {sym}")
                        else:
                            parts.append(f"{bal_str} {sym}")
                else:
                    if multi_chain and chain_display:
                        parts.append(f"{chain_display}: {bal_str} {sym}")
                    else:
                        parts.append(f"{bal_str} {sym}")

            label.configure(text="\n".join(parts), text_color="#51cf94")
        else:
            label.configure(text="0 (no balance found)", text_color="gray60")

    # --- End v4.0 methods ---

    def lock_session(self):
        """Lock the session and return to login screen."""
        # v4.0: Reset online mode state
        self.online_mode = False
        self.no_autolock = False  # v5.1: Reset autolock preference
        self._balance_labels.clear()

        # v5.0: Clear LP engine state
        self.lp_engine = None
        self._lp_widgets = {}
        self._lp_auto_fetched = False  # v5.1: Reset auto-fetch flag on logout

        # v5.1: Clear vault tracker state
        self.vault_tracker = None
        self._vault_widgets = {}

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

    # ------------------------------------------------------------------
    # v5.0: LP Positions tab
    # ------------------------------------------------------------------

    def create_lp_tab(self, parent):
        """Build the LP Positions tab content with wallet scan + single position fetch."""
        root = ctk.CTkFrame(parent, fg_color="transparent")
        root.pack(fill="both", expand=True, padx=10, pady=10)

        # -- Row 1: Wallet address scan (existing) --
        wallet_bar = ctk.CTkFrame(root, fg_color="transparent")
        wallet_bar.pack(fill="x", pady=(0, 5))

        ctk.CTkLabel(wallet_bar, text="Wallet Address:",
                     font=ctk.CTkFont(size=13)).pack(side="left", padx=(0, 8))

        address_entry = ctk.CTkEntry(wallet_bar, width=380, font=ctk.CTkFont(size=12))
        address_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self._lp_widgets["address_entry"] = address_entry

        refresh_btn = ctk.CTkButton(
            wallet_bar, text="Scan Wallet", width=110, height=30,
            font=ctk.CTkFont(size=12, weight="bold"),
            command=self._lp_do_fetch,
        )
        refresh_btn.pack(side="right")
        self._lp_widgets["refresh_btn"] = refresh_btn

        # -- Row 2: Single position fetch (NEW) --
        pos_bar = ctk.CTkFrame(root, fg_color="transparent")
        pos_bar.pack(fill="x", pady=(0, 10))

        ctk.CTkLabel(pos_bar, text="Platform:",
                     font=ctk.CTkFont(size=13)).pack(side="left", padx=(0, 5))

        # Build venue list from adapter registry + auto-detect
        venue_options = ["Auto-detect"]
        try:
            if self.lp_engine:
                venue_options.extend(self.lp_engine.list_venues())
        except Exception:
            venue_options.append("hyperliquid")

        platform_menu = ctk.CTkOptionMenu(
            pos_bar, variable=None, values=venue_options, width=140,
            font=ctk.CTkFont(size=12),
        )
        platform_menu.set("Auto-detect")
        platform_menu.pack(side="left", padx=(0, 8))
        self._lp_widgets["platform_menu"] = platform_menu

        ctk.CTkLabel(pos_bar, text="Position ID / NFT ID / Pool Address:",
                     font=ctk.CTkFont(size=11)).pack(side="left", padx=(0, 5))

        position_entry = ctk.CTkEntry(pos_bar, width=280, font=ctk.CTkFont(size=12))
        position_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self._lp_widgets["position_entry"] = position_entry

        fetch_pos_btn = ctk.CTkButton(
            pos_bar, text="Fetch Position", width=120, height=30,
            font=ctk.CTkFont(size=12, weight="bold"),
            command=self._lp_do_fetch_single,
        )
        fetch_pos_btn.pack(side="right")
        self._lp_widgets["fetch_pos_btn"] = fetch_pos_btn

        # Offline banner
        self._lp_widgets["offline_banner"] = ctk.CTkLabel(
            root,
            text="🔒 Offline -- Enable Online Mode in Settings to fetch LP positions",
            font=ctk.CTkFont(size=11), text_color="gray50",
        )
        if not self.online_mode:
            self._lp_widgets["offline_banner"].pack(fill="x", pady=(0, 10))

        # Scrollable card container
        scroll = ctk.CTkScrollableFrame(root)
        scroll.pack(fill="both", expand=True)
        self._lp_widgets["scroll"] = scroll

        # Status label
        status_label = ctk.CTkLabel(
            root, text="", font=ctk.CTkFont(size=11), text_color="gray50",
        )
        status_label.pack(fill="x", pady=(5, 0))
        self._lp_widgets["status_label"] = status_label

        self._lp_update_online_state()

        # v5.1: Saved Pools counter label
        self._lp_widgets["saved_pools_count_label"] = ctk.CTkLabel(
            root, text="", font=ctk.CTkFont(size=10), text_color="#0d6efd",
        )
        self._lp_widgets["saved_pools_count_label"].pack(fill="x", pady=(0, 5))

        # v5.1: Auto-fetch is handled by _on_tab_changed() and _lp_prefill_address()
        # Do NOT fire auto-fetch here — the address entry may not be prefilled yet.

    def _lp_update_saved_pools_count(self, address: str = ""):
        """Update the Saved Pools counter label for the given address."""
        label = self._lp_widgets.get("saved_pools_count_label")
        if not label:
            return
        if not self.key_manager:
            return
        all_saved = load_saved_pools(self.key_manager.address_db)
        if not all_saved:
            label.configure(text="")
            return
        if address:
            wallet_lower = address.lower()
            count = sum(
                1 for e in all_saved
                if isinstance(e, dict) and e.get("wallet_address", "").lower() == wallet_lower
            )
            if count > 0:
                label.configure(text=f"Saved Pools: {count} for this wallet")
            else:
                label.configure(text=f"Saved Pools: {len(all_saved)} total")
        else:
            label.configure(text=f"Saved Pools: {len(all_saved)} total")

    def _lp_update_online_state(self):
        """Enable/disable LP tab widgets based on online_mode."""
        if not self._lp_widgets:
            return
        refresh_btn = self._lp_widgets.get("refresh_btn")
        if refresh_btn:
            refresh_btn.configure(state="normal" if self.online_mode else "disabled")
        fetch_pos_btn = self._lp_widgets.get("fetch_pos_btn")
        if fetch_pos_btn:
            fetch_pos_btn.configure(state="normal" if self.online_mode else "disabled")
        offline_banner = self._lp_widgets.get("offline_banner")
        if offline_banner:
            try:
                if self.online_mode:
                    offline_banner.pack_forget()
                else:
                    offline_banner.pack(fill="x", pady=(0, 10))
            except Exception:
                pass

    def _lp_prefill_address(self, account_name: str):
        """Pre-fill the LP tab address entry with the account's EVM address.

        Priority order:
        1. If entry already has an address, keep it.
        2. If saved pools exist whose wallet_address matches an EVM address
           of the selected account, pre-fill that address.
        3. If no account-specific match, use the first saved pool's wallet.
        4. Fall back to the account's first EVM/HYPE address.

        After filling, update the saved-pools counter and trigger auto-fetch
        if online mode is on and we haven't fetched yet.
        """
        if not self._lp_widgets or not self.key_manager:
            return
        entry = self._lp_widgets.get("address_entry")
        if not entry:
            return
        # If entry already has an address, keep it
        current = entry.get().strip()
        if current:
            self._lp_update_saved_pools_count(current)
            return

        all_saved = load_saved_pools(self.key_manager.address_db)
        accounts_data = self.key_manager.address_db.get("accounts", {})
        addresses = accounts_data.get(account_name, {}).get("addresses", [])

        # Step 2: Check if any saved pool's wallet matches an account EVM address
        account_evm_addresses = []
        for addr in addresses:
            coin = addr.get("coin", "").lower()
            chain = addr.get("chain", "").lower()
            if "evm" in coin or "evm" in chain or "hype" in coin or "hype" in chain:
                account_evm_addresses.append(addr.get("address", "").lower())

        for saved_entry in all_saved:
            saved_wallet = saved_entry.get("wallet_address", "").lower()
            if saved_wallet and saved_wallet in account_evm_addresses:
                entry.delete(0, "end")
                entry.insert(0, saved_entry.get("wallet_address", ""))
                self._lp_update_saved_pools_count(saved_entry.get("wallet_address", ""))
                self._lp_maybe_auto_fetch()
                return

        # Step 3: Fall back to the first saved pool's wallet globally
        if all_saved:
            first_wallet = all_saved[0].get("wallet_address", "")
            if first_wallet:
                entry.delete(0, "end")
                entry.insert(0, first_wallet)
                self._lp_update_saved_pools_count(first_wallet)
                self._lp_maybe_auto_fetch()
                return

        # Step 4: Fall back to the account's first EVM address
        for addr in addresses:
            coin = addr.get("coin", "").lower()
            chain = addr.get("chain", "").lower()
            if "evm" in coin or "evm" in chain or "hype" in coin or "hype" in chain:
                entry.delete(0, "end")
                entry.insert(0, addr.get("address", ""))
                self._lp_update_saved_pools_count(addr.get("address", ""))
                self._lp_maybe_auto_fetch()
                return

    def _lp_maybe_auto_fetch(self):
        """If online and not yet auto-fetched, schedule a fetch."""
        if self.online_mode and not self._lp_auto_fetched:
            entry = self._lp_widgets.get("address_entry")
            addr = entry.get().strip() if entry else ""
            if addr and addr.startswith("0x") and len(addr) == 42:
                self._lp_auto_fetched = True
                self.root.after(500, self._lp_do_fetch)

    def _lp_do_fetch(self):
        """Fetch LP positions for the entered address (threaded)."""
        if not self.lp_engine or not self.online_mode:
            self.show_notification("Offline - enable Online Mode in Settings", error=True)
            return
        entry = self._lp_widgets.get("address_entry")
        if not entry:
            return
        address = entry.get().strip()
        if not address:
            status = self._lp_widgets.get("status_label")
            if status:
                status.configure(text="Enter a wallet or pool address.")
            return
        status = self._lp_widgets.get("status_label")
        refresh_btn = self._lp_widgets.get("refresh_btn")
        scroll = self._lp_widgets.get("scroll")
        if status:
            status.configure(text="Fetching...")
        if refresh_btn:
            refresh_btn.configure(state="disabled")
        if scroll:
            for widget in scroll.winfo_children():
                widget.destroy()

        def _fetch_thread():
            try:
                positions = self.lp_engine.fetch_all_positions(address)
                # v5.1: Merge saved pools for this wallet (fast token-ID lookup)
                saved = load_saved_pools(self.key_manager.address_db, wallet_address=address)
                print(f"[saved_pools] loaded {len(saved)} saved pools for {address}")
                if saved:
                    from venue_adapters.hyperliquid_adapter import HyperliquidAdapter
                    adapter = HyperliquidAdapter()
                    for entry in saved:
                        tid = entry.get("token_id")
                        venue = entry.get("venue", "HyperEVM")
                        if tid and venue == "HyperEVM":
                            # Skip if already in scanned positions
                            pid = f"hyperevm:{tid}"
                            if any(p.position_id == pid for p in positions):
                                continue
                            try:
                                pos = adapter.fetch_evm_position_by_token_id(tid, self.price_engine)
                                if pos and not pos.error:
                                    positions.append(pos)
                            except Exception:
                                pass
                self.root.after(0, lambda: self._lp_on_loaded(positions, address))
            except OfflineError:
                self.root.after(0, lambda: self._lp_on_error("Offline mode enabled"))
            except Exception as e:
                self.root.after(0, lambda: self._lp_on_error(str(e)))

        threading.Thread(target=_fetch_thread, daemon=True).start()

    def _lp_do_fetch_single(self):
        """Fetch a single LP position by NFT ID / position ID / pool address (threaded)."""
        if not self.lp_engine or not self.online_mode:
            self.show_notification("Offline - enable Online Mode in Settings", error=True)
            return
        pos_entry = self._lp_widgets.get("position_entry")
        if not pos_entry:
            return
        position_id = pos_entry.get().strip()
        if not position_id:
            status = self._lp_widgets.get("status_label")
            if status:
                status.configure(text="Enter a position ID, NFT token ID, or pool address.")
            return

        # Get selected platform
        platform_menu = self._lp_widgets.get("platform_menu")
        selected_venue = None
        if platform_menu:
            val = platform_menu.get()
            if val and val != "Auto-detect":
                selected_venue = val

        status = self._lp_widgets.get("status_label")
        refresh_btn = self._lp_widgets.get("refresh_btn")
        fetch_pos_btn = self._lp_widgets.get("fetch_pos_btn")
        scroll = self._lp_widgets.get("scroll")
        if status:
            status.configure(text="Fetching position...")
        if refresh_btn:
            refresh_btn.configure(state="disabled")
        if fetch_pos_btn:
            fetch_pos_btn.configure(state="disabled")
        if scroll:
            for widget in scroll.winfo_children():
                widget.destroy()

        def _fetch_single_thread():
            try:
                position = self.lp_engine.fetch_position(
                    position_id, venue_key=selected_venue
                )
                self.root.after(0, lambda: self._lp_on_loaded([position], position_id))
            except OfflineError:
                self.root.after(0, lambda: self._lp_on_error("Offline mode enabled"))
            except Exception as e:
                self.root.after(0, lambda: self._lp_on_error(str(e)))

        threading.Thread(target=_fetch_single_thread, daemon=True).start()

    def _lp_on_loaded(self, positions, address):
        """Render fetched LP positions as cards."""
        scroll = self._lp_widgets.get("scroll")
        status = self._lp_widgets.get("status_label")
        refresh_btn = self._lp_widgets.get("refresh_btn")
        if not scroll:
            return
        for widget in scroll.winfo_children():
            widget.destroy()
        # v5.1: Deduplicate by venue:position_id
        seen = set()
        unique_positions = []
        for pos in positions:
            key = f"{pos.venue}:{pos.position_id}"
            if key not in seen:
                seen.add(key)
                unique_positions.append(pos)
        if not unique_positions:
            ctk.CTkLabel(scroll, text=f"No LP positions found for {address}",
                         font=ctk.CTkFont(size=13), text_color="gray60").pack(pady=20)
        else:
            for pos in unique_positions:
                self._lp_render_card(pos)
        if status:
            status.configure(text=f"Last check: {len(unique_positions)} position(s)")
        if refresh_btn:
            refresh_btn.configure(state="normal" if self.online_mode else "disabled")

    def _lp_on_error(self, message: str):
        """Show an error in the LP tab."""
        scroll = self._lp_widgets.get("scroll")
        status = self._lp_widgets.get("status_label")
        refresh_btn = self._lp_widgets.get("refresh_btn")
        if scroll:
            for widget in scroll.winfo_children():
                widget.destroy()
            ctk.CTkLabel(scroll, text=f"Error: {message}",
                         font=ctk.CTkFont(size=12), text_color="#ff6b6b").pack(pady=20)
        if status:
            status.configure(text="Fetch failed")
        if refresh_btn:
            refresh_btn.configure(state="normal" if self.online_mode else "disabled")

    def _lp_render_card(self, position):
        """Render one LPPosition as a card matching v4.1 address card style."""
        scroll = self._lp_widgets.get("scroll")
        if not scroll:
            return
        card = ctk.CTkFrame(scroll, corner_radius=10)
        card.pack(fill="x", pady=5, padx=5)
        info = ctk.CTkFrame(card, fg_color="transparent")
        info.pack(side="left", fill="both", expand=True, padx=10, pady=8)

        header_text = f"{position.health_emoji} {position.pair}  ·  {position.venue}"
        ctk.CTkLabel(info, text=header_text,
                     font=ctk.CTkFont(size=14, weight="bold")).pack(anchor="w")
        ctk.CTkLabel(info, text=f"ID: {position.position_id}",
                     font=ctk.CTkFont(size=10), text_color="gray50").pack(anchor="w", pady=(2, 0))

        range_parts = []
        if position.range_low is not None and position.range_high is not None:
            range_parts.append(f"Range: {position.range_low:g} \u2013 {position.range_high:g}")
        if position.current_price is not None:
            range_parts.append(f"Current: {position.current_price:g}")
        if range_parts:
            ctk.CTkLabel(info, text="  \u00b7  ".join(range_parts),
                         font=ctk.CTkFont(size=11), text_color="gray70").pack(anchor="w", pady=(2, 0))

        # v5.2: Position range slider with marker
        if position.range_low is not None and position.range_high is not None:
            pct = position.position_in_range_pct
            in_range = pct is not None and 0 <= pct <= 100
            marker_color = "#51cf94" if in_range else "#ff6b6b"
            marker_pos = max(0, min(100, pct)) if pct is not None else 50

            # Slider container
            slider_frame = ctk.CTkFrame(info, fg_color="transparent", height=28)
            slider_frame.pack(fill="x", pady=(4, 2), padx=(10, 0))

            # Track (background bar)
            track = ctk.CTkFrame(slider_frame, height=6, corner_radius=3,
                                 fg_color="gray25")
            track.pack(fill="x", side="top", pady=(8, 0))

            # Marker (small vertical bar on the track)
            marker = ctk.CTkFrame(slider_frame, width=4, height=14, corner_radius=2,
                                  fg_color=marker_color)
            # Place marker at the percentage position
            # Use place() for absolute positioning
            marker.place(relx=marker_pos / 100.0, rely=0.15, anchor="n")

            # Position % label
            pct_text = f"{pct:.1f}%" if pct is not None else "?"
            status_text = "In Range" if in_range else "Out of Range"
            ctk.CTkLabel(info, text=f"{pct_text} \u00b7 {status_text}",
                         font=ctk.CTkFont(size=10, weight="bold"),
                         text_color=marker_color).pack(anchor="w", pady=(2, 0))

        fees_parts = []
        # v5.1: If fees_note is set (HyperEVM/Project X), show the note instead
        # of a potentially misleading fee number.
        if getattr(position, "fees_note", None):
            ctk.CTkLabel(info, text=f"Fees: {position.fees_note}",
                         font=ctk.CTkFont(size=11, weight="bold"),
                         text_color="#ffd43b").pack(anchor="w", pady=(2, 0))
        else:
            if position.fees_earned_usd is not None and position.fees_earned_usd != 0:
                fees_parts.append(f"Fees earned: ${position.fees_earned_usd:,.2f}")
            if position.fees_earned:
                for sym, amt in position.fees_earned.items():
                    if amt:
                        fees_parts.append(f"{amt:g} {sym}")
            if fees_parts:
                ctk.CTkLabel(info, text="  ·  ".join(fees_parts),
                             font=ctk.CTkFont(size=11), text_color="gray70").pack(anchor="w", pady=(2, 0))

        value_parts = []
        if position.current_value_usd is not None:
            value_parts.append(f"Value: ${position.current_value_usd:,.2f}")
        if position.pnl_usd is not None:
            sign = "+" if position.pnl_usd >= 0 else ""
            value_parts.append(f"PnL: {sign}${position.pnl_usd:,.2f}")
        if position.pnl_pct is not None:
            sign = "+" if position.pnl_pct >= 0 else ""
            value_parts.append(f"({sign}{position.pnl_pct:.2f}%)")
        if value_parts:
            ctk.CTkLabel(info, text="  ·  ".join(value_parts),
                         font=ctk.CTkFont(size=11), text_color="gray70").pack(anchor="w", pady=(2, 0))

        ctk.CTkLabel(info, text=f"Suggestion: {position.suggested_action}",
                     font=ctk.CTkFont(size=11, weight="bold"),
                     text_color={"safe": "#51cf94", "watch": "#ffd43b",
                                 "near_edge": "#ff922b", "out_of_range": "#ff6b6b",
                                 "profit_take": "#74c0fc"}.get(position.status, "gray70")
                     ).pack(anchor="w", pady=(4, 0))

        if position.error:
            ctk.CTkLabel(info, text=f"Note: {position.error}",
                         font=ctk.CTkFont(size=10), text_color="gray50").pack(anchor="w", pady=(2, 0))

        button_frame = ctk.CTkFrame(card, fg_color="transparent")
        button_frame.pack(side="right", padx=10, pady=8)

        if position.position_id:
            ctk.CTkButton(button_frame, text="Copy", width=80, height=26,
                          font=ctk.CTkFont(size=10),
                          command=lambda pid=position.position_id: self.copy_to_clipboard(pid)
                          ).pack(pady=2)

        if self.app_mode == "advanced" and position.position_id and position.position_id.startswith("hyperevm:"):
            ctk.CTkButton(button_frame, text="Compound Fees", width=110, height=26,
                          font=ctk.CTkFont(size=10),
                          fg_color=("#20c997", "#1aa179"),
                          command=lambda pos=position: self._lp_compound_fees_dialog(pos)
                          ).pack(pady=2)

        if self.app_mode == "advanced" and position.position_id:
            ctk.CTkButton(button_frame, text="Collect Fees", width=100, height=26,
                          font=ctk.CTkFont(size=10),
                          fg_color=("#fd7e14", "#dc6602"),
                          command=lambda pos=position: self._lp_collect_fees_dialog(pos)
                          ).pack(pady=2)

        # v5.1: Save Pool button — saves public identifiers to saved_pools.json
        if position.position_id and position.position_id.startswith("hyperevm:"):
            ctk.CTkButton(button_frame, text="Save Pool", width=80, height=26,
                          font=ctk.CTkFont(size=10),
                          fg_color=("#0d6efd", "#0b5ed7"),
                          command=lambda pos=position: self._lp_save_pool(pos)
                          ).pack(pady=2)

    def _lp_save_pool(self, position):
        """Save the current position's public identifiers to saved_pools.json."""
        if not position.position_id or not position.position_id.startswith("hyperevm:"):
            self.show_notification("Only HyperEVM positions can be saved")
            return
        # Extract token_id from "hyperevm:<token_id>"
        try:
            token_id = int(position.position_id.split(":", 1)[1])
        except (ValueError, IndexError):
            self.show_notification("Could not parse token ID", error=True)
            return
        # Get wallet address from the address entry
        entry = self._lp_widgets.get("address_entry")
        wallet_address = entry.get().strip() if entry else ""
        if not wallet_address:
            self.show_notification("Enter a wallet address first", error=True)
            return
        pool_address = position.pool_id or ""
        pair = position.pair or ""
        venue = position.venue or "HyperEVM"
        print(f"[saved_pools] saving token_id={token_id} to address_db id={id(self.key_manager.address_db)}")
        ok = save_pool(self.key_manager.address_db, wallet_address, token_id, venue, pool_address, pair)
        if ok:
            # Re-encrypt the vault to persist the saved pool
            ok = self.key_manager.save_encrypted_data(self.current_password)
            print(f"[saved_pools] vault saved, saved_pools count={len(self.key_manager.address_db.get('saved_pools', []))}")
        if ok:
            self.show_notification(f"Pool saved: {pair} (#{token_id})")
        else:
            self.show_notification("Failed to save pool", error=True)

    def _lp_compound_fees_dialog(self, position):
        """Show confirmation dialog and compound fees for an LP position."""
        from tkinter import messagebox
        if not self.current_account:
            self.show_notification("Select a vault account first", error=True)
            return
        confirm = messagebox.askyesno(
            "Confirm: Compound Fees",
            "You are about to compound fees for position:\n"
            f"  {position.pair} ({position.position_id})\n\n"
            "This will submit multiple transactions on HyperEVM:\n"
            "  1. Collect accrued fees\n"
            "  2. Swap to optimal ratio (if needed)\n"
            "  3. Increase liquidity with collected amounts\n\n"
            "The key_manager_agent must be running and unlocked.\n\n"
            "Continue?",
            parent=self.root,
        )
        if not confirm:
            return
        self.show_notification("Compounding fees... (multi-TX operation)")

        def _do_compound():
            try:
                writer = self.lp_engine.get_writer("hyperliquid", self.current_password)
                if writer is None:
                    self.root.after(0, lambda: self.show_notification(
                        "Writer not available", error=True))
                    return
                if not writer.is_available():
                    self.root.after(0, lambda: self.show_notification(
                        "Agent not running. Start key_manager_agent with --serve.", error=True))
                    return
                tx_hashes = writer.compound_fees(CompoundFeesParams(
                    account=self.current_account,
                    position_id=position.position_id,
                ))
                if tx_hashes:
                    self.root.after(0, lambda: self.show_notification(
                        f"Compound fees done. {len(tx_hashes)} TXs submitted. First: {tx_hashes[0][:20]}..."))
                else:
                    self.root.after(0, lambda: self.show_notification(
                        "Compound fees: no transactions submitted", error=True))
            except Exception as e:
                error_msg = str(e)
                print(f"[compound_fees] error: {error_msg}")
                self.root.after(0, lambda: self.show_notification(
                    f"Compound error: {error_msg}", error=True))

        threading.Thread(target=_do_compound, daemon=True).start()

    def _lp_collect_fees_dialog(self, position):
        """Show confirmation dialog and collect fees for an LP position."""
        from tkinter import messagebox
        if not self.current_account:
            self.show_notification("Select a vault account first", error=True)
            return
        confirm = messagebox.askyesno(
            "Confirm: Collect Fees",
            "You are about to collect fees for position:\n"
            f"  {position.pair} ({position.position_id})\n\n"
            "This will spend gas on HyperEVM.\n"
            "The key_manager_agent must be running and unlocked.\n\n"
            "Continue?",
            parent=self.root,
        )
        if not confirm:
            return
        self.show_notification("Collecting fees...")

        def _do_collect():
            try:
                writer = self.lp_engine.get_writer("hyperliquid", self.current_password)
                if writer is None:
                    self.root.after(0, lambda: self.show_notification(
                        "Writer not available", error=True))
                    return
                if not writer.is_available():
                    self.root.after(0, lambda: self.show_notification(
                        "Agent not running. Start key_manager_agent with --serve.", error=True))
                    return
                tx_hash = writer.collect_fees(CollectFeesParams(
                    account=self.current_account,
                    position_id=position.position_id,
                ))
                if tx_hash:
                    self.root.after(0, lambda: self.show_notification(
                        f"Fees collected. TX: {tx_hash[:20]}..."))
                else:
                    self.root.after(0, lambda: self.show_notification(
                        "Collect failed: no tx hash returned", error=True))
            except Exception as e:
                error_msg = str(e)
                print(f"[collect_fees] error: {error_msg}")
                self.root.after(0, lambda: self.show_notification(
                    f"Collect error: {error_msg}", error=True))

        threading.Thread(target=_do_collect, daemon=True).start()

    # --- End v5.0 LP tab methods ---

    # ------------------------------------------------------------------
    # v5.1: Hyperliquid Vaults section
    # ------------------------------------------------------------------

    def create_vault_section(self, parent):
        """Build the Hyperliquid Vaults section inside the Vault tab.

        Placed below the existing two-panel (accounts + chain view) layout.
        Provides a wallet address entry, a refresh button, and a scrollable
        card container — mirroring the LP tab pattern.
        """
        section = ctk.CTkFrame(parent, corner_radius=0)
        section.pack(fill="both", expand=True, side="top", pady=(0, 0))

        # Section header
        header_frame = ctk.CTkFrame(section, fg_color="transparent")
        header_frame.pack(fill="x", padx=10, pady=(2, 2))

        ctk.CTkLabel(
            header_frame,
            text="\U0001F3DB Hyperliquid Vaults",
            font=ctk.CTkFont(size=15, weight="bold"),
        ).pack(side="left")

        vault_refresh_btn = ctk.CTkButton(
            header_frame, text="Refresh Vaults", width=110, height=28,
            font=ctk.CTkFont(size=12, weight="bold"),
            command=self._vault_do_fetch,
        )
        vault_refresh_btn.pack(side="right")
        self._vault_widgets["refresh_btn"] = vault_refresh_btn

        # Wallet selector bar: Address or Account mode
        wallet_bar = ctk.CTkFrame(section, fg_color="transparent")
        wallet_bar.pack(fill="x", padx=10, pady=(2, 5))

        # Mode selector dropdown
        selector_menu = ctk.CTkOptionMenu(
            wallet_bar, values=["Address", "Account"], width=90,
            font=ctk.CTkFont(size=11),
            command=self._vault_on_selector_change,
        )
        selector_menu.set(self.vault_selector_mode if hasattr(self, 'vault_selector_mode') else "Address")
        selector_menu.pack(side="left", padx=(0, 5))
        self._vault_widgets["selector_menu"] = selector_menu

        # Address entry (shown when "Address" mode is selected)
        vault_addr_entry = ctk.CTkEntry(wallet_bar, width=320,
                                        font=ctk.CTkFont(size=11))
        self._vault_widgets["address_entry"] = vault_addr_entry

        # Account dropdown (shown when "Account" mode is selected)
        account_names = []
        if self.key_manager:
            account_names = sorted(
                self.key_manager.address_db.get("accounts", {}).keys()
            )
        account_menu = ctk.CTkOptionMenu(
            wallet_bar, values=account_names if account_names else ["(no accounts)"],
            width=200, font=ctk.CTkFont(size=11),
            command=self._vault_on_account_change,
        )
        self._vault_widgets["account_menu"] = account_menu

        # Pack the appropriate widget based on current mode
        self._vault_apply_selector_mode()

        # Offline banner
        self._vault_widgets["offline_banner"] = ctk.CTkLabel(
            section,
            text="\U0001F512 Offline -- Enable Online Mode in Settings to fetch Hyperliquid vaults",
            font=ctk.CTkFont(size=11), text_color="gray50",
        )
        if not self.online_mode:
            self._vault_widgets["offline_banner"].pack(fill="x", padx=10, pady=(0, 5))

        # Scrollable card container (fixed height so it doesn't eat the whole tab)
        vault_scroll = ctk.CTkScrollableFrame(section)
        vault_scroll.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self._vault_widgets["scroll"] = vault_scroll

        # Status label
        vault_status = ctk.CTkLabel(
            section, text="", font=ctk.CTkFont(size=11), text_color="gray50",
        )
        vault_status.pack(fill="x", padx=10, pady=(0, 8))
        self._vault_widgets["status_label"] = vault_status

        self._vault_update_online_state()

    def _vault_update_online_state(self):
        """Enable/disable vault section widgets based on online_mode."""
        if not self._vault_widgets:
            return
        refresh_btn = self._vault_widgets.get("refresh_btn")
        if refresh_btn:
            refresh_btn.configure(
                state="normal" if self.online_mode else "disabled"
            )
        offline_banner = self._vault_widgets.get("offline_banner")
        if offline_banner:
            try:
                if self.online_mode:
                    offline_banner.pack_forget()
                else:
                    offline_banner.pack(fill="x", padx=10, pady=(0, 5))
            except Exception:
                pass

    def _vault_apply_selector_mode(self):
        """Show/hide address entry vs account menu based on selector mode."""
        selector = self._vault_widgets.get("selector_menu")
        entry = self._vault_widgets.get("address_entry")
        account_menu = self._vault_widgets.get("account_menu")
        if not selector or not entry:
            return
        mode = selector.get()
        if mode == "Account":
            entry.pack_forget()
            if account_menu:
                account_menu.pack(side="left", fill="x", expand=True, padx=(0, 5))
        else:
            if account_menu:
                account_menu.pack_forget()
            entry.pack(side="left", fill="x", expand=True, padx=(0, 5))

    def _vault_on_selector_change(self, choice: str):
        """Handle Address/Account mode switch."""
        self.vault_selector_mode = choice
        self._vault_apply_selector_mode()
        # Save config
        if self.key_manager and self.current_password:
            cfg = self.key_manager.address_db.setdefault("config", {})
            cfg["vault_selector_mode"] = choice
            self.key_manager.save_encrypted_data(self.current_password)
        # If Account mode, try to resolve and render
        if choice == "Account":
            account_menu = self._vault_widgets.get("account_menu")
            if account_menu:
                acct = account_menu.get()
                if acct and acct != "(no accounts)":
                    self._vault_on_account_change(acct)
        else:
            # Address mode: render saved vaults for current address
            entry = self._vault_widgets.get("address_entry")
            if entry:
                addr = entry.get().strip()
                if addr:
                    self._vault_render_saved_only(addr)

    def _vault_on_account_change(self, choice: str):
        """Handle account selection from dropdown."""
        if not choice or choice == "(no accounts)":
            return
        self.vault_selected_account = choice
        addr = self._vault_resolve_account_address(choice)
        entry = self._vault_widgets.get("address_entry")
        if entry and addr:
            entry.delete(0, "end")
            entry.insert(0, addr)
            # Save config
            if self.key_manager and self.current_password:
                cfg = self.key_manager.address_db.setdefault("config", {})
                cfg["vault_selected_account"] = choice
                self.key_manager.save_encrypted_data(self.current_password)
            # Render saved vaults
            self._vault_render_saved_only(addr)

    def _vault_resolve_account_address(self, account_name: str) -> str:
        """Resolve an account name to its first EVM/HYPE address."""
        if not self.key_manager:
            return ""
        accounts_data = self.key_manager.address_db.get("accounts", {})
        addresses = accounts_data.get(account_name, {}).get("addresses", [])
        for addr in addresses:
            coin = addr.get("coin", "").lower()
            chain = addr.get("chain", "").lower()
            if "evm" in coin or "evm" in chain or "hype" in coin or "hype" in chain:
                return addr.get("address", "")
        return ""

    def _vault_restore_state(self):
        """Restore vault tab state from config after login."""
        if not self._vault_widgets:
            return
        selector = self._vault_widgets.get("selector_menu")
        if not selector:
            return
        mode = getattr(self, 'vault_selector_mode', 'address')
        selector.set(mode)
        self._vault_apply_selector_mode()
        if mode == "Account":
            account_menu = self._vault_widgets.get("account_menu")
            if account_menu:
                acct = getattr(self, 'vault_selected_account', '')
                if acct:
                    # Refresh account list
                    account_names = []
                    if self.key_manager:
                        account_names = sorted(
                            self.key_manager.address_db.get("accounts", {}).keys()
                        )
                    if account_names:
                        account_menu.configure(values=account_names)
                    if acct in account_names:
                        account_menu.set(acct)
                        self._vault_on_account_change(acct)
        else:
            # Address mode: render saved vaults if address entry has content
            entry = self._vault_widgets.get("address_entry")
            if entry:
                addr = entry.get().strip()
                if addr:
                    self._vault_render_saved_only(addr)

    def _vault_prefill_address(self, account_name: str):
        """Pre-fill the vault section address entry with the account's EVM/HYPE address.

        Also renders any saved vaults for that address from the encrypted vault
        so the user sees cached data immediately on tab load.
        """
        if not self._vault_widgets or not self.key_manager:
            return
        # Refresh account dropdown with current accounts
        account_menu = self._vault_widgets.get("account_menu")
        if account_menu:
            account_names = sorted(
                self.key_manager.address_db.get("accounts", {}).keys()
            )
            if account_names:
                account_menu.configure(values=account_names)
        # Restore saved selector state
        self._vault_restore_state()
        # If restore didn't resolve an address, try the selected account
        entry = self._vault_widgets.get("address_entry")
        if not entry:
            return
        if entry.get().strip():
            return  # Already has an address from restore
        accounts_data = self.key_manager.address_db.get("accounts", {})
        addresses = accounts_data.get(account_name, {}).get("addresses", [])
        for addr in addresses:
            coin = addr.get("coin", "").lower()
            chain = addr.get("chain", "").lower()
            if "evm" in coin or "evm" in chain or "hype" in coin or "hype" in chain:
                wallet_addr = addr.get("address", "")
                entry.delete(0, "end")
                entry.insert(0, wallet_addr)
                # Render saved vaults from cache immediately
                if wallet_addr:
                    self._vault_render_saved_only(wallet_addr)
                return

    def _vault_do_fetch(self):
        """Fetch Hyperliquid vault positions for the entered address (threaded)."""
        if not self.vault_tracker or not self.online_mode:
            self.show_notification("Offline - enable Online Mode in Settings", error=True)
            return
        entry = self._vault_widgets.get("address_entry")
        if not entry:
            return
        address = entry.get().strip()
        if not address:
            status = self._vault_widgets.get("status_label")
            if status:
                status.configure(text="Enter a wallet address to fetch vaults.")
            return

        status = self._vault_widgets.get("status_label")
        refresh_btn = self._vault_widgets.get("refresh_btn")
        scroll = self._vault_widgets.get("scroll")
        if status:
            status.configure(text="Fetching vaults...")
        if refresh_btn:
            refresh_btn.configure(state="disabled")
        if scroll:
            for widget in scroll.winfo_children():
                widget.destroy()

        def _fetch_thread():
            try:
                positions = self.vault_tracker.fetch_positions(address)
                self.root.after(0, lambda: self._vault_on_loaded(positions, address))
            except Exception as e:
                self.root.after(0, lambda: self._vault_on_error(str(e)))

        threading.Thread(target=_fetch_thread, daemon=True).start()

    def _vault_on_loaded(self, positions, address):
        """Render fetched vault positions as cards."""
        scroll = self._vault_widgets.get("scroll")
        status = self._vault_widgets.get("status_label")
        refresh_btn = self._vault_widgets.get("refresh_btn")
        if not scroll:
            return
        for widget in scroll.winfo_children():
            widget.destroy()

        # Filter out the "error" sentinel position (from failed userVaultEquities)
        real_positions = [p for p in positions if not (p.vault_address == "" and p.error)]

        # v5.1: Merge in saved vaults that weren't returned by the API
        if self.key_manager:
            saved_vaults = self.key_manager.address_db.get("saved_vaults", [])
            if isinstance(saved_vaults, list):
                api_vault_addrs = {p.vault_address.lower() for p in real_positions}
                for entry in saved_vaults:
                    if not isinstance(entry, dict):
                        continue
                    if entry.get("wallet_address", "").lower() != address.lower():
                        continue
                    va = entry.get("vault_address", "")
                    if va and va.lower() not in api_vault_addrs:
                        saved_pos = VaultPosition.from_saved_dict(entry)
                        real_positions.append(saved_pos)

        if not real_positions:
            # Check if we got an error sentinel
            error_positions = [p for p in positions if p.error and p.vault_address == ""]
            if error_positions:
                ctk.CTkLabel(
                    scroll, text=f"Error: {error_positions[0].error}",
                    font=ctk.CTkFont(size=12), text_color="#ff6b6b",
                ).pack(pady=20)
            else:
                ctk.CTkLabel(
                    scroll,
                    text=f"No vault positions found for {address}",
                    font=ctk.CTkFont(size=13), text_color="gray60",
                ).pack(pady=20)
        else:
            for pos in real_positions:
                self._vault_render_card(pos)

        if status:
            count = len(real_positions)
            status.configure(text=f"Last check: {count} vault position(s)")
        if refresh_btn:
            refresh_btn.configure(
                state="normal" if self.online_mode else "disabled"
            )

    def _vault_on_error(self, message: str):
        """Show an error in the vault section."""
        scroll = self._vault_widgets.get("scroll")
        status = self._vault_widgets.get("status_label")
        refresh_btn = self._vault_widgets.get("refresh_btn")
        if scroll:
            for widget in scroll.winfo_children():
                widget.destroy()
            ctk.CTkLabel(
                scroll, text=f"Error: {message}",
                font=ctk.CTkFont(size=12), text_color="#ff6b6b",
            ).pack(pady=20)
        if status:
            status.configure(text="Fetch failed")
        if refresh_btn:
            refresh_btn.configure(
                state="normal" if self.online_mode else "disabled"
            )

    def _vault_render_card(self, position: VaultPosition):
        """Render one VaultPosition as a card matching LP card style."""
        scroll = self._vault_widgets.get("scroll")
        if not scroll:
            return

        card = ctk.CTkFrame(scroll, corner_radius=10)
        card.pack(fill="x", pady=4, padx=5)

        info = ctk.CTkFrame(card, fg_color="transparent")
        info.pack(side="left", fill="both", expand=True, padx=10, pady=8)

        # Heading: vault name (large bold font - same as old address font)
        header = f"\U0001F3DB {position.vault_name}"
        ctk.CTkLabel(
            info, text=header,
            font=ctk.CTkFont(size=14, weight="bold"),
        ).pack(anchor="w")

        # Sub-heading: vault address (smaller font - same as old Deposited font)
        if position.vault_address:
            ctk.CTkLabel(
                info, text=position.vault_address,
                font=ctk.CTkFont(size=10), text_color="gray50",
            ).pack(anchor="w", pady=(2, 0))

        # Metrics line: APR · TVL · Vault age · Deposit age
        metric_parts = []
        if position.apr is not None:
            metric_parts.append(f"APR: {position.apr:.1f}%")
        else:
            metric_parts.append("APR: see Hyperliquid")
        if position.tvl_usd is not None:
            metric_parts.append(f"TVL: ${position.tvl_usd:,.2f}")
        # Vault age: from first_deposit_time (earliest portfolio timestamp)
        if position.first_deposit_time is not None:
            delta = datetime.now(timezone.utc) - position.first_deposit_time
            days = delta.days
            hours = delta.seconds // 3600
            metric_parts.append(f"Vault age: {days}d {hours}h")
        else:
            metric_parts.append("Vault age: unknown")
        # Deposit age: from user_deposit_time (user's personal vaultEntryTime)
        if position.user_deposit_time is not None:
            delta = datetime.now(timezone.utc) - position.user_deposit_time
            days = delta.days
            hours = delta.seconds // 3600
            metric_parts.append(f"Deposit age: {days}d {hours}h")
        else:
            metric_parts.append("Deposit age: unknown")
        if metric_parts:
            ctk.CTkLabel(
                info, text="  \u00b7  ".join(metric_parts),
                font=ctk.CTkFont(size=11), text_color="gray70",
            ).pack(anchor="w", pady=(2, 0))

        # Deposited + Current value
        value_parts = []
        if position.deposited_usd is not None:
            value_parts.append(f"Deposited: ${position.deposited_usd:,.2f}")
        if position.current_value_usd is not None:
            value_parts.append(f"Current: ${position.current_value_usd:,.2f}")
        if value_parts:
            ctk.CTkLabel(
                info, text="  \u00b7  ".join(value_parts),
                font=ctk.CTkFont(size=11), text_color="gray70",
            ).pack(anchor="w", pady=(2, 0))

        # P&L (APR moved to metrics line above)
        pnl_parts = []
        if position.unrealized_pnl_usd is not None:
            sign = "+" if position.unrealized_pnl_usd >= 0 else ""
            pnl_parts.append(f"P&L: {sign}${position.unrealized_pnl_usd:,.2f}")
        if position.unrealized_pnl_pct is not None:
            sign = "+" if position.unrealized_pnl_pct >= 0 else ""
            pnl_parts.append(f"({sign}{position.unrealized_pnl_pct:.2f}%)")
        if pnl_parts:
            ctk.CTkLabel(
                info, text="  \u00b7  ".join(pnl_parts),
                font=ctk.CTkFont(size=11), text_color="gray70",
            ).pack(anchor="w", pady=(2, 0))

        # Shares + Share price
        share_parts = []
        if position.shares is not None:
            share_parts.append(f"Shares: {position.shares:,.2f}")
        if position.share_price_usd is not None:
            share_parts.append(f"Share price: ${position.share_price_usd:.4f}")
        if share_parts:
            ctk.CTkLabel(
                info, text="  \u00b7  ".join(share_parts),
                font=ctk.CTkFont(size=11), text_color="gray70",
            ).pack(anchor="w", pady=(2, 0))

        # Error note
        if position.error:
            ctk.CTkLabel(
                info, text=f"Note: {position.error}",
                font=ctk.CTkFont(size=10), text_color="#ff922b",
            ).pack(anchor="w", pady=(2, 0))

        # Debug: log raw_data if vault name fell back to address-based placeholder
        if position.vault_name.startswith("Vault 0x") or position.vault_name == "Unknown Vault":
            print(f"[vault] Name fallback for {position.vault_address}: raw_data={position.raw_data}")

        # Button frame
        button_frame = ctk.CTkFrame(card, fg_color="transparent")
        button_frame.pack(side="right", padx=10, pady=8)

        if position.vault_address:
            ctk.CTkButton(
                button_frame, text="Copy Address", width=100, height=26,
                font=ctk.CTkFont(size=10),
                command=lambda addr=position.vault_address: self.copy_to_clipboard(addr),
            ).pack(pady=2)

        # v5.1: Save Vault / Delete Saved button
        wallet_addr = ""
        entry = self._vault_widgets.get("address_entry")
        if entry:
            wallet_addr = entry.get().strip()

        is_saved = self._vault_is_saved(wallet_addr, position.vault_address)

        # Detect if this is a cached/saved-only position (no live data from API)
        is_cached = (not position.deposited_usd and not position.current_value_usd and is_saved)

        # Show "Saved (cached)" badge for saved-only vaults (no live data)
        if is_cached:
            ctk.CTkLabel(
                info, text="\U0001F516 Saved (cached)",
                font=ctk.CTkFont(size=9), text_color="#0d6efd",
            ).pack(anchor="w", pady=(2, 0))

        if is_saved:
            ctk.CTkButton(
                button_frame, text="Delete Saved", width=100, height=26,
                font=ctk.CTkFont(size=10),
                fg_color=("#dc3545", "#c82333"),
                hover_color=("#c82333", "#a71d2a"),
                command=lambda pos=position: self._vault_delete_saved(pos),
            ).pack(pady=2)
            # Show Refresh button for cached (saved-only) vaults
            if is_cached:
                ctk.CTkButton(
                    button_frame, text="Refresh", width=100, height=26,
                    font=ctk.CTkFont(size=10),
                    fg_color=("#17a2b8", "#138496"),
                    hover_color=("#138496", "#117a8b"),
                    command=lambda pos=position: self._vault_refresh_saved(pos),
                ).pack(pady=2)
        else:
            ctk.CTkButton(
                button_frame, text="Save Vault", width=100, height=26,
                font=ctk.CTkFont(size=10),
                fg_color=("#0d6efd", "#0b5ed7"),
                hover_color=("#0b5ed7", "#0a58ca"),
                command=lambda pos=position: self._vault_save_vault(pos),
            ).pack(pady=2)

    def _vault_is_saved(self, wallet_address: str, vault_address: str) -> bool:
        """Check if a vault is saved in the encrypted vault."""
        if not self.key_manager:
            return False
        saved = self.key_manager.address_db.get("saved_vaults", [])
        if not isinstance(saved, list):
            return False
        wl = wallet_address.lower()
        vl = vault_address.lower()
        return any(
            isinstance(e, dict)
            and e.get("wallet_address", "").lower() == wl
            and e.get("vault_address", "").lower() == vl
            for e in saved
        )

    def _vault_save_vault(self, position: VaultPosition):
        """Save a vault to the encrypted vault's saved_vaults list."""
        if not self.key_manager or not self.current_password:
            self.show_notification("Vault not unlocked", error=True)
            return
        entry = self._vault_widgets.get("address_entry")
        wallet_address = entry.get().strip() if entry else ""
        if not wallet_address:
            self.show_notification("Enter a wallet address first", error=True)
            return
        # Ensure wallet_address is set on the position
        if not position.wallet_address:
            position.wallet_address = wallet_address
        # Check for duplicate
        if self._vault_is_saved(wallet_address, position.vault_address):
            self.show_notification("Vault already saved")
            return
        saved_list = self.key_manager.address_db.setdefault("saved_vaults", [])
        if not isinstance(saved_list, list):
            saved_list = []
            self.key_manager.address_db["saved_vaults"] = saved_list
        saved_list.append(position.to_saved_dict())
        ok = self.key_manager.save_encrypted_data(self.current_password)
        if ok:
            self.show_notification(f"Vault saved: {position.vault_name}")
            # Re-render to show Delete Saved button
            self._vault_do_fetch()
        else:
            self.show_notification("Failed to save vault", error=True)

    def _vault_delete_saved(self, position: VaultPosition):
        """Remove a vault from the encrypted vault's saved_vaults list."""
        if not self.key_manager or not self.current_password:
            self.show_notification("Vault not unlocked", error=True)
            return
        entry = self._vault_widgets.get("address_entry")
        wallet_address = entry.get().strip() if entry else ""
        if not wallet_address:
            self.show_notification("Enter a wallet address first", error=True)
            return
        saved_list = self.key_manager.address_db.get("saved_vaults", [])
        if not isinstance(saved_list, list):
            self.show_notification("No saved vaults to delete", error=True)
            return
        wl = wallet_address.lower()
        vl = position.vault_address.lower()
        original_len = len(saved_list)
        self.key_manager.address_db["saved_vaults"] = [
            e for e in saved_list
            if not (
                isinstance(e, dict)
                and e.get("wallet_address", "").lower() == wl
                and e.get("vault_address", "").lower() == vl
            )
        ]
        if len(self.key_manager.address_db["saved_vaults"]) == original_len:
            self.show_notification("Vault not found in saved list", error=True)
            return
        ok = self.key_manager.save_encrypted_data(self.current_password)
        if ok:
            self.show_notification(f"Deleted saved vault: {position.vault_name}")
            self._vault_do_fetch()
        else:
            self.show_notification("Failed to delete saved vault", error=True)

    def _vault_refresh_saved(self, position: VaultPosition):
        """Refresh a saved vault's data by fetching live data from the API."""
        if not self.vault_tracker or not self.online_mode:
            self.show_notification("Offline - enable Online Mode in Settings", error=True)
            return
        if not self.key_manager or not self.current_password:
            self.show_notification("Vault not unlocked", error=True)
            return
        entry = self._vault_widgets.get("address_entry")
        wallet_address = entry.get().strip() if entry else ""
        if not wallet_address:
            self.show_notification("Enter a wallet address first", error=True)
            return
        vault_address = position.vault_address
        if not vault_address:
            self.show_notification("No vault address to refresh", error=True)
            return

        status = self._vault_widgets.get("status_label")
        if status:
            status.configure(text=f"Refreshing {position.vault_name}...")

        def _refresh_thread():
            try:
                fresh_pos = self.vault_tracker.fetch_single_position(
                    wallet_address, vault_address
                )
                if fresh_pos and not fresh_pos.error:
                    # Update the saved snapshot in address_db
                    saved_list = self.key_manager.address_db.get("saved_vaults", [])
                    if isinstance(saved_list, list):
                        wl = wallet_address.lower()
                        vl = vault_address.lower()
                        for i, e in enumerate(saved_list):
                            if (
                                isinstance(e, dict)
                                and e.get("wallet_address", "").lower() == wl
                                and e.get("vault_address", "").lower() == vl
                            ):
                                # Update with fresh snapshot
                                fresh_pos.wallet_address = wallet_address
                                saved_list[i] = fresh_pos.to_saved_dict()
                                break
                        self.key_manager.save_encrypted_data(self.current_password)
                        self.root.after(0, lambda: self.show_notification(
                            f"Refreshed: {fresh_pos.vault_name}"))
                    # Re-render
                    self.root.after(0, self._vault_do_fetch)
                else:
                    error_msg = fresh_pos.error if fresh_pos else "No data returned"
                    self.root.after(0, lambda: self.show_notification(
                        f"Refresh failed: {error_msg}", error=True))
            except Exception as e:
                self.root.after(0, lambda: self.show_notification(
                    f"Refresh error: {e}", error=True))

        threading.Thread(target=_refresh_thread, daemon=True).start()

    def _vault_render_saved_only(self, wallet_address: str):
        """Render saved vaults for a wallet from the encrypted vault (no API call).

        Called on tab load / wallet selection to show cached vaults immediately.
        """
        scroll = self._vault_widgets.get("scroll")
        if not scroll or not self.key_manager:
            return
        # Clear existing cards
        for widget in scroll.winfo_children():
            widget.destroy()

        saved_vaults = self.key_manager.address_db.get("saved_vaults", [])
        if not isinstance(saved_vaults, list):
            return

        wl = wallet_address.lower()
        saved_positions = []
        for entry in saved_vaults:
            if not isinstance(entry, dict):
                continue
            if entry.get("wallet_address", "").lower() != wl:
                continue
            va = entry.get("vault_address", "")
            if va:
                saved_pos = VaultPosition.from_saved_dict(entry)
                saved_positions.append(saved_pos)

        if saved_positions:
            for pos in saved_positions:
                self._vault_render_card(pos)
            status = self._vault_widgets.get("status_label")
            if status:
                status.configure(text=f"Showing {len(saved_positions)} saved vault(s) (cached)")
        else:
            ctk.CTkLabel(
                scroll,
                text="No saved vaults. Enter a wallet address and click Refresh Vaults to fetch.",
                font=ctk.CTkFont(size=13), text_color="gray60",
            ).pack(pady=20)

    # --- End v5.1 vault section methods ---

    def run(self):
        """Run the GUI application."""
        self.root.mainloop()


def main():
    """Main entry point for ColdStack GUI application."""
    app = ColdStackGUI()
    app.run()


if __name__ == "__main__":
    main()