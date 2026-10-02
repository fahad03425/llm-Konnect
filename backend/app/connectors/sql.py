import os
import re
import urllib.parse
from urllib.parse import urlparse, urlsplit, urlunsplit
import pandas as pd
from typing import Optional, Dict, Any, List
from app.connectors.base import Connector

def get_best_odbc_driver() -> str:
    """Returns the newest available Microsoft SQL Server ODBC driver."""
    try:
        import pyodbc
        drivers = pyodbc.drivers()
        for preferred in ["ODBC Driver 18 for SQL Server", "ODBC Driver 17 for SQL Server", "SQL Server"]:
            if preferred in drivers:
                return preferred
        for d in drivers:
            if "sql server" in d.lower():
                return d
    except Exception:
        pass
    return "ODBC Driver 18 for SQL Server"

def get_local_mssql_instances() -> list[str]:
    """
    Dynamically discovers all installed Microsoft SQL Server instances on this computer.
    Uses the Windows Registry HKLM:\\SOFTWARE\\Microsoft\\Microsoft SQL Server\\Instance Names\\SQL,
    plus standard local instances as fallbacks.
    """
    instances = []
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Microsoft SQL Server\Instance Names\SQL") as key:
            i = 0
            while True:
                try:
                    name, _, _ = winreg.EnumValue(key, i)
                    if name.upper() == "MSSQLSERVER":
                        instances.extend([".", "localhost", "(local)"])
                    else:
                        instances.append(f".\\{name}")
                    i += 1
                except OSError:
                    break
    except Exception:
        pass

    for fb in [r".\SQLEXPRESS", r".\SQLEXPRESS01", "(local)", "localhost", "127.0.0.1"]:
        if fb not in instances:
            instances.append(fb)
    return instances

def find_local_mssql_instance_for_db(db_name: str) -> tuple[str, str]:
    """Finds which local SQL Server instance hosts the given database name."""
    driver = get_best_odbc_driver()
    candidates = get_local_mssql_instances()
    try:
        import pyodbc
        for inst in candidates:
            try:
                conn_str = f"DRIVER={{{driver}}};SERVER={inst};Trusted_Connection=yes;TrustServerCertificate=yes;Encrypt=optional;"
                conn = pyodbc.connect(conn_str, timeout=1)
                cur = conn.cursor()
                cur.execute("SELECT name FROM sys.databases WHERE LOWER(name) = LOWER(?)", (db_name,))
                row = cur.fetchone()
                conn.close()
                if row:
                    return inst, driver
            except Exception:
                continue
    except Exception:
        pass
    return candidates[0], driver

def parse_mssql_target(clean: str) -> tuple[str, str]:
    """Parses server instance and database name from user string (e.g. .\\SQLEXPRESS/PharmacyPOS)."""
    clean = clean.strip()
    if "/" in clean:
        server, db = clean.rsplit("/", 1)
        return server.strip(), db.strip()
    if ":" in clean and not clean.lower().startswith(("mssql", "http")):
        server, db = clean.rsplit(":", 1)
        return server.strip(), db.strip()
    if "\\" in clean:
        parts = [p for p in clean.split("\\") if p]
        if len(parts) >= 2 and parts[0] in (".", "localhost", "127.0.0.1", "(local)"):
            if len(parts) == 2:
                return f".\\{parts[1]}", ""
            elif len(parts) >= 3:
                return f".\\{parts[1]}", parts[2]
        elif len(parts) == 2:
            return parts[0], parts[1]
    return "", clean

