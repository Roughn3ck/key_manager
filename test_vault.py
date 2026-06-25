#!/usr/bin/env python3
"""Test vault password."""

import sys
import os
sys.path.insert(0, 'src')
from src.main import KeyManager

manager = KeyManager()
print('Vault exists:', manager.data_file.exists())
if manager.data_file.exists():
    print('Vault size:', manager.data_file.stat().st_size, 'bytes')
    # Try to load with password
    success = manager.load_encrypted_data('TestPassword123!')
    print('Load with TestPassword123!:', success)
