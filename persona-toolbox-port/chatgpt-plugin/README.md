# Плагин Persona Toolbox для ChatGPT (десктоп) и Codex

Работает локально, без доступа из интернета: ChatGPT на ПК сам запускает `D:\Persona\mcp_persona_bundle\run_persona_mcp.bat`, тот ходит в Persona API 127.0.0.1:8766.

В плагине:
- `mcp.json` — MCP-сервер persona (тот же, что в Claude), 12 инструментов;
- `skills/persona-toolbox` — как пользоваться Persona, что читает и что пишет;
- `skills/vacancy-helper` — оценка вакансий, подбор кейсов, письмо по шаблону Михаила.

## Установка

1. Скопировать папку `persona-toolbox` в `%USERPROFILE%\.codex\plugins\persona-toolbox`.
2. Скопировать `marketplace.json` в `%USERPROFILE%\.codex\plugins\.agents\plugins\marketplace.json` и зарегистрировать:
   `codex plugin marketplace add %USERPROFILE%\.codex\plugins`
3. Перезапустить ChatGPT, открыть плагины (режим Work или Codex), установить Persona Toolbox.
4. Проверка: в чате `/mcp` показывает persona, затем «покажи persona_stats».

Если в `config.toml` уже есть `[mcp_servers.persona]`, его убрать, чтобы не было двух одинаковых серверов. Политику подтверждений переносить в `plugins.persona-toolbox.mcp_servers.persona`.

## Что реально стоит на R9-PC (codex-cli 0.145.0)

Установлено как persona-toolbox@personal 0.5.1 в существующий маркетплейс `%USERPROFILE%\.agents\plugins\marketplace.json` (путь `./plugins/persona-toolbox`, исходник в `%USERPROFILE%\plugins\persona-toolbox`). Формат там проверенный для 0.145: `.codex-plugin/plugin.json` с полями skills и mcpServers плюс `.mcp.json`; вариант из этой папки (plugin.json в корне, mcp.json без точки) на 0.145 не проверялся. Архив для переустановки: `D:\Codex\persona-toolbox-codex-0.5.1.zip`.

Подтверждения для читающих инструментов задаются в `[plugins."persona-toolbox@personal".mcp_servers.persona.tools.<name>]`, `approval_mode = "approve"`.
