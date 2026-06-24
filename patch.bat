@echo off
setlocal EnableExtensions

REM =========================================================
REM  D2R Classic++ R200 Canon Patcher
REM =========================================================
REM Zero-flag canon runner. Put this file beside patcher.py plus:
REM   vanilla\
REM   static_mod\
REM   patch_sources\
REM Output is written to:
REM   output\
REM =========================================================

cd /d "%~dp0"

echo.
echo =========================================================
echo D2R Classic++ R200 Canon Patcher
echo Working folder: %CD%
echo =========================================================
echo.

if not exist "patcher.py" (
    echo ERROR: patcher.py was not found beside this batch file.
    pause
    exit /b 1
)

if not exist "vanilla\data\global\excel" (
    echo ERROR: vanilla dump not found or incomplete.
    echo Expected: "%~dp0vanilla\data\global\excel"
    pause
    exit /b 1
)

if not exist "static_mod\mods" (
    echo ERROR: static_mod folder not found or incomplete.
    echo Expected: "%~dp0static_mod\mods"
    pause
    exit /b 1
)

if not exist "patch_sources" (
    echo ERROR: patch_sources folder not found.
    echo Expected: "%~dp0patch_sources"
    pause
    exit /b 1
)

REM Keep canon reproducible: clear test/harness toggles if they exist in the shell.
set "AMAZON_SPECIFIC_HARNESS=0"
set "JAVE_TEST=0"
set "JAVE_FAMILY_HARNESS=0"

for %%A in (
TORS HELM GLOV BOOT BELT SHLD HEAD
SWOR AXE MACE HAMM CLUB WAND SCEP STAF SPEA JAVE DAGG POLE THRO BOW XBOW ORB
RING AMUL GEM
) do (
    set "STAGE1_%%A=0"
)

where py >nul 2>nul
if %ERRORLEVEL%==0 (
    set "PY_CMD=py -3"
) else (
    where python >nul 2>nul
    if %ERRORLEVEL%==0 (
        set "PY_CMD=python"
    ) else (
        echo ERROR: Python was not found. Install Python or use the Python launcher.
        pause
        exit /b 1
    )
)

echo Running: %PY_CMD% patcher.py
echo.
%PY_CMD% patcher.py
set "ERR=%ERRORLEVEL%"
echo.
if not "%ERR%"=="0" (
    echo FAILED with exit code %ERR%.
    pause
    exit /b %ERR%
)

echo Done. Output generated in: "%~dp0output"
pause
