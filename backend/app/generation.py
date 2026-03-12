import json
import logging
import re

import requests

OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL = "phi4:14b-q4_K_M"
OLLAMA_CONNECT_TIMEOUT_S = 10
OLLAMA_READ_TIMEOUT_S = 300
logger = logging.getLogger(__name__)

DEFAULT_EXTRACTION_FIELDS = [
    "incident_id",
    "date",
    "type",
    "description",
    "impact",
    "actions_taken",
    "recommendations",
    "reporter_name",
    "department",
    "contact_number",
    "email",
    "time",
    "location",
    "system",
    "amount_lost",
    "currency",
    "evidence_list",
]


def _is_missing_value(value) -> bool:
    if value is None:
        return True
    if isinstance(value, str) and not value.strip():
        return True
    if isinstance(value, (list, dict)) and len(value) == 0:
        return True
    return False


def _extract_from_context(context: str, fields: list) -> dict:
    alias_map = {
        "incident_id": ["incident id", "incident number"],
        "date": ["date", "date of incident"],
        "time": ["time", "time of incident", "incident time"],
        "location": ["location", "location affected"],
        "system": ["system", "system affected", "location/system affected"],
        "type": ["type", "incident type", "fraud type"],
        "description": ["description", "incident description", "full narrative", "narrative"],
        "impact": ["impact", "business impact", "losses", "damage"],
        "actions_taken": ["actions taken", "action taken", "steps taken", "mitigation"],
        "recommendations": ["recommended next actions", "next actions", "recommended actions", "recommendations"],
        "reporter_name": ["reporter name", "reported by", "name"],
        "department": ["department"],
        "contact_number": ["contact number", "phone", "mobile", "contact"],
        "email": ["email", "email address"],
        "amount_lost": ["amount lost", "loss amount"],
        "currency": ["currency"],
        "evidence_list": ["evidence", "evidence list", "attachments"],
    }

    def _extract_value_for_labels(text: str, labels: list) -> str | None:
        for label in labels:
            single_line = rf"(?im)^\s*{re.escape(label)}\s*:\s*(.+?)\s*$"
            match = re.search(single_line, text)
            if match:
                return match.group(1).strip()

        for label in labels:
            multiline = rf"(?is)(?:^|\n)\s*{re.escape(label)}\s*:\s*(.+?)(?=\n\s*[A-Za-z][A-Za-z0-9\s/()\-]{1,50}\s*:\s|\n\s*\d+\.\s|\Z)"
            match = re.search(multiline, text)
            if match:
                value = match.group(1).strip()
                if value:
                    return value
        return None

    extracted = {}
    for field in fields:
        base_label = field.replace("_", " ")
        variants = [base_label, base_label.title()]
        variants.extend(alias_map.get(field, []))
        extracted[field] = _extract_value_for_labels(context, variants)
    return extracted


def _ollama_post(payload: dict, read_timeout: int = OLLAMA_READ_TIMEOUT_S):
    return requests.post(
        OLLAMA_URL,
        json=payload,
        timeout=(OLLAMA_CONNECT_TIMEOUT_S, read_timeout),
    )


