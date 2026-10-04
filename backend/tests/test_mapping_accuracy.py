"""Regression tests for semantic mapping and relational analytics accuracy."""
import pandas as pd
import pytest
from app.schema.domain import get_domain_pack
from app.schema.mapper import suggest_mapping
from app.schema.normalize import apply_mapping
from app.api.analytics import _build_canonical_database, _filters, KPIRequest
from app.analytics.filters import KPIFilters
from app.analytics.kpi import transaction_count, gross_profit

def mapping(raw):
    proposal = suggest_mapping(list(raw.columns), raw.to_dict("records"), get_domain_pack("pharmacy"))
    return {s.source_column:s.canonical_field for s in proposal.suggestions if s.canonical_field}

@pytest.mark.parametrize("name,values", [
    ("ID",[1,2]), ("ACTIVE",[0,1]), ("DISABLE_TAX",[0,1]),
    ("CATEGORY_ID",[1,2]), ("ATTRIB_1_ID",[1,2]),
    ("P_ORIG_NAME",["Panadol 500mg","Brufen 400mg"]),
    ("WHOLE_SALE_PRICE",[500,700]), ("Mystery",[10,20]),
    ("Mystery",["INV12345","INV12346"]), ("Mystery",["01/26","02/26"])
])
def test_opaque_values_do_not_claim_business_fields(name,values):
    assert name not in mapping(pd.DataFrame({name:values}))

def test_sale_receivable_context_and_price_units():
    raw=pd.DataFrame({"ID":[1],"CUSTOMER_ID":[2],"AMOUNT_DUE":[50],
                      "DUE_DATE":["2026-10-03"],"PAID":[20],"GRAND_TOTAL":[70]})
    m=mapping(raw)
    assert m["AMOUNT_DUE"]=="customer_balance"
    assert m["DUE_DATE"]=="customer_due_date"
    assert m["PAID"]=="paid_amount"
    detail=pd.DataFrame({"QUANTITY":[2],"PURCHASE_PRICE":[5],"SALE_PRICE":[8],
                         "PURCHASE_SUB_TOTAL":[10],"SUB_TOTAL":[16],"ITEMS_PER_UNIT":[200]})
    m=mapping(detail)
    assert m["SALE_PRICE"]=="unit_price"
    assert m["PURCHASE_SUB_TOTAL"]=="line_cost"
    assert m["ITEMS_PER_UNIT"]=="pack_size"

def test_purchase_header_dates_and_totals_are_separate():
    raw=pd.DataFrame({"VENDOR_ID":[1],"TOTAL":[100],"SUB_TOTAL":[90],
                      "DELIVERY_DATE":["2026-09-30"],"LAST_MODIFIED":["2026-10-03"]})
    m=mapping(raw)
    assert m["DELIVERY_DATE"]=="date"
    assert m["TOTAL"]=="invoice_total"
    assert m["SUB_TOTAL"]=="sales_subtotal"
    assert "LAST_MODIFIED" not in m

def test_primary_key_survives_explicit_mapping():
    raw=pd.DataFrame({"ID":[1,2],"Quantity":[4,5]})
    df=apply_mapping(raw,{"ID":"transaction_id","Quantity":"quantity"},"pharmacy")
    assert df["_extra.ID"].tolist()==[1,2]

def test_distinct_receipts_do_not_count_products_or_reference_labels():
    df=pd.DataFrame({"txn_type":["sale"]*3,"invoice_id":["Panadol"]*3,
                     "transaction_id":["1","1","2"],"amount":[10,20,30]})
    assert transaction_count(df,KPIFilters()).value==2

def test_recorded_cogs_override_pack_assumptions():
    df=pd.DataFrame({"txn_type":["sale"],"amount":[100],"cost":[500],
                     "quantity":[2],"pack_size":[200],"line_cost":[40]})
    assert gross_profit(df,KPIFilters()).value==60

def test_missing_timeframe_dates_fail_instead_of_showing_all_time():
    with pytest.raises(ValueError,match="Timeframe"):
        _filters(KPIRequest(file_path="db://x",range_preset="7d"),pd.DataFrame({"amount":[10]}))

