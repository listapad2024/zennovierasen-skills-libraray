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
    # Михаил 30.10: рассылки не берет совсем, ни в каком виде; режем до оценки, как серое
    (-30, r"рассыл\w*|спам\w*|массов\w* (?:отправк|сообщен|писем|рассыл)\w*|e-?mail[- ]?маркетинг\w*|инвайтинг\w*|прогрев\w* (?:аккаунт|почт)\w*|\bsms[- ]?(?:рассыл|шлюз)\w*", "серое: рассылки"),
]
MINUS = [(w, re.compile(p, re.I), n) for w, p, n in MINUS]


def load_env():
    f = HERE / "fl_watch.env"
    if f.exists():
        for line in f.read_text(encoding="utf-8-sig").splitlines():
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


def send(text, url=None, oid=None, reply_to=None, markup=None):
    if not (os.environ.get("TG_BOT_TOKEN") and os.environ.get("TG_CHAT_ID")):
        print(text, markup or "")
        return {}
    p = {"chat_id": os.environ["TG_CHAT_ID"], "text": text[:4000], "disable_web_page_preview": "true"}
    if url and oid:  # у каждого заказа две кнопки: ссылка на заказ и разбор с черновиком отклика
        p["reply_markup"] = {"inline_keyboard": [[{"text": "Открыть на FL.ru", "url": url},
                                                  {"text": "Сделать отчёт", "callback_data": f"r:{oid}"}]]}
    if markup:
        p["reply_markup"] = markup
    if reply_to:
        p["reply_to_message_id"] = reply_to
    return tg("sendMessage", **p)


def send_doc(path, caption, reply_to=None, markup=None):
    """md-файл с вариантами отклика документом в Telegram: Михаил открывает и отправляет отклик сам (решение 28.10)."""
    tok, chat = os.environ.get("TG_BOT_TOKEN"), os.environ.get("TG_CHAT_ID")
    if not (tok and chat):
        print(caption, path)
        return
    b = "flwatch" + str(int(time.time() * 1000))
    parts = [("chat_id", chat), ("caption", caption[:1000])] + ([("reply_to_message_id", str(reply_to))] if reply_to else [])
    if markup:
        parts.append(("reply_markup", json.dumps(markup, ensure_ascii=False)))
    body = b"".join(f"--{b}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode() for k, v in parts)
    body += (f"--{b}\r\nContent-Disposition: form-data; name=\"document\"; filename=\"{Path(path).name}\"\r\n"
             f"Content-Type: text/markdown\r\n\r\n").encode() + Path(path).read_bytes() + f"\r\n--{b}--\r\n".encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{tok}/sendDocument", data=body,
                                 headers={"Content-Type": f"multipart/form-data; boundary={b}"})
    return json.loads(urllib.request.urlopen(req, timeout=60).read())


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


JOURNAL = HERE / "journal.md"  # Михаил 31.10: нарастающая история по каждому заказу, почему он ушел или не ушел в бот


def journal(v, result, reason, analysis=""):
    """Запись в journal.md: дата и время, заказ, ссылка, бюджет и отклики, итог, причина и разбор. Блоки через пустую строку."""
    rub = v.get("budget_rub")
    meta = " · ".join(x for x in (
        f"бюджет {rub:,} ₽".replace(",", " ") if rub else "бюджет по договоренности",
        f"откликов {v['responses']}" if v.get("responses") is not None else "",
        f"опубликован {v['age_h']} ч назад" if v.get("age_h") is not None else "") if x)
    parts = [f"## {dt.datetime.now(MINSK):%d.%m.%Y %H:%M} · {v.get('title') or v.get('id')}", v.get("url") or "", meta,
             f"Итог: {result}", f"Причина: {reason}" if reason else "", f"Разбор:\n\n{analysis.strip()}" if analysis.strip() else "", "---"]
    with JOURNAL.open("a", encoding="utf-8") as f:
        f.write("\n\n".join(x for x in parts if x) + "\n\n")


def sweep_triage():
    """Решения CLI-сессии (verdict/*.txt) убирают заказ в triage/done, заказы без решения дольше STALE_H часов в triage/stale."""
    if not TRIAGE.exists():
        return
    for sub in ("done", "stale"):
        (TRIAGE / sub).mkdir(exist_ok=True)
    for f in TRIAGE.glob("*.json"):
        vd = VERDICT / (f.stem + ".txt")
        if vd.exists():
            try:
                v = json.loads(f.read_text(encoding="utf-8"))
                raw = vd.read_text(encoding="utf-8", errors="replace").strip()
                head, _, rest = raw.partition("\n")
                ok = head.lower().startswith("ok")
                journal(v, "отправлен в бот" if ok else "не отправлен, отсеян оценкой",
                        head.split(":", 1)[1].strip() if ":" in head else head, rest)
            except Exception:
                pass
            with LOG.open("a", encoding="utf-8") as lg:  # решение с причиной остается в логе, verdict-файл удаляется
                lg.write(f"{dt.datetime.now():%Y-%m-%d %H:%M} verdict {f.stem}: {vd.read_text(encoding='utf-8', errors='replace').strip()[:300]}\n")
            f.replace(TRIAGE / "done" / f.name)
            vd.unlink()
        elif time.time() - f.stat().st_mtime > STALE_H * 3600:
            try:
                journal(json.loads(f.read_text(encoding="utf-8")), "не отправлен", f"оценка не разобрала его за {STALE_H} ч")
            except Exception:
                pass
            f.replace(TRIAGE / "stale" / f.name)


