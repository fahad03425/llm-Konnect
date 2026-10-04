"""Token-safe chunks and protected publication of prepared source replacements."""
import os
import re
import threading
import uuid
from functools import wraps

_locks = {}
_locks_guard = threading.Lock()


def collection_lock(kb):
    key = (os.path.normcase(os.path.abspath(kb.chroma_dir)), kb.collection_name)
    with _locks_guard:
        return _locks.setdefault(key, threading.RLock())


def locked(method):
    @wraps(method)
    def call(self, *args, **kwargs):
        with collection_lock(self):
            return method(self, *args, **kwargs)
    return call


def chunk_identity(meta):
    # New records carry an explicit ID; old records retain distinct text windows.
    return meta.get("chunk_id") or (
        meta.get("domain"), meta.get("file_id"), meta.get("source_file"),
        meta.get("source_row"), meta.get("doc_index"), meta.get("chunk_index", 0)
    )


def token_windows(text, embedder, prefix, size, overlap=0.0):
    """Preserve original character spans while respecting the encoder's limit."""
    if size <= 0 or not 0 <= overlap < 1:
        raise ValueError("chunk_size must be positive and chunk_overlap must be in [0, 1).")
    model_limit = getattr(embedder, "max_seq_length", size)
    if not isinstance(model_limit, int):
        model_limit = size
        tokenizer = None
    else:
        tokenizer = getattr(embedder, "tokenizer", None)
    limit = min(int(size), model_limit)
    if tokenizer is None:  # lightweight deterministic test embedders
        spans = [match.span() for match in re.finditer(r"\S+", text)]
        budget = limit - len(prefix.split())
        fits = lambda value: len((prefix + value).split()) <= limit
    else:
        overhead = len(tokenizer.encode(prefix, add_special_tokens=True))
        budget = limit - overhead
        fits = lambda value: len(tokenizer.encode(prefix + value, add_special_tokens=True)) <= limit
        try:
            encoded = tokenizer(text, add_special_tokens=False, return_offsets_mapping=True)
            spans = [tuple(span) for span in encoded["offset_mapping"] if span[1] > span[0]]
        except (NotImplementedError, TypeError):
            # Slow tokenizers: use exact character windows and validate each one.
            spans = [(index, index + 1) for index in range(len(text))]
    if budget <= 0:
        raise ValueError("Embedding limit leaves no space for document content.")
    if not spans:
        return []
    windows = []
    start = 0
    while start < len(spans):
        end = min(start + budget, len(spans))
        char_start = 0 if start == 0 else spans[start][0]
        char_end = len(text) if end == len(spans) else spans[end][0]
        while end > start and not fits(text[char_start:char_end]):
            end -= 1
            char_end = spans[end][0] if end < len(spans) else len(text)
        if end <= start:
            raise ValueError("A token cannot fit within the embedding model limit.")
        windows.append(text[char_start:char_end])
        if end == len(spans):
            break
        overlap_tokens = min(int((end - start) * overlap), end - start - 1)
        start = end - overlap_tokens
    return windows


def pages(collection, where=None):
    offset = 0
    while True:
        page = collection.get(where=where, include=["documents", "metadatas", "embeddings"],
                              limit=256, offset=offset)
        if not page["ids"]:
            break
        yield page
        offset += len(page["ids"])


def copy_page(target, page):
    target.upsert(ids=page["ids"], documents=page["documents"],
                  metadatas=page["metadatas"], embeddings=page["embeddings"])


