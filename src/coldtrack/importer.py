"""ColdTrack importer — bridge from ColdStack vault to ColdTrack DB.

Reads account data from the unlocked vault's in-memory address_db and syncs
it into the ColdTrack SQLite ledger. Only public identifiers (names, addresses,
chains) cross the bridge. Private keys and mnemonics NEVER cross.
"""
from typing import Any, Dict, Optional

from coldtrack.db import ColdTrackDB


class ColdTrackImporter:
    """Bridge: ColdStack vault → ColdTrack database.

    Reads account data from the unlocked vault's in-memory address_db
    and syncs it into the ColdTrack SQLite ledger. This is a controlled
    disclosure — only public identifiers (names, addresses, chains) cross
    the bridge. Private keys and mnemonics NEVER cross.
    """

    def __init__(self, db: ColdTrackDB, address_db: Dict[str, Any]) -> None:
        """
        Args:
            db: ColdTrackDB instance (initialized)
            address_db: The unlocked vault's address_db dict
        """
        self.db = db
        self.address_db = address_db

    def sync_portfolios(self) -> None:
        """Ensure default portfolios exist in the DB.

        For now, create 'Executive Mind' and 'Kit & Paul' if they don't exist.
        These are the two known portfolios from the taxonomy.
        """
        self.db.upsert_portfolio(
            "Executive Mind",
            display_name="Executive Mind",
            type="internal",
            reporting_currency="AUD",
            tax_jurisdiction="AU",
        )
        self.db.upsert_portfolio(
            "Kit & Paul",
            display_name="Kit & Paul",
            type="internal",
            reporting_currency="AUD",
            tax_jurisdiction="AU",
        )

    def sync_accounts(self) -> int:
        """Sync all accounts from address_db into ColdTrack DB.

        Map each ColdStack account/address to a ColdTrack ACCOUNTS row.
        Returns the number of accounts synced (rows upserted).

        Mapping logic:
        - Each ColdStack account → ColdTrack ACCOUNT (type='wallet')
        - Account name → ACCOUNTS.NAME
        - Address → ACCOUNTS.ADDRESS
        - Chain → ACCOUNTS.CHAIN (normalized to uppercase)
        - IS_PRIVACY_SHIELDED: 1 if "railgun" appears in chain/coin
        - Portfolio: all → 'Executive Mind' (Phase 7 adds multi-portfolio)
        """
        count = 0
        # Get the default portfolio ID for 'Executive Mind'
        portfolios = self.db.get_portfolios()
        portfolio_id: Optional[int] = None
        for p in portfolios:
            if p["NAME"] == "Executive Mind":
                portfolio_id = p["ID"]
                break
        if portfolio_id is None:
            portfolio_id = self.db.upsert_portfolio("Executive Mind")

        accounts_data = self.address_db.get("accounts", {})
        for account_name, account_data in accounts_data.items():
            addresses = account_data.get("addresses", [])
            if not addresses:
                # Account with no addresses — create a bare row
                self.db.upsert_account(
                    portfolio_id,
                    account_name,
                    type="wallet",
                    chain=None,
                    address=None,
                    platform=None,
                )
                count += 1
                continue

            for addr_entry in addresses:
                address = addr_entry.get("address", "")
                chain_raw = addr_entry.get("chain", "")
                coin = addr_entry.get("coin", "")

                # Normalize chain to uppercase (e.g. "EVM (Ethereum / ...)" → "ETHEREUM")
                chain = self._normalize_chain(chain_raw)

                # Detect Railgun-shielded addresses (heuristic: "railgun" in chain/coin)
                shielded = 0
                combined = f"{chain_raw} {coin}".lower()
                if "railgun" in combined:
                    shielded = 1

                # Use the first address as the primary for this account
                if count == 0 or address:
                    self.db.upsert_account(
                        portfolio_id,
                        account_name,
                        type="wallet",
                        chain=chain or coin or None,
                        address=address or None,
                        platform=None,
                        is_privacy_shielded=shielded,
                    )
                    count += 1
                    break  # One ColdStack account = one ColdTrack account (first address)

        return count

    def full_sync(self) -> Dict[str, int]:
        """Run sync_portfolios() then sync_accounts(). Returns summary dict."""
        self.sync_portfolios()
        accounts_synced = self.sync_accounts()
        portfolios = self.db.get_portfolios()
        return {"portfolios": len(portfolios), "accounts": accounts_synced}

    @staticmethod
    def _normalize_chain(chain_raw: str) -> str:
        """Normalize a chain label to a short uppercase identifier.

        Examples:
            "EVM (Ethereum / Arbitrum / Base)" → "ETHEREUM"
            "SOL (Solana)" → "SOLANA"
            "BTC Taproot (bc1p)" → "BTC"
            "HYPE (Hyperliquid)" → "HYPERLIQUID"
            "DASH (Dash)" → "DASH"
            "SUI (Sui)" → "SUI"
        """
        if not chain_raw:
            return ""
        # Strip em-dashes and parenthetical extras
        chain = chain_raw.split("—")[0].split("(")[0].strip()
        # Common mappings
        upper = chain.upper()
        if "EVM" in upper or "ETHEREUM" in upper or "ARBITRUM" in upper or "BASE" in upper:
            return "ETHEREUM"
        if "SOL" in upper:
            return "SOLANA"
        if "BTC" in upper or "BITCOIN" in upper:
            return "BITCOIN"
        if "DASH" in upper:
            return "DASH"
        if "SUI" in upper:
            return "SUI"
        if "HYPE" in upper or "HYPERLIQUID" in upper or "HYPEREVM" in upper:
            return "HYPEREVM"
        if "BSC" in upper or "BNB" in upper or "BINANCE" in upper:
            return "BSC"
        # Fallback: return the cleaned-up original
        return upper if upper else chain_raw
