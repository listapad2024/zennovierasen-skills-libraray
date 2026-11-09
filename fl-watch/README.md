# FL Watch
Раз в 5 минут с 9:00 до 21:00 по Минску смотрит свежую ленту FL.ru, оценивает заказ по стеку Михаила
(Python, парсинг, AI/LLM, Telegram-боты, браузерная автоматизация, API, данные) и шлет стоящие (оценка от 5) в Telegram.
Запуск: pythonw fl_watch.py (цикл) или python fl_watch.py --once. Токен: fl_watch.env (TG_BOT_TOKEN=..., TG_CHAT_ID=...).
Лог: fl_watch.log, уже виденные заказы: fl_watch_state.json, свои отклики: sent.json.