def test_sales_anchor_ignores_future_purchase_dates():
    df=pd.DataFrame({"txn_type":["sale","expense"],"date":["2026-10-02","2027-01-01"]})
    assert _filters(KPIRequest(file_path="db://x",range_preset="7d"),df).date_to=="2026-10-02"

@pytest.mark.parametrize("header_dates,should_raise",[
    (["2026-10-01","2026-10-01"],False),
    (["2026-10-01","2026-10-02"],True),
])
def test_duplicate_headers_require_consistent_payloads(header_dates,should_raise):
    frame=pd.DataFrame([
        {"table_name":"sales_header","invoice_id":"R1","date":d} for d in header_dates
    ]+[{"table_name":"sales_detail","invoice_id":"R1","product_id":"Panadol",
         "quantity":2,"amount":20}])
    if should_raise:
        with pytest.raises(ValueError,match="Conflicting"):
            _build_canonical_database(frame)
    else:
        result=_build_canonical_database(frame)
        assert len(result[result.txn_type=="sale"])==1

def test_orphan_detail_requires_review():
    frame=pd.DataFrame([
        {"table_name":"sales_header","invoice_id":"R1","date":"2026-10-01"},
        {"table_name":"sales_detail","invoice_id":"R1","quantity":1,"amount":10},
        {"table_name":"sales_detail","invoice_id":"R2","quantity":1,"amount":20}])
    with pytest.raises(ValueError,match="Orphan"):
        _build_canonical_database(frame)

def test_same_table_names_in_unrelated_databases_require_relationships():
    frame=pd.DataFrame({"table_name":["sales","sales"],"database_name":["A","B"],"amount":[1,2]})
    with pytest.raises(ValueError,match="different databases"):
        _build_canonical_database(frame)

def test_purchase_ids_do_not_override_sales_invoice_identity():
    frame=pd.DataFrame({"txn_type":["sale","sale","expense"],
                        "invoice_id":["R1","R1",None],
                        "transaction_id":[None,None,"P1"],"amount":[10,20,30]})
    assert transaction_count(frame,KPIFilters()).value==1

def test_sales_identity_requires_date_even_with_product_name():
    raw=pd.DataFrame({"Product_Name":["Panadol"],"TRANSACTION_ID":[1],"SUB_TOTAL":[10]})
    p=suggest_mapping(list(raw),raw.to_dict("records"),get_domain_pack("pharmacy"))
    assert "date" in p.required_fields_missing

def test_embedding_windows_count_the_source_row_once():
    frame=pd.DataFrame({"database_name":["POS","POS"],"table_name":["sales","sales"],
                        "row_id":["1","1"],"invoice_id":["R1","R1"],"amount":[10,10],
                        "chunk_id":["A","B"],"chunk_index":[0,1]})
    result=_build_canonical_database(frame)
    assert len(result)==1

def test_invoice_adjustments_reconcile_before_product_filtering():
    frame=pd.DataFrame([
        {"table_name":"sales_header","transaction_id":"1","invoice_total":30,"date":"2026-10-01"},
        {"table_name":"sales_detail","transaction_id":"1","product_id":"A","quantity":1,"amount":20,"line_cost":10},
        {"table_name":"sales_detail","transaction_id":"1","product_id":"B","quantity":1,"amount":20,"line_cost":10}])
    result=_build_canonical_database(frame)
    sales=result[result.txn_type=="sale"]
    assert sales.amount.sum()==30
    assert sales.loc[sales.product_id=="A","amount"].sum()==15
    assert sales["_extra.original_line_amount"].sum()==40
    assert gross_profit(sales,KPIFilters()).value==10

def test_invoice_adjustments_preserve_return_signs_and_rounding():
    frame=pd.DataFrame([
        {"table_name":"sales_header","transaction_id":"1","invoice_total":-21,"date":"2026-10-01"},
        {"table_name":"sales_detail","transaction_id":"1","product_id":"A","quantity":-1,"amount":-11,"line_cost":-5}])
    result=_build_canonical_database(frame)
    assert result.amount.sum()==-21