def infer_value_from_context(label: str, context: str, model: str = MODEL) -> str:
    """
    Use the LLM to infer a value for a template label from provided context.
    Returns an empty string when no value can be found.
    """
    normalized_label = re.sub(r"[^a-zA-Z0-9\s]", " ", (label or "").lower())
    normalized_label = re.sub(r"\s+", " ", normalized_label).strip()

    alias_map = {
        "name": ["name", "reporter name", "reported by"],
        "reporter name": ["reporter name", "reported by", "name"],
        "department": ["department"],
        "contact number": ["contact number", "phone", "mobile"],
        "email": ["email", "email address"],
        "date": ["date", "date of incident"],
        "time": ["time", "time of incident", "incident time"],
        "location": ["location", "location affected"],
        "system": ["system", "system affected", "location/system affected"],
        "incident id": ["incident id", "incident number"],
        "type": ["type", "fraud type", "incident type"],
        "description": ["description", "incident description", "full narrative"],
        "impact": ["impact"],
        "actions taken": ["actions taken", "action taken", "steps taken"],
        "recommended next actions": ["recommended next actions", "recommendations", "next actions"],
    }

    regex_labels = alias_map.get(normalized_label, [normalized_label] if normalized_label else [])
    for alias in regex_labels:
        if not alias:
            continue
        pattern = rf"(?im)^\s*{re.escape(alias)}\s*:\s*(.+?)\s*$"
        match = re.search(pattern, context or "")
        if match:
            candidate = match.group(1).strip()
            if candidate:
                return candidate

    prompt = f"""You are an AI assistant that extracts information from documents.
Given the following context, what is the value for \"{label}\"?
If the context does not contain relevant information, return an empty string.
Do not add explanations. Return only the value.

Context:
{context}

Value for \"{label}\":
"""

    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": 0.0,
            "max_tokens": 200,
        },
    }

    try:
        response = _ollama_post(payload)
        response.raise_for_status()
        raw = str(response.json().get("response", "")).strip()
        if not raw:
            return ""

        # Remove common wrapper noise from small-model outputs.
        raw = raw.replace("```markdown", "").replace("```", "").strip()

        for line in raw.splitlines():
            cleaned = line.strip().strip('"').strip("'")
            if not cleaned:
                continue
            if cleaned.lower().startswith("value for"):
                continue
            if cleaned.lower().startswith("context"):
                continue
            if cleaned.lower() in {
                "not found",
                "not available",
                "not provided",
                "none",
                "null",
                "unknown",
                "n/a",
            }:
                return ""
            return cleaned

        return ""
    except Exception as exc:
        logger.warning("infer_value_from_context failed for label '%s': %s", label, exc)
        return ""

def generate_cfir(query: str, context: str, model: str = MODEL, temperature: float = 0.0) -> str:
    prompt = f"""You are a precise AI assistant that generates Cyber Fraud Incident Reports (CFIR) **only** from the provided context.

**Rules:**
- You will be given a context block containing fraud incident details.
- Your task is to extract the following fields **exactly as they appear** in the context:
    - Incident ID
    - Date
    - Type
    - Description
    - Impact
    - Actions taken
- If a field is present in the context, you **must** copy it verbatim – do not paraphrase, summarize, or replace with placeholders.
- If a field is **not** present, write `Not specified` for that field.
- **Never** use `N/A`, `Unknown`, `[REDACTED]`, or any similar placeholder unless the context literally contains that word.
- Use the exact wording from the context. For example, if the context says `Incident ID: INSIDER-2025-09-14`, you must output exactly that.
- Cite the source filename (shown in brackets) if appropriate, but the primary requirement is to reproduce the fields exactly.

Context:
{context}

User query: {query}

Now, generate the CFIR using this exact format (do not add extra commentary):

Incident ID: [copy from context]
Date: [copy from context]
Type: [copy from context]
Description: [copy from context]
Impact: [copy from context]
Actions taken: [copy from context]
"""
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": temperature,
            "max_tokens": 1000
        }
    }
    try:
        response = _ollama_post(payload)
        response.raise_for_status()
        return response.json()["response"]
    except requests.exceptions.ConnectionError:
        return "Error: Cannot connect to Ollama. Is it running? (Run 'ollama serve')"
    except requests.exceptions.ReadTimeout:
        return "Error: Ollama timed out while generating. Phi4 can be slower; try again or reduce prompt size."
    except Exception as e:
        return f"Error generating response: {str(e)}"


