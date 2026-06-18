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
from backup_engine import BackupEngine

# Determine if we're running from EXE or script
if getattr(sys, 'frozen', False):
    # Running from EXE - use EXE directory
    base_dir = os.path.dirname(sys.executable)
else:
    # Running from script - use script directory
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


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
    
    def add_address(self, account: str, coin: str, chain: str, address: str, password: str) -> bool:
        """Delegate to underlying KeyManager."""
        self._manager.address_db = self.address_db
        result = self._manager.add_address(account, coin, chain, address, password)
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
    
    def add_private_key(self, account_name: str, private_key: str, password: str) -> bool:
        """Delegate to underlying KeyManager."""
        self._manager.address_db = self.address_db
        result = self._manager.add_private_key(account_name, private_key, password)
        self.address_db = self._manager.address_db
        return result
    
    def show_private_key(self, account_name: str) -> Optional[str]:
        """Get private key for an account."""
        return self.address_db.get("private_keys", {}).get(account_name)
    
    def show_mnemonic(self, account_name: str) -> Optional[str]:
        """Get mnemonic for an account."""
        return self.address_db.get("mnemonics", {}).get(account_name)
    
    def change_password(self, old_password: str, new_password: str) -> bool:
        """Change the vault password."""
        result = self._manager.change_password(old_password, new_password)
        if result:
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
        self.backup_engine = None
        self.key_manager = None
        
        # Session management
        self.session_start_time = None
        self.session_timeout = 300  # 5 minutes in seconds
        self.session_timer = None
        self.revealed_mnemonics = {}
        
        # Clipboard queue for multi-copy
        self.clipboard_queue = queue.Queue()
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
            text="🔐 Key Manager",
            font=ctk.CTkFont(size=32, weight="bold")
        )
        title_label.pack(pady=(40, 20))
        
        # Subtitle
        subtitle_label = ctk.CTkLabel(
            main_frame,
            text="Secure Crypto Key Storage",
            font=ctk.CTkFont(size=16)
        )
        subtitle_label.pack(pady=(0, 40))
        
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
            show="•",
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
                text="No vault found. Please initialize with CLI first (key_manager.exe init)",
                text_color="orange"
            )
        
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
                
                # Initialize backup engine
                self.backup_engine = BackupEngine(self.key_manager.data_dir)
                
                # Update UI
                self.login_status_label.configure(
                    text="✓ Vault unlocked successfully",
                    text_color="green"
                )
                
                # Force UI update
                self.root.update()
                
                # Create main dashboard after short delay
                self.root.after(500, self.create_main_dashboard)
            else:
                self.login_status_label.configure(
                    text="✗ Invalid password or vault doesn't exist",
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
            # Clear login screen
            for widget in self.root.winfo_children():
                widget.destroy()
                
            # Create main layout first (creates status bar with session_timer_label)
            self.create_main_layout()
            
            # Start session timer only after status bar exists
            self.start_session_timer()
            
            # Now that status bar exists, update backup count
            if self.backup_engine and hasattr(self, 'backup_status_label'):
                backup_count = self.backup_engine.get_backup_count()
                self.backup_status_label.configure(
                    text=f"Backups: {backup_count}"
                )
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
        
        # Scrollable frame for accounts
        scrollable_frame = ctk.CTkScrollableFrame(left_panel)
        scrollable_frame.pack(fill="both", expand=True, padx=20, pady=(0, 20))
        
        # Load pools from vault data
        pools = self.key_manager.address_db.get("pools", {})
        accounts_data = self.key_manager.address_db.get("accounts", {})
        
        for pool_name, pool_data in pools.items():
            # Pool header
            pool_header = ctk.CTkLabel(
                scrollable_frame,
                text=f"── {pool_name} ──",
                font=ctk.CTkFont(size=14, weight="bold")
            )
            pool_header.pack(pady=(10, 2), anchor="w")
            
            # Account buttons from pool data
            pool_accounts = pool_data.get("accounts", [])
            for account in pool_accounts:
                # Show count of addresses for this account
                addr_count = 0
                if account in accounts_data:
                    addr_count = len(accounts_data[account].get("addresses", []))
                
                label = f"{account} ({addr_count})" if addr_count > 0 else account
                account_btn = ctk.CTkButton(
                    scrollable_frame,
                    text=label,
                    command=lambda p=pool_name, a=account: self.select_account(p, a),
                    width=220,
                    height=35,
                    corner_radius=10,
                    fg_color="transparent",
                    hover_color=("gray70", "gray30"),
                    border_width=1,
                    border_color=("gray60", "gray40")
                )
                account_btn.pack(pady=2)
        
        # Show unassigned accounts (accounts not in any pool but have addresses)
        all_pool_accounts = set()
        for pool_data in pools.values():
            all_pool_accounts.update(pool_data.get("accounts", []))
        
        unassigned = set(accounts_data.keys()) - all_pool_accounts
        if unassigned:
            unassigned_header = ctk.CTkLabel(
                scrollable_frame,
                text="── Unassigned ──",
                font=ctk.CTkFont(size=14, weight="bold")
            )
            unassigned_header.pack(pady=(10, 2), anchor="w")
            
            for account in sorted(unassigned):
                addr_count = len(accounts_data[account].get("addresses", []))
                label = f"{account} ({addr_count})" if addr_count > 0 else account
                account_btn = ctk.CTkButton(
                    scrollable_frame,
                    text=label,
                    command=lambda a=account: self.select_account("Unassigned", a),
                    width=220,
                    height=35,
                    corner_radius=10,
                    fg_color="transparent",
                    hover_color=("gray70", "gray30"),
                    border_width=1,
                    border_color=("gray60", "gray40")
                )
                account_btn.pack(pady=2)
        
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
        
        # Backup status
        self.backup_status_label = ctk.CTkLabel(
            status_bar,
            text="Backups: 0",
            font=ctk.CTkFont(size=12)
        )
        self.backup_status_label.pack(side="left", padx=20)
        
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
        """Display addresses for the selected account."""
        # Check if account exists in vault
        accounts_data = self.key_manager.address_db.get("accounts", {})
        
        if account_name not in accounts_data:
            no_data_label = ctk.CTkLabel(
                self.chain_view_container,
                text=f"No addresses found for account '{account_name}'",
                font=ctk.CTkFont(size=14)
            )
            no_data_label.pack(pady=50)
            return
        
        # Get addresses from vault
        account_data = accounts_data[account_name]
        addresses = account_data.get("addresses", [])
        
        if not addresses:
            no_data_label = ctk.CTkLabel(
                self.chain_view_container,
                text=f"No addresses stored for account '{account_name}'",
                font=ctk.CTkFont(size=14)
            )
            no_data_label.pack(pady=50)
        else:
            # Create address cards
            for addr in addresses:
                self.create_address_card(addr)
        
        # Add private key section if account has a private key
        if account_name in self.key_manager.address_db.get("private_keys", {}):
            self.create_private_key_section(account_name)
        
        # Add mnemonic section if account has a mnemonic
        if account_name in self.key_manager.address_db.get("mnemonics", {}):
            self.create_mnemonic_section(account_name)
            
    def create_address_card(self, address_data):
        """Create a card for displaying an address with copy functionality."""
        card = ctk.CTkFrame(self.chain_view_container, corner_radius=10)
        card.pack(fill="x", pady=5, padx=10)
        
        # Address info
        info_frame = ctk.CTkFrame(card, fg_color="transparent")
        info_frame.pack(side="left", fill="both", expand=True, padx=10, pady=10)
        
        coin_label = ctk.CTkLabel(
            info_frame,
            text=address_data.get("coin", "Unknown"),
            font=ctk.CTkFont(size=14, weight="bold")
        )
        coin_label.pack(anchor="w")
        
        chain_label = ctk.CTkLabel(
            info_frame,
            text=address_data.get("chain", "Unknown"),
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
                text=f"📝 {notes}",
                font=ctk.CTkFont(size=10),
                text_color="yellow"
            )
            notes_label.pack(anchor="w", pady=(2, 0))
        
        # Copy button
        button_frame = ctk.CTkFrame(card, fg_color="transparent")
        button_frame.pack(side="right", padx=10, pady=10)
        
        copy_btn = ctk.CTkButton(
            button_frame,
            text="Copy",
            command=lambda a=address_data["address"]: self.copy_to_clipboard(a),
            width=80,
            height=30
        )
        copy_btn.pack(pady=2)
        
        # Add to multi-copy queue button
        queue_btn = ctk.CTkButton(
            button_frame,
            text="Add to Queue",
            command=lambda a=address_data["address"]: self.add_to_clipboard_queue(a),
            width=80,
            height=30,
            fg_color="transparent",
            border_width=1
        )
        queue_btn.pack(pady=2)
        
    def copy_to_clipboard(self, address):
        """Copy address to clipboard."""
        try:
            pyperclip.copy(address)
            self.show_notification(f"Copied: {address[:20]}...")
        except Exception as e:
            self.show_notification(f"Failed to copy: {str(e)}", error=True)
            
    def add_to_clipboard_queue(self, address):
        """Add address to multi-copy queue."""
        self.clipboard_queue.put(address)
        queue_size = self.clipboard_queue.qsize()
        self.show_notification(f"Added to queue ({queue_size}): {address[:20]}...")
        
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
                text=f"✗ {message}",
                text_color="#ff6b6b",
                fg_color="#3a1a1a"
            )
        else:
            self._notification_label.configure(
                text=f"✓ {message}",
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
        """Create a section for private key display and reveal."""
        # Separator
        separator = ctk.CTkFrame(self.chain_view_container, height=2, fg_color="gray30")
        separator.pack(fill="x", pady=20, padx=10)
        
        # Private key section
        pk_frame = ctk.CTkFrame(self.chain_view_container, corner_radius=10)
        pk_frame.pack(fill="x", pady=10, padx=10)
        
        # Title
        pk_title = ctk.CTkLabel(
            pk_frame,
            text="🔑 Private Key",
            font=ctk.CTkFont(size=16, weight="bold")
        )
        pk_title.pack(pady=(15, 10))
        
        # Status label
        self.pk_status_label = ctk.CTkLabel(
            pk_frame,
            text="Private key is hidden for security",
            font=ctk.CTkFont(size=12),
            text_color="orange"
        )
        self.pk_status_label.pack(pady=5)
        
        # Private key display (initially hidden)
        self.pk_display = ctk.CTkTextbox(
            pk_frame,
            height=60,
            font=ctk.CTkFont(size=12, family="monospace"),
            state="disabled"
        )
        self.pk_display.pack(fill="x", padx=20, pady=10)
        
        # Button frame
        button_frame = ctk.CTkFrame(pk_frame, fg_color="transparent")
        button_frame.pack(pady=(0, 15))
        
        # Reveal button
        self.pk_reveal_button = ctk.CTkButton(
            button_frame,
            text="Reveal Key",
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
        """Reveal private key with password re-entry."""
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
        
        password_entry = ctk.CTkEntry(dialog, placeholder_text="Enter master password", show="•", width=250, font=ctk.CTkFont(size=12))
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
                pk = self.key_manager.address_db["private_keys"][account_name]
                self.pk_display.configure(state="normal")
                self.pk_display.delete("1.0", "end")
                self.pk_display.insert("1.0", pk)
                self.pk_display.configure(state="disabled")
                self.pk_reveal_button.configure(state="disabled")
                self.pk_copy_button.configure(state="normal")
                self.pk_hide_button.configure(state="normal")
                self.pk_status_label.configure(text="Private key revealed - will auto-hide in 5 minutes", text_color="green")
                dialog.destroy()
                self.show_notification("Private key revealed")
                self.root.after(300000, lambda: self.auto_hide_private_key(account_name))
            else:
                status_label.configure(text="Invalid password", text_color="red")
        
        password_entry.bind("<Return>", lambda e: verify_password())
        
        btn_frame = ctk.CTkFrame(dialog, fg_color="transparent")
        btn_frame.pack(pady=20)
        ctk.CTkButton(btn_frame, text="Verify", command=verify_password, width=100).pack(side="left", padx=10)
        ctk.CTkButton(btn_frame, text="Cancel", command=dialog.destroy, width=100, fg_color="gray30").pack(side="left", padx=10)
    
    def hide_private_key(self, account_name):
        """Hide the revealed private key."""
        self.pk_display.configure(state="normal")
        self.pk_display.delete("1.0", "end")
        self.pk_display.insert("1.0", "••••••••••••••••••••••••••••••")
        self.pk_display.configure(state="disabled")
        self.pk_reveal_button.configure(state="normal")
        self.pk_copy_button.configure(state="disabled")
        self.pk_hide_button.configure(state="disabled")
        self.pk_status_label.configure(text="Private key is hidden for security", text_color="orange")
        self.show_notification("Private key hidden")
    
    def auto_hide_private_key(self, account_name):
        """Auto-hide private key after 5 minutes."""
        self.hide_private_key(account_name)
        self.show_notification("Private key auto-hidden after 5 minutes")
    
    def copy_private_key_to_clipboard(self, account_name):
        """Copy private key to clipboard."""
        if account_name in self.key_manager.address_db.get("private_keys", {}):
            pk = self.key_manager.address_db["private_keys"][account_name]
            try:
                pyperclip.copy(pk)
                self.show_notification("Private key copied to clipboard")
            except Exception as e:
                self.show_notification(f"Failed to copy: {str(e)}", error=True)
    
    def create_mnemonic_section(self, account_name):
        """Create a section for mnemonic display and reveal."""
        # Separator
        separator = ctk.CTkFrame(self.chain_view_container, height=2, fg_color="gray30")
        separator.pack(fill="x", pady=20, padx=10)
        
        # Mnemonic section
        mnemonic_frame = ctk.CTkFrame(self.chain_view_container, corner_radius=10)
        mnemonic_frame.pack(fill="x", pady=10, padx=10)
        
        # Title
        mnemonic_title = ctk.CTkLabel(
            mnemonic_frame,
            text="🔒 Recovery Phrase (24 words)",
            font=ctk.CTkFont(size=16, weight="bold")
        )
        mnemonic_title.pack(pady=(15, 10))
        
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
            show="•",
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
        self.mnemonic_display.insert("1.0", "••••••••••••••••••••••••")
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