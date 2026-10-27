#!/usr/bin/env python3
"""Радар подработок: заказы с фриланс-бирж (FL.ru, freelance.ru), отдельно от радара вакансий.

Запуск:  python3 freelance_radar.py [--hours 36] [--all]
  --hours  окно свежести в часах (по умолчанию 36)
  --all    игнорировать seen.json (для отладки)

Пишет runs/YYYY-MM-DD_HHMM.json: {"stats":..., "work":[...], "clarify":[...], "rep":[...]}
  work:    профильный заказ с бюджетом от WORK_MIN_RUB (крупные от BIG_MIN_USD помечены big)
  clarify: профиль подходит, бюджет «по договоренности» или долгосрочное сотрудничество
  rep:     мелкий профильный заказ (REP_MIN_RUB..WORK_MIN_RUB) ради первых отзывов
Только стандартная библиотека Python. Правила оценки и стоп-листы перенесены из радара Codex
(D:\\Codex\\CodexSandbox\\FreelanceRadarSlotKit, май-июнь 2026) и общего радара вакансий (18.10.2026).
"""
import argparse
import datetime as dt
import html
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
SEEN = HERE / "seen.json"
SENT = HERE / "sent.json"   # ID заказов, на которые Михаил уже откликнулся: не поднимать заново
RUNS = HERE / "runs"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/128 Safari/537.36"
MSK = dt.timezone(dt.timedelta(hours=3))

# Трек подработки: 15-20 ч/нед, от 1500 $/мес или от 20 $/ч. Бюджет заказа - на весь объем, поэтому
# планка «рабочего» заказа ниже месячной: 20 000 ₽ - это около 12 ч по 20 $/ч.
WORK_MIN_RUB = 20_000
BIG_MIN_USD = 1500
REP_MIN_RUB = 5_000         # ниже - только если Михаил решит добирать отзывы
HOT_RESPONSES = 40          # больше откликов - заказ перегрет (ослаблено 27.10 по решению Михаила, было 20)
STALE_HOURS = 24            # старше суток и уже >STALE_RESPONSES откликов - исполнителя скорее всего нашли
STALE_RESPONSES = 25        # было 10
USD_RUB_FALLBACK, EUR_RUB_FALLBACK = 80.0, 92.0

FL_CATEGORIES = ["ai-iskusstvenniy-intellekt", "avtomatizaciya-biznesa", "programmirovanie", "programmirovanie/python",
                 "messengers", "brauzery", "marketplace-management",
                 "programmirovanie/programmist-baz-dannyh", "programmirovanie/parsing-dannyih",
                 "programmirovanie/integraciya-po-api", "programmirovanie/n8n", "programmirovanie/mashinnoe-obuchenie",
                 "programmirovanie/razrabotka-chat-botov", "programmirovanie/sozdanie-skriptov",
                 # разделы с платным размещением Михаила (25.10): AI-агенты, ChatGPT, Telegram, чат-боты, парсинг, БД
                 "ai-iskusstvenniy-intellekt/ai-agenty", "ai-iskusstvenniy-intellekt/chat-gpt",
                 "ai-iskusstvenniy-intellekt/boty-s-ai", "ai-iskusstvenniy-intellekt/rag-bazy-znaniy",
                 "ai-iskusstvenniy-intellekt/ai-n8n", "ai-iskusstvenniy-intellekt/claude",
                 "messengers/telegram", "messengers/boty-dlya-messengerov", "messengers/telegram-mini-apps",
                 "avtomatizaciya-biznesa/ai-avtomatizatsiya", "avtomatizaciya-biznesa/biznes-analitika",
                 "avtomatizaciya-biznesa/boty-dlya-biznesa", "avtomatizaciya-biznesa/tablicy-i-excel",
                 "avtomatizaciya-biznesa/make-com", "brauzery/avtomatizacya-beauzerov",
                 "programmirovanie/programmirovanie-sozdanie-mvp", "programmirovanie/razrabotka-crm-i-erp"]
FL_FEED_MAX_PAGES = 15   # общая лента заказов (?kind=1): ~30 заказов на страницу, 2-3 страницы в сутки
FRL_CATS = ("Веб-разработка и IT", "ИИ и Нейросети", "Бизнес, Продажи и Консалтинг")

