# Key Manager — Status Report

**Project Location:** `B:\Github\key_manager\`
**Last Updated:** 2026-06-18
**Assigned Agent:** Cline

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
│   ├── key_manager.exe       — CLI version
│   └── key_manager_gui.exe   — GUI version ✅ WORKING
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
| CLI (key_manager.exe) | ✅ Working | Full CLI functionality |
| GUI Source Code | ✅ Working | CustomTkinter dark theme, dashboard layout |
| GUI EXE (key_manager_gui.exe) | ✅ Working | Rebuilt June 2026, all login crash bugs fixed |
| Backup Engine | ✅ Working | Timestamped encrypted backups |
| Vault Init | ✅ Working | Creates encrypted vault on first run |

## GUI Fix History (June 2026)

The GUI was crashing immediately after login in both script and EXE modes. Three bugs were identified and fixed:

### Bug 1: `FreeConsole()` crashing script mode
- `ctypes.windll.kernel32.FreeConsole()` was called unconditionally at startup.
- In script mode this detaches stdout/stderr, causing a fatal "I/O operation on closed file" error.
- **Fix:** `FreeConsole()` now only runs when `getattr(sys, 'frozen', False)` is `True` (EXE mode only).

### Bug 2: Session timer started before status bar existed
- `attempt_login()` → `start_session_timer()` → `update_session_timer()` tried to update `self.session_timer_label` before `create_status_bar()` created it.
- Raised `AttributeError`, crashing immediately after successful login.
- **Fix:** `start_session_timer()` is now called after `create_status_bar()`. Backup status update guarded with `hasattr(self, 'backup_status_label')`.

### Bug 3: `update_session_timer()` lacked defensive guards
- Timer callback could fire before/after dashboard widgets existed, crashing.
- **Fix:** `update_session_timer()` checks `hasattr(self, 'session_timer_label')` before updating and only reschedules if dashboard is active.

## Verification

Both modes tested and confirmed working:
- **Script mode:** `python src/gui_main.py` — login screen appears, successful login opens dashboard.
- **EXE mode:** `USB_DEPLOYMENT\key_manager_gui.exe` — launches, login works, stays open (previously shut down immediately).

## Dependencies

```
customtkinter  — Modern Tkinter theme
cryptography   — AES-256-GCM + Argon2id
argon2-cffi    — Argon2id password hashing (pip: argon2)
pyperclip      — Clipboard functionality
Pillow (PIL)   — Image support for CustomTkinter
PyInstaller    — Build tool for EXE creation
```

## Data in Vault

The vault currently holds data for the LP Sentinel project:
- 124 addresses across 28 accounts
- Pool positions, wallet addresses, chain data
- All encrypted with AES-256-GCM

## Future Integration Path

### Phase 1: Get GUI Working ✅ COMPLETE
- [x] Rebuild GUI EXE on Windows (PyInstaller)
- [x] Test on Windows 11
- [x] Verify vault load/save works
- [x] Copy to BitLocker-encrypted USB

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

1. **Keep `FreeConsole()` frozen-only** — never call it in script mode or it kills stdout/stderr.
2. **Always guard `self.*` widget references** — use `hasattr()` checks in any callback that might fire before/after the widget lifecycle.
3. **Add a Python API server mode** — `key_manager --serve --port 8080` for agent access
4. **Create separate vaults** per pool tier (Genesis vault, NET vault, etc.)

---

*Status report updated June 18, 2026. GUI is fully functional in both script and EXE modes.*