def resolve_sql_connection(raw_connection: str, db_type: str = "sqlite") -> tuple[str, str, str]:
    """
    Transforms raw input (e.g. .mdf file path, plain database name, or connection string)
    into a valid SQLAlchemy connection URL, db_type, and extracted database name.
    """
    clean = raw_connection.strip("\"' ")
    clean_lower = clean.lower()

    # Case 1: .mdf file (physical SQL Server database file)
    if clean_lower.endswith(".mdf"):
        base = os.path.basename(clean)
        db_name, _ = os.path.splitext(base)
        server, driver = find_local_mssql_instance_for_db(db_name)
        odbc_str = f"DRIVER={{{driver}}};SERVER={server};DATABASE={db_name};Trusted_Connection=yes;TrustServerCertificate=yes;Encrypt=optional;"
        sqlalchemy_url = f"mssql+pyodbc:///?odbc_connect={urllib.parse.quote_plus(odbc_str)}"
        return sqlalchemy_url, "mssql", db_name

    # Case 2: SQLite file (.db / .sqlite)
    has_url_scheme = bool(re.match(r"^[a-z][a-z0-9+.-]*://", clean_lower))
    if (not has_url_scheme and clean_lower.endswith((".db", ".sqlite", ".sqlite3", ".stardb"))) or (
        db_type == "sqlite" and not has_url_scheme and ("/" in clean or "\\" in clean)
    ):
        clean_path = clean.replace("sqlite:///", "").replace("\\", "/")
        base = os.path.basename(clean_path)
        db_name, _ = os.path.splitext(base)
        return clean, "sqlite", db_name or "sqlite_database"

    # Case 3: Bare database name or instance/dbname for MSSQL (e.g. .\SQLEXPRESS/PharmacyPOS or PharmacyPOS)
    if db_type == "mssql" and not clean_lower.startswith(("mssql", "postgresql", "mysql", "sqlite", "driver=")):
        server, db_name = parse_mssql_target(clean)
        if not server:
            server, driver = find_local_mssql_instance_for_db(db_name)
        else:
            driver = get_best_odbc_driver()
        odbc_str = f"DRIVER={{{driver}}};SERVER={server};DATABASE={db_name};Trusted_Connection=yes;TrustServerCertificate=yes;Encrypt=optional;"
        sqlalchemy_url = f"mssql+pyodbc:///?odbc_connect={urllib.parse.quote_plus(odbc_str)}"
        return sqlalchemy_url, "mssql", db_name

    # Case 4: Raw ODBC connection string (only if not already a SQLAlchemy URL scheme)
    is_uri_scheme = clean_lower.startswith(("mssql://", "mssql+", "postgresql://", "postgres://", "mysql://", "mysql+", "sqlite://"))
    if not is_uri_scheme and ("driver=" in clean_lower or ("server=" in clean_lower and "database=" in clean_lower)):
        if "trustservercertificate" not in clean_lower:
            clean += ";TrustServerCertificate=yes;Encrypt=optional"
        sqlalchemy_url = f"mssql+pyodbc:///?odbc_connect={urllib.parse.quote_plus(clean)}"
        db_match = re.search(r'(?:Database|Initial Catalog)\s*=\s*([^;&]+)', clean, re.IGNORECASE)
        db_name = db_match.group(1).strip() if db_match else "MSSQL_Database"
        return sqlalchemy_url, "mssql", db_name

    # Case 5: Standard URI schemes
    detected_type = db_type
    db_name = ""
    if clean_lower.startswith("mssql") or "sqlexpress" in clean_lower:
        detected_type = "mssql"
        if "trustservercertificate" not in clean_lower:
            delim = "&" if "?" in clean else "?"
            clean = f"{clean}{delim}TrustServerCertificate=yes"
        m = re.search(r'/([^/?@:]+)(?:\?|$)', clean)
        if m:
            db_name = m.group(1).strip()
    elif clean_lower.startswith("postgresql") or clean_lower.startswith("postgres"):
        detected_type = "postgresql"
        m = re.search(r'/([^/?@:]+)(?:\?|$)', clean)
        if m:
            db_name = m.group(1).strip()
        # Use the maintained psycopg v3 SQLAlchemy dialect by default. Explicit
        # driver URLs (postgresql+psycopg2://, etc.) remain untouched.
        parts = urlsplit(clean)
        if "+" not in parts.scheme:
            clean = urlunsplit(("postgresql+psycopg", parts.netloc, parts.path, parts.query, parts.fragment))
    elif clean_lower.startswith("mysql") or clean_lower.startswith("mariadb"):
        detected_type = "mysql"
        m = re.search(r'/([^/?@:]+)(?:\?|$)', clean)
        if m:
            db_name = m.group(1).strip()
        # PyMySQL is pure Python and works without native client libraries.
        parts = urlsplit(clean)
        if "+" not in parts.scheme:
            clean = urlunsplit(("mysql+pymysql", parts.netloc, parts.path, parts.query, parts.fragment))
    elif clean_lower.startswith("sqlite"):
        detected_type = "sqlite"

    return clean, detected_type, db_name