def order_caption(text, o):
    """Михаил 29.10 (вечер): на заказ одна подпись: заголовок, один абзац о чем заказ и почему хороший, ссылка. Подробности только в md."""
    lines = [l.strip() for l in text.strip().splitlines() if l.strip()]
    title = next((l.lstrip("# ").strip() for l in lines if l.startswith("# ")), o.get("title") or "Заказ FL.ru")
    short = next((l.split(":", 1)[1].strip() for l in lines if l.lower().startswith("кратко:")), "")
    if not short:  # старые md без строки «Кратко:»
        short = next((l.split(":", 1)[1].strip() for l in lines if l.lower().lstrip("-* ").startswith("чем подходит:")), "")
        total = next((l.split(":", 1)[1].strip() for l in lines if l.lower().startswith("итог:")), "")
        short = " ".join(x for x in (short, f"Оценка {total}." if total else "") if x)
    url = o.get("url") or next((m.group(0) for m in [re.search(r"https://www\.fl\.ru/projects/\d+\S*", text)] if m), "")
    num = "".join(re.findall(r"\d+", str(o.get("id") or "")))
    if not url and num:
        url = f"https://www.fl.ru/projects/{num}/"
    body = "\n\n".join(x for x in (title, short) if x)
    # ссылка обязательна (Михаил 02.11): режем текст, а не ссылку
    return (body[:1000 - len(url) - 2] + "\n\n" + url) if url else body[:1000]


def send_group(items):
    """Несколько заказов одним блоком (sendMediaGroup, до 10 файлов), у каждого файла своя подпись."""
    tok, chat = os.environ.get("TG_BOT_TOKEN"), os.environ.get("TG_CHAT_ID")
    if not (tok and chat):
        for dest, cap in items:
            print(cap, dest)
        return
    b = "flwatch" + str(int(time.time() * 1000))
    media = [{"type": "document", "media": f"attach://f{i}", "caption": cap} for i, (_, cap) in enumerate(items)]
    body = b"".join(f"--{b}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode()
                    for k, v in (("chat_id", chat), ("media", json.dumps(media, ensure_ascii=False))))
    for i, (dest, _) in enumerate(items):
        body += (f"--{b}\r\nContent-Disposition: form-data; name=\"f{i}\"; filename=\"{Path(dest).name}\"\r\n"
                 f"Content-Type: text/markdown\r\n\r\n").encode() + Path(dest).read_bytes() + b"\r\n"
    body += f"--{b}--\r\n".encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{tok}/sendMediaGroup", data=body,
                                 headers={"Content-Type": f"multipart/form-data; boundary={b}"})
    urllib.request.urlopen(req, timeout=90).read()


def deliver(names, fn):
    """Отправка с записью в лог; при ошибке файлы остаются в outbox и уйдут на следующем круге (29.10: ошибки молча терялись)."""
    try:
        fn()
        msg, ok = f"отправлен {', '.join(names)}", True
    except Exception as e:
        body = e.read().decode("utf-8", "replace")[:200] if hasattr(e, "read") else ""
        msg, ok = f"ОШИБКА отправки {', '.join(names)}: {type(e).__name__} {body}", False
    with LOG.open("a", encoding="utf-8") as lg:
        lg.write(f"{dt.datetime.now():%Y-%m-%d %H:%M} {msg}\n")
    return ok


def flush_outbox():
    """Готовые отчеты CLI-сессии уходят в Telegram, файл переезжает в reports/. Заказы одного круга идут одним блоком."""
    if not OUTBOX.exists():
        return
    REPORTS.mkdir(exist_ok=True)
    orders = []
    for f in sorted(OUTBOX.glob("*.md")):
        text = f.read_text(encoding="utf-8")
        reply_to = None
        if text.startswith("#order="):  # оцененный Claude заказ: md-файл документом, подпись из строки «Кратко:» и ссылка
            head, _, text = text.partition("\n")
            raw = head.split("=", 1)[1].strip()
            num = "".join(re.findall(r"\d+", raw))  # 02.11: Sonnet пишет id как fl_5527713 или 5527713, ключ в state fl:5527713
            o = (json.loads(STATE.read_text()).get("orders", {}).get(f"fl:{num}") or {"id": f"fl:{num}"}) if STATE.exists() else {}
            dest = REPORTS / f.name
            dest.write_text(text.strip() + "\n", encoding="utf-8")
            orders.append((f, dest, order_caption(text, o), o.get("id") or f"fl:{num}"))
            continue
        if text.startswith("#reply_to="):
            head, _, text = text.partition("\n")
            reply_to = head.split("=", 1)[1].strip() or None
        dest = REPORTS / f.name
        dest.write_text(text.strip() + "\n", encoding="utf-8")
        first = text.strip().splitlines()[0][:200] if text.strip() else f.name
        cap = f"Отчёт и варианты отклика: {first}\n\nОткликаешься сам на FL.ru, бот ничего не отправляет."
        if deliver([f.name], lambda: send_doc(dest, cap, reply_to=reply_to)):
            f.unlink()
    for i in range(0, len(orders), 10):
        chunk = orders[i:i + 10]
        if len(chunk) == 1:
            fn = lambda c=chunk[0]: send_doc(c[1], c[2], markup=order_keyboard([c[3]]))
        else:
            fn = lambda c=chunk: (send_group([(d, cap) for _, d, cap, _ in c]),
                                  send("Отметь по каждому заказу:", markup=order_keyboard([oid for *_, oid in c])))
        if deliver([f.name for f, *_ in chunk], fn):
            for f, *_ in chunk:
                f.unlink()


