# Key Manager — Status Report

**Project Location:** `/mnt/b/Github/key_manager/` → Moving to `/mnt/b/Blockchain/key_manager/`
**Last Updated:** 2026-04-26
**Assigned Agent:** TBD (DeepSeek V4 recommended for integration work)

## Architecture

```
key_manager/
├── src/
│   ├── gui_main.py      — CustomTkinter dark GUI (login, dashboard, vault)
│   ├── crypto_engine.py — AES-256-GCM + Argon2id key derivation
│   ├── backup_engine.py — Auto timestamped encrypted backups
│   └── main.py          — CLI interface + KeyManager class
├── build_gui.py         — PyInstaller build script
├── init_vault.py        — First-time vault setup
├── USB_DEPLOYMENT/      — Compiled EXEs for portable USB use
│   ├── key_manager.exe       (35MB) — CLI version
│   └── key_manager_gui.exe   (280KB) — GUI version ⚠️ BROKEN
└── README.md
```

## Security Design

- **Master Password → Argon2id (3 iterations, 64MB memory) → AES-256-GCM Key**
- Salt (16 bytes) + Nonce (12 bytes) per encryption
- Data stored as: `salt|nonce|ciphertext|tag` (base64 encoded)
- 5-minute auto-lock timeout
- Password re-entry required to view mnemonics
- Automatic encrypted backups on every session

## Component Status

| Component | Status | Notes |
|-----------|--------|-------|
| Crypto Engine | ✅ Working | AES-256-GCM + Argon2id, solid implementation |
| CLI (key_manager.exe) | ✅ Working | 35MB, full CLI functionality |
| GUI Source Code | ✅ Complete | CustomTkinter dark theme, dashboard layout |
| GUI EXE (key_manager_gui.exe) | ❌ BROKEN | 280KB (should be 50-100MB), DLL not bundled |
| Backup Engine | ✅ Working | Timestamped encrypted backups |
| Vault Init | ✅ Working | Creates encrypted vault on first run |

## Critical Issue: GUI EXE Build

**Error:** `The ordinal 380 could not be located in the dynamic link library B:\Github\key_manager\USB_DEPLOYMENT\key_manager_gui.exe`

**Root Cause:** The GUI EXE is only 280KB — PyInstaller did NOT properly bundle the Python runtime, CustomTkinter, cryptography, or argon2 libraries. The DLL ordinal 380 error means a function in a dependency DLL wasn't found because the DLL wasn't included.

**Fix Steps (must be done on Windows):**
```powershell
cd B:\Github\key_manager

# 1. Install build dependencies
pip install pyinstaller customtkinter cryptography argon2 pyperclip pillow

# 2. Clean previous builds
python build_gui.py
# OR manually:
rmdir /s /q build dist
pyinstaller --clean --noconfirm ^
    src/gui_main.py ^
    --name=key_manager_gui ^
    --onefile ^
    --windowed ^
    --add-data="src;src" ^
    --hidden-import=customtkinter ^
    --hidden-import=cryptography ^
    --hidden-import=argon2 ^
    --hidden-import=pyperclip ^
    --collect-all=customtkinter ^
    --collect-all=cryptography ^
    --collect-all=argon2 ^
    --collect-all=PIL ^
    --paths=src

# 3. Verify output size (should be 50-100MB+)
dir dist\key_manager_gui.exe

# 4. Copy to USB deployment
copy dist\key_manager_gui.exe USB_DEPLOYMENT\
```

**Expected size after rebuild:** 50-100MB (Python runtime + all deps bundled)

## Dependencies

```
customtkinter  — Modern Tkinter theme
cryptography   — AES-256-GCM + Argon2id
argon2-cffi     — Argon2id password hashing (pip: argon2)
pyperclip       — Clipboard functionality
Pillow (PIL)    — Image support for CustomTkinter
PyInstaller     — Build tool for EXE creation
```

## Data in Vault

The vault currently holds data for the LP Sentinel project:
- 124 addresses across 28 accounts
- Pool positions, wallet addresses, chain data
- All encrypted with AES-256-GCM

## Integration Path (Future)

### Phase 1: Get GUI Working
- [ ] Rebuild GUI EXE on Windows (PyInstaller)
- [ ] Test on Windows 10/11
- [ ] Verify vault load/save works
- [ ] Copy to BitLocker-encrypted USB

### Phase 2: OpenClaw Integration
- [ ] Create API wrapper (Flask/FastAPI) for programmatic access
- [ ] Agent can query vault for addresses (read-only, no key exposure)
- [ ] Kris approves vault operations via keymaster protocol
- [ ] Keymaster agent manages transfer orchestration

### Phase 3: Pool Management
- [ ] Vault holds all pool wallet keys
- [ ] Sentinel detects rebalancing needs
- [ ] Keymaster executes transfers via vault
- [ ] Privacy rails (ZEC/XMR) for movements
- [ ] G1 back to Chainflip, G5 → WBTC/SOL, G6 drain, N1 → G6

## Recommendations

1. **Rebuild the GUI EXE on Windows** — This is the blocker. Do it on the HP laptop or FishTank Windows side.
2. **Add a Python API server mode** — `key_manager --serve --port 8080` for agent access
3. **Move project** to `/mnt/b/Blockchain/key_manager/` (as planned)
4. **Assign to DeepSeek V4** when it's routed — V4 Pro is perfect for the integration coding work
5. **Create separate vaults** per pool tier (Genesis vault, NET vault, etc.)

---

*Status report by Circuit (local-logic). April 26, 2026.*
