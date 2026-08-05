"""
ColdStack GUI - Modern dark-themed interface for secure offline crypto key management.
Built with CustomTkinter.

Version: v5.1.3 (July 2026) - Swap Module + Wallet Card Redesign
"""
import sys
import os

# Guard sys.stdout/stderr for windowed/compiled mode where they may be None
if sys.stdout is None:
    sys.stdout = open(os.devnull, 'w', encoding='utf-8', errors='replace')
if sys.stderr is None:
    sys.stderr = open(os.devnull, 'w', encoding='utf-8', errors='replace')

import time as _time

# In the PyInstaller-frozen EXE, Python cannot locate the CA bundle that the
# stdlib ssl module uses by default. certifi ships a current CA bundle as a
# data file. We set SSL_CERT_FILE for any code that checks it, and we also
# monkey-patch ssl.create_default_context so every urllib.request HTTPS call
# (update check, balances, prices, vault tracker, LP) loads the certifi
# bundle explicitly. This must run before any network module is imported.
try:
    import certifi
    import ssl as _ssl_module
    _certifi_bundle = certifi.where()
    os.environ.setdefault("SSL_CERT_FILE", _certifi_bundle)
    os.environ.setdefault("SSL_CERT_DIR", os.path.dirname(_certifi_bundle))

    _original_create_default_context = _ssl_module.create_default_context

    def _create_default_context_with_certifi(*args, **kwargs):
        context = _original_create_default_context(*args, **kwargs)
        try:
            context.load_verify_locations(_certifi_bundle)
        except Exception:
            pass
        return context

    _ssl_module.create_default_context = _create_default_context_with_certifi
