# Persona Toolbox для Codex и ChatGPT

Исходный плагин для Claude: `D:\Persona\mcp_persona_bundle` (MCPB, stdio, FastMCP). Это тонкий прокси к Persona API `127.0.0.1:8766`, 12 инструментов, 5 из них пишут в корпус.

## Codex CLI

Локально, наружу ничего не уходит. Codex запускает тот же `run_persona_mcp.bat`, что и Claude.

1. Блок из `codex/config.toml.snippet` дописать в `%USERPROFILE%\.codex\config.toml`.
2. Папку `codex/skills/persona-toolbox` положить в `%USERPROFILE%\.codex\skills\`.
3. Проверить: `codex mcp list`, затем в Codex попросить «покажи persona_stats».

Все 12 инструментов доступны, пишущие скилл разрешает только после «да».

## ChatGPT (developer mode, свой коннектор)

ChatGPT ходит к MCP только по публичному HTTPS из облака OpenAI, поэтому нужен туннель. Версия для ChatGPT:

- `chatgpt/server_http.py` берёт инструменты из исходного бандла и публикует только 7 читающих (`persona_chat`, `persona_stats`, `persona_list_recent`, `persona_evaluate_vacancy`, `persona_evaluate_tz`, `persona_draft_response`, `persona_analyze`). Пишущих в этой версии нет совсем.
- Слушает `127.0.0.1:8767`, путь `/<секретный ключ>/mcp`. Ключ создаётся при первом запуске в `D:\Persona\chatgpt_mcp.key`. Без ключа сервер отвечает 404.
- `chatgpt/start_chatgpt.ps1` поднимает сервер и cloudflared quick tunnel, печатает адрес коннектора и держит доступ, пока открыто окно.

Как подключить:

1. Один раз: `winget install --id Cloudflare.cloudflared`.
2. Поднять Persona API (persona-startup-check), запустить `start_chatgpt.ps1`.
3. ChatGPT → Settings → Apps & Connectors → Advanced → Developer mode. Create connector: имя Persona, адрес из окна скрипта, Authentication: No authentication.
4. В чате включить коннектор Persona через «+» → Developer mode.

Ограничения:

- Quick tunnel меняет адрес при каждом запуске, коннектор придётся пересоздавать. Постоянный адрес даёт именованный туннель Cloudflare, для него нужен свой домен в Cloudflare.
- Куски кейсов из корпуса уходят в OpenAI при каждом вызове.
- Долгие инструменты (`persona_evaluate_vacancy`, `persona_analyze`) могут не уложиться в таймаут ChatGPT.
- Ключ в адресе и есть пароль. Если адрес утёк, удалить `D:\Persona\chatgpt_mcp.key` и перезапустить.
