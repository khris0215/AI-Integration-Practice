@echo off
start cmd /k "cd backend && venv\Scripts\activate && uvicorn app.main:app --reload --port 8000"
start cmd /k "ollama serve"
start cmd /k "cd frontend && python -m http.server 5500"