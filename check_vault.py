#!/usr/bin/env python3
"""Check vault contents."""

import sys
import os
sys.path.insert(0, 'src')
from src.main import KeyManager

manager = KeyManager()
if manager.load_encrypted_data('TestPassword123!'):
    print('Vault loaded successfully!')
    print('Accounts in vault:', len(manager.address_db["accounts"]))
    
    total_addresses = 0
    for account_name, account_data in manager.address_db["accounts"].items():
        addresses = account_data.get('addresses', [])
        num_addresses = len(addresses)
        total_addresses += num_addresses
        print(f'Account {account_name}: {num_addresses} addresses')
        
        # Show first few addresses for Main account
        if account_name == 'Main' and addresses:
            print(f'  Sample addresses:')
            for i, addr in enumerate(addresses[:3]):
                print(f'    {addr["coin"]} {addr["chain"]}: {addr["address"]}')
    
    print(f'\nTotal addresses in vault: {total_addresses}')
    
    # Check vault file size
    if manager.data_file.exists():
        print(f'Vault file size: {manager.data_file.stat().st_size} bytes')
else:
    print('Failed to load vault')
