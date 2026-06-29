"""
LP Engine - LP position aggregation + strategy for ColdStack.

Provides a standardized interface for fetching LP positions from any venue.
Each venue is implemented as a VenueAdapter in venue_adapters/.

Read-only by default. Write operations are accessed via get_writer(venue_key).
Offline by default (caller must check online_mode).

Version: v5.1 (June 2026) - VenueWriter integration + rebalance strategy
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from price_engine import PriceEngine


@dataclass
class LPPosition:
    """Standardized LP position data returned by every VenueAdapter."""

    position_id: str
    pool_id: Optional[str] = None
    venue: str = ""
    chain: str = ""
    pair: str = ""
    token_0: str = ""
    token_1: str = ""
    current_price: Optional[float] = None
    range_low: Optional[float] = None
    range_high: Optional[float] = None
    position_in_range_pct: Optional[float] = None
    deposit_amounts: Dict[str, float] = field(default_factory=dict)
    fees_earned: Dict[str, float] = field(default_factory=dict)
    fees_earned_usd: Optional[float] = None
    deposit_value_usd: Optional[float] = None
    current_value_usd: Optional[float] = None
    pnl_usd: Optional[float] = None
    pnl_pct: Optional[float] = None
    days_active: Optional[int] = None
    apy: Optional[float] = None
    status: str = "unknown"
    suggested_action: str = "Hold"
    health_emoji: str = "⚪"
    referral_code: Optional[str] = None
    referral_url: Optional[str] = None
    last_updated: str = ""
    raw_data: Any = None
    error: Optional[str] = None

    def __post_init__(self):
        if not self.last_updated:
            self.last_updated = datetime.now(timezone.utc).isoformat()


class VenueAdapter(ABC):
    """Base class all venue adapters must inherit from."""

    VENUE_KEY: str = ""
    CHAINS: List[str] = []

    @abstractmethod
    def can_handle(self, address_or_id: str, chain_hint: str = "") -> bool:
        """Return True if this adapter can parse the identifier."""
        raise NotImplementedError

    @abstractmethod
    def fetch_position(
        self,
        address_or_id: str,
        online_mode: bool = False,
        price_engine: Optional[PriceEngine] = None,
        chain_hint: str = "",
    ) -> LPPosition:
        """Fetch a single LP position by contract address or NFT ID."""
        raise NotImplementedError

    @abstractmethod
    def fetch_all_positions(
        self,
        wallet_address: str,
        online_mode: bool = False,
        price_engine: Optional[PriceEngine] = None,
    ) -> List[LPPosition]:
        """Fetch every LP position for a wallet address."""
        raise NotImplementedError

    @abstractmethod
    def fetch_fees_earned(
        self, position_id: str, online_mode: bool = False
    ) -> Dict[str, float]:
        """Return accumulated fees for a position."""
        raise NotImplementedError

    def referral_code(self) -> Optional[str]:
        """Optional referral code for fee discounts."""
        return None

    def referral_url(self) -> Optional[str]:
        """Optional referral URL."""
        return None

    def can_write(self) -> bool:
        """Return True if this venue has a writer implementation."""
        return False

    def get_writer(self):
        """Return a VenueWriter instance if can_write(), otherwise None."""
        return None


class OfflineError(Exception):
    """Raised when an adapter is called while ColdStack is offline."""

    pass


class StrategyEngine:
    """Read-only analysis engine for LP positions.

    No network access. Takes an LPPosition and returns status + suggestion.
    """

    SAFE_EDGE_BUFFER = 20.0  # pct from either edge considered safe
    NEAR_EDGE_BUFFER = 5.0   # pct from either edge triggers rebalance warning
    PROFIT_TAKE_THRESHOLD = 10.0  # fees > 10% of deposit value
    REBALANCE_EDGE_THRESHOLD = 20.0  # position drifted beyond 20% through range

    def analyze(self, position: LPPosition) -> LPPosition:
        """Analyze position and mutate status / suggestion / emoji."""
        pct = position.position_in_range_pct

        # Priority 1: out of range
        if pct is not None and (pct < 0 or pct > 100):
            position.status = "out_of_range"
            position.health_emoji = "🚨"
            position.suggested_action = "Out of Range — Action Required"
            return position

        # Priority 2: profit taking signal
        if self._should_withdraw(position):
            position.status = "profit_take"
            position.health_emoji = "💰"
            position.suggested_action = "Withdraw — Profit Taking Signal"
            return position

        # Priority 3: near edge
        if pct is not None and (pct < self.NEAR_EDGE_BUFFER or pct > (100 - self.NEAR_EDGE_BUFFER)):
            position.status = "near_edge"
            position.health_emoji = "⚠️"
            position.suggested_action = "Near Edge — Consider Rebalancing"
            return position

        # Priority 4: watch zone
        if pct is not None and (pct < self.SAFE_EDGE_BUFFER or pct > (100 - self.SAFE_EDGE_BUFFER)):
            position.status = "watch"
            position.health_emoji = "🟡"
            position.suggested_action = "Watch"
            return position

        # Priority 5: safe / inactive / unknown
        if position.range_low is None and position.range_high is None:
            position.status = "inactive"
            position.health_emoji = "⚪"
            position.suggested_action = "Hold — No Active Range"
        else:
            position.status = "safe"
            position.health_emoji = "🟢"
            position.suggested_action = "Hold"
        return position

    def _should_withdraw(self, position: LPPosition) -> bool:
        deposit = position.deposit_value_usd
        fees = position.fees_earned_usd
        if deposit and deposit > 0 and fees and fees > 0:
            return (fees / deposit) * 100 > self.PROFIT_TAKE_THRESHOLD
        return False

    def should_rebalance(self, position: LPPosition) -> tuple[bool, str]:
        """Return (should_rebalance, reason).

        Triggers:
        - Position < 20% through range (drifted to one edge)
        - Position > 80% through range (drifted to other edge)
        - Fees > 10% of deposit value (profit taking)
        """
        pct = position.position_in_range_pct
        if pct is not None and (pct < 0 or pct > 100):
            return True, "Position out of range"
        if pct is not None and pct < self.REBALANCE_EDGE_THRESHOLD:
            return True, f"Position at {pct:.1f}% through range — lower edge drifted"
        if pct is not None and pct > (100 - self.REBALANCE_EDGE_THRESHOLD):
            return True, f"Position at {pct:.1f}% through range — upper edge drifted"
        if self._should_withdraw(position):
            return True, "Fees exceed 10% of deposit value"
        return False, ""

    def compute_recentered_range(
        self,
        position: LPPosition,
        buffer_pct: float = 0.2,
        tick_spacing: int = 60,
    ) -> tuple[Optional[int], Optional[int]]:
        """Calculate new tick range centered on current price with buffer.

        Uses price → tick conversion. Returns (tick_lower, tick_upper).
        """
        if position.current_price is None or position.current_price <= 0:
            return None, None

        # We need decimals; fall back to standard token decimals if unavailable.
        decimals0 = 18
        decimals1 = 8
        if position.raw_data and isinstance(position.raw_data, dict):
            decimals0 = position.raw_data.get("decimals0", decimals0)
            decimals1 = position.raw_data.get("decimals1", decimals1)

        # Convert human price to raw tick space.
        human_price = position.current_price
        low_human = human_price * (1 - buffer_pct)
        high_human = human_price * (1 + buffer_pct)

        import math

        def _price_to_tick(price: float) -> int:
            raw = price * (10 ** (decimals1 - decimals0))
            return int(math.floor(math.log(raw, 1.0001)))

        low_tick = (_price_to_tick(low_human) // tick_spacing) * tick_spacing
        high_tick = (_price_to_tick(high_human) // tick_spacing) * tick_spacing

        # Ensure range actually brackets current tick.
        current_tick = _price_to_tick(human_price)
        if low_tick >= current_tick:
            low_tick -= tick_spacing
        if high_tick <= current_tick:
            high_tick += tick_spacing

        return low_tick, high_tick


# ---------------------------------------------------------------------------
# Adapter registry
# ---------------------------------------------------------------------------

_ADAPTERS: Dict[str, VenueAdapter] = {}


def register_adapter(adapter_class: type) -> type:
    """Class decorator that registers a VenueAdapter with the engine."""
    instance = adapter_class()
    if not instance.VENUE_KEY:
        raise ValueError(f"{adapter_class.__name__} must define VENUE_KEY")
    _ADAPTERS[instance.VENUE_KEY] = instance
    return adapter_class


def get_adapter(venue_key: str) -> Optional[VenueAdapter]:
    """Return a registered adapter by venue key."""
    return _ADAPTERS.get(venue_key)


def detect_venue(address_or_id: str, chain_hint: str = "") -> Optional[str]:
    """Auto-detect the venue for a given address/ID."""
    for key, adapter in _ADAPTERS.items():
        if adapter.can_handle(address_or_id, chain_hint):
            return key
    return None


def list_venues() -> List[str]:
    """Return all registered venue keys."""
    return sorted(_ADAPTERS.keys())


# ---------------------------------------------------------------------------
# LPEngine facade
# ---------------------------------------------------------------------------

class LPEngine:
    """High-level facade used by the GUI.

    online_mode mirrors the balance engine pattern. When False, all fetch
    methods raise OfflineError.
    """

    def __init__(
        self,
        online_mode: bool = False,
        price_engine: Optional[PriceEngine] = None,
    ):
        self.online_mode = online_mode
        self.price_engine = price_engine or PriceEngine()
        self.strategy_engine = StrategyEngine()
        self._writers: Dict[str, Any] = {}

    def _require_online(self):
        if not self.online_mode:
            raise OfflineError("Online mode is disabled. Enable Go Online to fetch LP positions.")

    def get_writer(self, venue_key: str, vault_password: Optional[str] = None):
        """Return a VenueWriter for a venue, constructing and unlocking it once."""
        adapter = get_adapter(venue_key)
        if not adapter or not adapter.can_write():
            return None
        writer = self._writers.get(venue_key)
        if writer is None:
            writer = adapter.get_writer()
            self._writers[venue_key] = writer
        if vault_password and not writer.is_available():
            writer.unlock({"password": vault_password})
        return writer

    def fetch_position(
        self,
        address_or_id: str,
        venue_key: Optional[str] = None,
        chain_hint: str = "",
    ) -> LPPosition:
        """Fetch a single position, auto-detecting venue if not supplied."""
        self._require_online()
        key = venue_key or detect_venue(address_or_id, chain_hint)
        if not key:
            pos = LPPosition(
                position_id=address_or_id,
                error="Unsupported address format or venue not detected.",
            )
            return self.strategy_engine.analyze(pos)
        adapter = get_adapter(key)
        if not adapter:
            pos = LPPosition(
                position_id=address_or_id,
                venue=key,
                error="Adapter not found for detected venue.",
            )
            return self.strategy_engine.analyze(pos)
        pos = adapter.fetch_position(
            address_or_id,
            online_mode=True,
            price_engine=self.price_engine,
            chain_hint=chain_hint,
        )
        return self.strategy_engine.analyze(pos)

    def fetch_all_positions(
        self, wallet_address: str, venue_key: Optional[str] = None, chain_hint: str = ""
    ) -> List[LPPosition]:
        """Fetch all positions for a wallet, auto-detecting venue if not supplied."""
        self._require_online()
        key = venue_key or detect_venue(wallet_address, chain_hint)
        if not key:
            return [
                self.strategy_engine.analyze(
                    LPPosition(
                        position_id=wallet_address,
                        error="Unsupported address format or venue not detected.",
                    )
                )
            ]
        adapter = get_adapter(key)
        if not adapter:
            return [
                self.strategy_engine.analyze(
                    LPPosition(
                        position_id=wallet_address,
                        venue=key,
                        error="Adapter not found for detected venue.",
                    )
                )
            ]
        positions = adapter.fetch_all_positions(
            wallet_address, online_mode=True, price_engine=self.price_engine
        )
        return [self.strategy_engine.analyze(p) for p in positions]

    def fetch_fees_earned(
        self, position_id: str, venue_key: str
    ) -> Dict[str, float]:
        """Return accumulated fees for a position at a specific venue."""
        self._require_online()
        adapter = get_adapter(venue_key)
        if not adapter:
            return {}
        return adapter.fetch_fees_earned(position_id, online_mode=True)

    def should_rebalance(self, position: LPPosition) -> tuple[bool, str]:
        """Strategy-level rebalance check."""
        return self.strategy_engine.should_rebalance(position)

    def compute_recentered_range(
        self, position: LPPosition, buffer_pct: float = 0.2, tick_spacing: int = 60
    ) -> tuple[Optional[int], Optional[int]]:
        """Compute a new range centered on current price."""
        return self.strategy_engine.compute_recentered_range(
            position, buffer_pct=buffer_pct, tick_spacing=tick_spacing
        )

    def refresh_prices(self) -> Dict[str, Dict[str, float]]:
        """Refresh the shared price cache."""
        if not self.online_mode:
            return {}
        return self.price_engine.fetch_prices(["usd", "aud"])


# Trigger adapter discovery when lp_engine is imported.
# The actual modules are imported in venue_adapters/__init__.py.
def discover_adapters():
    """Import venue_adapters package so @register_adapter decorators fire."""
    try:
        import venue_adapters  # noqa: F401
    except Exception:
        # In case venue_adapters is not on path yet
        pass


discover_adapters()