# --- Кнопки под заказами (Михаил 08.11) ---------------------------------------------------------------------------
# «Откликнулся» пишет отклик в Persona (record_response). «Отклонить» спрашивает причину, есть кнопка «Без причины».
# Без причины в базу ничего не идет, только строка в журнале. Причину разбирает LLM (CLI-сессия по QUEUE.md, сам
# скрипт к Claude не обращается): «не сейчас» не меняет оценку будущих заказов, «не мой стек» становится правилом
# отсева в learned_rules.md и анти-предпочтением в Persona.
BUTTONS = HERE / "fl_buttons.json"      # ждущие причины отказа и уже отмеченные заказы
FEEDBACK = HERE / "feedback"            # причины отказа для LLM: feedback/<имя>.json
FEEDBACK_RES = HERE / "feedback_result" # ответ LLM: feedback_result/<имя>.json
LEARNED = HERE / "learned_rules.md"     # правила «не мой стек», CLI-сессия читает их при оценке
PERSONA_BASE = PERSONA_URL.rsplit("/", 1)[0]
_tg_offset = None


def order_keyboard(oids):
    """Под одним заказом две кнопки; под блоком заказов строка на каждый: «✅ название» и «✖ Отклонить»."""
    orders = _orders()
    rows = []
    for oid in oids:
        name = (orders.get(oid, {}).get("title") or oid)[:28] if len(oids) > 1 else "Откликнулся"
        rows.append([{"text": f"✅ {name}", "callback_data": f"a:{oid}"},
                     {"text": "✖ Отклонить", "callback_data": f"x:{oid}"}])
    return {"inline_keyboard": rows}


def _orders():
    try:
        return json.loads(STATE.read_text()).get("orders", {})
    except Exception:
        return {}


def _btn_state():
    try:
        return json.loads(BUTTONS.read_text(encoding="utf-8"))
    except Exception:
        return {"pending": {}, "marked": {}}


def _btn_save(b):
    BUTTONS.write_text(json.dumps(b, ensure_ascii=False, indent=1), encoding="utf-8")


def _order_full(oid):
    """Заказ с полным ТЗ: из state, а текст из triage/done (там полный json)."""
    v = dict(_orders().get(oid) or {"id": oid})
    for d in (TRIAGE / "done", TRIAGE, TRIAGE / "stale"):
        f = d / f"{oid.replace(':', '_')}.json"
        if f.exists():
            try:
                v = {**json.loads(f.read_text(encoding="utf-8")), **v}
            except Exception:
                pass
            break
    if not v.get("url"):
        num = "".join(re.findall(r"\d+", oid))
        v["url"] = f"https://www.fl.ru/projects/{num}/"
    return v


def persona_post(path, payload):
    tok = next((l.split("=", 1)[1].strip() for l in PERSONA_ENV.read_text(encoding="utf-8").splitlines()
                if l.split("=", 1)[0].strip().upper().endswith("TOKEN")), "")
    req = urllib.request.Request(f"{PERSONA_BASE}/{path}", data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                                 headers={"Content-Type": "application/json", "X-API-Token": tok})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=120) as r:
        return json.loads(r.read() or b"{}")


def _log(msg):
    with LOG.open("a", encoding="utf-8") as lg:
        lg.write(f"{dt.datetime.now():%Y-%m-%d %H:%M} {msg}\n")


def _mark_row(cq, oid, label):
    """Строку с кнопками заказа заменяем отметкой, остальные строки блока не трогаем."""
    kb = (cq["message"].get("reply_markup") or {}).get("inline_keyboard") or []
    rows = []
    for row in kb:
        if any(b.get("callback_data", "")[2:] == oid for b in row):
            title = row[0]["text"][2:] if len(kb) > 1 else ""
            rows.append([{"text": f"{label} {title}".strip(" ·"), "callback_data": f"-:{oid}"}])
        else:
            rows.append(row)
    try:
        tg("editMessageReplyMarkup", chat_id=cq["message"]["chat"]["id"], message_id=cq["message"]["message_id"],
           reply_markup={"inline_keyboard": rows})
    except Exception:
        pass


