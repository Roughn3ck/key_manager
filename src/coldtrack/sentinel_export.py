"""ColdTrack Sentinel Export — materialize a MERGED ``strategy_view.json``.

A port of Kimi's reference tool ``kimi/coldtax/export_strategy_view.py`` into
ColdStack's embedded Python runtime. Kimi's tool remains the reference
implementation for the standalone flow; this adaptation runs Windows-natively
inside the PyInstaller EXE so the ColdTrack tab can emit the view the Argus
Sentinel consumes. Credit: Kimi (CFO) — original logic and schema design are hers.

Contract v1 (pinned by the sentinel's ``loadStrategyData()``):
  Reserved top-level keys (never pool ids):
    view_version, generated_by, generated_at, source_db,
    fee_events, capital_events, pool_groups
  Anything else is a pool record keyed by its sentinel position id ('G2', 'N1')
  or the 'KP' container ({ label, wallet, positions[] }).

Contract-format fixes this port applies (per Forge prompt rev 2):
  * ``fee_events[]`` / ``capital_events[]`` are emitted in **snake_case** with
    ``position_id`` as the sentinel pool-id **STRING** ('G2', 'KP1'), not raw SQL
    rows — this lights up the events pipeline (fees-as-events, owner attribution).
  * KP positions carry ``entry: { usd, date, token0_amt, token1_amt,
    fees_claimed_usd }`` from LP_POSITIONS — lights up K&P Net P&L enrichment.
  * ``view_version: 1`` (integer) matching the sentinel's contract version.

Reads ONLY coldtrack.db (no vault, no keys, no network). Registry seam is
``LP_POSITIONS`` + ``POOL_GROUPS``; events come from ``FEE_EVENTS`` /
``CAPITAL_EVENTS`` (FK → LP_POSITIONS.ID, mapped to the sentinel pool-id string
via ACCOUNTS.NAME). The write is an atomic tmp + ``os.replace`` swap so the
sentinel's fs.watchFile hot-reload never sees a partial read.

Stdlib only (sqlite3, json, os, argparse, datetime, logging, pathlib) — runs
inside the embedded interpreter.

Usage (module or function):
    python -m coldtrack.sentinel_export --db A.db --db B.db --out view.json
    coldtrack export            # via CLI (defaults + ARGUS_DB_PATH env)
"""
import argparse
import datetime
import json
import logging
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)

# Contract version the sentinel pins (argus_sentinel.js VIEW_CONTRACT_VERSION).
VIEW_CONTRACT_VERSION = 1
# Semver of this port, embedded in generated_by. Credit preserved.
EXPORTER_VERSION = "5.3.15"
GENERATED_BY = f"ColdTrack Sentinel Export {EXPORTER_VERSION} (port of kimi/coldtax)"

# Default merged-view sources: the Pack + K&P portfolio DBs (Kimi's layout).
_DEFAULT_BASE = Path(r"B:\OpenClaw\.openclaw\workspace\kimi\portfolios")
DEFAULT_DB_PATHS = [
    _DEFAULT_BASE / "the-pack-portfolio" / "coldtrack.db",
    _DEFAULT_BASE / "kitandpaul" / "coldtrack.db",
]
# Default view target (pack layout) — the sentinel's read-only input.
DEFAULT_EXPORT_PATH = Path(r"B:\Blockchain\lp-sentinel\strategy_view.json")

# The K&P wallet recorded in the view (Kimi's tool hardcodes this convention).
KP_WALLET = "0x8958Bd96896De55bFe31b1A6Eb2B280ebE098509"

# strategy.json consulted only for KP token0/token1 canonical order (Kimi's logic).
_STRATEGY_JSON = Path(r"B:\Blockchain\lp-sentinel\strategy.json")

RESERVED_KEYS = (
    "view_version", "generated_by", "generated_at", "source_db",
    "fee_events", "capital_events", "pool_groups",
)


