import time
import logging
import asyncio
import re
from pathlib import Path

from fastapi import FastAPI, HTTPException, File, Form, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, JSONResponse

from . import retrieval, generation, template_filler
from .models import QueryRequest, QueryResponse, SourceDocument


app = FastAPI(title="CFIR Generator API")
logger = logging.getLogger(__name__)
app.state.ready = False
app.state.startup_error = ""

FILENAME_HEADER_RE = re.compile(r"(?im)^\s*\[Filename:\s*[^\]]+\]\s*$")


def _clean_chunk_text(text: str) -> str:
    """Remove retrieval-only filename tags so they don't pollute extracted fields."""
    cleaned = FILENAME_HEADER_RE.sub("", text or "")
    return cleaned.strip()


def _build_incident_context(chunks_with_scores: list, max_chunks: int = 8) -> tuple[str, list[SourceDocument]]:
    """Focus context on the strongest single source file to avoid cross-incident field leakage."""
    if not chunks_with_scores:
        return "No relevant documents found in the database.", []

    grouped = {}
    for doc, score in chunks_with_scores:
        source_path = (doc.metadata or {}).get("source", "Unknown")
        filename = Path(source_path).name if source_path else "Unknown"
        bucket = grouped.setdefault(filename, {"total": 0.0, "items": []})
        bucket["total"] += float(score)
        bucket["items"].append((doc, float(score)))

    dominant_filename, dominant_group = max(
        grouped.items(),
        key=lambda item: (item[1]["total"], len(item[1]["items"])),
    )

    selected_items = sorted(dominant_group["items"], key=lambda pair: pair[1], reverse=True)[:max_chunks]
    context_parts = []
    sources = []
    for doc, score in selected_items:
        clean_text = _clean_chunk_text(doc.page_content)
        if clean_text:
            context_parts.append(clean_text)
        sources.append(SourceDocument(
            filename=dominant_filename,
            snippet=(clean_text[:200] + "...") if clean_text else "",
            relevance_score=float(score),
        ))

    context = "\n\n---\n\n".join(part for part in context_parts if part) or "No relevant documents found in the database."
    return context, sources

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


@app.on_event("startup")
async def warm_up_services() -> None:
    """Initialize heavy dependencies before accepting user requests."""
    try:
        await asyncio.to_thread(retrieval.warm_up_retrieval)
        app.state.ready = True
        app.state.startup_error = ""
        logger.info("Backend warmup completed successfully.")
    except Exception as exc:
        app.state.ready = False
        app.state.startup_error = str(exc)
        logger.exception("Backend warmup failed: %s", exc)


@app.post("/api/query", response_model=QueryResponse)
async def query(request: QueryRequest) -> QueryResponse:
    print("✅ Real query handler called")
    start_time = time.time()
    if not app.state.ready:
        raise HTTPException(status_code=503, detail="Backend warmup in progress. Please retry shortly.")
    try:
        # 1. Retrieve relevant chunks from Chroma
        chunks_with_scores = retrieval.retrieve_relevant_chunks(request.prompt, k=8)
        context, sources = _build_incident_context(chunks_with_scores)

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
    status = "healthy" if app.state.ready else "starting"
    return {
        "status": status,
        "ready": bool(app.state.ready),
        "version": "1.0.0",
        "startup_error": app.state.startup_error,
    }


@app.post("/api/ingest", status_code=202)
async def ingest() -> dict:
    """Trigger OneDrive download and rebuild vector store for future real queries."""
    from .ingestion import download_files_from_onedrive

    download_files_from_onedrive()
    retrieval.create_vector_store()
    return {"message": "Ingestion started. Check logs."}


@app.post("/api/fill-template")
async def fill_template(
    file: UploadFile = File(...),
    prompt: str = Form(...),
):
    if not app.state.ready:
        return JSONResponse(status_code=503, content={"error": "Backend warmup in progress. Please retry shortly."})

    try:
        chunks_with_scores = retrieval.retrieve_relevant_chunks(prompt, k=8)
        if not chunks_with_scores:
            if retrieval.query_has_temporal_constraint(prompt):
                return JSONResponse(
                    status_code=404,
                    content={
                        "error": "No incident matched the requested month/year in your prompt. Verify source files and rebuild the vector index."
                    },
                )
            context = "No relevant documents found."
        else:
            context, _ = _build_incident_context(chunks_with_scores)

        required_fields = [
            "incident_id", "date", "type", "description", "impact", "actions_taken",
            "recommendations",
            "reporter_name", "department", "contact_number", "email", "time", "location", "system",
            "amount_lost", "currency", "evidence_list",
        ]

        filename = file.filename or "template.docx"
        lower_filename = filename.lower()
        template_bytes = await file.read()

        extracted_data = generation.extract_structured_data(prompt, context, required_fields)
        if not isinstance(extracted_data, dict):
            extracted_data = {}
        for field in required_fields:
            extracted_data.setdefault(field, None)

        if not extracted_data.get("recommendations"):
            extracted_data["recommendations"] = generation.synthesize_recommendations(extracted_data)

        for key, value in extracted_data.items():
            if value is None:
                extracted_data[key] = ""

        if lower_filename.endswith(".txt"):
            template_text = template_bytes.decode("utf-8", errors="replace")
            filled = generation.fill_template(template_text, context, prompt)
            if isinstance(filled, str) and filled.startswith("Error:"):
                return Response(content=filled, status_code=502, media_type="text/plain")

            if "{{" in filled or "}}" in filled or "___" in filled:
                logger.warning("Text template still contains unresolved placeholders for %s", filename)

            output_name = f"filled_{Path(filename).stem}.txt"
            return Response(
                content=filled,
                media_type="text/plain; charset=utf-8",
                headers={"Content-Disposition": f'attachment; filename="{output_name}"'},
            )

        if lower_filename.endswith(".docx"):
            filled_docx = template_filler.fill_docx_intelligently(
                original_template_bytes=template_bytes,
                context=context,
                extracted_data=extracted_data,
            )

            if not template_filler.validate_docx(filled_docx):
                logger.error("Generated DOCX failed validation for file: %s", filename)
                return JSONResponse(
                    status_code=500,
                    content={"error": "Generated document is corrupted. Please try again."},
                )

            output_name = f"filled_{Path(filename).name}"
            return Response(
                content=filled_docx,
                media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                headers={"Content-Disposition": f'attachment; filename="{output_name}"'},
            )

        if lower_filename.endswith(".pdf"):
            return JSONResponse(status_code=400, content={"error": "PDF filling not yet supported"})

        return JSONResponse(status_code=400, content={"error": "Unsupported file type"})
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Unexpected /api/fill-template failure: %s", e)
        raise HTTPException(status_code=500, detail="Template filling failed due to an internal error.")