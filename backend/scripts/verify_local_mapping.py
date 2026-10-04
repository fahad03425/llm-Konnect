"""Read-only local source verification; writes only its JSON report.

Usage: python scripts/verify_local_mapping.py --report <path>
Requires locally available AsaanPOS and SQL Server PharmacyPOS.
"""
import argparse,json,sqlite3,sys
from pathlib import Path
import pandas as pd

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.schema.mapper import suggest_mapping
from app.schema.domain import get_domain_pack
from app.schema.normalize import apply_mapping
from app.api.analytics import _build_canonical_database, _filters, KPIRequest
from app.analytics.engine import engine
from app.analytics.filters import KPIFilters

ROOT=Path(__file__).resolve().parents[2]
PACK=get_domain_pack("pharmacy")

def mapped(raw,db,table,pk):
    proposal=suggest_mapping(list(raw.columns),raw.head(50).to_dict("records"),PACK)
    mapping={s.source_column:s.canonical_field for s in proposal.suggestions if s.canonical_field}
    canonical=apply_mapping(raw,mapping,"pharmacy",True)
    if pk:
        canonical["row_id"]=raw[pk].apply(lambda row:"_".join(str(x) for x in row),axis=1)
    canonical["table_name"]=table
    canonical["database_name"]=db
    canonical["source_file"]=f"sql://{db}/{table}"
    return canonical,mapping

def sqlite_frames(path):
    c=sqlite3.connect("file:"+str(path)+"?mode=ro",uri=True)
    db=path.stem
    result=[];maps={}
    for (t,) in c.execute("select name from sqlite_master where type='table' and name not like 'sqlite_%'"):
        raw=pd.read_sql_query('select * from "'+t+'"',c)
        if raw.empty:continue
        pk=[x[1] for x in sorted(c.execute('pragma table_info("'+t+'")').fetchall(),key=lambda x:x[5]) if x[5]]
        canonical,m=mapped(raw,db,t,pk);result.append(canonical);maps[t]=m
    c.close()
    return result,maps

def metrics(df,preset):
    filters=_filters(KPIRequest(file_path="db://local",range_preset=preset),df)
    keys=("total_revenue","gross_profit","gross_margin_pct","transaction_count","average_transaction_value")
    return {k:engine.compute(k,df,filters,domain="pharmacy").value for k in keys}

