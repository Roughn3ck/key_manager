# Forge Prompt: ColdStack v5.2.3 — Railgun Sidecar (Part 5: GUI Integration + Build + Docs + Release)

## Project
- **Repo:** `B:\Blockchain\coldstack\`
- **AGENTS.md:** Read `B:\Blockchain\coldstack\AGENTS.md` for project conventions
- **Version:** v5.2.3 — Final phase of Railgun Sidecar integration
- **Scope:** Add Railgun tab to ColdStack GUI, wire up sidecar lifecycle, update build script, update docs, build EXE, git push, create GitHub release

## ⚠️ CRITICAL RULES
- This is the FINAL prompt (5 of 5) — all documentation updates, build, and git push happen here
- **DO** update README.md, STATUS.md, and AGENTS.md with v5.2.3 changes
- **DO** update build_gui_v5.py with new hidden imports + sidecar data
- **DO** build the EXE after all changes are verified
- **DO** push to git after the build succeeds
- **DO** create a GitHub release after pushing
- **DO** create a backup in `backups/v5.2.3/`

---

## Background

Parts 1–4 built the complete Node.js sidecar:
- P1: Express server skeleton + Python bridge client (`src/railgun_bridge.py`)
- P2: Railgun engine init, wallet loading, POI, balance scanning, network providers, Groth16 prover
- P3: Shield, unshield, and private transfer (0zk→0zk) routes with self-signing
- P4: Full cache rebuild and cache clear routes

Part 5 integrates the sidecar into the ColdStack GUI and finalizes the release.

## Architecture Decision: GUI Scope for v5.2.3

**The Railgun tab is minimal for v5.2.3.** It provides:
1. **Sidecar status indicator** — shows if the sidecar is running, engine initialized, wallets loaded
2. **Start/Stop sidecar button** — launches/stops the Node.js sidecar process
3. **Engine init button** — initializes the Railgun engine with ColdStack's RPC config
4. **Wallet load** — load a Railgun wallet from a mnemonic + encryption key
5. **Balance display** — shows shielded balances (Spendable bucket) per chain
6. **Refresh balances button** — triggers a balance scan
7. **Shield/Unshield/Transfer dialogs** — basic transfer functionality

The GUI is intentionally lean — it's a functional integration layer, not a polished product. The focus is on proving the architecture works end-to-end.

## Part A: New File — `src/railgun_tab.py`

Create a new file `src/railgun_tab.py` in the ColdStack source directory. This is the Railgun tab UI module, following the same pattern as `lp_tab.py` and `vault_tab.py`.

```python
"""
Railgun Tab — Privacy protocol integration for ColdStack.

Provides GUI for managing the Railgun sidecar, loading shielded wallets,
viewing private balances, and executing shield/unshield/transfer operations.

Version: v5.2.3 (August 2026)
"""
import threading
import json
from typing import Dict, List, Optional, Any
from pathlib import Path

import customtkinter as ctk

# Import the Python bridge client
from railgun_bridge import RailgunSidecar


