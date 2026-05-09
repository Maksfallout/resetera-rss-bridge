"""
Публикатор: раз в сутки выбирает лучшую статью через ИИ-куратора,
скачивает её полный текст, делает рерайт+перевод через ИИ-рерайтера,
и кладёт результат в feed.xml для Hooppy.
"""

import json
import os
import re
import sys
from datetime import datetime, timezone

import requests
from feedgen.feed import FeedGenerator

from common import (
    POOL_FILE, PUBLISHED_FILE, OUTPUT_FEED, ITEM_LINK_PLACEHOLDER,
    REQUEST_TIMEOUT,
    load_json, save_json, get_full_text,
)

# ====== НАСТРОЙКИ ИИ ======
CHADGPT_API_URL = "https://ask.chadgpt.ru/api/public/gpt-5-mini"
TEXT_FOR_REWRITER_LIMIT = 8000      # сколько знаков статьи отдаём ИИ
MAX_ITEMS_IN_FEED = 10              # сколько последних постов держим в feed.xml
# ==========================

FEED_TITLE = "Gaming Daily Pick"
FEED_LINK = "https://www.resetera.com/forums/gaming-headlines.54/"
FEED_DESCRIPTION = "Главная игровая новость дня — рерайт на русском"

CURATOR_PROMPT = """Ты — главный редактор популярного канала о консольных играх и игровой индустрии. Перед тобой список заголовков свежих новостей за последние сутки. Выбери ОДИН — самый интересный для геймерской аудитории.

Критерии выбора (в порядке приоритета):

ПЕРВЫЙ ПРИОРИТЕТ — новости важные для российской аудитории:
- Официальный выход игры в России (или подтверждение выхода), включая Steam, PS Store, Xbox
- Новости о ценах и доступности консолей и игр для российского рынка
- Новости о сервисах, доступных в России (Game Pass, PS Plus и т.д.)
- Крупные анонсы игр от студий, у которых большая российская аудитория (GTA, FIFA/FC, Call of Duty, CS, Dota, Minecraft и т.п.)
- Новости об уходе или возвращении игровых компаний на российский рынок

ВТОРОЙ ПРИОРИТЕТ — важные мировые новости игровой индустрии:
- Крупные анонсы новых игр от известных студий
- Выход крупных игр (релизы AAA и крупных инди)
- Заявления CEO крупных компаний (Sony, Microsoft, Nintendo, Valve, Take-Two)
- Скандалы и конфликты с широким резонансом
- Крупные цифры продаж или финансовые результаты
- Новости о новых консолях и платформах

ИЗБЕГАЙ:
- Профсоюзных новостей и трудовых споров (не интересно широкой аудитории)
- Косплея, фан-арта, фан-проектов
- Технических подробностей для разработчиков
- Новостей о конкретных стримерах или ютуберах (если только это не касается платформы в целом)

Если все новости средние — всё равно выбери лучшую из имеющихся.

Список заголовков:
{titles}

Верни ответ СТРОГО в формате JSON, без других слов и без markdown:
{{"guid": "выбранный_guid", "reason": "одно предложение почему"}}"""


