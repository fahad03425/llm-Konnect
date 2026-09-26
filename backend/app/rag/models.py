from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional

from app.core.config import get_default_domain

class SourceReference(BaseModel):
    source_file: str = Field(..., description="The original file name.")
    source_row: Optional[int] = Field(None, description="The original row index.")
    label: str = Field(..., description="A short display label for the source (e.g., Invoice INV-123).")

class ChatRequest(BaseModel):
    question: str = Field(..., description="The user's question.")
    session_id: str = Field(..., description="The conversation session ID.")
    domain: str = Field(default_factory=get_default_domain, description="The domain context (defaults to active domain).")
    file_ids: Optional[List[str]] = Field(None, description="Optional list of file IDs to scope the answer to.")
    source_files: Optional[List[str]] = Field(None, description="Optional list of source file paths/names to scope the answer to.")

class ChatResponse(BaseModel):
    answer: str = Field(..., description="The generated or computed answer.")
    route: str = Field(..., description="The route taken: 'rag', 'analytics', or 'chit-chat'.")
    sources: List[SourceReference] = Field(default_factory=list, description="List of records used for the answer.")
    computed_values: Optional[Dict[str, Any]] = Field(None, description="Any deterministically computed numbers.")
    session_id: str = Field(..., description="The session ID.")
    timing: float = Field(..., description="Time taken to process in seconds.")