def main():
    parser=argparse.ArgumentParser();parser.add_argument("--report",required=True);args=parser.parse_args()
    report={"datasets":[],"checks":[]}
    path=Path.home()/"AsaanPOS"
    frames=[];maps={}
    for name in ("inventory","sales"):
        fs,ms=sqlite_frames(path/(name+".stardb"));frames+=fs;maps[name]=ms
    combined=_build_canonical_database(pd.concat(frames,ignore_index=True))
    c=sqlite3.connect("file:"+str(path/"sales.stardb")+"?mode=ro",uri=True)
    actual={}
    latest=c.execute("select date(max(TIME_STAMP)) from tbl_21").fetchone()[0]
    for preset,days in (("7d",7),("28d",28),("all",None)):
        sql="select count(*),sum(h.GRAND_TOTAL),sum(d.cost) from tbl_21 h join (select TRANSACTION_ID,sum(PURCHASE_SUB_TOTAL) cost from tbl_22 group by TRANSACTION_ID) d on h.ID=d.TRANSACTION_ID"
        params=[]
        if days:
            sql+=" where date(h.TIME_STAMP) between date(?,?) and date(?)";params=[latest,f"-{days-1} days",latest]
        n,revenue,cost=c.execute(sql,params).fetchone()
        got=metrics(combined,preset)
        assert got["transaction_count"]==n,(preset,got,n)
        assert abs(got["total_revenue"]-revenue)<0.01,(preset,got,revenue)
        assert abs(got["gross_profit"]-(revenue-cost))<0.01,(preset,got,cost)
        actual[preset]=got
    report["datasets"].append({"name":"AsaanPOS","mapping":maps,"metrics":actual,"checks":"net invoice revenue, transaction count, recorded COGS and profit match independent SQL in 7d/28d/all"})
    report["checks"].append("AsaanPOS 9 independent financial assertions passed")
    report["datasets"][-1]["invoice_line_discrepancy"]=float(c.execute("select (select sum(SUB_TOTAL) from tbl_22)-(select sum(GRAND_TOTAL) from tbl_21)").fetchone()[0])
    import pyodbc
    sql=pyodbc.connect(r"DRIVER={ODBC Driver 18 for SQL Server};SERVER=.\SQLEXPRESS;DATABASE=PharmacyPOS;Trusted_Connection=yes;TrustServerCertificate=yes")
    cursor=sql.cursor()
    frames=[];maps={}
    tables=[row[0] for row in cursor.execute("select TABLE_NAME from INFORMATION_SCHEMA.TABLES where TABLE_TYPE='BASE TABLE'")]
    for table in tables:
        cursor.execute("select * from ["+table+"]")
        cols=[x[0] for x in cursor.description];raw=pd.DataFrame.from_records(cursor.fetchall(),columns=cols)
        if raw.empty:continue
        pk=[x[0] for x in cursor.execute("select c.name from sys.indexes i join sys.index_columns ic on i.object_id=ic.object_id and i.index_id=ic.index_id join sys.columns c on c.object_id=ic.object_id and c.column_id=ic.column_id where i.is_primary_key=1 and i.object_id=object_id(?) order by ic.key_ordinal",table)]
        canon,m=mapped(raw,"PharmacyPOS",table,pk);frames.append(canon);maps[table]=m
    combined=_build_canonical_database(pd.concat(frames,ignore_index=True))
    actual={}
    for preset in ("7d","28d","all"):
        got=metrics(combined,preset);filters=_filters(KPIRequest(file_path="db://local",range_preset=preset),combined)
        query="select count(distinct h.BillNo),sum(d.Amount) from tbl_SalesDetails d join tbl_SalesHeader h on d.BillNo=h.BillNo"
        params=[]
        if preset!="all":
            query+=" where h.InvoiceDateTime>=? and h.InvoiceDateTime<dateadd(day,1,cast(? as date))"
            params=[filters.date_from,filters.date_to]
        n,revenue=cursor.execute(query,params).fetchone()
        assert got["transaction_count"]==n,(preset,got,n)
        assert abs(got["total_revenue"]-float(revenue))<0.01,(preset,got,revenue)
        actual[preset]=got
    report["datasets"].append({"name":"PharmacyPOS (live SQL Server)","mapping":maps,"metrics":actual,"checks":"revenue and transaction count match independent SQL in 7d/28d/all; profit uses inferred batch costs"})
    report["checks"].append("PharmacyPOS 6 independent financial assertions passed")
    candidates = set((ROOT/"data/uploads").glob("*"))
    candidates.update((Path.home()/"Downloads").glob("pharmacy_*test*.csv"))
    for p in sorted(candidates):
        if p.suffix.lower() not in (".csv",".xlsx"):continue
        raw=pd.read_csv(p) if p.suffix.lower()==".csv" else pd.read_excel(p)
        if len(raw)>5000:continue
        proposal=suggest_mapping(list(raw.columns),raw.head(50).fillna("").to_dict("records"),PACK)
        mapping={s.source_column:s.canonical_field for s in proposal.suggestions if s.canonical_field}
        canon=apply_mapping(raw,mapping,"pharmacy",True)
        assert len(canon)==len(raw)
        unmapped=[s.source_column for s in proposal.suggestions if not s.canonical_field]
        entry={"name":p.name,"rows":len(raw),"mapping":mapping,"unmapped":unmapped,"checks":"row count preserved; ambiguous columns preserved as extras"}
        measures = {}
        numeric_targets = {"amount", "invoice_total", "quantity", "stock_qty", "cost",
                           "unit_price", "mrp", "paid_amount", "line_cost"}
        for source,target in mapping.items():
            if target not in numeric_targets:
                continue
            numbers = pd.to_numeric(raw[source],errors="coerce")
            if numbers.notna().sum() != raw[source].notna().sum():
                continue
            expected = float(numbers.sum())
            actual = float(pd.to_numeric(canon[target],errors="coerce").sum())
            assert abs(actual-expected)<0.01,(p.name,source,target,expected,actual)
            measures[source] = {"canonical":target,"source_total":expected,"canonical_total":actual}
            if target == "stock_qty" and "quantity" in canon:
                assert abs(pd.to_numeric(canon.quantity).sum()-expected)<0.01
            if target == "invoice_total" and "amount" in canon and "amount" not in mapping.values():
                assert abs(canon.amount.sum()-expected)<0.01
        entry["measure_checks"] = measures
        entry["conversion_failures_count"] = len(canon.attrs.get("conversion_failures",[]))
        entry["checks"] += f"; {len(measures)} independent numeric source totals checked"
        report["datasets"].append(entry)
    Path(args.report).write_text(json.dumps(report,indent=2,default=str),encoding="utf-8")
    print(json.dumps({"datasets":len(report["datasets"]),"checks":report["checks"],"AsaanPOS":actual if False else report["datasets"][0]["metrics"],"report":args.report},indent=2))
if __name__=="__main__":main()