class RailgunTab:
    """Railgun privacy tab for ColdStack GUI.

    Manages the Railgun Node.js sidecar lifecycle and provides
    UI for shielded wallet operations.
    """

    def __init__(self, gui):
        self.gui = gui
        self.sidecar: Optional[RailgunSidecar] = None
        self._widgets: Dict[str, Any] = {}
        self._railgun_wallet_id: Optional[str] = None
        self._railgun_address: Optional[str] = None
        self._encryption_key: Optional[str] = None
        self._balance_labels: Dict[str, ctk.CTkLabel] = {}
        self._sidecar_running = False

    def create_tab(self, parent):
        """Create the Railgun tab content inside the parent frame."""
        # Main container
        container = ctk.CTkFrame(parent, corner_radius=0)
        container.pack(fill="both", expand=True, padx=10, pady=10)

        # ─── Section 1: Sidecar Status ───
        status_frame = ctk.CTkFrame(container, corner_radius=8)
        status_frame.pack(fill="x", pady=(0, 10))

        ctk.CTkLabel(
            status_frame,
            text="🔒 Railgun Privacy Engine",
            font=ctk.CTkFont(size=18, weight="bold")
        ).pack(pady=(10, 5))

        # Status row
        status_row = ctk.CTkFrame(status_frame, fg_color="transparent")
        status_row.pack(fill="x", padx=15, pady=(0, 10))

        self._widgets["sidecar_status"] = ctk.CTkLabel(
            status_row,
            text="Sidecar: Not running",
            font=ctk.CTkFont(size=13),
            text_color="#888888"
        )
        self._widgets["sidecar_status"].pack(side="left", padx=(0, 20))

        self._widgets["engine_status"] = ctk.CTkLabel(
            status_row,
            text="Engine: Not initialized",
            font=ctk.CTkFont(size=13),
            text_color="#888888"
        )
        self._widgets["engine_status"].pack(side="left", padx=(0, 20))

        self._widgets["wallet_status"] = ctk.CTkLabel(
            status_row,
            text="Wallet: Not loaded",
            font=ctk.CTkFont(size=13),
            text_color="#888888"
        )
        self._widgets["wallet_status"].pack(side="left")

        # Action buttons row
        button_row = ctk.CTkFrame(status_frame, fg_color="transparent")
        button_row.pack(fill="x", padx=15, pady=(0, 10))

        self._widgets["btn_start_sidecar"] = ctk.CTkButton(
            button_row,
            text="Start Sidecar",
            width=120,
            command=self._on_start_sidecar
        )
        self._widgets["btn_start_sidecar"].pack(side="left", padx=(0, 10))

        self._widgets["btn_stop_sidecar"] = ctk.CTkButton(
            button_row,
            text="Stop Sidecar",
            width=120,
            state="disabled",
            fg_color="#cc4444",
            hover_color="#aa3333",
            command=self._on_stop_sidecar
        )
        self._widgets["btn_stop_sidecar"].pack(side="left", padx=(0, 10))

        self._widgets["btn_init_engine"] = ctk.CTkButton(
            button_row,
            text="Init Engine",
            width=120,
            state="disabled",
            command=self._on_init_engine
        )
        self._widgets["btn_init_engine"].pack(side="left", padx=(0, 10))

        self._widgets["btn_refresh_status"] = ctk.CTkButton(
            button_row,
            text="Refresh Status",
            width=120,
            state="disabled",
            command=self._on_refresh_status
        )
        self._widgets["btn_refresh_status"].pack(side="left")

        # ─── Section 2: Wallet Loading ───
        wallet_frame = ctk.CTkFrame(container, corner_radius=8)
        wallet_frame.pack(fill="x", pady=(0, 10))

        ctk.CTkLabel(
            wallet_frame,
            text="Shielded Wallet",
            font=ctk.CTkFont(size=16, weight="bold")
        ).pack(pady=(10, 5))

        # Mnemonic entry
        mnem_row = ctk.CTkFrame(wallet_frame, fg_color="transparent")
        mnem_row.pack(fill="x", padx=15, pady=(0, 5))
        ctk.CTkLabel(mnem_row, text="Mnemonic:", width=100, anchor="w").pack(side="left")
        self._widgets["mnemonic_entry"] = ctk.CTkEntry(
            mnem_row, show="*", width=400, placeholder_text="12 or 24 word BIP39 mnemonic"
        )
        self._widgets["mnemonic_entry"].pack(side="left", fill="x", expand=True, padx=(5, 0))

        # Encryption key entry
        key_row = ctk.CTkFrame(wallet_frame, fg_color="transparent")
        key_row.pack(fill="x", padx=15, pady=(0, 5))
        ctk.CTkLabel(key_row, text="Encryption Key:", width=100, anchor="w").pack(side="left")
        self._widgets["encryption_key_entry"] = ctk.CTkEntry(
            key_row, show="*", width=400, placeholder_text="32-byte hex encryption key (64 hex chars)"
        )
        self._widgets["encryption_key_entry"].pack(side="left", fill="x", expand=True, padx=(5, 0))

        # Load wallet button
        self._widgets["btn_load_wallet"] = ctk.CTkButton(
            wallet_frame,
            text="Load Shielded Wallet",
            width=180,
            state="disabled",
            command=self._on_load_wallet
        )
        self._widgets["btn_load_wallet"].pack(padx=15, pady=(5, 10), anchor="w")

        # Wallet info display
        self._widgets["wallet_info"] = ctk.CTkLabel(
            wallet_frame,
            text="",
            font=ctk.CTkFont(size=12),
            justify="left",
            anchor="w"
        )
        self._widgets["wallet_info"].pack(fill="x", padx=15, pady=(0, 10))

        # ─── Section 3: Balances ───
        balance_frame = ctk.CTkFrame(container, corner_radius=8)
        balance_frame.pack(fill="both", expand=True, pady=(0, 10))

        ctk.CTkLabel(
            balance_frame,
            text="Shielded Balances",
            font=ctk.CTkFont(size=16, weight="bold")
        ).pack(pady=(10, 5))

        # Refresh button
        self._widgets["btn_refresh_balances"] = ctk.CTkButton(
            balance_frame,
            text="Refresh Balances",
            width=150,
            state="disabled",
            command=self._on_refresh_balances
        )
        self._widgets["btn_refresh_balances"].pack(padx=15, pady=(0, 10), anchor="w")

        # Scrollable balance list
        self._balance_scroll = ctk.CTkScrollableFrame(balance_frame)
        self._balance_scroll.pack(fill="both", expand=True, padx=15, pady=(0, 10))

        self._widgets["balance_placeholder"] = ctk.CTkLabel(
            self._balance_scroll,
            text="No balances loaded. Load a wallet and click Refresh.",
            font=ctk.CTkFont(size=13),
            text_color="#888888"
        )
        self._widgets["balance_placeholder"].pack(pady=20)

        # ─── Section 4: Transactions ───
        tx_frame = ctk.CTkFrame(container, corner_radius=8)
        tx_frame.pack(fill="x", pady=(0, 10))

        ctk.CTkLabel(
            tx_frame,
            text="Transactions",
            font=ctk.CTkFont(size=16, weight="bold")
        ).pack(pady=(10, 5))

        tx_row = ctk.CTkFrame(tx_frame, fg_color="transparent")
        tx_row.pack(fill="x", padx=15, pady=(0, 10))

        self._widgets["btn_shield"] = ctk.CTkButton(
            tx_row, text="Shield", width=100, state="disabled",
            command=self._on_shield
        )
        self._widgets["btn_shield"].pack(side="left", padx=(0, 10))

        self._widgets["btn_unshield"] = ctk.CTkButton(
            tx_row, text="Unshield", width=100, state="disabled",
            command=self._on_unshield
        )
        self._widgets["btn_unshield"].pack(side="left", padx=(0, 10))

        self._widgets["btn_transfer"] = ctk.CTkButton(
            tx_row, text="Private Transfer", width=140, state="disabled",
            command=self._on_transfer
        )
        self._widgets["btn_transfer"].pack(side="left")

    # ─── Sidecar Lifecycle ───

    def _on_start_sidecar(self):
        """Start the Railgun sidecar process."""
        try:
            self.sidecar = RailgunSidecar()
            self.gui.show_notification("Starting Railgun sidecar...")

            def _start():
                try:
                    ok = self.sidecar.start()
                    if ok:
                        self._sidecar_running = True
                        self.gui.root.after(0, lambda: self._update_sidecar_ui(running=True))
                        self.gui.root.after(0, lambda: self.gui.show_notification("Railgun sidecar started"))
                    else:
                        self.gui.root.after(0, lambda: self.gui.show_notification("Sidecar failed to start", error=True))
                except Exception as e:
                    self.gui.root.after(0, lambda: self.gui.show_notification(f"Sidecar error: {e}", error=True))

            threading.Thread(target=_start, daemon=True).start()
        except Exception as e:
            self.gui.show_notification(f"Failed to start sidecar: {e}", error=True)

    def _on_stop_sidecar(self):
        """Stop the Railgun sidecar process."""
        if self.sidecar:
            try:
                self.sidecar.stop()
                self._sidecar_running = False
                self._railgun_wallet_id = None
                self._railgun_address = None
                self._update_sidecar_ui(running=False)
                self.gui.show_notification("Railgun sidecar stopped")
            except Exception as e:
                self.gui.show_notification(f"Stop error: {e}", error=True)

    def _update_sidecar_ui(self, running):
        """Update UI elements based on sidecar running state."""
        if running:
            self._widgets["sidecar_status"].configure(
                text="Sidecar: Running", text_color="#4caf50"
            )
            self._widgets["btn_start_sidecar"].configure(state="disabled")
            self._widgets["btn_stop_sidecar"].configure(state="normal")
            self._widgets["btn_init_engine"].configure(state="normal")
            self._widgets["btn_refresh_status"].configure(state="normal")
        else:
            self._widgets["sidecar_status"].configure(
                text="Sidecar: Not running", text_color="#888888"
            )
            self._widgets["engine_status"].configure(
                text="Engine: Not initialized", text_color="#888888"
            )
            self._widgets["wallet_status"].configure(
                text="Wallet: Not loaded", text_color="#888888"
            )
            self._widgets["btn_start_sidecar"].configure(state="normal")
            self._widgets["btn_stop_sidecar"].configure(state="disabled")
            self._widgets["btn_init_engine"].configure(state="disabled")
            self._widgets["btn_refresh_status"].configure(state="disabled")
            self._widgets["btn_load_wallet"].configure(state="disabled")
            self._widgets["btn_refresh_balances"].configure(state="disabled")
            self._widgets["btn_shield"].configure(state="disabled")
            self._widgets["btn_unshield"].configure(state="disabled")
            self._widgets["btn_transfer"].configure(state="disabled")

    # ─── Engine Init ───

    def _on_init_engine(self):
        """Initialize the Railgun engine with ColdStack's RPC config."""
        if not self.sidecar or not self._sidecar_running:
            self.gui.show_notification("Sidecar not running", error=True)
            return

        # Get RPC config from the GUI
        rpc_config = self.gui.rpc_config
        if not rpc_config:
            self.gui.show_notification("No RPC config loaded. Go Online first.", error=True)
            return

        def _init():
            try:
                result = self.sidecar.init_engine(rpc_config)
                self.gui.root.after(0, lambda: self._on_engine_init_done(result))
            except Exception as e:
                self.gui.root.after(0, lambda: self.gui.show_notification(f"Engine init failed: {e}", error=True))

        self.gui.show_notification("Initializing Railgun engine (may take a moment)...")
        threading.Thread(target=_init, daemon=True).start()

    def _on_engine_init_done(self, result):
        """Called when engine init completes."""
        if result.get("status") == "ok" or result.get("status") == "already_initialized":
            self._widgets["engine_status"].configure(
                text="Engine: Initialized", text_color="#4caf50"
            )
            self._widgets["btn_load_wallet"].configure(state="normal")
            self._widgets["btn_shield"].configure(state="normal")
            self._widgets["btn_unshield"].configure(state="normal")
            self._widgets["btn_transfer"].configure(state="normal")

            providers = result.get("providers", {})
            loaded = [k for k, v in providers.items() if v.get("status") == "ok"]
            self.gui.show_notification(f"Engine initialized. Providers: {', '.join(loaded)}")
        else:
            self.gui.show_notification(
                f"Engine init error: {result.get('error', 'unknown')}", error=True
            )

    # ─── Wallet Loading ───

    def _on_load_wallet(self):
        """Load a Railgun wallet from mnemonic + encryption key."""
        if not self.sidecar or not self._sidecar_running:
            self.gui.show_notification("Sidecar not running", error=True)
            return

        mnemonic = self._widgets["mnemonic_entry"].get().strip()
        encryption_key = self._widgets["encryption_key_entry"].get().strip()

        if not mnemonic:
            self.gui.show_notification("Enter a mnemonic first", error=True)
            return
        if not encryption_key:
            self.gui.show_notification("Enter an encryption key", error=True)
            return

        def _load():
            try:
                result = self.sidecar.load_wallet(mnemonic, encryption_key)
                self._railgun_wallet_id = result.get("walletId")
                self._railgun_address = result.get("railgunAddress")
                self._encryption_key = encryption_key
                self.gui.root.after(0, lambda: self._on_wallet_loaded(result))
            except Exception as e:
                self.gui.root.after(0, lambda: self.gui.show_notification(f"Wallet load failed: {e}", error=True))

        self.gui.show_notification("Loading shielded wallet...")
        threading.Thread(target=_load, daemon=True).start()

    def _on_wallet_loaded(self, result):
        """Called when wallet loading completes."""
        if result.get("status") == "ok":
            self._widgets["wallet_status"].configure(
                text=f"Wallet: {self._railgun_address[:12]}...{self._railgun_address[-8:]}",
                text_color="#4caf50"
            )
            self._widgets["wallet_info"].configure(
                text=f"Wallet ID: {self._railgun_wallet_id}\n"
                     f"0zk Address: {self._railgun_address}\n"
                     f"Created: {result.get('created', 'existing')}"
            )
            self._widgets["btn_refresh_balances"].configure(state="normal")
            self.gui.show_notification("Shielded wallet loaded")
        else:
            self.gui.show_notification(
                f"Wallet load error: {result.get('error', 'unknown')}", error=True
            )

    # ─── Balance Refresh ───

    def _on_refresh_balances(self):
        """Refresh shielded balances for all loaded Railgun networks."""
        if not self.sidecar or not self._sidecar_running:
            self.gui.show_notification("Sidecar not running", error=True)
            return
        if not self._railgun_wallet_id:
            self.gui.show_notification("No wallet loaded", error=True)
            return

        # Refresh for all Railgun-supported chains
        chains = ["ethereum", "arbitrum", "bsc", "polygon", "base", "optimism"]

        def _refresh():
            for chain in chains:
                try:
                    self.sidecar.refresh_balances(chain, [self._railgun_wallet_id])
                except Exception as e:
                    print(f"[railgun] Balance refresh for {chain}: {e}")

            # Give callbacks a moment to populate
            import time
            time.sleep(2)

            # Fetch status
            try:
                status = self.sidecar.get_balance_status()
                self.gui.root.after(0, lambda: self._render_balances(status))
            except Exception as e:
                self.gui.root.after(0, lambda: self.gui.show_notification(f"Balance status failed: {e}", error=True))

        self.gui.show_notification("Scanning shielded balances...")
        threading.Thread(target=_refresh, daemon=True).start()

    def _render_balances(self, status):
        """Render balance data in the scrollable frame."""
        # Clear existing
        for widget in self._balance_scroll.winfo_children():
            widget.destroy()

        buckets = status.get("buckets", {})
        wallet_buckets = buckets.get(self._railgun_wallet_id, {})

        if not wallet_buckets:
            self._widgets["balance_placeholder"] = ctk.CTkLabel(
                self._balance_scroll,
                text="No shielded balances found. Try refreshing again after scan completes.",
                font=ctk.CTkFont(size=13),
                text_color="#888888"
            )
            self._widgets["balance_placeholder"].pack(pady=20)
            self.gui.show_notification("No shielded balances found")
            return

        # Render balances by chain and bucket
        for chain, chain_buckets in wallet_buckets.items():
            chain_frame = ctk.CTkFrame(self._balance_scroll, corner_radius=6)
            chain_frame.pack(fill="x", pady=5)

            ctk.CTkLabel(
                chain_frame,
                text=f"━━━ {chain.upper()} ━━━",
                font=ctk.CTkFont(size=14, weight="bold"),
                anchor="w"
            ).pack(fill="x", padx=10, pady=(5, 2))

            for bucket_name, bucket_data in chain_buckets.items():
                erc20_amounts = bucket_data.get("erc20Amounts", [])
                if not erc20_amounts:
                    continue

                # Only show Spendable bucket prominently
                color = "#4caf50" if bucket_name == "Spendable" else "#888888"
                bucket_text = f"  {bucket_name}:"
                ctk.CTkLabel(
                    chain_frame,
                    text=bucket_text,
                    font=ctk.CTkFont(size=12, weight="bold" if bucket_name == "Spendable" else "normal"),
                    text_color=color,
                    anchor="w"
                ).pack(fill="x", padx=10)

                for erc20 in erc20_amounts:
                    token_addr = erc20.get("tokenAddress", "")[:10] + "..."
                    amount = erc20.get("amount", "0")
                    ctk.CTkLabel(
                        chain_frame,
                        text=f"    {token_addr}: {amount}",
                        font=ctk.CTkFont(size=11),
                        text_color=color,
                        anchor="w"
                    ).pack(fill="x", padx=15)

        self.gui.show_notification("Shielded balances updated")

    # ─── Status Refresh ───

    def _on_refresh_status(self):
        """Refresh the sidecar status display."""
        if not self.sidecar or not self._sidecar_running:
            return

        def _check():
            try:
                healthy = self.sidecar.is_healthy()
                if healthy:
                    status = self.sidecar.get_engine_status()
                    self.gui.root.after(0, lambda: self._update_status_display(status))
                else:
                    self.gui.root.after(0, lambda: self._update_sidecar_ui(running=False))
            except Exception:
                self.gui.root.after(0, lambda: self._update_sidecar_ui(running=False))

        threading.Thread(target=_check, daemon=True).start()

    def _update_status_display(self, status):
        """Update the status labels from engine status response."""
        if status.get("engineInitialized"):
            self._widgets["engine_status"].configure(
                text="Engine: Initialized", text_color="#4caf50"
            )
        else:
            self._widgets["engine_status"].configure(
                text="Engine: Not initialized", text_color="#888888"
            )

        wallets = status.get("walletsLoaded", 0)
        if wallets > 0:
            self._widgets["wallet_status"].configure(
                text=f"Wallet: {wallets} loaded", text_color="#4caf50"
            )
        else:
            self._widgets["wallet_status"].configure(
                text="Wallet: Not loaded", text_color="#888888"
            )

    # ─── Transaction Stubs ───

    def _on_shield(self):
        """Open shield dialog — public → private."""
        self.gui.show_notification("Shield dialog — coming in v5.3.0")

    def _on_unshield(self):
        """Open unshield dialog — private → public."""
        self.gui.show_notification("Unshield dialog — coming in v5.3.0")

    def _on_transfer(self):
        """Open private transfer dialog — 0zk → 0zk."""
        self.gui.show_notification("Transfer dialog — coming in v5.3.0")

    # ─── Cleanup ───

    def on_close(self):
        """Called when the GUI is closing — stop the sidecar."""
        if self.sidecar:
            try:
                self.sidecar.stop()
            except Exception:
                pass
