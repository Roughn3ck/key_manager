"""
Price Engine - Fetches crypto prices via CoinGecko API (free tier, no API key).

Read-only. In-memory cache with 60-second TTL. No persistent storage.
The caller is responsible for checking online_mode before calling.

Version: v4.0 (June 2026)
"""
import json
import urllib.request
import urllib.error
import time
from typing import Dict, Optional, List

# CoinGecko API endpoint (free tier, no API key)
COINGECKO_URL = "https://api.coingecko.com/api/v3/simple/price"

# Supported coins -> CoinGecko IDs
COIN_IDS = {
    "bitcoin": "bitcoin",
    "ethereum": "ethereum",
    "solana": "solana",
    "dash": "dash",
    "sui": "sui",
    "hyperliquid": "hyperliquid",
    "usd-coin": "usd-coin",
    "tether": "tether",
    "weth": "weth",
    "chainlink": "chainlink",
    "uniswap": "uniswap",
    "arbitrum": "arbitrum",
    "coinbase-wrapped-btc": "coinbase-wrapped-btc",
}

# Symbols that are pegged 1:1 to USD - don't query CoinGecko, just use 1.0
STABLECOINS = {"USDC", "USDT", "DAI", "FRAX", "USDe"}

# Symbols that are pegged 1:1 to another asset - map to the pegged asset's symbol
PEGGED_TOKENS = {
    "WETH": "ETH",
    "cbBTC": "BTC",
}

# Supported fiat currencies
SUPPORTED_CURRENCIES = ["usd", "aud", "cad", "eur", "chf"]

# Cache TTL in seconds
CACHE_TTL = 60


class PriceEngine:
    """Fetches crypto prices from CoinGecko with in-memory caching.

    Prices are cached for 60 seconds. No persistent storage.
    All methods are read-only.
    """

    def __init__(self):
        """Initialize the price engine with an empty cache."""
        self._cache: Dict[str, Dict[str, float]] = {}  # {coin_id: {currency: price}}
        self._cache_time: float = 0  # timestamp of last fetch
        self._cached_currencies: List[str] = []

    def _is_cache_valid(self) -> bool:
        """Check if the cached prices are still valid (within TTL)."""
        if self._cache_time == 0:
            return False
        return (time.time() - self._cache_time) < CACHE_TTL

    def fetch_prices(self, currencies: List[str] = None) -> Dict[str, Dict[str, float]]:
        """Fetch current prices for all supported coins.

        Args:
            currencies: List of fiat currency codes (e.g., ['usd', 'aud']).
                        Defaults to ['usd', 'aud'].

        Returns:
            Dict mapping coin_id -> {currency: price}.
            e.g., {'bitcoin': {'usd': 65000.0, 'aud': 99000.0}}
            Returns empty dict on error.
        """
        if currencies is None:
            currencies = ["usd", "aud"]

        # Return cached data if still valid and currencies match
        if self._is_cache_valid() and set(currencies) == set(self._cached_currencies):
            return self._cache

        # Fetch prices for all coins in COIN_IDS
        coin_ids = ",".join(COIN_IDS.values())
        vs_currencies = ",".join(currencies)

        url = f"{COINGECKO_URL}?ids={coin_ids}&vs_currencies={vs_currencies}"

        req = urllib.request.Request(url, headers={
            "Accept": "application/json",
            "User-Agent": "ColdStack/4.0"
        })

        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                data = json.loads(response.read().decode('utf-8'))

                # Cache the results
                self._cache = data
                self._cache_time = time.time()
                self._cached_currencies = currencies

                return data

        except (urllib.error.URLError, json.JSONDecodeError, Exception):
            # Return cached data if available (even if stale), otherwise empty
            return self._cache if self._cache else {}

    def get_price(self, coin_id: str, currency: str = "usd") -> Optional[float]:
        """Get a single price for a coin in a specific currency.

        Args:
            coin_id: CoinGecko coin ID (e.g., 'bitcoin', 'ethereum').
            currency: Fiat currency code (e.g., 'usd', 'aud').

        Returns:
            Price as float, or None if unavailable.
        """
        prices = self.fetch_prices([currency])
        if coin_id in prices and currency in prices[coin_id]:
            return float(prices[coin_id][currency])
        return None

    def convert_balance_to_fiat(self, balance: float, coin_symbol: str, currency: str = "usd") -> Optional[float]:
        """Convert a balance to fiat value.

        Handles stablecoins (USDC, USDT = $1), pegged tokens (WETH=ETH, cbBTC=BTC),
        and native tokens (ETH, BTC, SOL, DASH, SUI, HYPE).

        Args:
            balance: The native balance (e.g., 0.5 ETH).
            coin_symbol: The coin symbol (e.g., 'ETH', 'BTC', 'USDC', 'HYPE').
            currency: Target fiat currency (e.g., 'usd', 'aud').

        Returns:
            Fiat value as float, or None if price unavailable.
        """
        sym_upper = coin_symbol.upper()

        # Stablecoins: always 1:1 USD peg
        if sym_upper in STABLECOINS:
            return balance * 1.0

        # Pegged tokens: use the underlying asset's price
        if sym_upper in PEGGED_TOKENS:
            pegged_to = PEGGED_TOKENS[sym_upper]
            return self.convert_balance_to_fiat(balance, pegged_to, currency)

        # Map symbols to CoinGecko IDs
        symbol_to_id = {
            "ETH": "ethereum",
            "BTC": "bitcoin",
            "SOL": "solana",
            "DASH": "dash",
            "SUI": "sui",
            "HYPE": "hyperliquid",
            "ZEC": "zcash",
            "XRP": "ripple",
            "ADA": "cardano",
            "ATOM": "cosmos",
            "SCRT": "secret",
            "RUNE": "thorchain",
            "BNB": "binancecoin",
            "MATIC": "matic-network",
            "LINK": "chainlink",
            "UNI": "uniswap",
            "ARB": "arbitrum",
            "USDC": "usd-coin",
            "USDT": "tether",
        }

        coin_id = symbol_to_id.get(sym_upper)
        if not coin_id:
            return None

        price = self.get_price(coin_id, currency)
        if price is not None:
            return balance * price
        return None

    def clear_cache(self) -> None:
        """Clear the in-memory price cache."""
        self._cache = {}
        self._cache_time = 0
        self._cached_currencies = []


# Supported display currency options for the Settings dialog
DISPLAY_CURRENCY_OPTIONS = [
    ("None (native only)", "none"),
    ("USD - US Dollar", "usd"),
    ("AUD - Australian Dollar", "aud"),
    ("CAD - Canadian Dollar", "cad"),
    ("EUR - Euro", "eur"),
    ("CHF - Swiss Franc", "chf"),
]