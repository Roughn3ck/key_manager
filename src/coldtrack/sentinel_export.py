"""ColdTrack Sentinel Export bridge.

Materializes ``strategy_view.json`` from coldtrack.db. This is the producer
of contract v1; the Argus Sentinel (separate repo) consumes exactly this shape
via ``loadStrategyData()``. The exporter reads ONLY public portfolio records —
coldtrack.db holds no keys and the exporter touches no vault data and no
network. The write is a local atomic file swap (tmp + os.replace) so the
sentinel's fs.watchFile never observes a partial read.

Contract v1 shape (pinned keys):
  view_version, generated_by, generated_at, source_db   (reserved header)
  fee_events[], capital_events[]                        (reserved arrays)
  <POOL_ID> { pool record }                             (sentinel position ids)
  KP { label, wallet, positions[] }                     (K&P section)

Anything outside the four header keys + the two event arrays is treated by the
sentinel as a pool record keyed by its position id. "KP" is just another
top-level key whose value happens to be the K&P container.
"""
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Contract version the sentinel pins (argus_sentinel.js VIEW_CONTRACT_VERSION).
VIEW_CONTRACT_VERSION = 1

# Semver of this exporter, embedded in generated_by.
EXPORTER_VERSION = "5.3.12"

# Default export target — the pack layout places the sentinel repo next to the
# ColdStack repo. Overridable; falls back to the app dir when unwritable.
DEFAULT_EXPORT_PATH = Path(r"B:\Blockchain\lp-sentinel\strategy_view.json")

# Top-level keys the sentinel treats as reserved (never pool ids). Pool ids
# must never collide with these.
RESERVED_KEYS = (
    "view_version",
    "generated_by",
    "generated_at",
    "source_db",
    "fee_events",
    "capital_events",
)