```

## Part B: Modify `src/gui_main_v5.py`

### B.1: Add import for RailgunTab

Near the top of `gui_main_v5.py`, after the existing imports of `LPTab` and `VaultTab`, add:

```python
from railgun_tab import RailgunTab
```

### B.2: Add Railgun tab to the dashboard

In the `create_dashboard` method, after the LP tab is created (around line 632), add a new Railgun tab:

```python
# After: lp_tab = self.tabview.add("LP Positions")
railgun_tab = self.tabview.add("Railgun")
```

### B.3: Create RailgunTab instance and create tab content

After the LP tab setup (after `self.lp_tab._lp_restore_state()`), add:

```python
# v5.2.3: Railgun privacy tab
if not hasattr(self, 'railgun_tab') or self.railgun_tab is None:
    self.railgun_tab = RailgunTab(self)
self.railgun_tab.create_tab(railgun_tab)
```

### B.4: Add railgun_tab instance variable in `__init__`

Near the `self.lp_tab = None` and `self.vault_tab = None` lines (around line 336), add:

```python
# v5.2.3: Railgun tab instance (created after login)
self.railgun_tab: Optional[RailgunTab] = None
```

### B.5: Stop sidecar on close

In the `_on_close` method (the WM_DELETE_WINDOW handler), add sidecar cleanup before the existing cleanup code:

```python
# v5.2.3: Stop Railgun sidecar if running
if hasattr(self, 'railgun_tab') and self.railgun_tab:
    self.railgun_tab.on_close()
