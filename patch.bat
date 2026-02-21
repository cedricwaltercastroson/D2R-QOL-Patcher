@echo off

REM ===== Toggle switches =====
set COWTEST=0
set UITOGGLE=0
set PHASE2=1

REM ===== Base command =====
set CMD=python patcher.py --vanilla "C:\D2Rmod\vanilla" --out "C:\D2Rmod\output"

REM ===== Apply toggles =====
if "%COWTEST%"=="1" (
    set CMD=%CMD% --cowtest
)

if "%UITOGGLE%"=="1" (
    set CMD=%CMD% --enable-ui
)

if "%PHASE2%"=="1" (
    set CMD=%CMD% --phase2drops
)

REM ===== Run patcher =====
%CMD%

pause