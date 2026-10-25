# Persona Toolbox для Codex и ChatGPT

Исходный плагин для Claude: `D:\Persona\mcp_persona_bundle` (MCPB, stdio, FastMCP). Это тонкий прокси к Persona API `127.0.0.1:8766`, 12 инструментов, 5 из них пишут в корпус.

## Codex CLI

Локально, наружу ничего не уходит. Codex запускает тот же `run_persona_mcp.bat`, что и Claude.

1. Блок из `codex/config.toml.snippet` дописать в `%USERPROFILE%\.codex\config.toml` (пути с прямыми слэшами).
2. Папку `codex/skills/persona-toolbox` положить в `%USERPROFILE%\.codex\skills\`.
3. Проверить: `codex mcp list`, затем в Codex попросить «покажи persona_stats».

Все 12 инструментов доступны. Четыре читающих идут без подтверждения, остальные Codex спрашивает.

## ChatGPT (developer mode, свой коннектор)

ChatGPT ходит к MCP только по публичному HTTPS из облака OpenAI, поэтому нужен туннель.

- `chatgpt/server_http.py` берёт все 12 инструментов из исходного бандла и отдаёт их по Streamable HTTP на `127.0.0.1:8767/mcp`. Читающие помечены `readOnlyHint`, пишущие нет, поэтому ChatGPT подтверждает каждую запись.
- `chatgpt/oauth_provider.py` — встроенный OAuth 2.1: PKCE, автоматическая регистрация клиента, вход по одному паролю. Регистрируются только клиенты с redirect на chatgpt.com / chat.openai.com. Access token живёт 1 час, refresh 30 дней с ротацией. После 5 неверных паролей вход блокируется на 15 минут. Состояние в `D:\Persona\chatgpt_oauth.json`: хэш пароля (scrypt) и sha256 от токенов, сами токены не хранятся.
- `chatgpt/start_chatgpt.ps1` поднимает туннель и сервер, печатает адрес коннектора и держит доступ, пока открыто окно.

Туннель. OAuth привязан к адресу, поэтому адрес нужен постоянный:

- Tailscale Funnel (по умолчанию): бесплатно, адрес вида `https://r9-pc.<tailnet>.ts.net` не меняется. Один раз: поставить Tailscale, войти, включить Funnel в админке (tailnet policy, атрибут funnel).
- cloudflared quick tunnel (`-Tunnel cloudflare`): для пробы, адрес новый при каждом запуске, коннектор придётся пересоздавать. Постоянный адрес у Cloudflare даёт только именованный туннель на своём домене.

Как подключить:

1. `python server_http.py --set-password` — задать пароль (от 12 символов).
2. Поднять Persona API (persona-startup-check), запустить `start_chatgpt.ps1`.
3. ChatGPT → Settings → Apps & Connectors → Advanced → Developer mode. Create connector: имя Persona, адрес из окна скрипта, Authentication: OAuth. Откроется страница Persona, ввести пароль.
4. В чате включить коннектор Persona через «+» → Developer mode.

Отключить: `python server_http.py --revoke-all` (или `--set-password`, он тоже гасит все подключения).

Ограничения:

- Куски кейсов из корпуса уходят в OpenAI при каждом вызове.
- Долгие инструменты (`persona_evaluate_vacancy`, `persona_analyze`) могут не уложиться в таймаут ChatGPT.
- Токены выдаются любому, кто знает пароль, поэтому пароль длинный и только у Михаила.