REWRITER_PROMPT = """Ты — автор поста для русскоязычного канала о консольных играх и игровой индустрии. Создай ЗАГОЛОВОК и ТЕКСТ поста по строгим правилам.

ПРАВИЛА ЯЗЫКА (это абсолютный приоритет):
1. Только русский. Полностью БЕЗ англицизмов в тексте.
2. Имена и фамилии людей — ПОЛНОСТЬЮ транслитерируй на русский. Не "Мэтт Piscatella", а "Мэтт Пискателла". Не оставляй ни одной части имени на латинице.
3. Названия КОМПАНИЙ, ИГР, ПЛАТФОРМ — оставляй на оригинальном языке: PlayStation, Nintendo Switch, Xbox Series X, Cyberpunk 2077, Take-Two, Rockstar, Circana, Bloomberg, Wall Street Journal.
4. Англицизмы заменяй на русские эквиваленты:
   - «казуальные / casual игроки» → «обычные игроки» или «игроки на досуге»
   - «хардкорные» → «увлечённые» / «преданные»
   - «релиз» → «выход» / «выпуск»
   - «эксклюзив» → «эксклюзивная игра»
   - «гейм-плей» → «игровой процесс»
   - «контент» → «материалы» / «содержимое»
   - «тайтл» → «игра»
   - «ивент» → «событие»
   - «фича» → «возможность» / «особенность»
   - «коммьюнити» → «сообщество»
   - «стартап» → «молодая компания»

ПРАВИЛА ЖИВОГО РУССКОГО ЯЗЫКА:
5. Пиши как живой человек, а не как переводчик с английского. Избегай канцеляризмов и буквальных переводов.
   - НЕЛЬЗЯ: "домохозяйства с высоким доходом" → ПИШИ: "обеспеченные семьи" или "состоятельные люди"
   - НЕЛЬЗЯ: "осуществляет производство" → ПИШИ: "делает" или "выпускает"
   - НЕЛЬЗЯ: "в свете последних событий" → ПИШИ: "после того что случилось"
   - НЕЛЬЗЯ: "является важным фактором" → ПИШИ: "это важно потому что"
6. Используй естественные обороты: "получается, что", "выходит так", "оказывается", "судя по всему", "похоже на то что".

ПРАВИЛА ЛОГИКИ И СВЯЗНОСТИ (важно!):
7. Текст должен быть ЛОГИЧЕСКИ СВЯЗНЫМ. Каждое следующее предложение должно вытекать из предыдущего или развивать его.
8. Если приводишь факт — объясняй ЗАЧЕМ читателю это знать и КАК это связано с темой поста. Не просто "у PS5 продано 100 млн", а "PS5 продано 100 млн — это значит, что у GTA 6 уже огромная база покупателей, и её цена напрямую влияет на спрос".
9. Если в исходной статье есть причинно-следственная связь между двумя темами — обязательно её сохрани и объясни читателю явно. Не оставляй "висящие" факты без связи с основной темой.
10. Перед окончательной выдачей ПРОВЕРЬ САМ СЕБЯ:
    - Понятна ли связь между предложениями?
    - Все ли упомянутые факты работают на главную мысль поста?
    - Нет ли резких смысловых разрывов?
    - Если что-то нелогично — перепиши.

ПРАВИЛА ФОРМЫ:
11. Объём ТЕКСТА (без заголовка): от 500 до 850 знаков, целевой объём — около 650.
12. Заголовок: краткий, до 100 знаков, на русском, цепляющий, без англицизмов. ЗАГОЛОВОК ОБЯЗАТЕЛЬНО ЗАКАНЧИВАЕТСЯ ТОЧКОЙ (если только это не вопрос или восклицание — тогда соответствующий знак).
13. Стиль — разговорный, живой, как будто рассказываешь другу.
14. Эмодзи — ровно 1 или 2 на пост (в тексте), к месту.
15. НЕ добавляй: ссылки, хештеги, призывы подписаться.
16. НЕ начинай текст со штампов: «Сегодня...», «На днях...», «Стало известно, что...», «Как сообщает...».
17. Перед окончательной выдачей ПРОВЕРЬ ПУНКТУАЦИЮ: точка ставится сразу после слова без пробела ("слово." — правильно, "слово ." — неправильно). Все знаки препинания должны быть на своих местах.
18. Если в исходнике мусор (меню, формы подписки, навигация) — игнорируй, бери только суть.

СТРУКТУРА ТЕКСТА — ОБЯЗАТЕЛЬНО:
19. Раздели текст на 2-3 абзаца с пустой строкой между ними (символ \n\n).
20. Каждый абзац — одна законченная мысль:
- Первый абзац: суть события (что случилось и почему это важно)
- Второй абзац: подробности и контекст
- Третий абзац (если нужен): последствия или вывод для читателя
21. НЕ пиши сплошным текстом без разбивки — это тяжело читать.

Текст исходной новости:
{article_text}

Верни ответ СТРОГО в формате JSON, без других слов, без markdown-обёрток:
{{"title": "русский заголовок с точкой в конце.", "text": "русский текст поста с правильной пунктуацией"}}"""


def call_chadgpt(prompt, api_key):
    """Делает запрос к ChadGPT API. Возвращает текст ответа или None."""
    if not api_key:
        print("✗ CHADGPT_API_KEY не задан!")
        return None

    payload = {"message": prompt, "api_key": api_key}
    try:
        r = requests.post(CHADGPT_API_URL, json=payload, timeout=60)
        if r.status_code != 200:
            print(f"✗ HTTP {r.status_code}: {r.text[:200]}")
            return None
        data = r.json()
        if not data.get("is_success"):
            print(f"✗ Ошибка API: {data.get('error_message')}")
            return None
        used = data.get("used_sparks_count", 0)
        print(f"  Потрачено искр: {used}")
        return data.get("response", "").strip()
    except Exception as e:
        print(f"✗ Запрос упал: {e}")
        return None


