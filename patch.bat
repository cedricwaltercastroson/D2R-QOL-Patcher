@echo off

REM =========================================================
REM  D2R Classic++ Patcher Control Panel
REM =========================================================

REM ===== MAIN GAMEPLAY TOGGLES =====

REM Enables Expansion (LoD) base items to drop naturally in Classic (TreasureClassEx integration)
set ENABLE_EXPANSION_DROPS_IN_CLASSIC=1

REM Enables scaled "all base items" sampling in Cow Level
set COWALLBASES=1

REM Enables FULL CHAOS mode (all bases all tiers in cows)
set COWCHAOS=0


REM Enables UI override features
set UITOGGLE=0


REM ===== Base command =====
set CMD=python patcher.py --vanilla "C:\D2Rmod\vanilla" --out "C:\D2Rmod\output"


REM ===== Apply toggles =====

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


REM ===== Run patcher =====
echo.
echo Running patcher with selected options...
echo %CMD%
echo.

%CMD%

pause
