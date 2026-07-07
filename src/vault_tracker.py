"""
ColdStack v5.1 — Hyperliquid Vault Tracker (read-only).

Fetches user vault equity positions from the Hyperliquid L1 API and
enriches them with vault-level details (share price, APR, performance history).

Architecture:
    - Read-only. No signing, no deposits, no withdrawals.
    - Offline by default. Returns empty list / None when online_mode=False.
    - No external dependencies. Uses stdlib urllib.request + json only.
    - Defensive field parsing with alias fallbacks (Hyperliquid field names
      vary slightly between docs/providers).
    - Hyperliquid-specific for v5.1 (no ABC — per product-owner decision).

API endpoints (both POST to https://api.hyperliquid.xyz/info):
    1. userVaultEquities  — list of user's vault positions
    2. vaultDetails       — vault metadata + performance history

Version: v5.1 (July 2026)
"""

import json
import urllib.request
import urllib.error
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class VaultPosition:
    """Standardized view of a user's position in a single Hyperliquid vault.

    Fields are populated from ``userVaultEquities`` (per-user) and enriched
    with ``vaultDetails`` (vault-level metadata).  All monetary values are
    in USD unless otherwise noted.
    """

    position_id: str  # e.g. "hyperliquid:<vault_address>"
    vault_address: str  # 0x... vault contract
    vault_name: str  # human-readable name
    chain: str = "Hyperliquid"
    venue: str = "Hyperliquid Vault"

    # Share data
    shares: Optional[float] = None
    share_price_usd: Optional[float] = None
    total_vault_shares: Optional[float] = None

    # Value data
    deposited_usd: Optional[float] = None
    current_value_usd: Optional[float] = None
    unrealized_pnl_usd: Optional[float] = None
    unrealized_pnl_pct: Optional[float] = None

    # Yield / performance
    apr: Optional[float] = None  # annual percentage rate, if computable
    apy: Optional[float] = None  # annual percentage yield, if computable
    performance_history: List[Dict] = field(default_factory=list)

    # Deposit / withdrawal summary (v5.1: summary only)
    deposit_count: int = 0
    withdrawal_count: int = 0
    total_deposited_usd: Optional[float] = None
    total_withdrawn_usd: Optional[float] = None

    # Metadata
    vault_leader: Optional[str] = None
    vault_description: Optional[str] = None
    raw_data: Optional[Dict] = None
    error: Optional[str] = None
    last_updated: str = ""

    def __post_init__(self) -> None:
        if not self.last_updated:
            self.last_updated = datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Tracker
# ---------------------------------------------------------------------------