def curator_pick(pool_items, published_guids, api_key):
    """Отдаёт куратору список заголовков, получает выбор."""
    candidates = [it for it in pool_items if it["guid"] not in published_guids]
    if not candidates:
        print("В пуле нет неопубликованных кандидатов")
        return None

    print(f"\nКуратор выбирает из {len(candidates)} кандидатов...")
    titles_text = "\n".join(
        f"- guid: {it['guid']} | {it['title']}" for it in candidates
    )
    prompt = CURATOR_PROMPT.format(titles=titles_text)

    answer = call_chadgpt(prompt, api_key)
    if not answer:
        return None

    # Парсим JSON-ответ. ИИ иногда оборачивает в ```json``` — убираем
    answer = re.sub(r"^```(?:json)?\s*", "", answer)
    answer = re.sub(r"\s*```$", "", answer)
    try:
        decision = json.loads(answer)
        chosen_guid = decision.get("guid")
        reason = decision.get("reason", "")
    except Exception as e:
        print(f"✗ Не смог распарсить ответ куратора: {e}")
        print(f"  Ответ был: {answer[:300]}")
        return None

    chosen = next((it for it in candidates if it["guid"] == chosen_guid), None)
    if not chosen:
        print(f"✗ Куратор вернул guid {chosen_guid}, но его нет в кандидатах")
        return None

    print(f"\n✓ Выбрано: {chosen['title']}")
    print(f"  Причина: {reason}")
    return chosen

def is_duplicate_story(chosen_title, published_titles, api_key):
    """Проверяет через ИИ — не является ли выбранная статья дублём уже опубликованных."""
    if not published_titles:
        return False

    titles_list = "\n".join(f"- {t}" for t in published_titles[-10:])
    prompt = (
        f"Перед тобой заголовок новой статьи и список уже опубликованных заголовков.\n\n"
        f"Новая статья: {chosen_title}\n\n"
        f"Уже опубликованные заголовки:\n{titles_list}\n\n"
        f"Вопрос: рассказывает ли новая статья по сути об ТОМ ЖЕ событии что и одна из "
        f"уже опубликованных? Учитывай смысл, а не точное совпадение слов.\n\n"
        f"Верни СТРОГО одно слово: YES если это дубль, NO если это другая история."
    )
    answer = call_chadgpt(prompt, api_key)
    if not answer:
        return False
    is_dup = answer.strip().upper().startswith("YES")
    if is_dup:
        print(f"  ⚠ Дубль обнаружен: '{chosen_title}' похожа на уже опубликованную")
    return is_dup

def rewrite_article(article_text, api_key):
    """Просит ИИ сделать рерайт+перевод. Возвращает (title, text) или None."""
    if len(article_text) > TEXT_FOR_REWRITER_LIMIT:
        article_text = article_text[:TEXT_FOR_REWRITER_LIMIT]
        last_dot = article_text.rfind(". ")
        if last_dot > TEXT_FOR_REWRITER_LIMIT * 0.7:
            article_text = article_text[:last_dot + 1]

    print(f"\nРерайтер пишет пост (на вход {len(article_text)} знаков)...")
    prompt = REWRITER_PROMPT.format(article_text=article_text)
    answer = call_chadgpt(prompt, api_key)
    if not answer:
        return None

    # Чистим markdown-обёртки если ИИ их добавил
    answer = re.sub(r"^```(?:json)?\s*", "", answer)
    answer = re.sub(r"\s*```$", "", answer)
    try:
        data = json.loads(answer)
        title = data.get("title", "").strip()
        text = data.get("text", "").strip()
        if not title or not text:
            print("✗ В ответе нет title или text")
            print(f"  Ответ: {answer[:300]}")
            return None
        print(f"✓ Заголовок: {title}")
        print(f"✓ Текст: {len(text)} знаков")
        total = len(title) + len(text)
        print(f"  Сумма title+text: {total} символов")
        return title, text
    except Exception as e:
        print(f"✗ Не смог распарсить JSON рерайтера: {e}")
        print(f"  Ответ был: {answer[:300]}")
        return None


