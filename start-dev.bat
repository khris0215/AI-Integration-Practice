@echo off
setlocal

set "ROOT=%~dp0"
if not defined START_WATCHER set "START_WATCHER=1"
set "START_WATCHER=%START_WATCHER:\"=%"

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

if /I "%START_WATCHER%"=="1" goto :start_watcher
echo [INFO] Watcher disabled. Set START_WATCHER=1 to enable it.
goto :after_watcher

:start_watcher
start "Watcher" cmd /k ""%ROOT%backend\venv\Scripts\python.exe" "%ROOT%backend\watcher.py""

:after_watcher

goto :eof

:is_port_listening
set "PORT=%~1"
netstat -ano | findstr /R /C:":%PORT% .*LISTENING" >nul
exit /b %errorlevel%