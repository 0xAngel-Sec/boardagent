@echo off
REM ============================================================
REM  BoardAgent uninstaller — removes autostart, watchdog task,
REM  PATH entry, and deletes the installed files.
REM ============================================================
setlocal EnableDelayedExpansion
set "APP_DIR=%LOCALAPPDATA%\BoardAgent"
set "RUN_KEY=HKCU\Software\Microsoft\Windows\CurrentVersion\Run"
set "ENV_KEY=HKCU\Environment"

echo.
echo  === BoardAgent uninstall ===

REM -- stop the running server ----------------------------------
taskkill /IM boardagent-server.exe /F >nul 2>&1
echo [1/4] server stopped

REM -- remove autostart + watchdog task --------------------------
reg delete "%RUN_KEY%" /v BoardAgent /f >nul 2>&1
schtasks /delete /f /tn "BoardAgentWatchdog" >nul 2>&1
echo [2/4] autostart and watchdog removed

REM -- remove the TUI from the user PATH -------------------------
set "NEWPATH="
for /f "tokens=2*" %%a in ('reg query "%ENV_KEY%" /v Path 2^>nul') do set "NEWPATH=%%a"
set "NEWPATH=!NEWPATH:;%APP_DIR%=;!"
if "!NEWPATH!"=="!NEWPATH:%APP_DIR%=!" goto :nopath
set "NEWPATH=!NEWPATH:%APP_DIR%;=!"
if "%NEWPATH%"=="" (
    reg delete "%ENV_KEY%" /v Path /f >nul 2>&1
) else (
    reg add "%ENV_KEY%" /v Path /t REG_EXPAND_SZ /d "!NEWPATH!" /f >nul
)
:nopath
echo [3/4] PATH entry removed

REM -- remove the installed files --------------------------------
if exist "%APP_DIR%" rmdir /s /q "%APP_DIR%"
echo [4/4] %APP_DIR% removed

echo.
echo  BoardAgent uninstalled.
echo  Your tasks lived in %APP_DIR%\boardagent.db and are gone.
echo  Backup that file before uninstalling if you want to keep them.
exit /b 0
