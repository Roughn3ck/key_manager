# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files
from PyInstaller.utils.hooks import collect_submodules
from PyInstaller.utils.hooks import collect_all

datas = [('rpc_endpoints.json', '.')]
binaries = []
hiddenimports = ['customtkinter', 'cryptography', 'argon2', 'pyperclip', 'json', 'queue', 'pathlib', 'datetime', 'openpyxl', 'hdwallet', 'mnemonic', 'coincurve', 'base58', 'cbor2', 'pynacl', 'ed25519_blake2b', 'crcmod', 'Crypto', 'Crypto.Hash', 'Crypto.Hash.keccak', 'click', 'rich', 'rich.console', 'rich.table', 'rich.panel', 'rich.progress', 'urllib', 'urllib.request', 'urllib.error', 'ssl', '_ssl', 'http.client', 'socket', '_socket', 'lp_liquidity_manager', 'evm_transfer_dialog', 'vault_deposit_dialog', 'swap_dialog', 'settings_dialog', 'account_dialogs', 'vault_tab', 'lp_tab', 'chain_options', 'appearance', 'lp_engine', 'price_engine', 'balance_engine', 'rpc_config', 'venue_adapters', 'venue_adapters.hyperliquid_adapter', 'venue_adapters.venue_writer', 'venue_adapters.hyperliquid_writer', 'saved_pools', 'vault_tracker', 'certifi', 'key_manager_agent', 'http.server', 'socketserver']
datas += collect_data_files('certifi')
hiddenimports += collect_submodules('urllib')
hiddenimports += collect_submodules('venue_adapters')
tmp_ret = collect_all('customtkinter')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('cryptography')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('argon2')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('pyperclip')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('PIL')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('hdwallet')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('mnemonic')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('rich')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['B:\\Blockchain\\coldstack\\src\\gui_main_v5.py'],
    pathex=['B:\\Blockchain\\coldstack\\src'],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='coldstack',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=True,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
