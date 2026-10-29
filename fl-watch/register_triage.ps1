# Регистрирует задачу «FL-Triage»: каждые 10 минут с 9 до 21 один проход claude -p по QUEUE.md (fl_triage.cmd).
# Через schtasks: командлеты ScheduledTask на R9-PC падают на ошибке WMI.
# Запуск один раз: powershell -ExecutionPolicy Bypass -File D:\Codex\fl_watch\register_triage.ps1
schtasks /Create /F /TN "FL-Triage" /SC MINUTE /MO 10 /ST 09:00 /ET 21:00 /TR "D:\Codex\fl_watch\fl_triage.cmd"
schtasks /Run /TN "FL-Triage"
