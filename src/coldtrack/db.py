"""ColdTrack SQLite database layer.

Stores the ledger database (coldtrack.db) co-located with key_vault.encrypted.
Schema v3.0 — 9 tables, ALL CAPS naming, INTEGER PRIMARY KEY AUTOINCREMENT.
"""
import sqlite3
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional


class ColdTrackDB:
    """SQLite database layer for ColdTrack ledger."""

    # ------------------------------------------------------------------
    # Connection / schema
    # ------------------------------------------------------------------

    def __init__(self, db_path: Optional[Path] = None) -> None:
        """Connect to coldtrack.db. If db_path is None, resolve base_dir
        the same way the vault does (EXE dir when frozen, project root
        when running from source)."""
        if db_path is None:
            if getattr(sys, "frozen", False):
                base_dir = Path(sys.executable).parent
            else:
                # src/coldtrack/db.py → project root is 3 levels up
                base_dir = Path(__file__).parent.parent.parent
            db_path = base_dir / "coldtrack.db"
        self.db_path = db_path
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")

    def init_schema(self) -> None:
        """Create all tables if they don't exist. Safe to call on every startup."""
        cur = self._conn.cursor()
        cur.executescript("""
        CREATE TABLE IF NOT EXISTS PORTFOLIOS (
            ID INTEGER PRIMARY KEY AUTOINCREMENT,
            NAME TEXT NOT NULL UNIQUE,
            DISPLAY_NAME TEXT,
            TYPE TEXT NOT NULL DEFAULT 'internal' CHECK(TYPE IN ('internal', 'client', 'pool')),
            REPORTING_CURRENCY TEXT NOT NULL DEFAULT 'USD',
            TAX_JURISDICTION TEXT,
            NOTES TEXT,
            CREATED_AT TEXT NOT NULL DEFAULT (datetime('now')),
            UPDATED_AT TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS ACCOUNTS (
            ID INTEGER PRIMARY KEY AUTOINCREMENT,
            PORTFOLIO_ID INTEGER NOT NULL REFERENCES PORTFOLIOS(ID),
            NAME TEXT NOT NULL,
            DISPLAY_NAME TEXT,
            TYPE TEXT NOT NULL CHECK(TYPE IN (
                'wallet', 'lp_position', 'vault', 'gss', 'cex', 'bank'
            )),
            CHAIN TEXT,
            ADDRESS TEXT,
            PLATFORM TEXT,
            CURRENCY TEXT NOT NULL DEFAULT 'USD',
            IS_PRIVACY_SHIELDED INTEGER DEFAULT 0,
            NOTES TEXT,
            CREATED_AT TEXT NOT NULL DEFAULT (datetime('now')),
            UPDATED_AT TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(PORTFOLIO_ID, NAME)
        );

        CREATE TABLE IF NOT EXISTS FX_RATES (
            ID INTEGER PRIMARY KEY AUTOINCREMENT,
            DATE TEXT NOT NULL,
            PAIR TEXT NOT NULL,
            RATE REAL NOT NULL,
            SOURCE TEXT NOT NULL DEFAULT 'Frankfurter API',
            CREATED_AT TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(DATE, PAIR)
        );

        CREATE TABLE IF NOT EXISTS TRANSACTIONS (
            ID INTEGER PRIMARY KEY AUTOINCREMENT,
            ACCOUNT_ID INTEGER NOT NULL REFERENCES ACCOUNTS(ID),
            DATE TEXT NOT NULL,
            TYPE TEXT NOT NULL,
            ASSET TEXT NOT NULL,
            AMOUNT REAL NOT NULL,
            VALUE_USD REAL,
            VALUE_CAD REAL,
            VALUE_EUR REAL,
            VALUE_AUD REAL,
            FX_RATE_CAD_USD REAL,
            FX_RATE_EUR_USD REAL,
            FX_RATE_AUD_USD REAL,
            CHAIN TEXT,
            TX_HASH TEXT,
            COUNTERPARTY_ASSET TEXT,
            COUNTERPARTY_AMOUNT REAL,
            FEE_ASSET TEXT,
            FEE_AMOUNT REAL,
            FEE_USD REAL,
            NOTES TEXT,
            CATEGORY TEXT,
            CREATED_AT TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS LP_POSITIONS (
            ID INTEGER PRIMARY KEY AUTOINCREMENT,
            ACCOUNT_ID INTEGER NOT NULL REFERENCES ACCOUNTS(ID),
            POOL_NAME TEXT NOT NULL,
            PLATFORM TEXT NOT NULL,
            CHAIN TEXT NOT NULL,
            POSITION_TYPE TEXT NOT NULL DEFAULT 'concentrated_liquidity'
                CHECK(POSITION_TYPE IN ('concentrated_liquidity', 'full_range', 'standard_amm')),
            POSITION_ID_TYPE TEXT,
            TOKEN_ID TEXT,
            POOL_ADDRESS TEXT,
            POSITION_ADDRESS TEXT,
            TOKEN_A TEXT NOT NULL,
            AMOUNT_A_ENTRY REAL,
            TOKEN_B TEXT NOT NULL,
            AMOUNT_B_ENTRY REAL,
            FEE_TIER REAL,
            TICK_LOWER REAL,
            TICK_UPPER REAL,
            RANGE_LOW REAL,
            RANGE_HIGH REAL,
            TOTAL_VALUE_USD_ENTRY REAL,
            TOTAL_VALUE_CAD_ENTRY REAL,
            TOTAL_VALUE_EUR_ENTRY REAL,
            FEES_EARNED_USD REAL DEFAULT 0,
            FEES_UNCLAIMED_USD REAL DEFAULT 0,
            FEES_CLAIMED_USD REAL DEFAULT 0,
            OPENED_DATE TEXT NOT NULL,
            CLOSED_DATE TEXT,
            STATUS TEXT NOT NULL DEFAULT 'active' CHECK(STATUS IN ('active', 'closed', 'migrated')),
            NOTES TEXT,
            CREATED_AT TEXT NOT NULL DEFAULT (datetime('now')),
            UPDATED_AT TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS LP_SNAPSHOTS (
            ID INTEGER PRIMARY KEY AUTOINCREMENT,
            LP_POSITION_ID INTEGER NOT NULL REFERENCES LP_POSITIONS(ID),
            REPORT_DATE TEXT NOT NULL,
            TOKEN_A_AMOUNT REAL,
            TOKEN_B_AMOUNT REAL,
            TOKEN_A_PRICE_USD REAL,
            TOKEN_B_PRICE_USD REAL,
            TOTAL_VALUE_USD REAL,
            TOTAL_VALUE_CAD REAL,
            TOTAL_VALUE_EUR REAL,
            FX_RATE_CAD_USD REAL,
            FX_RATE_EUR_USD REAL,
            IN_RANGE INTEGER DEFAULT 1,
            NOTES TEXT,
            CREATED_AT TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(LP_POSITION_ID, REPORT_DATE)
        );

        CREATE TABLE IF NOT EXISTS VAULT_DEPOSITS (
            ID INTEGER PRIMARY KEY AUTOINCREMENT,
            ACCOUNT_ID INTEGER NOT NULL REFERENCES ACCOUNTS(ID),
            VAULT_NAME TEXT NOT NULL,
            PLATFORM TEXT NOT NULL,
            VAULT_ADDRESS TEXT,
            ASSET TEXT NOT NULL,
            AMOUNT_DEPOSITED REAL NOT NULL,
            CURRENT_VALUE_USD REAL,
            CURRENT_VALUE_CAD REAL,
            CURRENT_VALUE_EUR REAL,
            APR REAL,
            ALLOCATION_PCT REAL,
            DEPOSIT_DATE TEXT NOT NULL,
            STATUS TEXT NOT NULL DEFAULT 'active' CHECK(STATUS IN ('active', 'withdrawn')),
            NOTES TEXT,
            CREATED_AT TEXT NOT NULL DEFAULT (datetime('now')),
            UPDATED_AT TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS HOLDINGS (
            ID INTEGER PRIMARY KEY AUTOINCREMENT,
            ACCOUNT_ID INTEGER NOT NULL REFERENCES ACCOUNTS(ID),
            ASSET TEXT NOT NULL,
            QUANTITY REAL NOT NULL,
            COST_BASIS_USD REAL,
            COST_BASIS_CAD REAL,
            COST_BASIS_EUR REAL,
            CURRENT_VALUE_USD REAL,
            CURRENT_VALUE_CAD REAL,
            CURRENT_VALUE_EUR REAL,
            CHAIN TEXT,
            LAST_UPDATED TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(ACCOUNT_ID, ASSET, CHAIN)
        );

        CREATE TABLE IF NOT EXISTS TAGS (
            ID INTEGER PRIMARY KEY AUTOINCREMENT,
            NAME TEXT NOT NULL UNIQUE,
            COLOR TEXT
        );

        CREATE TABLE IF NOT EXISTS TRANSACTION_TAGS (
            TRANSACTION_ID INTEGER NOT NULL REFERENCES TRANSACTIONS(ID),
            TAG_ID INTEGER NOT NULL REFERENCES TAGS(ID),
            PRIMARY KEY (TRANSACTION_ID, TAG_ID)
        );
        """)
        self._conn.commit()

    def close(self) -> None:
        """Close the database connection."""
        if self._conn:
            self._conn.close()
            self._conn = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _row_to_dict(self, row: Optional[sqlite3.Row]) -> Optional[Dict[str, Any]]:
        """Convert a sqlite3.Row to a plain dict, or return None."""
        if row is None:
            return None
        return {k: row[k] for k in row.keys()}

    def _rows_to_dicts(self, rows: List[sqlite3.Row]) -> List[Dict[str, Any]]:
        """Convert a list of sqlite3.Row to a list of plain dicts."""
        return [self._row_to_dict(r) for r in rows]

    # ------------------------------------------------------------------
    # PORTFOLIOS CRUD
    # ------------------------------------------------------------------

    def upsert_portfolio(
        self,
        name: str,
        display_name: Optional[str] = None,
        type: str = "internal",
        reporting_currency: str = "USD",
        tax_jurisdiction: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> int:
        """Insert or update a portfolio. Returns the portfolio ID."""
        cur = self._conn.cursor()
        cur.execute(
            """INSERT INTO PORTFOLIOS
               (NAME, DISPLAY_NAME, TYPE, REPORTING_CURRENCY, TAX_JURISDICTION, NOTES)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(NAME) DO UPDATE SET
                   DISPLAY_NAME = excluded.DISPLAY_NAME,
                   TYPE = excluded.TYPE,
                   REPORTING_CURRENCY = excluded.REPORTING_CURRENCY,
                   TAX_JURISDICTION = excluded.TAX_JURISDICTION,
                   NOTES = excluded.NOTES,
                   UPDATED_AT = datetime('now')
            """,
            (name, display_name, type, reporting_currency, tax_jurisdiction, notes),
        )
        self._conn.commit()
        return cur.lastrowid

    def get_portfolio(self, portfolio_id: int) -> Optional[Dict[str, Any]]:
        """Fetch a single portfolio by ID."""
        cur = self._conn.cursor()
        cur.execute("SELECT * FROM PORTFOLIOS WHERE ID = ?", (portfolio_id,))
        return self._row_to_dict(cur.fetchone())

    def get_portfolios(self) -> List[Dict[str, Any]]:
        """Fetch all portfolios."""
        cur = self._conn.cursor()
        cur.execute("SELECT * FROM PORTFOLIOS ORDER BY NAME")
        return self._rows_to_dicts(cur.fetchall())

    # ------------------------------------------------------------------
    # ACCOUNTS CRUD
    # ------------------------------------------------------------------

    def upsert_account(
        self,
        portfolio_id: int,
        name: str,
        type: str,
        display_name: Optional[str] = None,
        chain: Optional[str] = None,
        address: Optional[str] = None,
        platform: Optional[str] = None,
        currency: str = "USD",
        is_privacy_shielded: int = 0,
        notes: Optional[str] = None,
    ) -> int:
        """Insert or update an account. Returns the account ID."""
        cur = self._conn.cursor()
        cur.execute(
            """INSERT INTO ACCOUNTS
               (PORTFOLIO_ID, NAME, DISPLAY_NAME, TYPE, CHAIN, ADDRESS, PLATFORM,
                CURRENCY, IS_PRIVACY_SHIELDED, NOTES)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(PORTFOLIO_ID, NAME) DO UPDATE SET
                   DISPLAY_NAME = excluded.DISPLAY_NAME,
                   TYPE = excluded.TYPE,
                   CHAIN = excluded.CHAIN,
                   ADDRESS = excluded.ADDRESS,
                   PLATFORM = excluded.PLATFORM,
                   CURRENCY = excluded.CURRENCY,
                   IS_PRIVACY_SHIELDED = excluded.IS_PRIVACY_SHIELDED,
                   NOTES = excluded.NOTES,
                   UPDATED_AT = datetime('now')
            """,
            (portfolio_id, name, display_name, type, chain, address,
             platform, currency, is_privacy_shielded, notes),
        )
        self._conn.commit()
        return cur.lastrowid

    def get_account(self, account_id: int) -> Optional[Dict[str, Any]]:
        """Fetch a single account by ID."""
        cur = self._conn.cursor()
        cur.execute("SELECT * FROM ACCOUNTS WHERE ID = ?", (account_id,))
        return self._row_to_dict(cur.fetchone())

    def get_accounts(self, portfolio_id: Optional[int] = None) -> List[Dict[str, Any]]:
        """Fetch all accounts, optionally filtered by portfolio."""
        cur = self._conn.cursor()
        if portfolio_id is not None:
            cur.execute(
                "SELECT * FROM ACCOUNTS WHERE PORTFOLIO_ID = ? ORDER BY NAME",
                (portfolio_id,),
            )
        else:
            cur.execute("SELECT * FROM ACCOUNTS ORDER BY NAME")
        return self._rows_to_dicts(cur.fetchall())

    # ------------------------------------------------------------------
    # FX_RATES CRUD
    # ------------------------------------------------------------------

    def upsert_fx_rate(
        self,
        date: str,
        pair: str,
        rate: float,
        source: str = "Frankfurter API",
    ) -> None:
        """Insert or update an FX rate for a given date/pair."""
        cur = self._conn.cursor()
        cur.execute(
            """INSERT INTO FX_RATES (DATE, PAIR, RATE, SOURCE)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(DATE, PAIR) DO UPDATE SET
                   RATE = excluded.RATE,
                   SOURCE = excluded.SOURCE
            """,
            (date, pair, rate, source),
        )
        self._conn.commit()

    def get_fx_rate(self, date: str, pair: str) -> Optional[float]:
        """Get the FX rate for a specific date and pair."""
        cur = self._conn.cursor()
        cur.execute(
            "SELECT RATE FROM FX_RATES WHERE DATE = ? AND PAIR = ?",
            (date, pair),
        )
        row = cur.fetchone()
        return row["RATE"] if row else None

    def get_latest_fx_rate(self, pair: str) -> Optional[float]:
        """Get the most recent FX rate for a pair."""
        cur = self._conn.cursor()
        cur.execute(
            "SELECT RATE FROM FX_RATES WHERE PAIR = ? ORDER BY DATE DESC LIMIT 1",
            (pair,),
        )
        row = cur.fetchone()
        return row["RATE"] if row else None

    # ------------------------------------------------------------------
    # TRANSACTIONS CRUD
    # ------------------------------------------------------------------

    def insert_transaction(
        self,
        account_id: int,
        date: str,
        type: str,
        asset: str,
        amount: float,
        **kwargs: Any,
    ) -> int:
        """Insert a transaction. Returns the transaction ID."""
        cur = self._conn.cursor()
        columns = ["ACCOUNT_ID", "DATE", "TYPE", "ASSET", "AMOUNT"]
        values = [account_id, date, type, asset, amount]
        for key, val in kwargs.items():
            if val is not None:
                columns.append(key.upper())
                values.append(val)
        placeholders = ", ".join(["?"] * len(columns))
        col_names = ", ".join(columns)
        cur.execute(
            f"INSERT INTO TRANSACTIONS ({col_names}) VALUES ({placeholders})",
            values,
        )
        self._conn.commit()
        return cur.lastrowid

    def get_transactions(
        self,
        account_id: Optional[int] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        type: Optional[str] = None,
        category: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Fetch transactions with optional filters."""
        conditions: List[str] = []
        params: List[Any] = []
        if account_id is not None:
            conditions.append("ACCOUNT_ID = ?")
            params.append(account_id)
        if start_date is not None:
            conditions.append("DATE >= ?")
            params.append(start_date)
        if end_date is not None:
            conditions.append("DATE <= ?")
            params.append(end_date)
        if type is not None:
            conditions.append("TYPE = ?")
            params.append(type)
        if category is not None:
            conditions.append("CATEGORY = ?")
            params.append(category)
        where = " AND ".join(conditions) if conditions else "1=1"
        params.append(limit)
        cur = self._conn.cursor()
        cur.execute(
            f"SELECT * FROM TRANSACTIONS WHERE {where} ORDER BY DATE DESC, ID DESC LIMIT ?",
            params,
        )
        return self._rows_to_dicts(cur.fetchall())

    # ------------------------------------------------------------------
    # LP_POSITIONS CRUD
    # ------------------------------------------------------------------

    def upsert_lp_position(
        self,
        account_id: int,
        pool_name: str,
        platform: str,
        chain: str,
        token_a: str,
        token_b: str,
        opened_date: str,
        **kwargs: Any,
    ) -> int:
        """Insert or update an LP position. Returns the position ID."""
        cur = self._conn.cursor()
        columns = ["ACCOUNT_ID", "POOL_NAME", "PLATFORM", "CHAIN",
                    "TOKEN_A", "TOKEN_B", "OPENED_DATE"]
        values = [account_id, pool_name, platform, chain,
                  token_a, token_b, opened_date]
        for key, val in kwargs.items():
            if val is not None:
                columns.append(key.upper())
                values.append(val)
        placeholders = ", ".join(["?"] * len(columns))
        col_names = ", ".join(columns)
        # Use INSERT and let sqlite handle conflicts via UPDATE if unique constraint fires
        cur.execute(
            f"INSERT INTO LP_POSITIONS ({col_names}) VALUES ({placeholders})",
            values,
        )
        self._conn.commit()
        return cur.lastrowid

    def get_lp_positions(
        self,
        account_id: Optional[int] = None,
        status: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Fetch LP positions with optional filters."""
        conditions: List[str] = []
        params: List[Any] = []
        if account_id is not None:
            conditions.append("ACCOUNT_ID = ?")
            params.append(account_id)
        if status is not None:
            conditions.append("STATUS = ?")
            params.append(status)
        where = " AND ".join(conditions) if conditions else "1=1"
        cur = self._conn.cursor()
        cur.execute(
            f"SELECT * FROM LP_POSITIONS WHERE {where} ORDER BY OPENED_DATE DESC",
            params,
        )
        return self._rows_to_dicts(cur.fetchall())

    # ------------------------------------------------------------------
    # LP_SNAPSHOTS CRUD
    # ------------------------------------------------------------------

    def upsert_lp_snapshot(
        self,
        lp_position_id: int,
        report_date: str,
        **kwargs: Any,
    ) -> None:
        """Insert or update a daily LP snapshot. Uses UNIQUE constraint on (position_id, date)."""
        cur = self._conn.cursor()
        columns = ["LP_POSITION_ID", "REPORT_DATE"]
        values = [lp_position_id, report_date]
        for key, val in kwargs.items():
            if val is not None:
                columns.append(key.upper())
                values.append(val)
        # Build the UPDATE clause for ON CONFLICT
        update_cols = [c for c in columns if c not in ("LP_POSITION_ID", "REPORT_DATE")]
        update_set = ", ".join(f"{c} = excluded.{c}" for c in update_cols)
        placeholders = ", ".join(["?"] * len(columns))
        col_names = ", ".join(columns)
        cur.execute(
            f"""INSERT INTO LP_SNAPSHOTS ({col_names}) VALUES ({placeholders})
                ON CONFLICT(LP_POSITION_ID, REPORT_DATE) DO UPDATE SET {update_set}""",
            values,
        )
        self._conn.commit()

    def get_lp_snapshots(
        self,
        lp_position_id: int,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Fetch LP snapshots for a position."""
        conditions = ["LP_POSITION_ID = ?"]
        params: List[Any] = [lp_position_id]
        if start_date is not None:
            conditions.append("REPORT_DATE >= ?")
            params.append(start_date)
        if end_date is not None:
            conditions.append("REPORT_DATE <= ?")
            params.append(end_date)
        where = " AND ".join(conditions)
        cur = self._conn.cursor()
        cur.execute(
            f"SELECT * FROM LP_SNAPSHOTS WHERE {where} ORDER BY REPORT_DATE DESC",
            params,
        )
        return self._rows_to_dicts(cur.fetchall())

    # ------------------------------------------------------------------
    # VAULT_DEPOSITS CRUD
    # ------------------------------------------------------------------

    def upsert_vault_deposit(
        self,
        account_id: int,
        vault_name: str,
        platform: str,
        asset: str,
        amount_deposited: float,
        deposit_date: str,
        **kwargs: Any,
    ) -> int:
        """Insert or update a vault deposit. Returns the deposit ID."""
        cur = self._conn.cursor()
        columns = ["ACCOUNT_ID", "VAULT_NAME", "PLATFORM", "ASSET",
                    "AMOUNT_DEPOSITED", "DEPOSIT_DATE"]
        values = [account_id, vault_name, platform, asset,
                  amount_deposited, deposit_date]
        for key, val in kwargs.items():
            if val is not None:
                columns.append(key.upper())
                values.append(val)
        placeholders = ", ".join(["?"] * len(columns))
        col_names = ", ".join(columns)
        cur.execute(
            f"INSERT INTO VAULT_DEPOSITS ({col_names}) VALUES ({placeholders})",
            values,
        )
        self._conn.commit()
        return cur.lastrowid

    def get_vault_deposits(
        self,
        account_id: Optional[int] = None,
        status: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Fetch vault deposits with optional filters."""
        conditions: List[str] = []
        params: List[Any] = []
        if account_id is not None:
            conditions.append("ACCOUNT_ID = ?")
            params.append(account_id)
        if status is not None:
            conditions.append("STATUS = ?")
            params.append(status)
        where = " AND ".join(conditions) if conditions else "1=1"
        cur = self._conn.cursor()
        cur.execute(
            f"SELECT * FROM VAULT_DEPOSITS WHERE {where} ORDER BY DEPOSIT_DATE DESC",
            params,
        )
        return self._rows_to_dicts(cur.fetchall())

    # ------------------------------------------------------------------
    # HOLDINGS CRUD
    # ------------------------------------------------------------------

    def upsert_holding(
        self,
        account_id: int,
        asset: str,
        quantity: float,
        chain: Optional[str] = None,
        **kwargs: Any,
    ) -> None:
        """Insert or update a holding. Uses UNIQUE constraint on (account_id, asset, chain)."""
        cur = self._conn.cursor()
        columns = ["ACCOUNT_ID", "ASSET", "QUANTITY"]
        values = [account_id, asset, quantity]
        # Always include CHAIN (None is stored as NULL which is part of the UNIQUE)
        if chain is not None:
            columns.append("CHAIN")
            values.append(chain)
        for key, val in kwargs.items():
            if val is not None:
                columns.append(key.upper())
                values.append(val)
        update_cols = [c for c in columns if c not in ("ACCOUNT_ID", "ASSET", "CHAIN")]
        update_parts = [f"{c} = excluded.{c}" for c in update_cols]
        update_parts.append("LAST_UPDATED = datetime('now')")
        placeholders = ", ".join(["?"] * len(columns))
        col_names = ", ".join(columns)
        cur.execute(
            f"""INSERT INTO HOLDINGS ({col_names}) VALUES ({placeholders})
                ON CONFLICT(ACCOUNT_ID, ASSET, CHAIN) DO UPDATE SET
                {", ".join(update_parts)}""",
            values,
        )
        self._conn.commit()

    def get_holdings(self, account_id: Optional[int] = None) -> List[Dict[str, Any]]:
        """Fetch holdings with optional account filter."""
        cur = self._conn.cursor()
        if account_id is not None:
            cur.execute(
                "SELECT * FROM HOLDINGS WHERE ACCOUNT_ID = ? ORDER BY ASSET",
                (account_id,),
            )
        else:
            cur.execute("SELECT * FROM HOLDINGS ORDER BY ASSET")
        return self._rows_to_dicts(cur.fetchall())

    # ------------------------------------------------------------------
    # TAGS CRUD
    # ------------------------------------------------------------------

    def upsert_tag(self, name: str, color: Optional[str] = None) -> int:
        """Insert or update a tag. Returns the tag ID."""
        cur = self._conn.cursor()
        cur.execute(
            """INSERT INTO TAGS (NAME, COLOR) VALUES (?, ?)
               ON CONFLICT(NAME) DO UPDATE SET COLOR = excluded.COLOR""",
            (name, color),
        )
        self._conn.commit()
        return cur.lastrowid

    def tag_transaction(self, transaction_id: int, tag_id: int) -> None:
        """Associate a tag with a transaction."""
        cur = self._conn.cursor()
        cur.execute(
            "INSERT OR IGNORE INTO TRANSACTION_TAGS (TRANSACTION_ID, TAG_ID) VALUES (?, ?)",
            (transaction_id, tag_id),
        )
        self._conn.commit()

    def get_transaction_tags(self, transaction_id: int) -> List[Dict[str, Any]]:
        """Fetch tags for a transaction."""
        cur = self._conn.cursor()
        cur.execute(
            """SELECT t.* FROM TAGS t
               JOIN TRANSACTION_TAGS tt ON t.ID = tt.TAG_ID
               WHERE tt.TRANSACTION_ID = ?""",
            (transaction_id,),
        )
        return self._rows_to_dicts(cur.fetchall())
