"""Opt-in smoke tests against dedicated relational database endpoints.

Set KONNECT_TEST_POSTGRES_URL, KONNECT_TEST_MYSQL_URL,
KONNECT_TEST_MARIADB_URL, and/or KONNECT_TEST_SQLSERVER_URL to run these. The
tests create one uniquely named scratch table, exercise discovery/read/preview,
and drop only that table afterward. Use disposable test databases and a user
that can create and drop tables.
"""
import os
import uuid

import pandas as pd
import pytest

from app.connectors.sql import SQLConnector


@pytest.mark.parametrize(("env_name", "db_type"), [
    ("KONNECT_TEST_POSTGRES_URL", "postgresql"),
    ("KONNECT_TEST_MYSQL_URL", "mysql"),
    ("KONNECT_TEST_MARIADB_URL", "mysql"),
    ("KONNECT_TEST_SQLSERVER_URL", "mssql"),
])
def test_external_database_discovery_fetch_and_preview(env_name, db_type):
    url = os.getenv(env_name)
    if not url:
        pytest.skip(f"Set {env_name} to enable this live connector check")
    sqlalchemy = pytest.importorskip("sqlalchemy", reason="Install backend SQLAlchemy dependencies to run live connector checks")
    from sqlalchemy import text

    engine = sqlalchemy.create_engine(url, pool_pre_ping=True, connect_args={"connect_timeout": 5} if db_type != "mssql" else {"timeout": 5})
    table = "codex_db_smoke_" + uuid.uuid4().hex[:12]
    preparer = engine.dialect.identifier_preparer
    quoted_table = preparer.quote(table)
    connector = SQLConnector(url, db_type=db_type)
    try:
        with engine.begin() as conn:
            if db_type == "mssql":
                conn.execute(text(f"CREATE TABLE {quoted_table} (record_id VARCHAR(40) NOT NULL, quantity INT NOT NULL)"))
            else:
                conn.execute(text(f"CREATE TABLE {quoted_table} (record_id VARCHAR(40) NOT NULL, quantity INTEGER NOT NULL)"))
            conn.execute(text(f"INSERT INTO {quoted_table} (record_id, quantity) VALUES (:id, :qty)"), [
                {"id": "ROW-A", "qty": 3}, {"id": "ROW-B", "qty": 7},
            ])

        assert table in connector.list_tables()
        frame = connector.fetch(table)
        assert len(frame) == 2
        assert int(pd.to_numeric(frame["quantity"]).sum()) == 10
        assert connector.total_rows(table) == 2
        preview = connector.preview(n=1, table_or_query=table)
        assert len(preview) == 1
        assert preview.iloc[0]["record_id"] in {"ROW-A", "ROW-B"}
    finally:
        with engine.begin() as conn:
            conn.execute(text(f"DROP TABLE IF EXISTS {quoted_table}"))
        if connector._engine is not None:
            connector._engine.dispose()
        engine.dispose()
