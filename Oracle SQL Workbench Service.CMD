@echo off
setlocal
title Oracle SQL Workbench Service

echo Checking Oracle SQL Workbench service...
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0Oracle SQL Workbench Service.PS1" -Action EnsureService
echo.
timeout /t 2 /nobreak >nul

:MENU
cls
echo ================================================
echo          ORACLE SQL WORKBENCH SERVICE
echo ================================================
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0Oracle SQL Workbench Service.PS1" -Action Status
echo.
echo [1] Restart Service only
echo [2] Stop Service
echo [3] Open application (service unchanged)
echo [4] Start Service only
echo [5] Show Logs
echo [6] Check current server health
echo [Q] Close this menu and leave service running
echo.
choice /C 123456Q /N /M "Choose an option: "
if errorlevel 7 goto QUIT
if errorlevel 6 goto HEALTH
if errorlevel 5 goto LOGS
if errorlevel 4 goto START_ONLY
if errorlevel 3 goto OPENAPP
if errorlevel 2 goto STOP
if errorlevel 1 goto RESTART_ONLY
goto MENU

:RESTART_ONLY
call :RUN RestartOnly
goto MENU

:START_ONLY
call :RUN StartOnly
goto MENU

:STOP
call :RUN Stop
goto MENU

:OPENAPP
call :RUN Open
goto MENU

:LOGS
call :RUN Logs
goto MENU

:HEALTH
call :RUN Health
pause
goto MENU

:RUN
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0Oracle SQL Workbench Service.PS1" -Action %~1
echo.
exit /b

:QUIT
echo Closing the menu. The service, if running, will stay in the background.
timeout /t 2 /nobreak >nul
exit /b 0