# ----------------------------------------------------------------------
# Kimi's loaders (ported — logic preserved)
# ----------------------------------------------------------------------

def _load_pools(cur: sqlite3.Cursor) -> Dict[str, Dict[str, Any]]:
    """Genesis/SafetyNET-style pools from Executive Mind (lp_position accounts)."""
    pools: Dict[str, Dict[str, Any]] = {}
    rows = _rows_as_dicts(cur.execute("""
        SELECT lp.*, a.NAME AS account_name
        FROM LP_POSITIONS lp
        JOIN ACCOUNTS a ON a.ID = lp.ACCOUNT_ID
        JOIN PORTFOLIOS p ON p.ID = a.PORTFOLIO_ID
        WHERE p.NAME = 'Executive Mind' AND a.TYPE = 'lp_position'
        ORDER BY lp.ID
    """))
    for row in rows:
        key = (row["account_name"] or "").upper()  # g1 -> G1, n1 -> N1
        if not key:
            continue
        config = _parse_notes(row.get("NOTES"))
        live = "LP Live" if row.get("STATUS") == "active" else "Closed"
        pools[key] = {
            "pool_group": config.get("pool_group", ""),
            "type": "LP_POOL",
            "quote_asset": row.get("TOKEN_A"),
            "base_asset": row.get("TOKEN_B"),
            "symbol": config.get("symbol", ""),
            "lp_address": config.get("lp_address", ""),
            "status": live,
            "current_strategy": {
                "season_id": config.get("season_id"),
                "status": live,
                "type": "LP_POOL",
                "start_date": row.get("OPENED_DATE") or "",
                "lp_range_low": row.get("RANGE_LOW"),
                "lp_range_high": row.get("RANGE_HIGH"),
                "initial_investment_quote": row.get("AMOUNT_A_ENTRY"),
                "initial_investment_base": row.get("AMOUNT_B_ENTRY"),
                "total_usd_value_at_entry": row.get("TOTAL_VALUE_USD_ENTRY"),
                "venue": row.get("PLATFORM") or "",
                "network": row.get("CHAIN") or "",
            },
            "fee_snapshot": {"check_time": "", "fees_earned_usd": row.get("FEES_EARNED_USD") or 0},
            "risk_settings": config.get("risk_settings", {
                "stop_loss_pct": 0.05, "rsi_alert_high": 80, "rsi_alert_low": 20,
            }),
            "base_symbol": config.get("base_symbol", ""),
            "_lp_id": row.get("ID"),  # internal: for event position_id mapping
        }
    return pools