def test_sales_header_business_extras_survive_join_for_exact_receipt_lookup():
    from app.analytics.tabular_query import answer_tabular_question
    frame=pd.DataFrame([
        {"table_name":"sales_header","invoice_id":"REC-00001","date":"2026-09-18",
         "payment_method":"JazzCash","_extra.SHIP_VIA":"Counter Sale",
         "source_file":"sql://sales/tbl_21","source_row":1,"file_id":"db_sales_tbl_21"},
        {"table_name":"sales_detail","invoice_id":"REC-00001","product_id":"Medicine A",
         "quantity":1,"amount":10,"source_file":"sql://sales/tbl_22","source_row":1,
         "file_id":"db_sales_tbl_22"},
    ])
    assembled=_build_canonical_database(frame)
    result=answer_tabular_question(
        "For receipt REC-00001, what payment method was used, and was it marked as a counter sale?",
        assembled,
    )
    assert "Payment method: JazzCash" in result["answer"]
    assert "Recorded sale channel: Counter Sale" in result["answer"]
    assert result["source_rows"] == [("sql://sales/tbl_21", 1)]

def test_inventory_only_database_does_not_report_purchase_lines_as_sales():
    from app.analytics.engine import engine
    frame=pd.DataFrame([
        {"table_name":"tbl_10","source_file":"sql://inventory/tbl_10","product_id":"A",
         "quantity":10,"unit_price":20,"cost":5,"expiry_date":"2027-01-01"},
        {"table_name":"tbl_14","source_file":"sql://inventory/tbl_14","supplier_id":"V",
         "amount":100,"paid_amount":100,"date":"2026-10-01"},
        {"table_name":"tbl_15","source_file":"sql://inventory/tbl_15","product_id":"A",
         "purchase_order_no":"1","quantity":10,"amount":100,"unit_price":10}])
    # The actual PKs remain available to join opaque purchase headers.
    frame["row_id"]=["1","1","1"]
    result=_build_canonical_database(frame)
    assert not (result.txn_type=="sale").any()
    assert engine.compute("total_revenue",result,domain="pharmacy").value is None

def test_single_stock_table_is_inventory_not_revenue():
    from app.analytics.engine import engine
    frame=pd.DataFrame({"table_name":["inventory"],"quantity":[10],"unit_price":[20]})
    result=_build_canonical_database(frame)
    assert engine.compute("total_revenue",result,domain="pharmacy").value is None

def test_multiple_matching_sales_tables_require_explicit_relationships():
    frame=pd.DataFrame([
        {"table_name":"sales_detail","invoice_id":"R1","quantity":1,"amount":10},
        {"table_name":"sales_details_archive","invoice_id":"R1","quantity":1,"amount":10}])
    with pytest.raises(ValueError,match="Ambiguous table role"):
        _build_canonical_database(frame)

def test_sql_metadata_reads_ignore_staging_collections(tmp_path,monkeypatch):
    import sqlite3
    from app.api.analytics import _fetch_metadatas_from_sqlite
    from app.core.config import settings
    c=sqlite3.connect(tmp_path/"chroma.sqlite3")
    c.executescript("""
        CREATE TABLE collections(id TEXT,name TEXT);
        CREATE TABLE segments(id TEXT,collection TEXT);
        CREATE TABLE embeddings(id INTEGER,segment_id TEXT,embedding_id TEXT);
        CREATE TABLE embedding_metadata(id INTEGER,key TEXT,string_value TEXT,int_value INTEGER,float_value REAL,bool_value INTEGER);
        INSERT INTO collections VALUES ('C1','active'),('C2','stage_old');
        INSERT INTO segments VALUES ('S1','C1'),('S2','C2');
        INSERT INTO embeddings VALUES (1,'S1','active-chunk'),(2,'S2','stage-chunk');
        INSERT INTO embedding_metadata(id,key,string_value) VALUES (1,'file_id','ledger'),(2,'file_id','ledger');
        INSERT INTO embedding_metadata(id,key,float_value) VALUES (1,'amount',10),(2,'amount',999);
    """)
    c.commit();c.close()
    monkeypatch.setattr(settings,"collection_name","active")
    records=_fetch_metadatas_from_sqlite(str(tmp_path),"file_id",["ledger"])
    assert len(records)==1 and records[0]["amount"]==10
    assert records[0]["chunk_id"]=="active-chunk"
