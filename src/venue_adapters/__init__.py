"""
Venue adapter package for ColdStack LP Engine.

Importing this module causes all adapters below to self-register with lp_engine.

Version: v5.2.5 (August 2026) - Added Orca (Solana) read-only adapter
"""

from .hyperliquid_adapter import HyperliquidAdapter  # noqa: F401
from .bsc_adapter import BSCAdapter  # noqa: F401
from .aerodrome_adapter import AerodromeAdapter  # noqa: F401
from .orca_adapter import OrcaAdapter  # noqa: F401
