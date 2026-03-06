import requests

OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL = "mistral:7b-instruct-q4_K_M"   # or "llama3:8b-instruct-q4_K_M"

def generate_cfir(query: str, context: str) -> str:
    prompt = f"""You are an anti-fraud analyst. Using only the context below, generate a Cyber Fraud Incident Report (CFIR) following the standard template. Cite the source document for each key fact. If the context does not contain enough information, say "Insufficient data".

Context:
{context}

Query: {query}

CFIR Draft:
"""
    payload = {
        "model": MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.2, "max_tokens": 1000}
    }
    resp = requests.post(OLLAMA_URL, json=payload)
    resp.raise_for_status()
    return resp.json()["response"]