@echo off
chcp 65001 >nul
cd /d D:\Codex\fl_watch
C:\Python313\python.exe fl_watch.py --check
claude -p "обработай очередь по QUEUE.md" --allowedTools "Read,Glob,Write" >> fl_triage.log 2>&1
