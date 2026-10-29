#!/usr/bin/env python3
"""FL Watch: каждые 5 минут смотрит свежую ленту FL.ru и кладет новые заказы в triage/ для CLI-сессии Claude Code.
Claude оценивает их по QUEUE.md и кладет подходящие в outbox/ md-файлом (оценка + варианты отклика),
скрипт шлет эти файлы документом в Telegram. Мусор не шлется, на FL.ru ничего не отправляется (решение 28.10).

Запуск:  python fl_watch.py            # бесконечный цикл, раз в INTERVAL секунд, только 9:00-21:00 по Минску
         python fl_watch.py --once     # один проход (для проверки)
Настройки: переменные окружения TG_BOT_TOKEN, TG_CHAT_ID и необязательный FL_COOKIE (лента под входом) или файл fl_watch.env рядом (KEY=VALUE).
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
STATE = HERE / "fl_watch_state.json"
LOG = HERE / "fl_watch.log"
REPORTS = HERE / "reports"
QUEUE = HERE / "queue"        # задачи на отчет для CLI-сессии
OUTBOX = HERE / "outbox"      # готовые отчеты CLI-сессии, скрипт шлет их в Telegram
TRIAGE = HERE / "triage"      # все свежие заказы без фильтра: CLI-сессия сверяет их с Persona (Михаил 28.10)
VERDICT = HERE / "verdict"    # CLI-сессия пишет сюда <имя>.txt (ok/skip), скрипт по нему убирает заказ из очереди
STALE_H = 4                   # заказ старше этого без решения уже неинтересен: в triage/stale
PERSONA_URL = "http://127.0.0.1:8766/retrieve"
PERSONA_ENV = Path(os.environ.get("PERSONA_ENV", r"D:\Persona\client.env"))

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
    (-30, r"авторег\w*|антидетект|обход\w* (?:защит|cloudflare|капч)|фарм\w*|резидентск\w* прокси|персональн\w* данн\w* физ|stealth|анти-?фрод|добавить контакт\w*|доверенн\w* лиц\w*|верификац\w* (?:учетн|аккаунт)\w*|аренд\w* (?:учетн|аккаунт)\w*", "серое"),
]
MINUS = [(w, re.compile(p, re.I), n) for w, p, n in MINUS]


def load_env():
    f = HERE / "fl_watch.env"
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def use_fl_cookie():
    """FL_COOKIE в fl_watch.env: лента и страницы заказов FL.ru читаются под входом Михаила (решение 28.10)."""
    cookie = os.environ.get("FL_COOKIE", "").strip()
    if not cookie:
        return
    plain = fr.get

    def get(url, timeout=25, tries=3):
        if "fl.ru" not in url:
            return plain(url, timeout, tries)
        req = urllib.request.Request(url, headers={"User-Agent": fr.UA, "Accept-Language": "ru,en;q=0.8", "Cookie": cookie})
        for n in range(tries):
            try:
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    return r.read().decode("utf-8", "replace")
            except OSError:
                if n == tries - 1:
                    raise
                time.sleep(2 * (n + 1))
    fr.get = get


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


def tg(method, **params):
    tok = os.environ.get("TG_BOT_TOKEN")
    data = urllib.parse.urlencode({k: json.dumps(v) if isinstance(v, (dict, list)) else v
                                   for k, v in params.items()}).encode()
    with urllib.request.urlopen(f"https://api.telegram.org/bot{tok}/{method}", data=data, timeout=40) as r:
        return json.loads(r.read())


def send(text, url=None, oid=None, reply_to=None):
    if not (os.environ.get("TG_BOT_TOKEN") and os.environ.get("TG_CHAT_ID")):
        print(text)
        return
    p = {"chat_id": os.environ["TG_CHAT_ID"], "text": text[:4000], "disable_web_page_preview": "true"}
    if url and oid:  # у каждого заказа две кнопки: ссылка на заказ и разбор с черновиком отклика
        p["reply_markup"] = {"inline_keyboard": [[{"text": "Открыть на FL.ru", "url": url},
                                                  {"text": "Сделать отчёт", "callback_data": f"r:{oid}"}]]}
    if reply_to:
        p["reply_to_message_id"] = reply_to
    tg("sendMessage", **p)


def send_doc(path, caption, reply_to=None):
    """md-файл с вариантами отклика документом в Telegram: Михаил открывает и отправляет отклик сам (решение 28.10)."""
    tok, chat = os.environ.get("TG_BOT_TOKEN"), os.environ.get("TG_CHAT_ID")
    if not (tok and chat):
        print(caption, path)
        return
    b = "flwatch" + str(int(time.time() * 1000))
    parts = [("chat_id", chat), ("caption", caption[:1000])] + ([("reply_to_message_id", str(reply_to))] if reply_to else [])
    body = b"".join(f"--{b}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode() for k, v in parts)
    body += (f"--{b}\r\nContent-Disposition: form-data; name=\"document\"; filename=\"{Path(path).name}\"\r\n"
             f"Content-Type: text/markdown\r\n\r\n").encode() + Path(path).read_bytes() + f"\r\n--{b}--\r\n".encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{tok}/sendDocument", data=body,
                                 headers={"Content-Type": f"multipart/form-data; boundary={b}"})
    urllib.request.urlopen(req, timeout=60).read()


def enqueue(oid, reply_to):
    """Кнопка «Сделать отчёт»: кладем задачу в queue/. Отчет пишет отдельная CLI-сессия Claude Code по QUEUE.md,
    сам скрипт к Claude не обращается (решение Михаила 28.10)."""
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    v = state.get("orders", {}).get(oid)
    if not v:
        send(f"Заказ {oid} не найден в памяти бота.", reply_to=reply_to)
        return
    try:
        if fr.fl_details(v) == "taken":
            send(f"{v['title'][:100]}\nИсполнитель уже выбран, откликаться поздно.", reply_to=reply_to)
            return
    except OSError:
        pass
    v["score"], v["why"] = score(v)
    v["reply_to"] = reply_to
    QUEUE.mkdir(exist_ok=True)
    (QUEUE / f"{oid.replace(':', '_')}.json").write_text(json.dumps(v, ensure_ascii=False, indent=1), encoding="utf-8")
    send("Заказ в очереди на отчёт.", reply_to=reply_to)


def persona_hits(v):
    """Похожие кейсы из Persona для CLI-сессии: сессии тогда хватает чтения и записи файлов, без shell."""
    try:
        tok = next((l.split("=", 1)[1].strip() for l in PERSONA_ENV.read_text(encoding="utf-8").splitlines()
                    if l.split("=", 1)[0].strip().upper().endswith("TOKEN")), "")
        body = json.dumps({"query": f"{v['title']}. {v['text'][:500]}", "top_k": 5}).encode()
        req = urllib.request.Request(PERSONA_URL, data=body, headers={"Content-Type": "application/json", "X-API-Token": tok})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # localhost мимо прокси
        with opener.open(req, timeout=30) as r:
            return r.read().decode("utf-8", "replace")[:3000]
    except Exception as e:
        return f"Persona недоступна: {type(e).__name__}"


def sweep_triage():
    """Решения CLI-сессии (verdict/*.txt) убирают заказ в triage/done, заказы без решения дольше STALE_H часов в triage/stale."""
    if not TRIAGE.exists():
        return
    for sub in ("done", "stale"):
        (TRIAGE / sub).mkdir(exist_ok=True)
    for f in TRIAGE.glob("*.json"):
        vd = VERDICT / (f.stem + ".txt")
        if vd.exists():
            f.replace(TRIAGE / "done" / f.name)
            vd.unlink()
        elif time.time() - f.stat().st_mtime > STALE_H * 3600:
            f.replace(TRIAGE / "stale" / f.name)


def order_caption(text, o):
    """Подпись как у облачного прогона (Михаил 29.10, один формат): заголовок: бюджет или наша оценка, срок, отклики, ссылка."""
    lines = [l.strip() for l in text.strip().splitlines() if l.strip()]
    title = next((l.lstrip("# ").strip() for l in lines if l.startswith("# ")), o.get("title") or "Заказ FL.ru")
    total = next((l.split(":", 1)[1].strip() for l in lines if l.lower().startswith("итог:")), "")
    rub = o.get("budget_rub")
    parts = [f"бюджет {rub:,} ₽".replace(",", " ") if rub else "", f"наша оценка {total}" if total else ""]
    if o.get("responses") is not None:
        parts.append(f"{o['responses']} откл")
    cap = f"{title}: " + ", ".join(p for p in parts if p)
    return cap + (f"\n{o['url']}" if o.get("url") else "") + "\nОткликаешься сам на FL.ru, бот ничего не отправляет."


def flush_outbox():
    """Готовые отчеты CLI-сессии (outbox/*.md, первая строка #reply_to=N) уходят в Telegram, файл переезжает в reports/."""
    if not OUTBOX.exists():
        return
    REPORTS.mkdir(exist_ok=True)
    for f in sorted(OUTBOX.glob("*.md")):
        text = f.read_text(encoding="utf-8")
        reply_to = None
        if text.startswith("#order="):  # оцененный Claude заказ: md-файл документом, в подписи заказ и ссылка
            head, _, text = text.partition("\n")
            oid = head.split("=", 1)[1].strip()
            o = (json.loads(STATE.read_text()).get("orders", {}).get(oid) or {}) if STATE.exists() else {}
            dest = REPORTS / f.name
            dest.write_text(text.strip() + "\n", encoding="utf-8")
            f.unlink()
            send_doc(dest, order_caption(text, o))
            continue
        if text.startswith("#reply_to="):
            head, _, text = text.partition("\n")
            reply_to = head.split("=", 1)[1].strip() or None
        dest = REPORTS / f.name
        dest.write_text(text.strip() + "\n", encoding="utf-8")
        f.unlink()
        first = text.strip().splitlines()[0][:200] if text.strip() else f.name
        send_doc(dest, f"Отчёт и варианты отклика: {first}\nОткликаешься сам на FL.ru, бот ничего не отправляет.", reply_to=reply_to)


def poll(until):
    """До следующего прохода ленты слушаем нажатия кнопок «Сделать отчёт»."""
    if not os.environ.get("TG_BOT_TOKEN"):
        time.sleep(max(0, until - time.time()))
        return
    off = None
    while time.time() < until:
        try:
            sweep_triage()
            flush_outbox()
        except Exception:
            pass
        try:
            upd = tg("getUpdates", timeout=min(20, max(1, int(until - time.time()))),
                     allowed_updates=["callback_query"], **({"offset": off} if off else {}))
        except Exception:  # сеть или 409 от другого читателя бота: ждем и пробуем снова
            time.sleep(10)
            continue
        for u in upd.get("result", []):
            off = u["update_id"] + 1
            cq = u.get("callback_query")
            if not cq or not cq.get("data", "").startswith("r:"):
                continue
            if str(cq["message"]["chat"]["id"]) != str(os.environ.get("TG_CHAT_ID")):
                continue  # чужие чаты не обслуживаем
            tg("answerCallbackQuery", callback_query_id=cq["id"], text="Ставлю в очередь")
            try:
                enqueue(cq["data"][2:], cq["message"]["message_id"])
            except Exception as e:
                send(f"Отчёт не собрался: {type(e).__name__}", reply_to=cq["message"]["message_id"])


def once():
    """Новые заказы ленты: серые и занятые отсекаем, остальные в triage\\ для CLI-сессии Claude.
    Сам скрипт в Telegram заказы не шлет (решение Михаила 28.10: только оцененные Claude, без мусора)."""
    state = json.loads(STATE.read_text()) if STATE.exists() else {"seen": []}
    seen = set(state["seen"])
    sent = set(json.loads((HERE / "sent.json").read_text())) if (HERE / "sent.json").exists() else set()
    now = dt.datetime.now(fr.MSK)
    fr.SINCE = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=MAX_AGE_H)
    rates = fr.rates()
    out = {}
    fr.fl_parse(fr.get("https://www.fl.ru/projects/?kind=1"), out, now)
    orders = state.setdefault("orders", {})
    queued = 0
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
        if "серое" in score(v)[1]:
            continue
        try:
            if fr.fl_details(v) == "taken":  # полное ТЗ и проверка, не выбран ли исполнитель
                continue
        except OSError:
            pass
        s, why = score(v)
        if "серое" in why:
            continue
        keep = {k: v.get(k) for k in ("id", "title", "text", "url", "budget_rub", "responses", "age_h", "kind", "safe_deal")}
        keep["score"], keep["why"] = s, why
        keep["persona"] = persona_hits(v)
        TRIAGE.mkdir(exist_ok=True)
        (TRIAGE / f"{v['id'].replace(':', '_')}.json").write_text(json.dumps(keep, ensure_ascii=False, indent=1), encoding="utf-8")
        orders[v["id"]] = {k: keep[k] for k in ("id", "title", "url", "budget_rub", "responses")}
        queued += 1
    state["seen"] = sorted(seen)[-5000:]
    state["orders"] = dict(list(orders.items())[-300:])
    STATE.write_text(json.dumps(state))
    with LOG.open("a", encoding="utf-8") as f:
        f.write(f"{now:%Y-%m-%d %H:%M} лента {len(out)}, в triage {queued}\n")
    return queued


def main():
    load_env()
    use_fl_cookie()
    if "--once" in sys.argv:
        print("в triage:", once())
        return
    while True:
        start = time.time()
        h = dt.datetime.now(MINSK).hour
        if WORK_HOURS[0] <= h < WORK_HOURS[1]:
            try:
                once()
            except Exception as e:  # сеть или FL упали: пишем и ждем следующий круг
                with LOG.open("a", encoding="utf-8") as f:
                    f.write(f"{dt.datetime.now(MINSK):%Y-%m-%d %H:%M} ошибка {type(e).__name__}: {e}\n")
        poll(start + INTERVAL)


if __name__ == "__main__":
    main()
