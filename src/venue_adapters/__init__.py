"""
Venue adapter package for ColdStack LP Engine.

Importing this module causes all adapters below to self-register with lp_engine.

Version: v5.2.1 (August 2026) - Renamed Krystal adapter to BSC adapter
"""

from .hyperliquid_adapter import HyperliquidAdapter  # noqa: F401
from .bsc_adapter import BSCAdapter  # noqa: F401
