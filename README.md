# Key Manager - Secure Crypto Key Storage

## 📋 What is Key Manager?

Key Manager is a secure application for storing and managing your cryptocurrency wallet information. It's designed to keep your sensitive data safe with multiple layers of encryption, making it ideal for storing:

- **24-word recovery phrases (mnemonics)**
- **Cryptocurrency wallet addresses**
- **Account information for different coins and networks**

Think of it as a digital safe for your crypto keys that you can carry on a USB drive.

## 🎨 New Modern GUI (Version 2.0)

Key Manager now features a modern, dark-themed graphical interface with enhanced security features:

### GUI Features:
- **Modern Dark Theme** - Easy on the eyes with CustomTkinter
- **Secure Login Screen** - Password-only entry with Argon2id/AES validation
- **Dashboard Layout** - Left panel with account list, right panel with chain view
- **Clipboard Interface** - Copy buttons for addresses with multi-copy queue
- **5-Minute Session Rule** - Auto-lock after 5 minutes of inactivity
- **Secure Mnemonic Reveal** - Password re-entry required to view 24-word phrases
- **Automatic Backups** - Timestamped encrypted backups of your vault

### Two Interfaces Available:
1. **CLI Interface** - Original command-line tool (`key_manager.exe`)
2. **GUI Interface** - New graphical interface (`key_manager_gui.exe`)

## 🔒 Security Features

### Dual-Layer Protection
1. **USB Drive Encryption (BitLocker)** - The entire USB drive is encrypted
2. **Application Encryption (AES-256-GCM)** - Your data is encrypted again inside the application

### How Your Data is Protected
- **Master Password Required** - You create one strong password to access everything
- **Military-Grade Encryption** - Uses the same encryption standards as banks and governments
- **No Internet Connection Needed** - Works completely offline for maximum security
- **Automatic Locking** - Data is automatically encrypted when you close the program

## 📦 What's Included

### Files in This Package:
- `key_manager.exe` - The CLI program (original command-line interface)
- `key_manager_gui.exe` - The new GUI program (modern graphical interface)
- `USB_DEPLOYMENT/` - Folder containing the ready-to-use programs
- `address_database.json` - Sample database with 124 cryptocurrency addresses (for reference)
- `src/backup_engine.py` - Automated backup system with timestamped encrypted copies
- `src/gui_main.py` - Modern GUI implementation using CustomTkinter
- `build_gui.py` - PyInstaller build script for creating portable EXE
- Various support files for developers

## 🚀 Getting Started

### For Non-Technical Users:

1. **Copy to USB Drive:**
   - Copy the entire `USB_DEPLOYMENT` folder to your BitLocker-encrypted USB drive
   - The `key_manager.exe` file inside is your program

2. **Running the Program:**
   - Double-click `key_manager.exe` to open the program
   - **Important:** The window will close immediately if you just double-click
   - **Correct way:** Right-click on `key_manager.exe` and select "Open in Terminal" or run from Command Prompt

3. **First-Time Setup:**
   ```
   key_manager.exe init
   ```
   - This creates your secure vault
   - You'll be prompted to create a master password
   - **Remember this password!** If you lose it, your data is permanently locked

## 💡 How to Use (Step by Step)

### Using the GUI Interface:

1. **Run the GUI Program:**
   ```
   key_manager_gui.exe
   ```
   Or from Python:
   ```
   python src/gui_main.py
   ```

2. **Login Screen:**
   - Enter your master password
   - Click "Unlock Vault" or press Enter
   - The vault will unlock and show the main dashboard

3. **Main Dashboard:**
   - **Left Panel:** Click any account to select it
   - **Right Panel:** View addresses and copy them to clipboard
   - **Session Timer:** Shows remaining time before auto-lock (5 minutes)
   - **Status Bar:** Shows backup count and lock button

4. **Viewing Mnemonics:**
   - Click "Reveal Mnemonic" for accounts that have recovery phrases
   - Re-enter your master password for security
   - Mnemonic will auto-hide after 5 minutes

### Using the CLI Interface:

1. **Initialize a New Vault** (First time only):
   ```
   key_manager.exe init
   ```

2. **Unlock Your Vault** (Every time you use it):
   ```
   key_manager.exe unlock
   ```

3. **See Available Commands:**
   ```
   key_manager.exe --help
   ```

### Managing Your Crypto Data:

4. **List All Accounts:**
   ```
   key_manager.exe accounts
   ```

5. **View Addresses:**
   ```
   key_manager.exe addresses
   ```
   Or view a specific account:
   ```
   key_manager.exe addresses "G1"
   ```

6. **Add a New Address:**
   ```
   key_manager.exe add-address "AccountName" "BTC" "P2TR" "bc1q..."
   ```

7. **Add a Recovery Phrase (24 words):**
   ```
   key_manager.exe add-mnemonic "AccountName" "word1 word2 ... word24"
   ```

8. **Generate a Secure Password:**
   ```
   key_manager.exe gen-password
   ```

9. **Check Vault Status:**
   ```
   key_manager.exe status
   ```

## 🏗️ Account Structure

Your crypto accounts are organized into "pools" for easy management:

### Four Main Pools:
1. **Genesis Pool** - 6 accounts (G1, G2, G3, G4, G5, G6)
2. **SafetyNet Pool** - 5 accounts (N1, N2, N3, N4, N5)
3. **Foundation Pool** - 5 accounts (F1, F2, F3, F4, F5)
4. **Seed Pool** - 5 accounts (S1, S2, S3, S4, S5)

