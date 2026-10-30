# Регистрирует задачу «FL-Triage»: каждые 10 минут с 9 до 21 один проход claude -p по QUEUE.md (fl_triage.cmd).
# Через schtasks: командлеты ScheduledTask на R9-PC падают на ошибке WMI. DAILY + /RI 10 /DU 12:00: вариант /SC MINUTE /ET 29.10 создал разовый триггер, и 30.10 оценка не запустилась.
# Запуск один раз: powershell -ExecutionPolicy Bypass -File D:\Codex\fl_watch\register_triage.ps1
schtasks /Create /F /TN "FL-Triage" /SC DAILY /ST 09:00 /RI 10 /DU 12:00 /TR "D:\Codex\fl_watch\fl_triage.cmd"
schtasks /Run /TN "FL-Triage"
