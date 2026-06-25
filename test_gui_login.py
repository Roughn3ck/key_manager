#!/usr/bin/env python3
"""
Test GUI login functionality.
"""
import sys
import os
sys.path.insert(0, 'src')

print("Testing GUI login...")
print("="*60)

# Import with error handling
try:
    from gui_main import KeyManagerGUI
    print("✓ KeyManagerGUI imported successfully")
    
    # Create instance but don't run mainloop
    print("Creating GUI instance...")
    app = KeyManagerGUI()
    print("✓ GUI instance created")
    
    # Test password verification
    print("\nTesting password verification...")
    from crypto_engine import CryptoEngine
    crypto = CryptoEngine()
    
    # Create test data
    test_data = {"test": "data"}
    password = "TestPassword123!"
    
    # Encrypt test data
    encrypted = crypto.encrypt_json(test_data, password)
    print(f"✓ Encryption test passed")
    
    # Verify password
    verified = crypto.verify_password(encrypted, password)
    print(f"✓ Password verification: {verified}")
    
    # Test wrong password
    wrong_verified = crypto.verify_password(encrypted, "WrongPassword!")
    print(f"✓ Wrong password rejection: {not wrong_verified}")
    
    print("\n" + "="*60)
    print("GUI LOGIN TEST COMPLETE")
    print("="*60)
    print("\nIf the GUI shows a blank screen after login, it might be:")
    print("1. Password verification issue")
    print("2. Threading issue with the dashboard creation")
    print("3. Missing account data in the vault")
    
    print("\nTo debug further, try:")
    print("1. Check if vault exists and has data")
    print("2. Try the CLI first: python src/main.py unlock")
    print("3. Check console for error messages")
    
except Exception as e:
    print(f"✗ Error: {e}")
    import traceback
    traceback.print_exc()

print("\n" + "="*60)
print("PATHS SUMMARY:")
print("="*60)
from pathlib import Path
vault_path = Path.home() / '.key_manager' / 'key_vault.encrypted'
backup_path = Path.home() / '.key_manager' / 'backups'
print(f"Vault: {vault_path}")
print(f"Backups: {backup_path}")
print(f"EXE will be at: {os.path.abspath('USB_DEPLOYMENT/key_manager_gui.exe')}")