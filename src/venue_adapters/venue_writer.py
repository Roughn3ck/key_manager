"""
Venue Writer - Abstract base class for ColdStack LP write operations.

VenueWriter defines the standard interface every write-capable venue must
implement. It is intentionally generic across chains and signing systems:
- ECDSA venues (HyperEVM, BSC, Base, Arbitrum) use the vault agent.
- Ed25519 venues (Solana / Orca) will need a different signer adapter.
- SUI, Cardano and XRPL venues need their own implementations later.

Version: v5.1 (June 2026)
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class SwapParams:
    """Parameters for a single-pool exact-input swap."""
    account: str
    token_in: str
    token_out: str
    amount_in: float
    fee: int = 3000
    slippage_pct: float = 0.0
    deadline_seconds: int = 1200


@dataclass
class OpenPositionParams:
    """Parameters to open a new concentrated-liquidity position."""
    account: str
    token0: str
    token1: str
    fee: int
    tick_lower: int
    tick_upper: int
    amount0: float
    amount1: float
    slippage_pct: float = 0.0
    deadline_seconds: int = 1200


@dataclass
class IncreaseLiquidityParams:
    """Parameters to add liquidity to an existing position."""
    account: str
    position_id: str
    amount0: float
    amount1: float
    slippage_pct: float = 0.0
    deadline_seconds: int = 1200


@dataclass
class DecreaseLiquidityParams:
    """Parameters to remove liquidity from an existing position."""
    account: str
    position_id: str
    liquidity: int
    slippage_pct: float = 0.0
    deadline_seconds: int = 1200


@dataclass
class CollectFeesParams:
    """Parameters to collect accrued fees for a position."""
    account: str
    position_id: str
    recipient: Optional[str] = None


@dataclass
class RebalanceParams:
    """Parameters to rebalance an existing position into a new range."""
    account: str
    position_id: str
    new_tick_lower: int
    new_tick_upper: int
    slippage_pct: float = 0.0
    deadline_seconds: int = 1200


class VenueWriter(ABC):
    """Abstract write interface for a venue.

    Concrete implementations live in venue-specific writer modules (e.g.
    hyperliquid_writer.py). Each writer is responsible for its own signing,
    nonce management and broadcast path.
    """

    VENUE_KEY: str = ""

    @abstractmethod
    def is_available(self) -> bool:
        """Return True if the writer's signer / backend is reachable."""
        raise NotImplementedError

    @abstractmethod
    def unlock(self, credentials: Dict[str, Any]) -> bool:
        """Unlock the signer with the supplied credentials (password, key, etc.)."""
        raise NotImplementedError

    @abstractmethod
    def wrap_native(self, account: str, amount: float) -> str:
        """Wrap the chain's native gas token into its wrapped ERC-20 form."""
        raise NotImplementedError

    @abstractmethod
    def unwrap_native(self, account: str, amount: float) -> str:
        """Unwrap the wrapped native token back to native gas token."""
        raise NotImplementedError

    @abstractmethod
    def approve(self, account: str, token: str, spender: str, amount: float) -> str:
        """Approve a spender to pull up to ``amount`` of ``token``."""
        raise NotImplementedError

    @abstractmethod
    def swap(self, params: SwapParams) -> str:
        """Execute a single-pool exact-input swap. Returns TX hash."""
        raise NotImplementedError

    @abstractmethod
    def open_position(self, params: OpenPositionParams) -> Tuple[str, Optional[int]]:
        """Open a new LP position. Returns (tx_hash, new_position_id)."""
        raise NotImplementedError

    @abstractmethod
    def increase_liquidity(self, params: IncreaseLiquidityParams) -> str:
        """Add liquidity to an existing position. Returns TX hash."""
        raise NotImplementedError

    @abstractmethod
    def decrease_liquidity(self, params: DecreaseLiquidityParams) -> str:
        """Remove liquidity from an existing position. Returns TX hash."""
        raise NotImplementedError

    @abstractmethod
    def collect_fees(self, params: CollectFeesParams) -> str:
        """Collect accrued fees for a position. Returns TX hash."""
        raise NotImplementedError

    def close_position(self, position_id: str, account: str) -> List[str]:
        """Default close flow: decreaseLiquidity(100%) + collect().

        Venues may override if their architecture differs.
        """
        raise NotImplementedError

    @abstractmethod
    def rebalance(self, params: RebalanceParams) -> List[str]:
        """Full rebalance flow: close -> optional swap -> open. Returns TX hashes."""
        raise NotImplementedError
