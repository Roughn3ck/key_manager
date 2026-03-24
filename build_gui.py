#!/usr/bin/env python3
"""
PyInstaller build script for Key Manager GUI.
Creates a portable onefile EXE for use on encrypted USB drive.
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
    print("Building Key Manager GUI executable...")
    
    # PyInstaller arguments for onefile portable EXE
    # Fixed to avoid DLL errors and ensure proper bundling
    args = [
        'src/gui_main.py',
        '--name=key_manager_gui',
        '--onefile',
        '--windowed',  # No console window for GUI app
        '--icon=assets/icon.ico',  # Optional: add icon if available
        '--add-data=src;src',  # Include source modules
        '--hidden-import=customtkinter',
        '--hidden-import=cryptography',
        '--hidden-import=argon2',
        '--hidden-import=pyperclip',
        '--hidden-import=json',
        '--hidden-import=queue',
        '--hidden-import=pathlib',
        '--hidden-import=datetime',
        '--collect-all=customtkinter',
        '--collect-all=cryptography',
        '--collect-all=argon2',
        '--collect-all=pyperclip',
        '--collect-all=PIL',  # For image support if needed
        '--clean',
        '--noconfirm',
        '--paths=src',  # Explicitly add src to path
    ]
    
    # Add Windows-specific options to avoid DLL issues
    if sys.platform == 'win32':
        args.extend([
            '--uac-admin',  # Request admin privileges if needed
            '--disable-windowed-traceback',  # Cleaner error handling
        ])
    
    # Add data files if they exist
    data_files = [
        ('README.md', '.'),
        ('LICENSE', '.'),
    ]
    
    for src, dst in data_files:
        if os.path.exists(src):
            args.append(f'--add-data={src}{os.pathsep}{dst}')
    
    print(f"Running PyInstaller with args: {' '.join(args)}")
    
    try:
        PyInstaller.__main__.run(args)
        print("Build completed successfully!")
        return True
    except Exception as e:
        print(f"Build failed: {e}")
        return False


def copy_to_usb_deployment():
    """Copy the built EXE to USB_DEPLOYMENT folder."""
    exe_path = Path('dist') / 'key_manager_gui.exe'
    usb_dir = Path('USB_DEPLOYMENT')
    
    if not exe_path.exists():
        print(f"Executable not found at {exe_path}")
        return False
    
    # Create USB_DEPLOYMENT directory if it doesn't exist
    usb_dir.mkdir(exist_ok=True)
    
    # Copy the EXE
    target_path = usb_dir / 'key_manager_gui.exe'
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
    print(f"Total size: {target_path.stat().st_size / (1024*1024):.2f} MB")
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
    
    print("\n" + "="*60)
    print("PORTABLE BUILD COMPLETE!")
    print("="*60)
    print(f"Executable: USB_DEPLOYMENT/key_manager_gui.exe")
    print(f"Size: {(Path('USB_DEPLOYMENT/key_manager_gui.exe').stat().st_size / (1024*1024)):.2f} MB")
    print("\nThe application is ready to run from an encrypted USB drive.")
    print("No installation required - just copy the USB_DEPLOYMENT folder.")
    print("="*60)
    
    return True


def create_launcher_script():
    """Create a simple launcher script for the USB drive."""
    launcher_content = """@echo off
echo ========================================
echo   Key Manager - Secure Crypto Vault
echo ========================================
echo.
echo Starting Key Manager GUI...
echo.
key_manager_gui.exe
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
    print("="*60)
    print("Key Manager GUI - Portable EXE Builder")
    print("="*60)
    
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