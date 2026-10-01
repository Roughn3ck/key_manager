"""ColdStack — ColdTrack Collect-Fee Ledger Recorder (v5.3.30).

Records a standalone LP fee collection to coldtrack.db as a single FEE_EVENTS
row (SOURCE='MANUAL'), deduplicated by TX_HASH.  This is the accounting mirror
of compound_recorder.py and close_recorder.py:

  - collect: SOURCE='MANUAL' (user-initiated standalone collect).
  - compound: SOURCE='HARVEST' (reinvested fees via compound_recorder).
  - close:    SOURCE='HARVEST' (final fees realized on close via close_recorder).

All three use the same FEE_EVENTS table and the same TX_HASH dedupe rule so a
re-run, retry, or overlapping operation never creates duplicate income rows.
No CAPITAL_EVENTS row is written — collecting fees is an internal realization
of accrued yield, not an external capital movement.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from coldtrack.db import ColdTrackDB

logger = logging.getLogger(__name__)

PENDING_DIR_NAME = "coldstack_pending_records"


@dataclass
class CollectResult:
    """Captured facts of a successful standalone fee collection."""
    position_mint: str
    platform: str
    chain: str
    tx_hash: Optional[str] = None
    # Token amounts in human units.
    token_a_amount: Optional[float] = None
    token_b_amount: Optional[float] = None
    token_a_symbol: Optional[str] = None
    token_b_symbol: Optional[str] = None
    value_usd: Optional[float] = None
    block_time_iso: Optional[str] = None
    owner: Optional[str] = None

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2)

    @staticmethod
    def from_json(s: str) -> "CollectResult":
        return CollectResult(**json.loads(s))


class CollectRecorder:
    """Writes a standalone collect to FEE_EVENTS, deduped by TX_HASH."""

    def __init__(self, db: ColdTrackDB) -> None:
        self.db = db

    def record(self, result: CollectResult) -> Dict[str, Any]:
        """Write the collect to FEE_EVENTS atomically."""
        match = self._match_position(result)
        if match.get("error"):
            return match
        pos_id = match["position_id"]
        account_id = match["account_id"]
        conn = self.db.conn()

        cur = conn.cursor()
        existing = cur.execute(
            "SELECT STATUS FROM LP_POSITIONS WHERE ID=?", (pos_id,)
        ).fetchone()
        already_closed = bool(existing and existing["STATUS"] == "closed")

        self.db.begin_immediate(busy_timeout_ms=5000)
        try:
            if already_closed:
                sigs = result.tx_hash or ""
                append_note = f"ColdStack collect replay sig: {sigs}".strip()
                if append_note:
                    conn.execute(
                        """UPDATE LP_POSITIONS SET
                           NOTES = COALESCE(NOTES || '\n' || ?, ?),
                           UPDATED_AT = datetime('now') WHERE ID=?""",
                        (append_note, append_note, pos_id),
                    )
                self.db.commit()
                return {
                    "ok": True,
                    "position_id": pos_id,
                    "account_id": account_id,
                    "fee_events": 0,
                    "note": "position already closed — sig appended to NOTES",
                }

            n_fee = self._insert_fee_event(conn, result, pos_id, match)
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise

        return {
            "ok": True,
            "position_id": pos_id,
            "account_id": account_id,
            "fee_events": n_fee,
        }

    def _match_position(self, result: CollectResult) -> Dict[str, Any]:
        """Match the collect to a UNIQUE LP_POSITIONS row."""
        token_id = result.position_mint
        cur = self.db.conn().cursor()

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

        platform = (result.platform or "").strip()
        chain = (result.chain or "").strip()
        pair_guess = ""
        if result.token_a_symbol and result.token_b_symbol:
            pair_guess = f"{result.token_a_symbol}/{result.token_b_symbol}"
        fallback_rows = cur.execute(
            """SELECT * FROM LP_POSITIONS
               WHERE (UPPER(PLATFORM) = UPPER(?) OR PLATFORM IS NULL)
                 AND (UPPER(POOL_NAME) = UPPER(?)
                      OR UPPER(TOKEN_A) || '/' || UPPER(TOKEN_B) = UPPER(?))
                 AND STATUS = 'active'
               ORDER BY ID""",
            (platform, pair_guess, pair_guess),
        ).fetchall()
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
            return {
                "position_id": row["ID"],
                "account_id": row["ACCOUNT_ID"],
                "pool_name": row.get("POOL_NAME"),
                "token_a": row.get("TOKEN_A"),
                "token_b": row.get("TOKEN_B"),
            }
        if len(fallback_rows) > 1:
            return {"error": f"ambiguous LP_POSITIONS match for {platform}/{chain} "
                             f"({len(fallback_rows)} active rows) — needs manual mapping"}
        return {"error": f"no LP_POSITIONS row found for position mint {token_id} "
                          f"or platform/pool {platform}/{chain}"}

    def _insert_fee_event(
        self, conn, result: CollectResult, pos_id: int, match: Dict[str, Any]
    ) -> int:
        """Insert one FEE_EVENTS row for a standalone collect, deduped by TX_HASH."""
        tx_hash = result.tx_hash
        if not tx_hash:
            return 0
        cur = conn.cursor()
        dup = cur.execute(
            "SELECT 1 FROM FEE_EVENTS WHERE TX_HASH = ? AND POSITION_ID = ?",
            (tx_hash, pos_id),
        ).fetchone()
        if dup:
            return 0

        date_iso = result.block_time_iso or _now_iso()
        token_a = match.get("token_a")
        token_b = match.get("token_b")
        amt_a = result.token_a_amount if result.token_a_symbol == token_a else None
        amt_b = result.token_b_amount if result.token_b_symbol == token_b else None
        if result.token_a_symbol and result.token_a_symbol != token_a:
            amt_b = result.token_a_amount
        if result.token_b_symbol and result.token_b_symbol != token_b:
            amt_a = result.token_b_amount
        notes = "standalone collect fees"
        conn.execute(
            """INSERT INTO FEE_EVENTS
               (POSITION_ID, DATE, TOKEN_A_AMT, TOKEN_B_AMT, VALUE_USD,
                VALUE_CAD, VALUE_EUR, VALUE_AUD, TX_HASH, SOURCE, NOTES)
               VALUES (?,?,?,?,?,NULL,NULL,NULL,?,?,?)""",
            (pos_id, date_iso, amt_a, amt_b, result.value_usd,
             tx_hash, "MANUAL", notes),
        )
        return 1


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


def persist_pending(result: CollectResult, reason: str,
                    base_dir: Optional[Path] = None) -> Path:
    """Persist a CollectResult + failure reason to a pending JSON file."""
    d = pending_dir(base_dir)
    fname = d / f"pending_collect_{result.position_mint}_{_ts()}.json"
    fname.write_text(
        json.dumps({"reason": reason, "collect_result": json.loads(result.to_json())},
                   indent=2),
        encoding="utf-8",
    )
    return fname


def record_collect_and_export(result: CollectResult, db_path: Any,
                              base_dir: Optional[Path] = None) -> Dict[str, Any]:
    """Open the portfolio coldtrack.db, record the collect, auto-export view."""
    db = ColdTrackDB(Path(db_path))
    db.init_schema()
    try:
        out = CollectRecorder(db).record(result)
    except Exception as e:
        p = persist_pending(result, str(e), base_dir)
        logger.error("Collect ledger write failed; pending at %s: %s", p, e)
        raise
    finally:
        db.close()
    if out.get("error"):
        persist_pending(result, out["error"], base_dir)
        return out
    try:
        from coldtrack.sentinel_export import SentinelExporter
        SentinelExporter(None).export()
    except Exception as e:
        logger.warning("Post-record export failed (collect already recorded): %s", e)
    return out


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
