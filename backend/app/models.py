from typing import List, Optional

from pydantic import BaseModel


class QueryRequest(BaseModel):
    prompt: str
    conversation_id: Optional[int] = None
    model: Optional[str] = "mistral:7b-instruct-q4_K_M"
    temperature: Optional[float] = 0.2
    max_tokens: Optional[int] = 700


class SourceDocument(BaseModel):
    filename: str
    snippet: str
    relevance_score: Optional[float] = None


class QueryResponse(BaseModel):
    answer: str
    sources: List[SourceDocument]
    conversation_id: int
    processing_time_ms: Optional[int] = None


class IntentRouteRequest(BaseModel):
    prompt: str
    has_selected_template: Optional[bool] = False
    has_uploaded_template: Optional[bool] = False


class IntentRouteResponse(BaseModel):
    intent: str
    confidence: float
    reason: str


class TemplateMetadataPatch(BaseModel):
    name: Optional[str] = None
    type: Optional[str] = None
    owner_team: Optional[str] = None
    fields: Optional[List[str]] = None
    active: Optional[bool] = None
    is_default: Optional[bool] = None