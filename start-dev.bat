@echo off
setlocal

set "ROOT=%~dp0"

call :is_port_listening 8000
if %errorlevel%==0 (
	echo [INFO] Backend API already running on port 8000. Skipping launch.
) else (
	start "Backend API" /D "%ROOT%backend" cmd /k "call venv\Scripts\activate && uvicorn app.main:app --host 127.0.0.1 --port 8000"
)

call :is_port_listening 11434
if %errorlevel%==0 (
	echo [INFO] Ollama already running on port 11434. Skipping launch.
) else (
	start "Ollama" cmd /k "ollama serve"
)

call :is_port_listening 5500
if %errorlevel%==0 (
	echo [INFO] Frontend server already running on port 5500. Skipping launch.
) else (
	start "Frontend" /D "%ROOT%frontend" cmd /k "python -m http.server 5500"
)

start "Watcher" /D "%ROOT%backend" cmd /k "call venv\Scripts\activate && python watcher.py"

goto :eof

:is_port_listening
set "PORT=%~1"
netstat -ano | findstr /R /C:":%PORT% .*LISTENING" >nul
exit /b %errorlevel%