PROFILE = re.compile(
    r"(\bai\b|llm|\bml\b|gpt|genai|\brag\b|agent\w*|агент\w*|\bии\b|нейросет\w*|automat\w*|автоматиз\w*|rpa|n8n|"
    r"langchain|prompt\w*|python|clickhouse|metabase|\bbi\b|dwh|\betl\b|аналит\w*|данн\w+|"
    r"парс\w*|scrap\w*|zenno\w*|\bbas\b|браузер\w*|бот\w*|bot\b|telegram|телеграм\w*|mini ?app|"
    r"\bapi\b|интеграц\w*|crm|amo\w*|битрикс\w*|make\.com|дашборд\w*|dashboard|"
    r"отч[её]т\w*|google sheets|таблиц\w*|скрипт\w*|маркетплейс\w*|wildberries|ozon)", re.I)
EXCLUDE = re.compile(r"\b(frontend|фронтенд|ios|android|flutter|unity|unreal|java|kotlin|php|ruby|golang|"
                     r"laravel|ларавел\w*|full-?stack|фулстек\w*|wordpress|вордпресс\w*|"
                     r"junior|джун\w*|стаж[её]р\w*)\b|c\+\+|\.net\b|c#|\b1[сc]\b|битрикс|bitrix", re.I)  # 1С и Битрикс: нет опыта
NOISE = re.compile(r"(видео|ролик\w*|reels|аватар\w*|картин\w*|изображен\w*|анимац\w*|наставник\w*|курс\w*|"
                   r"отзыв\w*|опрос\w*|накрутк\w*|оператор\w*|рекрут\w*|менеджер\w*|чаттер\w*|блогер\w*|ведение|1с|1c|revit|"
                   r"autocad|чертеж\w*|дизайн\w*|верстк\w*|презентац\w*|логотип\w*|seo\b|smm\b|копирайт\w*|"
                   r"рерайт\w*|статей|статьи|перевод\w*|ручн\w+ (ввод|загрузк|систематиз)\w*|"
                   r"вуз\w*|диплом\w*|курсов\w*|итогов\w* работ\w*|figma|авито|wb\b|озон|ozon|граббер|"
                   r"раздел\w* (пд|рд|вк|ов|ос|эос|иос)|\bкд\b|ескд|\bпд\b|\bрд\b|ремонт\w*|вентиляц\w*|отоплен\w*|электротехн\w*|"
                   r"конструкторск\w*|ретопол\w*|айдентик\w*|семантическ\w*|репетитор\w*|лид(ы|ов)\b|привлечен\w*|igaming|\bux\b|"
                   r"фото\w*|ретуш\w*|бренд\w*|компас|3d\b|учебн\w*|хайлайт\w*|монтаж\w*|судебн\w*|экспертиз\w*|"
                   r"консультац\w*|1-2 консульт\w*|диссертац\w*|реферат\w*|реклам\w*|рассыл\w*|тестировщ\w*|тестирован\w*)", re.I)
# Набор на военные контракты под видом IT-заказов («подразделение», «единовременная выплата», от 300 000 ₽)
MILITARY = re.compile(r"(подразделени\w*|единовр\w*|бпла|беспилот\w*|спецпроект\w*|рубикон|элитн\w*|"
                      r"современн\w* техническ\w*|работ\w* с техникой|контракт\w* с мо\b|военн\w* контракт\w*)", re.I)
LONG_TERM = re.compile(r"(долгосроч\w*|постоянн\w* (основе|сотрудничеств\w*|работ\w*)|на постоянку|"
                       r"абонентск\w*|ежемесячн\w*|в месяц|/мес|long[- ]term|ongoing)", re.I)
OFFICE = re.compile(r"(в офисе|из офиса|офис\s+\d|гибрид\w*|on[- ]?site|релокаци\w*)", re.I)


def get(url, timeout=25, tries=3):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "ru,en;q=0.8"})
    for n in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8", "replace")
        except OSError:
            if n == tries - 1:
                raise
            time.sleep(2 * (n + 1))


def rates():
    try:
        d = json.loads(get("https://www.cbr-xml-daily.ru/daily_json.js"))
        return {"RUB": 1.0, "USD": d["Valute"]["USD"]["Value"], "EUR": d["Valute"]["EUR"]["Value"]}
    except Exception:
        return {"RUB": 1.0, "USD": USD_RUB_FALLBACK, "EUR": EUR_RUB_FALLBACK}


def strip(s):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()


def item(source, id_, title, url, kind, text, published, budget, currency, responses=None, premium=False):
    return {"source": source, "id": f"{source}:{id_}", "title": title, "url": url, "kind": kind, "text": text,
            "published": published, "budget": budget, "currency": currency, "responses": responses,
            "premium": premium}


MONTHS = ["январ", "феврал", "март", "апрел", "ма", "июн", "июл", "август", "сентябр", "октябр", "ноябр", "декабр"]


