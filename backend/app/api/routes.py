"""Module 1.4 — API routes. Month 2."""
from fastapi import APIRouter, HTTPException, Query, UploadFile, File
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
import os
import shutil
import pandas as pd

from app.core.config import get_default_domain, set_default_domain
from app.connectors.base import detect_connector, source_exists
from app.ingestion.registry import file_registry
from app.schema.mapper import map_headers, suggest_mapping, get_canonical_fields
from app.schema.normalize import normalize, apply_mapping, validate_mapping
from app.schema.source_domain import require_source_domain
from app.schema.validate import validate, clean
from app.schema.domain import get_domain_pack
from app.schema.profile import find_profile, save_profile, source_signature, source_identity, compatible_mapping, sql_signature, confirmed_mapping

router = APIRouter(prefix="/api/sources", tags=["sources"])

@router.post("/upload")
async def upload_file(file: UploadFile = File(...)):
    """Upload a file to the server for easy testing in Swagger UI."""
    # Project root (llm-konnect)
    base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    upload_dir = os.path.join(base_dir, "data", "uploads")
    os.makedirs(upload_dir, exist_ok=True)
    
    # Secure the filename or just use it directly for testing
    safe_filename = file.filename.replace("/", "").replace("\\", "").strip()
    if not safe_filename:
        raise HTTPException(status_code=400, detail="Invalid filename")

    contents = await file.read()
    if not contents:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")

    # Reject duplicate uploads (by filename or identical content)
    dup = file_registry.find_duplicate(safe_filename, contents)
    if dup:
        dup_type, existing_name = dup
        if dup_type == "content":
            raise HTTPException(
                status_code=409,
                detail=f"Duplicate file: An identical file already exists as '{existing_name}'."
            )
        else:
            raise HTTPException(
                status_code=409,
                detail=f"Duplicate file: A file named '{existing_name}' already exists. Please delete it first or rename your file."
            )

    file_path = os.path.join(upload_dir, safe_filename)
    
    with open(file_path, "wb") as buffer:
        buffer.write(contents)
        
    # Return with normalized slashes so it can be easily copied to other endpoints
    normalized_path = file_path.replace("\\", "/")
    
    # Register file immediately so it appears on Uploaded Files page in real-time
    try:
        file_registry.set_file_status(
            file_path=normalized_path,
            status="pending",
            progress=0.0,
            step_text="Uploaded (Pending Ingestion)"
        )
    except Exception:
        pass

    return {
        "message": "File uploaded successfully",
        "file_path": normalized_path,
        "filename": safe_filename
    }

class PreviewRequest(BaseModel):
    file_path: str
    n: int = 5
    sheet_name: Optional[str] = None
    table_or_query: Optional[str] = None
    domain: str = Field(default_factory=get_default_domain, description="Business domain context")

class MappingConfirmRequest(BaseModel):
    file_path: str
    mapping: Dict[str, str]
    domain: str = Field(default_factory=get_default_domain, description="Business domain context")
    sheet_name: Optional[str] = None
    table_or_query: Optional[str] = None
    keep_extras: bool = True
    save_profile: bool = True

class NormalizeRequest(BaseModel):
    file_path: str
    sheet_name: Optional[str] = None
    table_or_query: Optional[str] = None
    domain: str = Field(default_factory=get_default_domain, description="Business domain context")
    mapping: Optional[Dict[str, str]] = None 

class ValidateRequest(BaseModel):
    file_path: str
    mapping: Optional[Dict[str, str]] = None
    domain: str = Field(default_factory=get_default_domain, description="Business domain context")
    table_kind: str = "auto"
    sheet_name: Optional[str] = None
    table_or_query: Optional[str] = None

class CleanRequest(BaseModel):
    file_path: str
    mapping: Optional[Dict[str, str]] = None
    domain: str = Field(default_factory=get_default_domain, description="Business domain context")
    sheet_name: Optional[str] = None
    table_or_query: Optional[str] = None
    options: Optional[Dict[str, Any]] = None

