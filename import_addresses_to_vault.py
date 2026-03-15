#!/usr/bin/env python3
"""
Script to import addresses from address_database.json into the encrypted vault.
"""

import json
import os
import sys
from pathlib import Path

# Add the src directory to the path so we can import our modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from src.main import KeyManager

def import_addresses(password=None):
    """Import all addresses from address_database.json into the vault."""
    
    # Load the address database
    db_path = Path("address_database.json")
    if not db_path.exists():
        print("Error: address_database.json not found. Run process_excel.py first.")
        return False
    
    with open(db_path, 'r', encoding='utf-8') as f:
        address_db = json.load(f)
    
    # Initialize key manager
    manager = KeyManager()
    
    # Check if vault exists
    if not manager.data_file.exists():
        print("Error: Vault not found. Please initialize the vault first.")
        print("Run: python -m src.main init")
        return False
    
    # Get password from user if not provided
    if password is None:
        import getpass
        password = getpass.getpass("Enter master password: ")
    
    # Try to unlock the vault
    print("Unlocking vault...")
    if not manager.load_encrypted_data(password):
        print("Error: Failed to unlock vault. Wrong password or corrupted vault.")
        return False
    
    print("Vault unlocked successfully")
    
    # Import addresses for each account
    accounts_imported = 0
    addresses_imported = 0
    
    for account_name, account_data in address_db['accounts'].items():
        print(f"\nImporting addresses for account: {account_name}")
        
        # Initialize account if not exists
        if account_name not in manager.address_db["accounts"]:
            manager.address_db["accounts"][account_name] = {
                "addresses": [],
                "notes": ""
            }
        
        # Add each address to the in-memory database
        for address_data in account_data['addresses']:
            coin = address_data['coin']
            chain = address_data['chain']
            address = address_data['address']
            
            # Skip empty addresses
            if not address or address.strip() == '':
                print(f"  ⚠ Skipping empty address for {coin} ({chain})")
                continue
            
            # Add the address to the in-memory database
            try:
                manager.address_db["accounts"][account_name]["addresses"].append({
                    "coin": coin,
                    "chain": chain,
                    "address": address
                })
                addresses_imported += 1
                print(f"  ✓ Added {coin} ({chain}): {address[:20]}...")
            except Exception as e:
                print(f"  ✗ Error adding {coin} ({chain}): {e}")
        
        accounts_imported += 1
    
    # Save all changes at once
    print(f"\nSaving {addresses_imported} addresses from {accounts_imported} accounts to vault...")
    if manager.save_encrypted_data(password):
        print("✓ Vault saved successfully!")
    else:
        print("✗ Failed to save vault!")
        return False
    
    print(f"\nSuccessfully imported {addresses_imported} addresses from {accounts_imported} accounts!")
    
    # Show summary
    print("\n=== Import Summary ===")
    for account_name in address_db['accounts'].keys():
        if account_name in manager.address_db["accounts"]:
            count = len(manager.address_db["accounts"][account_name]["addresses"])
            print(f"{account_name}: {count} addresses")
    
    return True

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Import addresses from address_database.json into vault")
    parser.add_argument("--password", "-p", help="Master password (optional, will prompt if not provided)")
    
    args = parser.parse_args()
    
    success = import_addresses(password=args.password)
    sys.exit(0 if success else 1)