except Exception:
    pass

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
    # Redirect to a log file next to the EXE so errors remain diagnosable.
    try:
        _log_path = os.path.join(os.path.dirname(sys.executable), 'gui_debug.log')
        # Use line-buffered mode (buffering=1) so output is flushed after each line
        _log_file = open(_log_path, 'a', encoding='utf-8', errors='replace', buffering=1)
        sys.stdout = _log_file
        sys.stderr = _log_file
        # Write a startup marker so we can confirm logging works
        print(f"\n{'='*60}")
        print(f"ColdStack started at {_time.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"EXE: {sys.executable}")
        print(f"Log: {_log_path}")
        print(f"{'='*60}")
    except Exception as e:
        # Fallback: try user temp directory
        try:
            import tempfile
            _log_path = os.path.join(tempfile.gettempdir(), 'coldstack_debug.log')
            _log_file = open(_log_path, 'a', encoding='utf-8', errors='replace', buffering=1)
            sys.stdout = _log_file
            sys.stderr = _log_file
            print(f"Primary log failed ({e}), using fallback: {_log_path}")
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
import atexit
import pyperclip
import queue

# Add src directory to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from crypto_engine import CryptoEngine
from derivation_engine import DerivationEngine
from balance_engine import BalanceEngine, is_balance_supported
from price_engine import PriceEngine, DISPLAY_CURRENCY_OPTIONS
from rpc_config import load_rpc_config, save_rpc_config, get_default_endpoints, get_default_for_chain
# v5.2.1: Appearance mode management (carved out of gui_main_v5.py)
from appearance import load_appearance_mode, save_appearance_mode, style_combobox
# v5.0: LP Engine imports
from lp_engine import LPEngine, OfflineError
from saved_pools import migrate_saved_pools_json
# v5.1.4: Settings dialog carved out to keep gui_main_v5.py focused
from settings_dialog import open_settings_dialog
# v5.1.4: Account dialogs, Vault tab, and LP tab extracted into separate modules
from account_dialogs import (
    confirm_delete_address, show_delete_account_dialog, show_add_account_dialog,
    show_add_address_dialog, show_add_mnemonic_dialog, show_add_private_key_dialog,
    show_derivation_dialog, show_derive_all_chains_dialog, _save_derived_to_account,
    show_init_vault_dialog, show_change_password_dialog, show_import_dialog,
)
from vault_tab import VaultTab
from lp_tab import LPTab
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
from chain_options import CHAIN_OPTIONS, DERIVATION_CHAINS


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
# v5.2.1: Apply saved appearance mode BEFORE any widgets are created.
# This reads from a plaintext JSON file (not the encrypted vault) so it
# works before login. The vault config still stores the preference too,
# but this file is the authoritative source at startup.
_startup_appearance = load_appearance_mode()
ctk.set_appearance_mode(_startup_appearance)
ctk.set_default_color_theme("dark-blue")


class ColdStackGUI:
    """Main GUI application for ColdStack — secure offline crypto key vault."""

    # v5.1: Map user-friendly platform names to raw adapter keys.
    # Future adapters: add entries here (friendly_name -> adapter_key).
    LP_PLATFORM_MAP = {
        "HyperEVM (Project X)": "hyperliquid",
        "Krystal (BSC)": "krystal",
    }
    # Reverse map for converting adapter keys to friendly display names.
    LP_PLATFORM_MAP_reverse = {v: k for k, v in LP_PLATFORM_MAP.items()}

    def __init__(self):
        self.root = ctk.CTk()
        self.root.title("ColdStack - Secure Crypto Key Vault")
        self.root.geometry("1200x800")
        # v5.2.1: Handle window close event — clean up session file
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

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
        # v5.2.1: Appearance mode (light/dark/system) — loaded at startup
        self.appearance_mode = _startup_appearance
        self.api_keys: Dict[str, str] = {}
        self.rpc_config: Optional[Dict[str, Dict[str, Any]]] = None

        # v5.0: LP Engine instance (created after login)
        self.lp_engine: Optional[LPEngine] = None
        self.lp_tab: Optional[LPTab] = None

        # v5.1: Vault tracker instance (created after login)
        self.vault_tracker: Optional[HyperliquidVaultTracker] = None
        self.vault_tab: Optional[VaultTab] = None

        # v5.1: Embedded key_manager_agent HTTP server
        self._agent_server: Optional[Any] = None
        self._agent_thread: Optional[threading.Thread] = None

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
        self._notification_close_btn = None

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
            text="v5.2.1 - ColdStack | Krystal Skeleton + Light/Dark Mode",
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

                # v5.1: Start embedded key_manager_agent HTTP server so Collect/Compound
                # Fees work without a separate agent process.
                self._start_embedded_agent()

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
            self._notification_close_btn = None

            # v5.1: Ensure embedded agent is running (restarts if dashboard is
            # recreated, e.g., after returning from lock screen path).
            self._start_embedded_agent()

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
        if not hasattr(self, 'vault_tab') or self.vault_tab is None:
            self.vault_tab = VaultTab(self)
        self.vault_tab.create_section(vaults_tab)

        # v5.1: Restore HL1 Vaults selector state and auto-load all saved vaults
        self.vault_tab._vault_restore_state()

        # LP tab: LP Positions content
        if not hasattr(self, 'lp_tab') or self.lp_tab is None:
            self.lp_tab = LPTab(self)
        self.lp_tab.create_tab(lp_tab)

        # v5.1: Restore LP tab selector state after tab creation
        self.lp_tab._lp_restore_state()

        # v5.1: Tab change callback for auto-fetching saved pools
        self.tabview.configure(command=self._on_tab_changed)

        # Status bar (stays at root level, below tabs)
        self.create_status_bar()

    def _on_tab_changed(self):
        """Handle tab change — preload saved pools when LP tab is first shown."""
        if not hasattr(self, 'tabview'):
            return
        try:
            current_tab = self.tabview.get()
        except Exception:
            return
        if current_tab == "LP Positions":
            if (self.online_mode
                    and hasattr(self, 'lp_tab')
                    and not self.lp_tab._lp_auto_fetched):
                entry = self.lp_tab._lp_widgets.get("address_entry")
                addr = entry.get().strip() if entry else ""
                if addr and addr.startswith("0x") and len(addr) == 42:
                    self.lp_tab._lp_auto_fetched = True
                    # Only preload saved pools (fast, silent) — don't trigger full scan
                    self.lp_tab._lp_preload_saved_only(addr)

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

                current_version = "5.2.1"
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
        if self.lp_tab is not None:
            self.lp_tab._lp_prefill_address(account_name)

        # v5.1: Pre-fill vault section address entry
        if self.vault_tab is not None:
            self.vault_tab._vault_prefill_address(account_name)

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

        # Line 2: Address with inline copy icon
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

        # Buttons: Check Balance | Swap | Remove
        button_frame = ctk.CTkFrame(card, fg_color="transparent")
        button_frame.pack(side="right", padx=10, pady=6)

        chain = address_data.get("chain", "")
        coin = address_data.get("coin", "")
        chain_str_check = (chain + " " + coin).lower()
        balance_supported = is_balance_supported(chain) or is_balance_supported(coin) or is_balance_supported(chain + " " + coin)
        swap_supported = ("hype" in chain_str_check or "hyperliquid" in chain_str_check or
                          "evm" in chain_str_check or "btc" in chain_str_check or
                          "eth" in chain_str_check or "sol" in chain_str_check)

        check_btn = ctk.CTkButton(
            button_frame,
            text="Check Balance",
            command=lambda a=address_data, l=balance_label: self.check_single_balance(a, l),
            width=100,
            height=28,
            font=ctk.CTkFont(size=11),
            fg_color=("#2b6cb0", "#2c5282"),
            hover_color=("#3182ce", "#4299e1")
        )
        if not self.online_mode or not balance_supported:
            check_btn.configure(state="disabled")
        check_btn.pack(pady=2)

        if swap_supported and self.online_mode:
            ctk.CTkButton(
                button_frame,
                text="Swap",
                width=100,
                height=28,
                font=ctk.CTkFont(size=11),
                fg_color=("#6b46c1", "#553c9a"),
                hover_color=("#805ad5", "#6b46c1"),
                command=lambda addr=address_data["address"], acct=account_name: self._open_swap_dialog(addr, acct)
            ).pack(pady=2)

        delete_btn = ctk.CTkButton(
            button_frame,
            text="Remove",
            command=lambda idx=addr_index, acct=account_name: self.confirm_delete_address(acct, idx),
            width=100,
            height=28,
            fg_color=("#c53030", "#9b2c2c"),
            hover_color=("#e53e3e", "#c53030")
        )
        delete_btn.pack(pady=2)

    def _get_agent_url(self) -> str:
        """Return the key_manager_agent HTTP endpoint."""
        return "http://127.0.0.1:8842"

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


    def copy_to_clipboard(self, address):
        """Copy address to clipboard."""
        try:
            pyperclip.copy(address)
            self.show_notification(f"Copied: {address[:20]}...")
        except Exception as e:
            self.show_notification(f"Failed to copy: {str(e)}", error=True)

    def show_notification(self, message, error=False):
        """Show a notification toast. Errors persist until dismissed; success auto-dismisses."""
        # Cancel any existing notification timer
        if self._notification_timer is not None:
            self.root.after_cancel(self._notification_timer)
            self._notification_timer = None

        # Destroy any existing close button
        if hasattr(self, '_notification_close_btn') and self._notification_close_btn is not None:
            self._notification_close_btn.place_forget()
            self._notification_close_btn = None

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
                fg_color=("#3a1a1a", "#3a1a1a")
            )
        else:
            self._notification_label.configure(
                text=f"\u2713 {message}",
                text_color="#51cf94",
                fg_color=("#1a3a2a", "#1a3a2a")
            )

        # Place notification at bottom center, above status bar
        self._notification_label.place(relx=0.5, rely=0.93, anchor="center")
        self._notification_label.lift()

        if error:
            # Error notifications persist — add a close button to the right of the label
            self._notification_close_btn = ctk.CTkLabel(
                self.root,
                text="\u2715",
                font=ctk.CTkFont(size=14, weight="bold"),
                text_color="#ff6b6b",
                fg_color=("#3a1a1a", "#3a1a1a"),
                corner_radius=8,
                width=30,
                height=30,
                cursor="hand2"
            )
            # Fixed offset fallback for reliable placement
            close_x = 200
            try:
                close_x = self._notification_label.winfo_reqwidth() // 2 + 25
            except Exception:
                pass
            self._notification_close_btn.place(relx=0.5, rely=0.93, anchor="center", x=close_x)
            self._notification_close_btn.lift()
            self._notification_close_btn.bind("<Button-1>", lambda e: self._dismiss_notification())
            # Do NOT set auto-dismiss timer for errors
        else:
            # Success notifications auto-dismiss after 3 seconds
            self._notification_timer = self.root.after(3000, self._dismiss_notification)

    def _dismiss_notification(self):
        """Dismiss the current notification."""
        if self._notification_label is not None:
            self._notification_label.place_forget()
        if hasattr(self, '_notification_close_btn') and self._notification_close_btn is not None:
            self._notification_close_btn.place_forget()
            self._notification_close_btn = None
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
        """Apply theme-aware styling to a ttk Combobox (delegates to appearance module)."""
        style_combobox(style_name)

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
                    text_color=("#1a1a1a", "#dce4ee"),
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
                    text_color=("#1a1a1a", "#dce4ee"),
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












        # --- End v2 dialogs ---

    # --- v4.0: Online mode, balances, prices, settings ---

    def _load_vault_config(self):
        """Load config from vault: online_mode, display_currency, app_mode, api_keys."""
        config = self.key_manager.address_db.get("config", {})
        self.online_mode = config.get("online_mode", False)
        self.display_currency = config.get("display_currency", "none")
        # v4.2: New config fields (backward-compatible defaults)
        self.app_mode = config.get("app_mode", "standard")
        # v5.2.1: Appearance mode — sync vault config with startup file
        vault_mode = config.get("appearance_mode", _startup_appearance)
        # The plaintext file is authoritative at startup; sync vault to match
        self.appearance_mode = _startup_appearance
        if vault_mode != _startup_appearance:
            # Vault was out of sync — update it
            cfg = self.key_manager.address_db.setdefault("config", {})
            cfg["appearance_mode"] = _startup_appearance
            self.key_manager.save_encrypted_data(self.current_password)
        self.api_keys = config.get("api_keys", {})
        if not isinstance(self.api_keys, dict):
            self.api_keys = {}
        # v5.1: Vault tab selector state (default to Account mode)
        mode = config.get("vault_selector_mode", "Account")
        self.vault_selector_mode = "Account" if str(mode).lower() == "account" else "Address"
        self.vault_selected_account = config.get("vault_selected_account", "")
        # v5.1: LP tab selector state
        self.lp_selector_mode = config.get("lp_selector_mode", "Address")
        self.lp_selected_account = config.get("lp_selected_account", "")
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
        # v5.2.1: Appearance mode
        cfg["appearance_mode"] = self.appearance_mode
        # v5.2.1: Also write to plaintext file for next startup
        save_appearance_mode(self.appearance_mode)
        cfg["api_keys"] = self.api_keys
        # v5.1: Vault tab selector state
        cfg["vault_selector_mode"] = self.vault_selector_mode
        cfg["vault_selected_account"] = self.vault_selected_account
        # v5.1: LP tab selector state
        cfg["lp_selector_mode"] = self.lp_selector_mode
        cfg["lp_selected_account"] = self.lp_selected_account
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
        return open_settings_dialog(self)

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
        if self.lp_tab is not None:
            self.lp_tab._lp_update_online_state()

        # v5.1: Update vault tracker online state
        if self.vault_tracker:
            self.vault_tracker.online_mode = self.online_mode
        if self.vault_tab is not None:
            self.vault_tab._vault_update_online_state()

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

    def _format_currency(self, usd_amount: float, include_usd_label: bool = False) -> str:
        """Format a USD amount with optional secondary currency conversion.

        Args:
            usd_amount: The amount in USD.
            include_usd_label: If True, always include "USD" label.

        Returns:
            Formatted string like "$1,234.56" or "$1,234.56 USD · $1,890.23 AUD"
        """
        if self.display_currency == "none" or self.display_currency == "usd":
            if include_usd_label:
                return f"${usd_amount:,.2f} USD"
            return f"${usd_amount:,.2f}"
        # Convert to selected currency
        converted = self.price_engine.convert_balance_to_fiat(usd_amount, "USDC", self.display_currency)
        if converted is not None:
            curr_sym = self.display_currency.upper()
            return f"${usd_amount:,.2f} USD · ${converted:,.2f} {curr_sym}"
        # Conversion failed — show USD only
        return f"${usd_amount:,.2f} USD"

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
                        elif chain_name == "hyperliquid_evm":
                            chain_display = "HyperEVM"
                        elif chain_name == "hyperliquid":
                            chain_display = "HyperEVM"
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
                elif chain_name == "hyperliquid_evm":
                    chain_display = "HyperEVM"
                elif chain_name == "hyperliquid":
                    chain_display = "HyperEVM"
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

    def _on_close(self):
        """Handle window close event — ensure session file is cleaned up.

        This fires when the user closes the window via X button, Alt+F4,
        or any OS-level window close action. Without this, the
        .key_manager_session file (containing the base64-encoded password)
        would persist on disk.
        """
        try:
            # Stop the embedded agent server first (frees the port)
            self._stop_embedded_agent()
        except Exception:
            pass

        try:
            # Delete the session file if it exists
            if self.key_manager:
                self.key_manager.end_session()
        except Exception:
            pass

        # Destroy the root window and exit
        try:
            self.root.destroy()
        except Exception:
            pass

    def lock_session(self):
        """Lock the session and return to login screen."""
        # v4.0: Reset online mode state
        self.online_mode = False
        self.no_autolock = False  # v5.1: Reset autolock preference
        self._balance_labels.clear()

        # v5.0: Clear LP engine state
        self.lp_engine = None
        if self.lp_tab is not None:
            self.lp_tab._lp_widgets = {}
            self.lp_tab._lp_auto_fetched = False
        self.lp_tab = None

        # v5.1: Clear vault tracker state
        self.vault_tracker = None
        if self.vault_tab is not None:
            self.vault_tab._vault_widgets = {}
        self.vault_tab = None

        # v5.1: Stop embedded key_manager_agent HTTP server
        self._stop_embedded_agent()

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

    def _stop_embedded_agent(self):
        """Shut down the embedded key_manager_agent HTTP server, if running."""
        if hasattr(self, "_agent_server") and self._agent_server:
            try:
                self._agent_server.shutdown()
                self._agent_server = None
                print("[embedded_agent] HTTP server stopped")
            except Exception as e:
                print(f"[embedded_agent] Error stopping server: {e}")

    def _start_embedded_agent(self):
        """Start the key_manager_agent HTTP server in a background thread.

        This allows Collect/Compound Fees to work without a separate agent
        process. The server runs on localhost:8842 and uses the already-
        unlocked vault data. If an external agent is already listening on
        port 8842, the embedded agent is skipped.
        """
        try:
            from key_manager_agent import KeyManagerAgent
            from http.server import BaseHTTPRequestHandler, HTTPServer
            import socketserver

            # If an external agent is already running, don't start our own.
            try:
                from venue_adapters.hyperliquid_writer import HyperliquidWriter
                test_writer = HyperliquidWriter()
                if test_writer.is_available():
                    print("[embedded_agent] External agent already available on port 8842")
                    return
            except Exception:
                pass

            if not self.key_manager or not self.current_password:
                print("[embedded_agent] Vault not unlocked, cannot start embedded agent")
                return

            vault_path = str(self.key_manager.data_file)
            password = self.current_password

            agent = KeyManagerAgent(
                vault_path=vault_path,
                password=password,
                session_timeout=99999,  # GUI manages session lifecycle
            )
            if not agent.unlocked:
                print("[embedded_agent] Failed to unlock vault for embedded agent")
                return

            class AgentHandler(BaseHTTPRequestHandler):
                def _send_json(self, code, data):
                    body = json.dumps(data).encode("utf-8")
                    self.send_response(code)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)

                def do_POST(self):
                    content_length = int(self.headers.get("Content-Length", 0))
                    body = self.rfile.read(content_length)
                    try:
                        cmd = json.loads(body.decode("utf-8"))
                        result = agent.handle_command(cmd)
                        self._send_json(200, result)
                    except Exception as e:
                        self._send_json(200, {"status": "error", "error": str(e)})

                def do_GET(self):
                    self._send_json(200, agent.status())

                def log_message(self, format, *args):
                    pass  # Suppress access logs

            def _serve():
                try:
                    server = HTTPServer(("127.0.0.1", 8842), AgentHandler)
                    server.daemon_threads = True
                    self._agent_server = server
                    print("[embedded_agent] HTTP server started on http://127.0.0.1:8842")
                    server.serve_forever()
                except OSError as e:
                    if "Address already in use" in str(e):
                        print("[embedded_agent] Port 8842 already in use — external agent may be running")
                    else:
                        print(f"[embedded_agent] Failed to start HTTP server: {e}")
                except Exception as e:
                    print(f"[embedded_agent] Server error: {e}")

            self._agent_thread = threading.Thread(target=_serve, daemon=True)
            self._agent_thread.start()
            print("[embedded_agent] Agent thread started")
        except ImportError:
            print("[embedded_agent] key_manager_agent module not available")
        except Exception as e:
            print(f"[embedded_agent] Error starting agent: {e}")

    # ------------------------------------------------------------------
    # v5.0: LP Positions tab
    # ------------------------------------------------------------------


        # v5.1: Auto-fetch is handled by _on_tab_changed() and _lp_prefill_address()
        # Do NOT fire auto-fetch here — the address entry may not be prefilled yet.









































    # --- End v5.0 LP tab methods ---


    # ------------------------------------------------------------------
    # v5.1: Hyperliquid Vaults section
    # ------------------------------------------------------------------























    # --- End v5.1 vault section methods ---

    def confirm_delete_address(self, account_name, addr_index):
        """Delegate to account_dialogs."""
        return confirm_delete_address(self, account_name, addr_index)

    def show_delete_account_dialog(self):
        """Delegate to account_dialogs."""
        return show_delete_account_dialog(self)

    def show_add_account_dialog(self):
        """Delegate to account_dialogs."""
        return show_add_account_dialog(self)

    def show_add_address_dialog(self):
        """Delegate to account_dialogs."""
        return show_add_address_dialog(self)

    def show_add_mnemonic_dialog(self):
        """Delegate to account_dialogs."""
        return show_add_mnemonic_dialog(self)

    def show_add_private_key_dialog(self):
        """Delegate to account_dialogs."""
        return show_add_private_key_dialog(self)

    def show_derivation_dialog(self, account_name):
        """Delegate to account_dialogs."""
        return show_derivation_dialog(self, account_name)

    def show_derive_all_chains_dialog(self, account_name):
        """Delegate to account_dialogs."""
        return show_derive_all_chains_dialog(self, account_name)

    def _save_derived_to_account(self, account_name, chain, data, status_label):
        """Delegate to account_dialogs."""
        return _save_derived_to_account(self, account_name, chain, data, status_label)

    def show_init_vault_dialog(self):
        """Delegate to account_dialogs."""
        return show_init_vault_dialog(self)

    def show_change_password_dialog(self):
        """Delegate to account_dialogs."""
        return show_change_password_dialog(self)

    def show_import_dialog(self):
        """Delegate to account_dialogs."""
        return show_import_dialog(self)

    def run(self):
        """Run the GUI application."""
        self.root.mainloop()


def main():
    """Main entry point for ColdStack GUI application."""
    app = ColdStackGUI()

    # v5.2.1: atexit fallback — delete session file if WM_DELETE_WINDOW didn't fire
    def _cleanup_session():
        try:
            if app.key_manager:
                app.key_manager.end_session()
        except Exception:
            pass
    atexit.register(_cleanup_session)

    app.run()


if __name__ == "__main__":
    main()