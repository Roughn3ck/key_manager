"""
Balance Engine - Fetches wallet balances via public RPC endpoints.

Read-only. Stateless. Offline by default (caller must check online_mode).
Only public addresses are queried - private keys and mnemonics NEVER leave the vault.

Version: v4.2 (July 2026) - Customizable RPC endpoints + API key injection + fallback URLs
"""
import json
import urllib.request
import urllib.error
from typing import Dict, Optional, Any, List
from threading import Thread

# Default public RPC endpoints (no API keys needed) — legacy flat dict for backward compat.
# v4.2 uses DEFAULT_RPC_CONFIG (below) which includes fallback URLs and auth references.
DEFAULT_RPC_ENDPOINTS = {
    "ethereum": "https://eth.llamarpc.com",
    "arbitrum": "https://arb1.arbitrum.io/rpc",
    "base": "https://mainnet.base.org",
    "bsc": "https://bsc-dataseed.binance.org",
    "polygon": "https://polygon-rpc.com",
    "optimism": "https://mainnet.optimism.io",
    "hyperliquid": "https://rpc.hyperliquid.xyz/evm",
}

# v4.2: Full endpoint config with url, auth, and fallback per chain.
# This is the internal default used when no rpc_config is provided.
DEFAULT_RPC_CONFIG: Dict[str, Dict[str, Any]] = {
    "ethereum": {"url": "https://eth.llamarpc.com", "auth": None, "fallback": "https://rpc.ankr.com/eth"},
    "arbitrum": {"url": "https://arb1.arbitrum.io/rpc", "auth": None, "fallback": "https://rpc.ankr.com/arbitrum"},
    "base": {"url": "https://mainnet.base.org", "auth": None, "fallback": "https://base.llamarpc.com"},
    "bsc": {"url": "https://bsc-dataseed.binance.org", "auth": None, "fallback": "https://bsc-dataseed1.binance.org"},
    "polygon": {"url": "https://polygon-rpc.com", "auth": None, "fallback": "https://rpc.ankr.com/polygon"},
    "optimism": {"url": "https://mainnet.optimism.io", "auth": None, "fallback": "https://rpc.ankr.com/optimism"},
    "hyperliquid_evm": {"url": "https://rpc.hyperliquid.xyz/evm", "auth": None, "fallback": None},
    "bitcoin": {"url": "https://blockstream.info/api/address/{address}", "auth": None, "fallback": "https://mempool.space/api/address/{address}"},
    "solana": {"url": "https://api.mainnet-beta.solana.com", "auth": "helius", "fallback": "https://solana-api.projectserum.com"},
    "dash": {"url": "https://insight.dash.org/insight-api/addr/{address}", "auth": None, "fallback": None},
    "sui": {"url": "https://fullnode.mainnet.sui.io", "auth": None, "fallback": None},
    "hyperliquid_l1": {"url": "https://api.hyperliquid.xyz/info", "auth": None, "fallback": None},
    "zcash": {"url": "https://api.blockchair.com/zcash/dashboards/address/{address}", "auth": None, "fallback": None},
    "ripple": {"url": "https://s1.ripple.com:51234", "auth": None, "fallback": "https://s2.ripple.com:51234"},
    "cardano": {"url": "https://api.koios.rest/api/v1/address_info", "auth": None, "fallback": None},
    "cosmos": {"url": "https://rest.lavenderfive.com:443/cosmoshub/cosmos/bank/v1beta1/balances/{address}", "auth": None, "fallback": None},
    "secret": {"url": "https://rest.lavenderfive.com:443/secretnetwork/cosmos/bank/v1beta1/balances/{address}", "auth": None, "fallback": None},
    "thorchain": {"url": "https://thornode.thorchain.ninja/cosmos/bank/v1beta1/balances/{address}", "auth": None, "fallback": None},
}

# Legacy module-level constants (kept for backward compatibility — not used internally by v4.2)
BTC_API = "https://blockstream.info/api/address/{address}"
SOLANA_RPC = "https://api.mainnet-beta.solana.com"
DASH_API = "https://insight.dash.org/insight-api/addr/{address}"
SUI_RPC = "https://fullnode.mainnet.sui.io"
HYPERLIQUID_L1_API = "https://api.hyperliquid.xyz/info"
ZCASH_API = "https://api.blockchair.com/zcash/dashboards/address/{address}"
XRP_RPC = "https://s1.ripple.com:51234"
ADA_API = "https://api.koios.rest/api/v1/address_info"
COSMOS_API = "https://rest.lavenderfive.com:443/cosmoshub/cosmos/bank/v1beta1/balances/{address}"
SECRET_API = "https://rest.lavenderfive.com:443/secretnetwork/cosmos/bank/v1beta1/balances/{address}"
RUNE_API = "https://thornode.thorchain.ninja/cosmos/bank/v1beta1/balances/{address}"

# Native currency symbols per chain type
CURRENCY_SYMBOLS = {
    "ethereum": "ETH",
    "arbitrum": "ETH",
    "base": "ETH",
    "bsc": "BNB",
    "polygon": "MATIC",
    "optimism": "ETH",
    "hyperliquid": "HYPE",
    "bitcoin": "BTC",
    "solana": "SOL",
    "dash": "DASH",
    "sui": "SUI",
    "zcash": "ZEC",
    "ripple": "XRP",
    "cardano": "ADA",
    "cosmos": "ATOM",
    "secret": "SCRT",
    "thorchain": "RUNE",
}