def fl_age(s, now):
    """«2 часа 24 минуты назад», «5 минут назад», «Вчера в 14:20» -> datetime публикации (МСК)."""
    s = s.lower()
    if "назад" in s:
        delta = dt.timedelta()
        for n, unit in re.findall(r"(\d+)\s*(минут|мин|час|дн|день|дня)", s):
            n = int(n)
            delta += dt.timedelta(minutes=n) if unit.startswith("мин") else \
                dt.timedelta(hours=n) if unit.startswith("час") else dt.timedelta(days=n)
        return now - delta
    m = re.search(r"вчера\s*в?\s*(\d\d):(\d\d)", s)
    if m:
        return (now - dt.timedelta(days=1)).replace(hour=int(m[1]), minute=int(m[2]), second=0, microsecond=0)
    m = re.search(r"(\d{1,2})\s+(" + "|".join(MONTHS) + r")\w*,?\s*(\d\d):(\d\d)", s)  # «18 октября, 09:36»
    if m:
        d = now.replace(month=MONTHS.index(m[2]) + 1, day=int(m[1]), hour=int(m[3]), minute=int(m[4]),
                        second=0, microsecond=0)
        return d.replace(year=d.year - 1) if d > now else d
    return None


def fl_parse(page, out, now):
    """Карточки FL.ru со страницы списка в out; возвращает самую старую дату публикации на странице."""
    oldest = None
    for block in re.split(r'(?=<div[^>]+id="project-item\d+")', page)[1:]:
        pid = re.search(r'id="project-item(\d+)"', block).group(1)
        tm = re.search(r'id="prj_name_\d+"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', block, re.S)
        when = strip((re.search(r'text-gray-opacity-4[^>]*>(.*?)</span>', block, re.S) or [None, ""])[1])
        pub = fl_age(when, now)
        if pub and (oldest is None or pub < oldest):
            oldest = pub
        if not tm or pid in out:
            continue
        price = strip((re.search(r'b-post__price[^>]*>(.*?)</div>', block, re.S) or [None, ""])[1])
        desc = strip((re.search(r'class="b-post__txt text-5 ?"[^>]*>(.*?)</div>', block, re.S) or [None, ""])[1])
        kind = strip((re.search(r'b-post__bold[^>]*>(.*?)</span>', block, re.S) or [None, "Проект"])[1]) or "Проект"
        resp = re.search(r"(\d+)\s*ответ", strip(block))
        if "Исполнитель определ" in block:
            kind = "Исполнитель определен"
        budget = cur = None
        sm = re.search(r"([\d\s\u00a0]{3,})\s*(₽|\$|€)", price)
        if sm:
            budget, cur = int(re.sub(r"\D", "", sm.group(1))), {"₽": "RUB", "$": "USD", "€": "EUR"}[sm.group(2)]
        out[pid] = item("fl", pid, strip(tm.group(2)), "https://www.fl.ru" + tm.group(1), kind,
                        f"{desc} Бюджет: {price or 'по договоренности'}", pub.isoformat() if pub else None,
                        budget, cur, int(resp.group(1)) if resp else 0)
    return oldest


def src_fl():
    """FL.ru: вся лента заказов (?kind=1) до границы окна свежести + профильные категории.
    RSS закрыт robots.txt (Disallow */rss/*), поиск /search/projects отдает пустую страницу без JS."""
    out, now = {}, dt.datetime.now(MSK)
    for n in range(1, FL_FEED_MAX_PAGES + 1):
        url = "https://www.fl.ru/projects/?kind=1" if n == 1 else f"https://www.fl.ru/projects/page-{n}/?kind=1"
        oldest = fl_parse(get(url), out, now)
        if oldest and oldest < SINCE:
            break
    for cat in FL_CATEGORIES:
        fl_parse(get(f"https://www.fl.ru/projects/category/{cat}/"), out, now)
    return list(out.values())


