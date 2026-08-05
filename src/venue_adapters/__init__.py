"""
Venue adapter package for ColdStack LP Engine.

Importing this module causes all adapters below to self-register with lp_engine.

Version: v5.2-p1 (July 2026) - Added Krystal adapter skeleton
"""

from .hyperliquid_adapter import HyperliquidAdapter  # noqa: F401
from .krystal_adapter import KrystalAdapter  # noqa: F401
