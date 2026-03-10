import json
import logging
import re

import requests

OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL = "mistral:7b-instruct-q4_K_M"
logger = logging.getLogger(__name__)


def _is_missing_value(value) -> bool:
    if value is None:
        return True
    if isinstance(value, str) and not value.strip():
        return True
    if isinstance(value, (list, dict)) and len(value) == 0:
        return True
    return False


def _extract_from_context(context: str, fields: list) -> dict:
    extracted = {}
    for field in fields:
        base_label = field.replace("_", " ")
        variants = [base_label, base_label.title()]

        # Common incident-report aliases.
        alias_map = {
            "incident_id": ["incident id"],
            "actions_taken": ["actions taken", "action taken"],
            "amount_lost": ["amount lost", "loss amount"],
            "reporter_name": ["reporter name", "reported by"],
        }
        variants.extend(alias_map.get(field, []))

        value = None
        for label in variants:
            pattern = rf"(?im)^\s*{re.escape(label)}\s*:\s*(.+?)\s*$"
            match = re.search(pattern, context)
            if match:
                value = match.group(1).strip()
                break

        extracted[field] = value
    return extracted

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
        response = requests.post(OLLAMA_URL, json=payload, timeout=60)
        response.raise_for_status()
        return response.json()["response"]
    except requests.exceptions.ConnectionError:
        return "Error: Cannot connect to Ollama. Is it running? (Run 'ollama serve')"
    except Exception as e:
        return f"Error generating response: {str(e)}"


def fill_template(template: str, context: str, query: str, model: str = MODEL, temperature: float = 0.0) -> str:
    prompt = f"""You are given a template document with placeholders in the format {{field_name}}. Your task is to fill the template using only the information from the provided context. Replace each placeholder with the appropriate value from the context. If the context does not contain information for a field, leave it blank or write \"Not available\". Do not add any extra text. Output the entire filled document.

Context:
{context}

Template:
{template}

User query:
{query}

Filled document:
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
        response = requests.post(OLLAMA_URL, json=payload, timeout=60)
        response.raise_for_status()
        return response.json()["response"]
    except Exception as e:
        return f"Error filling template: {str(e)}"


def extract_structured_data(query: str, context: str, fields: list, model: str = MODEL) -> dict:
    """
    Extract specified fields from context and return as a JSON object.
    fields: list of field names (e.g., ['incident_id', 'date', 'description'])
    """
    field_list = ", ".join(fields)
    prompt = f"""You are an AI assistant that extracts structured information from documents.
Based on the following context, extract values for these fields: {field_list}
Return ONLY a valid JSON object with these fields. Do not add any explanations.
If a field is missing from the context, set its value to null.

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
            "max_tokens": 1000,
        },
    }
    try:
        response = requests.post(OLLAMA_URL, json=payload, timeout=60)
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
        merged = {}
        for field in fields:
            candidate = parsed.get(field) if isinstance(parsed, dict) else None
            merged[field] = context_fallback.get(field) if _is_missing_value(candidate) else candidate

        return merged
    except Exception as e:
        logger.exception("Error in extract_structured_data: %s", e)
        return _extract_from_context(context, fields)


def map_template_fields(template_text: str, known_fields: list) -> dict:
    prompt = f"""You are given a document template text. Identify all fields that need to be filled (e.g., after labels like "Incident ID:"). For each field, provide:
- The exact label text (e.g., "Incident ID")
- The canonical field name from this list: {known_fields}
Return a JSON dictionary mapping label -> canonical field name. If a label doesn't match any known field, map it to "unknown".
Template:
{template_text}
"""
    payload = {
        "model": MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.0, "max_tokens": 1000},
    }
    try:
        response = requests.post(OLLAMA_URL, json=payload, timeout=60)
        response.raise_for_status()
        result_text = response.json()["response"]
        json_match = re.search(r"\{.*\}", result_text, re.DOTALL)
        if json_match:
            return json.loads(json_match.group())
        return {}
    except Exception as e:
        logger.exception("Failed to map template fields: %s", e)
        return {}