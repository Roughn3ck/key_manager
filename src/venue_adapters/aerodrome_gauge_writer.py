"""Aerodrome SlipStream CLGauge writer — ColdStack v5.3.28.

Write operations for staked Aerodrome SlipStream V3 positions:
  - claim AERO emissions  (gauge.claimEmissions(account, recipient, [tokenId]))
  - unstake               (gauge.withdraw(tokenId))
  - guided close staked   (claim → unstake → existing close flow)

All signing is delegated to the key_manager_agent; this module never touches
private keys. Selectors are pinned from the public SlipStream source:
  - claimEmissions(address,address,uint256[]) -> 0xc04dbe2d
  - withdraw(uint256)                         -> 0x28c55f69

The claim row is written to coldtrack.db via the close-recorder path as a
FEE_EVENTS SOURCE='HARVEST' row (per the existing close-recorder contract).
"""
import json
import time
import urllib.request
import urllib.error
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from price_engine import PriceEngine
from venue_adapters.aerodrome_adapter import (
    AERO_TOKEN,
    SELECTOR_CLAIM_EMISSIONS,
    SELECTOR_GAUGE_WITHDRAW,
    SELECTOR_OWNER_OF,
    V3_POSITION_MANAGERS,
    _base_rpc_call,
    _decode_address,
    _get_gauge_address_for_position,
    _pad_address,
    _pad_int_to_64,
)
from venue_adapters.aerodrome_writer import AerodromeWriter
from venue_adapters.venue_writer import CollectFeesParams


AERO_DECIMALS = 18


@dataclass
class GaugeStepResult:
    """Result of one guided-sequence step."""
    step: str                       # 'claim' | 'unstake' | 'close'
    tx_hash: Optional[str] = None
    receipt: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    skipped: bool = False


@dataclass
class GaugeClaimRecord:
    """Minimal capture for a claim tx to be recorded as a FEE_EVENTS row."""
    position_id_str: str
    account_id: int
    position_db_id: int
    date_iso: str
    aero_amount: float
    value_usd: Optional[float] = None
    tx_hash: Optional[str] = None
    notes: str = "AERO emissions claim — gauge"


@dataclass
class GuidedCloseResult:
    """Complete result from the claim → unstake → close sequence."""
    steps: List[GaugeStepResult] = field(default_factory=list)
    close_tx_hashes: List[str] = field(default_factory=list)
    claim_record: Optional[GaugeClaimRecord] = None
    error: Optional[str] = None


