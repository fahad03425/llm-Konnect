"""Offline confirmation, domain isolation, RAG and reporting compatibility."""
import sqlite3
from types import SimpleNamespace
from unittest.mock import Mock

import pandas as pd
import pytest

from app.schema.normalize import apply_mapping, _clean_date
from app.schema.validate import validate
from app.schema.profile import source_signature, source_identity, confirmed_mapping, sql_signature
from app.schema.source_domain import require_source_domain

@pytest.fixture
def isolated_api(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    from app.api import routes, kb
    from app.core.config import settings
    from app.ingestion.registry import FileRegistry
    registry = FileRegistry(str(tmp_path / 'registry.db'))
    monkeypatch.setattr(settings, 'storage_dir', str(tmp_path / 'storage'))
    monkeypatch.setattr(routes, 'file_registry', registry)
    monkeypatch.setattr(kb, 'file_registry', registry)
    store = Mock()
    store.add_dataframe.return_value = SimpleNamespace(total_chunks=1, model_dump=lambda: {'total_chunks': 1})
    monkeypatch.setattr(kb, '_kb', store)
    # Source-domain and mapping tests never touch production embeddings or the vault.
    return TestClient(app), registry, store


@pytest.mark.parametrize('mapping,match', [
    ({'missing': 'amount'}, 'does not exist'),
    ({'a': 'invented'}, 'Unknown canonical'),
    ({'a': 'amount', 'b': 'amount'}, 'Multiple columns'),
])
def test_invalid_mapping_rejected(mapping, match):
    with pytest.raises(ValueError, match=match):
        apply_mapping(pd.DataFrame({'a': [1], 'b': [2]}), mapping, 'pharmacy')


def test_unmapped_target_collision_rejected():
    with pytest.raises(ValueError, match='overwrite'):
        apply_mapping(pd.DataFrame({'sales': [10], 'amount': [20]}), {'sales': 'amount'}, 'pharmacy')


def test_failures_keep_original_values_and_real_rows():
    raw = pd.DataFrame({'product_id': ['Panadol'], 'quantity': ['bad'], 'date': ['wrong'], 'source_row': [37]})
    frame = apply_mapping(raw, {c: c for c in raw if c != 'source_row'}, 'pharmacy')
    failures = frame.attrs['conversion_failures']
    assert {(f['field'], f['source_row'], f['value']) for f in failures} == {('quantity', 37, 'bad'), ('date', 37, 'wrong')}
    report = validate(frame, 'pharmacy')
    assert report.verdict == 'not_usable'
    assert {p.code for p in report.problems} >= {'UNPARSEABLE_NUMBER', 'UNPARSEABLE_DATE'}
    assert raw.quantity.iloc[0] == 'bad'


def test_ecommerce_dates_and_core_aliases_only():
    raw = pd.DataFrame({'order_date': [45000.0, '2026-10-02T13:00:00+05:00'], 'sale_amount': ['10', '20'], 'product_name': ['A', 'B']})
    frame = apply_mapping(raw, {c: c for c in raw}, 'ecommerce')
    assert frame.order_date.iloc[0] == pd.Timestamp('2023-03-15')
    assert frame.order_date.iloc[1] == pd.Timestamp('2026-10-02T08:00:00')
    assert frame.date.equals(frame.order_date)
    assert frame.amount.sum() == 30
    # Established pharmacy parser and table classification are not changed.
    assert pd.isna(_clean_date(45000.0))
    pharmacy = apply_mapping(pd.DataFrame({'date': ['05/25'], 'product_id': ['Panadol']}), {'date': 'date', 'product_id': 'product_id'}, 'pharmacy')
    assert pharmacy.date.iloc[0] == pd.Timestamp('2025-05-31')


def test_ecommerce_missing_order_date_is_transaction_error():
    frame = apply_mapping(pd.DataFrame({'order_id': ['1'], 'product_name': ['A'], 'sale_amount': [10]}),
                          {'order_id': 'order_id', 'product_name': 'product_name', 'sale_amount': 'sale_amount'}, 'ecommerce')
    assert validate(frame, 'ecommerce').verdict == 'not_usable'
    # Pharmacy inventory continues to work without transaction dates.
    assert validate(pd.DataFrame({'product_id': ['Panadol'], 'quantity': [10]}), 'pharmacy').is_usable


def test_ecommerce_negative_sale_and_invalid_rating_do_not_crash():
    frame = pd.DataFrame({'product_name': ['A'], 'order_date': [pd.Timestamp('2026-10-01')], 'sale_amount': [-10], 'rating': [9]})
    codes = {p.code for p in validate(frame, 'ecommerce').problems}
    assert codes >= {'INVALID_RATING', 'NEGATIVE_SALE_AMOUNT'}


@pytest.mark.parametrize('raw', [
    {'Product': ['Laptop'], 'Amount': [10]},
    {'Product': ['Unknown thing'], 'Amount': [10]},
    {'Medicine_Name': ['Laptop'], 'Amount': [10]},
    {'Product': ['Bread'], 'Batch_No': ['B1'], 'Expiry_Date': ['2027-01-01']},
])
def test_foreign_and_unproven_datasets_rejected_in_pharmacy(raw):
    with pytest.raises(ValueError):
        require_source_domain(pd.DataFrame(raw), 'pharmacy')
    require_source_domain(pd.DataFrame(raw), 'ecommerce')


@pytest.mark.parametrize('raw', [
    {'Medicine_Name': ['Unknown medicine brand'], 'Amount': [10]},
    {'Product': ['Panadol 500mg'], 'Amount': [10]},
    {'Batch_No': ['B1'], 'Expiry_Date': ['2027-01-01'], 'Product': ['Drug A']},
])
def test_pharmacy_exports_accepted(raw):
    require_source_domain(pd.DataFrame(raw), 'pharmacy')


def test_signature_isolates_domains_stores_and_sheets():
    signature = source_signature(['Qty'], 'CSV', 'pharmacy', 'source-a')
    assert signature == source_signature([' QTY '], 'CSV', 'pharmacy', 'source-a')
    assert signature != source_signature(['Qty'], 'CSV', 'ecommerce', 'source-a')
    assert signature != source_signature(['Qty'], 'CSV', 'pharmacy', 'source-b')
    assert source_identity('shopify://a?access_token=one&resource=orders') == source_identity('shopify://a?access_token=two&resource=orders')
    assert source_identity('shopify://a?resource=orders') != source_identity('shopify://a?resource=products')
    assert source_identity('data.xlsx', 'A') != source_identity('data.xlsx', 'B')
    assert source_identity('http://localhost:9000') != source_identity('http://localhost:9001')
    assert source_identity('http://localhost:9000?company=A') != source_identity('http://localhost:9000?company=B')


def test_confirm_reuse_rebind_and_wrong_mapping_never_saved(isolated_api, tmp_path):
    client, registry, store = isolated_api
    path = tmp_path / 'medicines.csv'
    path.write_text('Medicine_Name,Amount,Qty,Unknown\nPanadol,10,2,keep me\n', encoding='utf-8')
    request = {'file_path': str(path), 'domain': 'pharmacy'}
    preview = client.post('/api/sources/preview', json=request)
    assert preview.status_code == 200, preview.text
    assert preview.json()['saved_profile'] is None
    assert 'expiry_date' in preview.json()['canonical_fields']
    invalid = client.post('/api/sources/mapping/confirm', json={**request, 'mapping': {'Qty': 'wrong'}})
    assert invalid.status_code == 400
    assert client.post('/api/sources/preview', json=request).json()['saved_profile'] is None
    mapping = {'Medicine_Name': 'product_id', 'Amount': 'amount', 'Qty': 'quantity'}
    response = client.post('/api/sources/mapping/confirm', json={**request, 'mapping': mapping})
    assert response.status_code == 200, response.text
    assert response.json()['data_preview'][0]['_extra.Unknown'] == 'keep me'
    assert client.post('/api/sources/preview', json=request).json()['saved_profile']['mapping'] == mapping
    path.write_text('medicine_name,AMOUNT,qty,Unknown\nPanadol,10,2,keep me\n', encoding='utf-8')
    reused = client.post('/api/sources/preview', json=request).json()['saved_profile']['mapping']
    assert reused == {'medicine_name': 'product_id', 'AMOUNT': 'amount', 'qty': 'quantity'}
    response = client.post('/api/kb/ingest', json={**request, 'mapping': reused})
    assert response.status_code == 200, response.text
    assert store.add_dataframe.call_args.args[0].amount.sum() == 10


def test_pharmacy_rejection_keeps_existing_kb(isolated_api, tmp_path):
    client, _, store = isolated_api
    path = tmp_path / 'electronics.csv'
    path.write_text('Product,Amount\nLaptop,100\n', encoding='utf-8')
    request = {'file_path': str(path), 'domain': 'pharmacy', 'mapping': {'Product': 'product_id', 'Amount': 'amount'}}
    for endpoint in ('/api/sources/preview', '/api/sources/mapping/confirm', '/api/kb/ingest'):
        response = client.post(endpoint, json=request)
        assert response.status_code == 400, response.text
    store.delete_source.assert_not_called()
    store.add_dataframe.assert_not_called()


def test_pharmacy_mapping_preserves_rag_and_report_measures():
    from app.schema.domain import get_domain_pack
    from app.schema.mapper import suggest_mapping
    from app.analytics.tabular_query import answer_tabular_question
    from app.analytics.engine import engine
    raw = pd.DataFrame({'Medicine_Name': ['Panadol', 'Panadol'], 'Date': ['2026-10-01', '2026-10-02'],
                        'Qty': [2, 3], 'Amount': [20, 30], 'Transaction_Type': ['Sale', 'Sale'], 'Category': ['OTC', 'OTC']})
    proposal = suggest_mapping(list(raw), raw.to_dict('records'), get_domain_pack('pharmacy'))
    mapping = {s.source_column: s.canonical_field for s in proposal.suggestions if s.canonical_field}
    frame = apply_mapping(raw, mapping, 'pharmacy')
    assert frame.amount.sum() == 50
    assert frame.quantity.sum() == 5
    text = get_domain_pack('pharmacy').row_to_text(frame.iloc[0].to_dict())
    assert 'Panadol' in text and '20' in text
    answer = answer_tabular_question('Which category generates the most revenue?', frame)
    assert answer['values']['results'][0]['value'] == 50
    assert engine.compute('total_revenue', frame, domain='pharmacy').value == 50


def test_sql_confirmation_and_reuse_with_lookup_context(isolated_api, tmp_path):
    client, _, store = isolated_api
    store.reconcile_database_table.return_value = {'chunks': 1, 'total_db_rows': 1, 'new_rows': 1, 'updated_rows': 0, 'deleted_rows': 0, 'status': 'success', 'message': 'Dummy import'}
    database = tmp_path / 'pharmacy.db'
    with sqlite3.connect(database) as connection:
        connection.execute('CREATE TABLE products (Medicine_Name TEXT, quantity REAL)')
        connection.execute("INSERT INTO products VALUES ('Panadol', 2)")
        connection.execute('CREATE TABLE suppliers (supplier_id TEXT, description TEXT)')
        connection.execute("INSERT INTO suppliers VALUES ('S1', 'Local supplier')")
    request = {'connection_string': f'sqlite:///{database}', 'db_type': 'sqlite', 'domain': 'pharmacy', 'tables': ['products', 'suppliers']}
    discovery = client.post('/api/sources/sql/discover', json=request)
    assert discovery.status_code == 200, discovery.text
    mappings = {table['table_name']: {s['source_column']: s['canonical_field'] for s in table['mapping_proposal']['suggestions'] if s['canonical_field']} for table in discovery.json()['tables']}
    response = client.post('/api/kb/ingest-database', json={**request, 'table_mappings': mappings})
    assert response.status_code == 200, response.text
    assert response.json()['successful_tables'] == 2, response.text
    discovery = client.post('/api/sources/sql/discover', json=request).json()
    assert all(table['saved_profile'] is not None for table in discovery['tables'])
    reused = client.post('/api/kb/ingest-database', json=request)
    assert reused.status_code == 200, reused.text
    assert reused.json()['successful_tables'] == 2, reused.text


def test_new_sources_require_confirmation_before_automatic_ingest(isolated_api, tmp_path):
    client, _, store = isolated_api
    path = tmp_path / 'new.csv'
    path.write_text('Medicine_Name,Qty\nPanadol,2\n', encoding='utf-8')
    request = {'file_path': str(path), 'domain': 'pharmacy'}
    rejected = client.post('/api/kb/ingest', json=request)
    assert rejected.status_code == 400, rejected.text
    store.delete_source.assert_not_called()
    assert client.post('/api/sources/mapping/confirm', json={**request, 'mapping': {'Medicine_Name': 'product_id', 'Qty': 'quantity'}}).status_code == 200
    assert client.post('/api/kb/ingest', json=request).status_code == 200


def test_schema_choices_are_domain_specific_for_restored_ui_sessions(isolated_api):
    client, _, _ = isolated_api
    pharmacy = client.get('/api/sources/schema?domain=pharmacy')
    ecommerce = client.get('/api/sources/schema?domain=ecommerce')
    assert pharmacy.status_code == ecommerce.status_code == 200
    assert 'expiry_date' in pharmacy.json()['canonical_fields']
    assert 'order_date' not in pharmacy.json()['canonical_fields']
    assert 'order_date' in ecommerce.json()['canonical_fields']
    assert client.get('/api/sources/schema?domain=unknown').status_code == 400


def test_uploaded_files_quick_import_requires_review_and_rejects_foreign_data(isolated_api, tmp_path, monkeypatch):
    from app.api import files
    client, registry, store = isolated_api
    monkeypatch.setattr(files, 'file_registry', registry)
    monkeypatch.setattr(files, '_kb', store)
    thread = Mock()
    monkeypatch.setattr(files, 'threading', SimpleNamespace(Thread=thread))
    monkeypatch.setattr(files, '_active_tasks', {})
    monkeypatch.setattr(files, '_cancelled_tasks', set())
    path = tmp_path / 'medicine.csv'
    path.write_text('Medicine_Name,Qty\nPanadol,2\n', encoding='utf-8')
    request = {'file_path': str(path), 'domain': 'pharmacy'}
    response = client.post('/api/files/quick-ingest', json=request)
    assert response.status_code == 200, response.text
    assert response.json()['status'] == 'mapping_required'
    thread.assert_not_called()
    assert client.post('/api/sources/mapping/confirm', json={**request, 'mapping': {'Medicine_Name': 'product_id', 'Qty': 'quantity'}}).status_code == 200
    response = client.post('/api/files/quick-ingest', json=request)
    assert response.status_code == 200, response.text
    assert response.json()['status'] == 'processing'
    thread.return_value.start.assert_called_once()
    path = tmp_path / 'foreign.csv'
    path.write_text('Product,Amount\nLaptop,100\n', encoding='utf-8')
    assert client.post('/api/files/quick-ingest', json={**request, 'file_path': str(path)}).status_code == 400
    files._active_tasks.clear()


def test_reporting_loader_uses_confirmed_fields_and_invalidates_old_cache(isolated_api, tmp_path):
    from app.api.analytics import _load_canonical, KPIRequest, _df_cache
    client, _, _ = isolated_api
    path = tmp_path / 'report.csv'
    path.write_text('Medicine_Name,Date,Amount,Correct_Total,Transaction_Type\nPanadol,2026-10-01,100,20,Sale\n', encoding='utf-8')
    request = {'file_path': str(path), 'domain': 'pharmacy'}
    before, _ = _load_canonical(KPIRequest(**request))
    assert before.amount.iloc[0] == 100
    # Preserve the old amount as an extra rather than overwriting it implicitly.
    mapping = {'Medicine_Name': 'product_id', 'Date': 'date', 'Amount': 'cost', 'Correct_Total': 'amount', 'Transaction_Type': 'txn_type'}
    response = client.post('/api/sources/mapping/confirm', json={**request, 'mapping': mapping})
    assert response.status_code == 200, response.text
    after, used = _load_canonical(KPIRequest(**request))
    assert after.amount.iloc[0] == 20
    assert used == mapping
    _df_cache.clear()


def test_sync_uses_confirmed_mapping_and_does_not_register_rejected_rows(tmp_path, monkeypatch):
    import numpy as np
    from app.ingestion import sync_worker as module
    from app.ingestion.registry import FileRegistry
    from app.core.config import settings
    from app.schema.profile import save_profile
    registry = FileRegistry(str(tmp_path / 'registry.db'))
    monkeypatch.setattr(settings, 'storage_dir', str(tmp_path / 'storage'))
    monkeypatch.setattr(module, 'file_registry', registry)
    from app.ingestion.store import KnowledgeBase
    store = KnowledgeBase(str(tmp_path / 'vectors'), 'sync_regression')
    encoder = Mock()
    encoder.encode.side_effect = lambda texts, **kwargs: np.array([[1.0, 0.0] for _ in texts])
    monkeypatch.setattr(store, '_get_embedder', lambda *args, **kwargs: encoder)
    monkeypatch.setattr(module, 'KnowledgeBase', lambda: store)
    worker = module.SyncWorker()
    registry.save_db_connection('dummy', 'sqlite:///dummy.db', 'sqlite', domain='pharmacy')
    registry.register_or_update('sql://dummy/sales', 1, domain='pharmacy', file_id='db_dummy_sales', source_type='database')
    row = {'Medicine_Name': 'Panadol', 'Total_A': 100, 'Total_B': 20}
    signature = sql_signature(list(row), 'SQLConnector', 'pharmacy', 'sqlite:///dummy.db', 'sales')
    save_profile(signature, {'Medicine_Name': 'product_id', 'Total_B': 'amount'})
    good = module.ChangeEvent(action='update', database_name='dummy', table_name='sales', row_id='1', data=row)
    bad = module.ChangeEvent(action='insert', database_name='dummy', table_name='foreign', row_id='2', data={'Product': 'Laptop', 'Amount': 100})
    worker._process_batch([bad, good])
    stored = store._get_chroma().get(include=['metadatas'])
    assert len(stored['ids']) == 1
    assert stored['metadatas'][0]['amount'] == 20
    assert registry.get_file_by_id('db_dummy_foreign') is None
    assert worker.total_failed == 1


def test_periodic_sql_sync_reuses_corrections_and_pauses_new_tables(tmp_path, monkeypatch):
    from app.ingestion import sync_worker as module
    from app.ingestion.registry import FileRegistry
    from app.connectors.sql import SQLConnector
    from app.core.config import settings
    from app.schema.profile import save_profile
    database = tmp_path / 'sync.db'
    with sqlite3.connect(database) as connection:
        for table in ('reviewed', 'new_table'):
            connection.execute(f'CREATE TABLE {table} (Medicine_Name TEXT, First REAL, Second REAL)')
            connection.execute(f"INSERT INTO {table} VALUES ('Panadol', 100, 20)")
    connector = SQLConnector(f'sqlite:///{database}')
    monkeypatch.setattr(settings, 'storage_dir', str(tmp_path / 'storage'))
    monkeypatch.setattr(module, 'file_registry', FileRegistry(str(tmp_path / 'registry.db')))
    store = Mock()
    store.reconcile_database_table.return_value = {'chunks': 1}
    monkeypatch.setattr(module, 'KnowledgeBase', lambda: store)
    worker = module.SyncWorker()
    raw = connector.fetch('reviewed')
    save_profile(sql_signature(list(raw.columns), 'SQLConnector', 'pharmacy', connector.raw_connection_string, 'reviewed'),
                 {'Medicine_Name': 'product_id', 'Second': 'amount'})
    assert worker.sync_table(connector, 'sync', 'reviewed', 'pharmacy') == 1
    assert store.reconcile_database_table.call_args.kwargs['canonical_df'].amount.iloc[0] == 20
    assert worker.sync_table(connector, 'sync', 'new_table', 'pharmacy') == 0
    assert store.reconcile_database_table.call_count == 1


def test_failed_reimport_keeps_active_registry_and_uses_protected_write(isolated_api, tmp_path):
    client, registry, store = isolated_api
    path = tmp_path / "retain.csv"
    path.write_text("Medicine_Name,Quantity\nPanadol,2\n", encoding="utf-8")
    previous = registry.register_or_update(str(path), 1, domain="pharmacy", file_id="retain")
    store.add_dataframe.side_effect = RuntimeError("replacement failed")
    response = client.post("/api/kb/ingest", json={"file_path": str(path), "domain": "pharmacy",
        "mapping": {"Medicine_Name": "product_id", "Quantity": "quantity"}})
    assert response.status_code == 400
    assert registry.get_file_by_id(previous.file_id).status == "active"
    store.delete_source.assert_not_called()
    assert store.add_dataframe.call_args.kwargs["replace_existing"] is True


def test_cancel_endpoint_keeps_previous_index(isolated_api, monkeypatch, tmp_path):
    from app.api import files
    client, registry, store = isolated_api
    path = tmp_path / "cancel.csv"
    path.write_text("Medicine_Name,Quantity\nPanadol,2\n", encoding="utf-8")
    previous = registry.register_or_update(str(path), 1, domain="pharmacy", file_id="cancel_previous")
    registry.update_progress(str(path), 25.0, "Re-importing", status="processing")
    monkeypatch.setattr(files, "file_registry", registry)
    monkeypatch.setattr(files, "_kb", store)
    monkeypatch.setattr(files, "_active_tasks", {})
    monkeypatch.setattr(files, "_cancelled_tasks", set())
    response = client.post("/api/files/cancel-ingest", json={"file_id": previous.file_id})
    assert response.status_code == 200
    assert registry.get_file_by_id(previous.file_id).status == "active"
    store.delete_source.assert_not_called()