def add_to_feed(item, post_text):
    """Добавляет одну запись в feed.xml (или создаёт его)."""
    # Загружаем существующие записи если есть
    existing = []
    if os.path.exists(OUTPUT_FEED):
        try:
            import feedparser
            parsed = feedparser.parse(OUTPUT_FEED)
            for e in parsed.entries:
                pubdate = datetime.now(timezone.utc)
                if e.get("published_parsed"):
                    try:
                        pubdate = datetime(*e.published_parsed[:6], tzinfo=timezone.utc)
                    except Exception:
                        pass
                existing.append({
                    "title": e.get("title", ""),
                    "guid": e.get("id", e.get("link", "")),
                    "pubdate": pubdate,
                    "fulltext": e.get("summary", ""),
                })
        except Exception as e:
            print(f"Не удалось прочитать старый feed: {e}")

    # Новая запись
    new_entry = {
        "title": item["title"],
        "guid": item["guid"],
        "pubdate": datetime.now(timezone.utc),
        "fulltext": post_text,
    }

    # Объединяем, сортируем по дате (свежие сверху), оставляем последние N
    all_items = [new_entry] + [
        e for e in existing if e["guid"] != new_entry["guid"]
    ]
    all_items.sort(key=lambda x: x["pubdate"], reverse=True)
    all_items = all_items[:MAX_ITEMS_IN_FEED]

    # Пишем feed.xml
    fg = FeedGenerator()
    fg.title(FEED_TITLE)
    fg.link(href=FEED_LINK, rel="alternate")
    fg.description(FEED_DESCRIPTION)
    fg.language("ru")

    for it in all_items:
        fe = fg.add_entry(order='append')
        fe.title(it["title"])
        fe.link(href=ITEM_LINK_PLACEHOLDER)
        fe.guid(it["guid"], permalink=False)
        fe.pubDate(it["pubdate"])
        fe.description(it["fulltext"])

    fg.rss_file(OUTPUT_FEED, pretty=True)
    print(f"\n✓ feed.xml обновлён ({len(all_items)} записей)")


def main():
    print(f"=== Публикатор: {datetime.now(timezone.utc).isoformat()} ===")

    api_key = os.environ.get("CHADGPT_API_KEY")
    if not api_key:
        print("✗ Переменная CHADGPT_API_KEY не задана. Прерываюсь.")
        sys.exit(1)

    pool = load_json(POOL_FILE, {"items": []})
    pool_items = pool.get("items", [])
    if not pool_items:
        print("Пул пустой, нечего публиковать")
        return

    published = load_json(PUBLISHED_FILE, {"guids": [], "titles": []})
    published_guids = set(published.get("guids", []))

    # 1. Куратор выбирает статью
    chosen = curator_pick(pool_items, published_guids, api_key)
    if not chosen:
        print("Куратор не смог выбрать. Завершаю.")
        return

    # 1b. Проверяем на смысловой дубль
    published_data = load_json(PUBLISHED_FILE, {"guids": [], "titles": []})
    published_titles = published_data.get("titles", [])
    if is_duplicate_story(chosen["title"], published_titles, api_key):
        print("Выбранная статья — смысловой дубль. Помечаю GUID и завершаю.")
        published_guids.add(chosen["guid"])
        save_json(PUBLISHED_FILE, {
            "guids": list(published_guids)[-100:],
            "titles": published_titles
        })
        return
    
    # 2. Скачиваем полный текст выбранной
    print(f"\nСкачиваю полный текст: {chosen['original_url']}")
    fulltext, _ = get_full_text(chosen["original_url"])
    if not fulltext:
        print("✗ Не удалось скачать текст. Помечу как опубликованную, чтобы не выбирать снова.")
        published_guids.add(chosen["guid"])
        save_json(PUBLISHED_FILE, {"guids": list(published_guids)[-100:]})
        return

    # 3. Рерайтер пишет пост на русском
    rewrite_result = rewrite_article(fulltext, api_key)
    if not rewrite_result:
        print("✗ Рерайт не получился. Не публикую, не помечаю.")
        return
    post_title, post_text = rewrite_result

    # 4. Добавляем в feed.xml — используем переведённый заголовок
    chosen_with_ru_title = dict(chosen)
    chosen_with_ru_title["title"] = post_title
    add_to_feed(chosen_with_ru_title, post_text)

    # 5. Помечаем как опубликованную
    published_guids.add(chosen["guid"])
    save_json(PUBLISHED_FILE, {"guids": list(published_guids)[-100:]})

    print("\n=== Публикатор завершил работу ===")

    # 6. Помечаем как опубликованную — сохраняем GUID и заголовок
    published_guids.add(chosen["guid"])
    published_titles.append(post_title)
    save_json(PUBLISHED_FILE, {
        "guids": list(published_guids)[-100:],
        "titles": published_titles[-30:]   # храним последние 30 заголовков
    })

if __name__ == "__main__":
    main()