```

## Part C: Update `build_gui_v5.py`

### C.1: Update version strings

Change all `v5.2.2` references to `v5.2.3` in the build script:
- Docstring version
- `print` statements
- `build_gui_exe` function docstring
- Launcher script content

### C.2: Add new hidden imports

After the existing hidden imports block, add:

```python
# --- v5.2.3: Railgun bridge ---
'--hidden-import=railgun_bridge',
'--hidden-import=railgun_tab',
```

### C.3: Add sidecar directory to data files

After the existing `--add-data=rpc_endpoints.json;.` line, add:

```python
# --- v5.2.3: Railgun sidecar (Node.js) ---
'--add-data=sidecar;sidecar',
```

**Note:** This bundles the entire `sidecar/` directory (excluding `node_modules/` which is gitignored) into the EXE. The `railgun_bridge.py` `_find_sidecar_dir()` method already checks for `sidecar/` next to the EXE directory and in `resources/sidecar/`. The PyInstaller `--add-data` places it at `sidecar/` in the EXE's runtime directory.

**Important:** The sidecar requires Node.js to be installed on the user's system, OR the sidecar must be compiled to a standalone executable using `pkg` or `nexe`. For v5.2.3, document that Node.js is required. A future version can bundle a compiled sidecar executable.

### C.4: Update the backup version tag

Change:
```python
PREVIOUS_VERSION_TAG = "v5_1_4"
```
to:
```python
PREVIOUS_VERSION_TAG = "v5_2_1"
```

### C.5: Update build timestamp

Update the `BUILD:` timestamp at the bottom to the current date/time.

## Part D: Update `STATUS.md`

Add a new v5.2.3 section at the top (after the header), before the v5.2.1 section:

```markdown
## v5.2.3 - Railgun Privacy Integration (August 2026)