@router.post("/preview")
def preview_source(req: PreviewRequest):
    if not source_exists(req.file_path):
        raise HTTPException(status_code=404, detail="File not found")
        
    try:
        try:
            file_registry.set_file_status(
                file_path=req.file_path,
                status="pending",
                progress=35.0,
                step_text="Preview & Schema Analysis"
            )
        except Exception:
            pass

        connector = detect_connector(req.file_path)
        
        kwargs = {}
        if req.sheet_name:
            kwargs['sheet_name'] = req.sheet_name
        if req.table_or_query:
            kwargs['table_or_query'] = req.table_or_query
            
        df = connector.preview(n=req.n, **kwargs)
        require_source_domain(df, req.domain)
        df = df.astype(object).where(pd.notnull(df), None)
        
        columns = list(df.columns)
        sample_rows = df.to_dict(orient="records")
        
        # Check for saved profile
        sig = source_signature(columns, connector.__class__.__name__, req.domain, source_identity(req.file_path, req.sheet_name, req.table_or_query))
        saved_profile = find_profile(sig)
        if saved_profile:
            try:
                saved_profile = dict(saved_profile)
                saved_profile['mapping'] = compatible_mapping(saved_profile['mapping'], columns)
                validate_mapping(df, saved_profile['mapping'], req.domain)
            except (ValueError, KeyError):
                saved_profile = None
        
        domain_pack = None
        try:
            domain_pack = get_domain_pack(req.domain)
        except ValueError:
            pass
            
        # Generate mapping proposal
        proposal = suggest_mapping(columns, sample_rows, domain_pack)
        
        total_rows = 0
        try:
            if hasattr(connector, "total_rows"):
                total_rows = connector.total_rows(**kwargs)
        except Exception:
            pass

        return {
            "connector_description": connector.describe(),
            "connector_warnings": df.attrs.get("connector_warnings", []),
            "columns": columns,
            "data": sample_rows,
            "total_rows": total_rows,
            "signature": sig,
            "saved_profile": saved_profile,
            "canonical_fields": list(dict.fromkeys(get_canonical_fields(domain_pack))),
            "mapping_proposal": proposal.dict()
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/mapping/confirm")
def confirm_mapping(req: MappingConfirmRequest):
    """Confirm a mapping, save profile, and return canonical preview."""
    if not source_exists(req.file_path):
        raise HTTPException(status_code=404, detail="File not found")
        
    try:
        try:
            file_registry.set_file_status(
                file_path=req.file_path,
                status="pending",
                progress=55.0,
                step_text="Headers Mapped"
            )
        except Exception:
            pass

        connector = detect_connector(req.file_path)
        kwargs = {}
        if req.sheet_name:
            kwargs['sheet_name'] = req.sheet_name
        if req.table_or_query:
            kwargs['table_or_query'] = req.table_or_query
            
        # Get preview for quick verification
        df = connector.preview(n=10, **kwargs)
        
        require_source_domain(df, req.domain)
        validate_mapping(df, req.mapping, req.domain)
        canonical_df = apply_mapping(df, req.mapping, req.domain, req.keep_extras)
        if req.save_profile:
            sig = source_signature(list(df.columns), connector.__class__.__name__, req.domain, source_identity(req.file_path, req.sheet_name, req.table_or_query))
            source_label = source_identity(req.file_path).split('::')[0]
            label = f"{connector.__class__.__name__} - {source_label}"
            save_profile(sig, req.mapping, label)
            from app.api.analytics import clear_analytics_cache
            clear_analytics_cache()
            
        canonical_df = canonical_df.astype(object).where(pd.notnull(canonical_df), None)
        
        return {
            "mapped_columns": list(canonical_df.columns),
            "data_preview": canonical_df.to_dict(orient="records")
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/sheets")
def list_sheets(file_path: str = Query(...)):
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File not found")
        
    try:
        connector = detect_connector(file_path)
        if not connector.capabilities().get("multi_sheet"):
            return {"sheets": []}
            
        return {"sheets": connector.list_sheets()}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/normalize")
def normalize_source(req: NormalizeRequest):
    """Legacy normalize endpoint."""
    if not source_exists(req.file_path):
        raise HTTPException(status_code=404, detail="File not found")
        
    try:
        try:
            file_registry.set_file_status(
                file_path=req.file_path,
                status="pending",
                progress=70.0,
                step_text="Data Normalized"
            )
        except Exception:
            pass

        connector = detect_connector(req.file_path)
        kwargs = {}
        if req.sheet_name:
            kwargs['sheet_name'] = req.sheet_name
        if req.table_or_query:
            kwargs['table_or_query'] = req.table_or_query
            
        df = connector.fetch(**kwargs)
        require_source_domain(df, req.domain)
        
        if df.empty:
            return {"columns": [], "data": [], "problems": []}
            
        domain_pack = None
        try:
            domain_pack = get_domain_pack(req.domain)
        except ValueError:
            pass
            
        mapping = req.mapping
        if mapping is None:
            mapping = map_headers(list(df.columns), domain_pack, resolve_conflicts=True)
            
        norm_df = apply_mapping(df, mapping, domain=req.domain, keep_extras=False)
        report = validate(norm_df, domain=req.domain)
        
        norm_df = norm_df.astype(object).where(pd.notnull(norm_df), None)
        
        return {
            "connector_warnings": df.attrs.get("connector_warnings", []),
            "mapped_columns": list(norm_df.columns),
            "mapping_used": mapping,
            "data_preview": norm_df.head(10).to_dict(orient="records"),
            "problems": [p.to_dict() for p in report.problems],
            "validation_report": report.to_dict()
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/validate")
def validate_source(req: ValidateRequest):
    """Run mapping + validation on a connected source file and return the ValidationReport."""
    if not source_exists(req.file_path):
        raise HTTPException(status_code=404, detail="File not found")

    existing = file_registry.get_file_by_path(req.file_path)
    prior_status = "active" if (existing and existing.chunk_count > 0) else "not_ingested"

    try:
        # Only show validation progress if the file isn't already actively ingested
        if prior_status != "active":
            try:
                file_registry.set_file_status(
                    file_path=req.file_path,
                    status="pending",
                    progress=85.0,
                    step_text="Validating Data Quality"
                )
            except Exception:
                pass

        connector = detect_connector(req.file_path)
        kwargs = {}
        if req.sheet_name:
            kwargs["sheet_name"] = req.sheet_name
        if req.table_or_query:
            kwargs["table_or_query"] = req.table_or_query

        df = connector.fetch(**kwargs)
        require_source_domain(df, req.domain)
        if df.empty:
            from app.schema.validate import ValidationReport
            return ValidationReport(
                total_rows=0, error_rows=0, warning_rows=0, null_counts={}, verdict="not_usable", problems=[]
            ).to_dict()

        domain_pack = None
        try:
            domain_pack = get_domain_pack(req.domain)
        except ValueError:
            pass

        mapping = req.mapping
        if mapping is None:
            mapping = map_headers(list(df.columns), domain_pack, resolve_conflicts=True)

        canonical_df = apply_mapping(df, mapping, domain=req.domain, keep_extras=True)
        report = validate(canonical_df, domain=req.domain, table_kind=req.table_kind)
        result = report.to_dict()
        result["connector_warnings"] = df.attrs.get("connector_warnings", [])
        return result
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        try:
            file_registry.set_file_status(
                file_path=req.file_path,
                status=prior_status,
                progress=100.0 if prior_status == "active" else 0.0,
                step_text="Completed" if prior_status == "active" else ""
            )
        except Exception:
            pass

@router.post("/clean")
def clean_source(req: CleanRequest):
    """Run opt-in cleaning pass on a connected source file."""
    if not source_exists(req.file_path):
        raise HTTPException(status_code=404, detail="File not found")

    try:
        connector = detect_connector(req.file_path)
        kwargs = {}
        if req.sheet_name:
            kwargs["sheet_name"] = req.sheet_name
        if req.table_or_query:
            kwargs["table_or_query"] = req.table_or_query

        df = connector.fetch(**kwargs)
        require_source_domain(df, req.domain)
        domain_pack = None
        try:
            domain_pack = get_domain_pack(req.domain)
        except ValueError:
            pass

        mapping = req.mapping
        if mapping is None:
            mapping = map_headers(list(df.columns), domain_pack, resolve_conflicts=True)

        canonical_df = apply_mapping(df, mapping, domain=req.domain, keep_extras=True)
        cleaned_df, summary = clean(canonical_df, options=req.options)

        cleaned_df = cleaned_df.astype(object).where(pd.notnull(cleaned_df), None)

        return {
            "connector_warnings": df.attrs.get("connector_warnings", []),
            "cleaned_preview": cleaned_df.head(10).to_dict(orient="records"),
            "cleaning_summary": summary.to_dict()
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

class SQLConnectRequest(BaseModel):
    connection_string: str
    db_type: str = "sqlite"
    table_or_query: str
    n: int = 5
    watermark_column: Optional[str] = None
    watermark_value: Optional[Any] = None
    domain: str = Field(default_factory=get_default_domain, description="Business domain context")

class WatcherConfigRequest(BaseModel):
    watch_dir: str
    file_pattern: str = "*.*"
    domain: str = Field(default_factory=get_default_domain, description="Business domain context")

class SetActiveDomainRequest(BaseModel):
    domain: str

@router.get("/domains")
def list_available_domains():
    from app.schema.domain import registry
    return {
        "default_domain": get_default_domain(),
        "active_domain": get_default_domain(),
        "domains": registry.available_domains(),
        "domain_details": registry.get_domain_details(),
    }


@router.get('/schema')
def source_schema(domain: str = Query(default_factory=get_default_domain)):
    try:
        return {'canonical_fields': list(dict.fromkeys(get_canonical_fields(get_domain_pack(domain))))}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

@router.get("/domains/active")
def get_active_domain():
    from app.schema.domain import registry
    return {
        "active_domain": get_default_domain(),
        "available_domains": registry.available_domains(),
    }

@router.post("/domains/active")
def switch_active_domain(req: SetActiveDomainRequest):
    from app.schema.domain import registry
    if req.domain not in registry.available_domains():
        raise HTTPException(
            status_code=400,
            detail=f"Unknown domain '{req.domain}'. Available domains: {registry.available_domains()}"
        )
    set_default_domain(req.domain)
    return {
        "status": "success",
        "active_domain": get_default_domain(),
    }

class SQLDiscoverRequest(BaseModel):
    connection_string: str
    db_type: str = "sqlite"
    domain: str = Field(default_factory=get_default_domain, description="Business domain context")
    sample_n: int = 5

class TallyTestRequest(BaseModel):
    url: str
    timeout: int = 5

class ShopifyTestRequest(BaseModel):
    shop_name: str
    access_token: str
    resource: str = "orders"
    api_version: str = "2026-07"
    review_type: str = "review"


@router.post("/shopify/connect")
def connect_shopify_source(req: ShopifyTestRequest):
    """Store the token encrypted locally; return a reusable source URI without secrets."""
    from urllib.parse import urlencode
    from app.connectors.shopify import ShopifyConnector
    from app.connectors.credentials import save_shopify_token
    try:
        connector = ShopifyConnector(req.shop_name, req.access_token, req.api_version,
                                     resource=req.resource, review_type=req.review_type)
        identity = save_shopify_token(connector.shop_name, connector.access_token)
        query = urlencode({"connection_id": identity, "resource": req.resource,
                           "api_version": req.api_version, "review_type": req.review_type})
        return {"file_path": f"shopify://{connector.shop_name}?{query}"}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))

@router.post("/tally/test")
def test_tally_source(req: TallyTestRequest):
    try:
        from app.connectors.tally import TallyConnector
        connector = TallyConnector(req.url, timeout=req.timeout)
        df = connector.preview(n=5)
        return {
            "status": "success",
            "message": "Connected to Tally successfully",
            "total_preview_rows": len(df),
            "connector_warnings": df.attrs.get("connector_warnings", []),
            "columns": list(df.columns) if not df.empty else [],
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/shopify/test")
def test_shopify_source(req: ShopifyTestRequest):
    try:
        from app.connectors.shopify import ShopifyConnector
        connector = ShopifyConnector(shop_name=req.shop_name, access_token=req.access_token, api_version=req.api_version,
                                     resource=req.resource, review_type=req.review_type)
        df = connector.preview(resource=req.resource, n=5)
        return {
            "status": "success",
            "message": "Connected to Shopify successfully",
            "total_preview_rows": len(df),
            "connector_warnings": df.attrs.get("connector_warnings", []),
            "columns": list(df.columns) if not df.empty else [],
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/sql/discover")
def discover_sql_database(req: SQLDiscoverRequest):
    """Auto-discover all tables and schema mappings in a connected SQL database."""
    try:
        from app.connectors.sql import SQLConnector
        connector = SQLConnector(connection_string=req.connection_string, db_type=req.db_type)
        info = connector.inspect_database(sample_n=req.sample_n)
        
        domain_pack = None
        try:
            domain_pack = get_domain_pack(req.domain)
        except ValueError:
            pass
            
        for t in info.get("tables", []):
            if t.get("columns") and t.get("sample_rows"):
                try:
                    proposal = suggest_mapping(t["columns"], t["sample_rows"], domain_pack)
                    t["mapping_proposal"] = proposal.dict()
                    t['canonical_fields'] = list(dict.fromkeys(get_canonical_fields(domain_pack)))
                    sample = pd.DataFrame(t['sample_rows'])
                    signature = sql_signature(t['columns'], connector.__class__.__name__, req.domain, req.connection_string, t['table_name'])
                    mapping = confirmed_mapping(signature, sample, req.domain)
                    t['saved_profile'] = {'mapping': mapping} if mapping is not None else None
                except Exception:
                    t["mapping_proposal"] = {"suggestions": [], "confidence": 0.0}
            else:
                t["mapping_proposal"] = {"suggestions": [], "confidence": 0.0}
                
        return info
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/sql/preview")
def preview_sql_source(req: SQLConnectRequest):
    try:
        from app.connectors.sql import SQLConnector
        connector = SQLConnector(connection_string=req.connection_string, db_type=req.db_type)
        df = connector.preview(n=req.n, table_or_query=req.table_or_query)
        df = df.astype(object).where(pd.notnull(df), None)
        
        columns = list(df.columns)
        sample_rows = df.to_dict(orient="records")
        
        domain_pack = None
        try:
            domain_pack = get_domain_pack(req.domain)
        except ValueError:
            pass
            
        proposal = suggest_mapping(columns, sample_rows, domain_pack)
        
        return {
            "connector_description": connector.describe(),
            "connector_warnings": df.attrs.get("connector_warnings", []),
            "columns": columns,
            "data": sample_rows,
            "mapping_proposal": proposal.dict(),
            "canonical_fields": list(dict.fromkeys(get_canonical_fields(domain_pack))),
            "saved_profile": ({'mapping': saved} if (saved := confirmed_mapping(sql_signature(columns, connector.__class__.__name__, req.domain, req.connection_string, req.table_or_query), df, req.domain)) is not None else None)
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/watcher/list")
def list_watcher_files(req: WatcherConfigRequest):
    try:
        from app.connectors.watcher import DirectoryWatcherConnector
        watcher = DirectoryWatcherConnector(watch_dir=req.watch_dir, file_pattern=req.file_pattern)
        pending = watcher.list_pending_files()
        detected_sql = watcher.detect_sql_database()
        return {
            "watch_dir": req.watch_dir,
            "pending_files": [f.replace("\\", "/") for f in pending],
            "detected_sql_db": detected_sql
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.get("/sql/local-instances")
def detect_local_sql_instances():
    """Auto-detect running local MS SQL Server instances and databases."""
    try:
        from app.connectors.sql import get_best_odbc_driver, get_local_mssql_instances
        import pyodbc
        driver = get_best_odbc_driver()
        candidates = get_local_mssql_instances()
        detected = []
        seen = set()
        for inst in candidates:
            try:
                conn_str = f"DRIVER={{{driver}}};SERVER={inst};Trusted_Connection=yes;TrustServerCertificate=yes;Encrypt=optional;"
                conn = pyodbc.connect(conn_str, timeout=1)
                cur = conn.cursor()
                cur.execute("SELECT name FROM sys.databases WHERE database_id > 4 AND state_desc = 'ONLINE'")
                dbs = [r[0] for r in cur.fetchall()]
                conn.close()
                for db in dbs:
                    key = (inst.lower(), db.lower())
                    if key not in seen:
                        seen.add(key)
                        detected.append({
                            "database_name": db,
                            "server": inst,
                            "driver": driver,
                            "connection_string": f"{inst}/{db}"
                        })
            except Exception:
                continue
        return {"instances": detected}
    except Exception as e:
        return {"instances": [], "error": str(e)}

