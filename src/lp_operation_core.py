"""ColdStack LP operation shared core: capabilities, venue resolution, helpers.

v5.3.30: this module holds the capability model and common utilities used by
both the GUI controller and the operation runners.  It is intentionally GUI-
agnostic except for notification scheduling.
"""
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

from lp_engine import LPPosition
from venue_adapters.venue_writer import VenueWriter


@dataclass(frozen=True)
class OpVenueInfo:
    """Resolved venue metadata for an operation."""
    chain_name: str
    gas_token: str
    venue_key: str
    writer: Optional[VenueWriter]


class LPOpCore:
    """Shared, stateless-ish helpers for LP operation orchestration."""

    def __init__(self, lp_tab):
        self.tab = lp_tab
        self.gui = lp_tab.gui

    # ------------------------------------------------------------------
    # Capability model
    # ------------------------------------------------------------------

    def capabilities(self, position: LPPosition) -> Dict[str, bool]:
        """Return honest operation capability flags for a position.

        Rules:
          - Staked Aerodrome positions: no normal Collect/Compound/Close.
          - BSC: no compound (swap not implemented yet).
          - Compound in general requires same-position liquidity-add support.
          - If we cannot resolve the venue or writer, every operation is disabled.
        """
        defaults = {"collect": False, "compound": False, "close": False}
        if self._is_staked_aerodrome(position):
            return defaults

        try:
            info = self._resolve_info(position, require_available=False)
        except RuntimeError:
            return defaults
        if info.writer is None:
            return defaults

        caps = {
            "collect": getattr(info.writer, "supports_collect", True),
            "compound": getattr(info.writer, "supports_compound", True),
            "close": getattr(info.writer, "supports_close", True),
        }
        if info.venue_key == "bsc":
            caps["compound"] = False
        return caps

    def can_collect(self, position: LPPosition) -> bool:
        return self.capabilities(position)["collect"]

    def can_compound(self, position: LPPosition) -> bool:
        return self.capabilities(position)["compound"]

    def can_close(self, position: LPPosition) -> bool:
        return self.capabilities(position)["close"]

    # ------------------------------------------------------------------
    # Resolution helpers
    # ------------------------------------------------------------------

    def _resolve_info(self, position: LPPosition,
                      require_available: bool = True) -> OpVenueInfo:
        chain_name, gas_token, venue_key = self.tab._lp_resolve_venue_for_position(position)
        writer = None
        if require_available:
            try:
                writer = self.gui.lp_engine.get_writer(venue_key, self.gui.current_password)
            except Exception:
                writer = None
        else:
            writer = self.gui.lp_engine._writers.get(venue_key)
        return OpVenueInfo(chain_name, gas_token, venue_key, writer)

    def _resolve_account(self, position: LPPosition, venue_key: str) -> Tuple[str, str]:
        if position.position_id.startswith("solana:"):
            return self._resolve_solana_account(position)
        if position.position_id.startswith("sui:"):
            return self._resolve_sui_account(position)

        wallet_address, account_name = self.tab._lp_resolve_wallet_for_position(position)
        if not wallet_address:
            self._notify("Could not resolve wallet address for this position", error=True)
            return "", ""
        if not account_name:
            self._notify("Could not resolve vault account for this address", error=True)
            return "", ""
        return wallet_address, account_name

    def _resolve_solana_account(self, position: LPPosition) -> Tuple[str, str]:
        account_name = self.tab._lp_resolve_solana_account_for_position(position)
        if not account_name:
            self._notify(
                "Could not resolve the vault account that owns this Solana position NFT. "
                "Ensure the wallet that owns this position is in your vault.",
                error=True,
            )
            return "", ""
        writer = self.gui.lp_engine.get_writer("orca", self.gui.current_password)
        try:
            wallet_address = writer._get_solana_address(account_name) if writer else ""
        except Exception:
            wallet_address = ""
        if not wallet_address:
            self._notify("Could not resolve Solana address for this account", error=True)
            return "", ""
        return wallet_address, account_name

    def _resolve_sui_account(self, position: LPPosition) -> Tuple[str, str]:
        account_name = self.tab._lp_resolve_solana_account_for_position(position)
        if not account_name:
            self._notify(
                "Could not resolve the vault account for this Sui position. "
                "Ensure the wallet that owns this position is in your vault.",
                error=True,
            )
            return "", ""
        writer = self.gui.lp_engine.get_writer("cetus", self.gui.current_password)
        try:
            wallet_address = writer._get_sui_address(account_name) if writer else ""
        except Exception:
            wallet_address = ""
        if not wallet_address:
            self._notify("Could not resolve Sui address for this account", error=True)
            return "", ""
        return wallet_address, account_name

    # ------------------------------------------------------------------
    # Writer / notification helpers
    # ------------------------------------------------------------------

    def _check_writer_available(self, writer: VenueWriter) -> None:
        if not writer.is_available():
            raise RuntimeError("Agent not running. Start key_manager_agent with --serve.")

    def _attach_price_engine(self, writer: VenueWriter) -> None:
        try:
            writer.price_engine = self.gui.price_engine
        except Exception:
            pass

    def _notify(self, message: str, error: bool = False) -> None:
        try:
            self.gui.show_notification(message, error=error)
        except Exception:
            print(f"[lp_operations] {'ERROR' if error else 'INFO'}: {message}")

    def _schedule(self, delay_ms: int, callback) -> None:
        try:
            self.gui.root.after(delay_ms, callback)
        except Exception:
            import time
            time.sleep(delay_ms / 1000.0)
            callback()

    def _refresh_fees(self, position: LPPosition, wallet_address: str) -> None:
        self.tab._lp_refresh_position_fees(position.position_id, wallet_address)

    def _numeric_id(self, position_id: str) -> Optional[int]:
        try:
            return int(position_id.split(":", 1)[1])
        except (ValueError, IndexError):
            return None

    def _is_staked_aerodrome(self, position: LPPosition) -> bool:
        raw = getattr(position, "raw_data", None) or {}
        if not raw.get("is_staked"):
            return False
        pid = getattr(position, "position_id", "") or ""
        return pid.startswith("base:")

    def _humanize_error(self, error_msg: str, gas_token: str) -> str:
        low = error_msg.lower()
        if "insufficient funds" in low or "insufficient gas" in low:
            return (
                f"Wallet has no {gas_token} for gas. Send {gas_token} to your wallet address "
                f"to pay for transactions. (Details: {error_msg})"
            )
        if "nonce too high" in low:
            return (
                "Transaction rejected (nonce conflict). Wait a moment and try again. "
                f"(Details: {error_msg})"
            )
        if "overflowerror" in low or "result too large" in low:
            return (
                "Internal error during transaction signing. The transaction may have "
                "succeeded on-chain — check Project X to verify. Try again if needed."
            )
        return error_msg

    def _record_collect(self, position: LPPosition, info: OpVenueInfo,
                        tx_hash: str) -> None:
        """Best-effort write a standalone collect to FEE_EVENTS (deduped by TX_HASH)."""
        from coldtrack.collect_recorder import CollectResult, record_collect_and_export
        db_path = self.tab._lp_portfolio_db_path()
        if db_path is None:
            return
        try:
            result = CollectResult(
                position_mint=position.position_id.split(":", 1)[1]
                if ":" in position.position_id else position.position_id,
                platform=position.venue or info.chain_name,
                chain=info.chain_name,
                tx_hash=tx_hash,
                token_a_symbol=position.token_0 or "",
                token_b_symbol=position.token_1 or "",
                value_usd=position.fees_earned_usd,
            )
            record_collect_and_export(result, db_path, base_dir=db_path.parent)
        except Exception as e:
            print(f"[collect] ledger recording skipped (non-fatal): {e}")

    def _evm_ownership_check(self, position: LPPosition, account_name: str,
                             venue_key: str, operation: str) -> str:
        """Run the EVM ownership pre-check and return the resolved account."""
        try:
            return self.tab._lp_verify_evm_position_ownership(
                position, account_name, venue_key
            )
        except RuntimeError:
            raise
        except Exception as e:
            error_msg = str(e)
            print(f"[{operation}] ownership check crashed: {error_msg}")
            raise RuntimeError(
                f"Ownership check failed for {position.position_id}: {error_msg}. "
                "Refetch this position and try again."
            ) from e