def _load_kp_positions(cur: sqlite3.Cursor) -> List[Dict[str, Any]]:
    """Active K&P positions (Kit & Paul portfolio, kitandpaul account).

    Token order: strategy.json token0/token1 is the CANONICAL onchain order;
    the DB's TOKEN_A/TOKEN_B uses quote-first and may be inverted — load the
    canonical order from strategy.json keyed by pool/position/address.
    Positions carry an ``entry`` block (contract requirement — lights up K&P
    Net P&L enrichment).
    """
    canonical: Dict[Any, Dict[str, Any]] = {}
    if _STRATEGY_JSON.exists():
        try:
            strat = json.loads(_STRATEGY_JSON.read_text(encoding="utf-8"))
            for p in strat.get("KP", {}).get("positions", []):
                canonical[p.get("pool")] = p
                canonical[p.get("position_id")] = p
                canonical[p.get("position_address")] = p
        except (json.JSONDecodeError, OSError):
            logger.warning("KP canonical: strategy.json unreadable — falling back to DB order.")

    rows = _rows_as_dicts(cur.execute("""
        SELECT lp.*
        FROM LP_POSITIONS lp
        JOIN ACCOUNTS a ON a.ID = lp.ACCOUNT_ID
        WHERE a.NAME = 'kitandpaul' AND lp.STATUS = 'active'
        ORDER BY lp.ID
    """))
    positions: List[Dict[str, Any]] = []
    for i, row in enumerate(rows, start=1):
        config = _parse_notes(row.get("NOTES"))
        pid_type = row.get("POSITION_ID_TYPE") or ""
        protocol = {"erc721": "projectx", "numeric_id": "aerodrome",
                    "solana_position": "orca"}.get(pid_type, "")
        canon = (canonical.get(row.get("TOKEN_ID"))
                 or canonical.get(row.get("POOL_ADDRESS"))
                 or canonical.get(row.get("POSITION_ADDRESS")))
        pos: Dict[str, Any] = {
            "id": f"KP{i}",
            "label": f"{row.get('POOL_NAME')} #{row.get('TOKEN_ID') or ''}".strip(),
            "venue": row.get("PLATFORM") or "",
            "protocol": protocol,
            "chain": row.get("CHAIN") or "",
            "position_id": row.get("TOKEN_ID"),
            "pool": row.get("POOL_ADDRESS"),
            "rpcs": config.get("rpcs", []),
            "token0": (canon.get("token0") if canon else None) or row.get("TOKEN_A"),
            "token0_decimals": (canon.get("token0_decimals") if canon else None),
            "token1": (canon.get("token1") if canon else None) or row.get("TOKEN_B"),
            "token1_decimals": (canon.get("token1_decimals") if canon else None),
            "fees": config.get("fees"),
        }
        if pid_type == "solana_position":
            pos["position_address"] = row.get("POSITION_ADDRESS")
            pos["whirlpool"] = config.get("whirlpool")
        if config.get("staked") is not None:
            pos["staked"] = config["staked"]
        if row.get("TICK_LOWER") is not None:
            pos["tick_lower"] = row["TICK_LOWER"]
            pos["tick_upper"] = row["TICK_UPPER"]
        # monitor plumbing the emit list used to drop (data lives in the NOTES config blob):
        # nfpm — Project X positions() call · liquidity — Aerodrome config-frozen position math
        if config.get("nfpm"):
            pos["nfpm"] = config["nfpm"]
        if config.get("liquidity"):
            pos["liquidity"] = config["liquidity"]
        # entry record — lights up K&P Net P&L in the sentinel (its enrichment keys off `entry`).
        # Amounts follow the CANONICAL token0/token1 order: the DB's TOKEN_A/TOKEN_B is
        # quote-first and may be inverted (Orca cbBTC/SOL vs canonical SOL/cbBTC).
        entry_usd = row.get("TOTAL_VALUE_USD_ENTRY")
        if entry_usd:
            amt_a, amt_b = row.get("AMOUNT_A_ENTRY"), row.get("AMOUNT_B_ENTRY")
            if canon and canon.get("token0") and not _same_token(canon.get("token0"), row.get("TOKEN_A")):
                amt_a, amt_b = amt_b, amt_a   # canonical token0 order is inverted vs TOKEN_A/TOKEN_B
            pos["entry"] = {
                "usd": entry_usd,
                "date": row.get("OPENED_DATE"),
                "token0_amt": amt_a,
                "token1_amt": amt_b,
                "fees_claimed_usd": row.get("FEES_CLAIMED_USD") or 0,
            }
        pos["_lp_id"] = row.get("ID")  # internal: event mapping
        positions.append(pos)
    return positions


def _load_groups(cur: sqlite3.Cursor) -> List[Dict[str, Any]]:
    return _rows_as_dicts(cur.execute("SELECT NAME, LABEL, ICON, NOTES FROM POOL_GROUPS"))


# ----------------------------------------------------------------------
# Contract-fix event loaders (raw SQL rows → snake_case, position_id string)
# ----------------------------------------------------------------------

