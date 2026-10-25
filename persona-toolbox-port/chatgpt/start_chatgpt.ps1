# Поднимает Persona Toolbox для ChatGPT: HTTP MCP с OAuth на 127.0.0.1:8767 + туннель.
# Доступ живёт, пока открыто окно. Ctrl+C или закрытие окна гасит и сервер, и туннель.
#
#   .\start_chatgpt.ps1                    Tailscale Funnel, постоянный адрес (основной вариант)
#   .\start_chatgpt.ps1 -Tunnel cloudflare cloudflared quick tunnel, адрес новый при каждом запуске
#
# Нужно: Persona API на 8766 (persona-startup-check), пароль (python server_http.py --set-password),
# tailscale с включённым Funnel или cloudflared в PATH.

param([ValidateSet("tailscale", "cloudflare")] [string]$Tunnel = "tailscale")

$ErrorActionPreference = "Stop"
$Python = "C:\Python313\python.exe"
$Here   = Split-Path -Parent $MyInvocation.MyCommand.Path
$Port   = 8767
$Log    = Join-Path $env:TEMP "persona_tunnel.log"

$env:NO_PROXY = "localhost,127.0.0.1"; $env:no_proxy = $env:NO_PROXY
$env:PERSONA_GPT_PORT = "$Port"

# Токен API берём из того же client.env, что и бандл для Claude.
$ClientEnv = "D:\Persona\client.env"
if (Test-Path $ClientEnv) {
    Get-Content $ClientEnv | Where-Object { $_ -match '^\s*[^#].*=' } | ForEach-Object {
        $k, $v = $_ -split '=', 2
        Set-Item -Path "env:$($k.Trim())" -Value $v.Trim()
    }
}

$tunnelProc = $null
$server = $null
try {
    # 1. Туннель. Адрес нужен серверу до старта: он же issuer для OAuth.
    if ($Tunnel -eq "tailscale") {
        $dns = ((tailscale status --json | ConvertFrom-Json).Self.DNSName).TrimEnd(".")
        if (-not $dns) { throw "tailscale не залогинен" }
        tailscale funnel --bg $Port | Out-Null
        $url = "https://$dns"
    } else {
        if (Test-Path $Log) { Remove-Item $Log }
        $tunnelProc = Start-Process -FilePath "cloudflared" `
            -ArgumentList "tunnel --no-autoupdate --url http://127.0.0.1:$Port --logfile `"$Log`"" `
            -PassThru -WindowStyle Hidden
        $url = $null
        for ($i = 0; $i -lt 30 -and -not $url; $i++) {
            Start-Sleep 1
            if (Test-Path $Log) {
                $m = Select-String -Path $Log -Pattern 'https://[a-z0-9-]+\.trycloudflare\.com' | Select-Object -First 1
                if ($m) { $url = $m.Matches[0].Value }
            }
        }
        if (-not $url) { throw "cloudflared не выдал адрес, смотри $Log" }
    }

    # 2. Сервер.
    $env:PERSONA_PUBLIC_URL = $url
    $server = Start-Process -FilePath $Python -ArgumentList "`"$Here\server_http.py`"" -PassThru -WindowStyle Hidden
    Start-Sleep 3
    if ($server.HasExited) { throw "server_http.py упал при старте (пароль задан?)" }

    Write-Host ""
    Write-Host "Адрес коннектора для ChatGPT:"
    Write-Host "  $url/mcp"
    Write-Host "Authentication: OAuth. При подключении откроется страница входа, там твой пароль."
    Write-Host ""
    Write-Host "Ctrl+C, чтобы закрыть доступ."
    while (-not $server.HasExited -and (-not $tunnelProc -or -not $tunnelProc.HasExited)) { Start-Sleep 2 }
}
finally {
    foreach ($p in @($tunnelProc, $server)) { if ($p -and -not $p.HasExited) { Stop-Process -Id $p.Id -Force } }
    if ($Tunnel -eq "tailscale") { tailscale funnel --https=443 off 2>$null | Out-Null }
    Write-Host "Persona для ChatGPT остановлена."
}
