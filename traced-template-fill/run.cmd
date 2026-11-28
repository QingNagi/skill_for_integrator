@echo off
setlocal EnableExtensions DisableDelayedExpansion
if exist "%~dp0offline-runtime\" goto paired
for %%D in ("%~dp0..") do if /i "%%~nxD"=="skills" (
    for %%H in ("%~dp0..\..") do (
        if /i "%%~nxH"==".agents" goto registered
        if /i "%%~nxH"==".claude" goto registered
    )
)
:standalone
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

:registered
set "binding=%~dp0..\..\..\.traced-template-fill\win64"
if not exist "%binding%\." goto standalone
"%binding%\runtime\python\python.exe" -B -X utf8 "%binding%\scripts\di.py" %*
exit /b %errorlevel%

:paired
"%~dp0offline-runtime\run.cmd" %*
