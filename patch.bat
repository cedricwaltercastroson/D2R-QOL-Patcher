set COWTEST=1
REM Set COWTEST=0 to disable Cow test drops after testing
if "%COWTEST%"=="1" (python patcher.py --vanilla "C:\vanilla" --out "C:\output" --cowtest) else (python patcher.py --vanilla "C:\vanilla" --out "C:\output")
pause