def _load_fee_events(
    cur: sqlite3.Cursor, lp_to_position: Dict[Any, str]
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for r in _rows_as_dicts(cur.execute("SELECT * FROM FEE_EVENTS ORDER BY DATE, ID")):
        out.append({
            "position_id": lp_to_position.get(r.get("POSITION_ID")),
            "date": r.get("DATE"),
            "token0_amt": r.get("TOKEN_A_AMT"),
            "token1_amt": r.get("TOKEN_B_AMT"),
            "value_usd": r.get("VALUE_USD"),
            "source": (r.get("SOURCE") or "MANUAL").upper(),
            "tx_hash": r.get("TX_HASH"),
            "notes": r.get("NOTES") or "",
        })
    return out


def _load_capital_events(
    cur: sqlite3.Cursor, lp_to_position: Dict[Any, str]
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for r in _rows_as_dicts(cur.execute("""
        SELECT ce.*, p.NAME AS pf_name
        FROM CAPITAL_EVENTS ce
        JOIN ACCOUNTS a ON a.ID = ce.ACCOUNT_ID
        JOIN PORTFOLIOS p ON p.ID = a.PORTFOLIO_ID
        ORDER BY ce.DATE, ce.ID
    """)):
        out.append({
            "position_id": lp_to_position.get(r.get("POSITION_ID")),
            "portfolio": r.get("pf_name"),
            "owner": r.get("OWNER"),
            "date": r.get("DATE"),
            "type": (r.get("TYPE") or "").upper(),
            "asset": r.get("ASSET"),
            "amount": r.get("AMOUNT"),
            "value_usd": r.get("VALUE_USD"),
            "notes": r.get("NOTES") or "",
        })
    return out


# ----------------------------------------------------------------------
# Exporter
# ----------------------------------------------------------------------

class SentinelExporter:
    """Build and write the merged ``strategy_view.json``. Construction reads the
    DB(s) (read-only); ``export()`` performs the atomic write. Never raises for
    an empty registry — emits header + empty arrays and logs a warning."""

    def __init__(
        self,
        db_paths: Optional[Sequence[Any]] = None,
        export_path: Optional[Any] = None,
    ) -> None:
        """
        Args:
            db_paths: coldtrack.db paths (merged view). None → DEFAULT_DB_PATHS,
                or ``ARGUS_DB_PATH`` env (';'-separated) when set.
            export_path: view target. None → DEFAULT_EXPORT_PATH.
        """
        if db_paths is not None:
            self.db_paths = [Path(p) for p in db_paths]
        else:
            env = os.environ.get("ARGUS_DB_PATH", "").strip()
            self.db_paths = [Path(x) for x in env.split(";") if x.strip()] \
                if env else list(DEFAULT_DB_PATHS)
        self.export_path = Path(export_path) if export_path else DEFAULT_EXPORT_PATH

    def export(self) -> Dict[str, Any]:
        """Build the view and write it atomically. Returns a result summary."""
        view = self._build_view()
        target = self.export_path
        self._write_atomic(view, target)
        return {
            "path": str(target),
            "pools": self._pool_count,
            "kp_positions": self._kp_count,
            "fee_events": len(view["fee_events"]),
            "capital_events": len(view["capital_events"]),
            "pool_groups": len(view["pool_groups"]),
            "generated_at": view["generated_at"],
            "dbs": [str(p) for p in self.db_paths],
        }

    # ------------------------------------------------------------------

    def _build_view(self) -> Dict[str, Any]:
        view: Dict[str, Any] = {
            "view_version": VIEW_CONTRACT_VERSION,
            "generated_by": GENERATED_BY,
            "generated_at": datetime.datetime.now(datetime.timezone.utc)
            .isoformat().replace("+00:00", "Z"),
            "source_db": "coldtrack.db",
            "pool_groups": [],
            "fee_events": [],
            "capital_events": [],
        }
        self._pool_count = 0
        self._kp_count = 0
        seen_groups: Dict[str, bool] = {}
        loaded_any = False

        for db_path in self.db_paths:
            if not db_path.exists():
                logger.warning("Sentinel export: DB not found, skipped: %s", db_path)
                continue
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            try:
                pools = _load_pools(cur)
                kp_positions = _load_kp_positions(cur)

                # Map LP_POSITIONS.ID → sentinel pool-id string for events.
                lp_to_pos: Dict[Any, str] = {}
                for pid, rec in pools.items():
                    lp_key = rec.pop("_lp_id", None)
                    if lp_key is not None:
                        lp_to_pos[lp_key] = pid
                for pos in kp_positions:
                    lp_key = pos.pop("_lp_id", None)
                    if lp_key is not None and "id" in pos:
                        lp_to_pos[lp_key] = pos["id"]

                for k, v in pools.items():
                    view[k] = v
                if kp_positions:
                    view["KP"] = {
                        "label": "K&P",
                        "wallet": KP_WALLET,
                        "positions": kp_positions,
                    }
                    self._kp_count += len(kp_positions)
                self._pool_count += len(pools)

                for g in _load_groups(cur):
                    if g["NAME"] not in seen_groups:
                        seen_groups[g["NAME"]] = True
                        view["pool_groups"].append(g)

                view["fee_events"].extend(_load_fee_events(cur, lp_to_pos))
                view["capital_events"].extend(_load_capital_events(cur, lp_to_pos))
                loaded_any = True
            finally:
                conn.close()

        if not loaded_any:
            logger.warning(
                "Sentinel export: no source DBs readable — emitting header + empty arrays."
            )
        return view

    @staticmethod
    def _write_atomic(view: Dict[str, Any], target: Path) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_text(json.dumps(view, indent=2), encoding="utf-8")
        os.replace(str(tmp), str(target))


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------

def _rows_as_dicts(cur: sqlite3.Cursor) -> List[Dict[str, Any]]:
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _same_token(x: Any, y: Any) -> bool:
    """Alias-tolerant token match: WHYPE/HYPE, WETH/ETH wrap aliases compare equal.

    Guard for the canonical-token0 inversion check: a 'W'-prefixed wrap alias of
    the same underlying asset must NOT false-trigger the token0/token1 swap.
    (from kimi/coldtax/export_strategy_view.py)
    """
    if not x or not y:
        return False
    x, y = str(x).upper(), str(y).upper()
    return x == y or (x.startswith("W") and x[1:] == y) or (y.startswith("W") and y[1:] == x)


def _parse_notes(raw: Optional[str]) -> Dict[str, Any]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def export_sentinel_view(
    db_paths: Optional[Sequence[Any]] = None,
    export_path: Optional[Any] = None,
) -> Dict[str, Any]:
    """Convenience wrapper: build + write the merged view, return summary."""
    return SentinelExporter(db_paths, export_path).export()


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="coldtrack export",
        description="Materialize merged strategy_view.json (ColdTrack → Sentinel).",
    )
    parser.add_argument(
        "--db", action="append", dest="dbs", metavar="PATH",
        help="coldtrack.db path (repeatable, or ';'-separated). "
             "Defaults to the Pack + K&P DBs or $ARGUS_DB_PATH.",
    )
    parser.add_argument(
        "--out", dest="out", default=None, metavar="PATH",
        help=f"View target (default: {DEFAULT_EXPORT_PATH})",
    )
    args = parser.parse_args(argv)

    db_paths: List[str] = []
    if args.dbs:
        for chunk in args.dbs:
            db_paths.extend([p for p in str(chunk).split(";") if p.strip()])

    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    result = export_sentinel_view(db_paths or None, args.out)
    print(f"Wrote {result['path']}")
    print(f"  view_version: {VIEW_CONTRACT_VERSION}")
    print(f"  pools: {result['pools']}  KP positions: {result['kp_positions']}")
    print(f"  pool_groups: {result['pool_groups']}  "
          f"fee_events: {result['fee_events']}  capital_events: {result['capital_events']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
