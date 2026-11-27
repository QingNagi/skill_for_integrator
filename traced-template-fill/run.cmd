@echo off
setlocal EnableExtensions DisableDelayedExpansion
if exist "%~dp0runtime\python\python.exe" (
    "%~dp0runtime\python\python.exe" -B -X utf8 "%~dp0scripts\di.py" %*
) else (
    python -B -X utf8 "%~dp0scripts\di.py" %*
)
exit /b %errorlevel%
