@echo off
rem ASCII only: Cyrillic in a .cmd under chcp 65001 shifts cmd's line offsets
cd /d D:\Codex\fl_watch
C:\Python313\python.exe fl_watch.py --check
rem run Claude only if triage has a json without a verdict
powershell -NoProfile -Command "if (Get-ChildItem 'triage' -Filter *.json -ErrorAction SilentlyContinue | Where-Object { -not (Test-Path ('verdict\' + $_.BaseName + '.txt')) }) { exit 0 } else { exit 1 }"
if errorlevel 1 exit /b 0
rem proxy for the API, taken from settings.json even if that file is broken
for /f "usebackq delims=" %%p in (`C:\Python313\python.exe get_proxy.py`) do set "HTTPS_PROXY=%%p"
set "NO_PROXY=localhost,127.0.0.1,::1"
echo [%date% %time%] triage run >> fl_triage.log
claude -p --model claude-sonnet-5-5 "Process the queue as described in QUEUE.md. Write all verdicts and outbox files in Russian." --allowedTools "Read,Glob,Write" >> fl_triage.log 2>&1