### Additional Accounts:
- Main, G SS, Expenses, Monero, Logos, and more

## 🔧 Troubleshooting

### Common Issues:

**Problem:** "The program opens and closes immediately"
**Solution:** Run from Command Prompt instead of double-clicking

**Problem:** "ModuleNotFoundError: No module named 'click'"
**Solution:** All dependencies are already included in the executable - this shouldn't happen with the provided `key_manager.exe`

**Problem:** "Failed to unlock vault"
**Solution:** 
- Make sure you're using the correct password
- Check that `key_vault.encrypted` exists in the same folder
- If you lost your password, you cannot recover the data (this is for security)

**Problem:** "Command not recognized"
**Solution:** Make sure you're typing commands exactly as shown, including the `.exe` extension

## 📁 File Locations

### Where Your Data is Stored:
- **Encrypted Data:** `C:\Users\[YourUsername]\.key_manager\key_vault.encrypted`
- **Program Files:** Wherever you copied the `USB_DEPLOYMENT` folder

### Backup Recommendations:
1. **Regular Backups:** Copy your `key_vault.encrypted` file to multiple secure locations
2. **Password Storage:** Keep your master password in a secure password manager
3. **USB Drive:** Keep the USB drive in a safe, physically secure location

## 🛡️ Security Best Practices

### Do:
- ✅ Use a strong, unique master password (12+ characters, mix of letters, numbers, symbols)
- ✅ Store the USB drive in a safe or lockbox
- ✅ Make regular backups of your `key_vault.encrypted` file
- ✅ Test restoring from backup periodically
- ✅ Keep the program offline (no internet connection)

### Don't:
- ❌ Don't share your master password with anyone
- ❌ Don't store the password with the USB drive
- ❌ Don't use the program on public or untrusted computers
- ❌ Don't forget to make backups
- ❌ Don't lose your master password (data is irrecoverable)

## 🔄 Updating the Program

### For Future Updates:
1. Download the new version
2. Copy it to your USB drive (overwrite the old `key_manager.exe`)
3. Your existing `key_vault.encrypted` data will still work

## 🤝 Getting Help

### If You Need Assistance:
1. **Check This README** - Most questions are answered here
2. **Review Available Commands** - Run `key_manager.exe --help`
3. **Look at Sample Data** - The `address_database.json` shows the structure
4. **Contact Support** - If you're still having issues

## 📊 What's Already Set Up

This package comes pre-configured with:
- ✅ 124 cryptocurrency addresses across 28 accounts
- ✅ 4 organized pools (Genesis, SafetyNet, Foundation, Seed)
- ✅ Support for multiple coins: BTC, ETH, DASH, ZEC, XMR, SOL, DOT, ATOM, RUNE, SCRT, SUI
- ✅ Ready-to-use executable (no Python installation needed)
- ✅ All security features enabled

## 🎯 Quick Start Summary

1. **Copy** `USB_DEPLOYMENT` folder to encrypted USB drive
2. **Open** Command Prompt in that folder
3. **Initialize:** `key_manager.exe init` (first time only)
4. **Unlock:** `key_manager.exe unlock` (each use)
5. **Explore:** Try `accounts`, `addresses`, `status` commands
6. **Secure:** Make backups and store password safely

---

**Remember:** Your crypto security is only as strong as your practices. This tool provides the technology, but you provide the careful handling that keeps your assets safe.

## 🔧 Developer Notes — GUI Fixes (June 2026)

The GUI was crashing on login in both script (`python src/gui_main.py`) and EXE modes. Three root causes were identified and fixed:

### Bug 1: `FreeConsole()` crashing script mode
- **Cause:** `ctypes.windll.kernel32.FreeConsole()` was called unconditionally at startup. In script mode, this detaches stdout/stderr from the terminal, causing a fatal "I/O operation on closed file" error that immediately crashed the app.
- **Fix:** `FreeConsole()` now only runs when the app is frozen (i.e., running as a PyInstaller EXE). Script mode keeps the console attached so errors are visible.

### Bug 2: Session timer started before the status bar existed
- **Cause:** `attempt_login()` called `start_session_timer()` → `update_session_timer()`, which tried to update `self.session_timer_label` before `create_status_bar()` had created it. This raised an `AttributeError` and crashed immediately after successful login.
- **Fix:** `start_session_timer()` is now called only after the status bar (and its labels) have been created. The backup status update is also guarded with `hasattr(self, 'backup_status_label')`.

### Bug 3: `update_session_timer()` lacked defensive guards
- **Cause:** If the timer callback fired before or after the dashboard widgets existed, it would crash with an `AttributeError`.
- **Fix:** `update_session_timer()` now checks `hasattr(self, 'session_timer_label')` before updating the label, and only reschedules if the dashboard is still active.

### Running the GUI

**Script mode (development):**
```
python src/gui_main.py
```
This now works correctly — the login screen appears, and successful login opens the dashboard without crashing.

**Portable EXE mode:**
```
USB_DEPLOYMENT\key_manager_gui.exe
```
The EXE was rebuilt with `python build_gui.py` and launches correctly. It stays open after login (previously it shut down immediately due to Bug 1 above).

### Rebuilding the GUI EXE
```
python build_gui.py
```
This runs PyInstaller with `--onefile --windowed` and copies the result to `USB_DEPLOYMENT/key_manager_gui.exe`. The resulting EXE is ~50-100MB (Python runtime + CustomTkinter + cryptography + argon2 bundled).

*Last Updated: June 18, 2026*
