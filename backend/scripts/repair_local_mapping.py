"""Repair registered local POS mappings using the normal ingestion pipeline.

Source databases are queried only. A complete application-store backup is taken
before updating mappings and reconciling imported records.
"""
import argparse,datetime,json,shutil,sqlite3,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.core.config import settings
from app.schema.mapper import suggest_mapping
from app.schema.domain import get_domain_pack
from app.connectors.sql import SQLConnector
from app.ingestion.registry import file_registry
from app.ingestion.store import KnowledgeBase
from app.api import kb as kb_api
from app.api.analytics import clear_analytics_cache,compute_kpi_pack,KPIRequest

ROOT=Path(__file__).resolve().parents[2]

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--backup",required=True)
    parser.add_argument("--report",required=True)
    parser.add_argument("--apply",action="store_true",required=True)
    args=parser.parse_args()
    backup=Path(args.backup)
    backup.mkdir(parents=True,exist_ok=False)
    chroma=ROOT/"backend/chroma"
    shutil.copytree(chroma,backup/"chroma")
    shutil.copytree(ROOT/"data/storage/mapping_profiles",backup/"mapping_profiles")
    source=sqlite3.connect(str(ROOT/"data/file_registry.sqlite3"))
    dest=sqlite3.connect(str(backup/"file_registry.sqlite3"))
    source.backup(dest);source.close();dest.close()
    settings.chroma_dir=str(chroma)
    kb_api._kb=KnowledgeBase(str(chroma))
    results=[]
    for db in ("sales","inventory","PharmacyPOS"):
        saved=file_registry.get_db_connection(db)
        if saved is None:
            raise RuntimeError(f"Database '{db}' is not registered; refusing to create a different connection")
        connector=SQLConnector(saved.connection_string,saved.db_type)
        tables=connector.list_tables()
        mappings={}
        for table in tables:
            raw=connector.fetch(table_or_query=table)
            proposal=suggest_mapping(list(raw.columns),raw.head(50).to_dict("records"),get_domain_pack(saved.domain))
            mappings[table]={s.source_column:s.canonical_field for s in proposal.suggestions if s.canonical_field}
        result=kb_api.ingest_sql_database(kb_api.IngestDatabaseRequest(
            connection_string=saved.connection_string,db_type=saved.db_type,
            domain=saved.domain,tables=tables,table_mappings=mappings,strategy=saved.strategy))
        file_registry.save_db_connection(database_name=db,connection_string=saved.connection_string,
            db_type=saved.db_type,domain=saved.domain,strategy=saved.strategy,
            auto_sync=saved.auto_sync,sync_interval_sec=saved.sync_interval_sec,
            table_count=result.get("successful_tables",0),row_count=result.get("total_rows",0))
        results.append({"database":db,"result":result})
        errors=[item for item in result.get("table_results",[]) if item.get("status")=="error"]
        print(json.dumps({"database":db,"tables":len(tables),"errors":len(errors)}),flush=True)
        if errors:
            raise RuntimeError(f"{db}: table repair errors; see result and backup at {backup}")
    clear_analytics_cache()
    metrics={}
    for target in ("db://inventory,sales","db://PharmacyPOS"):
        metrics[target]={}
        for preset in ("7d","28d","all"):
            result=compute_kpi_pack(KPIRequest(file_path=target,domain="pharmacy",range_preset=preset))
            metrics[target][preset]={k:result["kpis"][k]["value"] for k in (
                "total_revenue","gross_profit","gross_margin_pct","transaction_count","average_transaction_value")}
    document={"backup":str(backup),"results":results,"stored_analytics":metrics}
    Path(args.report).write_text(json.dumps(document,indent=2,default=str),encoding="utf-8")
    print(json.dumps({"backup":str(backup),"stored_analytics":metrics,"report":args.report},indent=2),flush=True)

if __name__=="__main__":main()
