#!/usr/bin/env python3
"""
Test script for Key Manager GUI.
This script tests the basic functionality of the GUI components.
"""
import sys
import os
from pathlib import Path

# Add src directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

def test_backup_engine():
    """Test the backup engine functionality."""
    print("Testing Backup Engine...")
    
    from backup_engine import BackupEngine
    
    # Create a test directory
    test_dir = Path("test_backup")
    test_dir.mkdir(exist_ok=True)
    
    # Create a test vault file
    vault_file = test_dir / "test_vault.encrypted"
    vault_file.write_text("test encrypted data")
    
    # Initialize backup engine
    backup_engine = BackupEngine(test_dir)
    
    # Test backup
    backup_path = backup_engine.backup_vault(vault_file, reason="test")
    if backup_path and backup_path.exists():
        print(f"✓ Backup created: {backup_path}")
    else:
        print("✗ Backup failed")
        return False
    
    # Test listing backups
    backups = backup_engine.list_backups()
    print(f"✓ Found {len(backups)} backups")
    
    # Cleanup
    import shutil
    shutil.rmtree(test_dir)
    
    print("✓ Backup engine tests passed")
    return True

def test_crypto_engine():
    """Test the crypto engine functionality."""
    print("\nTesting Crypto Engine...")
    
    from crypto_engine import CryptoEngine
    
    crypto = CryptoEngine()
    
    # Test key derivation
    password = "test_password"
    salt = b"test_salt_123456"
    key = crypto.derive_key(password, salt)
    
    if len(key) == 32:  # 32 bytes for AES-256
        print("✓ Key derivation works")
    else:
        print("✗ Key derivation failed")
        return False
    
    # Test encryption/decryption
    plaintext = b"test secret data"
    ciphertext, nonce, tag = crypto.encrypt_data(plaintext, key)
    decrypted = crypto.decrypt_data(ciphertext, nonce, tag, key)
    
    if decrypted == plaintext:
        print("✓ Encryption/decryption works")
    else:
        print("✗ Encryption/decryption failed")
        return False
    
    print("✓ Crypto engine tests passed")
    return True

def test_gui_imports():
    """Test that GUI imports work correctly."""
    print("\nTesting GUI Imports...")
    
    try:
        import customtkinter as ctk
        import pyperclip
        from gui_main import KeyManagerGUI
        
        print("✓ All GUI imports successful")
        print(f"✓ CustomTkinter version: {ctk.__version__ if hasattr(ctk, '__version__') else 'unknown'}")
        return True
    except ImportError as e:
        print(f"✗ Import error: {e}")
        return False

def main():
    """Run all tests."""
    print("="*60)
    print("Key Manager GUI - Test Suite")
    print("="*60)
    
    all_passed = True
    
    # Run tests
    if not test_backup_engine():
        all_passed = False
    
    if not test_crypto_engine():
        all_passed = False
    
    if not test_gui_imports():
        all_passed = False
    
    print("\n" + "="*60)
    if all_passed:
        print("✓ ALL TESTS PASSED")
        print("\nThe GUI is ready to run. Use:")
        print("  python src/gui_main.py")
        print("\nTo build the portable EXE:")
        print("  python build_gui.py")
    else:
        print("✗ SOME TESTS FAILED")
        print("\nPlease check the error messages above.")
    
    print("="*60)
    
    return 0 if all_passed else 1

if __name__ == "__main__":
    sys.exit(main())