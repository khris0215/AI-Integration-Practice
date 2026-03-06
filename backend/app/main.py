from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from . import retrieval, generation
from .models import QueryRequest, QueryResponse, SourceDocument

app = FastAPI(title="CFIR Generator Prototype")

# Allow CORS for frontend (adjust origins as needed)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # For development only; restrict in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.post("/ingest", status_code=202)
async def ingest():
    """Trigger OneDrive download and rebuild vector store."""
    from .ingestion import download_files_from_onedrive
    download_files_from_onedrive()
    retrieval.create_vector_store()
    return {"message": "Ingestion started. Check logs."}

@app.post("/query", response_model=QueryResponse)
async def query(request: QueryRequest):
    chunks_with_scores = retrieval.retrieve_relevant_chunks(request.prompt, k=5)
    if not chunks_with_scores:
        raise HTTPException(status_code=404, detail="No relevant documents found.")
    
    context = "\n\n".join([doc.page_content for doc, score in chunks_with_scores])
    sources = [
        SourceDocument(
            filename=doc.metadata.get("source", "Unknown"),
            snippet=doc.page_content[:200] + "..."
        ) for doc, score in chunks_with_scores
    ]
    answer = generation.generate_cfir(request.prompt, context)
    return QueryResponse(answer=answer, sources=sources)