# CoinGecko IDs for price lookup
COINGECKO_IDS = {
    "bitcoin": "bitcoin",
    "ethereum": "ethereum",
    "solana": "solana",
    "dash": "dash",
    "sui": "sui",
    "hyperliquid": "hyperliquid",
    "zcash": "zcash",
    "ripple": "ripple",
    "cardano": "cardano",
    "cosmos": "cosmos",
    "secret": "secret",
    "thorchain": "thorchain",
}


# Common ERC-20 token contracts per chain
# Each token has: { address, decimals }
TOKEN_CONTRACTS = {
    "ethereum": {
        "USDC": {"address": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48", "decimals": 6},
        "USDT": {"address": "0xdAC17F958D2ee523a2206206994597C13D831ec7", "decimals": 6},
        "WETH": {"address": "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2", "decimals": 18},
        "LINK": {"address": "0x514910771AF9Ca656af840dff83E8264EcF986CA", "decimals": 18},
        "UNI": {"address": "0x1f9840a85d5aF5bf1D1762F925BDADdC4201F984", "decimals": 18},
        "cbBTC": {"address": "0xcbB45146687557Fd9B6F8cB1E2D51a65F3B1D1c1", "decimals": 8},
    },
    "arbitrum": {
        "USDC": {"address": "0xaf88d065e77c8cC2239327C5EDB3A432268e5831", "decimals": 6},
        "USDT": {"address": "0xFd086bD7e092E6D1d87da0c6Fd4aCDd09b3d3b07", "decimals": 6},
        "WETH": {"address": "0x82aF49447D8a03e3BD8BaC6F6640594cB3a1D1c3", "decimals": 18},
        "ARB": {"address": "0x912CE59144191C1204E64559FE8253a0e49E6548", "decimals": 18},
    },
    "base": {
        "USDC": {"address": "0x833589fCD6eDb6E08357c3f4147D2935738F1a30", "decimals": 6},
        "WETH": {"address": "0x4200000000000000000000000000000000000006", "decimals": 18},
        "cbBTC": {"address": "0xcbB45146687557Fd9B6F8cB1E2D51a65F3B1D1c1", "decimals": 8},
    },
    "bsc": {
        "USDT": {"address": "0x55d398326f99059fF775485246999027B3197955", "decimals": 18},
        "USDC": {"address": "0x8AC76A51cc950d9822D68b83fE1DacBbFB1a271f", "decimals": 18},
        "LINK": {"address": "0xF8A0BF9cF54fE7956424EAB8c9F0e3a348a3FE1d", "decimals": 18},
    },
    "polygon": {
        "USDC": {"address": "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359", "decimals": 6},
        "USDT": {"address": "0xc2132D05D31c914e87C66402F3a32E3c4b95c3e9", "decimals": 6},
    },
    "optimism": {
        "USDC": {"address": "0x0b2C639c533813f4Aa9D1893E996BA9468Aa2734", "decimals": 6},
        "USDT": {"address": "0x94b008aA00579c1307C0c60cE8aF3a1cCB3d8a47", "decimals": 6},
    },
}


class BalanceEngine:
    """Stateless balance fetcher for EVM, BTC, SOL, DASH, SUI, and other chains.

    All methods are read-only and make a single network request per call.
    The caller is responsible for checking online_mode before calling.

    v4.2: Supports customizable RPC endpoints (from rpc_endpoints.json) and
    API key injection from the encrypted vault. Falls back to fallback URLs
    on primary endpoint failure.
    """

    def __init__(self, rpc_config: Optional[Dict[str, Dict[str, Any]]] = None,
                 api_keys: Optional[Dict[str, str]] = None,
                 rpc_endpoints: Optional[Dict[str, str]] = None):
        """Initialize with optional custom RPC configuration.

        Args:
            rpc_config: Full endpoint config dict in {chain_id: {url, auth, fallback}} format.
                        Takes precedence over rpc_endpoints. If None, uses DEFAULT_RPC_CONFIG.
            api_keys: API keys from vault config. Format: {provider: "key_string"}.
                      Used to inject keys into URLs for chains with auth configured.
            rpc_endpoints: Legacy flat dict {chain_id: "url_string"} for backward compatibility.
                           If rpc_config is None and rpc_endpoints is provided, a minimal
                           rpc_config is built from these URLs.
        """
        if rpc_config is not None:
            self.rpc_config = rpc_config
        elif rpc_endpoints is not None:
            # Backward compat: build config from flat dict, use defaults for missing chains
            self.rpc_config = DEFAULT_RPC_CONFIG.copy()
            for chain_id, url in rpc_endpoints.items():
                if chain_id in self.rpc_config:
                    self.rpc_config[chain_id] = {
                        "url": url,
                        "auth": self.rpc_config[chain_id].get("auth"),
                        "fallback": self.rpc_config[chain_id].get("fallback"),
                    }
                else:
                    self.rpc_config[chain_id] = {"url": url, "auth": None, "fallback": None}
        else:
            self.rpc_config = DEFAULT_RPC_CONFIG.copy()

        self.api_keys = api_keys or {}

    def _get_url(self, chain_id: str) -> Optional[str]:
        """Get the effective URL for a chain, with API key injected if configured.

        Args:
            chain_id: Chain identifier (e.g., "ethereum", "bitcoin", "solana").

        Returns:
            The URL string with API key applied, or None if chain not configured.
        """
        endpoint = self.rpc_config.get(chain_id, {})
        url = endpoint.get("url")
        if not url:
            return None

        auth_provider = endpoint.get("auth")
        if auth_provider and auth_provider in self.api_keys:
            key = self.api_keys[auth_provider]
            if key:
                url = self._inject_api_key(url, auth_provider, key)

        return url

    def _get_fallback(self, chain_id: str) -> Optional[str]:
        """Get the fallback URL for a chain, with API key injected if configured.

        Args:
            chain_id: Chain identifier.

        Returns:
            Fallback URL string with API key applied, or None if no fallback.
        """
        endpoint = self.rpc_config.get(chain_id, {})
        url = endpoint.get("fallback")
        if not url:
            return None

        auth_provider = endpoint.get("auth")
        if auth_provider and auth_provider in self.api_keys:
            key = self.api_keys[auth_provider]
            if key:
                url = self._inject_api_key(url, auth_provider, key)

        return url

    @staticmethod
    def _inject_api_key(url: str, provider: str, key: str) -> str:
        """Inject an API key into a URL based on the provider pattern.

        Args:
            url: The base URL to modify.
            provider: The auth provider name (e.g., "helius").
            key: The API key string.

        Returns:
            Modified URL with the API key applied.
        """
        if provider == "helius":
            # Helius: replace public Solana hostname with Helius endpoint
            url = url.replace("api.mainnet-beta.solana.com", "mainnet.helius-rpc.com")
            # Remove any existing query params to avoid double api-key
            if "?" in url:
                url = url.split("?")[0]
            url += f"?api-key={key}"
        # Add more provider patterns here as needed
        return url

    # --- EVM chains ---

    def _rpc_call(self, url: str, method: str, params: list, request_id: int = 1) -> Optional[Any]:
        """Make a JSON-RPC POST request to an EVM endpoint.

        Returns the 'result' field, or None on error.
        """
        payload = json.dumps({
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
            "id": request_id
        }).encode('utf-8')

        req = urllib.request.Request(url, data=payload, headers={
            "Content-Type": "application/json",
            "User-Agent": "ColdStack/4.2"
        })

        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                data = json.loads(response.read().decode('utf-8'))
                return data.get("result")
        except (urllib.error.URLError, json.JSONDecodeError, KeyError, Exception):
            return None

    def fetch_evm_balance(self, address: str, chain_key: str) -> Optional[float]:
        """Fetch native balance for an EVM address on a specific chain.

        Args:
            address: The EVM address (0x...)
            chain_key: One of 'ethereum', 'arbitrum', 'base', 'bsc', 'polygon', 'optimism'
                       Also supports 'hyperliquid_evm' for HyperEVM.

        Returns:
            Balance in native currency (ETH/BNB/MATIC) as float, or None on error.
        """
        url = self._get_url(chain_key)
        if not url:
            return None

        result = self._rpc_call(url, "eth_getBalance", [address, "latest"])
        if result is not None:
            try:
                wei = int(result, 16)
                return wei / 1e18
            except (ValueError, TypeError):
                pass

        # Try fallback URL
        fallback_url = self._get_fallback(chain_key)
        if fallback_url and fallback_url != url:
            result = self._rpc_call(fallback_url, "eth_getBalance", [address, "latest"])
            if result is not None:
                try:
                    wei = int(result, 16)
                    return wei / 1e18
                except (ValueError, TypeError):
                    return None

        return None


    def fetch_erc20_balance(self, wallet_address: str, token_address: str, chain_key: str, decimals: int) -> Optional[float]:
        """Fetch ERC-20 token balance for an address on a specific EVM chain.

        Uses eth_call to the token contract's balanceOf(address) function.

        Args:
            wallet_address: The wallet address (0x...)
            token_address: The ERC-20 token contract address.
            chain_key: One of 'ethereum', 'arbitrum', 'base', etc.
            decimals: Token decimals (6 for USDC, 18 for WETH, etc.)

        Returns:
            Token balance as float, or None on error.
        """
        url = self._get_url(chain_key)
        if not url:
            return None

        # balanceOf(address) function selector = 0x70a08231
        # Pad wallet address to 32 bytes
        padded_addr = wallet_address[2:].lower().zfill(64)
        call_data = "0x70a08231" + padded_addr

        result = self._rpc_call(url, "eth_call", [{"to": token_address, "data": call_data}, "latest"])
        if result is not None:
            try:
                balance = int(result, 16)
                return balance / (10 ** decimals)
            except (ValueError, TypeError):
                pass

        # Try fallback URL
        fallback_url = self._get_fallback(chain_key)
        if fallback_url and fallback_url != url:
            result = self._rpc_call(fallback_url, "eth_call", [{"to": token_address, "data": call_data}, "latest"])
            if result is not None:
                try:
                    balance = int(result, 16)
                    return balance / (10 ** decimals)
                except (ValueError, TypeError):
                    return None

        return None

    def fetch_all_evm_balances(self, address: str, progress_callback=None) -> List[Dict[str, Any]]:
        """Fetch native + ERC-20 token balances across ALL configured EVM chains.

        Returns a list of {chain, balance, symbol, type} dicts for chains with non-zero balance.
        """
        results = []

        # v4.2: Use 'hyperliquid_evm' as the chain key (was 'hyperliquid' in v4.1)
        evm_chains = ["ethereum", "arbitrum", "base", "bsc", "polygon", "optimism", "hyperliquid_evm"]

        for chain_key in evm_chains:
            # v4.2: Report progress to UI
            if progress_callback:
                display_name = chain_key.replace("_", " ").title()
                progress_callback(f"Fetching {display_name}...")
            url = self._get_url(chain_key)
            if not url:
                continue

            # Native balance
            balance = self.fetch_evm_balance(address, chain_key)
            # Use 'hyperliquid' as the display name for HYPE (backward compat with CURRENCY_SYMBOLS)
            display_key = "hyperliquid" if chain_key == "hyperliquid_evm" else chain_key
            if balance is not None and balance > 0:
                results.append({
                    "chain": display_key,
                    "balance": balance,
                    "symbol": CURRENCY_SYMBOLS.get(display_key, "?"),
                    "type": "native"
                })

            # ERC-20 token balances for this chain
            tokens = TOKEN_CONTRACTS.get(chain_key, TOKEN_CONTRACTS.get(display_key, {}))
            for token_symbol, token_info in tokens.items():
                token_balance = self.fetch_erc20_balance(
                    address, token_info["address"], chain_key, token_info["decimals"]
                )
                if token_balance is not None and token_balance > 0:
                    results.append({
                        "chain": display_key,
                        "balance": token_balance,
                        "symbol": token_symbol,
                        "type": "erc20"
                    })

        return results

    # --- Bitcoin ---

    def fetch_btc_balance(self, address: str) -> Optional[float]:
        """Fetch confirmed balance for a Bitcoin address (BTC).

        Returns balance in BTC, or None on error.
        """
        url_template = self._get_url("bitcoin")
        if not url_template:
            return None

        url = url_template.format(address=address)
        result = self._fetch_btc_from_url(url)
        if result is not None:
            return result

        # Try fallback URL
        fallback_template = self._get_fallback("bitcoin")
        if fallback_template:
            fallback_url = fallback_template.format(address=address)
            return self._fetch_btc_from_url(fallback_url)

        return None

    def _fetch_btc_from_url(self, url: str) -> Optional[float]:
        """Fetch BTC balance from a specific URL. Internal helper."""
        req = urllib.request.Request(url, headers={
            "User-Agent": "ColdStack/4.2",
            "Accept": "application/json"
        })
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                data = json.loads(response.read().decode('utf-8'))
                funded = data.get("chain_stats", {}).get("funded_txo_sum", 0)
                spent = data.get("chain_stats", {}).get("spent_txo_sum", 0)
                balance_sats = funded - spent
                return balance_sats / 1e8
        except (urllib.error.URLError, json.JSONDecodeError, KeyError, Exception):
            return None

    # --- Solana ---

    def fetch_solana_balance(self, address: str) -> Optional[float]:
        """Fetch SOL balance for a Solana address.

        Returns balance in SOL, or None on error.
        """
        url = self._get_url("solana")
        if not url:
            return None

        result = self._fetch_solana_from_url(url, address)
        if result is not None:
            return result

        # Try fallback URL (note: fallback won't have API key — it's a public endpoint)
        fallback_url = self._get_fallback("solana")
        if fallback_url and fallback_url != url:
            return self._fetch_solana_from_url(fallback_url, address)

        return None

    def _fetch_solana_from_url(self, url: str, address: str) -> Optional[float]:
        """Fetch SOL balance from a specific URL. Internal helper."""
        payload = json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getBalance",
            "params": [address]
        }).encode('utf-8')

        req = urllib.request.Request(url, data=payload, headers={
            "Content-Type": "application/json",
            "User-Agent": "ColdStack/4.2"
        })
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                data = json.loads(response.read().decode('utf-8'))
                lamports = data.get("result", {}).get("value", 0)
                return lamports / 1e9
        except (urllib.error.URLError, json.JSONDecodeError, KeyError, Exception):
            return None

    # --- Dash ---

    def fetch_dash_balance(self, address: str) -> Optional[float]:
        """Fetch confirmed balance for a Dash address.

        Returns balance in DASH, or None on error.
        """
        url_template = self._get_url("dash")
        if not url_template:
            return None

        url = url_template.format(address=address)
        result = self._fetch_dash_from_url(url)
        if result is not None:
            return result

        # Try fallback URL
        fallback_template = self._get_fallback("dash")
        if fallback_template:
            fallback_url = fallback_template.format(address=address)
            return self._fetch_dash_from_url(fallback_url)

        return None

    def _fetch_dash_from_url(self, url: str) -> Optional[float]:
        """Fetch DASH balance from a specific URL. Internal helper."""
        req = urllib.request.Request(url, headers={
            "User-Agent": "ColdStack/4.2",
            "Accept": "application/json"
        })
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                data = json.loads(response.read().decode('utf-8'))
                balance_sat = data.get("balanceSat", 0)
                if balance_sat == 0:
                    balance = data.get("balance", 0)
                    return float(balance) if balance else 0.0
                return balance_sat / 1e8
        except (urllib.error.URLError, json.JSONDecodeError, KeyError, Exception):
            return None

    # --- Sui ---

    def fetch_sui_balance(self, address: str) -> Optional[float]:
        """Fetch SUI balance for a Sui address.

        Returns balance in SUI, or None on error.
        """
        url = self._get_url("sui")
        if not url:
            return None

        result = self._fetch_sui_from_url(url, address)
        if result is not None:
            return result

        # Try fallback URL
        fallback_url = self._get_fallback("sui")
        if fallback_url and fallback_url != url:
            return self._fetch_sui_from_url(fallback_url, address)

        return None

    def _fetch_sui_from_url(self, url: str, address: str) -> Optional[float]:
        """Fetch SUI balance from a specific URL. Internal helper."""
        payload = json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "suix_getBalance",
            "params": [address]
        }).encode('utf-8')

        req = urllib.request.Request(url, data=payload, headers={
            "Content-Type": "application/json",
            "User-Agent": "ColdStack/4.2"
        })
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                data = json.loads(response.read().decode('utf-8'))
                total = data.get("result", {}).get("totalBalance", "0")
                return float(total) / 1e9
        except (urllib.error.URLError, json.JSONDecodeError, KeyError, ValueError, Exception):
            return None


    def fetch_hyperliquid_l1_balances(self, address: str) -> List[Dict[str, Any]]:
        """Fetch spot balances from Hyperliquid L1 (HyperCore) via the info API.

        This queries the L1 spot trading balances -- USDC, HYPE, and any spot assets.
        Different from HyperEVM eth_getBalance which only returns gas HYPE.

        Returns a list of {chain, balance, symbol, type} dicts for non-zero balances.
        """
        url = self._get_url("hyperliquid_l1")
        if not url:
            return []

        result = self._fetch_hl1_from_url(url, address)
        if result:
            return result

        # Try fallback URL
        fallback_url = self._get_fallback("hyperliquid_l1")
        if fallback_url and fallback_url != url:
            return self._fetch_hl1_from_url(fallback_url, address)

        return []

    def _fetch_hl1_from_url(self, url: str, address: str) -> List[Dict[str, Any]]:
        """Fetch Hyperliquid L1 balances from a specific URL. Internal helper."""
        payload = json.dumps({
            "type": "spotClearinghouseState",
            "user": address
        }).encode('utf-8')

        req = urllib.request.Request(
            url,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "User-Agent": "ColdStack/4.2"
            }
        )

        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                data = json.loads(response.read().decode('utf-8'))
                balances = data.get("balances", [])
                results = []
                for b in balances:
                    coin = b.get("coin", "")
                    total = b.get("total", "0")
                    hold = b.get("hold", "0")
                    try:
                        total_f = float(total)
                        hold_f = float(hold)
                        available = total_f - hold_f
                        if total_f > 0:
                            results.append({
                                "chain": "hyperliquid_l1",
                                "balance": total_f,
                                "symbol": coin,
                                "type": "spot",
                                "available": available,
                                "hold": hold_f
                            })
                    except (ValueError, TypeError):
                        continue
                return results
        except (urllib.error.URLError, json.JSONDecodeError, Exception):
            return []


    def fetch_zec_balance(self, address: str) -> Optional[float]:
        """Fetch transparent ZEC balance for a Zcash t-address."""
        url_template = self._get_url("zcash")
        if not url_template:
            return None

        url = url_template.format(address=address)
        result = self._fetch_zec_from_url(url, address)
        if result is not None:
            return result

        # Try fallback URL
        fallback_template = self._get_fallback("zcash")
        if fallback_template:
            fallback_url = fallback_template.format(address=address)
            return self._fetch_zec_from_url(fallback_url, address)

        return None

    def _fetch_zec_from_url(self, url: str, address: str) -> Optional[float]:
        """Fetch ZEC balance from a specific URL. Internal helper."""
        req = urllib.request.Request(url, headers={
            "User-Agent": "ColdStack/4.2",
            "Accept": "application/json"
        })
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                data = json.loads(response.read().decode('utf-8'))
                addr_data = data.get("data", {}).get(address, {})
                if addr_data:
                    balance_zat = addr_data.get("address", {}).get("balance", 0)
                    return balance_zat / 1e8
                return None
        except (urllib.error.URLError, json.JSONDecodeError, KeyError, Exception):
            return None

    def fetch_xrp_balance(self, address: str) -> Optional[float]:
        """Fetch XRP balance for a Ripple address."""
        url = self._get_url("ripple")
        if not url:
            return None

        result = self._fetch_xrp_from_url(url, address)
        if result is not None:
            return result

        # Try fallback URL
        fallback_url = self._get_fallback("ripple")
        if fallback_url and fallback_url != url:
            return self._fetch_xrp_from_url(fallback_url, address)

        return None

    def _fetch_xrp_from_url(self, url: str, address: str) -> Optional[float]:
        """Fetch XRP balance from a specific URL. Internal helper."""
        payload = json.dumps({
            "method": "account_info",
            "params": [{"account": address, "ledger_index": "validated"}]
        }).encode('utf-8')
        req = urllib.request.Request(url, data=payload, headers={
            "Content-Type": "application/json",
            "User-Agent": "ColdStack/4.2"
        })
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                data = json.loads(response.read().decode('utf-8'))
                balance_drops = data.get("result", {}).get("account_data", {}).get("Balance", 0)
                return int(balance_drops) / 1e6
        except (urllib.error.URLError, json.JSONDecodeError, KeyError, ValueError, Exception):
            return None

    def fetch_ada_balance(self, address: str) -> Optional[float]:
        """Fetch ADA balance for a Cardano address via Koios (no API key needed)."""
        url = self._get_url("cardano")
        if not url:
            return None

        result = self._fetch_ada_from_url(url, address)
        if result is not None:
            return result

        # Try fallback URL
        fallback_url = self._get_fallback("cardano")
        if fallback_url and fallback_url != url:
            return self._fetch_ada_from_url(fallback_url, address)

        return None

    def _fetch_ada_from_url(self, url: str, address: str) -> Optional[float]:
        """Fetch ADA balance from a specific URL. Internal helper."""
        payload = json.dumps({"_addresses": [address]}).encode('utf-8')
        req = urllib.request.Request(url, data=payload, headers={
            "Content-Type": "application/json",
            "User-Agent": "ColdStack/4.2"
        })
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                data = json.loads(response.read().decode('utf-8'))
                if data and len(data) > 0:
                    balance_lovelace = data[0].get("balance", "0")
                    return int(balance_lovelace) / 1e6
        except (urllib.error.URLError, json.JSONDecodeError, KeyError, ValueError, Exception):
            return None

    def fetch_cosmos_balances(self, address: str) -> List[Dict[str, Any]]:
        """Fetch all token balances for a Cosmos Hub address."""
        url_template = self._get_url("cosmos")
        if not url_template:
            return []

        url = url_template.format(address=address)
        result = self._fetch_cosmos_from_url(url)
        if result:
            return result

        # Try fallback URL
        fallback_template = self._get_fallback("cosmos")
        if fallback_template:
            fallback_url = fallback_template.format(address=address)
            return self._fetch_cosmos_from_url(fallback_url)

        return []

    def _fetch_cosmos_from_url(self, url: str) -> List[Dict[str, Any]]:
        """Fetch Cosmos balances from a specific URL. Internal helper."""
        req = urllib.request.Request(url, headers={
            "User-Agent": "ColdStack/4.2",
            "Accept": "application/json"
        })
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                data = json.loads(response.read().decode('utf-8'))
                balances = data.get("balances", [])
                results = []
                for b in balances:
                    denom = b.get("denom", "")
                    amount_str = b.get("amount", "0")
                    try:
                        amount = float(amount_str)
                        if amount <= 0:
                            continue
                        if denom == "uatom":
                            symbol = "ATOM"
                            decimals = 6
                        elif denom == "uusdc":
                            symbol = "USDC"
                            decimals = 6
                        elif denom.startswith("ibc/"):
                            symbol = denom[:12] + "..."
                            decimals = 6
                        else:
                            symbol = denom.upper().lstrip('U')
                            decimals = 6
                        balance = amount / (10 ** decimals)
                        results.append({
                            "chain": "cosmos",
                            "balance": balance,
                            "symbol": symbol,
                            "type": "bank"
                        })
                    except (ValueError, TypeError):
                        continue
                return results
        except (urllib.error.URLError, json.JSONDecodeError, KeyError, Exception):
            return []


    def fetch_scrt_balances(self, address: str) -> List[Dict[str, Any]]:
        """Fetch transparent SCRT balance for a Secret Network address."""
        url_template = self._get_url("secret")
        if not url_template:
            return []

        url = url_template.format(address=address)
        result = self._fetch_scrt_from_url(url)
        if result:
            return result

        # Try fallback URL
        fallback_template = self._get_fallback("secret")
        if fallback_template:
            fallback_url = fallback_template.format(address=address)
            return self._fetch_scrt_from_url(fallback_url)

        return []

    def _fetch_scrt_from_url(self, url: str) -> List[Dict[str, Any]]:
        """Fetch SCRT balances from a specific URL. Internal helper."""
        req = urllib.request.Request(url, headers={
            "User-Agent": "ColdStack/4.2",
            "Accept": "application/json"
        })
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                data = json.loads(response.read().decode('utf-8'))
                balances = data.get("balances", [])
                results = []
                for b in balances:
                    denom = b.get("denom", "")
                    amount_str = b.get("amount", "0")
                    try:
                        amount = float(amount_str)
                        if amount <= 0:
                            continue
                        if denom == "uscrt":
                            symbol = "SCRT"
                            decimals = 8
                        else:
                            symbol = denom.upper().lstrip('U')[:12]
                            decimals = 6
                        balance = amount / (10 ** decimals)
                        results.append({
                            "chain": "secret",
                            "balance": balance,
                            "symbol": symbol,
                            "type": "bank"
                        })
                    except (ValueError, TypeError):
                        continue
                return results
        except (urllib.error.URLError, json.JSONDecodeError, KeyError, Exception):
            return []

    def fetch_rune_balance(self, address: str) -> List[Dict[str, Any]]:
        """Fetch RUNE balance for a THORChain address."""
        url_template = self._get_url("thorchain")
        if not url_template:
            return []

        url = url_template.format(address=address)
        result = self._fetch_rune_from_url(url)
        if result:
            return result

        # Try fallback URL
        fallback_template = self._get_fallback("thorchain")
        if fallback_template:
            fallback_url = fallback_template.format(address=address)
            return self._fetch_rune_from_url(fallback_url)

        return []

    def _fetch_rune_from_url(self, url: str) -> List[Dict[str, Any]]:
        """Fetch RUNE balances from a specific URL. Internal helper."""
        req = urllib.request.Request(url, headers={
            "User-Agent": "ColdStack/4.2",
            "Accept": "application/json"
        })
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                data = json.loads(response.read().decode('utf-8'))
                balances = data.get("balances", [])
                results = []
                for b in balances:
                    denom = b.get("denom", "")
                    amount_str = b.get("amount", "0")
                    try:
                        amount = float(amount_str)
                        if amount <= 0:
                            continue
                        if denom == "rune":
                            symbol = "RUNE"
                            decimals = 8
                        else:
                            symbol = denom.upper()[:12]
                            decimals = 8
                        balance = amount / (10 ** decimals)
                        results.append({
                            "chain": "thorchain",
                            "balance": balance,
                            "symbol": symbol,
                            "type": "bank"
                        })
                    except (ValueError, TypeError):
                        continue
                return results
        except (urllib.error.URLError, json.JSONDecodeError, KeyError, Exception):
            return []

    def fetch_hype_balances(self, address: str, progress_callback=None) -> List[Dict[str, Any]]:
        """Fetch HyperEVM gas balance + Hyperliquid L1 spot balances only.

        This is used for HYPE (Hyperliquid) chain type — it only queries
        HyperEVM (for gas HYPE) and Hyperliquid L1 (for spot balances),
        NOT all EVM chains like fetch_all_evm_balances does.
        """
        results = []

        # 1. HyperEVM native balance (gas HYPE)
        if progress_callback:
            progress_callback("Fetching HyperEVM...")
        balance = self.fetch_evm_balance(address, "hyperliquid_evm")
        if balance is not None and balance > 0:
            results.append({
                "chain": "hyperliquid",
                "balance": balance,
                "symbol": CURRENCY_SYMBOLS.get("hyperliquid", "HYPE"),
                "type": "native"
            })

        # 2. Hyperliquid L1 spot balances (USDC, HYPE spot, etc.)
        if progress_callback:
            progress_callback("Fetching Hyperliquid L1 spot...")
        hl1_balances = self.fetch_hyperliquid_l1_balances(address)
        results = results + hl1_balances

        return results

    # --- Dispatcher ---

    def fetch_balance(self, address: str, chain_type: str, coin: str = "", progress_callback=None) -> Dict[str, Any]:
        """Fetch balance for an address based on its chain type.

        This is the main entry point. It determines which API to use based on
        the chain_type string (from the vault's address entry 'coin' or 'chain' field).

        Args:
            address: The wallet address string.
            chain_type: The chain label (e.g., "EVM (Ethereum / Arbitrum / Base)", "BTC Taproot (bc1p)").
            coin: Optional coin field from the vault entry.

        Returns:
            Dict with keys:
                - 'balances': List of {chain, balance, symbol} (may be multiple for EVM)
                - 'error': str if something went wrong (empty string if success)
        """
        # Check both chain_type and coin for matching
        check_str = (chain_type or "") + " " + (coin or "")
        chain_lower = check_str.lower()

        try:
            # Railgun shielded addresses cannot be queried publicly
            if "railgun" in chain_lower:
                return {"balances": [], "error": "Shielded addresses cannot be queried publicly"}

            # Hyperliquid L1 (Spot) - must check BEFORE the HYPE/HyperEVM branch
            if "hyperliquid l1" in chain_lower or "hyperliquid_l1" in chain_lower or "hl1" in chain_lower or "(spot)" in chain_lower:
                balances = self.fetch_hyperliquid_l1_balances(address)
                if balances:
                    return {"balances": balances, "error": ""}
                return {"balances": [], "error": "No balances shown on Hyperliquid L1"}

            # HYPE (Hyperliquid) - only search HyperEVM + L1, not all EVM chains
            if ("hype" in chain_lower or "hyperliquid" in chain_lower) and "evm" not in chain_lower:
                balances = self.fetch_hype_balances(address, progress_callback=progress_callback)
                if balances:
                    return {"balances": balances, "error": ""}
                return {"balances": [], "error": "No balances found on HyperEVM or Hyperliquid L1"}

            # EVM chains - query all configured EVM RPCs (generic EVM only)
            if "evm" in chain_lower:
                balances = self.fetch_all_evm_balances(address, progress_callback=progress_callback)
                if balances:
                    return {"balances": balances, "error": ""}
                return {"balances": [], "error": "No balances found on any EVM chain"}

            # Bitcoin (all types: Taproot, SegWit, Legacy)
            elif "btc" in chain_lower or "bitcoin" in chain_lower:
                balance = self.fetch_btc_balance(address)
                if balance is not None:
                    return {"balances": [{"chain": "bitcoin", "balance": balance, "symbol": "BTC"}], "error": ""}
                return {"balances": [], "error": "Could not fetch BTC balance"}

            # Solana
            elif "sol" in chain_lower or "solana" in chain_lower:
                balance = self.fetch_solana_balance(address)
                if balance is not None:
                    return {"balances": [{"chain": "solana", "balance": balance, "symbol": "SOL"}], "error": ""}
                return {"balances": [], "error": "Could not fetch SOL balance"}

            # Dash
            elif "dash" in chain_lower:
                balance = self.fetch_dash_balance(address)
                if balance is not None:
                    return {"balances": [{"chain": "dash", "balance": balance, "symbol": "DASH"}], "error": ""}
                return {"balances": [], "error": "Could not fetch DASH balance"}

            # Sui
            elif "sui" in chain_lower:
                balance = self.fetch_sui_balance(address)
                if balance is not None:
                    return {"balances": [{"chain": "sui", "balance": balance, "symbol": "SUI"}], "error": ""}
                return {"balances": [], "error": "Could not fetch SUI balance"}

            # Zcash (transparent only)
            elif "zec" in chain_lower or "zcash" in chain_lower:
                if address.startswith("t1") or address.startswith("t3"):
                    balance = self.fetch_zec_balance(address)
                    if balance is not None:
                        return {"balances": [{"chain": "zcash", "balance": balance, "symbol": "ZEC"}], "error": ""}
                    return {"balances": [], "error": "Could not fetch ZEC balance"}
                else:
                    return {"balances": [], "error": "Shielded addresses cannot be queried publicly"}

            # XRP (Ripple)
            elif "xrp" in chain_lower or "ripple" in chain_lower:
                balance = self.fetch_xrp_balance(address)
                if balance is not None:
                    return {"balances": [{"chain": "ripple", "balance": balance, "symbol": "XRP"}], "error": ""}
                return {"balances": [], "error": "Could not fetch XRP balance"}

            # ADA (Cardano)
            elif "ada" in chain_lower or "cardano" in chain_lower:
                balance = self.fetch_ada_balance(address)
                if balance is not None:
                    return {"balances": [{"chain": "cardano", "balance": balance, "symbol": "ADA"}], "error": ""}
                return {"balances": [], "error": "Could not fetch ADA balance"}

            # Cosmos (ATOM + IBC tokens)
            elif "atom" in chain_lower or "cosmos" in chain_lower:
                balances = self.fetch_cosmos_balances(address)
                if balances:
                    return {"balances": balances, "error": ""}
                return {"balances": [], "error": "No Cosmos balances found"}

            # SCRT (Secret Network) - transparent only
            elif "scrt" in chain_lower or "secret" in chain_lower:
                balances = self.fetch_scrt_balances(address)
                if balances:
                    return {"balances": balances, "error": ""}
                return {"balances": [], "error": "No SCRT balances found (sScrt encrypted tokens cannot be queried)"}

            # RUNE (THORChain)
            elif "rune" in chain_lower or "thorchain" in chain_lower:
                balances = self.fetch_rune_balance(address)
                if balances:
                    return {"balances": balances, "error": ""}
                return {"balances": [], "error": "No RUNE balances found"}

            else:
                return {"balances": [], "error": f"Unsupported chain type: {chain_type}"}

        except Exception as e:
            return {"balances": [], "error": str(e)}

    def fetch_balances_batch(self, addresses: List[Dict[str, str]]) -> Dict[str, Dict[str, Any]]:
        """Fetch balances for multiple addresses in background threads.

        Args:
            addresses: List of {address, chain_type} dicts.

        Returns:
            Dict mapping address -> {balances, error}
        """
        results = {}

        def _fetch_one(addr_info):
            addr = addr_info["address"]
            chain = addr_info.get("chain_type", addr_info.get("coin", ""))
            results[addr] = self.fetch_balance(addr, chain)

        threads = []
        for addr_info in addresses:
            t = Thread(target=_fetch_one, args=(addr_info,), daemon=True)
            threads.append(t)
            t.start()

        # Wait for all threads (with timeout)
        for t in threads:
            t.join(timeout=30)

        return results


