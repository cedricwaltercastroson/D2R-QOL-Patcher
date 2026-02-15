set COWTEST=0
REM Set COWTEST=0 to disable Cow test drops after testing
if "%COWTEST%"=="1" (python patcher.py --vanilla "C:\D2Rmod\vanilla" --out "C:\D2Rmod\output" --cowtest) else (python patcher.py --vanilla "C:\D2Rmod\vanilla" --out "C:\D2Rmod\output")
pause
