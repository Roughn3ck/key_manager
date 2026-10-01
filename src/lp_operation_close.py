"""ColdStack LP operation close executor.

v5.3.30: this module owns the close-position primitive dispatch and post-close
verification/recording.  It is split out of the main runner to keep every new
file under ~300 lines.
"""
import time
from typing import List

from lp_engine import LPPosition
from lp_operation_core import LPOpCore, OpVenueInfo


class LPCloseExecutor(LPOpCore):
    """Execute the close-position primitive and record the result."""

    def execute(
        self,
        position: LPPosition,
        info: OpVenueInfo,
        wallet_address: str,
        account_name: str,
    ) -> None:
        """Run writer.close_position() and record/verify the outcome."""
        writer = info.writer
        assert writer is not None

        if position.position_id.startswith("solana:"):
            self._execute_close_solana(position, info, account_name)
            return
        if position.position_id.startswith("sui:"):
            self._execute_close_sui(position, info, account_name)
            return

        self._check_writer_available(writer)
        self._attach_price_engine(writer)

        writer_chain = getattr(writer, "chain_id", None)
        expected_chain_id = {
            "aerodrome": 8453,
            "hyperliquid": 999,
            "bsc": 56,
        }.get(info.venue_key)
        if expected_chain_id is not None and writer_chain is not None \
                and writer_chain != expected_chain_id:
            self._notify(
                f"Chain mismatch: resolved {info.venue_key} writer serves chain "
                f"{writer_chain}, expected {expected_chain_id}. Refetch this position.",
                error=True,
            )
            return

        tx_hashes = writer.close_position(position.position_id, account_name)
        if not tx_hashes:
            self._notify("Close position: no transactions submitted", error=True)
            return

        self._notify(
            f"Closing position… waiting for on-chain confirmation ({len(tx_hashes)} TX)"
        )

        receipt_errs: List[str] = []
        for tx_hash in tx_hashes:
            try:
                receipt = writer._wait_for_tx_receipt(
                    tx_hash, timeout=120, poll_interval=2.0
                )
                if receipt is None:
                    receipt_errs.append(tx_hash)
            except RuntimeError as e:
                receipt_errs.append(f"{tx_hash} reverted: {e}")
                break

        if receipt_errs:
            for err in receipt_errs:
                print(f"[close_position] receipt problem: {err}")
            unconfirmed = [e for e in receipt_errs if "reverted" not in e]
            reverted = [e for e in receipt_errs if "reverted" in e]
            if reverted:
                self._notify(
                    f"Close TX reverted on-chain — position NOT closed. "
                    f"TX: {reverted[0].split()[0]}…",
                    error=True,
                )
            else:
                self._notify(
                    f"Close TX unconfirmed after 120s — check the explorer. "
                    f"TX: {unconfirmed[0][:20]}…",
                    error=True,
                )
            return

        token_id = self._numeric_id(position.position_id)
        liquidity = 0
        if token_id is not None:
            try:
                liquidity = writer._get_position_liquidity(token_id)
            except Exception as e:
                print(f"[close_position] post-close liquidity read failed: {e}")
                liquidity = 0
        if liquidity > 0:
            self._notify("Liquidity still on-chain — retry Close or Collect Fees", error=True)
            return

        vkey = (info.venue_key or "").lower()
        is_aero = vkey in ("aerodrome", "aerodrome/base") or "aerodrome" in vkey
        is_cetus = vkey == "cetus"
        if is_aero or is_cetus:
            note = self.tab._lp_record_close_for_writer(writer, venue=info.venue_key)
            burn_note = ""
            if getattr(writer, "last_burn_sig", None):
                burn_note = f" · NFT burned ({writer.last_burn_sig[:12]}...)"
            self.tab._lp_forget_position(
                position, notify=f"Position closed ✓ {note}{burn_note}"
            )
        else:
            print(
                f"[close_position] ledger recording not implemented for venue "
                f"'{info.venue_key}' yet — close confirmed on-chain; record via Kimi's tool."
            )
            self._notify(
                "Close confirmed on-chain — ledger recording pending for this venue.",
                error=True,
            )

    def _execute_close_solana(
        self, position: LPPosition, info: OpVenueInfo, account_name: str
    ) -> None:
        from venue_adapters.orca_adapter import _get_account_data, _derive_position_address

        writer = info.writer
        assert writer is not None
        self._check_writer_available(writer)
        self._attach_price_engine(writer)

        tx_hashes = writer.close_position(position.position_id, account_name)
        if not tx_hashes:
            self._notify("Close position: no transactions submitted", error=True)
            return

        mint = position.position_id.split(":", 1)[1] if ":" in position.position_id else position.position_id
        self._notify(
            f"Close TXs submitted ({len(tx_hashes)}). Waiting for on-chain confirmation..."
        )

        confirmed = False
        verifiable = False
        deadline = time.time() + 10
        pos_addr = _derive_position_address(mint)
        if pos_addr:
            verifiable = True
            while time.time() < deadline:
                if _get_account_data(pos_addr) is None:
                    confirmed = True
                    break
                time.sleep(2)

        if confirmed:
            note = self.tab._lp_record_orca_close(writer, position, account_name)
            sigs_note = " · ".join(f"{s[:12]}..." for s in tx_hashes if s)
            notify = f"Position closed ✓ {note}"
            if sigs_note:
                notify += f"  ·  TXs: {sigs_note}"
            self.tab._lp_forget_position(position, notify=notify)
        elif verifiable:
            self._notify(
                f"Close TXs submitted but position still on-chain — verify. "
                f"({len(tx_hashes)} TXs)",
                error=True,
            )
        else:
            self._notify(
                f"Close TXs submitted ({len(tx_hashes)}) but closure could "
                f"not be verified on-chain — check the position on Orca.",
                error=True,
            )

    def _execute_close_sui(
        self, position: LPPosition, info: OpVenueInfo, account_name: str
    ) -> None:
        writer = info.writer
        assert writer is not None
        self._check_writer_available(writer)
        self._attach_price_engine(writer)

        tx_hashes = writer.close_position(position.position_id, account_name)
        if tx_hashes:
            note = self.tab._lp_record_close_for_writer(writer, venue=info.venue_key)
            sigs_note = " · ".join(f"{s[:12]}..." for s in tx_hashes if s)
            notify = f"Position closed ✓ {note}"
            if sigs_note:
                notify += f"  ·  TXs: {sigs_note}"
            self.tab._lp_forget_position(position, notify=notify)
        else:
            self._notify("Close position: no transactions submitted", error=True)
