import requests

OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL = "mistral:7b-instruct-q4_K_M"

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