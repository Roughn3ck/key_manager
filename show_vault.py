#!/usr/bin/env python3
"""Show vault contents in detail."""

import sys
import os
sys.path.insert(0, 'src')
from src.main import KeyManager

def show_vault_contents():
    manager = KeyManager()
    if manager.load_encrypted_data('TestPassword123!'):
        print('=== VAULT CONTENTS ===')
        print(f'Accounts found: {len(manager.address_db["accounts"])}')
        
        total_addresses = 0
        for account_name, account_data in manager.address_db["accounts"].items():
            addresses = account_data.get('addresses', [])
            num_addresses = len(addresses)
            total_addresses += num_addresses
            print(f'\nAccount: {account_name}')
            print(f'  Addresses: {num_addresses}')
            
            for addr in addresses:
                print(f'    {addr["coin"]} {addr["chain"]}: {addr["address"]}')
        
        print(f'\nTotal addresses in vault: {total_addresses}')
        
        # Check vault file size
        if manager.data_file.exists():
            print(f'Vault file size: {manager.data_file.stat().st_size} bytes')
    else:
        print('Failed to load vault')

if __name__ == '__main__':
    show_vault_contents()