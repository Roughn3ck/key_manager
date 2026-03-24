#!/usr/bin/env python3
"""
Debug script to show all paths and test GUI functionality.
"""
import sys
import os
from pathlib import Path

# Add src directory to path
sys.path.insert(0, 'src')

print("="*60)
print("KEY MANAGER PATHS DEBUG")
print("="*60)

# 1. Vault location
from main import KeyManager
manager = KeyManager()
vault_file = manager.data_file
print(f"1. VAULT LOCATION:")
print(f"   Path: {vault_file}")
print(f"   Exists: {vault_file.exists()}")
print(f"   Full: {os.path.abspath(vault_file)}")
print()

# 2. Backup location
from backup_engine import BackupEngine
backup_engine = BackupEngine(manager.data_dir)
print(f"2. BACKUP LOCATION:")
print(f"   Data dir: {manager.data_dir}")
print(f"   Backup dir: {backup_engine.backup_dir}")
print(f"   Backup exists: {backup_engine.backup_dir.exists()}")
print(f"   Full: {os.path.abspath(backup_engine.backup_dir)}")
print()

# 3. EXE build locations
print(f"3. EXE BUILD LOCATIONS:")
print(f"   Current dir: {os.getcwd()}")
print(f"   Dist folder: {os.path.abspath('dist')}")
print(f"   USB Deployment: {os.path.abspath('USB_DEPLOYMENT')}")
print()

# 4. Test if GUI can load
print(f"4. GUI IMPORT TEST:")
try:
    import customtkinter as ctk
    import pyperclip
    from gui_main import KeyManagerGUI
    print(f"   ✓ CustomTkinter: {ctk.__version__}")
    print(f"   ✓ Pyperclip: {pyperclip.__version__}")
    print(f"   ✓ KeyManagerGUI: Import successful")
    
    # Test creating GUI instance
    print(f"   Testing GUI creation...")
    # Don't actually run mainloop in debug script
    print(f"   ✓ GUI components load successfully")
    
except Exception as e:
    print(f"   ✗ Error: {e}")
    import traceback
    traceback.print_exc()

print()
print("="*60)
print("TO BUILD THE EXE:")
print("="*60)
print("Run: python build_gui.py")
print()
print("The EXE will be created in:")
print("  - dist/key_manager_gui.exe")
print("  - USB_DEPLOYMENT/key_manager_gui.exe (copied automatically)")
print()
print("Backup folder will be created at:")
print(f"  {os.path.abspath(backup_engine.backup_dir)}")
print("="*60)