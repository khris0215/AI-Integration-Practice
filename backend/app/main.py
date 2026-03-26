import time
import logging
import asyncio
import os
import re
from uuid import uuid4
from pathlib import Path

from fastapi import FastAPI, HTTPException, File, Form, UploadFile, Request, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse

from . import retrieval, generation, template_filler, database, template_registry
from .paths import DATA_PATH
from .models import (
    QueryRequest,
    QueryResponse,
    SourceDocument,
    IntentRouteRequest,
    IntentRouteResponse,
    TemplateMetadataPatch,
)


app = FastAPI(title="CFIR Generator API")
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.DEBUG)
app.state.ready = False
app.state.startup_error = ""
app.state.last_logged_health_ready = None
app.state.request_trace_counter = 0
app.state.ingest_running = False
app.state.ingest_last_error = ""
app.state.ingest_last_result = None
app.state.ingest_started_at = None
app.state.auto_sync_task = None
app.state.auto_sync_running = False
app.state.auto_sync_last_error = ""
app.state.auto_sync_last_result = None
app.state.auto_sync_interval_s = 0
GENERATED_ATTACHMENTS_DIR = Path(__file__).resolve().parent.parent / "generated_files"
GENERATED_ATTACHMENTS_DIR.mkdir(parents=True, exist_ok=True)

FILENAME_HEADER_RE = re.compile(r"(?im)^\s*\[Filename:\s*[^\]]+\]\s*$")


def next_request_trace_id() -> str:
    app.state.request_trace_counter += 1
    return f"srv-{app.state.request_trace_counter}"


def _auto_sync_enabled() -> bool:
    value = str(os.getenv("ONEDRIVE_AUTO_SYNC") or "").strip().lower()
    return value in {"1", "true", "yes", "on"}


def _auto_sync_interval_seconds() -> int:
    raw = str(os.getenv("ONEDRIVE_AUTO_SYNC_INTERVAL_SECONDS") or "120").strip()
    try:
        return max(int(raw), 30)
    except Exception:
        return 120


def _perform_ingest(mode: str, interactive_onedrive: bool = True) -> dict:
    from .ingestion import download_files_from_onedrive, get_local_data_snapshot

    logger.debug("ingest worker start mode=%s data_path=%s", mode, DATA_PATH)
    download_summary = None

    if mode == "onedrive":
        download_summary = download_files_from_onedrive(interactive=interactive_onedrive)

    retrieval.create_vector_store()
    snapshot = get_local_data_snapshot()
    result = {
        "message": "Ingestion complete.",
        "mode": mode,
        "data_path": str(DATA_PATH),
        "download": download_summary,
        "snapshot": snapshot,
    }
    logger.debug("ingest worker complete mode=%s snapshot=%s", mode, snapshot)
    return result


async def _ingest_worker(mode: str) -> None:
    app.state.ingest_running = True
    app.state.ingest_last_error = ""
    app.state.ingest_last_result = None
    app.state.ingest_started_at = int(time.time())
    try:
        app.state.ingest_last_result = await asyncio.to_thread(_perform_ingest, mode)
    except Exception as exc:  # pragma: no cover - defensive runtime guard
        app.state.ingest_last_error = str(exc)
        logger.exception("ingest worker failed mode=%s", mode)
    finally:
        app.state.ingest_running = False


async def _onedrive_auto_sync_worker() -> None:
    app.state.auto_sync_running = True
    app.state.auto_sync_last_error = ""
    interval_s = _auto_sync_interval_seconds()
    app.state.auto_sync_interval_s = interval_s
    logger.info("OneDrive auto-sync enabled (interval=%ss)", interval_s)

    try:
        while True:
            try:
                if app.state.ingest_running:
                    logger.info("Auto-sync skipped because another ingestion is running.")
                else:
                    result = await asyncio.to_thread(_perform_ingest, "onedrive", False)
                    app.state.auto_sync_last_result = result
                    app.state.auto_sync_last_error = ""
                    logger.info("Auto-sync cycle complete: %s", result.get("download"))
            except Exception as exc:
                app.state.auto_sync_last_error = str(exc)
                logger.warning("Auto-sync cycle failed: %s", exc)

            await asyncio.sleep(interval_s)
    except asyncio.CancelledError:
        logger.info("OneDrive auto-sync worker stopped.")
        raise
    finally:
        app.state.auto_sync_running = False


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


