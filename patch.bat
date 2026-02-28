@echo off

REM =========================================================
REM  D2R Classic++ Patcher Control Panel (R120 Canonical)
REM =========================================================
REM
REM Usage:
REM   patch.bat              -> Stage 0 baseline (all toggles 0)
REM   patch.bat stage0       -> Stage 0 baseline
REM   patch.bat stage1       -> EXP ladder stage 1 (port layer only)
REM   patch.bat stage2       -> EXP ladder stage 2 (port + cow sampling)
REM   patch.bat stage3       -> EXP ladder stage 3 (port + cow + tc-enrich diagnostic)
REM   patch.bat stage4       -> EXP ladder stage 4 (stage3 + chaos)
REM   patch.bat ui           -> Stage 0 baseline + UI overrides
REM

setlocal EnableExtensions EnableDelayedExpansion

REM ===== Paths (edit to your setup) =====
set VANILLA_ROOT=C:\D2Rmod\vanilla
set OUT_ROOT=C:\D2Rmod\output

REM ===== Sanity checks =====
if not exist "%VANILLA_ROOT%\data\global\excel" (
    echo.
    echo ERROR: VANILLA_ROOT does not look like a valid vanilla dump.
    echo Expected folder missing: "%VANILLA_ROOT%\data\global\excel"
    echo Please edit VANILLA_ROOT at the top of patch.bat.
    echo.
    pause
    exit /b 1
)

REM =========================================================
REM  Default Baseline Toggles (locked until user says otherwise)
REM =========================================================
set ENABLE_EXPANSION_DROPS_IN_CLASSIC=1
set COWALLBASES=1
set COWCHAOS=1
set EXP_DROPS_STAGE=4
set UITOGGLE=0

REM =========================================================
REM  Stage-1 Cow Harness Toggles (process-of-elimination)
REM  Set EXACTLY ONE of these to 1. Leave all 0 to disable.
REM  (Read by patcher.py via env vars; no extra args needed)
REM =========================================================
set STAGE1_TORS=0
set STAGE1_HELM=0
set STAGE1_GLOV=0
set STAGE1_BOOT=0
set STAGE1_BELT=0
set STAGE1_SHLD=0
set STAGE1_HEAD=0

set STAGE1_SWOR=0
set STAGE1_AXE=0
set STAGE1_MACE=0
set STAGE1_HAMM=0

set STAGE1_CLUB=0

set STAGE1_WAND=0
set STAGE1_SCEP=0
set STAGE1_STAF=0
set STAGE1_SPEA=0
set STAGE1_JAVE=0
set STAGE1_DAGG=0
set STAGE1_POLE=0
set STAGE1_THRO=0
set STAGE1_BOW=0
set STAGE1_XBOW=0
set STAGE1_ORB=0

set STAGE1_RING=0
set STAGE1_AMUL=0
set STAGE1_GEM=0


REM ===== Parse mode argument =====
set MODE=%~1
if "%MODE%"=="" set MODE=stage0
if /I "%MODE%"=="baseline" set MODE=stage0

if /I "%MODE%"=="stage0" (
    REM Baseline already set
) else if /I "%MODE%"=="stage1" (
    set ENABLE_EXPANSION_DROPS_IN_CLASSIC=1
    set EXP_DROPS_STAGE=1
) else if /I "%MODE%"=="stage2" (
    set ENABLE_EXPANSION_DROPS_IN_CLASSIC=1
    set EXP_DROPS_STAGE=2
    set COWALLBASES=1
) else if /I "%MODE%"=="stage3" (
    set ENABLE_EXPANSION_DROPS_IN_CLASSIC=1
    set EXP_DROPS_STAGE=3
    set COWALLBASES=1
) else if /I "%MODE%"=="stage4" (
    set ENABLE_EXPANSION_DROPS_IN_CLASSIC=1
    set EXP_DROPS_STAGE=4
    set COWALLBASES=1
    set COWCHAOS=1
) else if /I "%MODE%"=="ui" (
    set UITOGGLE=1
) else (
    echo.
    echo Unknown mode: "%MODE%"
    echo.
    echo Valid modes: stage0, stage1, stage2, stage3, stage4, ui
    echo.
    pause
    exit /b 1
)

REM ===== Build command =====
set CMD=python patcher.py --vanilla "%VANILLA_ROOT%" --out "%OUT_ROOT%"

if "%ENABLE_EXPANSION_DROPS_IN_CLASSIC%"=="1" (
    set CMD=%CMD% --enable-expansion-drops-in-classic
)

if "%COWCHAOS%"=="1" (
    set CMD=%CMD% --cow-all-bases-full
) else (
    if "%COWALLBASES%"=="1" (
        set CMD=%CMD% --cow-all-bases
    )
)

if "%UITOGGLE%"=="1" (
    set CMD=%CMD% --enable-ui
)

echo.
echo =========================================================
echo Mode: %MODE%
echo ENABLE_EXPANSION_DROPS_IN_CLASSIC=%ENABLE_EXPANSION_DROPS_IN_CLASSIC%
echo EXP_DROPS_STAGE=%EXP_DROPS_STAGE%
echo COWALLBASES=%COWALLBASES%
echo COWCHAOS=%COWCHAOS%
echo UITOGGLE=%UITOGGLE%
echo VANILLA_ROOT=%VANILLA_ROOT%
echo OUT_ROOT=%OUT_ROOT%
echo =========================================================
echo.

echo Running patcher with selected options...
echo.
echo ===== Stage 1 Selection =====
for %%A in (
TORS HELM GLOV BOOT BELT SHLD HEAD
SWOR AXE MACE HAMM CLUB WAND SCEP STAF SPEA JAVE DAGG POLE THRO BOW XBOW ORB
RING AMUL GEM
) do (
    if "!STAGE1_%%A!"=="1" echo Enabled: %%A
)
echo ==============================
echo.
echo %CMD%
echo.

%CMD%

echo.
echo Done.
pause
endlocal