class AerodromeGaugeWriter:
    """Staked-Aerodrome operations: claim, unstake, guided close.

    Reuses AerodromeWriter for the final close (decrease + collect + burn).
    """

    def __init__(self, agent_url: str = "http://127.0.0.1:8842", price_engine: Optional[PriceEngine] = None):
        self.agent_url = agent_url.rstrip("/")
        self.base_writer = AerodromeWriter(agent_url=agent_url)
        self.price_engine = price_engine

    # ------------------------------------------------------------------
    # Calldata builders (pinned selectors)
    # ------------------------------------------------------------------

    @staticmethod
    def _claim_emissions_calldata(account: str, recipient: str, token_id: int) -> str:
        """Encode claimEmissions(address account, address recipient, uint256[] tokenIds)."""
        return (
            SELECTOR_CLAIM_EMISSIONS
            + _pad_address(account)
            + _pad_address(recipient)
            + _pad_int_to_64(0x60)  # array offset
            + _pad_int_to_64(1)     # length
            + _pad_int_to_64(token_id)
        )

    @staticmethod
    def _withdraw_calldata(token_id: int) -> str:
        """Encode withdraw(uint256 tokenId)."""
        return SELECTOR_GAUGE_WITHDRAW + _pad_int_to_64(token_id)

    # ------------------------------------------------------------------
    # RPC helpers
    # ------------------------------------------------------------------

    def _rpc_call(self, method: str, params: list) -> Optional[Any]:
        return _base_rpc_call(method, params)

    def _owner_of(self, token_id: int, position_manager: str) -> Optional[str]:
        data = SELECTOR_OWNER_OF + _pad_int_to_64(token_id)
        result = self._rpc_call("eth_call", [{"to": position_manager, "data": data}, "latest"])
        if result and isinstance(result, str) and len(result) >= 66:
            return _decode_address(result[2:66])
        return None

    def _find_position_manager(self, token_id: int) -> str:
        """Find the Position Manager that owns this token ID."""
        for pm in V3_POSITION_MANAGERS:
            data = "0x99fbab88" + _pad_int_to_64(token_id)  # positions(uint256)
            result = self._rpc_call("eth_call", [{"to": pm, "data": data}, "latest"])
            if result and isinstance(result, str) and len(result) >= 2 + 32 * 13:
                body = result[2:]
                nonce = int(body[0:64], 16)
                liquidity = int(body[448:512], 16)
                if nonce > 0 or liquidity > 0:
                    return pm
        return V3_POSITION_MANAGERS[0]

    def _preflight_eth_call(self, account: str, to: str, data: str) -> Optional[str]:
        """Simulate the exact calldata from the signer. Returns None on success,
        or the decoded revert reason string on failure."""
        from_address = self.base_writer._get_account_address(account)
        if not from_address:
            return "Could not resolve signer address"
        try:
            result = self._rpc_call(
                "eth_call",
                [{"from": from_address, "to": to, "data": data}, "latest"],
            )
            # A successful eth_call returns hex data; any revert raises in the RPC layer.
            if result is None:
                return "eth_call returned no data (reverted)"
            return None
        except Exception as e:
            msg = str(e)
            # Extract a revert reason if present.
            if "execution reverted" in msg:
                parts = msg.split("execution reverted")
                if len(parts) > 1:
                    reason = parts[-1].strip(" :")
                    if reason:
                        return reason
            if "revert" in msg.lower():
                return msg
            return f"Pre-flight failed: {msg}"

    def _broadcast(self, account: str, to: str, data: str, gas_check_address: str = "") -> str:
        return self.base_writer._broadcast(account, to, data, gas_check_address=gas_check_address)

    def _wait_receipt(self, tx_hash: str) -> Optional[Dict[str, Any]]:
        return self.base_writer._wait_for_tx_receipt(tx_hash)

    def _is_staked(self, token_id: int, position_manager: str, wallet_address: str) -> Optional[str]:
        """Return the gauge address if the position is staked, else None."""
        return _get_gauge_address_for_position(token_id, position_manager, wallet_address)

    def _aero_price_usd(self) -> Optional[float]:
        if self.price_engine is None:
            return None
        try:
            return self.price_engine.get_price("aerodrome", "usd")
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Individual operations
    # ------------------------------------------------------------------

    def claim_emissions(self, token_id: int, account: str, recipient: Optional[str] = None,
                        position_manager: Optional[str] = None) -> GaugeStepResult:
        """Claim AERO emissions for a staked position.

        Idempotent: if the position is not currently staked, the claim is skipped
        (the emissions were already claimed on unstake).
        """
        if position_manager is None:
            position_manager = self._find_position_manager(token_id)
        wallet_address = self.base_writer._get_account_address(account)
        gauge_address = self._is_staked(token_id, position_manager, wallet_address)
        if not gauge_address:
            return GaugeStepResult(step="claim", skipped=True,
                                   error="Position is not staked — nothing to claim")
        if recipient is None:
            recipient = wallet_address

        data = self._claim_emissions_calldata(account, recipient, token_id)
        reason = self._preflight_eth_call(account, gauge_address, data)
        if reason:
            return GaugeStepResult(step="claim", error=f"Pre-flight revert: {reason}")

        try:
            tx_hash = self._broadcast(account, gauge_address, data,
                                      gas_check_address=wallet_address)
        except Exception as e:
            return GaugeStepResult(step="claim", error=f"Broadcast failed: {e}")

        receipt = self._wait_receipt(tx_hash)
        if receipt is None:
            return GaugeStepResult(step="claim", tx_hash=tx_hash,
                                   error="Transaction not mined within timeout")
        if receipt.get("status") != "0x1":
            return GaugeStepResult(step="claim", tx_hash=tx_hash, receipt=receipt,
                                   error="Transaction failed on-chain")
        return GaugeStepResult(step="claim", tx_hash=tx_hash, receipt=receipt)

    def unstake(self, token_id: int, account: str,
                position_manager: Optional[str] = None) -> GaugeStepResult:
        """Unstake a CL position from its gauge.

        Idempotent: if ownerOf(tokenId) is already the wallet, we report skipped.
        """
        if position_manager is None:
            position_manager = self._find_position_manager(token_id)
        wallet_address = self.base_writer._get_account_address(account)

        current_owner = self._owner_of(token_id, position_manager)
        if current_owner and current_owner.lower() == wallet_address.lower():
            return GaugeStepResult(step="unstake", skipped=True,
                                   error="Position already unstaked")

        gauge_address = self._is_staked(token_id, position_manager, wallet_address)
        if not gauge_address:
            if current_owner:
                return GaugeStepResult(step="unstake", skipped=True,
                                       error="Position is not held by a gauge")
            return GaugeStepResult(step="unstake", error="Could not locate gauge for position")

        data = self._withdraw_calldata(token_id)
        reason = self._preflight_eth_call(account, gauge_address, data)
        if reason:
            return GaugeStepResult(step="unstake", error=f"Pre-flight revert: {reason}")

        try:
            tx_hash = self._broadcast(account, gauge_address, data,
                                      gas_check_address=wallet_address)
        except Exception as e:
            return GaugeStepResult(step="unstake", error=f"Broadcast failed: {e}")

        receipt = self._wait_receipt(tx_hash)
        if receipt is None:
            return GaugeStepResult(step="unstake", tx_hash=tx_hash,
                                   error="Transaction not mined within timeout")
        if receipt.get("status") != "0x1":
            return GaugeStepResult(step="unstake", tx_hash=tx_hash, receipt=receipt,
                                   error="Transaction failed on-chain")

        # Confirm NFT returned to the wallet before reporting success.
        post_owner = self._owner_of(token_id, position_manager)
        if not post_owner or post_owner.lower() != wallet_address.lower():
            return GaugeStepResult(step="unstake", tx_hash=tx_hash, receipt=receipt,
                                   error="Unstake tx succeeded but NFT owner did not change")
        return GaugeStepResult(step="unstake", tx_hash=tx_hash, receipt=receipt)

    def _record_claim(self, record: GaugeClaimRecord, db_path: Any,
                      base_dir: Optional[Path] = None) -> Dict[str, Any]:
        """Write a FEE_EVENTS row for a gauge claim."""
        from coldtrack.db import ColdTrackDB
        from coldtrack.close_recorder import record_close_and_export

        db = ColdTrackDB(Path(db_path))
        db.init_schema()
        try:
            # Dedupe by TX_HASH so a retry never double-records.
            cur = db.conn().cursor()
            dup = cur.execute(
                "SELECT 1 FROM FEE_EVENTS WHERE TX_HASH = ? AND POSITION_ID = ?",
                (record.tx_hash, record.position_db_id),
            ).fetchone()
            if dup:
                return {"ok": True, "note": "already recorded", "fee_events": 0}

            # Best-effort CAD conversion via FX_RATES (same contract as close recorder).
            value_cad = None
            if record.value_usd:
                date_key = record.date_iso[:10]
                cadusd = cur.execute(
                    "SELECT RATE FROM FX_RATES WHERE DATE = ? AND PAIR = ?",
                    (date_key, "CADUSD"),
                ).fetchone()
                if not cadusd:
                    cadusd = cur.execute(
                        "SELECT RATE FROM FX_RATES WHERE PAIR = ? ORDER BY DATE DESC LIMIT 1",
                        ("CADUSD",),
                    ).fetchone()
                if cadusd and cadusd["RATE"]:
                    value_cad = round(record.value_usd * cadusd["RATE"], 6)

            cur.execute(
                """INSERT INTO FEE_EVENTS
                   (POSITION_ID, DATE, TOKEN_A_AMT, TOKEN_B_AMT, VALUE_USD,
                    VALUE_CAD, VALUE_EUR, VALUE_AUD, TX_HASH, SOURCE, NOTES)
                   VALUES (?,?,?,?,?,?,NULL,NULL,?,?,?)""",
                (record.position_db_id, record.date_iso, record.aero_amount, None,
                 record.value_usd, value_cad, record.tx_hash, "HARVEST",
                 record.notes),
            )
            db.commit()
            return {"ok": True, "fee_events": 1}
        except Exception as e:
            db.rollback()
            # Persist to pending so the record is not lost.
            from coldtrack.close_recorder import persist_pending
            pending = {
                "reason": str(e),
                "claim_record": {
                    "position_id_str": record.position_id_str,
                    "position_db_id": record.position_db_id,
                    "aero_amount": record.aero_amount,
                    "value_usd": record.value_usd,
                    "tx_hash": record.tx_hash,
                    "notes": record.notes,
                },
            }
            p = Path(pending_dir(base_dir)) / f"pending_claim_{record.position_id_str}_{_ts()}.json"
            p.write_text(json.dumps(pending, indent=2), encoding="utf-8")
            return {"error": str(e), "pending": str(p)}
        finally:
            db.close()

    def claim_and_record(self, token_id: int, account: str, position_db_id: int,
                         db_path: Any, position_manager: Optional[str] = None,
                         base_dir: Optional[Path] = None) -> GaugeStepResult:
        """Claim emissions and write a FEE_EVENTS row for the claim."""
        result = self.claim_emissions(token_id, account, position_manager=position_manager)
        if result.error or result.skipped or not result.tx_hash:
            return result

        # Compute AERO amount from the receipt: look for a Transfer event from the
        # LeafVoter minting AERO to the recipient. The exact topic depends on the
        # AERO token contract; we approximate by reading the recipient's AERO
        # balance delta across the claim tx.
        wallet_address = self.base_writer._get_account_address(account)
        pre_bal = self._read_erc20_balance(AERO_TOKEN, wallet_address)
        receipt = result.receipt or {}
        post_bal = self._read_erc20_balance(AERO_TOKEN, wallet_address)
        aero_amount = max(0, (post_bal - pre_bal)) / (10 ** AERO_DECIMALS)

        value_usd = None
        if aero_amount > 0:
            price = self._aero_price_usd()
            if price:
                value_usd = round(aero_amount * price, 6)

        record = GaugeClaimRecord(
            position_id_str=str(token_id),
            account_id=0,  # not used for FEE_EVENTS insert
            position_db_id=position_db_id,
            date_iso=datetime.now(timezone.utc).isoformat(),
            aero_amount=aero_amount,
            value_usd=value_usd,
            tx_hash=result.tx_hash,
        )
        self._record_claim(record, db_path, base_dir=base_dir)
        return result

    def _read_erc20_balance(self, token: str, wallet: str) -> int:
        data = "0x70a08231" + _pad_address(wallet)
        result = self._rpc_call("eth_call", [{"to": token, "data": data}, "latest"])
        if result and isinstance(result, str) and len(result) >= 66:
            return int(result[2:66], 16)
        return 0

    # ------------------------------------------------------------------
    # Guided close sequence
    # ------------------------------------------------------------------

    def close_staked_position(self, position_id: str, account: str, db_path: Any,
                              position_manager: Optional[str] = None,
                              base_dir: Optional[Path] = None,
                              progress_callback=None) -> GuidedCloseResult:
        """Claim → unstake → close a staked Aerodrome position.

        Each step is gated on the previous step's confirmed state. A mid-sequence
        failure leaves the position in its actual on-chain state and returns a
        clear resume path.
        """
        result = GuidedCloseResult()
        token_id = int(position_id.split(":")[-1]) if ":" in position_id else int(position_id)
        if position_manager is None:
            position_manager = self._find_position_manager(token_id)
        wallet_address = self.base_writer._get_account_address(account)

        def _notify(msg: str):
            if progress_callback:
                try:
                    progress_callback(msg)
                except Exception:
                    pass
            print(f"[aero-gauge] {msg}")

        # Step 1: claim emissions while still staked.
        _notify("Step 1/3: claiming AERO emissions...")
        claim = self.claim_emissions(token_id, account, position_manager=position_manager)
        result.steps.append(claim)
        if claim.error:
            result.error = f"Claim failed: {claim.error}"
            return result
        if claim.tx_hash:
            _notify(f"Claim tx: {claim.tx_hash}")

        # Step 2: unstake from gauge.
        _notify("Step 2/3: unstaking from gauge...")
        unstake = self.unstake(token_id, account, position_manager=position_manager)
        result.steps.append(unstake)
        if unstake.error and not unstake.skipped:
            result.error = f"Unstake failed: {unstake.error}"
            return result
        if unstake.tx_hash:
            _notify(f"Unstake tx: {unstake.tx_hash}")

        # Step 3: run the normal close flow (decrease + collect + burn).
        _notify("Step 3/3: closing position...")
        try:
            close_txs = self.base_writer.close_position(
                f"base:{token_id}", account
            )
            result.close_tx_hashes = close_txs
            _notify(f"Close tx(s): {close_txs}")
        except Exception as e:
            result.error = f"Close failed: {e}"
            return result

        # Record the claim if it produced a tx. The close itself is recorded by
        # AerodromeWriter._post_close_state / record_close_and_export in lp_tab.
        if claim.tx_hash and db_path:
            # Best-effort ledger row for the AERO emissions claim. We do not have
            # the portfolio LP_POSITIONS.ID here, so we look it up by TOKEN_ID.
            from coldtrack.db import ColdTrackDB
            try:
                db = ColdTrackDB(Path(db_path))
                db.init_schema()
                row = db.conn().execute(
                    "SELECT ID FROM LP_POSITIONS WHERE TOKEN_ID = ? AND STATUS = 'active'",
                    (str(token_id),),
                ).fetchone()
                if row:
                    self.claim_and_record(
                        token_id, account, row["ID"], db_path,
                        position_manager=position_manager,
                        base_dir=base_dir,
                    )
            except Exception as e:
                print(f"[aero-gauge] claim ledger recording skipped: {e}")
            finally:
                try:
                    db.close()
                except Exception:
                    pass

        return result


# ---------------------------------------------------------------------------
# Helpers mirrored from close_recorder to keep this module standalone
# ---------------------------------------------------------------------------

def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def pending_dir(base_dir: Optional[Path] = None) -> Path:
    """Pending-record folder, co-located with the vault by default."""
    if base_dir is None:
        import sys
        if getattr(sys, "frozen", False):
            base_dir = Path(sys.executable).parent
        else:
            base_dir = Path(__file__).parent.parent.parent
    d = Path(base_dir) / "coldstack_pending_records"
    d.mkdir(parents=True, exist_ok=True)
    return d
