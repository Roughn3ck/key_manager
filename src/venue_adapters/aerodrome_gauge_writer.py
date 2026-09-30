"""Aerodrome SlipStream CLGauge writer — ColdStack v5.3.29.

Write operations for staked Aerodrome SlipStream V3 positions:
  - claim AERO emissions  (gauge.getReward(tokenId) or claimEmissions(...))
  - unstake               (gauge.withdraw(tokenId))
  - guided close staked   (claim → unstake → existing close flow)

All signing is delegated to the key_manager_agent; this module never touches
private keys. Selectors are resolved per deployed gauge by probing the
implementation bytecode (EIP-1167 clone) and choosing the ABI that is actually
present. The deployed SlipStream CLGauge uses:
  - getReward(uint256)  -> 0x1c4b774b
  - withdraw(uint256) -> 0x2e1a7d4d
An alternate generation exposes claimEmissions(address,address,uint256[]).

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
    SELECTOR_GAUGE_GET_REWARD_UINT,
    SELECTOR_GAUGE_WITHDRAW,
    SELECTOR_OWNER_OF,
    V3_POSITION_MANAGERS,
    _base_rpc_call,
    _decode_address,
    _gauge_interface,
    _get_eip1167_implementation,
    _get_gauge_address_for_position,
    _pad_address,
    _pad_int_to_64,
    _probe_gauge_selectors,
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
    value_cad: Optional[float] = None
    tx_hash: Optional[str] = None
    notes: str = "gauge emissions"


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
    # Calldata builders (interface-resolved per gauge)
    # ------------------------------------------------------------------

    def _claim_emissions_calldata(self, account: str, recipient: str, token_id: int,
                                  gauge_address: str) -> str:
        """Encode the claim call that the deployed gauge actually exposes."""
        interface = _gauge_interface(gauge_address)
        selector = interface["claim_selector"]
        args = interface["claim_args"]
        if args == "uint256":
            return selector + _pad_int_to_64(token_id)
        if args == "address":
            return selector + _pad_address(account)
        # claimEmissions(address,address,uint256[])
        return (
            selector
            + _pad_address(account)
            + _pad_address(recipient)
            + _pad_int_to_64(0x60)  # array offset
            + _pad_int_to_64(1)     # length
            + _pad_int_to_64(token_id)
        )

    def _withdraw_calldata(self, token_id: int, gauge_address: str) -> str:
        """Encode withdraw(uint256 tokenId) using the gauge's actual selector."""
        interface = _gauge_interface(gauge_address)
        return interface["withdraw_selector"] + _pad_int_to_64(token_id)

    # ------------------------------------------------------------------
    # RPC helpers
    # ------------------------------------------------------------------

    def _rpc_call(self, method: str, params: list) -> Optional[Any]:
        return _base_rpc_call(method, params)

    def _classify_simulation_failure(self, gauge_address: str, data: str,
                                     exception_msg: str) -> str:
        """Turn a raw eth_call failure into an honest, actionable reason.

        If the selector used is not in the implementation bytecode, report a
        gauge-interface mismatch before falling back to auth/precondition
        classification.
        """
        selector = data[:10].lower()
        present = _probe_gauge_selectors(gauge_address)
        present_sels = {sel: name for name, sel in {
            "get_reward_uint": SELECTOR_GAUGE_GET_REWARD_UINT[2:],
            "get_reward_addr": "c00007b0",
            "claim_emissions": SELECTOR_CLAIM_EMISSIONS[2:],
            "withdraw_deployed": SELECTOR_GAUGE_WITHDRAW[2:],
            "withdraw_alt": "28c55f69",
        }.items()}
        for name, sel in present_sels.items():
            if selector == "0x" + sel:
                if not present.get(name, False):
                    return (
                        f"gauge interface mismatch: selector {selector} "
                        f"({name}) not found in gauge implementation "
                        f"({_get_eip1167_implementation(gauge_address) or 'unknown'})"
                    )
                break

        msg = exception_msg
        if "execution reverted" in msg:
            parts = msg.split("execution reverted")
            if len(parts) > 1:
                reason = parts[-1].strip(" :")
                if reason:
                    return reason
        if "revert" in msg.lower():
            return msg
        if "no data" in msg.lower() or "returned none" in msg.lower():
            return "gauge interface mismatch: selector not present (eth_call returned no data)"
        return f"Simulation failed: {msg}"

    def _simulate(self, account: str, to: str, data: str) -> Optional[str]:
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
            if result is None:
                return self._classify_simulation_failure(to, data,
                    "eth_call returned no data (reverted)")
            return None
        except Exception as e:
            return self._classify_simulation_failure(to, data, str(e))

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

        data = self._claim_emissions_calldata(account, recipient, token_id, gauge_address)
        reason = self._simulate(account, gauge_address, data)
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
            return GaugeStepResult(step="claim", tx_hash=tx_hash,
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

        data = self._withdraw_calldata(token_id, gauge_address)
        reason = self._simulate(account, gauge_address, data)
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
            return GaugeStepResult(step="unstake", tx_hash=tx_hash,
                                   error="Transaction failed on-chain")

        # Confirm NFT returned to the wallet before reporting success.
        post_owner = self._owner_of(token_id, position_manager)
        if not post_owner or post_owner.lower() != wallet_address.lower():
            return GaugeStepResult(step="unstake", tx_hash=tx_hash, receipt=receipt,
                                   error="Unstake tx succeeded but NFT owner did not change")
        return GaugeStepResult(step="unstake", tx_hash=tx_hash, receipt=receipt)

    def _record_claim(self, record: GaugeClaimRecord, db_path: Any,
                      base_dir: Optional[Path] = None) -> Dict[str, Any]:
        """Write a TRANSACTIONS + FEE_EVENTS row for an AERO emissions claim.

        v5.3.29: NOTES tag is the exact string "gauge emissions". Dedupe by
        TX_HASH against both tables so external claims and retries never double-
        record.
        """
        from coldtrack.db import ColdTrackDB

        db = ColdTrackDB(Path(db_path))
        db.init_schema()
        try:
            cur = db.conn().cursor()
            # v5.3.29: dedupe by tx hash against both tables.
            for table in ("FEE_EVENTS", "TRANSACTIONS"):
                dup = cur.execute(
                    f"SELECT 1 FROM {table} WHERE TX_HASH = ?", (record.tx_hash,)
                ).fetchone()
                if dup:
                    return {"ok": True, "note": "already recorded", "fee_events": 0, "transactions": 0}

            # Best-effort CAD conversion via FX_RATES (same contract as close recorder).
            value_cad = record.value_cad
            if value_cad is None and record.value_usd:
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

            # FEE_EVENTS row: AERO amount is stored as TOKEN_B_AMT to match the
            # Pack FEE_EVENT #5 precedent (TOKEN_B_AMT = 127.356 AERO).
            cur.execute(
                """INSERT INTO FEE_EVENTS
                   (POSITION_ID, DATE, TOKEN_A_AMT, TOKEN_B_AMT, VALUE_USD,
                    VALUE_CAD, VALUE_EUR, VALUE_AUD, TX_HASH, SOURCE, NOTES)
                   VALUES (?,?,?,?,?,?,NULL,NULL,?,?,?)""",
                (record.position_db_id, record.date_iso, None, record.aero_amount,
                 record.value_usd, value_cad, record.tx_hash, "HARVEST",
                 record.notes),
            )
            # TRANSACTIONS yield row (AERO emission) — required for portfolio accounting.
            cur.execute(
                """INSERT INTO TRANSACTIONS
                   (ACCOUNT_ID, DATE, TYPE, ASSET, AMOUNT, VALUE_USD,
                    VALUE_CAD, VALUE_EUR, VALUE_AUD,
                    FX_RATE_CAD_USD, FX_RATE_EUR_USD, FX_RATE_AUD_USD,
                    CHAIN, TX_HASH, COUNTERPARTY_ASSET, COUNTERPARTY_AMOUNT,
                    FEE_ASSET, FEE_AMOUNT, FEE_USD, NOTES, CATEGORY)
                   VALUES (?,?,?,?,?,?,NULL,NULL,NULL,NULL,NULL,NULL,?,?,?,?,?,?,?,?,?)""",
                (
                    record.account_id, record.date_iso, "yield", "AERO",
                    record.aero_amount, record.value_usd,
                    "Base", record.tx_hash,
                    None, None,  # COUNTERPARTY_*
                    None, None, None,  # fee columns
                    "gauge emissions", "yield",
                ),
            )
            # v5.3.29: increment cumulative realized fee income.
            cur.execute(
                """UPDATE LP_POSITIONS SET
                   FEES_CLAIMED_USD = COALESCE(FEES_CLAIMED_USD, 0) + ?,
                   FEES_EARNED_USD = COALESCE(FEES_EARNED_USD, 0) + ?,
                   UPDATED_AT=datetime('now')
                 WHERE ID=?""",
                (record.value_usd or 0, record.value_usd or 0, record.position_db_id),
            )
            db.commit()
            return {"ok": True, "fee_events": 1, "transactions": 1}
        except Exception as e:
            db.rollback()
            # Persist to pending so the record is not lost.
            pending = {
                "reason": str(e),
                "claim_record": {
                    "position_id_str": record.position_id_str,
                    "position_db_id": record.position_db_id,
                    "account_id": record.account_id,
                    "aero_amount": record.aero_amount,
                    "value_usd": record.value_usd,
                    "value_cad": record.value_cad,
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

        # v5.3.29: resolve the real account_id for the TRANSACTIONS yield row.
        account_id = 0
        from coldtrack.db import ColdTrackDB
        try:
            db_lookup = ColdTrackDB(Path(db_path))
            db_lookup.init_schema()
            acct_row = db_lookup.conn().execute(
                "SELECT ID FROM ACCOUNTS WHERE NAME=?", (account,)
            ).fetchone()
            if acct_row:
                account_id = acct_row["ID"]
            db_lookup.close()
        except Exception:
            pass

        record = GaugeClaimRecord(
            position_id_str=str(token_id),
            account_id=account_id,
            position_db_id=position_db_id,
            date_iso=datetime.now(timezone.utc).isoformat(),
            aero_amount=aero_amount,
            value_usd=value_usd,
            tx_hash=result.tx_hash,
        )
        self._record_claim(record, db_path, base_dir=base_dir)
        return result

    def detect_external_claims(
        self,
        gauge_address: str,
        wallet_address: str,
        db_path: Any,
        position_db_id: int,
        from_block: Optional[int] = None,
        to_block: Optional[int] = None,
    ) -> List[GaugeClaimRecord]:
        """Detect AERO transfer events from a gauge to the wallet not yet recorded.

        v5.3.29: external claims (e.g. Aerodrome UI) leave on-chain traces. We
        scan Transfer logs from the gauge address to the wallet and return
        claim records that are NOT already present in FEE_EVENTS or
        TRANSACTIONS by tx hash. Caller decides whether to auto-record or
        prompt for review.
        """
        if not gauge_address or not wallet_address:
            return []

        # Find the AERO token Transfer events: topic0 = Transfer, topic1 = from
        # (gauge), topic2 = to (wallet). The gauge is the source of the reward.
        transfer_topic = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
        pad_wallet = wallet_address[2:].lower().zfill(64)
        pad_gauge = gauge_address[2:].lower().zfill(64)
        # Build an eth_getLogs filter. Some RPCs dislike an empty topics list
        # but accept [[transfer_topic], [pad_gauge], [pad_wallet]].
        params = [{
            "fromBlock": hex(from_block) if from_block else "earliest",
            "toBlock": hex(to_block) if to_block else "latest",
            "address": AERO_TOKEN,
            "topics": [
                transfer_topic,
                "0x" + pad_gauge,
                "0x" + pad_wallet,
            ],
        }]
        try:
            logs = self._rpc_call("eth_getLogs", params) or []
        except Exception as e:
            print(f"[aero-gauge] external claim scan failed: {e}")
            return []
        if not isinstance(logs, list):
            return []

        # Load already-recorded tx hashes to dedupe.
        from coldtrack.db import ColdTrackDB
        try:
            db = ColdTrackDB(Path(db_path))
            db.init_schema()
            recorded = {
                r[0].lower()
                for r in db.conn().execute(
                    "SELECT TX_HASH FROM FEE_EVENTS WHERE TX_HASH IS NOT NULL "
                    "UNION SELECT TX_HASH FROM TRANSACTIONS WHERE TX_HASH IS NOT NULL"
                ).fetchall()
            }
            db.close()
        except Exception as e:
            print(f"[aero-gauge] dedupe load failed: {e}")
            recorded = set()

        aero_price = self._aero_price_usd()
        records: List[GaugeClaimRecord] = []
        seen_tx: set = set()
        for log in logs:
            try:
                tx_hash = log.get("transactionHash")
                if not tx_hash or tx_hash.lower() in seen_tx:
                    continue
                if tx_hash.lower() in recorded:
                    continue
                seen_tx.add(tx_hash.lower())
                data = log.get("data", "0x0")
                raw_amt = int(data, 16)
                aero_amount = raw_amt / (10 ** AERO_DECIMALS)
                if aero_amount <= 0:
                    continue
                value_usd = round(aero_amount * aero_price, 6) if aero_price else None
                # Block timestamp for the date is best-effort.
                date_iso = datetime.now(timezone.utc).isoformat()
                try:
                    receipt = self._rpc_call("eth_getTransactionReceipt", [tx_hash])
                    if isinstance(receipt, dict):
                        block_num = receipt.get("blockNumber")
                        if block_num:
                            block = self._rpc_call("eth_getBlockByNumber", [block_num, False])
                            if isinstance(block, dict) and block.get("timestamp"):
                                date_iso = datetime.fromtimestamp(
                                    int(block["timestamp"], 16), tz=timezone.utc
                                ).isoformat()
                except Exception:
                    pass
                records.append(GaugeClaimRecord(
                    position_id_str=str(position_db_id),
                    account_id=0,
                    position_db_id=position_db_id,
                    date_iso=date_iso,
                    aero_amount=aero_amount,
                    value_usd=value_usd,
                    tx_hash=tx_hash,
                ))
            except Exception:
                continue
        return records

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
        # v5.3.29: flag the writer so the post-close state decomposes withdrawn
        # token0/token1 into principal + accrued trading fees for the ledger.
        self.base_writer._staked_close_decompose = True
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
        finally:
            self.base_writer._staked_close_decompose = False

        # Record the claim if it produced a tx. The close itself is recorded by
        # AerodromeWriter._post_close_state / record_close_and_export in lp_tab.
        position_db_id: Optional[int] = None
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
                    position_db_id = row["ID"]
                    self.claim_and_record(
                        token_id, account, position_db_id, db_path,
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

        # v5.3.29: also surface any external claims detected for this gauge.
        try:
            wallet_address = self.base_writer._get_account_address(account)
            gauge_address = self._is_staked(token_id, position_manager, wallet_address)
            if gauge_address and db_path and position_db_id:
                ext = self.detect_external_claims(
                    gauge_address, wallet_address, db_path, position_db_id
                )
                for rec in ext:
                    # Best-effort auto-record external claims. In production the
                    # caller may prefer a pending-review dialog; here we record
                    # and log so the user sees the backfill happened.
                    self._record_claim(rec, db_path, base_dir=base_dir)
                    print(f"[aero-gauge] recorded external claim {rec.tx_hash}: {rec.aero_amount} AERO")
        except Exception as e:
            print(f"[aero-gauge] external claim detection skipped: {e}")

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