def replace_source(kb, method_name, args, kwargs):
    """Prepare without touching active data; roll back publication on failure.

    Chroma has no multi-batch transaction. A disk-backed backup keeps rollback
    memory bounded, and the collection lock hides publication from app readers.
    """
    from app.ingestion.store import KnowledgeBase
    collection = kb._get_chroma()
    client = kb._chroma_client
    name = "stage_" + uuid.uuid4().hex
    backup_name = "backup_" + uuid.uuid4().hex
    stage = KnowledgeBase(kb.chroma_dir, name)
    stage.embedding_model_name = kb.embedding_model_name
    stage.passage_prefix = kb.passage_prefix
    stage.query_prefix = kb.query_prefix
    stage._get_embedder = kb._get_embedder
    backup = None
    preserve_backup = False
    cancel = kwargs.get("cancel_check")
    source_meta = kwargs["source_meta"]
    source = source_meta.get("source_file", "unknown")
    file_id = kwargs.get("file_id")
    domain = kwargs.get("domain")
    if not domain:
        from app.core.config import get_default_domain
        domain = get_default_domain()
    source_conditions = [{"source_file": source}]
    normalized = source.replace("\\", "/")
    if normalized != source:
        source_conditions.append({"source_file": normalized})
    if file_id:
        source_conditions.append({"file_id": file_id})
    source_filter = source_conditions[0] if len(source_conditions) == 1 else {"$or": source_conditions}
    where = {"$and": [source_filter, {"domain": domain}]}
    try:
        summary = getattr(stage, method_name)(*args, **kwargs)
        if cancel and cancel():
            raise InterruptedError("Ingestion cancelled before publication")
        with collection_lock(kb):
            backup = client.get_or_create_collection(backup_name)
            for page in pages(collection, where):
                copy_page(backup, page)
            try:
                for page in pages(stage._get_chroma()):
                    if cancel and cancel():
                        raise InterruptedError("Ingestion cancelled during publication")
                    copy_page(collection, page)
                if cancel and cancel():
                    raise InterruptedError("Ingestion cancelled during publication")
                # Prune stale IDs only after every replacement vector is stored.
                for page in pages(backup):
                    present = stage._get_chroma().get(ids=page["ids"], include=[])["ids"]
                    stale = list(set(page["ids"]) - set(present))
                    if stale:
                        collection.delete(ids=stale)
            except BaseException:
                try:
                    for page in pages(stage._get_chroma()):
                        collection.delete(ids=page["ids"])
                    for page in pages(backup):
                        copy_page(collection, page)
                except Exception as restore_error:
                    preserve_backup = True
                    raise RuntimeError(f"Replacement rollback failed; recovery data retained in {backup_name}") from restore_error
                raise
        return summary
    finally:
        for cleanup_name in (name, None if preserve_backup else backup_name):
            if cleanup_name:
                try:
                    client.delete_collection(cleanup_name)
                except Exception:
                    pass


def publish_delta(kb, prepared, deleted_ids, cancel=None):
    """Publish only changed SQL chunks with a disk-backed rollback snapshot."""
    collection = kb._get_chroma()
    client = kb._chroma_client
    backup_name = "backup_" + uuid.uuid4().hex
    touched = set(deleted_ids)
    for page in pages(prepared):
        touched.update(page["ids"])
    if not touched:
        return
    touched = list(touched)
    keep_backup = False
    with collection_lock(kb):
        backup = client.get_or_create_collection(backup_name)
        try:
            for start in range(0, len(touched), 256):
                page = collection.get(ids=touched[start:start + 256],
                                      include=["documents", "metadatas", "embeddings"])
                if page["ids"]:
                    copy_page(backup, page)
            try:
                for page in pages(prepared):
                    if cancel and cancel():
                        raise InterruptedError("Database synchronization cancelled")
                    copy_page(collection, page)
                for start in range(0, len(deleted_ids), 256):
                    if cancel and cancel():
                        raise InterruptedError("Database synchronization cancelled")
                    collection.delete(ids=deleted_ids[start:start + 256])
            except BaseException:
                try:
                    for start in range(0, len(touched), 256):
                        collection.delete(ids=touched[start:start + 256])
                    for page in pages(backup):
                        copy_page(collection, page)
                except Exception as error:
                    keep_backup = True
                    raise RuntimeError(f"Synchronization rollback failed; recovery data retained in {backup_name}") from error
                raise
        finally:
            if not keep_backup:
                client.delete_collection(backup_name)
