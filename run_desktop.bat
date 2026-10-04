@echo off
title LLM-Konnect Desktop Launcher
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\run_desktop.ps1"
if errorlevel 1 pause
