#!/usr/bin/env python
"""Test dashboard creation flow to capture post-login crash."""
import sys
import os
import traceback

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

# Redirect stdout/stderr to files so we can capture even if windowed
log_path = os.path.join(os.path.dirname(__file__), 'dashboard_test.log')
with open(log_path, 'w', encoding='utf-8') as log:
    sys.stdout = log
    sys.stderr = log

    try:
        from pathlib import Path
        from gui_main import KeyManager, KeyManagerGUI
        import customtkinter as ctk

        print("Creating GUI instance...")
        app = KeyManagerGUI()
        print("GUI created successfully")

        print(f"Vault path: {app.key_manager.data_file}")
        print(f"Vault exists: {app.key_manager.data_file.exists()}")

        # Simulate what happens after login
        print("Loading backup_engine import...")
        from backup_engine import BackupEngine

        # Manually test dashboard creation steps
        print("Testing create_main_layout components...")

        # Check address_db structure
        km = KeyManager()
        print(f"address_db keys: {list(km.address_db.keys()) if km.address_db else 'empty'}")
        print(f"pools: {km.address_db.get('pools', {})}")
        print(f"accounts: {km.address_db.get('accounts', {})}")

        # Try creating main layout in the existing root
        print("Attempting create_main_layout...")
        try:
            app.create_main_layout()
            print("create_main_layout succeeded")
        except Exception as e:
            print(f"create_main_layout FAILED: {e}")
            traceback.print_exc()

    except Exception as e:
        print(f"FATAL: {e}")
        traceback.print_exc()

print("Test complete. Check dashboard_test.log")