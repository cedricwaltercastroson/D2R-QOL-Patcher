@echo off

REM ===== Toggle switches =====
set COWTEST=0
set UITOGGLE=0

REM ===== Run patcher =====
if "%COWTEST%"=="1" (
    if "%UITOGGLE%"=="1" (
        python patcher.py --vanilla "C:\D2Rmod\vanilla" --out "C:\D2Rmod\output" --cowtest --enable-ui
    ) else (
        python patcher.py --vanilla "C:\D2Rmod\vanilla" --out "C:\D2Rmod\output" --cowtest
    )
) else (
    if "%UITOGGLE%"=="1" (
        python patcher.py --vanilla "C:\D2Rmod\vanilla" --out "C:\D2Rmod\output" --enable-ui
    ) else (
        python patcher.py --vanilla "C:\D2Rmod\vanilla" --out "C:\D2Rmod\output"
    )
)

pause
