import os
import shutil
import threading
from datetime import datetime
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, HTTPException, UploadFile, File, Query, Path
from pydantic import BaseModel

from app.ingestion.registry import file_registry, FileRecord
from app.ingestion.store import KnowledgeBase
from app.connectors.base import detect_connector
from app.schema.mapper import map_headers
from app.schema.normalize import apply_mapping
from app.schema.validate import validate
from app.schema.domain import get_domain_pack

router = APIRouter(prefix="/api/files", tags=["files"])
_kb = KnowledgeBase()

# Fast in-memory tracking for active background ingestion tasks
_active_tasks: Dict[str, Dict[str, Any]] = {}
_tasks_lock = threading.Lock()

_cancelled_tasks: set = set()
_cancelled_tasks_lock = threading.Lock()

def _norm_key(path: str) -> str:
    return path.replace("\\", "/").lower()

def _is_task_cancelled(file_path: str) -> bool:
    key = _norm_key(file_path)
    with _cancelled_tasks_lock:
        return key in _cancelled_tasks

def _set_task_cancelled(file_path: str):
    key = _norm_key(file_path)
    with _cancelled_tasks_lock:
        _cancelled_tasks.add(key)

def _clear_task_cancelled(file_path: str):
    key = _norm_key(file_path)
    with _cancelled_tasks_lock:
        _cancelled_tasks.discard(key)

def _update_task(
    file_path: str,
    file_id: Optional[str],
    progress: float,
    step_text: str,
    status: str = "processing",
    error_message: Optional[str] = None
):
    key = _norm_key(file_path)
    with _tasks_lock:
        _active_tasks[key] = {
            "file_path": file_path,
            "file_id": file_id,
            "progress": progress,
            "step_text": step_text,
            "status": status,
            "error_message": error_message or "",
            "updated_at": datetime.now().isoformat()
        }
        if status in ("active", "completed", "failed"):
            # Keep in memory briefly or clear on next check
            pass

    # Persist to SQLite file registry
    file_registry.set_file_status(
        file_path=file_path,
        status=status,
        file_id=file_id,
        progress=progress,
        step_text=step_text,
        error_message=error_message
    )

def get_base_dirs():
    base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    upload_dir = os.path.join(base_dir, "data", "uploads")
    samples_dir = os.path.join(base_dir, "data", "samples")
    storage_dir = os.path.join(base_dir, "data", "storage")
    os.makedirs(upload_dir, exist_ok=True)
    return base_dir, upload_dir, samples_dir, storage_dir

