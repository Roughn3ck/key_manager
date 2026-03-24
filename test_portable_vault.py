#!/usr/bin/env python3
"""
Test portable vault functionality.
"""
import sys
import os
from pathlib import Path

# Add src directory to path
sys.path.insert(0, 'src')

print("Testing Portable Vault Setup")
print("="*60)

# Test 1: Check if vault can be created in portable location
print("\n1. Testing portable vault location...")

# Simulate running from EXE directory
test_exe_dir = Path("USB_DEPLOYMENT")
test_exe_dir.mkdir(exist_ok=True)

# Create a test script that simulates EXE behavior
test_script = """
import sys
import os

# Simulate frozen EXE
sys.frozen = True
sys.executable = r'{}'

# Now import the portable KeyManager
import sys
sys.path.insert(0, 'src')

from gui_main import PortableKeyManager

# Create portable manager
manager = PortableKeyManager()
print(f"Data dir: {{manager.data_dir}}")
print(f"Vault file: {{manager.data_file}}")
print(f"Portable path: {{manager.data_file.relative_to(Path('.'))}}")
""".format(os.path.abspath(test_exe_dir / "test.exe"))

# Write and run test
test_file = test_exe_dir / "test_portable.py"
with open(test_file, 'w') as f:
    f.write(test_script)

# Run the test
import subprocess
result = subprocess.run([sys.executable, str(test_file)], 
                       capture_output=True, text=True, cwd=test_exe_dir)

if result.returncode == 0:
    print("✓ Portable vault test passed")
    print(result.stdout)
else:
    print("✗ Portable vault test failed")
    print(result.stderr)

# Test 2: Check actual vault migration
print("\n2. Testing vault migration...")

# Check if original vault exists
original_vault = Path.home() / '.key_manager' / 'key_vault.encrypted'
if original_vault.exists():
    print(f"Original vault found: {original_vault}")
    print(f"Size: {original_vault.stat().st_size} bytes")
    
    # Check if we should copy it to portable location
    portable_vault_dir = test_exe_dir / '.key_manager'
    portable_vault_dir.mkdir(exist_ok=True)
    portable_vault = portable_vault_dir / 'key_vault.encrypted'
    
    if not portable_vault.exists():
        print("Would copy vault to portable location for USB use")
        print(f"Portable location: {portable_vault}")
    else:
        print(f"Portable vault already exists: {portable_vault}")
else:
    print("No original vault found - need to initialize first")

# Test 3: Check backup location
print("\n3. Testing backup system...")

from backup_engine import BackupEngine

# Test with portable directory
backup_engine = BackupEngine(test_exe_dir / '.key_manager')
print(f"Backup directory: {backup_engine.backup_dir}")
print(f"Backup directory exists: {backup_engine.backup_dir.exists()}")

# Create a test vault file for backup test
test_vault = test_exe_dir / '.key_manager' / 'test_vault.encrypted'
test_vault.parent.mkdir(parents=True, exist_ok=True)
test_vault.write_text("test data")

backup_path = backup_engine.backup_vault(test_vault, reason="test")
if backup_path:
    print(f"✓ Backup created: {backup_path.name}")
    print(f"  Size: {backup_path.stat().st_size} bytes")
else:
    print("✗ Backup failed")

# Cleanup
test_vault.unlink()
if backup_path and backup_path.exists():
    backup_path.unlink()
    meta_file = backup_path.with_suffix('.meta.json')
    if meta_file.exists():
        meta_file.unlink()

print("\n" + "="*60)
print("PORTABLE VAULT SETUP INSTRUCTIONS:")
print("="*60)
print("\nFor true portability on USB drive:")
print("1. Copy entire USB_DEPLOYMENT folder to USB drive")
print("2. Copy your vault file to USB_DEPLOYMENT\\.key_manager\\")
print("   OR let the GUI create a new vault there")
print("3. Run key_manager_gui.exe from the USB drive")
print("\nVault will be stored at:")
print("  USB_DEPLOYMENT\\.key_manager\\key_vault.encrypted")
print("\nBackups will be stored at:")
print("  USB_DEPLOYMENT\\.key_manager\\backups\\")
print("\nThis makes everything self-contained on the USB drive!")