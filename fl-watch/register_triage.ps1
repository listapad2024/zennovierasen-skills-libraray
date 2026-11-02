# FL-Triage: daily from 09:00, every 10 minutes for 12 hours, one claude -p pass over QUEUE.md (fl_triage.cmd).
# ASCII only: Windows PowerShell 5 reads a BOM-less file as ANSI, Cyrillic broke the quotes (30.10).
# Run once from an elevated PowerShell (recreating an existing task needs admin):
#   powershell -ExecutionPolicy Bypass -File D:\Codex\fl_watch\register_triage.ps1
# DAILY + /RI /DU: "/SC MINUTE /ET" created a one-time trigger and triage stopped on 30.10.
schtasks /Create /F /TN "FL-Triage" /SC DAILY /ST 09:00 /RI 10 /DU 12:00 /TR "D:\Codex\fl_watch\fl_triage.cmd"
schtasks /Run /TN "FL-Triage"