def fill_template(template: str, context: str, query: str, model: str = MODEL, temperature: float = 0.0) -> str:
    fields = [
        "incident_id",
        "date",
        "time_of_incident",
        "location_system_affected",
        "type",
        "description",
        "impact",
        "actions_taken",
        "recommended_next_actions",
        "reporter_name",
        "department",
        "contact_number",
        "email",
        "amount_lost",
        "currency",
    ]

    def normalize_field_name(raw: str) -> str:
        clean = re.sub(r"[^a-zA-Z0-9\s/]", " ", str(raw).lower())
        clean = re.sub(r"\s+", " ", clean).strip()
        aliases = {
            "incident id": "incident_id",
            "incident number": "incident_id",
            "date": "date",
            "date of incident": "date",
            "time of incident": "time_of_incident",
            "incident time": "time_of_incident",
            "location system affected": "location_system_affected",
            "location/system affected": "location_system_affected",
            "system affected": "location_system_affected",
            "type": "type",
            "incident type": "type",
            "type of fraud": "type",
            "description": "description",
            "incident description": "description",
            "full narrative": "description",
            "impact": "impact",
            "actions taken": "actions_taken",
            "recommended next actions": "recommended_next_actions",
            "name": "reporter_name",
            "reporter name": "reporter_name",
            "department": "department",
            "contact number": "contact_number",
            "email": "email",
            "email address": "email",
            "amount lost": "amount_lost",
            "currency": "currency",
        }
        if clean in aliases:
            return aliases[clean]
        return clean.replace(" ", "_")

    def as_fill_value(value) -> str:
        if _is_missing_value(value):
            return "Not available"
        return str(value).strip()

    def set_section_body(lines: list[str], heading: str, value: str) -> None:
        if not value:
            return
        heading_norm = heading.lower()
        for idx, line in enumerate(lines):
            line_norm = re.sub(r"\s+", " ", line.lower()).strip()
            if heading_norm not in line_norm:
                continue
            for j in range(idx + 1, len(lines)):
                probe = lines[j].strip()
                if re.match(r"^\d+[.)]\s", probe):
                    break
                if not probe:
                    continue
                if probe.startswith("(") and probe.endswith(")"):
                    continue
                if "___" in probe or len(probe.split()) <= 3:
                    lines[j] = value
                break
            break

    def apply_checkbox_lines(lines: list[str], incident_type_value: str) -> None:
        incident_type = re.sub(r"[^a-zA-Z0-9\s]", " ", incident_type_value.lower())
        incident_type = re.sub(r"\s+", " ", incident_type).strip()
        if not incident_type:
            return

        for idx, line in enumerate(lines):
            if not re.search(r"^[\s]*[☐☑☒□]", line):
                continue
            normalized_line = re.sub(r"^[\s]*[☐☑☒□]\s*", "", line)
            normalized_line = re.sub(r"[^a-zA-Z0-9\s]", " ", normalized_line.lower())
            normalized_line = re.sub(r"\s+", " ", normalized_line).strip()
            should_check = bool(normalized_line) and normalized_line in incident_type
            marker = "☑" if should_check else "☐"
            lines[idx] = re.sub(r"^[\s]*[☐☑☒□]", marker, line, count=1)

    extracted = extract_structured_data(query=query, context=context, fields=fields, model=model)

    # First pass: deterministic fill for common template patterns.
    filled_text = template

    def replace_placeholder(match: re.Match) -> str:
        field = normalize_field_name(match.group(1))
        return as_fill_value(extracted.get(field))

    filled_text = re.sub(r"\{\{\s*([^{}]+?)\s*\}\}", replace_placeholder, filled_text)

    lines = filled_text.splitlines()
    for i, line in enumerate(lines):
        label_blank_match = re.match(r"^(\s*[^:\n]{1,100}:)\s*_{3,}(.*)$", line)
        if label_blank_match:
            label = label_blank_match.group(1)
            tail = label_blank_match.group(2)
            field = normalize_field_name(label.split(":", 1)[0])
            lines[i] = f"{label} {as_fill_value(extracted.get(field))}{tail}"

    set_section_body(lines, "incident description", as_fill_value(extracted.get("description")))
    set_section_body(lines, "actions taken", as_fill_value(extracted.get("actions_taken")))
    set_section_body(lines, "recommended next actions", as_fill_value(extracted.get("recommended_next_actions")))
    apply_checkbox_lines(lines, as_fill_value(extracted.get("type")))

    filled_text = "\n".join(lines)
    filled_text = re.sub(r"_{3,}", "Not available", filled_text)

    unresolved_tokens = bool(re.search(r"\{\{[^{}]+\}\}|_{3,}", filled_text))
    if not unresolved_tokens:
        return filled_text

    # Second pass: ask the model to resolve any remaining unresolved placeholders only.
    prompt = f"""Fill unresolved blanks in this cyber incident template using only the context.

Rules:
- Keep existing non-blank text unchanged.
- Replace placeholders in {{field_name}} format.
- Replace visible blank lines made of underscores.
- For checkbox lines (☐/☑), mark the best matching fraud type as ☑ and uncheck others.
- If data is missing, use \"Not available\".
- Return only the completed document text.

Context:
{context}

Extracted fields (JSON):
{json.dumps(extracted, ensure_ascii=True)}

Partially filled template:
{filled_text}

Completed document:
"""
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": temperature,
            "max_tokens": 2000,
        },
    }
    try:
        response = _ollama_post(payload)
        response.raise_for_status()
        refined = response.json()["response"]
        if refined and refined.strip():
            return refined
        return filled_text
    except requests.exceptions.ReadTimeout:
        return filled_text
    except Exception:
        return filled_text


