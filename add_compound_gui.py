#!/usr/bin/env python3
"""Patch script to add compound fees GUI to gui_main_v5.py"""
import os

os.chdir(r"B:\Blockchain\coldstack")

path = "src/gui_main_v5.py"
with open(path, "r", encoding="utf-8") as f:
    content = f.read()

# 1. Add CompoundFeesParams to imports
content = content.replace(
    "    CollectFeesParams, DecreaseLiquidityParams, RebalanceParams,\n)",
    "    CollectFeesParams, CompoundFeesParams, DecreaseLiquidityParams, RebalanceParams,\n)",
)

# 2. Add Compound Fees button before Collect Fees button
old_button = '''        if self.app_mode == "advanced" and position.position_id:
            ctk.CTkButton(button_frame, text="Collect Fees", width=100, height=26,'''

new_button = '''        if self.app_mode == "advanced" and position.position_id and position.position_id.startswith("hyperevm:"):
            ctk.CTkButton(button_frame, text="Compound Fees", width=110, height=26,
                          font=ctk.CTkFont(size=10),
                          fg_color=("#20c997", "#1aa179"),
                          command=lambda pos=position: self._lp_compound_fees_dialog(pos)
                          ).pack(pady=2)

        if self.app_mode == "advanced" and position.position_id:
            ctk.CTkButton(button_frame, text="Collect Fees", width=100, height=26,'''

content = content.replace(old_button, new_button)

# 3. Add _lp_compound_fees_dialog method before _lp_collect_fees_dialog
dialog_method = '''    def _lp_compound_fees_dialog(self, position):
        """Show confirmation dialog and compound fees for an LP position."""
        from tkinter import messagebox
        if not self.current_account:
            self.show_notification("Select a vault account first", error=True)
            return
        confirm = messagebox.askyesno(
            "Confirm: Compound Fees",
            "You are about to compound fees for position:\\n"
            f"  {position.pair} ({position.position_id})\\n\\n"
            "This will submit multiple transactions on HyperEVM:\\n"
            "  1. Collect accrued fees\\n"
            "  2. Swap to optimal ratio (if needed)\\n"
            "  3. Increase liquidity with collected amounts\\n\\n"
            "The key_manager_agent must be running and unlocked.\\n\\n"
            "Continue?",
            parent=self.root,
        )
        if not confirm:
            return
        self.show_notification("Compounding fees... (multi-TX operation)")

        def _do_compound():
            try:
                writer = self.lp_engine.get_writer("hyperliquid", self.current_password)
                if writer is None:
                    self.root.after(0, lambda: self.show_notification(
                        "Writer not available", error=True))
                    return
                if not writer.is_available():
                    self.root.after(0, lambda: self.show_notification(
                        "Agent not running. Start key_manager_agent with --serve.", error=True))
                    return
                tx_hashes = writer.compound_fees(CompoundFeesParams(
                    account=self.current_account,
                    position_id=position.position_id,
                ))
                if tx_hashes:
                    self.root.after(0, lambda: self.show_notification(
                        f"Compound fees done. {len(tx_hashes)} TXs submitted. First: {tx_hashes[0][:20]}..."))
                else:
                    self.root.after(0, lambda: self.show_notification(
                        "Compound fees: no transactions submitted", error=True))
            except Exception as e:
                error_msg = str(e)
                print(f"[compound_fees] error: {error_msg}")
                self.root.after(0, lambda: self.show_notification(
                    f"Compound error: {error_msg}", error=True))

        threading.Thread(target=_do_compound, daemon=True).start()

'''

content = content.replace(
    "    def _lp_collect_fees_dialog(self, position):",
    dialog_method + "    def _lp_collect_fees_dialog(self, position):",
)

with open(path, "w", encoding="utf-8") as f:
    f.write(content)

print("Done - compound fees GUI added to gui_main_v5.py")