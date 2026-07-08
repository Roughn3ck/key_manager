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
    wallet_address: Optional[str] = None  # the wallet that holds this position
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

    # v5.1: TVL + first deposit time (for card display)
    tvl_usd: Optional[float] = None  # total value locked in the vault
    first_deposit_time: Optional[datetime] = None  # vault's earliest performance data point (Vault age)
    user_deposit_time: Optional[datetime] = None  # user's personal deposit timestamp (Deposit age)

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

    def to_saved_dict(self) -> dict:
        """Serialize full vault snapshot for persistent storage.

        Stores all public data needed to render a card without a network call.
        The caller must re-encrypt the vault via KeyManager.save_encrypted_data().
        """
        return {
            "wallet_address": self.wallet_address or "",
            "vault_address": self.vault_address,
            "vault_name": self.vault_name,
            "venue": self.venue,
            "shares": self.shares,
            "share_price_usd": self.share_price_usd,
            "deposited_usd": self.deposited_usd,
            "current_value_usd": self.current_value_usd,
            "unrealized_pnl_usd": self.unrealized_pnl_usd,
            "unrealized_pnl_pct": self.unrealized_pnl_pct,
            "apr": self.apr,
            "tvl_usd": self.tvl_usd,
            "first_deposit_time": self.first_deposit_time.isoformat() if self.first_deposit_time else None,
            "user_deposit_time": self.user_deposit_time.isoformat() if self.user_deposit_time else None,
            "performance_history": self.performance_history,
            "date_saved": datetime.now(timezone.utc).isoformat(),
        }

    @classmethod
    def from_saved_dict(cls, data: dict) -> "VaultPosition":
        """Rehydrate a full VaultPosition from a saved snapshot dict.

        Restores all cached fields so the card can render without a network call.
        """
        # Parse first_deposit_time from ISO string
        fdt_str = data.get("first_deposit_time")
        fdt = None
        if fdt_str:
            try:
                fdt = datetime.fromisoformat(fdt_str)
            except (ValueError, TypeError):
                pass

        # Parse user_deposit_time from ISO string
        udt_str = data.get("user_deposit_time")
        udt = None
        if udt_str:
            try:
                udt = datetime.fromisoformat(udt_str)
            except (ValueError, TypeError):
                pass

        # Parse performance_history (may be absent in older saved entries)
        perf_hist = data.get("performance_history", [])
        if not isinstance(perf_hist, list):
            perf_hist = []

        return cls(
            position_id=f"hyperliquid:{data.get('vault_address', '')}",
            vault_address=data.get("vault_address", ""),
            vault_name=data.get("vault_name", "Unknown Vault"),
            venue=data.get("venue", "Hyperliquid Vault"),
            wallet_address=data.get("wallet_address", ""),
            shares=data.get("shares"),
            share_price_usd=data.get("share_price_usd"),
            deposited_usd=data.get("deposited_usd"),
            current_value_usd=data.get("current_value_usd"),
            unrealized_pnl_usd=data.get("unrealized_pnl_usd"),
            unrealized_pnl_pct=data.get("unrealized_pnl_pct"),
            apr=data.get("apr"),
            tvl_usd=data.get("tvl_usd"),
            first_deposit_time=fdt,
            user_deposit_time=udt,
            performance_history=perf_hist,
        )


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

        # Name: prefer vaultDetails (has "name" field), then equity, then fallback
        vault_name = self._get_str(details, ["name", "vaultName"], default=None)
        if not vault_name:
            vault_name = self._get_str(equity, ["name", "vaultName"], default=None)
        if not vault_name:
            vault_name = f"Vault {vault_address[:10]}..."

        pos = VaultPosition(
            position_id=f"hyperliquid:{vault_address}",
            vault_address=vault_address,
            vault_name=vault_name,
            wallet_address=wallet,
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
            # APR: prefer direct "apr" field from vaultDetails, fallback to computed
            direct_apr = self._get_float(details, ["apr", "aprPct", "annualizedRate"], default=None)
            if direct_apr is not None:
                # API returns APR as a fraction (e.g. 0.123 = 12.3%); convert to percentage
                if abs(direct_apr) < 1.0:
                    pos.apr = direct_apr * 100.0
                else:
                    pos.apr = direct_apr
            else:
                pos.apr = self._compute_apr(pos.performance_history)

            # Share price: prefer personal (current_value / shares),
            # fallback to vault-level (total_value / total_shares)
            total_value = self._get_float(
                details, ["totalValue", "equity", "vaultEquity"], default=None
            )
            if pos.shares and pos.current_value_usd and pos.shares > 0:
                pos.share_price_usd = pos.current_value_usd / pos.shares
            elif pos.total_vault_shares and pos.total_vault_shares > 0:
                if total_value:
                    pos.share_price_usd = total_value / pos.total_vault_shares

            # v5.1: TVL — 3-tier fallback:
            #   1. maxDistributable (vault's max distributable value)
            #   2. Sum of followers' vaultEquity
            #   3. totalValue/equity/vaultEquity
            #   4. Computed share_price * total_vault_shares
            max_dist = self._get_float(details, ["maxDistributable"], default=None)
            if max_dist is not None:
                pos.tvl_usd = max_dist
            else:
                # Try summing followers' vaultEquity
                followers = details.get("followers", [])
                follower_tvl = None
                if isinstance(followers, list) and followers:
                    follower_sum = 0.0
                    found_any = False
                    for f in followers:
                        if isinstance(f, dict):
                            ve = self._get_float(f, ["vaultEquity", "equity"], default=None)
                            if ve is not None:
                                follower_sum += ve
                                found_any = True
                    if found_any:
                        follower_tvl = follower_sum
                if follower_tvl is not None:
                    pos.tvl_usd = follower_tvl
                elif total_value is not None:
                    pos.tvl_usd = total_value
                elif (
                    pos.share_price_usd is not None
                    and pos.total_vault_shares is not None
                    and pos.total_vault_shares > 0
                ):
                    pos.tvl_usd = pos.share_price_usd * pos.total_vault_shares

            # v5.1: Vault age — earliest timestamp from performance history
            pos.first_deposit_time = self._extract_earliest_timestamp(
                pos.performance_history
            )

            # v5.1: Deposit age — user's personal deposit timestamp from followerState
            entry_ts = None
            follower_state = details.get("followerState", {})
            if isinstance(follower_state, dict):
                entry_ts = self._get_float(follower_state, ["vaultEntryTime", "entryTime"], default=None)
            if entry_ts is None:
                entry_ts = self._get_float(equity, ["vaultEntryTime", "entryTime"], default=None)
            if entry_ts is not None:
                try:
                    pos.user_deposit_time = datetime.fromtimestamp(entry_ts / 1000.0, tz=timezone.utc)
                except (OverflowError, OSError, ValueError):
                    pass

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
    def _flatten_portfolio_history(portfolio_history: List[Any]) -> List[Dict]:
        """Flatten Hyperliquid portfolio tuples into (timestamp, value) points.

        The vaultDetails API returns ``portfolio`` as a list of tuples:
        ``("allTime", {"accountValueHistory": [[ts, val], ...], "pnlHistory": [[ts, val], ...]})``

        This method extracts all (timestamp, value) pairs into a flat list of
        dicts with a ``type`` field indicating the source (accountValueHistory
        or pnlHistory).

        Args:
            portfolio_history: Raw portfolio data from vaultDetails (list of
                tuples or dicts).

        Returns:
            Flat list of ``{"timestamp": float, "value": float, "type": str}`` dicts.
        """
        points = []
        if not isinstance(portfolio_history, list):
            return points
        for entry in portfolio_history:
            if not isinstance(entry, (list, tuple)) or len(entry) < 2:
                continue
            timeframe, payload = entry[0], entry[1]
            if not isinstance(payload, dict):
                continue
            for hist_name in ("accountValueHistory", "pnlHistory"):
                hist = payload.get(hist_name)
                if not isinstance(hist, list):
                    continue
                for point in hist:
                    if isinstance(point, (list, tuple)) and len(point) >= 2:
                        try:
                            ts = float(point[0])
                            val = float(point[1])
                            points.append({"timestamp": ts, "value": val, "type": hist_name})
                        except (ValueError, TypeError):
                            continue
        return points

    @staticmethod
    def _extract_earliest_timestamp(
        portfolio_history: List[Any],
    ) -> Optional[datetime]:
        """Extract the earliest timestamp from performance history.

        Uses ``_flatten_portfolio_history()`` to handle the tuple format
        returned by the Hyperliquid vaultDetails API.

        Args:
            portfolio_history: Raw portfolio data from vaultDetails.

        Returns:
            A UTC ``datetime`` for the earliest data point, or ``None`` if
            no valid timestamp is found.
        """
        points = HyperliquidVaultTracker._flatten_portfolio_history(portfolio_history)
        if not points:
            return None

        earliest_ts = min(p["timestamp"] for p in points)

        # Hyperliquid timestamps are in milliseconds
        try:
            return datetime.fromtimestamp(earliest_ts / 1000.0, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None

    @staticmethod
    def _compute_apr(portfolio_history: List[Any]) -> Optional[float]:
        """Compute a simple APR from portfolio account-value history.

        Uses ``_flatten_portfolio_history()`` to extract (timestamp, value)
        points from the tuple format returned by the Hyperliquid vaultDetails
        API.  Prefers ``accountValueHistory`` for value-based APR.

        If insufficient data is available (fewer than 2 data points, or
        missing timestamps/values), return ``None``.

        Args:
            portfolio_history: Raw portfolio data from vaultDetails.

        Returns:
            Annualized percentage rate as a float (e.g. 12.4 for 12.4%),
            or ``None`` if it cannot be computed.
        """
        all_points = HyperliquidVaultTracker._flatten_portfolio_history(portfolio_history)
        if len(all_points) < 2:
            return None

        # Prefer accountValueHistory for value-based APR
        av_points = [p for p in all_points if p["type"] == "accountValueHistory"]
        points = av_points if len(av_points) >= 2 else all_points
        if len(points) < 2:
            return None

        # Sort by timestamp
        points.sort(key=lambda p: p["timestamp"])

        start_ts, start_val = points[0]["timestamp"], points[0]["value"]
        end_ts, end_val = points[-1]["timestamp"], points[-1]["value"]

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