def on_responded(cq, oid):
    v = _order_full(oid)
    num = "".join(re.findall(r"\d+", oid))
    rep = REPORTS / f"fl_{num}.md"
    try:
        persona_post("record_response", {
            "id": f"fl_{num}", "platform": "fl_ru", "url": v.get("url", ""), "title": v.get("title", ""),
            "response_text": f"Отклик отправлен Михаилом на FL.ru. Черновики вариантов: {rep.name if rep.exists() else 'нет'}.",
            "commentary": "Отмечено кнопкой «Откликнулся» в боте радара.",
            "budget_in_field": v.get("budget_rub"), "task_description": (v.get("text") or "")[:4000],
            "verdict_at_send": "candidate", "outcome": "pending", "sent_by": "manual", "reindex": True})
        saved = True
    except Exception as e:
        saved = False
        _log(f"ОШИБКА записи отклика {oid} в Persona: {type(e).__name__}")
    b = _btn_state()
    b["marked"][oid] = {"result": "responded", "at": time.time()}
    _btn_save(b)
    journal(v, "Михаил откликнулся", "кнопка «Откликнулся»")
    _log(f"кнопка: откликнулся {oid}" + ("" if saved else " (Persona недоступна, запись только в журнале)"))
    _mark_row(cq, oid, "📨 Отклик отправлен ·")
    resp_save(oid, sent_at=dt.datetime.now(MINSK).isoformat(timespec="seconds"), outcome="pending")
    ask_sent_text(oid, cq["message"]["message_id"])
    return "Записал отклик" if saved else "Persona недоступна, записал только в журнал"


def on_reject(cq, oid):
    v = _orders().get(oid) or {}
    r = send(f"Почему отклоняешь «{(v.get('title') or oid)[:80]}»?\n\nНапиши причину ответом на это сообщение. "
             f"Например: «не хочу сейчас, занят» или «вообще не мой стек».",
             reply_to=cq["message"]["message_id"],
             markup={"inline_keyboard": [[{"text": "Без причины", "callback_data": f"n:{oid}"}]]})
    b = _btn_state()
    mid = str((r.get("result") or {}).get("message_id", ""))
    b["pending"][mid] = {"oid": oid, "src_msg": cq["message"]["message_id"], "at": time.time()}
    _btn_save(b)
    _mark_row(cq, oid, "🚫 Отклонено ·")
    return "Жду причину"


def on_no_reason(cq, oid):
    b = _btn_state()
    b["pending"].pop(str(cq["message"]["message_id"]), None)
    b["marked"][oid] = {"result": "rejected", "at": time.time()}
    _btn_save(b)
    journal(_order_full(oid), "Михаил отклонил", "без причины, в базу не пишем")
    _log(f"кнопка: отклонен без причины {oid}")
    try:
        tg("editMessageText", chat_id=cq["message"]["chat"]["id"], message_id=cq["message"]["message_id"],
           text="Отклонено без причины, в базу не записываю.")
    except Exception:
        pass
    return "Отклонено"


def on_reason(msg):
    """Текст Михаила: ответ на вопрос о причине или, если вопрос один, просто следующее сообщение."""
    b = _btn_state()
    b["pending"] = {k: p for k, p in b["pending"].items() if time.time() - p.get("at", 0) < 24 * 3600}
    reply = str((msg.get("reply_to_message") or {}).get("message_id", ""))
    key = reply if reply in b["pending"] else (next(iter(b["pending"])) if len(b["pending"]) == 1 else None)
    if not key:
        return
    p = b["pending"].pop(key)
    oid = p["oid"]
    if p.get("kind") in ("sent_text", "reply"):
        _btn_save(b)
        return on_text_for_response(p, msg, key)
    b["marked"][oid] = {"result": "rejected", "reason": msg["text"], "at": time.time()}
    _btn_save(b)
    v = _order_full(oid)
    FEEDBACK.mkdir(exist_ok=True)
    keep = {k: v.get(k) for k in ("id", "title", "text", "url", "budget_rub", "responses")}
    keep.update(reason=msg["text"], reason_at=dt.datetime.now(MINSK).isoformat(timespec="seconds"), reply_to=msg["message_id"])
    (FEEDBACK / f"{oid.replace(':', '_')}.json").write_text(json.dumps(keep, ensure_ascii=False, indent=1), encoding="utf-8")
    _log(f"кнопка: отклонен {oid}, причина на разбор: {msg['text'][:200]}")
    send("Принял. Разберу причину и запишу в базу, напишу, что понял.", reply_to=msg["message_id"])


KIND_RU = {"not_now": "не сейчас", "not_stack": "не мой стек", "budget": "не устроил бюджет",
           "client": "не устроил заказчик или ТЗ", "other": "другое"}


