from typing import Optional

import requests

OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL = "mistral:7b-instruct-q4_K_M"   # or "llama3:8b-instruct-q4_K_M"

def generate_cfir(
    query: str,
    context: str,
    model: Optional[str] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
) -> str:
    prompt = f"""You are an anti-fraud analyst. Using only the context below, generate a Cyber Fraud Incident Report (CFIR) following the standard template. Cite the source document for each key fact. If the context does not contain enough information, say "Insufficient data".

Context:
{context}

Query: {query}

CFIR Draft:
"""
    model_name = model or MODEL
    payload = {
        "model": model_name,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": 0.2 if temperature is None else temperature,
            "max_tokens": 1000 if max_tokens is None else max_tokens,
        }
    }
    resp = requests.post(OLLAMA_URL, json=payload)
    resp.raise_for_status()
    return resp.json()["response"]