class HyperliquidVaultTracker:
    """Read-only tracker for Hyperliquid vault positions.

    Usage::

        tracker = HyperliquidVaultTracker(online_mode=True)
        positions = tracker.fetch_positions("0x...")
    """

    INFO_URL = "https://api.hyperliquid.xyz/info"
    TIMEOUT = 15

    def __init__(self, online_mode: bool = False) -> None:
        """Initialise the tracker.

        Args:
            online_mode: When ``False`` (default), all fetch methods return
                empty results without making network requests.
        """
        self.online_mode = online_mode

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def fetch_positions(self, wallet_address: str) -> List[VaultPosition]:
        """Return all vault positions for *wallet_address*.

        Calls ``userVaultEquities`` first, then enriches each position with
        ``vaultDetails``.

        Args:
            wallet_address: Hyperliquid wallet address (0x...).

        Returns:
            List of :class:`VaultPosition` objects.  Empty list when offline
            or when the wallet has no vault positions.
        """
        if not self.online_mode:
            return []

        try:
            equities = self._post(
                {"type": "userVaultEquities", "user": wallet_address}
            )
        except Exception as exc:
            return [VaultPosition(
                position_id="hyperliquid:error",
                vault_address="",
                vault_name="Error",
                error=f"userVaultEquities failed: {exc}",
            )]

        if not isinstance(equities, list):
            return []

        positions: List[VaultPosition] = []
        for equity in equities:
            if not isinstance(equity, dict):
                continue

            vault_address = self._extract_vault_address(equity)
            if not vault_address:
                continue

            # Enrich with vaultDetails
            details: Any = {}
            try:
                details = self._post(
                    {
                        "type": "vaultDetails",
                        "vaultAddress": vault_address,
                        "user": wallet_address,
                    }
                )
                if not isinstance(details, dict):
                    details = {}
            except Exception as exc:
                # Build position with error note but don't skip — we still have equity data
                pos = self._build_position(equity, {}, wallet_address)
                pos.error = f"vaultDetails failed: {exc}"
                positions.append(pos)
                continue

            positions.append(self._build_position(equity, details, wallet_address))

        return positions

    def fetch_single_position(
        self, wallet_address: str, vault_address: str
    ) -> Optional[VaultPosition]:
        """Return a single vault position for *vault_address*.

        Calls ``vaultDetails`` only (no ``userVaultEquities`` scan).

        Args:
            wallet_address: Hyperliquid wallet address.
            vault_address: Vault contract address (0x...).

        Returns:
            A :class:`VaultPosition` or ``None`` when offline.
        """
        if not self.online_mode:
            return None

        try:
            details = self._post(
                {
                    "type": "vaultDetails",
                    "vaultAddress": vault_address,
                    "user": wallet_address,
                }
            )
        except Exception as exc:
            return VaultPosition(
                position_id=f"hyperliquid:{vault_address}",
                vault_address=vault_address,
                vault_name="Error",
                error=f"vaultDetails failed: {exc}",
            )

        if not isinstance(details, dict):
            details = {}

        return self._build_position({}, details, wallet_address, fallback_vault_addr=vault_address)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _post(self, payload: Dict[str, Any]) -> Any:
        """POST a JSON payload to the Hyperliquid /info endpoint.

        Mirrors the pattern in ``venue_adapters/hyperliquid_adapter.py``.

        Args:
            payload: Dict to be JSON-encoded as the request body.

        Returns:
            Parsed JSON response (dict, list, or scalar).

        Raises:
            urllib.error.URLError: on network failure.
            ValueError: on invalid JSON response.
        """
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self.INFO_URL,
            data=data,
            headers={
                "Content-Type": "application/json",
                "User-Agent": "ColdStack/5.1",
            },
        )
        with urllib.request.urlopen(req, timeout=self.TIMEOUT) as response:
            return json.loads(response.read().decode("utf-8"))

    def _build_position(
        self,
        equity: Dict,
        details: Dict,
        wallet: str,
        fallback_vault_addr: Optional[str] = None,
    ) -> VaultPosition:
        """Build a :class:`VaultPosition` from userVaultEquities + vaultDetails.

        Args:
            equity: Per-user equity data from ``userVaultEquities``.
            details: Vault-level metadata from ``vaultDetails``.
            wallet: The wallet address being queried.
            fallback_vault_addr: Used when equity and details don't contain
                a vault address (single-position fetch).

        Returns:
            A populated :class:`VaultPosition`.
        """
        vault_address = self._extract_vault_address(equity)
        if not vault_address and isinstance(details, dict):
            vault_address = self._extract_vault_address(details)
        if not vault_address:
            vault_address = fallback_vault_addr or wallet

        source = equity if equity else details

        pos = VaultPosition(
            position_id=f"hyperliquid:{vault_address}",
            vault_address=vault_address,
            vault_name=self._get_str(
                source, ["vaultName", "name"], default=vault_address
            ),
            shares=self._get_float(equity, ["shares", "shareBalance"], default=None),
            deposited_usd=self._get_float(
                equity, ["equity", "deposited", "depositedAmount"], default=None
            ),
            current_value_usd=self._get_float(
                equity, ["currentValue", "value", "totalValue"], default=None
            ),
            unrealized_pnl_usd=self._get_float(
                equity, ["unrealizedPnl", "pnl", "profit"], default=None
            ),
            unrealized_pnl_pct=self._get_float(
                equity, ["pnlPercent", "pnlPct", "roi"], default=None
            ),
            raw_data={"equity": equity, "details": details},
        )

        # Enrich from vaultDetails when available
        if isinstance(details, dict) and details:
            pos.vault_leader = self._get_str(
                details, ["leader", "leaderAddress", "manager"], default=None
            )
            pos.total_vault_shares = self._get_float(
                details, ["totalShares", "shares", "totalSupply"], default=None
            )
            pos.performance_history = self._get_list(
                details, ["portfolio", "performance", "history"], default=[]
            )
            pos.apr = self._compute_apr(pos.performance_history)

            # Share price: prefer personal (current_value / shares),
            # fallback to vault-level (total_value / total_shares)
            if pos.shares and pos.current_value_usd and pos.shares > 0:
                pos.share_price_usd = pos.current_value_usd / pos.shares
            elif pos.total_vault_shares and pos.total_vault_shares > 0:
                total_value = self._get_float(
                    details, ["totalValue", "equity", "vaultEquity"], default=None
                )
                if total_value:
                    pos.share_price_usd = total_value / pos.total_vault_shares

            # Fallback P&L if not provided by userVaultEquities
            if (
                pos.unrealized_pnl_usd is None
                and pos.current_value_usd is not None
                and pos.deposited_usd is not None
            ):
                pos.unrealized_pnl_usd = pos.current_value_usd - pos.deposited_usd
            if (
                pos.unrealized_pnl_pct is None
                and pos.unrealized_pnl_usd is not None
                and pos.deposited_usd
                and pos.deposited_usd > 0
            ):
                pos.unrealized_pnl_pct = (
                    pos.unrealized_pnl_usd / pos.deposited_usd
                ) * 100.0

        # Also compute fallback P&L from equity alone if details were empty
        if (
            pos.unrealized_pnl_usd is None
            and pos.current_value_usd is not None
            and pos.deposited_usd is not None
        ):
            pos.unrealized_pnl_usd = pos.current_value_usd - pos.deposited_usd
        if (
            pos.unrealized_pnl_pct is None
            and pos.unrealized_pnl_usd is not None
            and pos.deposited_usd
            and pos.deposited_usd > 0
        ):
            pos.unrealized_pnl_pct = (
                pos.unrealized_pnl_usd / pos.deposited_usd
            ) * 100.0

        return pos

    # ------------------------------------------------------------------
    # Defensive parsing helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _get_float(
        data: Dict, keys: List[str], default: Optional[float] = None
    ) -> Optional[float]:
        """Try each key in *keys* on *data*; return the first as ``float()``.

        All Hyperliquid numeric values are strings, so we parse defensively.
        """
        if not isinstance(data, dict):
            return default
        for key in keys:
            if key in data and data[key] is not None:
                try:
                    val = data[key]
                    if isinstance(val, str):
                        val = val.strip()
                        if not val:
                            continue
                    return float(val)
                except (ValueError, TypeError):
                    continue
        return default

    @staticmethod
    def _get_str(
        data: Dict, keys: List[str], default: Optional[str] = None
    ) -> Optional[str]:
        """Try each key in *keys* on *data*; return the first as ``str``."""
        if not isinstance(data, dict):
            return default
        for key in keys:
            if key in data and data[key] is not None:
                val = str(data[key]).strip()
                if val:
                    return val
        return default

    @staticmethod
    def _get_list(
        data: Dict, keys: List[str], default: Optional[list] = None
    ) -> List:
        """Try each key in *keys* on *data*; return the first as a list."""
        if default is None:
            default = []
        if not isinstance(data, dict):
            return default
        for key in keys:
            if key in data and data[key] is not None:
                val = data[key]
                if isinstance(val, list):
                    return val
                # Some APIs wrap in a dict with "data" key
                if isinstance(val, dict) and "data" in val:
                    inner = val["data"]
                    if isinstance(inner, list):
                        return inner
        return default

    @staticmethod
    def _extract_vault_address(data: Dict) -> Optional[str]:
        """Extract the vault contract address from a response dict."""
        if not isinstance(data, dict):
            return None
        for key in ("vaultAddress", "vault", "address"):
            val = data.get(key)
            if val and isinstance(val, str) and val.strip():
                return val.strip()
        return None

    @staticmethod
    def _compute_apr(portfolio_history: List[Dict]) -> Optional[float]:
        """Compute a simple APR from portfolio account-value history.

        The ``portfolio`` array from ``vaultDetails`` contains timeframes
        with ``accountValue`` fields.  We compute the return over the
        available period and annualize it.

        If insufficient data is available (fewer than 2 data points, or
        missing timestamps/values), return ``None`` — the UI should show
        "APR: see Hyperliquid" rather than fabricating a number.

        Args:
            portfolio_history: List of timeframe dicts, each expected to
                contain ``accountValue`` (string or float) and optionally
                a timestamp field.

        Returns:
            Annualized percentage rate as a float (e.g. 12.4 for 12.4%),
            or ``None`` if it cannot be computed.
        """
        if not isinstance(portfolio_history, list) or len(portfolio_history) < 2:
            return None

        # Extract (timestamp, account_value) pairs
        points: List[tuple] = []
        for entry in portfolio_history:
            if not isinstance(entry, dict):
                continue
            # Try common value keys
            value = None
            for key in ("accountValue", "value", "equity"):
                if key in entry and entry[key] is not None:
                    try:
                        value = float(entry[key])
                        break
                    except (ValueError, TypeError):
                        continue
            if value is None:
                continue

            # Try common timestamp keys
            ts = None
            for key in ("time", "timestamp", "ts"):
                if key in entry and entry[key] is not None:
                    try:
                        ts = float(entry[key])
                        break
                    except (ValueError, TypeError):
                        continue
            if ts is None:
                continue

            points.append((ts, value))

        if len(points) < 2:
            return None

        # Sort by timestamp
        points.sort(key=lambda p: p[0])

        start_ts, start_val = points[0]
        end_ts, end_val = points[-1]

        if start_val <= 0 or end_ts <= start_ts:
            return None

        # Total return over the period
        total_return = (end_val - start_val) / start_val

        # Elapsed time in seconds (Hyperliquid timestamps are in ms)
        elapsed_seconds = (end_ts - start_ts) / 1000.0
        if elapsed_seconds <= 0:
            return None

        # Annualize: scale to 365 days
        seconds_per_year = 365.0 * 24 * 60 * 60
        years = elapsed_seconds / seconds_per_year
        if years <= 0:
            return None

        apr = (total_return / years) * 100.0

        # Sanity check — if APR is absurd (>1000% or <-100%), the data is
        # likely noisy or the timestamps are in seconds not milliseconds.
        if abs(apr) > 1000.0:
            # Try seconds instead of ms
            elapsed_seconds = end_ts - start_ts
            if elapsed_seconds <= 0:
                return None
            years = elapsed_seconds / seconds_per_year
            if years <= 0:
                return None
            apr = (total_return / years) * 100.0
            if abs(apr) > 1000.0:
                return None

        return apr