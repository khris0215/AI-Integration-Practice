@echo off
setlocal EnableExtensions EnableDelayedExpansion

set "ROOT=%~dp0"
if not defined START_WATCHER set "START_WATCHER=1"
set "START_WATCHER=%START_WATCHER:\"=%"
if not defined ONEDRIVE_AUTO_SYNC set "ONEDRIVE_AUTO_SYNC=0"
if not defined WATCHER_OBSERVER_MODE set "WATCHER_OBSERVER_MODE=polling"
if not defined DATA_PATH (
	if exist "%USERPROFILE%\Desktop\OneDrive\FraudIncidents" (
		set "DATA_PATH=%USERPROFILE%\Desktop\OneDrive\FraudIncidents"
	) else (
		set "DATA_PATH=%USERPROFILE%\OneDrive\FraudIncidents"
	)
)

set "LAN_IP="
for /f "tokens=2 delims=:" %%i in ('ipconfig ^| findstr /I "IPv4"') do (
	set "CANDIDATE=%%i"
	set "CANDIDATE=!CANDIDATE: =!"
	if not defined LAN_IP (
		if /I not "!CANDIDATE:~0,8!"=="127.0.0." (
			if /I not "!CANDIDATE:~0,8!"=="169.254." (
				set "LAN_IP=!CANDIDATE!"
			)
		)
	)
)

call :is_port_listening 8000
if %errorlevel%==0 (
	echo [INFO] Backend API already running on port 8000. Skipping launch.
) else (
	echo [INFO] Backend OneDrive auto-sync mode: %ONEDRIVE_AUTO_SYNC%
	echo [INFO] Backend data path: %DATA_PATH%
	start "Backend API (Mobile)" /D "%ROOT%backend" cmd /k "set ONEDRIVE_AUTO_SYNC=%ONEDRIVE_AUTO_SYNC% && set DATA_PATH=%DATA_PATH% && call venv\Scripts\activate && uvicorn app.main:app --host 0.0.0.0 --port 8000"
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
	start "Frontend (Mobile)" /D "%ROOT%frontend" cmd /k "python -m http.server 5500 --bind 0.0.0.0"
)

if /I "%START_WATCHER%"=="1" goto :start_watcher
echo [INFO] Watcher disabled. Set START_WATCHER=1 to enable it.
goto :after_watcher

:start_watcher
echo [INFO] Watcher observer mode: %WATCHER_OBSERVER_MODE%
echo [INFO] Watcher data path: %DATA_PATH%
start "Watcher" /D "%ROOT%backend" cmd /k "set WATCHER_OBSERVER_MODE=%WATCHER_OBSERVER_MODE% && set DATA_PATH=%DATA_PATH% && call venv\Scripts\activate && python watcher.py"

:after_watcher
echo.
echo [INFO] Mobile test launcher started.
if defined LAN_IP (
	echo [INFO] Laptop IP detected: %LAN_IP%
	echo [INFO] Open on phone: http://%LAN_IP%:5500/landing-page.html
	echo [INFO] Backend health:  http://%LAN_IP%:8000/api/health
	echo [INFO] Make sure frontend/script.js API_BASE matches: http://%LAN_IP%:8000/api
) else (
	echo [WARN] Could not auto-detect laptop IP. Run "ipconfig" and use your IPv4 address.
)
echo.
echo [INFO] If phone cannot connect, allow ports 5500 and 8000 in Windows Firewall.

goto :eof

:is_port_listening
set "PORT=%~1"
netstat -ano | findstr /R /C:":%PORT% .*LISTENING" >nul
exit /b %errorlevel%