class SentinelExporter:
    """Build and write strategy_view.json from coldtrack.db.

    Construction reads the DB (read-only); ``export()`` performs the atomic
    write. The export never raises for an empty/unconfigured registry — it
    emits the header + empty arrays and logs a warning.
    """

    def __init__(self, db: Any, export_path: Optional[Path] = None) -> None:
        """
        Args:
            db: ColdTrackDB instance (schema initialized).
            export_path: override for the output file. None → DEFAULT_EXPORT_PATH,
                with fallback to <app dir>/strategy_view.json if unwritable.
        """
        self.db = db
        self._requested_path = Path(export_path) if export_path else DEFAULT_EXPORT_PATH

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def export(self) -> Dict[str, Any]:
        """Build the view and write it atomically. Returns a result summary:
        { path, pools, fee_events, capital_events, generated_at, fallback }.
        Never raises for an empty registry.
        """
        view = self._build_view()
        target, used_fallback = self._resolve_path()
        result = {
            "path": str(target),
            "pools": self._pool_count,
            "fee_events": len(view["fee_events"]),
            "capital_events": len(view["capital_events"]),
            "generated_at": view["generated_at"],
            "fallback": used_fallback,
        }
        self._write_atomic(view, target)
        return result

    # ------------------------------------------------------------------
    # View assembly
    # ------------------------------------------------------------------

    def _build_view(self) -> Dict[str, Any]:
        """Assemble the full contract-v1 payload (in memory)."""
        generated_at = datetime.now(timezone.utc).isoformat()
        view: Dict[str, Any] = {
            "view_version": VIEW_CONTRACT_VERSION,
            "generated_by": f"ColdTrack Sentinel Export {EXPORTER_VERSION}",
            "generated_at": generated_at,
            "source_db": "coldtrack.db",
        }

        registry = self.db.get_sentinel_pools()
        self._pool_count = len(registry)
        if not registry:
            logger.warning(
                "Sentinel export: SENTINEL_POOLS is empty — emitting header "
                "plus empty event arrays only."
            )

        # Map account_id → pool_id for event tagging (position-tagged events).
        account_to_pool = {
            row["ACCOUNT_ID"]: row["POOL_ID"]
            for row in registry
            if row.get("ACCOUNT_ID") is not None
        }

        # fee_events — cumulative Σ per pool, also emitted as the event array.
        fee_sums = self._fee_sums_by_pool(account_to_pool)

        for row in registry:
            pool_id = row["POOL_ID"]
            config = self._parse_config(row.get("POSITION_CONFIG"))
            if pool_id == "KP" or pool_id.upper().startswith("KP"):
                # K&P section: KP container is emitted once, keyed "KP".
                continue
            view[pool_id] = self._build_pool_record(row, config, fee_sums)

        # KP container from registry rows whose id is KP / KP<n>.
        kp = self._build_kp_section(registry)
        if kp is not None:
            view["KP"] = kp

        view["fee_events"] = self._build_fee_events(account_to_pool)
        view["capital_events"] = self._build_capital_events(account_to_pool)
        return view

    def _build_pool_record(
        self,
        row: Dict[str, Any],
        config: Dict[str, Any],
        fee_sums: Dict[str, Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Compose one pool record: verbatim strategy.json fields from
        POSITION_CONFIG, with entry data refreshed from LP_POSITIONS and
        fee_snapshot from the cumulative Σ fee events (backward-compat display).
        """
        record: Dict[str, Any] = dict(config)  # verbatim strategy plumbing

        account_id = row.get("ACCOUNT_ID")
        lp = self._latest_lp_position(account_id) if account_id is not None else None

        cs: Dict[str, Any] = dict(record.get("current_strategy") or {})
        if lp:
            cs["lp_range_low"] = lp.get("RANGE_LOW")
            cs["lp_range_high"] = lp.get("RANGE_HIGH")
            cs["initial_investment_quote"] = lp.get("AMOUNT_A_ENTRY")
            cs["initial_investment_base"] = lp.get("AMOUNT_B_ENTRY")
            cs["total_usd_value_at_entry"] = lp.get("TOTAL_VALUE_USD_ENTRY")
            if lp.get("OPENED_DATE"):
                cs["start_date"] = lp["OPENED_DATE"]
        if row.get("SEASON") is not None:
            cs["season_id"] = row["SEASON"]
        cs.setdefault("type", row.get("POSITION_TYPE") or record.get("type"))
        record["current_strategy"] = cs

        record.setdefault("pool_group", row.get("GROUP_NAME"))
        record.setdefault("type", row.get("POSITION_TYPE"))

        snap = fee_sums.get(row["POOL_ID"], {"total": 0.0, "check_time": ""})
        record["fee_snapshot"] = {
            "check_time": snap["check_time"],
            "fees_earned_usd": round(snap["total"], 2),
        }
        record.setdefault(
            "risk_settings",
            {"stop_loss_pct": 0.05, "rsi_alert_high": 80, "rsi_alert_low": 20},
        )
        return record

    def _build_kp_section(
        self, registry: List[Dict[str, Any]]
    ) -> Optional[Dict[str, Any]]:
        """Compose the KP container. Positions come from SENTINEL_POOLS rows
        whose POOL_ID is 'KP<n>'; 'POSITION_CONFIG' is carried verbatim and an
        optional per-position 'entry' is injected from LP_POSITIONS. A registry
        row with POOL_ID == 'KP' may supply {label, wallet}."""
        label = "K&P"
        wallet = ""
        positions: List[Dict[str, Any]] = []

        for row in registry:
            pool_id = row["POOL_ID"]
            config = self._parse_config(row.get("POSITION_CONFIG"))
            if pool_id == "KP":
                label = config.get("label", label)
                wallet = config.get("wallet", wallet)
                continue
            if not pool_id.upper().startswith("KP"):
                continue
            pos = dict(config)  # verbatim monitor plumbing
            pos.setdefault("id", pool_id)
            lp = self._latest_lp_position(row.get("ACCOUNT_ID"))
            if lp:
                pos["entry"] = {
                    "usd": lp.get("TOTAL_VALUE_USD_ENTRY"),
                    "date": lp.get("OPENED_DATE"),
                    "token0_amt": lp.get("AMOUNT_A_ENTRY"),
                    "token1_amt": lp.get("AMOUNT_B_ENTRY"),
                    "fees_claimed_usd": lp.get("FEES_CLAIMED_USD")
                    if lp.get("FEES_CLAIMED_USD") is not None
                    else lp.get("FEES_EARNED_USD"),
                }
            positions.append(pos)

        if not positions and not wallet:
            return None
        return {"label": label, "wallet": wallet, "positions": positions}

    def _build_fee_events(
        self, account_to_pool: Dict[int, str]
    ) -> List[Dict[str, Any]]:
        """TRANSACTIONS WHERE TYPE='fee_harvest', position-tagged via the
        sentinel pool registry. One row per claim, dates ASC."""
        cur = self.db._conn.cursor()
        cur.execute(
            "SELECT * FROM TRANSACTIONS WHERE TYPE = 'fee_harvest' ORDER BY DATE ASC, ID ASC"
        )
        events: List[Dict[str, Any]] = []
        for r in self.db._rows_to_dicts(cur.fetchall()):
            pool_id = account_to_pool.get(r["ACCOUNT_ID"])
            if pool_id is None:
                continue
            events.append(
                {
                    "position_id": pool_id,
                    "date": r.get("DATE"),
                    "token0_amt": r.get("AMOUNT"),
                    "token1_amt": r.get("COUNTERPARTY_AMOUNT"),
                    "value_usd": r.get("VALUE_USD"),
                    "source": (r.get("CATEGORY") or "MANUAL").upper(),
                    "tx_hash": r.get("TX_HASH"),
                    "notes": r.get("NOTES") or "",
                }
            )
        return events

    def _build_capital_events(
        self, account_to_pool: Dict[int, str]
    ) -> List[Dict[str, Any]]:
        """TRANSACTIONS WHERE TYPE IN ('deposit','withdrawal'). Position-tagged
        when the account maps to a sentinel pool, else position_id null and the
        event feeds owner attribution only. Deposits → INJECTION."""
        cur = self.db._conn.cursor()
        cur.execute(
            """SELECT t.*, a.PORTFOLIO_ID AS PF_ID, a.DISPLAY_NAME AS OWNER
               FROM TRANSACTIONS t
               JOIN ACCOUNTS a ON a.ID = t.ACCOUNT_ID
               WHERE t.TYPE IN ('deposit', 'withdrawal')
               ORDER BY t.DATE ASC, t.ID ASC"""
        )
        pf_names = {p["ID"]: p["NAME"] for p in self.db.get_portfolios()}
        events: List[Dict[str, Any]] = []
        for r in self.db._rows_to_dicts(cur.fetchall()):
            pool_id = account_to_pool.get(r["ACCOUNT_ID"])
            events.append(
                {
                    "position_id": pool_id,
                    "portfolio": pf_names.get(r.get("PF_ID")),
                    "owner": r.get("OWNER"),
                    "date": r.get("DATE"),
                    "type": "INJECTION" if r["TYPE"] == "deposit" else "WITHDRAWAL",
                    "asset": r.get("ASSET"),
                    "amount": r.get("AMOUNT"),
                    "value_usd": r.get("VALUE_USD"),
                    "notes": r.get("NOTES") or "",
                }
            )
        return events

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _fee_sums_by_pool(
        self, account_to_pool: Dict[int, str]
    ) -> Dict[str, Dict[str, Any]]:
        """Cumulative Σ fee_harvest events per pool id → fee_snapshot display."""
        cur = self.db._conn.cursor()
        cur.execute(
            "SELECT ACCOUNT_ID, VALUE_USD, DATE FROM TRANSACTIONS "
            "WHERE TYPE = 'fee_harvest' ORDER BY DATE ASC, ID ASC"
        )
        out: Dict[str, Dict[str, Any]] = {}
        for row in cur.fetchall():
            pool_id = account_to_pool.get(row["ACCOUNT_ID"])
            if pool_id is None:
                continue
            bucket = out.setdefault(pool_id, {"total": 0.0, "check_time": ""})
            bucket["total"] += float(row["VALUE_USD"] or 0.0)
            bucket["check_time"] = row["DATE"] or bucket["check_time"]
        return out

    def _latest_lp_position(self, account_id: Optional[int]) -> Optional[Dict[str, Any]]:
        """Most recently opened LP position for an account (entry data source)."""
        if account_id is None:
            return None
        positions = self.db.get_lp_positions(account_id=account_id)
        return positions[0] if positions else None

    @staticmethod
    def _parse_config(raw: Optional[str]) -> Dict[str, Any]:
        """Parse the POSITION_CONFIG JSON blob; tolerate malformed/empty."""
        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except (json.JSONDecodeError, TypeError):
            logger.warning("Sentinel export: malformed POSITION_CONFIG ignored.")
            return {}

    def _resolve_path(self) -> tuple:
        """Pick the write target: requested path, else app-dir fallback."""
        requested = self._requested_path
        parent = requested.parent
        try:
            parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        if os.access(str(parent), os.W_OK):
            return requested, False
        app_dir = (
            Path(sys.executable).parent
            if getattr(sys, "frozen", False)
            else Path(__file__).parent.parent.parent
        )
        return app_dir / "strategy_view.json", True

    @staticmethod
    def _write_atomic(view: Dict[str, Any], target: Path) -> None:
        """Write tmp then os.replace — the sentinel's fs.watchFile only ever
        sees a complete file."""
        tmp = target.with_suffix(target.suffix + ".tmp")
        payload = json.dumps(view, indent=2)
        tmp.write_text(payload, encoding="utf-8")
        os.replace(str(tmp), str(target))


def export_sentinel_view(
    db: Any, export_path: Optional[Path] = None
) -> Dict[str, Any]:
    """Convenience wrapper: build + write the sentinel view, return summary."""
    return SentinelExporter(db, export_path).export()
