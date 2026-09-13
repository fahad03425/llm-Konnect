"""Module 1.2 — Data connectors (Tally). Month 1."""

import pandas as pd
import sqlite3
import os
from typing import Optional
from app.connectors.base import Connector

class LocalDBConnector(Connector):
    def __init__(self, path_or_url: str, db_type: str = "sqlite"):
        self.path_or_url = path_or_url
        self.db_type = db_type.lower()
        
    def _get_sqlite_conn(self):
        return sqlite3.connect(self.path_or_url)
        
    def _get_odbc_conn(self):
        try:
            import pyodbc
        except ImportError:
            raise ImportError(
                "pyodbc is required for MS Access / ODBC connections but is not installed. "
                "Please install it using: pip install pyodbc"
            )
            
        # Example connection string for Access
        conn_str = (
            r"DRIVER={Microsoft Access Driver (*.mdb, *.accdb)};"
            f"DBQ={self.path_or_url};"
        )
        try:
            return pyodbc.connect(conn_str)
        except pyodbc.Error as e:
            raise RuntimeError(
                f"Failed to connect to MS Access database. Ensure you have the correct Access ODBC drivers installed. Error: {e}"
            )

    def _get_default_sqlite_table(self) -> Optional[str]:
        """Auto-detect the first user table in a SQLite database."""
        try:
            with self._get_sqlite_conn() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%';")
                tables = [r[0] for r in cursor.fetchall()]
                return tables[0] if tables else None
        except Exception:
            return None

    def fetch(self, table_or_query: Optional[str] = None, **kwargs) -> pd.DataFrame:
        if not table_or_query:
            if self.db_type == "sqlite":
                table_or_query = self._get_default_sqlite_table()
                if not table_or_query:
                    return pd.DataFrame()
            else:
                raise ValueError("Must provide 'table_or_query' to fetch from DB")
            
        if self.db_type == "tally":
            raise NotImplementedError(
                "TallyPrime HTTP/XML interface integration is scheduled for a later stage."
            )
            
        is_query = table_or_query.strip().lower().startswith("select ")
        query = table_or_query if is_query else f"SELECT * FROM {table_or_query}"
        
        if self.db_type == "sqlite":
            with self._get_sqlite_conn() as conn:
                df = pd.read_sql_query(query, conn)
        elif self.db_type == "access":
            with self._get_odbc_conn() as conn:
                df = pd.read_sql_query(query, conn)
        else:
            raise ValueError(f"Unsupported db_type: {self.db_type}")
            
        df['source_connector'] = self.db_type
        df['source_row'] = df.index + 1
        return df

    def preview(self, n: int = 5, table_or_query: Optional[str] = None, **kwargs) -> pd.DataFrame:
        if not table_or_query:
            if self.db_type == "sqlite":
                table_or_query = self._get_default_sqlite_table()
                if not table_or_query:
                    return pd.DataFrame()
            else:
                raise ValueError("Must provide 'table_or_query' to preview from DB")
            
        if self.db_type == "tally":
            raise NotImplementedError(
                "TallyPrime HTTP/XML interface integration is scheduled for a later stage."
            )
            
        is_query = table_or_query.strip().lower().startswith("select ")
        query = table_or_query if is_query else f"SELECT * FROM {table_or_query}"
        
        # Modify query for limit based on db_type
        if self.db_type == "sqlite":
            query = f"{query} LIMIT {n}"
            with self._get_sqlite_conn() as conn:
                df = pd.read_sql_query(query, conn)
        elif self.db_type == "access":
            # Access uses TOP N
            if not is_query:
                query = f"SELECT TOP {n} * FROM {table_or_query}"
            # if it is a complex query, we might just read and head()
            with self._get_odbc_conn() as conn:
                df = pd.read_sql_query(query, conn)
                df = df.head(n)
        else:
            raise ValueError(f"Unsupported db_type: {self.db_type}")
            
        df['source_connector'] = self.db_type
        df['source_row'] = df.index + 1
        return df

    def describe(self) -> str:
        return f"Local DB Connector ({self.db_type}) reading from {self.path_or_url}"
