from pathlib import Path
import logging
import re
import shutil
import importlib
from langchain_core.documents import Document

from langchain_community.document_loaders import DirectoryLoader, UnstructuredFileLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter

try:
    HuggingFaceEmbeddings = importlib.import_module("langchain_huggingface").HuggingFaceEmbeddings
except Exception:  # pragma: no cover - compatibility fallback
    HuggingFaceEmbeddings = importlib.import_module("langchain_community.embeddings").HuggingFaceEmbeddings

try:
    Chroma = importlib.import_module("langchain_chroma").Chroma
except Exception:  # pragma: no cover - compatibility fallback
    Chroma = importlib.import_module("langchain_community.vectorstores").Chroma

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CHROMA_PATH = str(PROJECT_ROOT / "chroma_db")
EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
DATA_PATH = PROJECT_ROOT / "file_dump"
logger = logging.getLogger(__name__)
_EMBEDDINGS = None
_VECTOR_STORE = None

MONTHS = {
    "january", "february", "march", "april", "may", "june",
    "july", "august", "september", "october", "november", "december",
}

MONTH_TO_NUM = {
    "january": "01",
    "february": "02",
    "march": "03",
    "april": "04",
    "may": "05",
    "june": "06",
    "july": "07",
    "august": "08",
    "september": "09",
    "october": "10",
    "november": "11",
    "december": "12",
}

FRAUD_TERMS = {
    "phishing": ["phishing"],
    "ransomware": ["ransomware", "malware"],
    "business email compromise": ["business email compromise", "bec"],
    "unauthorized transfer": ["unauthorized transfer", "wire transfer", "fund transfer"],
    "identity theft": ["identity theft"],
    "insider threat": ["insider threat", "insider"],
}

def get_embeddings():
    global _EMBEDDINGS
    if _EMBEDDINGS is None:
        _EMBEDDINGS = HuggingFaceEmbeddings(model_name=EMBEDDING_MODEL)
    return _EMBEDDINGS


def warm_up_retrieval() -> None:
    """Preload embeddings + vector store so first user prompt does not pay cold-start cost."""
    chroma_sqlite = Path(CHROMA_PATH) / "chroma.sqlite3"
    if not chroma_sqlite.exists():
        logger.warning("Vector store not found at %s. Building a fresh index.", CHROMA_PATH)
        create_vector_store()
    store = get_vector_store()
    # Force collection initialization once at startup.
    if hasattr(store, "_collection"):
        _ = store._collection.count()

def create_vector_store():
    global _VECTOR_STORE
    # Rebuild from scratch to prevent stale chunks from deleted/renamed files.
    if Path(CHROMA_PATH).exists():
        shutil.rmtree(CHROMA_PATH, ignore_errors=True)

    loader = DirectoryLoader(str(DATA_PATH), glob="**/*", loader_cls=UnstructuredFileLoader, show_progress=True)
    docs = loader.load()
    # Larger chunks reduce the chance an incident narrative is split across vectors.
    text_splitter = RecursiveCharacterTextSplitter(chunk_size=1500, chunk_overlap=200)
    chunks = text_splitter.split_documents(docs)
    for chunk in chunks:
        source_path = chunk.metadata.get("source")
        if source_path:
            filename = Path(source_path).name
            chunk.page_content = f"[Filename: {filename}]\n{chunk.page_content}"
    embeddings = get_embeddings()
    vectorstore = Chroma.from_documents(chunks, embeddings, persist_directory=CHROMA_PATH)
    if hasattr(vectorstore, "persist"):
        vectorstore.persist()
    _VECTOR_STORE = vectorstore
    print(f"Stored {len(chunks)} chunks.")
    return vectorstore

def get_vector_store():
    global _VECTOR_STORE
    if _VECTOR_STORE is None:
        embeddings = get_embeddings()
        _VECTOR_STORE = Chroma(persist_directory=CHROMA_PATH, embedding_function=embeddings)
    return _VECTOR_STORE


def _query_tokens(query: str) -> set:
    tokens = re.findall(r"[a-zA-Z0-9]+", (query or "").lower())
    return {t for t in tokens if len(t) > 2}


def _temporal_tokens(query: str) -> set:
    tokens = _query_tokens(query)
    years = {t for t in tokens if re.fullmatch(r"20\d{2}", t)}
    months = {t for t in tokens if t in MONTHS}
    return years.union(months)


