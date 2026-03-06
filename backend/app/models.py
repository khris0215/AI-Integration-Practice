from typing import List, Optional

from pydantic import BaseModel


class QueryRequest(BaseModel):
    prompt: str
    model: Optional[str] = "mistral:7b-instruct-q4_K_M"
    temperature: Optional[float] = 0.2
    max_tokens: Optional[int] = 1000


class SourceDocument(BaseModel):
    filename: str
    snippet: str
    relevance_score: Optional[float] = None


class QueryResponse(BaseModel):
    answer: str
    sources: List[SourceDocument]
    processing_time_ms: Optional[int] = None