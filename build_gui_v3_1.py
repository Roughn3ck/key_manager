#!/usr/bin/env python3
"""
PyInstaller build script for ColdStack GUI v3.1.
Creates a portable onefile EXE for use on encrypted USB drive.

Version: v3.1 (June 2026) - ColdStack rebrand + Check for Updates
Builds: src/gui_main_v3_1.py
Output: USB_DEPLOYMENT/coldstack.exe (overwrites previous, with backup)
"""
import os
import sys
import PyInstaller.__main__
import shutil
from pathlib import Path


def clean_build_dirs():
    """Clean up previous build directories."""
    dirs_to_clean = ['build', 'dist']
    for dir_name in dirs_to_clean:
        if os.path.exists(dir_name):
            print(f"Cleaning {dir_name} directory...")
            shutil.rmtree(dir_name)


def build_gui_exe():
    """Build the GUI executable using PyInstaller."""
    print("Building ColdStack v3.1 executable...")

    project_dir = Path(__file__).parent.resolve()

    # Check icon exists, use absolute path
    icon_path = project_dir / 'assets' / 'icon.ico'
    icon_args = [f'--icon={icon_path}'] if icon_path.exists() else []

    # PyInstaller arguments for onefile portable EXE
    args = [
        str(project_dir / 'src' / 'gui_main_v3_1.py'),
        '--name=coldstack',
        '--onefile',
        '--console',  # Use console mode to avoid DLL ordinal errors; GUI hides console via FreeConsole()
        *icon_args,
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
        '--collect-all=customtkinter',
        '--collect-all=cryptography',
        '--collect-all=argon2',
        '--collect-all=pyperclip',
        '--collect-all=PIL',
        '--collect-all=hdwallet',
        '--collect-all=mnemonic',
        '--clean',
        '--noconfirm',
        f'--paths={project_dir / "src"}',
        f'--distpath={project_dir / "dist"}',
        f'--workpath={project_dir / "build"}',
        f'--specpath={project_dir}',
    ]

    # Add Windows-specific options to avoid DLL issues
    if sys.platform == 'win32':
        args.extend([
            '--disable-windowed-traceback',
        ])

    print(f"Running PyInstaller with args: {' '.join(args)}")

    try:
        PyInstaller.__main__.run(args)
        print("Build completed successfully!")
        return True
    except Exception as e:
        print(f"Build failed: {e}")
        return False


def copy_to_usb_deployment():
    """Copy the built EXE to USB_DEPLOYMENT folder, backing up the previous EXE."""
    exe_path = Path('dist') / 'coldstack.exe'
    usb_dir = Path('USB_DEPLOYMENT')

    if not exe_path.exists():
        print(f"Executable not found at {exe_path}")
        return False

    # Create USB_DEPLOYMENT directory if it doesn't exist
    usb_dir.mkdir(exist_ok=True)

    # Back up the previous EXE before overwriting (versioning rule 5)
    target_path = usb_dir / 'coldstack.exe'
    backups_dir = Path('backups')
    backups_dir.mkdir(exist_ok=True)
    if target_path.exists():
        backup_path = backups_dir / 'key_manager_gui_v3.exe'
        print(f"Backing up previous EXE to {backup_path}...")
        shutil.copy2(target_path, backup_path)

    # Copy the new EXE (overwrite the canonical name)
    shutil.copy2(exe_path, target_path)

    # Copy any additional files needed for USB deployment
    files_to_copy = [
        'README.md',
        'LICENSE',
    ]

    for file_name in files_to_copy:
        if os.path.exists(file_name):
            shutil.copy2(file_name, usb_dir / file_name)

    print(f"Copied {exe_path} to {target_path}")
    print(f"Total size: {target_path.stat().st_size / (1024 * 1024):.2f} MB")
    return True


def create_portable_package():
    """Create a complete portable package with all dependencies."""
    print("Creating portable package...")

    # Clean previous builds
    clean_build_dirs()

    # Build the EXE
    if not build_gui_exe():
        return False

    # Copy to USB deployment
    if not copy_to_usb_deployment():
        return False

    # Create a simple launcher script for the USB
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


def create_launcher_script():
    """Create a simple launcher script for the USB drive."""
    launcher_content = """@echo off
echo ========================================
echo   ColdStack - Secure Crypto Key Vault
echo ========================================
echo.
echo Starting ColdStack v3.1...
echo.
coldstack.exe
pause
"""

    launcher_path = Path('USB_DEPLOYMENT') / 'launch.bat'
    with open(launcher_path, 'w') as f:
        f.write(launcher_content)

    print(f"Created launcher script: {launcher_path}")


def check_dependencies():
    """Check if all required dependencies are installed."""
    required_packages = [
        'PyInstaller',
        'customtkinter',
        'cryptography',
        'argon2',
        'pyperclip',
        'hdwallet',
        'mnemonic',
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
        print("\nInstall missing packages with:")
        print(f"pip install {' '.join(missing)}")
        return False

    return True


def main():
    """Main build function."""
    # Change to project directory
    project_dir = Path(__file__).parent.resolve()
    os.chdir(project_dir)
    print(f"Working directory: {os.getcwd()}")

    print("=" * 60)
    print("ColdStack v3.1 - Portable EXE Builder")
    print("=" * 60)

    # Check dependencies
    if not check_dependencies():
        print("\nPlease install missing dependencies and try again.")
        return 1

    # Create portable package
    if create_portable_package():
        print("\nBuild process completed successfully!")
        return 0
    else:
        print("\nBuild process failed!")
        return 1


if __name__ == '__main__':
    sys.exit(main())