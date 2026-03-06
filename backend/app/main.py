import time

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import retrieval
from .models import QueryRequest, QueryResponse, SourceDocument


app = FastAPI(title="CFIR Generator API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5500",
        "http://127.0.0.1:5500",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
        "null",  # file:// origin during local testing
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.post("/api/query", response_model=QueryResponse)
async def query(request: QueryRequest) -> QueryResponse:
    """Return a mock CFIR response so the frontend can integrate safely."""
    start_time = time.time()

    mock_answer = f"""# Cyber Fraud Incident Report

**Incident Type:** Phishing Campaign
**Date Range:** March 1-15, 2025
**Summary:** Multiple phishing emails targeting finance department

## Details
Based on your query: \"{request.prompt}\"

This is a mock response while we integrate the AI model.
- 23 employees received suspicious emails
- 3 clicked on malicious links
- No data exfiltration detected

## Recommendations
- Enable MFA for all finance users
- Conduct phishing awareness training
- Review email filtering rules
"""

    mock_sources = [
        SourceDocument(
            filename="incident_20250312_phishing.pdf",
            snippet="Email security alert: Multiple users reported suspicious messages...",
            relevance_score=0.95,
        ),
        SourceDocument(
            filename="quarterly_report_q1_2025.docx",
            snippet="Phishing attempts increased 45% compared to previous quarter...",
            relevance_score=0.87,
        ),
    ]

    processing_time = int((time.time() - start_time) * 1000)

    return QueryResponse(
        answer=mock_answer,
        sources=mock_sources,
        processing_time_ms=processing_time,
    )


@app.get("/api/health")
async def health() -> dict:
    return {"status": "healthy", "version": "1.0.0"}


@app.post("/api/ingest", status_code=202)
async def ingest() -> dict:
    """Trigger OneDrive download and rebuild vector store for future real queries."""
    from .ingestion import download_files_from_onedrive

    download_files_from_onedrive()
    retrieval.create_vector_store()
    return {"message": "Ingestion started. Check logs."}