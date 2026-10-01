"""ColdStack LP operation runner: executes collect/compound primitives.

v5.3.30: this module owns the actual dispatch to writer primitives and the post-
tx recording/refresh steps.  The close executor lives in `lp_operation_close.py`
to keep every new file under ~300 lines.
"""
import threading
from tkinter import messagebox
from typing import Callable

from lp_engine import LPPosition
from lp_operation_close import LPCloseExecutor
from lp_operation_core import LPOpCore, OpVenueInfo
from venue_adapters.venue_writer import CollectFeesParams, CompoundFeesParams


class LPOperationRunner(LPOpCore):
    """Runs Collect/Compound through the writer primitives; delegates Close."""

    def __init__(self, lp_tab):
        super().__init__(lp_tab)
        self._close_executor = LPCloseExecutor(lp_tab)

    # ------------------------------------------------------------------
    # Public runners
    # ------------------------------------------------------------------

    def run_collect(self, position: LPPosition) -> None:
        self._run_operation(
            position,
            operation="collect",
            title="Confirm: Collect Fees",
            body_template=(
                "You are about to collect fees for position:\n"
                "  {pair} ({position_id})\n\n"
                "This will spend gas on {chain_name}.\n"
                "Ensure your wallet has {gas_token} for gas.\n"
                "The key_manager_agent must be running and unlocked.\n\n"
                "Continue?"
            ),
            notify_start="Collecting fees...",
            executor=self._execute_collect,
            refresh_after=5000,
        )

    def run_compound(self, position: LPPosition) -> None:
        self._run_operation(
            position,
            operation="compound",
            title="Confirm: Compound Fees",
            body_template=(
                "You are about to compound fees for position:\n"
                "  {pair} ({position_id})\n\n"
                "This will submit multiple transactions on {chain_name}:\n"
                "  1. Collect accrued fees\n"
                "  2. Swap to optimal ratio (if needed)\n"
                "  3. Increase liquidity with collected amounts\n\n"
                "Ensure your wallet has {gas_token} for gas.\n"
                "The key_manager_agent must be running and unlocked.\n\n"
                "Continue?"
            ),
            notify_start="Compounding fees... (multi-TX operation)",
            executor=self._execute_compound,
            refresh_after=8000,
        )

    def run_close(self, position: LPPosition) -> None:
        info = self._resolve_info(position)
        if info.writer is None:
            self._notify("Writer not available", error=True)
            return

        is_solana = position.position_id.startswith("solana:")
        is_sui = position.position_id.startswith("sui:")
        if is_solana:
            body = (
                "You are about to CLOSE this position completely:\n"
                f"  {position.pair} ({position.position_id})\n\n"
                "This will:\n"
                "  1. Withdraw ALL liquidity from the position\n"
                "  2. Collect any remaining fees\n"
                "  3. Burn the position NFT, recovering rent\n\n"
                f"This will spend gas on {info.chain_name}.\n"
                f"Ensure your wallet has {info.gas_token} for gas.\n"
                "The key_manager_agent must be running and unlocked.\n\n"
                "Continue?"
            )
            notify_start = "Closing position on Solana... (multi-TX operation)"
        elif is_sui:
            body = (
                "You are about to CLOSE this position completely:\n"
                f"  {position.pair} ({position.position_id})\n\n"
                "This will:\n"
                "  1. Withdraw ALL liquidity from the position\n"
                "  2. Collect any remaining fees\n"
                "  3. Close the Cetus position object\n\n"
                f"This will spend gas on {info.chain_name}.\n"
                f"Ensure your wallet has {info.gas_token} for gas.\n"
                "The key_manager_agent must be running and unlocked.\n\n"
                "Continue?"
            )
            notify_start = "Closing position on Sui..."
        else:
            body = (
                "You are about to CLOSE this position completely:\n"
                f"  {position.pair} ({position.position_id})\n\n"
                "This will:\n"
                "  1. Withdraw ALL liquidity from the position\n"
                "  2. Collect any remaining fees\n\n"
                "Your position NFT will remain but with zero liquidity.\n"
                f"This will spend gas on {info.chain_name}.\n"
                f"Ensure your wallet has {info.gas_token} for gas.\n"
                "The key_manager_agent must be running and unlocked.\n\n"
                "Continue?"
            )
            notify_start = "Closing position... (multi-TX operation)"

        self._run_operation(
            position,
            operation="close",
            title="Confirm: Close Position",
            body_template=body,
            notify_start=notify_start,
            executor=self._close_executor.execute,
            refresh_after=5000,
        )

    # ------------------------------------------------------------------
    # Internal operation shell
    # ------------------------------------------------------------------

    def _run_operation(
        self,
        position: LPPosition,
        operation: str,
        title: str,
        body_template: str,
        notify_start: str,
        executor: Callable,
        refresh_after: int,
    ) -> None:
        if self.tab._lp_guard_staked_action(position, operation.title()):
            return

        caps = self.capabilities(position)
        if not caps.get(operation, False):
            self._notify(
                f"{operation.title()} is not supported for this position/venue.",
                error=True,
            )
            return

        info = self._resolve_info(position)
        if info.writer is None:
            self._notify("Writer not available", error=True)
            return

        wallet_address, account_name = self._resolve_account(position, info.venue_key)
        if not wallet_address or not account_name:
            return

        if info.venue_key in ("aerodrome", "hyperliquid", "bsc"):
            try:
                account_name = self._evm_ownership_check(
                    position, account_name, info.venue_key, operation
                )
            except RuntimeError as e:
                self._notify(str(e), error=True)
                return

        body = body_template.format(
            pair=position.pair,
            position_id=position.position_id,
            chain_name=info.chain_name,
            gas_token=info.gas_token,
        )
        if not messagebox.askyesno(title, body):
            return
        self._notify(notify_start)

        def _thread() -> None:
            try:
                executor(position, info, wallet_address, account_name)
            except Exception as e:
                error_msg = self._humanize_error(str(e), info.gas_token)
                print(f"[{operation}] error: {error_msg}")
                self._notify(f"{operation.title()} error: {error_msg}", error=True)
                self._schedule(refresh_after, lambda: self._refresh_fees(position, wallet_address))

        threading.Thread(target=_thread, daemon=True).start()

    # ------------------------------------------------------------------
    # Executors
    # ------------------------------------------------------------------

    def _execute_collect(
        self, position: LPPosition, info: OpVenueInfo,
        wallet_address: str, account_name: str
    ) -> None:
        writer = info.writer
        assert writer is not None
        self._check_writer_available(writer)

        tx_hash = writer.collect_fees(CollectFeesParams(
            account=account_name,
            position_id=position.position_id,
            recipient=wallet_address,
        ))
        if tx_hash:
            self._notify(f"Fees collected. TX: {tx_hash[:20]}...")
            self.tab._lp_record_fee_collection(
                position.position_id, wallet_address, [tx_hash]
            )
            self._record_collect(position, info, tx_hash)
            self._schedule(5000, lambda: self._refresh_fees(position, wallet_address))
        else:
            self._notify("Collect failed: no tx hash returned", error=True)

    def _execute_compound(
        self, position: LPPosition, info: OpVenueInfo,
        wallet_address: str, account_name: str
    ) -> None:
        writer = info.writer
        assert writer is not None
        self._check_writer_available(writer)
        self._attach_price_engine(writer)

        tx_hashes = writer.compound_fees(CompoundFeesParams(
            account=account_name,
            position_id=position.position_id,
        ))
        if tx_hashes:
            note = self.tab._lp_record_compound_for_writer(writer, venue=info.venue_key)
            self._notify(
                f"Compound fees done. {len(tx_hashes)} TXs submitted. "
                f"First: {tx_hashes[0][:20]}... {note}"
            )
            self.tab._lp_record_fee_collection(
                position.position_id, wallet_address, tx_hashes
            )
            self._schedule(8000, lambda: self._refresh_fees(position, wallet_address))
        else:
            self._notify("Compound fees: no transactions submitted", error=True)
