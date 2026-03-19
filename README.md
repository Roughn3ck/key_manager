# Key Manager - Secure Crypto Key Storage

## 📋 What is Key Manager?

Key Manager is a secure application for storing and managing your cryptocurrency wallet information. It's designed to keep your sensitive data safe with multiple layers of encryption, making it ideal for storing:

- **24-word recovery phrases (mnemonics)**
- **Cryptocurrency wallet addresses**
- **Account information for different coins and networks**

Think of it as a digital safe for your crypto keys that you can carry on a USB drive.

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
- `key_manager.exe` - The main program (run this file)
- `USB_DEPLOYMENT/` - Folder containing the ready-to-use program
- `address_database.json` - Sample database with 124 cryptocurrency addresses (for reference)
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

### Basic Commands:

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

*Last Updated: March 19, 2026*