def apply_feedback():
    """Разбор причины от LLM (feedback_result/*.json) пишем в Persona и, для «не мой стек», в learned_rules.md."""
    if not FEEDBACK_RES.exists():
        return
    for f in FEEDBACK_RES.glob("*.json"):
        src = FEEDBACK / f.name
        try:
            res = json.loads(f.read_text(encoding="utf-8"))
            v = json.loads(src.read_text(encoding="utf-8")) if src.exists() else {"id": f.stem.replace("_", ":", 1)}
        except Exception as e:
            _log(f"ОШИБКА разбора причины {f.name}: {type(e).__name__}")
            f.replace(f.with_suffix(".bad"))
            continue
        kind = res.get("kind") if res.get("kind") in KIND_RU else "other"
        summary, rule = (res.get("summary") or "").strip(), (res.get("rule") or "").strip()
        title = v.get("title") or v.get("id")
        if kind == "not_stack":
            text = f"Михаил не берет такие заказы на фрилансе: {rule or summary}. Пример: «{title}». Его слова: {v.get('reason')}"
            tags, category = ["fl_decline", "not_stack", "anti_preference"], "skill"
        elif kind == "not_now":
            text = (f"Михаил пропустил заказ «{title}» только по времени ({summary}). Тип заказа ему подходит, "
                    f"на оценку похожих заказов это не влияет. Его слова: {v.get('reason')}")
            tags, category = ["fl_decline", "not_now"], "fact"
        else:
            text = f"Михаил отказался от заказа «{title}» ({KIND_RU[kind]}): {summary}. Его слова: {v.get('reason')}"
            tags, category = ["fl_decline", kind], "fact"
        try:
            persona_post("remember", {"text": text, "category": category, "source": "fl_radar_decline", "tags": tags,
                                      "platform": "fl_ru", "extra_payload": {"order_id": v.get("id"), "url": v.get("url"), "kind": kind}})
            saved = True
        except Exception as e:
            saved = False
            _log(f"ОШИБКА записи причины {f.stem} в Persona: {type(e).__name__}")
        if kind == "not_stack" and rule:
            if not LEARNED.exists():
                LEARNED.write_text("# Чему научили отказы Михаила (не мой стек)\n\nТакие заказы отсеивай (`skip`), "
                                   "даже если они похожи на стек.\n\n", encoding="utf-8")
            with LEARNED.open("a", encoding="utf-8") as lf:
                lf.write(f"- {rule} (пример: {title}, {dt.datetime.now(MINSK):%d.%m.%Y})\n")
        journal(v, "Михаил отклонил", f"{KIND_RU[kind]}: {v.get('reason')}", summary)
        _log(f"причина {f.stem}: {kind} {summary[:150]}")
        after = {"not_stack": f"Такие заказы дальше буду отсеивать: {rule or summary}.",
                 "not_now": "Тип заказа тебе подходит, на будущие оценки это не влияет."}.get(kind, "На отсев будущих заказов это не влияет.")
        send(f"Понял причину как «{KIND_RU[kind]}». {after}" + ("" if saved else "\n\nPersona недоступна, записал только в журнал."),
             reply_to=v.get("reply_to"))
        (FEEDBACK / "done").mkdir(parents=True, exist_ok=True)
        if src.exists():
            src.replace(FEEDBACK / "done" / src.name)
        f.unlink()

# --- Что реально ушло и чем кончилось (Михаил 09.11) ---------------------------------------------------------------
# Цель Михаила: со временем доверить радару отклики без вычитки. Для этого копим пары «черновик радара -> текст,
# который Михаил реально отправил» и что ответил заказчик. responses/<имя>.json хранит все локально, Persona
# получает отправленный текст и исход (record_response идемпотентен по id: answered/won дают буст в поиске).
RESPONSES = HERE / "responses"


def _resp_path(oid):
    return RESPONSES / f"{oid.replace(':', '_')}.json"


def resp_load(oid):
    try:
        return json.loads(_resp_path(oid).read_text(encoding="utf-8"))
    except Exception:
        v = _order_full(oid)
        r = {k: v.get(k) for k in ("id", "title", "url", "budget_rub", "responses")}
        r.update(task=(v.get("text") or "")[:6000], drafts=draft_variants(oid), sent_text="", sent_source="", replies=[], outcome="pending")
        return r


def resp_save(oid, **kw):
    r = resp_load(oid)
    r.update(kw)
    RESPONSES.mkdir(exist_ok=True)
    _resp_path(oid).write_text(json.dumps(r, ensure_ascii=False, indent=1), encoding="utf-8")
    return r


def draft_variants(oid):
    """Тексты отклика из md радара без строки «Поля: ...»: «## Вариант N» или, с 09.11, один «## Отклик» (ключ "1")."""
    num = "".join(re.findall(r"\d+", oid))
    f = REPORTS / f"fl_{num}.md"
    if not f.exists():
        return {}
    out = {}
    for m in re.finditer(r"^## (?:Вариант (\d)|Отклик)[^\n]*\n(.*?)(?=^## |\Z)", f.read_text(encoding="utf-8"), re.S | re.M):
        body = "\n".join(l for l in m.group(2).strip().splitlines() if not l.strip().lower().startswith("поля:"))
        out[m.group(1) or "1"] = re.sub(r"\n{3,}", "\n\n", body).strip()
    return out


