#!/usr/bin/env python3
"""FL Watch: каждые 5 минут смотрит свежую ленту FL.ru, оценивает заказ по стеку Михаила и шлет стоящие в Telegram.

Запуск:  python fl_watch.py            # бесконечный цикл, раз в INTERVAL секунд, только 9:00-21:00 по Минску
         python fl_watch.py --once     # один проход (для проверки)
Настройки: переменные окружения TG_BOT_TOKEN и TG_CHAT_ID или файл fl_watch.env рядом (KEY=VALUE).
Нужен freelance_radar.py в той же папке (парсер ленты FL.ru). Только стандартная библиотека.
"""
import datetime as dt
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import freelance_radar as fr  # noqa: E402

INTERVAL = 300
WORK_HOURS = (9, 21)          # по Минску (решение Михаила 27.10)
MINSK = dt.timezone(dt.timedelta(hours=3))
MAX_AGE_H = 3                 # старше не смотрим: хорошие заказы закрываются за часы
THRESHOLD = 5                 # порог «стоящего» заказа
STATE = HERE / "fl_watch_state.json"
LOG = HERE / "fl_watch.log"

# стек Михаила: (вес, шаблон, подпись для причины)
STACK = [
    (4, r"python|питон", "Python"),
    (4, r"парс\w*|scrap\w*|сбор данн\w*|выгрузк\w*", "парсинг"),
    (4, r"\bai\b|\bии\b|нейросет\w*|gpt|llm|claude|openai|агент\w*|agent\w*|\brag\b|эмбеддинг\w*", "AI/LLM"),
    (3, r"телеграм\w*|telegram|\bтг\b|чат-?бот\w*|\bбот\w*", "Telegram/боты"),
    (3, r"playwright|selenium|puppeteer|браузер\w*|zenno\w*", "браузерная автоматизация"),
    (3, r"автоматиз\w*|automat\w*|n8n|make\.com|zapier|rpa", "автоматизация"),
    (2, r"\bapi\b|интеграц\w*|webhook|вебхук\w*", "API/интеграции"),
    (2, r"clickhouse|postgres\w*|\bsql\b|баз\w* данн\w*|metabase|дашборд\w*|dashboard|\betl\b", "данные/SQL"),
    (2, r"\bcrm\b|amocrm|retailcrm|google sheets|гугл.?таблиц\w*|excel", "CRM/таблицы"),
    (1, r"mvp|fastapi|docker", "MVP/бэкенд"),
]
STACK = [(w, re.compile(p, re.I), n) for w, p, n in STACK]
MINUS = [
    (-8, r"\b1[сc]\b|битрикс|bitrix|wordpress|вордпресс|tilda|тильд\w*|laravel|\bphp\b|ios|android|flutter|unity|unreal|"
         r"c#|\.net|java\b|дизайн\w*|логотип\w*|верстк\w*|видео|монтаж\w*|\b3d\b|отзыв\w*|накрут\w*|"
         r"учебн\w*|курсов\w*|диплом\w*|\bвуз\w*|блогер\w*|рендер\w*|рендинг\w*|иллюстрац\w*|smm|таргет\w*|\bseo\b|ларавел\w*|\bгта\b|\bмод\b|игров\w*|подписчик\w*|радиолокац\w*|проектн\w* документац\w*", "не стек"),
    (-30, r"авторег\w*|антидетект|обход\w* (?:защит|cloudflare|капч)|фарм\w*|резидентск\w* прокси|персональн\w* данн\w* физ|stealth|анти-?фрод|добавить контакт\w*", "серое"),
]
MINUS = [(w, re.compile(p, re.I), n) for w, p, n in MINUS]


def load_env():
    f = HERE / "fl_watch.env"
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def score(v):
    blob = f"{v['title']} {v['title']} {v['text'][:600]}"  # заголовок весит вдвое
    s, why = 0, []
    for w, rx, name in STACK:
        if rx.search(blob):
            s += w
            why.append(name)
    for w, rx, name in MINUS:
        if rx.search(blob):
            s += w
            why.append(name)
    age = v.get("age_h")
    if age is not None:
        s += 2 if age <= 0.5 else 1 if age <= 1.5 else 0
    resp = v.get("responses") or 0
    s += 2 if resp < 5 else 0 if resp < 15 else -3
    rub = v.get("budget_rub")
    if rub:
        s += 3 if rub >= 50_000 else 2 if rub >= 20_000 else -1 if rub < 5_000 else 0
    if v.get("kind", "").startswith("Вакансия"):
        why.append("вакансия")
    return s, why


def send(text):
    tok, chat = os.environ.get("TG_BOT_TOKEN"), os.environ.get("TG_CHAT_ID")
    if not (tok and chat):
        print(text)
        return
    data = urllib.parse.urlencode({"chat_id": chat, "text": text, "disable_web_page_preview": "true"}).encode()
    urllib.request.urlopen(f"https://api.telegram.org/bot{tok}/sendMessage", data=data, timeout=20)


def once():
    state = json.loads(STATE.read_text()) if STATE.exists() else {"seen": []}
    seen = set(state["seen"])
    sent = set(json.loads((HERE / "sent.json").read_text())) if (HERE / "sent.json").exists() else set()
    now = dt.datetime.now(fr.MSK)
    fr.SINCE = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=MAX_AGE_H)
    rates = fr.rates()
    out = {}
    fr.fl_parse(fr.get("https://www.fl.ru/projects/?kind=1"), out, now)
    hits = []
    for v in out.values():
        if v["id"] in seen or v["id"] in sent:
            continue
        seen.add(v["id"])
        if v["published"]:
            v["age_h"] = round((dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(v["published"])).total_seconds() / 3600, 1)
            if v["age_h"] > MAX_AGE_H:
                continue
        if v["budget"] and v["currency"] in rates:
            v["budget_rub"] = round(v["budget"] * rates[v["currency"]])
        s, why = score(v)
        if s < THRESHOLD:
            continue
        try:
            if fr.fl_details(v) == "taken":
                continue
            s, why = score(v)  # переоценка по полному ТЗ
        except OSError:
            pass
        if s >= THRESHOLD:
            hits.append((s, why, v))
    state["seen"] = sorted(seen)[-5000:]
    STATE.write_text(json.dumps(state))
    for s, why, v in sorted(hits, key=lambda x: -x[0]):
        b = f"{v['budget_rub']:,} ₽".replace(",", " ") if v.get("budget_rub") else "бюджет не указан"
        send(f"FL: {v['title'][:120]}\nОценка {s}: {', '.join(why)}\n{b} · {v.get('responses') or 0} откл · "
             f"{v.get('age_h')} ч назад\n{v['text'][:300]}\n{v['url']}")
    with LOG.open("a", encoding="utf-8") as f:
        f.write(f"{now:%Y-%m-%d %H:%M} лента {len(out)}, отправлено {len(hits)}\n")
    return len(hits)


def main():
    load_env()
    if "--once" in sys.argv:
        print("отправлено:", once())
        return
    while True:
        h = dt.datetime.now(MINSK).hour
        if WORK_HOURS[0] <= h < WORK_HOURS[1]:
            try:
                once()
            except Exception as e:  # сеть или FL упали: пишем и ждем следующий круг
                with LOG.open("a", encoding="utf-8") as f:
                    f.write(f"{dt.datetime.now(MINSK):%Y-%m-%d %H:%M} ошибка {type(e).__name__}: {e}\n")
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
