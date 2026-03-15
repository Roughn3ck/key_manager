#!/usr/bin/env python3
"""
Initialize the vault with a default password.
"""

import sys
import os
from pathlib import Path

# Add the src directory to the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from src.main import KeyManager

def initialize_vault():
    """Initialize the vault with a default password."""
    
    print("Initializing vault...")
    
    # Create a KeyManager instance
    manager = KeyManager()
    
    # Check if vault already exists
    if manager.data_file.exists():
        print("Vault already exists at:", manager.data_file)
        return True
    
    # Set a default password (in production, user should set their own)
    default_password = "ChangeMe123!"  # User should change this immediately
    
    print(f"Creating new vault with default password: {default_password}")
    print("WARNING: Change this password immediately after initialization!")
    
    # Initialize the vault
    if manager.init_vault(default_password):
        print("Vault initialized successfully!")
        print(f"Vault location: {manager.data_file}")
        return True
    else:
        print("Failed to initialize vault!")
        return False

if __name__ == "__main__":
    success = initialize_vault()
    sys.exit(0 if success else 1)