# Convenience: determine if a chain type is balance-fetchable
SUPPORTED_BALANCE_CHAINS = [
    "EVM (Ethereum / Arbitrum / Base)",
    "EVM Railgun",
    "BTC Taproot (bc1p)",
    "BTC SegWit (bc1q)",
    "BTC (Bitcoin)",
    "SOL (Solana)",
    "DASH (Dash)",
    "SUI (Sui)",
    "HYPE (Hyperliquid)",
    "Hyperliquid L1 (Spot)",
    "ZEC (Zcash)",
    "XRP (Ripple)",
    "ADA (Cardano)",
    "ATOM (Cosmos)",
    "SCRT (Secret Network)",
    "RUNE (THORChain)",
]


def is_balance_supported(chain_type: str) -> bool:
    """Check if a chain type supports balance fetching.

    Args:
        chain_type: The chain label from the vault.

    Returns:
        True if balance fetching is supported for this chain type.
    """
    chain_lower = chain_type.lower()
    keywords = [
        "evm", "ethereum", "eth", "arbitrum", "base", "bsc", "polygon", "optimism",
        "btc", "bitcoin", "sol", "solana", "dash", "sui", "hype", "hyperliquid",
        "hl1", "(spot)", "zec", "zcash", "xrp", "ripple", "ada", "cardano",
        "atom", "cosmos", "scrt", "secret", "rune", "thorchain", "tron"
    ]
    return any(k in chain_lower for k in keywords)