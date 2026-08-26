@echo off
REM ============================================================
REM  BoardAgent installer — Windows
REM  Installs without Python: copies the bundled exes to
REM  %LOCALAPPDATA%\BoardAgent, registers the server as an
REM  always-on autostart (at logon), installs a 5-minute
REM  watchdog scheduled task, and adds the TUI to the user PATH.
REM ============================================================
setlocal EnableDelayedExpansion
cd /d "%~dp0"

set "APP_DIR=%LOCALAPPDATA%\BoardAgent"
set "SERVER_EXE=%APP_DIR%\boardagent-server.exe"
set "TUI_EXE=%APP_DIR%\boardagent.exe"
set "MCP_EXE=%APP_DIR%\boardagent-mcp.exe"
set "RUN_KEY=HKCU\Software\Microsoft\Windows\CurrentVersion\Run"
set "ENV_KEY=HKCU\Environment"

echo.
echo  === BoardAgent install ===
echo.

REM -- 1. stop any running instance FIRST (Windows locks running exes,
REM    so copying over a live boardagent-server.exe would fail silently) ---
taskkill /IM boardagent-server.exe /F >nul 2>&1
echo [1/5] stopped old instances

REM -- 2. copy files -------------------------------------------------
if not exist "%APP_DIR%" mkdir "%APP_DIR%"
if not exist "%~dp0dist\boardagent-server.exe" (
    echo [ERROR] dist\boardagent-server.exe not found.
    echo         Build the exes first, then run this installer:
    echo         python scripts\build_exes.py
    goto :fail
)
copy /y "dist\boardagent-server.exe" "%SERVER_EXE%" >nul || goto :copyfail
copy /y "dist\boardagent.exe"        "%TUI_EXE%" >nul || goto :copyfail
if exist "dist\boardagent-mcp.exe" copy /y "dist\boardagent-mcp.exe" "%MCP_EXE%" >nul || goto :copyfail
echo [2/5] binaries copied to %APP_DIR%

REM -- 3. autostart at logon (background, no console window) -------
reg add "%RUN_KEY%" /v BoardAgent /t REG_SZ /d "\"%SERVER_EXE%\" --watch" /f >nul
echo [3/5] autostart registered (HKCU Run)

REM -- 4. 5-minute watchdog task (restarts if the server dies) -----
REM    One command, no shell operators: the exe itself checks the port in
REM    --watchdog mode and heals if the server is down. (Task Scheduler
REM    executes the action via CreateProcess, not cmd.exe — `||`/`&&`
REM    would be passed to the exe as literal arguments and silently fail.)
schtasks /create /f /tn "BoardAgentWatchdog" /tr "\"%SERVER_EXE%\" --watchdog" /sc minute /mo 5 /ru "%USERNAME%" >nul
echo [4/5] watchdog scheduled task installed (every 5 min)

REM -- 5. add the TUI to the user PATH (persistent) ----------------
set "NEWPATH="
for /f "tokens=2*" %%a in ('reg query "%ENV_KEY%" /v Path 2^>nul') do set "NEWPATH=%%b"
echo !NEWPATH! | findstr /i "%APP_DIR%" >nul
if errorlevel 1 (
    if defined NEWPATH (
        reg add "%ENV_KEY%" /v Path /t REG_EXPAND_SZ /d "!NEWPATH!;%APP_DIR%" /f >nul
    ) else (
        reg add "%ENV_KEY%" /v Path /t REG_EXPAND_SZ /d "%APP_DIR%" /f >nul
    )
)
echo [5/5] TUI added to user PATH (visible in new terminals)

REM -- start it now -------------------------------------------------
start "" "%SERVER_EXE%" --watch
echo.
echo  DONE. BoardAgent is running and will auto-start at every logon.
echo  Open the TUI:  double-click boardagent.exe (or type "boardagent"
echo  in a terminal).
echo.
echo  API:  http://127.0.0.1:7373/healthz
echo  Data: %USERPROFILE%\.boardagent\boardagent.db
echo  MCP:  point your MCP host at %MCP_EXE%  (see docs/human/mcp.md)
echo  Uninstall:  run uninstall.bat
echo.
exit /b 0

:copyfail
echo.
echo  [ERROR] Failed to copy binaries to %APP_DIR%.
echo          Close any running BoardAgent first, then retry.
exit /b 1

:fail
echo.
echo  Install aborted. See README.md "Build the binaries".
exit /b 1