def src_freelance_ru():
    """freelance.ru: первая страница ленты /task (?page= закрыт robots, /search ищет исполнителей, не заказы).
    Лента общая на все категории, ~25 заказов за 1-2 дня; берем только IT, ИИ и бизнес."""
    out = []
    page = get("https://freelance.ru/task")
    for block in re.split(r'<article class="task-card', page)[1:]:
        tm = re.search(r'href="/task/view/(\d+)"[^>]*title="([^"]*)"', block)
        cat = (re.search(r'task-chip--cat">([^<]+)', block) or [None, ""])[1].strip()
        if not tm or cat not in FRL_CATS:
            continue
        chips = ", ".join(strip(c) for c in re.findall(r'class="task-chip">([^<]+)', block) if not c.startswith("+"))
        desc = strip((re.search(r'task-card__desc">(.*?)</p>', block, re.S) or [None, ""])[1])
        price = strip((re.search(r'task-card__budget[ "][^>]*>(.*?)</div>', block, re.S) or [None, ""])[1])
        pub = None
        dm = re.search(r'title="(\d\d)\.(\d\d)\.(\d{4}) (\d\d):(\d\d)"', block)
        if dm:  # время на сайте московское
            d, mo, y, h, mi = map(int, dm.groups())
            pub = dt.datetime(y, mo, d, h, mi, tzinfo=MSK).isoformat()
        budget = cur = None
        sm = re.search(r"([\d\s\u00a0]{2,})\s*₽", price)
        if sm:
            budget, cur = int(re.sub(r"\D", "", sm.group(1))), "RUB"
        out.append(item("frl", tm.group(1), html.unescape(tm.group(2)), f"https://freelance.ru/task/view/{tm.group(1)}",
                        cat, f"{desc} Навыки: {chips}. Бюджет: {price or 'по договоренности'}", pub, budget, cur,
                        premium="task-badge--premium" in block))
    return out


def fl_details(v):
    """Страница заказа FL.ru: полное ТЗ, безопасная сделка, выбран ли исполнитель. Для кандидатов после фильтра."""
    page = get(v["url"])
    m = re.search(r'id="projectp\d+"[^>]*>(.*?)</div>', page, re.S)
    if m:
        v["text"] = strip(m.group(1)) + " " + v["text"][v["text"].find("Бюджет:"):]
    flat = strip(page)
    v["safe_deal"] = "Безопасная сделка" in flat
    taken = re.search(r"Заказчик выбрал исполнителя|Исполнитель определ|Заказ закрыт|Проект закрыт", flat)
    return "taken" if taken else None


SINCE = None  # граница окна свежести, задает main()
SOURCES = {"fl": src_fl}  # с 26.10 только FL.ru (Михаил: искать на fl.ru, не на freelance.ru)


def classify(v, r, since):
    blob = f"{v['title']} {v['text']}"
    if v["kind"] == "Исполнитель определен":
        return "taken"
    # вакансии FL с 27.10 не режем целиком: по стеку и без офиса идут в выдачу с пометкой (решение Михаила)
    v["vacancy"] = v["kind"].startswith("Вакансия")
    if v["published"] and dt.datetime.fromisoformat(v["published"]) < since:
        return "old"
    # платный отклик сгорает впустую: заказ висит, а исполнителя уже нашли (правило Михаила 25.10)
    resp = v["responses"] or 0
    age_h = (dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(v["published"])).total_seconds() / 3600 if v["published"] else None
    v["age_h"] = round(age_h, 1) if age_h is not None else None
    if resp > HOT_RESPONSES or (age_h and age_h > STALE_HOURS and resp > STALE_RESPONSES):
        return "stale"
    if NOISE.search(v["title"]) or MILITARY.search(blob) or OFFICE.search(blob):
        return "off_profile"
    head = f"{v['title']} {v['text'][:300]}"
    # профиль: слово из стека в заголовке или хотя бы два разных в начале описания (общая лента шумная)
    strong = PROFILE.search(v["title"]) or PROFILE.search(v["text"][:300])  # с 27.10 хватает одного слова стека
    if EXCLUDE.search(head) or not strong:
        return "off_profile"
    rub = v["budget"] * r[v["currency"]] if v["budget"] and v["currency"] in r else None
    v["budget_rub"] = round(rub) if rub else None
    v["big"] = bool(rub and rub >= BIG_MIN_USD * r["USD"])
    v["hot"] = (v["responses"] or 0) > HOT_RESPONSES
    if rub is not None and rub >= WORK_MIN_RUB:
        return "work"
    if rub is None or LONG_TERM.search(blob):
        return "clarify"
    return "rep" if rub >= REP_MIN_RUB else "low_budget"


FRESH_HOURS, FRESH_MAX_RESP = 2, 10
ALERTED = HERE / "alerted.json"