class SQLConnector(Connector):
    def __init__(self, connection_string: str, db_type: str = "sqlite"):
        self.raw_connection_string = connection_string.strip()
        resolved_url, resolved_type, extracted_name = resolve_sql_connection(self.raw_connection_string, db_type)
        self.connection_string = resolved_url
        self.db_type = resolved_type
        self._custom_db_name = extracted_name
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
        if self._custom_db_name:
            return self._custom_db_name

        if self.db_type == "sqlite":
            clean_path = self.connection_string.replace("sqlite:///", "")
            base = os.path.basename(clean_path)
            name, _ = os.path.splitext(base)
            return name or "sqlite_database"

        # Check for Database=... or Initial Catalog=... in ODBC style strings
        db_match = re.search(r'(?:Database|Initial Catalog)\s*=\s*([^;&]+)', self.raw_connection_string, re.IGNORECASE)
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

    @staticmethod
    def _quote_table_identifier(connection, table_name: str, db_type: str) -> str:
        """Quote a discovered table name for the active SQL dialect."""
        if hasattr(connection, "dialect"):
            preparer = connection.dialect.identifier_preparer
            return ".".join(preparer.quote(part) for part in str(table_name).split("."))
        escaped = str(table_name).replace('"', '""')
        return f'"{escaped}"'

    def _validate_discovered_table(self, table_name: str) -> str:
        if table_name not in self.list_tables():
            raise ValueError(f"Table {table_name!r} was not found among discovered tables")
        return table_name

    def get_table_primary_key(self, table_name: str) -> List[str]:
        """Discover primary key column(s) for a given table."""
        conn = self._get_engine()
        try:
            if self.db_type == "sqlite":
                if hasattr(conn, 'cursor'):
                    cursor = conn.cursor()
                    safe_table = str(table_name).replace('"', '""')
                    cursor.execute(f'PRAGMA table_info("{safe_table}")')
                    cols = cursor.fetchall()
                    pk_cols = [c[1] for c in cols if len(c) > 5 and c[5] > 0]
                    if pk_cols:
                        return pk_cols
            from sqlalchemy import inspect
            inspector = inspect(conn)
            pk_dict = inspector.get_pk_constraint(table_name)
            if pk_dict and pk_dict.get("constrained_columns"):
                return list(pk_dict["constrained_columns"])
        except Exception:
            pass
        finally:
            if hasattr(conn, 'close') and self.db_type == "sqlite":
                conn.close()
                self._engine = None
        return []

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
                    self._validate_discovered_table(table)
                    table_ref = self._quote_table_identifier(conn, table, self.db_type)
                    count_df = pd.read_sql(f"SELECT COUNT(*) AS total_count FROM {table_ref}", conn)
                    row_count = int(count_df.iloc[0, 0])
                except Exception:
                    try:
                        count_df = pd.read_sql(f"SELECT COUNT(*) AS total_count FROM {table_ref}", conn)
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
                clean_df = sample_df[cols].astype(object).where(pd.notnull(sample_df[cols]), None)
                sample_rows = clean_df.to_dict(orient="records")

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

    def _get_default_table(self) -> str:
        tables = self.list_tables()
        if not tables:
            raise ValueError(f"No tables found in database '{self.extract_db_name()}'")
        # Prefer recognizable tables like products or sales if present
        for pref in ["tbl_products", "tbl_salesheader", "products", "sales", "items"]:
            for t in tables:
                if t.lower() == pref:
                    return t
        return tables[0]

    def total_rows(self, table_or_query: Optional[str] = None, **kwargs) -> int:
        target = table_or_query or self._get_default_table()
        conn = self._get_engine()
        try:
            if target.strip().lower().startswith(("select ", "with ")):
                query = f"SELECT COUNT(*) AS total_count FROM ({target}) AS source_rows"
            else:
                self._validate_discovered_table(target)
                table_ref = self._quote_table_identifier(conn, target, self.db_type)
                query = f"SELECT COUNT(*) AS total_count FROM {table_ref}"
            count_df = pd.read_sql(query, conn)
            return int(count_df.iloc[0, 0])
        except Exception:
            return 0
        finally:
            if hasattr(conn, 'close') and self.db_type == "sqlite":
                conn.close()
                self._engine = None

    def fetch(self, table_or_query: Optional[str] = None, watermark_column: Optional[str] = None, watermark_value: Optional[Any] = None, **kwargs) -> pd.DataFrame:
        target = table_or_query or self._get_default_table()
        is_query = target.strip().lower().startswith(("select ", "with "))
        conn = self._get_engine()
        if is_query:
            query = target
        else:
            self._validate_discovered_table(target)
            table_ref = self._quote_table_identifier(conn, target, self.db_type)
            query = f"SELECT * FROM {table_ref}"

        params = None
        if watermark_column and watermark_value is not None:
            col_ref = self._quote_table_identifier(conn, watermark_column, self.db_type)
            if " WHERE " in query.upper():
                query += f" AND {col_ref} > :__watermark_value"
            else:
                query += f" WHERE {col_ref} > :__watermark_value"
            params = {"__watermark_value": watermark_value}

        try:
            if self.db_type == "sqlite" and not hasattr(conn, 'connect'):
                df = pd.read_sql_query(query, conn, params=params)
            else:
                df = pd.read_sql(query, conn, params=params)
        finally:
            if hasattr(conn, 'close') and self.db_type == "sqlite":
                conn.close()
                self._engine = None

        df['source_connector'] = f"sql_{self.db_type}"
        df['source_row'] = df.index + 1
        return df

    def preview(self, n: int = 5, table_or_query: Optional[str] = None, **kwargs) -> pd.DataFrame:
        target = table_or_query or self._get_default_table()
        is_query = target.strip().lower().startswith(("select ", "with "))
        conn = self._get_engine()
        try:
            table_ref = None
            if not is_query:
                self._validate_discovered_table(target)
                table_ref = self._quote_table_identifier(conn, target, self.db_type)
            if self.db_type == "mssql":
                if is_query:
                    # If it already starts with SELECT, try inserting TOP n if not already present
                    if "TOP " not in target.upper():
                        query = re.sub(r'^(SELECT\s+)(DISTINCT\s+)?', rf'\1\2TOP {n} ', target, flags=re.IGNORECASE)
                    else:
                        query = target
                else:
                    query = f"SELECT TOP {n} * FROM {table_ref}"
                df = pd.read_sql(query, conn)
            elif self.db_type == "sqlite" and not hasattr(conn, 'connect'):
                query = target if is_query else f"SELECT * FROM {table_ref}"
                limited_query = f"{query} LIMIT {n}"
                df = pd.read_sql_query(limited_query, conn)
            else:
                query = target if is_query else f"SELECT * FROM {table_ref}"
                limited_query = f"{query} LIMIT {n}"
                df = pd.read_sql(limited_query, conn)
        except Exception:
            # Fallback if LIMIT or syntax fails
            query = target if is_query else f"SELECT * FROM {table_ref}"
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
        # Connection URLs and ODBC strings can carry usernames/passwords.
        return f"Universal SQL Connector ({self.db_type}) -> {self.extract_db_name()}"

    def capabilities(self) -> Dict[str, Any]:
        return {
            "db_type": self.db_type,
            "supports_watermark": True,
            "supports_custom_query": True,
            "supports_schema_discovery": True
        }
