@echo off
title Oracle SQL Workbench Service
setlocal
:MAIN
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0Oracle SQL Workbench Service.ps1"
echo.
close this window to exit.
goto MAIN