def resp_to_persona(r):
    num = "".join(re.findall(r"\d+", str(r.get("id"))))
    replies = "\n\n".join(f"[{x.get('at', '')}] {x.get('text', '')}" for x in r.get("replies", []))
    try:
        persona_post("record_response", {
            "id": f"fl_{num}", "platform": "fl_ru", "url": r.get("url") or "", "title": r.get("title") or "",
            "response_text": r.get("sent_text") or "(текст не сохранен)",
            "commentary": "\n\n".join(x for x in (f"Источник текста: {r.get('sent_source') or 'неизвестно'}.",
                                                   f"Ответы заказчика:\n{replies}" if replies else "") if x),
            "budget_in_field": r.get("budget_rub"), "task_description": (r.get("task") or "")[:4000],
            "verdict_at_send": "candidate", "outcome": r.get("outcome") or "pending", "sent_by": "manual", "reindex": True})
        return True
    except Exception as e:
        _log(f"ОШИБКА записи отклика {r.get('id')} в Persona: {type(e).__name__}")
        return False


def ask_sent_text(oid, reply_to):
    vs = sorted(draft_variants(oid))
    row = [{"text": "Как в черновике" if len(vs) == 1 else f"Вариант {n}", "callback_data": f"s:{n}:{oid}"} for n in vs]
    kb = ([row] if row else []) + [[{"text": "✍️ Свой текст", "callback_data": f"s:own:{oid}"},
                                    {"text": "Пропустить", "callback_data": f"s:skip:{oid}"}]]
    send("Какой текст ушел заказчику? Если правил вариант, нажми «Свой текст» и пришли то, что отправил.",
         reply_to=reply_to, markup={"inline_keyboard": kb})


def outcome_keyboard(oid):
    return {"inline_keyboard": [[{"text": "💬 Заказчик ответил", "callback_data": f"o:reply:{oid}"}],
                                [{"text": "🏆 Взял заказ", "callback_data": f"o:won:{oid}"},
                                 {"text": "Выбрали другого", "callback_data": f"o:lost:{oid}"}]]}


def _edit(cq, text, markup=None):
    try:
        p = {"chat_id": cq["message"]["chat"]["id"], "message_id": cq["message"]["message_id"], "text": text}
        if markup:
            p["reply_markup"] = markup
        tg("editMessageText", **p)
    except Exception:
        pass


def _wait_text(cq, oid, kind, question):
    r = send(question, reply_to=cq["message"]["message_id"])
    b = _btn_state()
    b["pending"][str((r.get("result") or {}).get("message_id", ""))] = {"oid": oid, "kind": kind, "src_msg": cq["message"]["message_id"], "at": time.time()}
    _btn_save(b)


def on_sent_choice(cq, oid, choice):
    if choice == "skip":
        _edit(cq, "Текст отклика не сохранен. Исход можно отметить позже.", outcome_keyboard(oid))
        return "Ок"
    if choice == "own":
        _wait_text(cq, oid, "sent_text", "Пришли ответом на это сообщение текст, который ушел заказчику.")
        return "Жду текст"
    r = resp_load(oid)
    text = (r.get("drafts") or {}).get(choice) or draft_variants(oid).get(choice, "")
    r = resp_save(oid, sent_text=text, sent_source=f"черновик радара {choice} без правок")
    ok = resp_to_persona(r)
    _log(f"отклик {oid}: ушел вариант {choice}")
    _edit(cq, "Сохранил: ушел черновик радара без правок." + ("" if ok else " Persona недоступна, сохранил локально.")
          + "\n\nКогда будет ответ или исход, отметь здесь:", outcome_keyboard(oid))
    return "Сохранил"


def on_outcome(cq, oid, choice):
    if choice == "reply":
        _wait_text(cq, oid, "reply", "Пришли ответом на это сообщение, что написал заказчик. Можно несколько сообщений, каждое ответом сюда.")
        return "Жду ответ заказчика"
    r = resp_save(oid, outcome=choice, outcome_at=dt.datetime.now(MINSK).isoformat(timespec="seconds"))
    resp_to_persona(r)
    journal(_order_full(oid), "Михаил взял заказ" if choice == "won" else "выбрали другого исполнителя", "отметка в боте")
    _log(f"отклик {oid}: исход {choice}")
    _edit(cq, "🏆 Заказ твой, записал." if choice == "won" else "Записал: выбрали другого.")
    return "Записал"