def detect_requested_output_format(prompt: str) -> str:
    """Default to editable DOCX unless user explicitly requests PDF."""
    normalized = re.sub(r"\s+", " ", str(prompt or "").lower()).strip()
    pdf_patterns = [
        r"\bpdf\b",
        r"\bas\s+pdf\b",
        r"\bin\s+pdf\b",
        r"\bexport\s+to\s+pdf\b",
        r"\bconvert\s+(it|this|file|document)\s+to\s+pdf\b",
    ]
    if any(re.search(pattern, normalized) for pattern in pdf_patterns):
        return "pdf"
    return "docx"


def _is_detail_followup_prompt(prompt: str) -> bool:
    text = str(prompt or "").strip().lower()
    if not text:
        return False
    return bool(re.search(r"\b(full|complete|details|detailed|more info|expand|everything)\b", text))


def _find_recent_incident_anchor(messages: list[dict], latest_prompt: str) -> str:
    latest_normalized = str(latest_prompt or "").strip().lower()
    skipped_current = False

    for msg in reversed(messages or []):
        if str(msg.get("role", "")) != "user":
            continue

        content = str(msg.get("content") or "").strip()
        if not content:
            continue

        if not skipped_current and content.lower() == latest_normalized:
            skipped_current = True
            continue

        if retrieval.should_use_incident_rag(content):
            return content

    return ""


def _wants_full_incident_details(prompt: str) -> bool:
    text = str(prompt or "").strip().lower()
    if not text:
        return False
    detail_terms = bool(re.search(r"\b(full|complete|all|entire|detailed|details)\b", text))
    info_terms = bool(re.search(r"\b(info|information|incident|case|report)\b", text))
    return detail_terms and info_terms

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
    expose_headers=["X-Trace-Id"],
)


@app.middleware("http")
async def log_request_flow(request: Request, call_next):
    trace_id = next_request_trace_id()
    start = time.perf_counter()
    logger.debug(
        "[REQ %s] start method=%s path=%s query=%s ready=%s",
        trace_id,
        request.method,
        request.url.path,
        str(request.url.query),
        app.state.ready,
    )

    try:
        response = await call_next(request)
    except Exception as exc:
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        logger.exception("[REQ %s] crash after %sms: %s", trace_id, elapsed_ms, exc)
        raise

    elapsed_ms = int((time.perf_counter() - start) * 1000)
    response.headers["X-Trace-Id"] = trace_id
    logger.debug(
        "[REQ %s] end status=%s duration_ms=%s ready=%s",
        trace_id,
        response.status_code,
        elapsed_ms,
        app.state.ready,
    )
    return response


@app.on_event("startup")
async def warm_up_services() -> None:
    """Initialize heavy dependencies before accepting user requests."""
    try:
        logger.debug("startup warmup begin")
        database.init_db()
        template_registry.ensure_registry_initialized()
        await asyncio.to_thread(retrieval.warm_up_retrieval)
        app.state.ready = True
        app.state.startup_error = ""
        logger.info("Backend warmup completed successfully.")

        if _auto_sync_enabled():
            app.state.auto_sync_task = asyncio.create_task(_onedrive_auto_sync_worker())
    except Exception as exc:
        app.state.ready = False
        app.state.startup_error = str(exc)
        logger.exception("Backend warmup failed: %s", exc)


@app.on_event("shutdown")
async def stop_background_workers() -> None:
    task = app.state.auto_sync_task
    if task is not None:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        app.state.auto_sync_task = None