def fresh():
    """Ежечасно 9-21 по Минску (решение Михаила 27.10): хорошие заказы закрываются за часы.
    Только первые 2 страницы ленты, без категорий; шлет в Telegram сам, без черновиков."""
    import os
    import urllib.parse
    global SINCE
    since = SINCE = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=FRESH_HOURS)
    alerted = set(json.loads(ALERTED.read_text())) if ALERTED.exists() else set()
    sent = set(json.loads(SENT.read_text())) if SENT.exists() else set()
    r, out, now = rates(), {}, dt.datetime.now(MSK)
    for n in (1, 2):
        fl_parse(get("https://www.fl.ru/projects/?kind=1" if n == 1 else f"https://www.fl.ru/projects/page-{n}/?kind=1"),
                 out, now)
    hits = []
    for v in out.values():
        if v["id"] in alerted or v["id"] in sent or (v["responses"] or 0) >= FRESH_MAX_RESP:
            continue
        if classify(v, r, since) not in ("work", "clarify", "rep"):
            continue
        try:
            if fl_details(v) == "taken" or classify(v, r, since) not in ("work", "clarify", "rep"):
                continue
        except OSError:
            pass
        hits.append(v)
        alerted.add(v["id"])
    ALERTED.write_text(json.dumps(sorted(alerted)[-2000:]))
    if hits:
        lines = ["Фриланс: свежее на FL.ru"]
        for v in hits:
            b = f"{v['budget_rub']:,} ₽".replace(",", " ") if v.get("budget_rub") else "бюджет не указан"
            tag = " (вакансия)" if v.get("vacancy") else ""
            lines.append(f"- {v['title'][:90]}{tag}: {b}, {v['responses'] or 0} откл, {v.get('age_h')} ч назад\n  {v['url']}")
        lines.append("Черновик: напиши в треде радара «черновик <номер заказа>»")
        tok, chat = os.environ.get("TG_BOT_TOKEN"), os.environ.get("TG_CHAT_ID")
        if tok and chat:
            urllib.request.urlopen(f"https://api.telegram.org/bot{tok}/sendMessage",
                                   data=urllib.parse.urlencode({"chat_id": chat, "text": "\n".join(lines),
                                                                "disable_web_page_preview": "true"}).encode(), timeout=20)
    print(json.dumps({"fresh": len(hits), "ids": [v["id"] for v in hits],
                      "titles": [v["title"][:80] for v in hits]}, ensure_ascii=False))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=36)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--no-details", action="store_true", help="не открывать страницы кандидатов FL.ru")
    ap.add_argument("--fresh", action="store_true",
                    help="ежечасная проверка: 2 страницы ленты, заказы до 2 ч и меньше FRESH_MAX_RESP откликов, алерт в Telegram")
    a = ap.parse_args()
    if a.fresh:
        return fresh()
    global SINCE
    since = SINCE = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=a.hours)
    seen = set() if a.all or not SEEN.exists() else set(json.loads(SEEN.read_text()))
    sent = set(json.loads(SENT.read_text())) if SENT.exists() else set()
    r = rates()
    stats, errors, buckets = {}, {}, {"work": [], "clarify": [], "rep": [], "stale": [], "low_budget": [], "off_profile": []}
    for name, fn in SOURCES.items():
        try:
            items = fn()
        except Exception as e:  # биржа упала: фиксируем и идем дальше
            errors[name] = f"{type(e).__name__}: {e}"[:200]
            continue
        st = stats.setdefault(name, {"fetched": len(items)})
        for v in items:
            if v["id"] in seen or v["id"] in sent:
                st["seen"] = st.get("seen", 0) + 1
                continue
            c = classify(v, r, since)
            if c in buckets and v["source"] == "fl" and not a.no_details:
                try:
                    if fl_details(v) == "taken":
                        c = "taken"
                    else:
                        c = classify(v, r, since)  # переоценка по полному ТЗ
                except OSError:
                    pass
            st[c] = st.get(c, 0) + 1
            if c in buckets:
                buckets[c].append(v)
            if c != "old":
                seen.add(v["id"])
    # сначала крупные и неперегретые, внутри - по бюджету
    # сначала свежие (по 12-часовым корзинам), внутри крупные и по бюджету
    for k in ("work", "clarify", "rep"):
        if k in buckets:
            buckets[k].sort(key=lambda v: ((v.get("age_h") or 99) // 12, not v.get("big"), -(v.get("budget_rub") or 0)))
    RUNS.mkdir(exist_ok=True)
    stamp = dt.datetime.now(MSK).strftime("%Y-%m-%d_%H%M")
    out = {"run": stamp, "rates": r, "work_min_rub": WORK_MIN_RUB, "big_min_usd": BIG_MIN_USD,
           "stats": stats, "errors": errors, **buckets}
    (RUNS / f"{stamp}.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))
    if not a.all:
        SEEN.write_text(json.dumps(sorted(seen)))
    print(json.dumps({"run": stamp, "stats": stats, "errors": errors,
                      **{k: len(v) for k, v in buckets.items()}, "file": str(RUNS / f"{stamp}.json")},
                     ensure_ascii=False, indent=1))


if __name__ == "__main__":
    sys.exit(main())
