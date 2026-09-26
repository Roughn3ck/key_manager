"""ColdStack v5.3.20 - Cetus (Sui) writer stub (READ-ONLY PHASE).

Cetus writes (close / collect / compound / rebalance / open) require Sui
Programmable Transaction Block (PTB) construction, BCS serialization and Sui
intent signing through the key-manager agent. The agent currently exposes raw
Ed25519 signing (the Solana path) but has NO Sui PTB/BCS serializer or intent
signing path, so writes land in Phase 2.

This stub reserves the :class:`VenueWriter` interface and surfaces a clear
message; :meth:`CetusAdapter.can_write` returns False so the read-only phase
never routes a write here.
"""
from typing import Any, Dict, List, Optional, Tuple

from .venue_writer import (
    VenueWriter, SwapParams, OpenPositionParams, IncreaseLiquidityParams,
    DecreaseLiquidityParams, CollectFeesParams, CompoundFeesParams, RebalanceParams,
)

PHASE2_MESSAGE = "Cetus writes land in Phase 2"


class CetusPhase2Error(NotImplementedError):
    """Raised by the Cetus writer stub until Phase 2 lands."""


def _phase2(*_args: Any, **_kwargs: Any) -> Any:
    raise CetusPhase2Error(PHASE2_MESSAGE)


class CetusWriter(VenueWriter):
    """Phase-2 placeholder for Cetus (Sui) writes."""

    VENUE_KEY = "cetus"

    def is_available(self) -> bool:
        return False

    def unlock(self, credentials: Dict[str, Any]) -> bool:
        return _phase2()

    def wrap_native(self, account: str, amount: float) -> str:
        return _phase2()

    def unwrap_native(self, account: str, amount: float) -> str:
        return _phase2()

    def approve(self, account: str, token: str, spender: str, amount: float) -> str:
        return _phase2()

    def swap(self, params: SwapParams) -> str:
        return _phase2()

    def open_position(self, params: OpenPositionParams) -> Tuple[str, Optional[int]]:
        return _phase2()

    def increase_liquidity(self, params: IncreaseLiquidityParams) -> str:
        return _phase2()

    def decrease_liquidity(self, params: DecreaseLiquidityParams) -> str:
        return _phase2()

    def collect_fees(self, params: CollectFeesParams) -> str:
        return _phase2()

    def compound_fees(self, params: CompoundFeesParams) -> List[str]:
        return _phase2()

    def close_position(self, position_id: str, account: str) -> List[str]:
        return _phase2()

    def rebalance(self, params: RebalanceParams) -> List[str]:
        return _phase2()
