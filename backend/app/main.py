import time
import logging
import asyncio
import re
from uuid import uuid4
from pathlib import Path

from fastapi import FastAPI, HTTPException, File, Form, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse

from . import retrieval, generation, template_filler, database
from .models import QueryRequest, QueryResponse, SourceDocument


app = FastAPI(title="CFIR Generator API")
logger = logging.getLogger(__name__)
app.state.ready = False
app.state.startup_error = ""
GENERATED_ATTACHMENTS_DIR = Path(__file__).resolve().parent.parent / "generated_files"
GENERATED_ATTACHMENTS_DIR.mkdir(parents=True, exist_ok=True)

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


def _build_chat_context(chunks_with_scores: list, max_chunks: int = 10) -> tuple[str, list[SourceDocument], int]:
    """Preserve multiple candidate incidents so assistant can ask clarifying questions."""
    if not chunks_with_scores:
        return "No relevant documents found in the database.", [], 0

    selected_items = sorted(chunks_with_scores, key=lambda pair: float(pair[1]), reverse=True)[:max_chunks]
    context_parts = []
    sources = []
    filenames = set()

    for doc, score in selected_items:
        source_path = (doc.metadata or {}).get("source", "Unknown")
        filename = Path(source_path).name if source_path else "Unknown"
        filenames.add(filename)
        clean_text = _clean_chunk_text(doc.page_content)
        if clean_text:
            context_parts.append(f"[Source: {filename}]\n{clean_text}")
        sources.append(SourceDocument(
            filename=filename,
            snippet=(clean_text[:200] + "...") if clean_text else "",
            relevance_score=float(score),
        ))

    context = "\n\n---\n\n".join(part for part in context_parts if part) or "No relevant documents found in the database."
    return context, sources, len(filenames)

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
        database.init_db()
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
    start_time = time.time()
    if not app.state.ready:
        raise HTTPException(status_code=503, detail="Backend warmup in progress. Please retry shortly.")
    try:
        conv_id = request.conversation_id
        if conv_id is None:
            conv_id = database.create_conversation(title="")
        else:
            existing = database.get_conversation(conv_id)
            if not existing:
                raise HTTPException(status_code=404, detail="Conversation not found")

        database.add_message(conv_id, "user", request.prompt)

        use_incident_rag = retrieval.should_use_incident_rag(request.prompt)
        if use_incident_rag:
            chunks_with_scores = retrieval.retrieve_relevant_chunks(request.prompt, k=10)
            context, sources, incident_candidates = _build_chat_context(chunks_with_scores)
        else:
            chunks_with_scores = []
            context = "No incident retrieval context required for this prompt."
            sources = []
            incident_candidates = 0

        history = database.get_messages(conv_id, limit=20)
        conversation_history = ""
        for msg in history:
            role = "User" if msg["role"] == "user" else "Assistant"
            conversation_history += f"{role}: {msg['content']}\n\n"

        if incident_candidates > 1:
            conversation_history += (
                f"System: Retrieved {incident_candidates} possible incident sources from RAG. "
                "If ambiguity remains, ask the user for date or incident ID.\n\n"
            )

        answer = generation.chat_response(
            conversation_history=conversation_history,
            context=context,
            latest_user_prompt=request.prompt,
            use_incident_context=use_incident_rag,
            model=request.model,
            temperature=request.temperature,
            max_tokens=request.max_tokens,
        )

        database.add_message(conv_id, "assistant", answer)

        conversation_meta = database.get_conversation(conv_id) or {}
        existing_title = str(conversation_meta.get("title", "")).strip()
        if not existing_title:
            latest_history = database.get_messages(conv_id, limit=16)
            title_history = ""
            for msg in latest_history:
                role = "User" if msg["role"] == "user" else "Assistant"
                title_history += f"{role}: {msg['content']}\n"

            generated_title = generation.generate_conversation_title(
                conversation_history=title_history,
                model=request.model,
            )
            if not generated_title:
                fallback = request.prompt.strip().splitlines()[0][:80]
                generated_title = fallback or "Fraud Incident Conversation"
            database.update_conversation_title(conv_id, generated_title)

        processing_time = int((time.time() - start_time) * 1000)
        return QueryResponse(
            answer=answer,
            sources=sources,
            conversation_id=conv_id,
            processing_time_ms=processing_time,
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/conversations", response_model=dict)
async def create_conversation(title: str = "") -> dict:
    conv_id = database.create_conversation(title=title)
    return {"id": conv_id, "title": title}


@app.get("/api/conversations", response_model=list)
async def list_conversations() -> list:
    return database.get_conversations()


@app.get("/api/conversations/{conv_id}", response_model=dict)
async def get_conversation(conv_id: int) -> dict:
    conv = database.get_conversation(conv_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conv


@app.delete("/api/conversations/{conv_id}")
async def delete_conversation(conv_id: int) -> dict:
    deleted = database.delete_conversation(conv_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {"message": "Conversation deleted"}


@app.get("/api/conversations/{conv_id}/messages", response_model=list)
async def get_messages(conv_id: int) -> list:
    conv = database.get_conversation(conv_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return database.get_messages(conv_id)


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
    conversation_id: int | None = Form(None),
):
    if not app.state.ready:
        return JSONResponse(status_code=503, content={"error": "Backend warmup in progress. Please retry shortly."})

    try:
        conv_id = conversation_id
        if conv_id is None:
            conv_id = database.create_conversation(title="")
        else:
            existing = database.get_conversation(conv_id)
            if not existing:
                raise HTTPException(status_code=404, detail="Conversation not found")

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
        user_prompt_for_history = f"Fill template request ({filename}):\n{prompt}"
        database.add_message(conv_id, "user", user_prompt_for_history)

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
                return JSONResponse(status_code=502, content={"error": filled})

            if "{{" in filled or "}}" in filled or "___" in filled:
                logger.warning("Text template still contains unresolved placeholders for %s", filename)

            output_name = f"filled_{Path(filename).stem}.txt"
            output_bytes = filled.encode("utf-8")
            content_type = "text/plain; charset=utf-8"
            assistant_text = "Template completed. Review it and download from the attachment below."

            assistant_message_id = database.add_message(conv_id, "assistant", assistant_text)
            storage_name = f"{uuid4().hex}_{output_name}"
            storage_path = GENERATED_ATTACHMENTS_DIR / storage_name
            storage_path.write_bytes(output_bytes)
            attachment_id = database.add_attachment(
                conv_id=conv_id,
                message_id=assistant_message_id,
                filename=output_name,
                storage_path=str(storage_path),
                content_type=content_type,
            )

            return JSONResponse(
                status_code=200,
                content={
                    "conversation_id": conv_id,
                    "answer": assistant_text,
                    "attachment_id": attachment_id,
                    "attachment_filename": output_name,
                },
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
            assistant_text = "Template completed. Review it and download from the attachment below."
            assistant_message_id = database.add_message(conv_id, "assistant", assistant_text)
            storage_name = f"{uuid4().hex}_{output_name}"
            storage_path = GENERATED_ATTACHMENTS_DIR / storage_name
            storage_path.write_bytes(filled_docx)
            attachment_id = database.add_attachment(
                conv_id=conv_id,
                message_id=assistant_message_id,
                filename=output_name,
                storage_path=str(storage_path),
                content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )

            return JSONResponse(
                status_code=200,
                content={
                    "conversation_id": conv_id,
                    "answer": assistant_text,
                    "attachment_id": attachment_id,
                    "attachment_filename": output_name,
                },
            )

        if lower_filename.endswith(".pdf"):
            return JSONResponse(status_code=400, content={"error": "PDF filling not yet supported"})

        return JSONResponse(status_code=400, content={"error": "Unsupported file type"})
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Unexpected /api/fill-template failure: %s", e)
        raise HTTPException(status_code=500, detail="Template filling failed due to an internal error.")


@app.get("/api/attachments/{attachment_id}/download")
async def download_attachment(attachment_id: int):
    attachment = database.get_attachment(attachment_id)
    if not attachment:
        raise HTTPException(status_code=404, detail="Attachment not found")

    storage_path = Path(str(attachment.get("storage_path", "")))
    if not storage_path.exists() or not storage_path.is_file():
        raise HTTPException(status_code=404, detail="Attachment file is missing on server")

    return FileResponse(
        path=storage_path,
        media_type=str(attachment.get("content_type") or "application/octet-stream"),
        filename=str(attachment.get("filename") or storage_path.name),
    )