def extract_structured_data(query: str, context: str, fields: list | None = None, model: str = MODEL) -> dict:
    """
    Extract specified fields from context and return as a JSON object.
    fields: list of field names (e.g., ['incident_id', 'date', 'description'])
    """
    fields = fields or DEFAULT_EXTRACTION_FIELDS
    field_list = ", ".join(fields)
    prompt = f"""You are a strict information extraction engine for cyber incident reports.
Extract values for these fields: {field_list}

Rules:
- Return ONLY valid JSON with exactly those keys.
- Use values from context only; no guessing and no invented details.
- For missing values use null.
- Keep full narrative text for long fields like description, impact, actions_taken.
- Preserve exact values when present (IDs, dates, names, amounts).

Few-shot examples:

Example 1
Context:
Incident ID: RANSOM-001
Date: 2025-03-12
Type: Ransomware
Description: Core banking file shares were encrypted by malware.

Output JSON:
{{
    "incident_id": "RANSOM-001",
    "date": "2025-03-12",
    "type": "Ransomware",
    "description": "Core banking file shares were encrypted by malware.",
    "impact": null,
    "actions_taken": null,
    "reporter_name": null,
    "department": null,
    "contact_number": null,
    "email": null,
    "time": null,
    "location": null,
    "system": null,
    "amount_lost": null,
    "currency": null,
    "evidence_list": null
}}

Example 2
Context:
Employee downloaded 10,000 customer records to a personal USB drive before resigning.

Output JSON:
{{
    "incident_id": null,
    "date": null,
    "type": "Insider Threat",
    "description": "Employee downloaded 10,000 customer records to a personal USB drive before resigning.",
    "impact": "Data exfiltration",
    "actions_taken": "Terminated",
    "reporter_name": null,
    "department": null,
    "contact_number": null,
    "email": null,
    "time": null,
    "location": null,
    "system": null,
    "amount_lost": null,
    "currency": null,
    "evidence_list": null
}}

Example 3
Context:
Incident ID: BEC-442
Fraud type: Business Email Compromise
Reporter Name: Ana Cruz
Department: Treasury
Contact Number: +63-917-111-2222
Email: ana.cruz@bank.example
Amount lost: 250000
Currency: PHP
System affected: Payment portal
Evidence list: Email headers, transaction logs

Output JSON:
{{
    "incident_id": "BEC-442",
    "date": null,
    "type": "Business Email Compromise",
    "description": null,
    "impact": null,
    "actions_taken": null,
    "reporter_name": "Ana Cruz",
    "department": "Treasury",
    "contact_number": "+63-917-111-2222",
    "email": "ana.cruz@bank.example",
    "time": null,
    "location": null,
    "system": "Payment portal",
    "amount_lost": "250000",
    "currency": "PHP",
    "evidence_list": "Email headers, transaction logs"
}}

Now extract from this input and return strict JSON:
Context:
{context}

User query: {query}

JSON output:
"""
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": 0.0,
            "max_tokens": 2000,
        },
    }
    try:
        response = _ollama_post(payload)
        response.raise_for_status()
        result_text = response.json()["response"]
        # Extract JSON from the response (in case the AI adds extra text)
        parsed = {}
        json_match = re.search(r"\{.*\}", result_text, re.DOTALL)
        if json_match:
            try:
                parsed = json.loads(json_match.group())
            except Exception:
                parsed = {}

        context_fallback = _extract_from_context(context, fields)
        narrative_fields = {"description", "impact", "actions_taken", "recommendations"}
        merged = {}
        for field in fields:
            candidate = parsed.get(field) if isinstance(parsed, dict) else None
            fallback_value = context_fallback.get(field)
            chosen = fallback_value if _is_missing_value(candidate) else candidate

            # Guard against low-quality LLM extraction for long-form fields.
            if field in narrative_fields and isinstance(chosen, str) and fallback_value:
                chosen_tokens = len(chosen.strip().split())
                fallback_tokens = len(str(fallback_value).strip().split())
                if chosen_tokens <= 3 and fallback_tokens > chosen_tokens:
                    chosen = fallback_value

            merged[field] = chosen

        # Ensure all required fields are present and default to null when missing.
        for field in fields:
            if field not in merged:
                merged[field] = None

        return merged
    except requests.exceptions.ReadTimeout:
        logger.warning("Ollama timed out in extract_structured_data; using regex/context fallback.")
        fallback = _extract_from_context(context, fields)
        for field in fields:
            if field not in fallback:
                fallback[field] = None
        return fallback
    except Exception as e:
        logger.exception("Error in extract_structured_data: %s", e)
        fallback = _extract_from_context(context, fields)
        for field in fields:
            if field not in fallback:
                fallback[field] = None
        return fallback


