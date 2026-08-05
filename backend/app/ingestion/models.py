"""Models for Document Ingestion and Knowledge Base."""
from pydantic import BaseModel, Field
from typing import Dict, Any, Optional

class IngestSummary(BaseModel):
    total_chunks: int = Field(..., description="Total number of chunks successfully embedded and stored.")
    source_file: str = Field(..., description="The original source file path.")
    source_connector: str = Field(..., description="The connector used (e.g., CSVConnector).")
    domain: str = Field(..., description="The domain pack used for ingestion.")
    time_taken_sec: float = Field(..., description="Time taken to ingest in seconds.")

class RetrievedChunk(BaseModel):
    text: str = Field(..., description="The natural language chunk text.")
    metadata: Dict[str, Any] = Field(..., description="The filterable metadata associated with the chunk.")
    score: float = Field(..., description="The similarity score (e.g., cosine distance converted to similarity).")
    source_row: Optional[int] = Field(None, description="The original row index from the canonical dataframe, if applicable.")