### Summary
v5.2.3 adds Railgun privacy protocol support via a Node.js sidecar process. ColdStack can now shield ERC-20 tokens, manage shielded balances, and execute private transfers (0zk→0zk) across Ethereum, Arbitrum, BNB Chain, Polygon, Base, and Optimism.

### New Features
- **Railgun Sidecar** — Node.js Express server wrapping the @railgun-community/wallet SDK
- **Shielded Wallets** — load BIP39 mnemonics into Railgun private balances
- **Shield/Unshield** — move ERC-20 tokens between public and private balances
- **Private Transfers** — 0zk→0zk encrypted transfers with optional memo
- **Balance Scanning** — automatic shielded balance updates via Railgun engine callbacks
- **POI Support** — Private Proof of Innocence status tracking
- **Cache Management** — full rebuild and cache clear for engine database

### Architecture
- Sidecar: Node.js Express server on localhost:8765
- Bridge: Python HTTP client (`src/railgun_bridge.py`) manages sidecar lifecycle
- GUI: New "Railgun" tab in the main tabview
- Self-signing mode (no Broadcaster/Waku dependency for v5.2.3)

### Requirements
- Node.js 18+ installed on the system (for running the sidecar)
- First launch downloads 50MB+ of proof artifacts (cached for subsequent use)