def _fraud_keywords(query: str) -> set:
    text = (query or "").lower()
    detected = set()
    for canonical, variants in FRAUD_TERMS.items():
        if any(variant in text for variant in variants):
            detected.add(canonical)
    return detected


def _extract_constraints(query: str) -> dict:
    temporal = _temporal_tokens(query)
    years = {token for token in temporal if re.fullmatch(r"20\d{2}", token)}
    months = {token for token in temporal if token in MONTHS}
    fraud = _fraud_keywords(query)
    return {
        "years": years,
        "months": months,
        "fraud": fraud,
    }


def _has_temporal_constraint(constraints: dict) -> bool:
    return bool(constraints.get("years") or constraints.get("months"))


def query_has_temporal_constraint(query: str) -> bool:
    return _has_temporal_constraint(_extract_constraints(query))


def _is_live_source(doc) -> bool:
    source = (doc.metadata or {}).get("source")
    if not source:
        return False
    try:
        source_path = Path(source)
        if not source_path.is_absolute():
            source_path = (Path.cwd() / source_path).resolve()
        else:
            source_path = source_path.resolve()

        if not source_path.exists():
            # Some vector metadata keeps only basename; try resolving under DATA_PATH.
            source_path = (DATA_PATH / Path(source).name).resolve()
            if not source_path.exists():
                return False

        data_root = DATA_PATH.resolve()
        return source_path == data_root or data_root in source_path.parents
    except Exception:
        return False


def _build_file_scan_candidates(query: str, k: int) -> list:
    constraints = _extract_constraints(query)
    q_tokens = _query_tokens(query)
    candidates = []

    for file_path in DATA_PATH.glob("*"):
        if not file_path.is_file() or file_path.suffix.lower() not in {".txt"}:
            continue

        try:
            text = file_path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue

        text_lower = text.lower()
        temporal_ok = _doc_matches_temporal(text_lower, constraints)
        fraud_ok = _doc_matches_fraud(text_lower, constraints)

        if _has_temporal_constraint(constraints) and not temporal_ok:
            continue

        if constraints.get("fraud") and not fraud_ok:
            continue

        lexical_hits = sum(1 for token in q_tokens if token in text_lower)
        lexical_ratio = lexical_hits / max(len(q_tokens), 1) if q_tokens else 0.0
        temporal_bonus = 1.0 if temporal_ok and _has_temporal_constraint(constraints) else 0.0
        fraud_bonus = 0.5 if fraud_ok and constraints.get("fraud") else 0.0
        score = lexical_ratio + temporal_bonus + fraud_bonus

        doc = Document(page_content=f"[Filename: {file_path.name}]\n{text}", metadata={"source": str(file_path.resolve())})
        candidates.append((doc, float(score)))

    candidates.sort(key=lambda item: item[1], reverse=True)
    return candidates[:k]


def _matches_month_with_optional_year(text: str, month_name: str, years: set) -> bool:
    month_name = (month_name or "").lower()
    if not month_name:
        return True

    if month_name in text:
        if not years:
            return True
        return any(year in text for year in years)

    month_num = MONTH_TO_NUM.get(month_name)
    if not month_num:
        return False

    # Match common numeric date forms: YYYY-MM, YYYY/MM, MM-YYYY, MM/YYYY, and incident IDs like XXX-YYYY-MM-XX
    if years:
        for year in years:
            patterns = [
                rf"{re.escape(year)}[-/]({month_num})(?:[-/]|\b)",
                rf"(^|\b)({month_num})[-/]{re.escape(year)}(\b|$)",
            ]
            if any(re.search(pattern, text) for pattern in patterns):
                return True
        return False

    return bool(re.search(rf"(^|\b){month_num}[-/](20\d{{2}})(\b|$)", text))


def _doc_matches_temporal(text: str, constraints: dict) -> bool:
    years = constraints.get("years", set())
    months = constraints.get("months", set())

    if not years and not months:
        return True
    if years and not all(year in text for year in years):
        return False
    if months:
        for month in months:
            if not _matches_month_with_optional_year(text, month, years):
                return False
    return True


