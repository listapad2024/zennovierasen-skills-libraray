# Поднимает Persona Toolbox для ChatGPT: HTTP MCP на 127.0.0.1:8767 + cloudflared quick tunnel.
# Живёт, пока открыто окно. Ctrl+C или закрытие окна гасит и сервер, и туннель.
# Нужно: Persona API на 8766 (persona-startup-check), cloudflared в PATH
#   (winget install --id Cloudflare.cloudflared).

$ErrorActionPreference = "Stop"
$Python  = "C:\Python313\python.exe"
$Here    = Split-Path -Parent $MyInvocation.MyCommand.Path
$Port    = 8767
$KeyFile = "D:\Persona\chatgpt_mcp.key"
$Log     = Join-Path $env:TEMP "persona_cloudflared.log"

$env:NO_PROXY = "localhost,127.0.0.1"; $env:no_proxy = $env:NO_PROXY
$env:PERSONA_GPT_PORT = "$Port"
$env:PERSONA_GPT_KEY_FILE = $KeyFile

# Токен API берём из того же client.env, что и бандл для Claude.
$ClientEnv = "D:\Persona\client.env"
if (Test-Path $ClientEnv) {
    Get-Content $ClientEnv | Where-Object { $_ -match '^\s*[^#].*=' } | ForEach-Object {
        $k, $v = $_ -split '=', 2
        Set-Item -Path "env:$($k.Trim())" -Value $v.Trim()
    }
}

$server = Start-Process -FilePath $Python -ArgumentList "`"$Here\server_http.py`"" -PassThru -WindowStyle Hidden
Start-Sleep 3
if ($server.HasExited) { throw "server_http.py упал при старте" }

if (Test-Path $Log) { Remove-Item $Log }
$tunnel = Start-Process -FilePath "cloudflared" `
    -ArgumentList "tunnel --no-autoupdate --url http://127.0.0.1:$Port --logfile `"$Log`"" `
    -PassThru -WindowStyle Hidden

try {
    $url = $null
    for ($i = 0; $i -lt 30 -and -not $url; $i++) {
        Start-Sleep 1
        if (Test-Path $Log) {
            $m = Select-String -Path $Log -Pattern 'https://[a-z0-9-]+\.trycloudflare\.com' | Select-Object -First 1
            if ($m) { $url = $m.Matches[0].Value }
        }
    }
    if (-not $url) { throw "cloudflared не выдал адрес, смотри $Log" }

    $key = (Get-Content $KeyFile -Raw).Trim()
    Write-Host ""
    Write-Host "Адрес коннектора для ChatGPT (никому не пересылать):"
    Write-Host "  $url/$key/mcp"
    Write-Host ""
    Write-Host "Адрес меняется при каждом запуске. Ctrl+C, чтобы закрыть доступ."
    while (-not $server.HasExited -and -not $tunnel.HasExited) { Start-Sleep 2 }
}
finally {
    foreach ($p in @($tunnel, $server)) { if ($p -and -not $p.HasExited) { Stop-Process -Id $p.Id -Force } }
    Write-Host "Persona для ChatGPT остановлена."
}
