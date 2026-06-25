#!/usr/bin/env python3
"""
PyInstaller build script for Key Manager CLI.
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


def build_cli_exe():
    """Build the CLI executable using PyInstaller."""
    print("Building Key Manager CLI executable...")
    
    project_dir = Path(__file__).parent.resolve()
    
    # Check icon exists, use absolute path
    icon_path = project_dir / 'assets' / 'icon.ico'
    icon_args = [f'--icon={icon_path}'] if icon_path.exists() else []
    
    # PyInstaller arguments for onefile portable EXE
    # CLI app needs a console window (no --windowed flag)
    args = [
        str(project_dir / 'src' / 'main.py'),
        '--name=key_manager',
        '--onefile',
        '--console',  # CLI app needs console window
        *icon_args,
        '--hidden-import=click',
        '--hidden-import=rich',
        '--hidden-import=cryptography',
        '--hidden-import=argon2',
        '--hidden-import=json',
        '--hidden-import=pathlib',
        '--hidden-import=datetime',
        '--collect-all=cryptography',
        '--collect-all=argon2',
        '--clean',
        '--noconfirm',
        f'--paths={project_dir / "src"}',  # Explicitly add src to path
        f'--distpath={project_dir / "dist"}',
        f'--workpath={project_dir / "build"}',
        f'--specpath={project_dir}',
    ]
    
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
    exe_path = Path('dist') / 'key_manager.exe'
    usb_dir = Path('USB_DEPLOYMENT')
    
    if not exe_path.exists():
        print(f"Executable not found at {exe_path}")
        return False
    
    # Create USB_DEPLOYMENT directory if it doesn't exist
    usb_dir.mkdir(exist_ok=True)
    
    # Copy the EXE
    target_path = usb_dir / 'key_manager.exe'
    shutil.copy2(exe_path, target_path)
    
    print(f"Copied {exe_path} to {target_path}")
    print(f"Total size: {target_path.stat().st_size / (1024*1024):.2f} MB")
    return True


def check_dependencies():
    """Check if all required dependencies are installed."""
    required_packages = [
        'PyInstaller',
        'click',
        'rich',
        'cryptography',
        'argon2',
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
    
    print("="*60)
    print("Key Manager CLI - Portable EXE Builder")
    print("="*60)
    
    # Check dependencies
    if not check_dependencies():
        print("\nPlease install missing dependencies and try again.")
        return 1
    
    # Clean previous builds
    clean_build_dirs()
    
    # Build the EXE
    if not build_cli_exe():
        return 1
    
    # Copy to USB deployment
    if not copy_to_usb_deployment():
        return 1
    
    print("\n" + "="*60)
    print("CLI BUILD COMPLETE!")
    print("="*60)
    print(f"Executable: USB_DEPLOYMENT/key_manager.exe")
    print(f"Size: {(Path('USB_DEPLOYMENT/key_manager.exe').stat().st_size / (1024*1024)):.2f} MB")
    print("="*60)
    
    return 0


if __name__ == '__main__':
    sys.exit(main())