def format_bytes(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    elif size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    elif size < 1024 * 1024 * 1024:
        return f"{size / (1024 * 1024):.1f} MB"
    else:
        return f"{size / (1024 * 1024 * 1024):.2f} GB"

class FileItem(BaseModel):
    file_id: Optional[str] = None
    filename: str
    file_path: str
    file_size_bytes: int
    file_size_formatted: str
    extension: str
    dir_type: Optional[str] = "upload"
    modified_at: Optional[str] = None
    is_ingested: bool
    is_processing: Optional[bool] = False
    is_duplicate_of: Optional[str] = None
    chunk_count: int = 0
    domain: Optional[str] = "pharmacy"
    strategy: Optional[str] = "row"
    status: Optional[str] = "not_ingested"
    progress: Optional[float] = 0.0
    step_text: Optional[str] = ""
    error_message: Optional[str] = None
    ingested_at: Optional[str] = None
    group_name: Optional[str] = None
    source_type: Optional[str] = "file"
    table_name: Optional[str] = None

class QuickIngestRequest(BaseModel):
    file_path: str
    domain: str = "pharmacy"
    strategy: str = "row"
    file_id: Optional[str] = None

class CancelIngestRequest(BaseModel):
    file_path: Optional[str] = None
    file_id: Optional[str] = None
    filename: Optional[str] = None

class UningestRequest(BaseModel):
    file_id: Optional[str] = None
    file_path: Optional[str] = None

@router.get("", response_model=Dict[str, Any])
@router.get("/list", response_model=Dict[str, Any])
def list_all_files():
    """List all uploaded and sample dataset files with ingestion status, real-time progress, and duplicate detection."""
    _, upload_dir, samples_dir, storage_dir = get_base_dirs()

    # Query all records from registry including active, processing, and failed
    all_registered = file_registry.list_files(include_all=True)
    registered_files: Dict[str, FileRecord] = {}
    registered_by_filename: Dict[str, FileRecord] = {}
    ingested_by_hash: Dict[str, FileRecord] = {}

    for r in all_registered:
        k_path = file_registry.normalize_path(r.file_path).lower()
        if k_path not in registered_files:
            registered_files[k_path] = r
        if r.filename.lower() not in registered_by_filename:
            registered_by_filename[r.filename.lower()] = r
        if r.status == "active" and r.chunk_count > 0 and r.file_hash not in ingested_by_hash:
            ingested_by_hash[r.file_hash] = r

    with _tasks_lock:
        active_tasks_copy = dict(_active_tasks)

    items: List[Dict[str, Any]] = []
    seen_paths = set()

    scan_dirs = [(upload_dir, "upload"), (samples_dir, "sample")]

    for directory, dir_type in scan_dirs:
        if not os.path.exists(directory):
            continue
        for fname in os.listdir(directory):
            if fname.startswith("."):
                continue
            fpath = os.path.join(directory, fname)
            if not os.path.isfile(fpath):
                continue
            canonical_path = os.path.abspath(fpath).replace("\\", "/")
            path_key = canonical_path.lower()
            if path_key in seen_paths:
                continue
            seen_paths.add(path_key)

            size_bytes = os.path.getsize(fpath)
            ext = os.path.splitext(fname)[1].lower()
            mtime = datetime.fromtimestamp(os.path.getmtime(fpath)).isoformat()

            reg = registered_files.get(path_key) or registered_by_filename.get(fname.lower())
            active_task = active_tasks_copy.get(path_key)

            # Determine live status & progress
            if active_task and active_task.get("status") == "processing":
                is_processing = True
                status = "processing"
                progress = float(active_task.get("progress", 10.0))
                step_text = active_task.get("step_text", "Processing...")
                err_msg = None
                is_ingested = False
            elif reg and reg.status == "processing":
                is_processing = True
                status = "processing"
                progress = float(reg.progress or 10.0)
                step_text = reg.step_text or "Processing..."
                err_msg = None
                is_ingested = False
            elif reg and reg.status == "failed":
                is_processing = False
                status = "failed"
                progress = 0.0
                step_text = "Failed"
                err_msg = reg.error_message
                is_ingested = False
            elif reg and reg.status == "active" and reg.chunk_count > 0:
                is_processing = False
                status = "active"
                progress = 100.0
                step_text = "Completed"
                err_msg = None
                is_ingested = True
            else:
                is_processing = False
                status = "not_ingested"
                progress = 0.0
                step_text = ""
                err_msg = None
                is_ingested = False

            # Duplicate detection by SHA-256
            is_dup_of = None
            if not is_ingested and not is_processing and size_bytes > 0:
                f_hash = file_registry.calculate_hash(fpath)
                match = ingested_by_hash.get(f_hash)
                if match and match.filename.lower() != fname.lower():
                    is_dup_of = match.filename

            items.append({
                "file_id": reg.file_id if reg else (active_task.get("file_id") if active_task else None),
                "filename": fname,
                "file_path": canonical_path,
                "file_size_bytes": size_bytes,
                "file_size_formatted": format_bytes(size_bytes),
                "extension": ext,
                "dir_type": dir_type,
                "modified_at": mtime,
                "is_ingested": is_ingested,
                "is_processing": is_processing,
                "is_duplicate_of": is_dup_of,
                "chunk_count": reg.chunk_count if reg else 0,
                "domain": reg.domain if reg else "pharmacy",
                "strategy": reg.strategy if reg else "row",
                "status": status,
                "progress": progress,
                "step_text": step_text,
                "error_message": err_msg,
                "ingested_at": reg.ingested_at if reg else None,
                "group_name": reg.group_name if reg else None,
                "source_type": reg.source_type if reg else ("database" if "sql://" in canonical_path else "file"),
                "table_name": reg.table_name if reg else None
            })

    # Also include any registered files that might be in external paths or databases
    for reg in all_registered:
        if reg.filename.startswith("."):
            continue
        norm = os.path.abspath(reg.file_path).replace("\\", "/").lower()
        if norm not in seen_paths:
            seen_paths.add(norm)
            exists = os.path.exists(reg.file_path)
            size_bytes = os.path.getsize(reg.file_path) if exists else reg.file_size_bytes or 0
            active_task = active_tasks_copy.get(norm)

            if active_task and active_task.get("status") == "processing":
                is_proc = True
                stat = "processing"
                prog = float(active_task.get("progress", 10.0))
                step = active_task.get("step_text", "Processing...")
                err = None
                is_ing = False
            elif reg.status == "processing":
                if reg.chunk_count and reg.chunk_count > 0:
                    is_proc = False
                    stat = "active"
                    prog = 100.0
                    step = "Completed"
                    err = None
                    is_ing = True
                else:
                    is_proc = True
                    stat = "processing"
                    prog = float(reg.progress or 10.0)
                    step = reg.step_text or "Processing..."
                    err = None
                    is_ing = False
            elif reg.status == "failed":
                is_proc = False
                stat = "failed"
                prog = 0.0
                step = "Failed"
                err = reg.error_message
                is_ing = False
            elif reg.status == "active" and reg.chunk_count > 0:
                is_proc = False
                stat = "active"
                prog = 100.0
                step = "Completed"
                err = None
                is_ing = True
            else:
                is_proc = False
                stat = "not_ingested"
                prog = 0.0
                step = ""
                err = None
                is_ing = False

            items.append({
                "file_id": reg.file_id,
                "filename": reg.filename,
                "file_path": reg.file_path.replace("\\", "/"),
                "file_size_bytes": size_bytes,
                "file_size_formatted": format_bytes(size_bytes),
                "extension": os.path.splitext(reg.filename)[1].lower() or "db",
                "dir_type": "database" if (reg.source_type == "database" or "sql://" in reg.file_path) else "external",
                "modified_at": reg.ingested_at,
                "is_ingested": is_ing,
                "is_processing": is_proc,
                "is_duplicate_of": None,
                "chunk_count": reg.chunk_count,
                "domain": reg.domain,
                "strategy": reg.strategy,
                "status": stat,
                "progress": prog,
                "step_text": step,
                "error_message": err,
                "ingested_at": reg.ingested_at,
                "group_name": reg.group_name,
                "source_type": reg.source_type or ("database" if "sql://" in reg.file_path else "file"),
                "table_name": reg.table_name
            })

    total_chunks = sum(i["chunk_count"] for i in items)
    ingested_count = sum(1 for i in items if i["is_ingested"])
    total_bytes = sum(i["file_size_bytes"] for i in items)

    # Sort: Processing first, then Ingested, then largest datasets
    sorted_files = sorted(
        items,
        key=lambda x: (not x.get("is_processing", False), not x["is_ingested"], -x["file_size_bytes"], x["filename"].lower())
    )

    return {
        "files": sorted_files,
        "total_files": len(items),
        "total_ingested_files": ingested_count,
        "total_chunks": total_chunks,
        "total_size_formatted": format_bytes(total_bytes)
    }

@router.post("/upload")
async def upload_file(file: UploadFile = File(...)):
    """Upload a file to data/uploads."""
    _, upload_dir, _, _ = get_base_dirs()
    safe_filename = file.filename.replace("/", "").replace("\\", "")
    file_path = os.path.join(upload_dir, safe_filename)

    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    normalized_path = file_path.replace("\\", "/")
    size_bytes = os.path.getsize(file_path)

    return {
        "message": "File uploaded successfully",
        "filename": safe_filename,
        "file_path": normalized_path,
        "file_size_bytes": size_bytes,
        "file_size_formatted": format_bytes(size_bytes),
        "is_ingested": False,
        "progress": 0.0,
        "status": "not_ingested"
    }

def _run_ingest_background(file_path: str, domain: str, strategy: str, file_id: Optional[str]):
    """Execute ingestion in background thread with live stage reporting and cancellation support."""
    active_file_id = file_id
    try:
        if _is_task_cancelled(file_path):
            raise InterruptedError("Ingestion was cancelled before start")

        _update_task(file_path, file_id, 10.0, "Reading dataset & detecting format...", status="processing")
        connector = detect_connector(file_path)
        df = connector.fetch()

        if _is_task_cancelled(file_path):
            raise InterruptedError("Ingestion was cancelled")

        if df.empty:
            _update_task(file_path, file_id, 0.0, "File is empty", status="failed", error_message="Source file contains no data rows.")
            return

        _update_task(file_path, file_id, 25.0, "Auto-mapping domain schema...", status="processing")
        domain_pack = None
        try:
            domain_pack = get_domain_pack(domain)
        except ValueError:
            pass

        mapping = map_headers(list(df.columns), domain_pack)
        canonical_df = apply_mapping(df, mapping, domain=domain, keep_extras=True)

        if _is_task_cancelled(file_path):
            raise InterruptedError("Ingestion was cancelled")

        _update_task(file_path, file_id, 35.0, "Validating canonical records...", status="processing")
        try:
            report = validate(canonical_df, domain=domain)
            if not report.is_usable:
                err_reasons = "; ".join(p.message for p in report.errors) if report.errors else "Data validation notice"
                print(f"[Ingestion] Dataset format notices for {file_path}: {err_reasons}")
        except Exception as ve:
            print(f"[Ingestion] Validation notice for {file_path}: {ve}")

        existing_reg = file_registry.get_file_by_path(file_path)
        active_file_id = file_id or (existing_reg.file_id if existing_reg else None)

        if active_file_id:
            _kb.delete_source(active_file_id)
        _kb.delete_source(file_path)

        if _is_task_cancelled(file_path):
            raise InterruptedError("Ingestion was cancelled")

        source_meta = {
            "source_file": file_path,
            "source_connector": connector.__class__.__name__
        }

        def on_kb_progress(pct: float, step: str):
            if _is_task_cancelled(file_path):
                raise InterruptedError("Ingestion was cancelled")
            _update_task(file_path, active_file_id, pct, step, status="processing")

        summary = _kb.add_dataframe(
            canonical_df,
            source_meta=source_meta,
            domain=domain,
            strategy=strategy,
            file_id=active_file_id,
            progress_callback=on_kb_progress,
            cancel_check=lambda: _is_task_cancelled(file_path)
        )

        if _is_task_cancelled(file_path):
            raise InterruptedError("Ingestion was cancelled")

        _update_task(file_path, active_file_id, 98.0, "Registering in Knowledge Base...", status="processing")

        file_registry.register_or_update(
            file_path=file_path,
            chunk_count=summary.total_chunks,
            domain=domain,
            strategy=strategy,
            file_id=active_file_id
        )

        _update_task(file_path, active_file_id, 100.0, f"Completed ({summary.total_chunks} chunks)", status="active")

        # Clean up active memory task
        with _tasks_lock:
            _active_tasks.pop(_norm_key(file_path), None)
        _clear_task_cancelled(file_path)

    except (InterruptedError, Exception) as e:
        is_cancelled = isinstance(e, InterruptedError) or _is_task_cancelled(file_path)
        if is_cancelled:
            print(f"[Ingestion] Ingestion cancelled for {file_path}")
            try:
                if active_file_id:
                    _kb.delete_source(active_file_id)
                _kb.delete_source(file_path)
                file_registry.delete_file(file_path)
                if active_file_id:
                    file_registry.delete_file(active_file_id)
            except Exception as ce:
                print(f"[Ingestion] Cleanup error on cancel: {ce}")
            with _tasks_lock:
                _active_tasks.pop(_norm_key(file_path), None)
            _clear_task_cancelled(file_path)
        else:
            import traceback
            traceback.print_exc()
            print(f"Background ingestion failed for {file_path}: {e}")
            _update_task(file_path, file_id, 0.0, "Ingestion failed", status="failed", error_message=str(e))
            with _tasks_lock:
                _active_tasks.pop(_norm_key(file_path), None)
            _clear_task_cancelled(file_path)

@router.post("/quick-ingest")
def quick_ingest_file(req: QuickIngestRequest):
    """Start background ingestion with strict duplicate checking and live progress tracking."""
    if not os.path.exists(req.file_path):
        raise HTTPException(status_code=404, detail="File not found")

    # Clear any previous cancelled state for this file
    _clear_task_cancelled(req.file_path)

    # 1. Check if the exact file or identical content is already ingested or processing
    file_hash = file_registry.calculate_hash(req.file_path)
    existing_by_hash = file_registry.get_file_by_hash(file_hash)

    if existing_by_hash and existing_by_hash.status == "active" and existing_by_hash.chunk_count > 0:
        norm_existing = existing_by_hash.file_path.replace("\\", "/").lower()
        norm_req = req.file_path.replace("\\", "/").lower()
        if norm_existing == norm_req:
            raise HTTPException(
                status_code=409,
                detail=f"File '{existing_by_hash.filename}' is already ingested ({existing_by_hash.chunk_count} chunks). Un-ingest first if you wish to re-index."
            )
        else:
            raise HTTPException(
                status_code=409,
                detail=f"Duplicate content: This file is identical to '{existing_by_hash.filename}', which is already ingested ({existing_by_hash.chunk_count} chunks)."
            )

    norm_path = _norm_key(req.file_path)
    with _tasks_lock:
        in_memory_task = _active_tasks.get(norm_path)
        if in_memory_task and in_memory_task.get("status") == "processing":
            raise HTTPException(
                status_code=409,
                detail=f"File '{os.path.basename(req.file_path)}' is already currently processing."
            )

    if existing_by_hash and existing_by_hash.status == "processing":
        norm_hash_path = _norm_key(existing_by_hash.file_path)
        with _tasks_lock:
            if norm_hash_path in _active_tasks and _active_tasks[norm_hash_path].get("status") == "processing":
                raise HTTPException(
                    status_code=409,
                    detail=f"File '{existing_by_hash.filename}' is already currently processing."
                )

    # 2. Mark as processing in memory and SQLite registry
    reg = file_registry.set_file_status(
        file_path=req.file_path,
        status="processing",
        file_id=req.file_id,
        domain=req.domain,
        strategy=req.strategy,
        progress=5.0,
        step_text="Starting ingestion..."
    )

    with _tasks_lock:
        _active_tasks[norm_path] = {
            "file_path": req.file_path,
            "file_id": reg.file_id,
            "progress": 5.0,
            "step_text": "Starting ingestion...",
            "status": "processing",
            "error_message": "",
            "updated_at": datetime.now().isoformat()
        }

    # 3. Launch background thread
    t = threading.Thread(
        target=_run_ingest_background,
        args=(req.file_path, req.domain, req.strategy, reg.file_id),
        daemon=True
    )
    t.start()

    return {
        "status": "processing",
        "message": f"Ingestion started in background for {reg.filename}",
        "file_id": reg.file_id,
        "filename": reg.filename,
        "file_path": req.file_path,
        "progress": 5.0,
        "step_text": "Starting ingestion..."
    }

@router.post("/cancel-ingest")
def cancel_ingest_file(req: CancelIngestRequest):
    """Cancel an active background ingestion task and reset file state."""
    target_path = req.file_path
    target_id = req.file_id

    if not target_path and target_id:
        reg = file_registry.get_file_by_id(target_id)
        if reg:
            target_path = reg.file_path

    if not target_path and req.filename:
        _, upload_dir, samples_dir, _ = get_base_dirs()
        cand1 = os.path.join(upload_dir, req.filename)
        cand2 = os.path.join(samples_dir, req.filename)
        if os.path.exists(cand1):
            target_path = cand1
        elif os.path.exists(cand2):
            target_path = cand2

    if not target_path:
        raise HTTPException(status_code=400, detail="file_path, file_id, or filename is required to cancel ingestion.")

    norm_path = target_path.replace("\\", "/")
    key = _norm_key(norm_path)

    # 1. Mark task as cancelled so any background loop aborts immediately
    _set_task_cancelled(norm_path)

    # 2. Clear memory task tracking
    with _tasks_lock:
        _active_tasks.pop(key, None)

    # 3. Clean up SQLite registry and Chroma DB
    try:
        if target_id:
            _kb.delete_source(target_id)
            file_registry.delete_file(target_id)
        _kb.delete_source(norm_path)
        file_registry.delete_file(norm_path)
    except Exception as e:
        print(f"[Cancel] Cleanup notice for {norm_path}: {e}")

    return {
        "status": "cancelled",
        "message": f"Ingestion cancelled for {os.path.basename(norm_path)}",
        "file_path": norm_path,
        "file_id": target_id
    }

@router.post("/un-ingest")
def uningest_file(req: UningestRequest):
    """Un-ingest a file from Chroma and registry, keeping the physical file."""
    try:
        file_id = req.file_id
        file_path = req.file_path

        if file_id:
            reg = file_registry.get_file_by_id(file_id)
            if reg:
                file_path = reg.file_path
            _kb.delete_source(file_id)
            file_registry.delete_file(file_id)

        if file_path:
            norm = _norm_key(file_path)
            with _tasks_lock:
                _active_tasks.pop(norm, None)
            _kb.delete_source(file_path)
            file_registry.delete_file(file_path)

        return {
            "status": "success",
            "message": "File successfully un-ingested from Knowledge Base",
            "file_id": file_id,
            "file_path": file_path
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.delete("")
def delete_file_and_source(
    file_path: Optional[str] = Query(None),
    file_id: Optional[str] = Query(None),
    filename: Optional[str] = Query(None)
):
    """Delete the physical file from disk AND un-ingest from Chroma/registry."""
    try:
        target_path = file_path
        reg = None
        if file_id:
            reg = file_registry.get_file_by_id(file_id)
            if reg and not target_path:
                target_path = reg.file_path
        
        if not target_path and filename:
            _, upload_dir, samples_dir, _ = get_base_dirs()
            candidate1 = os.path.join(upload_dir, filename)
            candidate2 = os.path.join(samples_dir, filename)
            if os.path.exists(candidate1):
                target_path = candidate1
            elif os.path.exists(candidate2):
                target_path = candidate2

        if not reg and target_path:
            reg = file_registry.get_file_by_path(target_path)

        # 1. Un-ingest from Chroma & registry ONLY if file was registered/ingested
        if reg:
            _kb.delete_source(reg.file_id)
            _kb.delete_source(reg.filename)
            file_registry.delete_file(reg.file_id)
        elif file_id:
            _kb.delete_source(file_id)
            file_registry.delete_file(file_id)

        if target_path:
            norm = _norm_key(target_path)
            with _tasks_lock:
                _active_tasks.pop(norm, None)

        # 2. Delete file from disk if it exists
        deleted_from_disk = False
        if target_path and os.path.exists(target_path):
            os.remove(target_path)
            deleted_from_disk = True

        return {
            "status": "success",
            "message": "Successfully deleted file.",
            "deleted_from_disk": deleted_from_disk,
            "target_path": target_path
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