def synthesize_recommendations(extracted_data: dict | None) -> str:
    """Create actionable next-step recommendations when source documents do not provide them."""
    data = extracted_data or {}
    existing = str(data.get("recommendations") or "").strip()
    if existing:
        return existing

    incident_type = str(data.get("type") or "").lower()
    baseline = [
        "Continue enhanced monitoring for at least 30 days.",
        "Preserve forensic artifacts and maintain a complete incident timeline.",
        "Submit a post-incident review with root cause and control improvements.",
    ]

    type_specific = {
        "phishing": [
            "Block sender/domain and enforce URL rewriting plus attachment sandboxing.",
            "Require phishing-resistant MFA and conditional access for exposed users.",
            "Run a targeted awareness simulation for departments that clicked.",
        ],
        "business email compromise": [
            "Enforce out-of-band payment verification for all high-risk transfer requests.",
            "Add mailbox rule auditing and impossible-travel alerts for finance users.",
            "Harden executive impersonation detection in secure email gateway policies.",
        ],
        "ransomware": [
            "Validate offline backup restoration with a full recovery drill.",
            "Isolate affected endpoints and rotate privileged credentials immediately.",
            "Deploy EDR containment policies for rapid lateral movement blocking.",
        ],
        "identity theft": [
            "Invalidate compromised identities and reissue credentials after identity proofing.",
            "Increase anomaly detection on account recovery and profile change workflows.",
            "Notify affected users and require credential reset with MFA re-registration.",
        ],
        "unauthorized transfer": [
            "Coordinate with banking partners for recall and fraud case escalation.",
            "Implement dual authorization and transaction risk scoring thresholds.",
            "Review beneficiary allowlists and freeze suspicious destinations.",
        ],
        "insider threat": [
            "Enforce least privilege review for high-risk data repositories.",
            "Enable DLP controls for removable media and personal cloud uploads.",
            "Coordinate HR and legal for evidence handling and policy action.",
        ],
    }

    selected = []
    for key, actions in type_specific.items():
        if key in incident_type:
            selected.extend(actions)
            break

    if not selected:
        selected.append("Perform targeted control hardening based on the incident attack path.")

    recommendations = selected + baseline
    return "\n".join(f"- {item}" for item in recommendations)


def map_template_fields(template_text: str, known_fields: list) -> dict:
    # Deterministic mapping is more reliable than LLM mapping for generic labels.
    alias_map = {
        "incident id": "incident_id",
        "incident number": "incident_id",
        "date": "date",
        "date of incident": "date",
        "time of incident": "time_of_incident",
        "system affected": "location_system_affected",
        "location/system affected": "location_system_affected",
        "type": "type",
        "type of fraud": "type",
        "incident description": "description",
        "description": "description",
        "full narrative": "description",
        "impact": "impact",
        "actions taken": "actions_taken",
        "recommended next actions": "recommended_next_actions",
        "name": "reporter_name",
        "reporter name": "reporter_name",
        "department": "department",
        "contact number": "contact_number",
        "email": "email",
        "email address": "email",
        "amount lost": "amount_lost",
        "currency": "currency",
    }

    known = set(known_fields or [])
    mapping = {}
    for raw_line in template_text.splitlines():
        if ":" not in raw_line:
            continue
        raw_label = raw_line.split(":", 1)[0].strip()
        if not raw_label:
            continue
        cleaned = re.sub(r"^\d+[.)]\s*", "", raw_label)
        normalized = re.sub(r"[^a-zA-Z0-9\s/]", "", cleaned.lower()).strip()
        normalized = re.sub(r"\s+", " ", normalized)
        if not normalized:
            continue

        canonical = alias_map.get(normalized, "unknown")
        if canonical != "unknown" and known and canonical not in known:
            canonical = "unknown"
        mapping[raw_label] = canonical

    return mapping