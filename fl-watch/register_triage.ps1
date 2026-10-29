# Регистрирует задачу «FL-Triage»: каждые 10 минут с 9 до 21 один проход claude -p по QUEUE.md.
# Запуск один раз: powershell -ExecutionPolicy Bypass -File D:\Codex\fl_watch\register_triage.ps1
$dir = "D:\Codex\fl_watch"
$claude = (Get-Command claude -ErrorAction Stop).Source
$cmd = "Set-Location '$dir'; & '$claude' -p 'обработай очередь по QUEUE.md' --allowedTools 'Read,Glob,Write' *>> '$dir\fl_triage.log'"
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -WindowStyle Hidden -Command `"$cmd`""
$trigger = New-ScheduledTaskTrigger -Daily -At 9:00
$trigger.Repetition = (New-ScheduledTaskTrigger -Once -At 9:00 -RepetitionInterval (New-TimeSpan -Minutes 10) -RepetitionDuration (New-TimeSpan -Hours 12)).Repetition
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 9) -StartWhenAvailable
Register-ScheduledTask -TaskName "FL-Triage" -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
Start-ScheduledTask -TaskName "FL-Triage"
"FL-Triage зарегистрирована и запущена"