@app.post("/api/query", response_model=QueryResponse)
async def query(request: QueryRequest) -> QueryResponse:
    request_start = time.time()
    logger.debug(
        "query start conversation_id=%s prompt_len=%s model=%s temp=%s max_tokens=%s",
        request.conversation_id,
        len(request.prompt or ""),
        request.model,
        request.temperature,
        request.max_tokens,
    )
    if not app.state.ready:
        logger.debug("query rejected because backend not ready")
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
        logger.debug("query[%s] persisted user message len=%s", conv_id, len(request.prompt or ""))

        retrieval_start = time.time()
        use_incident_rag = retrieval.should_use_incident_rag(request.prompt)
        retrieval_query = request.prompt

        if not use_incident_rag and _is_detail_followup_prompt(request.prompt):
            recent_messages = database.get_messages(conv_id, limit=8)
            anchor = _find_recent_incident_anchor(recent_messages, request.prompt)
            if anchor:
                use_incident_rag = True
                retrieval_query = f"{anchor}\nFollow-up request: {request.prompt}"
                logger.info("query[%s] using incident anchor for detail follow-up", conv_id)

        if use_incident_rag:
            chunks_with_scores = retrieval.retrieve_relevant_chunks(retrieval_query, k=10)
            context, sources, incident_candidates = _build_chat_context(chunks_with_scores)
        else:
            chunks_with_scores = []
            context = "No incident retrieval context required for this prompt."
            sources = []
            incident_candidates = 0
        logger.info(
            "query[%s] retrieval took %.2fs (rag=%s, chunks=%s)",
            conv_id,
            time.time() - retrieval_start,
            use_incident_rag,
            len(chunks_with_scores),
        )

        if use_incident_rag and not chunks_with_scores:
            if retrieval.query_has_temporal_constraint(request.prompt):
                answer = (
                    "I could not find a matching incident for the requested date in indexed files. "
                    "Please verify the file exists under the configured DATA_PATH and retry after index rebuild."
                )
            else:
                answer = (
                    "I could not find supporting incident records in the indexed files for that request, "
                    "so I cannot provide incident details without a source document."
                )

            database.add_message(conv_id, "assistant", answer)
            processing_time = int((time.time() - request_start) * 1000)
            logger.info("query[%s] blocked response without retrieval evidence", conv_id)
            return QueryResponse(
                answer=answer,
                sources=[],
                conversation_id=conv_id,
                processing_time_ms=processing_time,
            )

        history_start = time.time()
        history = database.get_messages(conv_id, limit=12)
        conversation_history = ""
        for msg in history:
            role = "User" if msg["role"] == "user" else "Assistant"
            conversation_history += f"{role}: {msg['content']}\n\n"

        if len(conversation_history) > 6000:
            conversation_history = conversation_history[-6000:]

        context_for_generation = context
        if len(context_for_generation) > 7000:
            context_for_generation = context_for_generation[:7000] + "\n\n[Context truncated for latency.]"

        if incident_candidates > 1:
            conversation_history += (
                f"System: Retrieved {incident_candidates} possible incident sources from RAG. "
                "If ambiguity remains, ask the user for date or incident ID.\n\n"
            )
        logger.info("query[%s] history build took %.2fs", conv_id, time.time() - history_start)

        generation_start = time.time()
        if use_incident_rag and _wants_full_incident_details(request.prompt):
            answer = generation.build_incident_details_response(context_for_generation)
        else:
            answer = generation.chat_response(
                conversation_history=conversation_history,
                context=context_for_generation,
                latest_user_prompt=request.prompt,
                use_incident_context=use_incident_rag,
                model=request.model,
                temperature=request.temperature,
                max_tokens=request.max_tokens,
            )
        logger.info("query[%s] generation took %.2fs", conv_id, time.time() - generation_start)

        database.add_message(conv_id, "assistant", answer)
        logger.debug("query[%s] persisted assistant message len=%s", conv_id, len(answer or ""))

        conversation_meta = database.get_conversation(conv_id) or {}
        existing_title = str(conversation_meta.get("title", "")).strip()
        if not existing_title:
            title_start = time.time()
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
            logger.info("query[%s] title generation took %.2fs", conv_id, time.time() - title_start)

        processing_time = int((time.time() - request_start) * 1000)
        logger.info("query[%s] total processing took %.2fs", conv_id, time.time() - request_start)
        logger.debug(
            "query[%s] returning answer_len=%s source_count=%s processing_ms=%s",
            conv_id,
            len(answer or ""),
            len(sources),
            processing_time,
        )
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
    logger.debug("create_conversation title_len=%s", len(title or ""))
    conv_id = database.create_conversation(title=title)
    logger.debug("create_conversation created id=%s", conv_id)
    return {"id": conv_id, "title": title}


@app.get("/api/conversations", response_model=list)
async def list_conversations() -> list:
    rows = database.get_conversations()
    logger.debug("list_conversations returned count=%s", len(rows))
    return rows


