---
name: persona-toolbox
description: Личный RAG Михаила (Persona) через MCP-сервер persona. Звать, когда нужны его кейсы, навыки, стиль текстов, оценка вакансии или ТЗ, черновик отклика.
---

# Persona Toolbox в Codex

MCP-сервер `persona` (D:\Persona\mcp_persona_bundle) ходит в Persona API 127.0.0.1:8766, тот в Qdrant 127.0.0.1:8333.

## Перед первым вызовом

Проверь оба сервиса одной командой PowerShell:

```powershell
$q = try { Invoke-RestMethod http://localhost:8333/healthz -TimeoutSec 2 | Out-Null; "ok" } catch { "down" }
$p = try { (Invoke-RestMethod http://127.0.0.1:8766/health -TimeoutSec 2 -NoProxy).status } catch { "down" }
"qdrant=$q persona=$p"
```

Если что-то down, подними по D:\Persona\skills\persona-startup-check\SKILL.md (run_qdrant.ps1, run_api.ps1). API грузит bge-m3 20–40 с.

## Какой инструмент брать

Только чтение, можно звать сразу:
- `persona_chat(query, top_k, platform?)` — поиск по кейсам и фактам, основной. Синтез делаешь сам.
- `persona_stats()`, `persona_list_recent(category, limit)` — что лежит в базе.
- `persona_evaluate_vacancy(vacancy_text, url?, salary_text?)` — ОТКЛИКАТЬСЯ / УТОЧНИТЬ / МИМО, 75–165 с.
- `persona_evaluate_tz(task_description)` — оценка фриланс-заказа (на вакансии всегда «НЕ БРАТЬ», для них не использовать).
- `persona_draft_response(task_description, platform)` — черновик отклика в стиле Михаила.
- `persona_analyze(question)` — медленная аналитика по всему корпусу, до 10 минут.

Пишут в корпус, только после явного «да» Михаила в этом чате:
- `persona_remember`, `persona_save_feedback`, `persona_record_pass`, `persona_record_response`, `persona_record_session_note`.

## Правила

- Ответ `persona_evaluate_vacancy` может содержать личное. Работодателю и наружу его не отдавать.
- Корпус целиком не выгружать и не копировать в облако.
- Токен API лежит в D:\Persona\client.env, его не печатать.
