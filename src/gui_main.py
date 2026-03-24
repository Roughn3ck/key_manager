"""
Key Manager GUI - Modern dark-themed interface for secure key management.
Built with CustomTkinter.
"""
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
import sys
import os

# Add src directory to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from crypto_engine import CryptoEngine
from backup_engine import BackupEngine

# Import KeyManager with modified data directory for portability
import sys
import os

# Determine if we're running from EXE or script
if getattr(sys, 'frozen', False):
    # Running from EXE - use EXE directory
    base_dir = os.path.dirname(sys.executable)
else:
    # Running from script - use script directory
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Create a portable KeyManager class
class PortableKeyManager:
    """KeyManager that stores vault in the same directory as the executable."""
    
    def __init__(self):
        from main import KeyManager as OriginalKeyManager
        import os
        
        # Use portable data directory
        self.data_dir = os.path.join(base_dir, '.key_manager')
        os.makedirs(self.data_dir, exist_ok=True)
        
        # Create original KeyManager with custom data directory
        self.manager = OriginalKeyManager(data_dir=self.data_dir)
        
        # Proxy all attributes and methods
        self.__dict__.update(self.manager.__dict__)
        
    def __getattr__(self, name):
        # Forward any missing attributes to the original manager
        return getattr(self.manager, name)

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
        self.revealed_mnemonics = {}  # Track revealed mnemonics and their expiry
        
        # Clipboard queue for multi-copy
        self.clipboard_queue = queue.Queue()
        self.current_account = None
        self.current_pool = None
        
        # Password for current session
        self.current_password = None
        
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
                text="No vault found. Please initialize with CLI first.",
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
                
                # Initialize backup engine
                self.backup_engine = BackupEngine(self.key_manager.data_dir)
                
                # Update backup status
                backup_count = self.backup_engine.get_backup_count()
                self.backup_status_label.configure(
                    text=f"Backups: {backup_count}"
                )
                
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
            # Print error to console for debugging
            print(f"Login error: {e}")
            import traceback
            traceback.print_exc()
        
    def create_main_dashboard(self):
        """Create the main dashboard after successful login."""
        # Clear login screen
        for widget in self.root.winfo_children():
            widget.destroy()
            
        # Start session timer
        self.start_session_timer()
        
        # Create main layout
        self.create_main_layout()
        
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
        """Create left panel with scrollable account list."""
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
        
        # Sample accounts (will be loaded from vault)
        pools = {
            "Genesis": ["G SS", "Expenses", "G1", "G2", "G3", "G4", "G5", "G6"],
            "SafetyNet": ["N1", "N2", "N3", "N4", "N5"],
            "Foundation": ["F1", "F2", "F3", "F4", "F5"],
            "Seed": ["S1", "S2", "S3", "S4", "S5"]
        }
        
        for pool_name, accounts in pools.items():
            # Pool header
            pool_header = ctk.CTkLabel(
                scrollable_frame,
                text=pool_name,
                font=ctk.CTkFont(size=14, weight="bold")
            )
            pool_header.pack(pady=(10, 5), anchor="w")
            
            # Account buttons
            for account in accounts:
                account_btn = ctk.CTkButton(
                    scrollable_frame,
                    text=account,
                    command=lambda p=pool_name, a=account: self.select_account(p, a),
                    width=200,
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
        if account_name not in self.key_manager.address_db["accounts"]:
            no_data_label = ctk.CTkLabel(
                self.chain_view_container,
                text=f"No addresses found for account '{account_name}'",
                font=ctk.CTkFont(size=14)
            )
            no_data_label.pack(pady=50)
            return
        
        # Get addresses from vault
        account_data = self.key_manager.address_db["accounts"][account_name]
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
        
        # Add mnemonic section if account has a mnemonic
        if account_name in self.key_manager.address_db["mnemonics"]:
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
            text=address_data["coin"],
            font=ctk.CTkFont(size=14, weight="bold")
        )
        coin_label.pack(anchor="w")
        
        chain_label = ctk.CTkLabel(
            info_frame,
            text=address_data["chain"],
            font=ctk.CTkFont(size=12)
        )
        chain_label.pack(anchor="w")
        
        address_label = ctk.CTkLabel(
            info_frame,
            text=address_data["address"],
            font=ctk.CTkFont(size=11),
            wraplength=400
        )
        address_label.pack(anchor="w", pady=(5, 0))
        
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
            self.show_notification(f"Copied to clipboard: {address[:20]}...")
        except Exception as e:
            self.show_notification(f"Failed to copy: {str(e)}", error=True)
            
    def add_to_clipboard_queue(self, address):
        """Add address to multi-copy queue."""
        self.clipboard_queue.put(address)
        self.show_notification(f"Added to queue: {address[:20]}...")
        
    def show_notification(self, message, error=False):
        """Show a temporary notification."""
        # This would be implemented as a toast notification
        print(f"Notification: {message}")
        
    def start_session_timer(self):
        """Start the 5-minute session timer."""
        self.session_start_time = datetime.now()
        self.update_session_timer()
        
    def update_session_timer(self):
        """Update the session timer display."""
        if self.session_start_time:
            elapsed = (datetime.now() - self.session_start_time).seconds
            remaining = max(0, self.session_timeout - elapsed)
            
            minutes = remaining // 60
            seconds = remaining % 60
            
            self.session_timer_label.configure(
                text=f"Session: {minutes}:{seconds:02d}"
            )
            
            # Check if session expired
            if remaining <= 0:
                self.lock_session()
            else:
                # Schedule next update
                self.root.after(1000, self.update_session_timer)
                
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
            
            # Verify password
            if self.crypto.verify_password(
                json.dumps(self.key_manager.address_db),  # Simplified verification
                password
            ):
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
        if account_name in self.key_manager.address_db["mnemonics"]:
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