def _doc_matches_fraud(text: str, constraints: dict) -> bool:
    fraud = constraints.get("fraud", set())
    if not fraud:
        return True

    for canonical in fraud:
        variants = FRAUD_TERMS.get(canonical, [canonical])
        if any(variant in text for variant in variants):
            return True
    return False


def _score_chunk(query: str, doc_text: str, base_score: float) -> float:
    text = (doc_text or "").lower()
    q_tokens = _query_tokens(query)
    constraints = _extract_constraints(query)
    t_tokens = constraints["years"].union(constraints["months"])
    fraud_terms = constraints["fraud"]

    if not q_tokens:
        return float(base_score)

    lexical_hits = sum(1 for token in q_tokens if token in text)
    lexical_ratio = lexical_hits / max(len(q_tokens), 1)

    temporal_hits = sum(1 for token in t_tokens if token in text)
    if constraints["months"]:
        month_hits = sum(
            1 for month in constraints["months"] if _matches_month_with_optional_year(text, month, constraints["years"])
        )
    else:
        month_hits = 0

    year_hits = sum(1 for year in constraints["years"] if year in text)
    temporal_hit_units = temporal_hits + month_hits + year_hits
    temporal_unit_count = max(len(t_tokens) + len(constraints["months"]) + len(constraints["years"]), 1)
    temporal_ratio = temporal_hit_units / temporal_unit_count if t_tokens or constraints["months"] or constraints["years"] else 0.0

    fraud_hits = 0
    if fraud_terms:
        for canonical in fraud_terms:
            variants = FRAUD_TERMS.get(canonical, [canonical])
            if any(variant in text for variant in variants):
                fraud_hits += 1
    fraud_ratio = fraud_hits / max(len(fraud_terms), 1) if fraud_terms else 0.0

    # Blend semantic score with exact-term matching, strongly preferring temporal matches.
    return float(base_score) + (0.20 * lexical_ratio) + (0.55 * temporal_ratio) + (0.25 * fraud_ratio)

def retrieve_relevant_chunks(query, k=10):
    db = get_vector_store()
    constraints = _extract_constraints(query)

    # Pull many candidates so the reranker can find date/type matches reliably.
    candidate_count = max(k * 5, 24)
    raw_results = db.similarity_search_with_relevance_scores(query, k=candidate_count)

    # Remove stale vectors that point to non-existent source files.
    live_results = [(doc, score) for doc, score in raw_results if _is_live_source(doc)]
    if raw_results and not live_results:
        logger.warning("All retrieved chunks were stale. Rebuilding vector store and retrying query=%r", query)
        create_vector_store()
        db = get_vector_store()
        raw_results = db.similarity_search_with_relevance_scores(query, k=candidate_count)
        live_results = [(doc, score) for doc, score in raw_results if _is_live_source(doc)]

    raw_results = live_results

    reranked = []
    for doc, score in raw_results:
        combined_score = _score_chunk(query, doc.page_content, score)
        text = (doc.page_content or "").lower()
        temporal_ok = _doc_matches_temporal(text, constraints)
        fraud_ok = _doc_matches_fraud(text, constraints)
        reranked.append((doc, float(combined_score), temporal_ok, fraud_ok))

    strict_matches = [item for item in reranked if item[2] and item[3]]
    temporal_only_matches = [item for item in reranked if item[2]]

    if strict_matches:
        working_set = strict_matches
    elif temporal_only_matches:
        working_set = temporal_only_matches
    elif _has_temporal_constraint(constraints):
        # Do not return unrelated dates when user asked for specific month/year.
        # Try direct file scan first to recover from stale/insufficient vector index.
        fallback = _build_file_scan_candidates(query, k)
        if fallback:
            logger.info("Vector retrieval found no temporal match; file-scan fallback returned %s chunks", len(fallback))
            return fallback
        logger.info("No temporal match found for query=%r; returning no chunks", query)
        return []
    else:
        working_set = reranked

    working_set.sort(key=lambda item: item[1], reverse=True)
    selected = [(doc, score) for doc, score, _, _ in working_set[:k]]

    logger.info(
        "retrieve_relevant_chunks query=%r candidates=%s strict=%s temporal=%s selected=%s",
        query,
        len(raw_results),
        len(strict_matches),
        len(temporal_only_matches),
        len(selected),
    )
    return selected