"""ColdStack — ColdTrack Compound-Fee Ledger Recorder (v5.3.29).

Writes a successful HyperEVM LP fee compound to coldtrack.db: the collected fees
as realized income (FEE_EVENTS), the reinvested amounts as an entry-basis
increase on LP_POSITIONS, and a post-compound snapshot in LP_SNAPSHOTS — all
in ONE sqlite transaction.

A compound = collect fees -> re-add them as liquidity. Recording rules (Kimi's
v5.3.29 data contract):

  1. FEE_EVENTS: one row, SOURCE='HARVEST', NOTES "auto-compound — fees
     reinvested", TOKEN_A_AMT/TOKEN_B_AMT as collected, VALUE_USD at spot,
     TX_HASH = the compound transaction hash.
  2. LP_POSITIONS: increment FEES_CLAIMED_USD / FEES_EARNED_USD; grow the
     entry basis (AMOUNT_A_ENTRY, AMOUNT_B_ENTRY, TOTAL_VALUE_USD_ENTRY) by the
     reinvested amounts so capital-growth math never double-counts the fee income.
  3. NO CAPITAL_EVENTS row — internal reinvestment, not external capital.
  4. LP_SNAPSHOTS: post-compound snapshot (new amounts, prices, IN_RANGE state,
     NOTES with the tx hash + "auto-compound").
  5. Dedupe by TX_HASH so a re-run never double-records.

No schema changes. Column names mirror the existing Project X rows. The
recorder is called from the GUI compound success path and from the sole-writer
CLI `coldtrack compound` subcommand.

On write failure the on-chain compound is still done: the caller surfaces
"compound succeeded; ledger write failed" and the CompoundResult is persisted
for retry. The auto-export hook regenerates strategy_view.json after a
successful record so the sentinel hot-reloads.
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
# CompoundResult — everything captured from a successful on-chain compound
# ---------------------------------------------------------------------------

@dataclass
class CompoundLeg:
    """One reinvested token leg of a compound, attributed to the increase tx."""
    asset: str
    amount: float
    value_usd: Optional[float] = None
    kind: str = "compound"          # 'compound' always
    sig: Optional[str] = None


@dataclass
class CompoundResult:
    """Captured facts of a successful LP fee compound (all network data pre-fetched).

    Fully serializable to JSON for the pending-record retry path.
    """
    position_mint: str              # LP_POSITIONS.TOKEN_ID match key
    platform: str                   # e.g. 'HyperEVM'
    chain: str                      # e.g. 'HyperEVM'
    tx_hash: Optional[str] = None   # the compound (increaseLiquidity) tx hash
    collect_sig: Optional[str] = None  # the collect tx hash (source of fees)
    legs: List[CompoundLeg] = field(default_factory=list)
    block_time_iso: Optional[str] = None   # compound tx blockTime, ISO-8601 UTC
    token_price_usd: Dict[str, float] = field(default_factory=dict)  # symbol -> USD
    # Final on-chain position amounts (for the post-compound snapshot).
    final_amounts: Dict[str, float] = field(default_factory=dict)    # asset -> amount
    # Wallet address that owned/signed the position.
    owner: Optional[str] = None

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2)

    @staticmethod
    def from_json(s: str) -> "CompoundResult":
        d = json.loads(s)
        d["legs"] = [CompoundLeg(**leg) for leg in d.get("legs", [])]
        return CompoundResult(**d)


# ---------------------------------------------------------------------------
# Recorder
# ---------------------------------------------------------------------------

class CompoundRecorder:
    """Orchestrates the three-table atomic write for a successful compound."""

    def __init__(self, db: ColdTrackDB) -> None:
        self.db = db

    # -- public ---------------------------------------------------------

    def record(self, result: CompoundResult) -> Dict[str, Any]:
        """Write the compound to the ledger atomically. Never raises for a
        missing/ambiguous LP match — that goes to the pending file instead.
        Raises on a DB write error (caller persists to pending + reports)."""
        match = self._match_position(result)
        if match.get("error"):
            return match  # {"error": reason, ...} — caller persists to pending
        pos_id = match["position_id"]
        account_id = match["account_id"]
        conn = self.db.conn()

        cur = conn.cursor()
        existing = cur.execute(
            "SELECT STATUS FROM LP_POSITIONS WHERE ID=?", (pos_id,)
        ).fetchone()
        already_closed = bool(existing and existing["STATUS"] == "closed")

        # Begin the atomic write — everything was captured before this point.
        self.db.begin_immediate(busy_timeout_ms=5000)
        try:
            if already_closed:
                sigs = ", ".join(s for s in (result.tx_hash, result.collect_sig) if s)
                append_note = f"ColdStack compound replay sigs: {sigs}".strip()
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
                    "snapshots": 0,
                    "note": "position already closed — sigs appended to NOTES",
                }

            self._update_position_basis(conn, result, pos_id, match)
            n_fee = self._insert_fee_events(conn, result, pos_id, match)
            n_snap = self._upsert_snapshot(conn, result, pos_id, match)
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise

        return {
            "ok": True,
            "position_id": pos_id,
            "account_id": account_id,
            "fee_events": n_fee,
            "snapshots": n_snap,
        }

    # -- matching ---------------------------------------------------------

    def _match_position(self, result: CompoundResult) -> Dict[str, Any]:
        """Match the compound to a UNIQUE LP_POSITIONS row.

        Primary match is by TOKEN_ID = position mint. If that misses, fall back
        to a unique match on PLATFORM + POOL_NAME + STATUS='active'. On a unique
        fallback match, sync the live TOKEN_ID in NOTES.

        Ambiguous or missing → never fabricate entry data; caller goes pending.
        """
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
        pool_candidates = []
        if result.legs:
            pair_guess = "/".join(
                dict.fromkeys(l.asset for l in result.legs)
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

    # -- per-table writers (raw SQL inside the caller's transaction) ----

    def _fx_rates(self, conn, date_iso: str) -> Dict[str, float]:
        """Best-effort FX rates for the compound date; falls back to latest."""
        rates: Dict[str, float] = {}
        if not date_iso:
            return rates
        date_key = date_iso[:10]
        for pair in ("CADUSD", "EURUSD", "AUDUSD"):
            cur = conn.cursor()
            row = cur.execute(
                "SELECT RATE FROM FX_RATES WHERE DATE = ? AND PAIR = ?",
                (date_key, pair),
            ).fetchone()
            if row:
                rates[pair] = row["RATE"]
            else:
                latest = cur.execute(
                    "SELECT RATE FROM FX_RATES WHERE PAIR = ? ORDER BY DATE DESC LIMIT 1",
                    (pair,),
                ).fetchone()
                if latest:
                    rates[pair] = latest["RATE"]
        return rates

    def _update_position_basis(
        self, conn, result: CompoundResult, pos_id: int, match: Dict[str, Any]
    ) -> None:
        """Grow the position's entry basis by the reinvested fee amounts.

        Income is counted once at FEE_EVENTS; the basis grows so that capital-
        growth math stays clean and never double-counts the compounded fees.
        """
        fees_claimed = sum((l.value_usd or 0) for l in result.legs) or 0.0
        token_a = match.get("token_a")
        token_b = match.get("token_b")
        add_a = sum((l.amount or 0) for l in result.legs if l.asset == token_a) or 0.0
        add_b = sum((l.amount or 0) for l in result.legs if l.asset == token_b) or 0.0

        conn.execute(
            """UPDATE LP_POSITIONS SET
               AMOUNT_A_ENTRY = COALESCE(AMOUNT_A_ENTRY, 0) + ?,
               AMOUNT_B_ENTRY = COALESCE(AMOUNT_B_ENTRY, 0) + ?,
               TOTAL_VALUE_USD_ENTRY = COALESCE(TOTAL_VALUE_USD_ENTRY, 0) + ?,
               FEES_CLAIMED_USD = COALESCE(FEES_CLAIMED_USD, 0) + ?,
               FEES_EARNED_USD = COALESCE(FEES_EARNED_USD, 0) + ?,
               UPDATED_AT=datetime('now')
               WHERE ID=?""",
            (add_a, add_b, fees_claimed, fees_claimed, fees_claimed, pos_id),
        )

    def _insert_fee_events(
        self, conn, result: CompoundResult, pos_id: int, match: Dict[str, Any]
    ) -> int:
        """One dated FEE_EVENTS row for the compounded (reinvested) fees.

        SOURCE='HARVEST' is the v5.3.29 convention for realized LP fees.
        Dedupe by TX_HASH so a re-run never double-records.
        """
        if not result.legs:
            return 0
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
        amt_a = sum((l.amount or 0) for l in result.legs if l.asset == token_a) or None
        amt_b = sum((l.amount or 0) for l in result.legs if l.asset == token_b) or None
        value_usd = sum((l.value_usd or 0) for l in result.legs) or None
        notes = "auto-compound — fees reinvested"
        conn.execute(
            """INSERT INTO FEE_EVENTS
               (POSITION_ID, DATE, TOKEN_A_AMT, TOKEN_B_AMT, VALUE_USD,
                VALUE_CAD, VALUE_EUR, VALUE_AUD, TX_HASH, SOURCE, NOTES)
               VALUES (?,?,?,?,?,NULL,NULL,NULL,?,?,?)""",
            (pos_id, date_iso, amt_a, amt_b, value_usd, tx_hash, "HARVEST", notes),
        )
        return 1

    def _upsert_snapshot(
        self, conn, result: CompoundResult, pos_id: int, match: Dict[str, Any]
    ) -> int:
        """Post-compound snapshot: new amounts/prices, IN_RANGE=1, tx hash in notes.

        Returns 1 if a snapshot row was inserted/updated, 0 if no data is available.
        """
        report_date = (result.block_time_iso or _now_iso())[:10]
        ta = match.get("token_a")
        tb = match.get("token_b")
        amt_a = result.final_amounts.get(ta) if ta else None
        amt_b = result.final_amounts.get(tb) if tb else None
        pa = result.token_price_usd.get(ta) if ta else None
        pb = result.token_price_usd.get(tb) if tb else None
        if amt_a is None and amt_b is None:
            return 0
        total = (amt_a or 0) * (pa or 0) + (amt_b or 0) * (pb or 0)
        sigs = ", ".join(s for s in (result.tx_hash, result.collect_sig) if s)
        notes = f"auto-compound via ColdStack. sigs: {sigs}".strip()
        conn.execute(
            """INSERT INTO LP_SNAPSHOTS
               (LP_POSITION_ID, REPORT_DATE, TOKEN_A_AMOUNT, TOKEN_B_AMOUNT,
                TOKEN_A_PRICE_USD, TOKEN_B_PRICE_USD, TOTAL_VALUE_USD,
                TOTAL_VALUE_CAD, TOTAL_VALUE_EUR,
                FX_RATE_CAD_USD, FX_RATE_EUR_USD,
                IN_RANGE, NOTES)
               VALUES (?,?,?,?,?,?,?,NULL,NULL,NULL,NULL,?,?)
               ON CONFLICT(LP_POSITION_ID, REPORT_DATE) DO UPDATE SET
                    TOKEN_A_AMOUNT = excluded.TOKEN_A_AMOUNT,
                    TOKEN_B_AMOUNT = excluded.TOKEN_B_AMOUNT,
                    TOKEN_A_PRICE_USD = excluded.TOKEN_A_PRICE_USD,
                    TOKEN_B_PRICE_USD = excluded.TOKEN_B_PRICE_USD,
                    TOTAL_VALUE_USD = excluded.TOTAL_VALUE_USD,
                    IN_RANGE = excluded.IN_RANGE,
                    NOTES = excluded.NOTES""",
            (pos_id, report_date, amt_a, amt_b, pa, pb, total, 1, notes),
        )
        return 1


# ---------------------------------------------------------------------------
# Pending-record persistence (never lose the compound record on a write failure)
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


def persist_pending(result: CompoundResult, reason: str,
                    base_dir: Optional[Path] = None) -> Path:
    """Persist a CompoundResult + failure reason to a pending JSON file."""
    d = pending_dir(base_dir)
    fname = d / f"pending_compound_{result.position_mint}_{_ts()}.json"
    fname.write_text(
        json.dumps({"reason": reason, "compound_result": json.loads(result.to_json())},
                   indent=2),
        encoding="utf-8",
    )
    return fname


def retry_pending(path: Path, db: ColdTrackDB) -> Dict[str, Any]:
    """Retry a pending compound record. Raises on a hard DB error again."""
    blob = json.loads(Path(path).read_text(encoding="utf-8"))
    result = CompoundResult.from_json(json.dumps(blob["compound_result"]))
    return CompoundRecorder(db).record(result)


# ---------------------------------------------------------------------------
# Convenience: record a compound + auto-export the sentinel view (one flow)
# ---------------------------------------------------------------------------

def record_compound_and_export(result: CompoundResult, db_path: Any,
                               base_dir: Optional[Path] = None) -> Dict[str, Any]:
    """Open the portfolio coldtrack.db, record atomically, auto-export the view.
    On a DB write failure, persist to pending and re-raise so the caller reports
    'compound succeeded; ledger write failed'."""
    db = ColdTrackDB(Path(db_path))
    db.init_schema()
    try:
        out = CompoundRecorder(db).record(result)
    except Exception as e:
        p = persist_pending(result, str(e), base_dir)
        logger.error("Compound ledger write failed; pending at %s: %s", p, e)
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
        logger.warning("Post-record export failed (compound already recorded): %s", e)
    return out


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
