@echo off
setlocal EnableExtensions

REM =========================================================
REM  D2R Classic++ Canon Patcher
REM =========================================================
REM
REM Zero-flag runner:
REM   - Put this batch file beside patcher.py
REM   - Put vanilla/, static_mod/, and patch_sources/ beside patcher.py
REM   - Double-click this file or run: patch.bat
REM
REM The patch profile is baked into patcher.py.
REM This batch file does not pass gameplay flags or modes.
REM UITOGGLE is intentionally still an environment toggle; change it below to 1
REM when you want UI layout overrides enabled for a test/build.
REM
REM Baked canon profile mirrored from old batch defaults:
REM   ENABLE_EXPANSION_DROPS_IN_CLASSIC=1
REM   COWALLBASES=1
REM   COWCHAOS=1
REM   EXP_DROPS_STAGE=4
REM   UITOGGLE=0  ^(change local set UITOGGLE=1 to enable UI overrides^)
REM   LODSTASH=1
REM   COWALWAYSDROP=1
REM   NOLOWQUALITY=1
REM   COW_ALLBASES_SEED=1782137524
REM   COW_ALLBASES_POOL_SIZE=45
REM   COW_ALLBASES_WRAP_PROB=8192
REM =========================================================

cd /d "%~dp0"

echo.
echo =========================================================
echo D2R Classic++ Canon Patcher
echo Working folder: %CD%
echo =========================================================
echo.

REM =========================================================
REM  Local zero-flag toggles
REM  Change UITOGGLE to 1 to enable UI layout overrides.
REM =========================================================
set "UITOGGLE=0"

REM ===== Sanity checks =====
if not exist "patcher.py" (
    echo ERROR: patcher.py was not found beside this batch file.
    echo Expected: "%~dp0patcher.py"
    echo.
    pause
    exit /b 1
)

REM Detect the old flag-based patcher before running it.
REM The zero-flag canon patcher contains this baked-in constant.
findstr /C:"CANON_EXP_DROPS_STAGE" "%~dp0patcher.py" >nul 2>nul
if errorlevel 1 (
    echo ERROR: This looks like the OLD flag-based patcher.py.
    echo.
    echo Replace C:\D2Rmod\patcher.py with the new zero-flag patcher.py.
    echo The old file requires --vanilla and --out, which is why it failed.
    echo.
    pause
    exit /b 2
)

if not exist "vanilla\data" (
    echo ERROR: vanilla dump not found or incomplete.
    echo Expected folder:
    echo   "%~dp0vanilla\data"
    echo.
    pause
    exit /b 1
)

if not exist "vanilla\data\global\excel" (
    echo ERROR: vanilla Excel dump not found or incomplete.
    echo Expected folder:
    echo   "%~dp0vanilla\data\global\excel"
    echo.
    pause
    exit /b 1
)

if not exist "static_mod\mods" (
    echo ERROR: static_mod folder not found or incomplete.
    echo Expected folder:
    echo   "%~dp0static_mod\mods"
    echo.
    pause
    exit /b 1
)

if not exist "patch_sources" (
    echo ERROR: patch_sources folder not found.
    echo Expected folder:
    echo   "%~dp0patch_sources"
    echo.
    pause
    exit /b 1
)

REM Clear old Stage-1 harness env toggles so the canon run is reproducible.
for %%A in (
TORS HELM GLOV BOOT BELT SHLD HEAD
SWOR AXE MACE HAMM CLUB WAND SCEP STAF SPEA JAVE DAGG POLE THRO BOW XBOW ORB
RING AMUL GEM
) do (
    set "STAGE1_%%A=0"
)

REM ===== Find Python =====
where py >nul 2>nul
if %ERRORLEVEL%==0 (
    set "PY_CMD=py -3"
) else (
    where python >nul 2>nul
    if %ERRORLEVEL%==0 (
        set "PY_CMD=python"
    ) else (
        echo ERROR: Python was not found.
        echo Install Python 3, or make sure python/py is available in PATH.
        echo.
        pause
        exit /b 1
    )
)

echo Running:
echo   %PY_CMD% "%~dp0patcher.py"
echo.

%PY_CMD% "%~dp0patcher.py"
set "PATCH_EXIT=%ERRORLEVEL%"

echo.
if not "%PATCH_EXIT%"=="0" (
    echo =========================================================
    echo PATCH FAILED with exit code %PATCH_EXIT%.
    echo Check the error message above.
    echo =========================================================
    echo.
    pause
    exit /b %PATCH_EXIT%
)

echo =========================================================
echo PATCH COMPLETE
echo Output:
echo   "%~dp0output"
echo Log:
echo   "%~dp0output\log.txt"
echo =========================================================
echo.
pause
endlocal
