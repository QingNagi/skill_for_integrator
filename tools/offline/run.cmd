@echo off
setlocal EnableExtensions DisableDelayedExpansion
set "kit=%~dp0"
set "digest=@PAYLOAD_SHA256@"
set "cache=%LOCALAPPDATA%\traced-template-fill\offline"
if defined DI_CACHE_DIR set "cache=%DI_CACHE_DIR%\offline"
for %%F in ("%cache%") do set "cache=%%~fF"
set "installed=%cache%\%digest:~0,24%"
set "found="
set "marker=.offline-payload.sha256"
set "system=%SystemRoot%\System32"
"%system%\findstr.exe" /l /x /c:"%digest%" "%kit%payload.sha256" >nul || goto damaged

rem Respect a matching explicit project/user installation, including the old skill name.
set "base=%CD%"
:ancestors
call :discover
if defined found goto selected
for %%P in ("%base%\..") do set "parent=%%~fP"
if /i "%parent%"=="%base%" goto home
set "base=%parent%"
goto ancestors
:home
set "base=%USERPROFILE%"
call :discover
:selected
if defined found set "installed=%found%"
if not exist "%installed%\%marker%" goto prepare
"%system%\findstr.exe" /l /x /c:"%digest%" "%installed%\%marker%" >nul || goto prepare
if not exist "%kit%runtime-files.txt" goto damaged
for /f "usebackq delims=" %%F in ("%kit%runtime-files.txt") do (
    for %%P in ("%installed%\%%F") do (
        if "%%~zP"=="" goto prepare
        if "%%~zP"=="0" goto prepare
    )
)
:launch
"%installed%\runtime\python\python.exe" -B -X utf8 "%installed%\scripts\di.py" %*
exit /b %errorlevel%

:discover
for %%A in (.agents .claude .codex) do for %%N in (traced-template-fill doc-integrator) do (
    if exist "%base%\%%A\skills\%%N\%marker%" (
        "%system%\findstr.exe" /l /x /c:"%digest%" "%base%\%%A\skills\%%N\%marker%" >nul && (
            set "found=%base%\%%A\skills\%%N"
            exit /b 0
        )
    )
)
exit /b 0

:prepare
if not exist "%kit%payload.zip" goto damaged
if not exist "%cache%\." mkdir "%cache%"
if not exist "%cache%\." exit /b 1
:temporary
set "stage=%cache%\.cmd-%RANDOM%-%RANDOM%-%RANDOM%"
if exist "%stage%" goto temporary
mkdir "%stage%" || exit /b 1
set "rc=1"
copy /b /y "%kit%payload.zip" "%stage%\payload.zip" >nul || goto cleanup
"%system%\certutil.exe" -hashfile "%stage%\payload.zip" SHA256 > "%stage%\computed.sha256" || goto cleanup
"%system%\findstr.exe" /i /l /x /c:"%digest%" "%stage%\computed.sha256" >nul || goto cleanup
"%system%\tar.exe" -xf "%stage%\payload.zip" -C "%stage%" "traced-template-fill/runtime/python" || goto cleanup
"%stage%\traced-template-fill\runtime\python\python.exe" -B -X utf8 "%kit%cmd_install.py" "%stage%\payload.zip" "%installed%" "%digest%" "%kit%runtime-files.txt"
set "rc=%errorlevel%"
:cleanup
rem stage is a fresh direct child of the absolute cache path.
for %%P in ("%stage%\..") do if /i "%%~fP"=="%cache%" rd /s /q "%stage%"
if "%rc%"=="0" goto launch
echo Offline installation failed. Check the package checksum and the cache directory. 1>&2
exit /b %rc%

:damaged
echo Missing or mismatched payload. Restore this version of the offline kit. 1>&2
exit /b 1
