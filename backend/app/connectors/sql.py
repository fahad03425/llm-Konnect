import os
import re
from urllib.parse import urlparse
import pandas as pd
from typing import Optional, Dict, Any, List
from app.connectors.base import Connector

class SQLConnector(Connector):
    def __init__(self, connection_string: str, db_type: str = "sqlite"):
        self.connection_string = connection_string.strip()
        self.db_type = db_type.lower()
        
        # Intelligent auto-detection from URI scheme
        conn_lower = self.connection_string.lower()
        if conn_lower.startswith("mssql") or "sqlexpress" in conn_lower or "driver=" in conn_lower:
            self.db_type = "mssql"
        elif conn_lower.startswith("postgresql") or conn_lower.startswith("postgres"):
            self.db_type = "postgresql"
        elif conn_lower.startswith("mysql") or conn_lower.startswith("mariadb"):
            self.db_type = "mysql"
        elif conn_lower.startswith("sqlite") or conn_lower.endswith(".db") or conn_lower.endswith(".sqlite"):
            self.db_type = "sqlite"

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

    def extract_db_name(self) -> str:
        """Extract a clean, user-friendly database name from the connection string or file path."""
        if self.db_type == "sqlite":
            clean_path = self.connection_string.replace("sqlite:///", "")
            base = os.path.basename(clean_path)
            name, _ = os.path.splitext(base)
            return name or "sqlite_database"

        # Check for Database=... or Initial Catalog=... in ODBC style strings
        db_match = re.search(r'(?:Database|Initial Catalog)\s*=\s*([^;&]+)', self.connection_string, re.IGNORECASE)
        if db_match:
            return db_match.group(1).strip()

        # Parse standard SQLAlchemy/URI URLs
        try:
            parsed = urlparse(self.connection_string)
            path_part = parsed.path.lstrip('/')
            if path_part:
                # Remove query params or subpaths
                db_name = path_part.split('?')[0].split('/')[0]
                if db_name:
                    return db_name
        except Exception:
            pass

        # Fallback: regex search after host/instance
        m = re.search(r'/([^/?]+)(?:\?|$)', self.connection_string)
        if m:
            return m.group(1).strip()

        return f"{self.db_type.upper()}_Database"

    def list_tables(self) -> List[str]:
        """List all non-system user tables in the database."""
        conn = self._get_engine()
        tables = []
        try:
            if self.db_type == "sqlite":
                if hasattr(conn, 'cursor'):
                    cursor = conn.cursor()
                    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name NOT LIKE 'file_registry' AND name NOT LIKE 'file_hash_cache' ORDER BY name")
                    tables = [row[0] for row in cursor.fetchall()]
                else:
                    from sqlalchemy import inspect
                    inspector = inspect(conn)
                    tables = [t for t in inspector.get_table_names() if not t.startswith("sqlite_")]
            else:
                from sqlalchemy import inspect
                inspector = inspect(conn)
                tables = inspector.get_table_names()
                # Filter out system tables if any
                tables = [t for t in tables if not t.startswith("sys") and not t.startswith("dtproperties")]
        finally:
            if hasattr(conn, 'close') and self.db_type == "sqlite":
                conn.close()
                self._engine = None

        return tables

    def inspect_database(self, sample_n: int = 5) -> Dict[str, Any]:
        """Auto-discover all tables with estimated row counts, column lists, and sample rows."""
        db_name = self.extract_db_name()
        tables = self.list_tables()
        table_details = []

        for table in tables:
            try:
                # Get row count
                conn = self._get_engine()
                row_count = 0
                try:
                    count_df = pd.read_sql(f"SELECT COUNT(*) AS total_count FROM [{table}]" if self.db_type == "mssql" else f"SELECT COUNT(*) AS total_count FROM {table}", conn)
                    row_count = int(count_df.iloc[0, 0])
                except Exception:
                    try:
                        count_df = pd.read_sql(f"SELECT COUNT(*) AS total_count FROM {table}", conn)
                        row_count = int(count_df.iloc[0, 0])
                    except Exception:
                        row_count = 0
                finally:
                    if hasattr(conn, 'close') and self.db_type == "sqlite":
                        conn.close()
                        self._engine = None

                # Get preview sample
                sample_df = self.preview(n=sample_n, table_or_query=table)
                # Drop tracking columns from preview
                cols = [c for c in sample_df.columns if c not in ('source_connector', 'source_row')]
                sample_rows = sample_df[cols].to_dict(orient="records")

                table_details.append({
                    "table_name": table,
                    "row_count": row_count,
                    "columns": cols,
                    "sample_rows": sample_rows
                })
            except Exception as e:
                table_details.append({
                    "table_name": table,
                    "row_count": 0,
                    "columns": [],
                    "sample_rows": [],
                    "error": str(e)
                })

        return {
            "database_name": db_name,
            "db_type": self.db_type,
            "total_tables": len(tables),
            "tables": table_details
        }

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
                self._engine = None

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
                self._engine = None

        df['source_connector'] = f"sql_{self.db_type}"
        df['source_row'] = df.index + 1
        return df

    def describe(self) -> str:
        return f"Universal SQL Connector ({self.db_type}) -> {self.connection_string}"

    def capabilities(self) -> Dict[str, Any]:
        return {
            "db_type": self.db_type,
            "supports_watermark": True,
            "supports_custom_query": True,
            "supports_schema_discovery": True
        }
