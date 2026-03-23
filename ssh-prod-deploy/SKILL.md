---
name: ssh-prod-deploy
description: Деплоит текущие изменения проекта на рабочий прод-сервер по SSH через локальный ключ пользователя, перезапускает сервис и проверяет health/public доступность. Использовать, когда пользователь просит выкатить в прод, отправить локальные изменения на сервер, перезапустить сервис после изменений или сразу увидеть результат в браузере.
---

# SSH деплой на прод

## Назначение

Используй этот skill, когда пользователь просит сразу отправить локальные изменения на рабочий сервер (`91.188.213.204`) и проверить, что результат доступен.

## Область применения

- Сервер: `user@91.188.213.204`
- Путь приложения на сервере: `/opt/wildduck-api`
- Имя контейнера: `wildduck-api-svc`
- Имя образа: `wildduck-api`
- Точка входа деплоя на Windows: `deploy.ps1`

## Обязательный порядок действий

Выполняй строго в этом порядке:

1. Убедись, что локальные изменения закоммичены (если пользователь просил отправить последние изменения).
2. Отправь изменения в `origin`.
3. Запусти деплой из корня проекта:
   - `powershell -ExecutionPolicy Bypass -File "./deploy.ps1"`
4. Проверь health сервиса на сервере:
   - `curl http://127.0.0.1:8000/health` (через SSH)
5. Проверь публичную доступность:
   - проверь `https://api.supermails.ru/admin/domains` (для неавторизованного доступа ожидается редирект на логин).
6. Верни короткий отчет: статус деплоя, статус перезапуска, health, проверка браузера/публичного URL.

## Команды проверки

Запускать из корня проекта локально:

```powershell
ssh -o BatchMode=yes -o ConnectTimeout=10 -i "$env:USERPROFILE/.ssh/key-for-1st-server" user@91.188.213.204 "curl -fsS http://127.0.0.1:8000/health"
```

```powershell
ssh -o BatchMode=yes -o ConnectTimeout=10 -i "$env:USERPROFILE/.ssh/key-for-1st-server" user@91.188.213.204 "docker ps --filter name=wildduck-api-svc --format 'container={{.Names}} status={{.Status}} image={{.Image}}'"
```

```powershell
ssh -o BatchMode=yes -o ConnectTimeout=10 -i "$env:USERPROFILE/.ssh/key-for-1st-server" user@91.188.213.204 "curl -sS -o /dev/null -w '%{http_code} %{redirect_url}\n' https://api.supermails.ru/admin/domains"
```

## Правила безопасности

- Никогда не выводи в ответах секреты из `.env`.
- Никогда не коммить `.env`, ключи, файлы БД и другие креды.
- Если деплой упал, явно укажи этап падения (upload/build/run/health/public-check).
- Если браузерный инструмент упал, продолжай проверку через SSH/curl и отдельно укажи, что сломался именно browser tool.

## Известные проблемы и решения

- `deploy.sh` может требовать WSL/rsync; на Windows используй `deploy.ps1`.
- CRLF может ломать удаленные `bash`-скрипты; при передаче скрипта через SSH отправляй только LF.
- Краткий `curl connection reset` сразу после рестарта контейнера допустим, если повторные проверки заканчиваются успешным health-ответом.

## Формат ответа

Используй короткие статус-пункты:

- `Commit/Pushed`: `<hash>` или `already up to date`
- `Deploy`: `success` или `failed at <stage>`
- `Container`: `<name> <status>`
- `Health`: `ok` или текст ошибки
- `Public check`: `HTTP <code> <details>`
