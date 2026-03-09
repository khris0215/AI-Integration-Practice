import time
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from . import retrieval, generation
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
    print("✅ Real query handler called")
    start_time = time.time()
    try:
        # 1. Retrieve relevant chunks from Chroma
        chunks_with_scores = retrieval.retrieve_relevant_chunks(request.prompt, k=5)

        if not chunks_with_scores:
            context = "No relevant documents found in the database."
            sources = []
        else:
            context_parts = []
            sources = []
            for doc, score in chunks_with_scores:
                full_path = doc.metadata.get("source", "Unknown")
                filename = Path(full_path).name
                context_parts.append(f"[Source: {filename}]\n{doc.page_content}")
                sources.append(SourceDocument(
                    filename=filename,
                    snippet=doc.page_content[:200] + "...",
                    relevance_score=float(score)
                ))
            context = "\n\n---\n\n".join(context_parts)

        # 2. Generate CFIR using Ollama
        answer = generation.generate_cfir(
            query=request.prompt,
            context=context,
            model=request.model,
            temperature=request.temperature
        )

        processing_time = int((time.time() - start_time) * 1000)
        return QueryResponse(
            answer=answer,
            sources=sources,
            processing_time_ms=processing_time,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


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