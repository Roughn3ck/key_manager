"""ColdStack — ColdTrack Close-Position Ledger Recorder (v5.3.16).

Writes a successful LP close to coldtrack.db: the liquidity legs, the collected
fees, the position STATUS flip, a closing snapshot, and dated FEE_EVENTS — all
in ONE sqlite transaction. ColdStack becomes a db writer for closes (alongside
Kimi's tooling), with strict locking discipline: BEGIN IMMEDIATE + busy_timeout,
everything captured before the txn opens, never a network call inside the txn.

No schema changes. Column names mirror the existing Project X close rows
(KP db #68–72). FEE_EVENTS.SOURCE uses 'HARVEST' (close-collected fees).
Never fabricates LP entry data: if the position can't be matched to a UNIQUE
LP_POSITIONS row (by TOKEN_ID = position mint), the record is written to a
pending file and retried — never guessed.

On write failure the on-chain close is still done: the caller surfaces
"close succeeded; ledger write failed" and the CloseResult is persisted for
retry. The auto-export hook regenerates strategy_view.json after a successful
record so the sentinel hot-reloads.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from coldtrack.db import ColdTrackDB

logger = logging.getLogger(__name__)

PENDING_DIR_NAME = "coldstack_pending_records"


# ---------------------------------------------------------------------------
# CloseResult — everything captured from a successful on-chain close
# ---------------------------------------------------------------------------

@dataclass
class CloseLeg:
    """One token movement leg of a close, attributed to the tx that moved it.

    kind: 'liquidity' (position principal) or 'fee' (collected yield).
    sig is the signature of the transaction that MOVED these tokens (the
    Orca decrease tx for liquidity legs, the collect tx for fee legs) — the
    close (NFT-burn) signature NEVER appears on a withdraw/yield row.
    """
    asset: str
    amount: float
    value_usd: Optional[float] = None
    kind: str = "liquidity"          # 'liquidity' | 'fee'
    sig: Optional[str] = None


@dataclass
class CloseResult:
    """Captured facts of a successful LP close (all network data pre-fetched).

    Fully serializable to JSON for the pending-record retry path.
    """
    position_mint: str              # LP_POSITIONS.TOKEN_ID match key (e.g. 'FbNH…')
    platform: str                   # e.g. 'Orca'
    chain: str                      # e.g. 'Solana'
    legs: List[CloseLeg] = field(default_factory=list)
    close_sig: Optional[str] = None        # the close (burn) tx — snapshot notes only
    collect_sig: Optional[str] = None      # the fee-collect tx (fee legs + FEE_EVENTS)
    decrease_sig: Optional[str] = None     # the liquidity tx (liquidity legs)
    block_time_iso: Optional[str] = None   # close tx blockTime, ISO-8601 UTC (CLOSED_DATE)
    # Gas per tx (native token; SOL for Solana), keyed by tx sig.
    gas: Dict[str, float] = field(default_factory=dict)   # sig -> native amount
    gas_asset: Optional[str] = None                        # e.g. 'SOL'
    token_price_usd: Dict[str, float] = field(default_factory=dict)  # symbol -> USD
    # Final on-chain position amounts (for the closing snapshot).
    final_amounts: Dict[str, float] = field(default_factory=dict)    # asset -> amount

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2)

    @staticmethod
    def from_json(s: str) -> "CloseResult":
        d = json.loads(s)
        d["legs"] = [CloseLeg(**leg) for leg in d.get("legs", [])]
        return CloseResult(**d)


# ---------------------------------------------------------------------------
# Recorder
# ---------------------------------------------------------------------------

class CloseRecorder:
    """Orchestrates the four-table atomic write for a successful close."""

    def __init__(self, db: ColdTrackDB) -> None:
        self.db = db

    # -- public ---------------------------------------------------------

    def record(self, result: CloseResult) -> Dict[str, Any]:
        """Write the close to the ledger atomically. Never raises for a
        missing/ambiguous LP match — that goes to the pending file instead.
        Raises on a DB write error (caller persists to pending + reports)."""
        match = self._match_position(result)
        if match.get("error"):
            return match  # {"error": reason, ...} — caller persists to pending
        pos_id = match["position_id"]
        account_id = match["account_id"]
        conn = self.db.conn()

        # If the row is already closed (e.g. Kimi recorded it manually, or a
        # previous close succeeded), do NOT duplicate close data. Just append the
        # new sigs to NOTES and sync the live TOKEN_ID if needed.
        cur = conn.cursor()
        existing = cur.execute(
            "SELECT STATUS, NOTES, TOKEN_ID FROM LP_POSITIONS WHERE ID=?", (pos_id,)
        ).fetchone()
        already_closed = bool(existing and existing["STATUS"] == "closed")

        # Begin the atomic write — everything was captured before this point.
        self.db.begin_immediate(busy_timeout_ms=5000)
        try:
            # If we matched via the stale-id fallback, sync the live TOKEN_ID now,
            # inside the same transaction.
            sync_id = match.get("_sync_token_id")
            if sync_id is not None:
                old_id = match.get("_sync_token_id_old")
                sync_note = f"identifier sync: TOKEN_ID {old_id} -> {sync_id}"
                conn.execute(
                    """UPDATE LP_POSITIONS SET TOKEN_ID = ?,
                       NOTES = COALESCE(NOTES || '\n' || ?, ?),
                       UPDATED_AT = datetime('now') WHERE ID = ?""",
                    (sync_id, sync_note, sync_note, pos_id),
                )

            if already_closed:
                sigs = ", ".join(s for s in (result.decrease_sig, result.collect_sig,
                                              result.close_sig) if s)
                append_note = f"ColdStack close replay sigs: {sigs}".strip()
                if append_note:
                    conn.execute(
                        """UPDATE LP_POSITIONS SET
                           NOTES = COALESCE(NOTES || '\n' || ?, ?),
                           UPDATED_AT = datetime('now') WHERE ID = ?""",
                        (append_note, append_note, pos_id),
                    )
                self.db.commit()
                return {
                    "ok": True,
                    "position_id": pos_id,
                    "account_id": account_id,
                    "fee_events": 0,
                    "transactions": 0,
                    "note": "already closed — sigs appended to NOTES",
                }

            self._insert_transactions(conn, result, account_id, match)
            self._close_position_row(conn, result, pos_id)
            self._upsert_snapshot(conn, result, pos_id, match)
            n_fee = self._insert_fee_events(conn, result, pos_id, match)
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise

        return {
            "ok": True,
            "position_id": pos_id,
            "account_id": account_id,
            "fee_events": n_fee,
            "transactions": len(result.legs),
        }

    # -- matching ---------------------------------------------------------

    def _match_position(self, result: CloseResult) -> Dict[str, Any]:
        """Match the close to a UNIQUE LP_POSITIONS row.

        Primary match is by TOKEN_ID = position mint. If that misses (e.g. the
        saved record carries a stale/dead NFT id), fall back to a unique match
        on PLATFORM + POOL_NAME + STATUS='active'. On a unique fallback match,
        the row's TOKEN_ID is synced to the live mint (noted in NOTES) so the
        sentinel view uses the correct identifier.

        Ambiguous or missing → never fabricate entry data; caller goes pending.
        """
        token_id = result.position_mint
        cur = self.db.conn().cursor()

        # 1. Primary: exact TOKEN_ID match
        rows = cur.execute(
            "SELECT * FROM LP_POSITIONS WHERE TOKEN_ID = ? ORDER BY ID",
            (token_id,),
        ).fetchall()
        rows = [dict(r) for r in rows]
        if len(rows) > 1:
            active = [r for r in rows if r.get("STATUS") == "active"]
            if len(active) == 1:
                rows = active
            else:
                return {"error": f"ambiguous LP_POSITIONS match for {token_id} "
                                 f"({len(rows)} rows) — needs manual mapping"}
        if rows:
            row = rows[0]
            return {
                "position_id": row["ID"],
                "account_id": row["ACCOUNT_ID"],
                "pool_name": row.get("POOL_NAME"),
                "token_a": row.get("TOKEN_A"),
                "token_b": row.get("TOKEN_B"),
            }

        # 2. Fallback: unique active row by platform + pool + chain. This
        # handles stale id records (e.g. Aerodrome G1 Pack row 7).
        platform = (result.platform or "").strip()
        chain = (result.chain or "").strip()
        # Prefer an exact pool name, but also try the token pair if available.
        pool_candidates = []
        if result.legs:
            pair_guess = "/".join(
                dict.fromkeys(l.asset for l in result.legs if l.kind == "liquidity")
            )
            if pair_guess:
                pool_candidates.append(pair_guess)
        fallback_rows = cur.execute(
            """SELECT * FROM LP_POSITIONS
               WHERE (UPPER(PLATFORM) = UPPER(?) OR PLATFORM IS NULL)
                 AND (UPPER(POOL_NAME) = UPPER(?)
                      OR UPPER(TOKEN_A) || '/' || UPPER(TOKEN_B) = UPPER(?))
                 AND STATUS = 'active'
               ORDER BY ID""",
            (platform, pool_candidates[0] if pool_candidates else "", pool_candidates[0] if pool_candidates else ""),
        ).fetchall()
        # If no leg-based pool guess, broaden to platform+chain active rows.
        if not fallback_rows and platform and chain:
            fallback_rows = cur.execute(
                """SELECT * FROM LP_POSITIONS
                   WHERE UPPER(PLATFORM) = UPPER(?)
                     AND UPPER(CHAIN) = UPPER(?)
                     AND STATUS = 'active'
                   ORDER BY ID""",
                (platform, chain),
            ).fetchall()
        fallback_rows = [dict(r) for r in fallback_rows]
        if len(fallback_rows) == 1:
            row = fallback_rows[0]
            old_token_id = row.get("TOKEN_ID")
            new_token_id = token_id
            if old_token_id != new_token_id:
                # Defer the identifier sync so it happens inside the recorder's
                # atomic transaction. The caller will apply it via _sync_token_id.
                row["_sync_token_id"] = new_token_id
                row["_sync_token_id_old"] = old_token_id
            return {
                "position_id": row["ID"],
                "account_id": row["ACCOUNT_ID"],
                "pool_name": row.get("POOL_NAME"),
                "token_a": row.get("TOKEN_A"),
                "token_b": row.get("TOKEN_B"),
                "_sync_token_id": row.get("_sync_token_id"),
                "_sync_token_id_old": row.get("_sync_token_id_old"),
            }
        if len(fallback_rows) > 1:
            return {"error": f"ambiguous LP_POSITIONS match for {platform}/{chain} "
                             f"({len(fallback_rows)} active rows) — needs manual mapping"}

        return {"error": f"no LP_POSITIONS row found for position mint {token_id} "
                          f"or platform/pool {platform}/{chain}"}

    # -- per-table writers (raw SQL inside the caller's transaction) --------

    def _insert_transactions(self, conn, result: CloseResult, account_id: int,
                             match: Dict[str, Any]) -> None:
        date_iso = result.block_time_iso or _now_iso()
        pool = match.get("pool_name") or f"{match.get('token_a')}/{match.get('token_b')}"
        for leg in result.legs:
            type_ = "yield" if leg.kind == "fee" else "lp_withdraw"
            category = "yield" if leg.kind == "fee" else "lp"
            gas_amt = result.gas.get(leg.sig) if leg.sig else None
            notes = (f"{pool} close — {leg.asset} {leg.kind} "
                     f"returned. token_id {result.position_mint}. "
                     f"{'collect tx.' if leg.kind == 'fee' else 'collect/decrease tx.'}")
            conn.execute(
                """INSERT INTO TRANSACTIONS
                   (ACCOUNT_ID, DATE, TYPE, ASSET, AMOUNT, VALUE_USD,
                    VALUE_CAD, VALUE_EUR, VALUE_AUD,
                    FX_RATE_CAD_USD, FX_RATE_EUR_USD, FX_RATE_AUD_USD,
                    CHAIN, TX_HASH, COUNTERPARTY_ASSET, COUNTERPARTY_AMOUNT,
                    FEE_ASSET, FEE_AMOUNT, FEE_USD, NOTES, CATEGORY)
                   VALUES (?,?,?,?,?,?,NULL,NULL,NULL,NULL,NULL,NULL,?,?,?,?,?,?,?,?,?)""",
                (
                    account_id, date_iso, type_, leg.asset, leg.amount,
                    leg.value_usd,
                    result.chain, leg.sig,
                    None, None,                      # COUNTERPARTY_*
                    (result.gas_asset if gas_amt is not None else None),
                    gas_amt,
                    None,                            # FEE_USD (no confident FX → NULL)
                    notes, category,
                ),
            )

    def _close_position_row(self, conn, result: CloseResult, pos_id: int) -> None:
        closed_date = result.block_time_iso or _now_iso()
        conn.execute(
            """UPDATE LP_POSITIONS SET STATUS='closed', CLOSED_DATE=?,
               UPDATED_AT=datetime('now') WHERE ID=?""",
            (closed_date, pos_id),
        )

    def _upsert_snapshot(self, conn, result: CloseResult, pos_id: int,
                         match: Dict[str, Any]) -> None:
        report_date = (result.block_time_iso or _now_iso())[:10]
        ta = match.get("token_a")
        tb = match.get("token_b")
        amt_a = result.final_amounts.get(ta) if ta else None
        amt_b = result.final_amounts.get(tb) if tb else None
        pa = result.token_price_usd.get(ta) if ta else None
        pb = result.token_price_usd.get(tb) if tb else None
        total = (amt_a or 0) * (pa or 0) + (amt_b or 0) * (pb or 0) \
            if (amt_a is not None or amt_b is not None) else None
        sigs = ", ".join(s for s in (result.decrease_sig, result.collect_sig,
                                      result.close_sig) if s)
        notes = f"CLOSED via ColdStack. sigs: {sigs}".strip()
        conn.execute(
            """INSERT INTO LP_SNAPSHOTS
               (LP_POSITION_ID, REPORT_DATE, TOKEN_A_AMOUNT, TOKEN_B_AMOUNT,
                TOKEN_A_PRICE_USD, TOKEN_B_PRICE_USD, TOTAL_VALUE_USD,
                TOTAL_VALUE_CAD, TOTAL_VALUE_EUR, TOTAL_VALUE_AUD,
                FX_RATE_CAD_USD, FX_RATE_EUR_USD, FX_RATE_AUD_USD,
                IN_RANGE, NOTES)
               VALUES (?,?,?,?,?,?,?,NULL,NULL,NULL,NULL,NULL,NULL,?,?)
               ON CONFLICT(LP_POSITION_ID, REPORT_DATE) DO UPDATE SET
                    TOKEN_A_AMOUNT = excluded.TOKEN_A_AMOUNT,
                    TOKEN_B_AMOUNT = excluded.TOKEN_B_AMOUNT,
                    TOKEN_A_PRICE_USD = excluded.TOKEN_A_PRICE_USD,
                    TOKEN_B_PRICE_USD = excluded.TOKEN_B_PRICE_USD,
                    TOTAL_VALUE_USD = excluded.TOTAL_VALUE_USD,
                    IN_RANGE = excluded.IN_RANGE,
                    NOTES = excluded.NOTES""",
            (pos_id, report_date, amt_a, amt_b, pa, pb, total, 0, notes),
        )

    def _insert_fee_events(self, conn, result: CloseResult, pos_id: int,
                           match: Dict[str, Any]) -> int:
        """One dated FEE_EVENTS row per close when any fee leg exists.
        SOURCE='HARVEST' (close-collected); the source CHECK allows only
        MANUAL/READER/HARVEST — never altered."""
        fee_legs = [l for l in result.legs if l.kind == "fee"]
        if not fee_legs:
            return 0
        date_iso = result.block_time_iso or _now_iso()
        amt_a = sum((l.amount or 0) for l in fee_legs if l.asset == match.get("token_a")) or None
        amt_b = sum((l.amount or 0) for l in fee_legs if l.asset == match.get("token_b")) or None
        # Token order vs the position row: A first.
        value_usd = sum((l.value_usd or 0) for l in fee_legs) or None
        conn.execute(
            """INSERT INTO FEE_EVENTS
               (POSITION_ID, DATE, TOKEN_A_AMT, TOKEN_B_AMT, VALUE_USD,
                VALUE_CAD, VALUE_EUR, VALUE_AUD, TX_HASH, SOURCE, NOTES)
               VALUES (?,?,?,?,?,NULL,NULL,NULL,?,?,?)""",
            (pos_id, date_iso, amt_a, amt_b, value_usd,
             result.collect_sig or result.decrease_sig, "HARVEST",
             "CLOSED via ColdStack"),
        )
        return 1


# ---------------------------------------------------------------------------
# Pending-record persistence (never lose the close record on a write failure)
# ---------------------------------------------------------------------------

def pending_dir(base_dir: Optional[Path] = None) -> Path:
    """The pending-record folder, co-located with the vault by default."""
    if base_dir is None:
        import sys as _sys
        if getattr(_sys, "frozen", False):
            base_dir = Path(_sys.executable).parent
        else:
            base_dir = Path(__file__).parent.parent.parent
    d = Path(base_dir) / PENDING_DIR_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def persist_pending(result: CloseResult, reason: str,
                    base_dir: Optional[Path] = None) -> Path:
    """Persist a CloseResult + failure reason to a pending JSON file."""
    d = pending_dir(base_dir)
    fname = d / f"pending_close_{result.position_mint}_{_ts()}.json"
    fname.write_text(
        json.dumps({"reason": reason, "close_result": json.loads(result.to_json())},
                   indent=2),
        encoding="utf-8",
    )
    return fname


def retry_pending(path: Path, db: ColdTrackDB) -> Dict[str, Any]:
    """Retry a pending close record. Raises on a hard DB error again."""
    blob = json.loads(Path(path).read_text(encoding="utf-8"))
    result = CloseResult.from_json(json.dumps(blob["close_result"]))
    return CloseRecorder(db).record(result)


# ---------------------------------------------------------------------------
# Convenience: record a close + auto-export the sentinel view (one flow)
# ---------------------------------------------------------------------------

def record_close_and_export(result: CloseResult, db_path: Any,
                            base_dir: Optional[Path] = None) -> Dict[str, Any]:
    """Open the portfolio coldtrack.db, record atomically, auto-export the view.
    On a DB write failure, persist to pending and re-raise so the caller reports
    'close succeeded; ledger write failed'."""
    db = ColdTrackDB(Path(db_path))
    db.init_schema()
    try:
        out = CloseRecorder(db).record(result)
    except Exception as e:
        p = persist_pending(result, str(e), base_dir)
        logger.error("Close ledger write failed; pending at %s: %s", p, e)
        raise
    finally:
        db.close()
    if out.get("error"):
        # Not-mapped/ambiguous close — persist for manual retry, never fabricate.
        persist_pending(result, out["error"], base_dir)
        return out
    # Auto-export the merged view (the sentinel hot-reloads in ~20s).
    try:
        from coldtrack.sentinel_export import SentinelExporter
        SentinelExporter(None).export()  # default Pack + K&P merge
    except Exception as e:  # export failure must never mask a successful record
        logger.warning("Post-record export failed (position already recorded): %s", e)
    return out


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
