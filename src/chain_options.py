"""ColdStack chain option constants (extracted from gui_main_v5.py in v5.1.4).

This module exists to break the circular import between gui_main_v5 and the
extracted account_dialogs module. Both modules import these constants from here.
"""

from derivation_engine import DerivationEngine


# Standardized chain options used across Add Address and Add Private Key dialogs
CHAIN_OPTIONS = [
    "BTC Taproot (bc1p)",
    "BTC SegWit (bc1q)",
    "BTC (Bitcoin)",
    "EVM (Ethereum / Arbitrum / Base)",
    "EVM Railgun",
    "SOL (Solana)",
    "ZEC (Zcash)",
    "ZEC Transparent",
    "ZEC Orchard",
    "XMR (Monero)",
    "DASH (Dash)",
    "RUNE (THORChain)",
    "SUI (Sui)",
    "Hyperliquid (HL1 & HyperEVM)",
    "TRON (Tron)",
    "ATOM (Cosmos)",
    "DOT (Polkadot)",
    "ADA (Cardano)",
    "XRP (Ripple)",
    "SCRT (Secret Network)",
    "Custom...",
]

# Chains supported by the DerivationEngine (subset of CHAIN_OPTIONS)
DERIVATION_CHAINS = list(DerivationEngine.SUPPORTED_CHAINS.keys())
