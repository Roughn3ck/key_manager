#!/usr/bin/env python3
"""
PyInstaller build script for ColdStack GUI v5.2.2.
Creates a portable onefile EXE for use on encrypted USB drive.

Version: v5.2.2 (August 2026) - Aerodrome SlipStream + BSC V3
Builds: src/gui_main_v5.py
Output: USB_DEPLOYMENT/coldstack.exe (overwrites previous, with backup)

Changes from build_gui_v5.py:
  1. Entry point: src/gui_main_v5.py
  2. New hidden imports: settings_dialog, account_dialogs, lp_tab, vault_tab, chain_options
  3. Version strings updated to v5.2.2
  4. Krystal adapter skeleton + BSC RPC configuration

WARNING: After ANY source change, rebuild: python build_gui_v5.py
"""
import os
import sys
import PyInstaller.__main__
import shutil
from pathlib import Path


def clean_build_dirs():
    """Clean build/ and dist/ directories before a fresh build."""
    dirs_to_clean = ['build', 'dist']
    for dir_name in dirs_to_clean:
        if os.path.exists(dir_name):
            print(f"Cleaning {dir_name} directory...")
            shutil.rmtree(dir_name)


def build_gui_exe():
    """Run PyInstaller to build the ColdStack v5.2.2 EXE."""
    print("Building ColdStack v5.2.2 executable...")

    project_dir = Path(__file__).parent.resolve()

    icon_path = project_dir / 'assets' / 'icon.ico'
    icon_args = [f'--icon={icon_path}'] if icon_path.exists() else []

    args = [
        str(project_dir / 'src' / 'gui_main_v5.py'),
        '--name=coldstack',
        '--onefile',
        '--console',
        *icon_args,
        # --- Core hidden imports (same as v4) ---
        '--hidden-import=customtkinter',
        '--hidden-import=cryptography',
        '--hidden-import=argon2',
        '--hidden-import=pyperclip',
        '--hidden-import=json',
        '--hidden-import=queue',
        '--hidden-import=pathlib',
        '--hidden-import=datetime',
        '--hidden-import=openpyxl',
        '--hidden-import=hdwallet',
        '--hidden-import=mnemonic',
        '--hidden-import=coincurve',
        '--hidden-import=base58',
        '--hidden-import=cbor2',
        '--hidden-import=pynacl',
        '--hidden-import=ed25519_blake2b',
        '--hidden-import=crcmod',
        '--hidden-import=Crypto',
        '--hidden-import=Crypto.Hash',
        '--hidden-import=Crypto.Hash.keccak',
        # --- v5.1 CLI deps pulled in by main.py (via gui_main_v5 import) ---
        '--hidden-import=click',
        '--hidden-import=rich',
        '--hidden-import=rich.console',
        '--hidden-import=rich.table',
        '--hidden-import=rich.panel',
        '--hidden-import=rich.progress',
        # --- v5.1 stdlib urllib used by network engines ---
        '--hidden-import=urllib',
        '--hidden-import=urllib.request',
        '--hidden-import=urllib.error',
        '--collect-submodules=urllib',
        # --- v5.1 stdlib HTTPS/SSL stack required by urllib.request at runtime ---
        '--hidden-import=ssl',
        '--hidden-import=_ssl',
        '--hidden-import=http.client',
        '--hidden-import=socket',
        '--hidden-import=_socket',
        # --- v5.1.1 LP Liquidity Manager hidden import ---
        '--hidden-import=lp_liquidity_manager',
        # --- v5.1.2 EVM Transfer + Vault Deposit dialogs ---
        '--hidden-import=evm_transfer_dialog',
        '--hidden-import=vault_deposit_dialog',
        # --- v5.1.3 Swap dialog ---
        '--hidden-import=swap_dialog',
        # --- v5.1.4 Carve-off modules ---
        '--hidden-import=settings_dialog',
        '--hidden-import=account_dialogs',
        '--hidden-import=vault_tab',
        '--hidden-import=lp_tab',
        '--hidden-import=chain_options',
        # --- v5.2.2 Appearance module ---
        '--hidden-import=appearance',
        # --- v5.1 LP Engine hidden imports ---
        '--hidden-import=lp_engine',
        '--hidden-import=price_engine',
        '--hidden-import=balance_engine',
        '--hidden-import=rpc_config',
        '--hidden-import=venue_adapters',
        '--hidden-import=venue_adapters.hyperliquid_adapter',
        '--hidden-import=venue_adapters.venue_writer',
        '--hidden-import=venue_adapters.hyperliquid_writer',
        '--hidden-import=venue_adapters.bsc_writer',
        '--hidden-import=venue_adapters.aerodrome_adapter',
        '--hidden-import=venue_adapters.aerodrome_writer',
        '--collect-submodules=venue_adapters',
        # --- v5.1: Saved pools ---
        '--hidden-import=saved_pools',
        # --- v5.1: Vault tracker ---
        '--hidden-import=vault_tracker',
        # --- v5.1: certifi CA bundle for HTTPS TLS verification in frozen EXE ---
        '--hidden-import=certifi',
        '--collect-data=certifi',
        # --- v5.1: Embedded key_manager_agent HTTP server ---
        '--hidden-import=key_manager_agent',
        '--hidden-import=http.server',
        '--hidden-import=socketserver',
        # --- Collect-all for packages with data files ---
        '--collect-all=customtkinter',
        '--collect-all=cryptography',
        '--collect-all=argon2',
        '--collect-all=pyperclip',
        '--collect-all=PIL',
        '--collect-all=hdwallet',
        '--collect-all=mnemonic',
        '--collect-all=rich',
        # --- Data files ---
        '--add-data=rpc_endpoints.json;.',
        # --- Build options ---
        '--clean',
        '--noconfirm',
        f'--paths={project_dir / "src"}',
        f'--distpath={project_dir / "dist"}',
        f'--workpath={project_dir / "build"}',
        f'--specpath={project_dir}',
    ]

    if sys.platform == 'win32':
        args.extend(['--disable-windowed-traceback'])

    print(f"Running PyInstaller with args: {' '.join(args)}")

    try:
        PyInstaller.__main__.run(args)
        print("Build completed successfully!")
        return True
    except Exception as e:
        print(f"Build failed: {e}")
        return False


