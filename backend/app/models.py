from pydantic import BaseModel
from typing import List

class QueryRequest(BaseModel):
    prompt: str

class SourceDocument(BaseModel):
    filename: str
    snippet: str

class QueryResponse(BaseModel):
    answer: str
    sources: List[SourceDocument]