@app.get("/api/conversations/{conv_id}", response_model=dict)
async def get_conversation(conv_id: int) -> dict:
    logger.debug("get_conversation id=%s", conv_id)
    conv = database.get_conversation(conv_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conv


@app.delete("/api/conversations/{conv_id}")
async def delete_conversation(conv_id: int) -> dict:
    logger.debug("delete_conversation id=%s", conv_id)
    deleted = database.delete_conversation(conv_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {"message": "Conversation deleted"}


@app.get("/api/conversations/{conv_id}/messages", response_model=list)
async def get_messages(conv_id: int) -> list:
    logger.debug("get_messages conv_id=%s", conv_id)
    conv = database.get_conversation(conv_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    rows = database.get_messages(conv_id)
    logger.debug("get_messages conv_id=%s count=%s", conv_id, len(rows))
    return rows


@app.get("/api/health")
async def health() -> dict:
    status = "healthy" if app.state.ready else "starting"
    ready = bool(app.state.ready)
    if app.state.last_logged_health_ready is None:
        logger.info("health initialized: ready=%s status=%s", ready, status)
        app.state.last_logged_health_ready = ready
    elif app.state.last_logged_health_ready != ready:
        logger.warning(
            "health readiness changed: ready=%s status=%s startup_error=%s",
            ready,
            status,
            app.state.startup_error,
        )
        app.state.last_logged_health_ready = ready

    logger.debug("health check response status=%s ready=%s", status, ready)
    return {
        "status": status,
        "ready": ready,
        "version": "1.0.0",
        "startup_error": app.state.startup_error,
    }


@app.get("/api/templates")
async def list_templates(active_only: bool = False):
    rows = template_registry.list_templates(active_only=active_only)
    logger.debug("list_templates returned count=%s active_only=%s", len(rows), active_only)
    return rows


@app.post("/api/templates/upload")
async def upload_template(
    file: UploadFile = File(...),
    name: str = Form(""),
    template_type: str = Form("custom"),
    owner_team: str = Form("Fraud Ops"),
    fields: str = Form(""),
    active: bool = Form(True),
    is_default: bool = Form(False),
):
    filename = file.filename or "template.txt"
    if not filename.lower().endswith((".txt", ".docx")):
        return JSONResponse(status_code=400, content={"error": "Only .txt and .docx template files are supported."})

    payload = await file.read()
    item = template_registry.create_template(
        name=name,
        template_type=template_type,
        owner_team=owner_team,
        fields=fields,
        active=active,
        is_default=is_default,
        filename=filename,
        content_bytes=payload,
        source="user",
    )
    logger.debug("upload_template created id=%s filename=%s", item.get("id"), filename)
    return item


@app.patch("/api/templates/{template_id}")
async def patch_template(template_id: str, patch: TemplateMetadataPatch):
    updated = template_registry.update_template(template_id, patch.dict(exclude_none=True))
    if not updated:
        raise HTTPException(status_code=404, detail="Template not found")
    return updated


@app.post("/api/classify-intent", response_model=IntentRouteResponse)
async def classify_intent(request: IntentRouteRequest) -> IntentRouteResponse:
    result = generation.classify_user_intent(
        request.prompt,
        has_selected_template=bool(request.has_selected_template),
        has_uploaded_template=bool(request.has_uploaded_template),
    )
    logger.debug(
        "classify_intent intent=%s confidence=%.2f reason=%s",
        result.get("intent"),
        float(result.get("confidence", 0.0)),
        result.get("reason"),
    )
    return IntentRouteResponse(**result)


@app.post("/api/ingest", status_code=202)
async def ingest(mode: str = Query("local", pattern="^(local|onedrive)$")) -> dict:
    """Start background indexing from local OneDrive mirror or Graph pull."""
    if app.state.ingest_running:
        return {
            "message": "Ingestion is already running.",
            "running": True,
            "started_at": app.state.ingest_started_at,
        }

    logger.debug("ingest queued mode=%s data_path=%s", mode, DATA_PATH)
    asyncio.create_task(_ingest_worker(mode))
    return {
        "message": "Ingestion started.",
        "mode": mode,
        "running": True,
        "started_at": int(time.time()),
        "data_path": str(DATA_PATH),
    }


@app.get("/api/ingest/status")
async def ingest_status() -> dict:
    return {
        "running": bool(app.state.ingest_running),
        "started_at": app.state.ingest_started_at,
        "last_error": app.state.ingest_last_error,
        "last_result": app.state.ingest_last_result,
        "data_path": str(DATA_PATH),
        "auto_sync": {
            "enabled": _auto_sync_enabled(),
            "running": bool(app.state.auto_sync_running),
            "interval_seconds": app.state.auto_sync_interval_s,
            "last_error": app.state.auto_sync_last_error,
            "last_result": app.state.auto_sync_last_result,
        },
    }


@app.post("/api/fill-template")
async def fill_template(
    file: UploadFile | None = File(None),
    prompt: str = Form(...),
    conversation_id: int | None = Form(None),
    template_id: str | None = Form(None),
):
    incoming_filename = file.filename if file is not None else None
    logger.debug(
        "fill_template start filename=%s template_id=%s prompt_len=%s conversation_id=%s",
        incoming_filename,
        template_id,
        len(prompt or ""),
        conversation_id,
    )
    if not app.state.ready:
        logger.debug("fill_template rejected because backend not ready")
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

        template_bytes = b""
        filename = ""
        lower_filename = ""

        selected_template = None
        if template_id:
            selected_template = template_registry.get_template(template_id)
            if not selected_template:
                return JSONResponse(status_code=404, content={"error": "Selected template does not exist."})
            if not bool(selected_template.get("active", True)):
                return JSONResponse(status_code=400, content={"error": "Selected template is inactive."})

        if file is not None:
            filename = file.filename or "template.docx"
            template_bytes = await file.read()
        elif selected_template is not None:
            filename = str(selected_template.get("filename") or "template.txt")
            storage_path = Path(str(selected_template.get("storage_path") or ""))
            if not storage_path.exists() or not storage_path.is_file():
                return JSONResponse(status_code=404, content={"error": "Stored template file is missing."})
            template_bytes = storage_path.read_bytes()
        else:
            fallback = template_registry.get_default_template()
            if fallback is None:
                return JSONResponse(status_code=400, content={"error": "No template selected. Choose a template or upload one."})
            filename = str(fallback.get("filename") or "template.txt")
            storage_path = Path(str(fallback.get("storage_path") or ""))
            if not storage_path.exists() or not storage_path.is_file():
                return JSONResponse(status_code=404, content={"error": "Default template file is missing."})
            template_bytes = storage_path.read_bytes()

        lower_filename = filename.lower()
        user_prompt_for_history = f"Fill template request ({filename}):\n{prompt}"
        database.add_message(conv_id, "user", user_prompt_for_history)
        logger.debug("fill_template[%s] persisted user template request", conv_id)

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

        requested_output_format = detect_requested_output_format(prompt)

        if lower_filename.endswith(".txt"):
            template_text = template_bytes.decode("utf-8", errors="replace")
            filled = generation.fill_template(template_text, context, prompt)
            if isinstance(filled, str) and filled.startswith("Error:"):
                return JSONResponse(status_code=502, content={"error": filled})

            if "{{" in filled or "}}" in filled or "___" in filled:
                logger.warning("Text template still contains unresolved placeholders for %s", filename)

            if requested_output_format == "pdf":
                output_name = f"filled_{Path(filename).stem}.pdf"
                try:
                    output_bytes = template_filler.text_to_pdf_bytes(filled, title=output_name)
                except RuntimeError as exc:
                    return JSONResponse(status_code=500, content={"error": str(exc)})
                content_type = "application/pdf"
            else:
                output_name = f"filled_{Path(filename).stem}.docx"
                output_bytes = template_filler.text_to_docx_bytes(filled)
                content_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
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

            logger.debug(
                "fill_template[%s] txt-template output=%s bytes=%s attachment_id=%s",
                conv_id,
                requested_output_format,
                len(output_bytes),
                attachment_id,
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

            if requested_output_format == "pdf":
                docx_text = template_filler.docx_bytes_to_plain_text(filled_docx)
                try:
                    output_bytes = template_filler.text_to_pdf_bytes(docx_text, title=f"filled_{Path(filename).stem}.pdf")
                except RuntimeError as exc:
                    return JSONResponse(status_code=500, content={"error": str(exc)})
                output_name = f"filled_{Path(filename).stem}.pdf"
                content_type = "application/pdf"
            else:
                output_bytes = filled_docx
                output_name = f"filled_{Path(filename).stem}.docx"
                content_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

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

            logger.debug(
                "fill_template[%s] docx-template output=%s bytes=%s attachment_id=%s",
                conv_id,
                requested_output_format,
                len(output_bytes),
                attachment_id,
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
            return JSONResponse(status_code=400, content={"error": "PDF templates are not supported yet. Upload .docx or .txt templates."})

        return JSONResponse(status_code=400, content={"error": "Unsupported file type"})
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Unexpected /api/fill-template failure: %s", e)
        raise HTTPException(status_code=500, detail="Template filling failed due to an internal error.")


@app.get("/api/attachments/{attachment_id}/download")
async def download_attachment(attachment_id: int):
    logger.debug("download_attachment id=%s", attachment_id)
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