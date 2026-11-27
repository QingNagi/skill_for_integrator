@echo off
setlocal EnableExtensions DisableDelayedExpansion
set "kit=%~dp0"
set "digest=@PAYLOAD_SHA256@"
set "cache=%LOCALAPPDATA%\traced-template-fill\offline"
if defined DI_CACHE_DIR set "cache=%DI_CACHE_DIR%\offline"
for %%F in ("%cache%") do set "cache=%%~fF"
set "installed=%cache%\%digest:~0,24%"
set "marker=.offline-payload.sha256"
set "system=%SystemRoot%\System32"
"%system%\findstr.exe" /l /x /c:"%digest%" "%kit%payload.sha256" >nul || goto damaged

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

:prepare
if not exist "%kit%payload.zip" goto damaged
if not exist "%cache%\." mkdir "%cache%" 2>nul
if not exist "%cache%\." exit /b 1
set "RANDOM="
set /a attempts=0
:temporary
set /a attempts+=1
set "stage=%cache%\.cmd-%RANDOM%-%RANDOM%-%RANDOM%"
mkdir "%stage%" 2>nul && goto staged
if exist "%stage%\." if %attempts% LSS 32 goto temporary
echo Cannot create an installation staging directory in the cache. 1>&2
exit /b 1
:staged
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
