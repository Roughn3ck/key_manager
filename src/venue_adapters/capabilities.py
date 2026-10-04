"""Venue capability matrix for ColdStack LP action-button rendering.

v5.3.30 (Forge slice): this module is intentionally small and standalone. It
maps a venue key (or a saved-pool venue name) to the operations the GUI is
allowed to show for that venue. The rules:

  - If the operation is not implemented, the button is NOT rendered (honesty).
  - If the operation is implemented, the button IS rendered for every card of
    that venue, unless the position is staked Aerodrome (which has its own
    dedicated Claim/Unstake/Close Staked buttons and cannot compound).

The matrix is used by `lp_tab.py` so button rendering is data-driven and
diagnosable via the [button-render] trace.
"""
from dataclasses import dataclass
from typing import Dict, Optional


@dataclass(frozen=True)
class VenueCapabilities:
    """Capability flags for a venue."""
    collect: bool = True
    compound: bool = True
    close: bool = True


# ---------------------------------------------------------------------------
# Canonical matrix
# ---------------------------------------------------------------------------

_CAPABILITIES: Dict[str, VenueCapabilities] = {
    # Solana / Orca: collect and close are implemented; compound is not.
    "orca": VenueCapabilities(collect=True, compound=False, close=True),

    # BNB Chain / BSC: collect and close are implemented; compound is not.
    "bsc": VenueCapabilities(collect=True, compound=False, close=True),

    # Sui / Cetus: all three are implemented via PTBs.
    "cetus": VenueCapabilities(collect=True, compound=True, close=True),

    # HyperEVM / Project X: all three are implemented.
    "hyperliquid": VenueCapabilities(collect=True, compound=True, close=True),

    # Base / Aerodrome unstaked: all three are implemented.
    # Staked Aerodrome is handled separately by the card renderer.
    "aerodrome": VenueCapabilities(collect=True, compound=True, close=True),
}


# Normalised venue-name aliases that may appear in saved-pool records.
_VENUE_ALIASES: Dict[str, str] = {
    "aerodrome/base": "aerodrome",
    "aerodrome": "aerodrome",
    "base": "aerodrome",
    "hyperliquid": "hyperliquid",
    "hyperevm": "hyperliquid",
    "project x": "hyperliquid",
    "hl1": "hyperliquid",
    "bsc": "bsc",
    "pancakeswap": "bsc",
    "uniswap_bsc": "bsc",
    "orca": "orca",
    "solana": "orca",
    "sol": "orca",
    "cetus": "cetus",
    "sui": "cetus",
}


def resolve_venue_key(venue: Optional[str], position_id: Optional[str] = "") -> str:
    """Return a canonical venue key from a venue name or position_id prefix.

    Args:
        venue: Saved-pool venue name (e.g. 'Aerodrome', 'BSC').
        position_id: e.g. 'base:123', 'solana:abc'.

    Returns:
        Canonical key or "" if it cannot be resolved.
    """
    if venue:
        key = _VENUE_ALIASES.get((venue or "").strip().lower())
        if key:
            return key
    if position_id:
        prefix = str(position_id).split(":", 1)[0].lower()
        if prefix == "base":
            return "aerodrome"
        if prefix == "bsc":
            return "bsc"
        if prefix in ("hyperevm", "hyperliquid"):
            return "hyperliquid"
        if prefix in ("solana",):
            return "orca"
        if prefix in ("sui",):
            return "cetus"
    return ""


def get_capabilities(venue: Optional[str] = None,
                     position_id: Optional[str] = "",
                     is_staked: bool = False) -> VenueCapabilities:
    """Return the capability flags for a card.

    Staked Aerodrome positions never show the normal matrix because they have
    dedicated gauge buttons and cannot compound while staked.
    """
    if is_staked:
        return VenueCapabilities(collect=False, compound=False, close=False)
    key = resolve_venue_key(venue, position_id)
    return _CAPABILITIES.get(key, VenueCapabilities(collect=False, compound=False, close=False))


def button_labels(caps: VenueCapabilities) -> Dict[str, str]:
    """Human labels for the buttons implied by a capability set."""
    labels: Dict[str, str] = {}
    if caps.collect:
        labels["collect"] = "💰 Collect"
    if caps.compound:
        labels["compound"] = "🔄 Compound"
    if caps.close:
        labels["close"] = "✕ Close"
    return labels