### Files Added
- `sidecar/` — complete Node.js sidecar (14 JS files + package.json)
- `src/railgun_bridge.py` — Python HTTP bridge client
- `src/railgun_tab.py` — Railgun GUI tab
```

## Part E: Update `README.md`

### E.1: Update the header subtitle

Change:
```
*Offline-first crypto key vault with LP position management for Hyperliquid and BNB Chain.*
```
to:
```
*Offline-first crypto key vault with LP position management and Railgun privacy protocol integration.*
```

### E.2: Update the latest release line

Change:
```
**Latest release: [v5.2.1 — BSC Pool Reads + Light/Dark Mode](https://github.com/Roughn3ck/key_manager/releases/tag/v5.2.1)**
```
to:
```
**Latest release: [v5.2.3 — Railgun Privacy Integration](https://github.com/Roughn3ck/key_manager/releases/tag/v5.2.3)**
```

### E.3: Add Railgun to the core capabilities section

After the existing "Three core capabilities" section (or however many there are), add a new capability:

```markdown
**4. Railgun Privacy** — Shield and transfer tokens privately on 6 EVM chains.
- Shield ERC-20 tokens into RAILGUN private balances
- Private transfers (0zk→0zk) with encrypted memos
- Shielded balance scanning with POI status tracking
- Supports Ethereum, Arbitrum, BNB Chain, Polygon, Base, and Optimism
- Node.js sidecar process (requires Node.js 18+)
```

### E.4: Add a "Requirements" section if one doesn't exist

```markdown
### System Requirements
- Windows 10/11 (64-bit)
- Python 3.10+ (for development only)
- Node.js 18+ (for Railgun privacy features)
- 200MB free disk space (includes Railgun proof artifacts)
```

## Part F: Update `AGENTS.md`

In the `## Architecture` section, after the module layout, add a new subsection:

```markdown
### Railgun Sidecar (v5.2.3)
- `sidecar/` — Node.js Express server wrapping @railgun-community/wallet SDK. Runs on localhost:8765.
- `sidecar/src/routes/` — engine, wallet, balances, transfer, poi, cache, health routes
- `sidecar/src/state.js` — shared sidecar state (engine, wallets, balances, scan status)
- `sidecar/src/db.js` — LevelDOWN database for encrypted wallet storage
- `sidecar/src/artifacts.js` — ArtifactStore for proof artifact downloads
- `sidecar/src/networks.js` — ColdStack chain name → Railgun NetworkName mapping
- `sidecar/src/callbacks.js` — balance and scan progress callbacks
- `sidecar/src/utils.js` — transaction helpers (gas, signing, approval)
- `src/railgun_bridge.py` — Python HTTP client managing sidecar lifecycle
- `src/railgun_tab.py` — CustomTkinter Railgun tab (sidecar status, wallet, balances, transactions)
```

## Part G: Build, Push, Release

### G.1: Syntax check all Python files

```bash
cd B:\Blockchain\coldstack
python -m py_compile src/railgun_bridge.py
python -m py_compile src/railgun_tab.py
python -m py_compile src/gui_main_v5.py
```

### G.2: Syntax check all JS files

```bash
cd sidecar
for f in src/server.js src/state.js src/db.js src/artifacts.js src/networks.js src/callbacks.js src/utils.js src/routes/*.js; do node -c "$f" && echo "$f: OK" || echo "$f: FAIL"; done
```

### G.3: Build the EXE

```bash
cd B:\Blockchain\coldstack
python build_gui_v5.py
```

### G.4: Verify the build

```bash
# Check the EXE was created
dir USB_DEPLOYMENT\coldstack.exe

# Check the sidecar directory is bundled
python -c "import zipfile; z=zipfile.ZipFile('dist/coldstack.exe'); print('sidecar' in str(z.namelist())[:1000])"
```

### G.5: Git push

```bash
cd B:\Blockchain\coldstack
git add -A
git status
git commit -m "v5.2.3: Railgun Privacy Integration

- Node.js sidecar (Express + @railgun-community/wallet SDK)
- Python bridge client (src/railgun_bridge.py)
- Railgun GUI tab (src/railgun_tab.py)
- Engine init, wallet loading, POI, balance scanning (P2)
- Shield/unshield/private transfer routes with self-signing (P3)
- Full cache rebuild and clear routes (P4)
- Supports Ethereum, Arbitrum, BNB Chain, Polygon, Base, Optimism
- Build script updated with sidecar bundling
- README.md and STATUS.md updated"

git push origin main
```

### G.6: Create GitHub release

First, review existing release formatting:
```bash
# Check existing releases
```

Create a new release `v5.2.3` with the EXE as an asset:
- Title: `v5.2.3 — Railgun Privacy Integration`
- Tags: `v5.2.3`
- Asset: `USB_DEPLOYMENT/coldstack.exe`
- Release notes: Use the STATUS.md v5.2.3 section as the release body

### G.7: Backup

```bash
cd B:\Blockchain\coldstack
mkdir backups\v5.2.3
xcopy src backups\v5.2.3\src\ /E /I
xcopy sidecar backups\v5.2.3\sidecar\ /E /I
copy build_gui_v5.py backups\v5.2.3\
copy rpc_endpoints.json backups\v5.2.3\
```

---

## File Summary

| File | Action |
|------|--------|
| `src/railgun_tab.py` | **NEW** |
| `src/gui_main_v5.py` | **MODIFIED** (import, tab add, init var, close handler) |
| `build_gui_v5.py` | **MODIFIED** (version, hidden imports, data files, backup tag) |
| `STATUS.md` | **MODIFIED** (add v5.2.3 section) |
| `README.md` | **MODIFIED** (update header, release link, Railgun section, requirements) |
| `AGENTS.md` | **MODIFIED** (add sidecar architecture subsection) |

## Verification Checklist

After all changes:

- [ ] `python -m py_compile src/railgun_tab.py` passes
- [ ] `python -m py_compile src/gui_main_v5.py` passes
- [ ] All 14 JS files pass `node -c`
- [ ] `build_gui_v5.py` runs and produces `USB_DEPLOYMENT/coldstack.exe`
- [ ] EXE file size is reasonable (~50-80MB with sidecar bundled)
- [ ] `git status` is clean after push
- [ ] GitHub release `v5.2.3` created with EXE asset
- [ ] `backups/v5.2.3/` contains src + sidecar + build script + rpc_endpoints.json