def copy_to_usb_deployment():
    """Copy the built EXE to USB_DEPLOYMENT/, backing up the previous version."""
    exe_path = Path('dist') / 'coldstack.exe'
    usb_dir = Path('USB_DEPLOYMENT')

    if not exe_path.exists():
        print(f"Executable not found at {exe_path}")
        return False

    usb_dir.mkdir(exist_ok=True)

    target_path = usb_dir / 'coldstack.exe'
    backups_dir = Path('backups')
    backups_dir.mkdir(exist_ok=True)
    if target_path.exists():
        # Backup the previous production EXE using the version it was built from.
        # Bump this string whenever the shipped version changes so the backup name
        # matches the last stable build.
        PREVIOUS_VERSION_TAG = "v5_1_4"  # v5.1.4 is the previous production build
        backup_path = backups_dir / f'coldstack_{PREVIOUS_VERSION_TAG}.exe'
        print(f"Backing up previous EXE to {backup_path}...")
        shutil.copy2(target_path, backup_path)

    shutil.copy2(exe_path, target_path)

    for file_name in ['README.md', 'LICENSE']:
        if os.path.exists(file_name):
            shutil.copy2(file_name, usb_dir / file_name)

    print(f"Copied {exe_path} to {target_path}")
    print(f"Total size: {target_path.stat().st_size / (1024 * 1024):.2f} MB")
    return True


def create_launcher_script():
    """Create launch.bat in USB_DEPLOYMENT/."""
    launcher_content = """@echo off
echo ========================================
echo   ColdStack - Secure Crypto Key Vault
echo ========================================
echo.
echo Starting ColdStack v5.2.2...
echo.
coldstack.exe
pause
"""
    launcher_path = Path('USB_DEPLOYMENT') / 'launch.bat'
    with open(launcher_path, 'w') as f:
        f.write(launcher_content)
    print(f"Created launcher script: {launcher_path}")


def check_dependencies():
    """Check that required Python packages are installed."""
    required_packages = [
        'PyInstaller', 'customtkinter', 'cryptography',
        'argon2', 'pyperclip', 'hdwallet', 'mnemonic',
    ]
    missing = []
    for package in required_packages:
        try:
            __import__(package.replace('-', '_'))
        except ImportError:
            missing.append(package)
    if missing:
        print("Missing dependencies:")
        for package in missing:
            print(f"  - {package}")
        print(f"\nInstall missing packages with:\npip install {' '.join(missing)}")
        return False
    return True


def create_portable_package():
    """Full build pipeline: clean, build, copy, create launcher."""
    print("Creating portable package...")
    clean_build_dirs()
    if not build_gui_exe():
        return False
    if not copy_to_usb_deployment():
        return False
    create_launcher_script()

    print("\n" + "=" * 60)
    print("PORTABLE BUILD COMPLETE!")
    print("=" * 60)
    print("Executable: USB_DEPLOYMENT/coldstack.exe")
    print(f"Size: {(Path('USB_DEPLOYMENT/coldstack.exe').stat().st_size / (1024 * 1024)):.2f} MB")
    print("\nThe application is ready to run from an encrypted USB drive.")
    print("No installation required - just copy the USB_DEPLOYMENT folder.")
    print("=" * 60)
    return True


def main():
    project_dir = Path(__file__).parent.resolve()
    os.chdir(project_dir)
    print(f"Working directory: {os.getcwd()}")

    print("=" * 60)
    print("ColdStack v5.2.2 - Portable EXE Builder")
    print("Aerodrome SlipStream + BSC V3")
    print("=" * 60)

    if not check_dependencies():
        print("\nPlease install missing dependencies and try again.")
        return 1

    if create_portable_package():
        print("\nBuild process completed successfully!")
        return 0
    else:
        print("\nBuild process failed!")
        return 1


if __name__ == '__main__':
    sys.exit(main())


# Build timestamp (auto-updated on each run — compare this to the EXE's
# file modification time to verify the EXE matches the current source code.
# If the EXE is older than this timestamp, rebuild: python build_gui_v5.py)
# BUILD: 2026-08-06 09:10 AEST

