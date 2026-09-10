"""
Universal SQL Database Connector.
Supports SQLite, PostgreSQL, MySQL, MS SQL Server, and custom connection strings.
Includes support for incremental watermark queries.
"""

import pandas as pd
from typing import Optional, Dict, Any
from app.connectors.base import Connector

class SQLConnector(Connector):
    def __init__(self, connection_string: str, db_type: str = "sqlite"):
        self.connection_string = connection_string
        self.db_type = db_type.lower()
        self._engine = None

    def _get_engine(self):
        if self._engine is None:
            if self.db_type == "sqlite":
                import sqlite3
                # Remove sqlite:/// prefix if passed as file path
                clean_path = self.connection_string.replace("sqlite:///", "")
                return sqlite3.connect(clean_path)
            else:
                try:
                    from sqlalchemy import create_engine
                    self._engine = create_engine(self.connection_string)
                    return self._engine
                except ImportError:
                    raise ImportError(
                        "sqlalchemy package is required for SQL database connections. "
                        "Please install via: pip install sqlalchemy"
                    )
        return self._engine

    def fetch(self, table_or_query: Optional[str] = None, watermark_column: Optional[str] = None, watermark_value: Optional[Any] = None, **kwargs) -> pd.DataFrame:
        if not table_or_query:
            raise ValueError("Must provide 'table_or_query' to fetch from database")

        is_query = table_or_query.strip().lower().startswith("select ")
        query = table_or_query if is_query else f"SELECT * FROM {table_or_query}"

        if watermark_column and watermark_value:
            if " WHERE " in query.upper():
                query += f" AND {watermark_column} > '{watermark_value}'"
            else:
                query += f" WHERE {watermark_column} > '{watermark_value}'"

        conn = self._get_engine()
        try:
            if self.db_type == "sqlite" and not hasattr(conn, 'connect'):
                df = pd.read_sql_query(query, conn)
            else:
                df = pd.read_sql(query, conn)
        finally:
            if hasattr(conn, 'close') and self.db_type == "sqlite":
                conn.close()

        df['source_connector'] = f"sql_{self.db_type}"
        df['source_row'] = df.index + 1
        return df

    def preview(self, n: int = 5, table_or_query: Optional[str] = None, **kwargs) -> pd.DataFrame:
        if not table_or_query:
            raise ValueError("Must provide 'table_or_query' to preview from database")

        is_query = table_or_query.strip().lower().startswith("select ")
        
        if is_query:
            query = table_or_query
        else:
            query = f"SELECT * FROM {table_or_query}"

        conn = self._get_engine()
        try:
            if self.db_type == "sqlite" and not hasattr(conn, 'connect'):
                limited_query = f"{query} LIMIT {n}"
                df = pd.read_sql_query(limited_query, conn)
            else:
                limited_query = f"{query} LIMIT {n}"
                df = pd.read_sql(limited_query, conn)
        except Exception:
            # Fallback if LIMIT syntax fails on MSSQL or custom dialect
            if self.db_type == "sqlite" and not hasattr(conn, 'connect'):
                df = pd.read_sql_query(query, conn)
            else:
                df = pd.read_sql(query, conn)
            df = df.head(n)
        finally:
            if hasattr(conn, 'close') and self.db_type == "sqlite":
                conn.close()

        df['source_connector'] = f"sql_{self.db_type}"
        df['source_row'] = df.index + 1
        return df

    def describe(self) -> str:
        return f"Universal SQL Connector ({self.db_type}) -> {self.connection_string}"

    def capabilities(self) -> Dict[str, Any]:
        return {
            "db_type": self.db_type,
            "supports_watermark": True,
            "supports_custom_query": True
        }
