@echo off
setlocal EnableExtensions DisableDelayedExpansion
set "runner=%~dp0runtime\python\python.exe"
if exist "%runner%" goto launch
for %%P in (python.exe) do set "runner=%%~$PATH:P"
if not defined runner goto missing
for %%P in ("%runner%") do if /i "%%~dpP"=="%CD%\" goto missing
:launch
"%runner%" -B -X utf8 "%~dp0scripts\di.py" %*
exit /b %errorlevel%
:missing
echo Cannot find an installed python.exe on PATH. 1>&2
exit /b 1