def on_text_for_response(p, msg, key):
    oid, now = p["oid"], dt.datetime.now(MINSK).isoformat(timespec="seconds")
    r = resp_load(oid)
    if p["kind"] == "sent_text":
        r = resp_save(oid, sent_text=msg["text"], sent_source="текст Михаила (свой или правленый вариант)")
        note = "Сохранил текст отклика."
    else:
        r["replies"].append({"at": now, "text": msg["text"]})
        r = resp_save(oid, replies=r["replies"], outcome="answered" if r.get("outcome") == "pending" else r.get("outcome"))
        note = "Сохранил ответ заказчика."
        b = _btn_state()  # следующие ответы заказчика тоже можно присылать ответом на тот же вопрос
        b["pending"][key] = {**p, "at": time.time()}
        _btn_save(b)
    ok = resp_to_persona(r)
    _log(f"отклик {oid}: {'текст' if p['kind'] == 'sent_text' else 'ответ заказчика'} сохранен")
    send(note + ("" if ok else " Persona недоступна, сохранил локально.") + "\n\nДальше отметь здесь:",
         reply_to=msg["message_id"], markup=outcome_keyboard(oid))



def handle_update(u):
    chat = str(os.environ.get("TG_CHAT_ID"))
    cq = u.get("callback_query")
    if cq:
        if str(cq["message"]["chat"]["id"]) != chat:
            return  # чужие чаты не обслуживаем
        data = cq.get("data", "")
        kind, oid = data[:1], data[2:]
        if kind == "r":
            tg("answerCallbackQuery", callback_query_id=cq["id"], text="Ставлю в очередь")
            try:
                enqueue(oid, cq["message"]["message_id"])
            except Exception as e:
                send(f"Отчёт не собрался: {type(e).__name__}", reply_to=cq["message"]["message_id"])
            return
        if kind in ("s", "o"):  # s:<1|2|3|own|skip>:<oid> какой текст ушел, o:<reply|won|lost>:<oid> что дальше
            _, choice, oid = data.split(":", 2)
            try:
                text = (on_sent_choice if kind == "s" else on_outcome)(cq, oid, choice)
            except Exception as e:
                text = f"Ошибка: {type(e).__name__}"
                _log(f"ОШИБКА кнопки {data}: {type(e).__name__} {e}")
            tg("answerCallbackQuery", callback_query_id=cq["id"], text=text)
            return
        fn = {"a": on_responded, "x": on_reject, "n": on_no_reason}.get(kind)
        text = "Уже отмечено" if kind == "-" else ""
        if fn:
            try:
                text = fn(cq, oid)
            except Exception as e:
                text = f"Ошибка: {type(e).__name__}"
                _log(f"ОШИБКА кнопки {data}: {type(e).__name__} {e}")
        tg("answerCallbackQuery", callback_query_id=cq["id"], text=text)
        return
    msg = u.get("message")
    if msg and msg.get("text") and str(msg["chat"]["id"]) == chat and not msg["text"].startswith("/"):
        on_reason(msg)


def poll(until):
    """До следующего прохода ленты слушаем кнопки под заказами и ответы Михаила с причиной отказа."""
    global _tg_offset
    if not os.environ.get("TG_BOT_TOKEN"):
        time.sleep(max(0, until - time.time()))
        return
    while time.time() < until:
        for step in (sweep_triage, flush_outbox, apply_feedback):
            try:
                step()
            except Exception:
                pass
        try:
            upd = tg("getUpdates", timeout=min(20, max(1, int(until - time.time()))),
                     allowed_updates=["callback_query", "message"], **({"offset": _tg_offset} if _tg_offset else {}))
        except Exception:  # сеть или 409 от другого читателя бота: ждем и пробуем снова
            time.sleep(10)
            continue
        for u in upd.get("result", []):
            _tg_offset = u["update_id"] + 1
            try:
                handle_update(u)
            except Exception as e:
                _log(f"ОШИБКА обработки обновления: {type(e).__name__} {e}")


def once():
    """Новые заказы ленты: серые и занятые отсекаем, остальные в triage\\ для CLI-сессии Claude.
    Сам скрипт в Telegram заказы не шлет (решение Михаила 28.10: только оцененные Claude, без мусора)."""
    state = json.loads(STATE.read_text()) if STATE.exists() else {"seen": []}
    seen = set(state["seen"])
    sent = set(json.loads((HERE / "sent.json").read_text())) if (HERE / "sent.json").exists() else set()
    now = dt.datetime.now(fr.MSK)
    # 31.10: заказы, вышедшие ночью (после 21:00, когда радар спит), утром резались как старые. Окно свежести
    # = с последнего рабочего круга, но не меньше MAX_AGE_H и не больше 14 ч.
    max_age = min(14, max(MAX_AGE_H, (time.time() - state.get("last_round", time.time())) / 3600 + 0.5))
    fr.SINCE = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=max_age)
    rates = fr.rates()
    out = {}
    pages = 1 if max_age <= MAX_AGE_H + 1 else 5  # после ночи одной страницы (30 заказов) мало
    for n in range(1, pages + 1):
        fr.fl_parse(fr.get("https://www.fl.ru/projects/?kind=1" if n == 1 else f"https://www.fl.ru/projects/page-{n}/?kind=1"), out, now)
    orders = state.setdefault("orders", {})
    queued = 0
    for v in out.values():
        if v["id"] in seen or v["id"] in sent:
            continue
        seen.add(v["id"])
        if v["published"]:
            v["age_h"] = round((dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(v["published"])).total_seconds() / 3600, 1)
            if v["age_h"] > max_age:
                journal(v, "не отправлен, отсеян до оценки", f"опубликован больше {max_age:.0f} ч назад, заказ уже не свежий")
                continue
        if v["budget"] and v["currency"] in rates:
            v["budget_rub"] = round(v["budget"] * rates[v["currency"]])
        if "серое" in score(v)[1]:
            journal(v, "не отправлен, отсеян до оценки", ", ".join(w for w in score(v)[1] if "серое" in w), v.get("text", "")[:600])
            continue
        try:
            if fr.fl_details(v) == "taken":  # полное ТЗ и проверка, не выбран ли исполнитель
                journal(v, "не отправлен, отсеян до оценки", "исполнитель уже выбран")
                continue
        except OSError:
            pass
        s, why = score(v)
        if "серое" in why:
            journal(v, "не отправлен, отсеян до оценки", ", ".join(w for w in why if "серое" in w), v.get("text", "")[:600])
            continue
        keep = {k: v.get(k) for k in ("id", "title", "text", "url", "budget_rub", "responses", "age_h", "kind", "safe_deal")}
        keep["score"], keep["why"] = s, why
        keep["persona"] = persona_hits(v)
        TRIAGE.mkdir(exist_ok=True)
        (TRIAGE / f"{v['id'].replace(':', '_')}.json").write_text(json.dumps(keep, ensure_ascii=False, indent=1), encoding="utf-8")
        orders[v["id"]] = {k: keep[k] for k in ("id", "title", "url", "budget_rub", "responses")}
        queued += 1
    state["last_round"] = time.time()
    state["seen"] = sorted(seen)[-5000:]
    state["orders"] = dict(list(orders.items())[-300:])
    STATE.write_text(json.dumps(state))
    with LOG.open("a", encoding="utf-8") as f:
        f.write(f"{now:%Y-%m-%d %H:%M} лента {len(out)}, в triage {queued}\n")
    return queued


ALERTS = HERE / "fl_alerts.json"  # когда какой алерт уже слали, чтобы не спамить


def alert(key, text, every_h=2):
    """Михаил 30.10: если цепочка встала, бот сам говорит об этом (не чаще раза в every_h часов на ключ)."""
    try:
        st = json.loads(ALERTS.read_text()) if ALERTS.exists() else {}
    except Exception:
        st = {}
    if time.time() - st.get(key, 0) < every_h * 3600:
        return
    st[key] = time.time()
    ALERTS.write_text(json.dumps(st))
    try:
        send("⚠️ FL-радар: " + text)
    except Exception:
        pass
    with LOG.open("a", encoding="utf-8") as f:
        f.write(f"{dt.datetime.now(MINSK):%Y-%m-%d %H:%M} алерт {key}: {text}\n")


def health(errors_in_row):
    """Проверки внутри FL Watch: оценка не разбирает очередь, лента не читается."""
    q = list(TRIAGE.glob("*.json")) if TRIAGE.exists() else []
    if q:
        age = (time.time() - min(f.stat().st_mtime for f in q)) / 60
        if age > 30:
            alert("triage", f"оценка заказов не идет, в очереди {len(q)}, самый старый ждет {age:.0f} мин. "
                            "Проверь задачу FL-Triage в планировщике.")
    if errors_in_row >= 3:
        alert("feed", f"лента FL.ru не читается {errors_in_row} круга подряд, подробности в fl_watch.log.")


def check_watch():
    """Запускается из fl_triage.cmd: если сам FL Watch молчит, пишем в бот и перезапускаем его."""
    now = dt.datetime.now(MINSK)
    if not (WORK_HOURS[0] <= now.hour < WORK_HOURS[1]) or (now.hour == WORK_HOURS[0] and now.minute < 30) or not LOG.exists():
        return  # до 9:30 лог еще вчерашний, это не сбой
    idle = (time.time() - LOG.stat().st_mtime) / 60
    if idle > 20:
        alert("watch", f"FL Watch молчит {idle:.0f} мин, перезапускаю задачу FL-Watch. Если сообщение повторится, ПК надо проверить.")
        os.system('schtasks /Run /TN "FL-Watch" >nul 2>&1')


def main():
    load_env()
    use_fl_cookie()
    if "--once" in sys.argv:
        print("в triage:", once())
        return
    if "--check" in sys.argv:
        check_watch()
        return
    errors = 0
    while True:
        start = time.time()
        h = dt.datetime.now(MINSK).hour
        if WORK_HOURS[0] <= h < WORK_HOURS[1]:
            try:
                once()
                errors = 0
            except Exception as e:  # сеть или FL упали: пишем и ждем следующий круг
                errors += 1
                with LOG.open("a", encoding="utf-8") as f:
                    f.write(f"{dt.datetime.now(MINSK):%Y-%m-%d %H:%M} ошибка {type(e).__name__}: {e}\n")
            try:
                health(errors)
            except Exception:
                pass
        poll(start + INTERVAL)


if __name__ == "__main__":
    main()
