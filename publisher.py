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

import time
import shutil
from urllib.parse import urlparse

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

# ====== НАСТРОЙКИ КАРТИНОК ======
IMAGE_API_URL = "https://ask.chadgpt.ru/api/public/imagen-4-fast/imagine"
IMAGE_CHECK_URL = "https://ask.chadgpt.ru/api/public/check"
IMAGE_ASPECT_RATIO = "16:9"
IMAGES_DIR = "images"
IMAGES_RETENTION_DAYS = 90
IMAGE_WAIT_TIMEOUT = 120        # макс секунд ждать пока картинка сгенерится
IMAGE_CHECK_INTERVAL = 5        # каждые сколько секунд опрашивать статус
# Публичный URL базы, через который Hooppy будет скачивать картинки
IMAGE_PUBLIC_BASE = "https://maksfallout.github.io/resetera-rss-bridge/images"
# ================================

FEED_TITLE = "Gaming Daily Pick"
FEED_LINK = "https://www.resetera.com/forums/gaming-headlines.54/"
FEED_DESCRIPTION = "Главная игровая новость дня — рерайт на русском"

CURATOR_PROMPT = """Ты — главный редактор популярного канала о консольных играх и игровой индустрии. Перед тобой список заголовков свежих новостей за последние сутки. Выбери ОДИН — самый интересный для геймерской аудитории.

Критерии:
- Значимость для индустрии или сообщества (анонсы, релизы, заявления крупных компаний, скандалы, цифры продаж, новости платформ)
- Способность вызвать обсуждение, эмоцию, любопытство
- Баланс между важностью и интересностью

Избегай: мелких локальных новостей, нишевых обзоров инди-игр, статей о фан-творчестве и косплее (если это не часть крупного события).

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
11. Объём ТЕКСТА (без заголовка): от 450 до 750 знаков, целевой объём — около 600.
12. Заголовок: краткий, до 100 знаков, на русском, цепляющий, без англицизмов. ЗАГОЛОВОК ОБЯЗАТЕЛЬНО ЗАКАНЧИВАЕТСЯ ТОЧКОЙ (если только это не вопрос или восклицание — тогда соответствующий знак).
13. Стиль — разговорный, живой, как будто рассказываешь другу.
14. Эмодзи — ровно 1 или 2 на пост (в тексте), к месту.
15. НЕ добавляй: ссылки, хештеги, призывы подписаться.
16. НЕ начинай текст со штампов: «Сегодня...», «На днях...», «Стало известно, что...», «Как сообщает...».
17. Перед окончательной выдачей ПРОВЕРЬ ПУНКТУАЦИЮ: точка ставится сразу после слова без пробела ("слово." — правильно, "слово ." — неправильно). Все знаки препинания должны быть на своих местах.
18. Если в исходнике мусор (меню, формы подписки, навигация) — игнорируй, бери только суть.

Текст исходной новости:
{article_text}

Верни ответ СТРОГО в формате JSON, без других слов, без markdown-обёрток:
{{"title": "русский заголовок с точкой в конце.", "text": "русский текст поста с правильной пунктуацией"}}"""

IMAGE_PROMPT_GENERATOR = """Ты — арт-директор. На основе текста новости из консольной игровой индустрии создай ОДИН промпт на АНГЛИЙСКОМ языке для генерации иллюстрации.

ГЛАВНЫЙ ПРИНЦИП КОМПОЗИЦИИ:
В кадре должна быть ОДНА сцена с ОДНИМ героем и ОДНИМ ключевым объектом. Между ними должна быть ЯСНАЯ ВИЗУАЛЬНАЯ СВЯЗЬ — герой смотрит на объект, держит его, реагирует на него, тянется к нему, отталкивает его. Зритель должен понять смысл за полсекунды.

НЕ ДЕЛАЙ:
- Двух персонажей в одной сцене (если только они не обнимаются/борются — то есть физически связаны)
- Несколько объектов вокруг героя — модель расставит их раздельно, и связи не будет
- Несвязанные элементы (герой слева, объект справа, между ними пустота)

ДЕЛАЙ:
- Один герой + один объект, между которыми есть физический или эмоциональный контакт
- Используй фразы: "looking directly at", "holding tightly", "pointing at", "trying to lift", "shocked by", "pushing away", "running from", "embracing"

ЖЁСТКИЕ ТРЕБОВАНИЯ К СТИЛЮ:
- simple colorful sketch in a notebook, hand-drawn cartoon style, light watercolor over pencil lines, casual minimal doodle
- Соотношение сторон: 16:9 horizontal
- Лицо героя — мультяшное, с яркой эмоцией (удивление, радость, ярость, разочарование, восторг)
- Минималистичный фон — пастельный или белый, без архитектуры

ЖЁСТКИЕ ТРЕБОВАНИЯ К СОДЕРЖАНИЮ:
- АБСОЛЮТНО НИКАКОГО ТЕКСТА: no text, no letters, no words, no signs, no labels, no readable writing anywhere
- Если в сцене ценник — ОН ПУСТОЙ или с восклицательным знаком, БЕЗ цифр и слов
- Если в сцене экран — ОН ПУСТОЙ или с абстрактными формами
- Если в сцене коробка/упаковка — БЕЗ названий и логотипов
- БЕЗ узнаваемых лиц реальных людей
- БЕЗ конкретных названий игр и компаний — нейтральные образы (вместо "PlayStation 5" → "a generic gaming console")

КАК ПРИДУМЫВАТЬ СЦЕНУ — пошагово:
1. Прочитай заголовок. Какая ОДНА главная эмоция?
2. Кто её испытывает? — Один герой.
3. К чему/из-за чего? — Один объект.
4. Какое физическое действие или направление взгляда показывает связь?
5. Опиши в виде "{{герой}} {{действие/эмоция}} {{объект}}".

ХОРОШИЕ ПРИМЕРЫ ОДНОЙ СЦЕНЫ:

Новость про шок цены консоли:
"a cartoon young man with eyes wide open in shock, both hands on his cheeks, staring directly at a giant blank price tag floating in front of him, the tag is comically huge compared to him, plain pastel background, no numbers no text on the tag"

Новость про задержку игры:
"a cartoon character sitting on a giant calendar page, slumping with disappointment, pointing one finger at a single calendar square that is X-ed out, plain background, no numbers or text on calendar"

Новость про споры в индустрии:
"a single cartoon character in the middle, holding a generic game controller, the controller is being pulled by two giant disembodied hands from left and right, character has frustrated face, simple background"

Новость про успех игры:
"a cheering cartoon character lifting a giant generic game controller above their head like a championship trophy, confetti falling around, big smile, plain pastel background"

Новость про ИИ в разработке игр:
"a cartoon developer character looking surprised at a glowing robotic hand that is offering them a generic game controller, the developer reaches out hesitantly, plain background"

ПЛОХИЕ ПРИМЕРЫ (НЕ ДЕЛАЙ):
- "two characters and a price tag and a console" — несколько элементов, связь распадётся
- "a coin with letter on it" — слишком абстрактно
- "a gaming store" — провокация текста на вывесках
- "shocked person on left, console on right" — пространственный разрыв, зритель не свяжет

ПРАВИЛА ПРОМПТА:
- Только английский, длина 50-120 слов
- Опиши: ОДНОГО героя, его эмоцию, его действие/взгляд, ОДИН объект внимания, минимальный фон
- Заверши промпт фразой: "no text, no letters, no words, no logos, no readable writing anywhere in the image"

Заголовок и текст новости:
ЗАГОЛОВОК: {title}
ТЕКСТ: {text}

Верни СТРОГО только готовый промпт на английском, без объяснений, без вступлений, без markdown."""


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

def generate_image_prompt(title, text, api_key):
    """Просит ИИ сгенерировать английский промпт для картинки."""
    print("\nГенерирую промпт для картинки...")
    prompt = IMAGE_PROMPT_GENERATOR.format(title=title, text=text)
    image_prompt = call_chadgpt(prompt, api_key)
    if not image_prompt:
        return None
    # Чистка markdown-обёрток на всякий случай
    image_prompt = re.sub(r"^```\w*\s*", "", image_prompt)
    image_prompt = re.sub(r"\s*```$", "", image_prompt)
    image_prompt = image_prompt.strip()
    print(f"  Промпт ({len(image_prompt)} зн.): {image_prompt[:200]}...")
    return image_prompt


def request_image_generation(image_prompt, api_key):
    """Отправляет запрос на генерацию картинки. Возвращает content_id."""
    print("\nОтправляю запрос на генерацию картинки...")
    payload = {
        "api_key": api_key,
        "prompt": image_prompt,
        "aspect_ratio": IMAGE_ASPECT_RATIO,
    }
    try:
        r = requests.post(IMAGE_API_URL, json=payload, timeout=30)
        if r.status_code != 200:
            print(f"  ✗ HTTP {r.status_code}: {r.text[:200]}")
            return None
        data = r.json()
        if data.get("status") == "failed":
            print(f"  ✗ Ошибка API: {data.get('error_message')}")
            return None
        content_id = data.get("content_id")
        if not content_id:
            print(f"  ✗ Не получен content_id. Ответ: {data}")
            return None
        print(f"  ✓ Запрос принят, content_id: {content_id}")
        return content_id
    except Exception as e:
        print(f"  ✗ Запрос упал: {e}")
        return None


def wait_for_image(content_id, api_key):
    """В цикле опрашивает /check, ждёт готовности. Возвращает URL картинки."""
    print("Ждём готовности картинки...")
    deadline = time.time() + IMAGE_WAIT_TIMEOUT
    payload = {"api_key": api_key, "content_id": content_id}
    while time.time() < deadline:
        time.sleep(IMAGE_CHECK_INTERVAL)
        try:
            r = requests.post(IMAGE_CHECK_URL, json=payload, timeout=30)
            if r.status_code != 200:
                print(f"  ⚠ HTTP {r.status_code} при проверке, продолжаю ждать...")
                continue
            data = r.json()
            status = data.get("status")
            if status == "completed":
                output = data.get("output", [])
                if output and isinstance(output, list):
                    print(f"  ✓ Картинка готова: {output[0]}")
                    return output[0]
                print(f"  ✗ Статус completed, но нет URL. Ответ: {data}")
                return None
            elif status == "failed":
                print(f"  ✗ Генерация провалилась: {data.get('error_message', 'unknown')}")
                return None
            elif status in ("pending", "starting"):
                print(f"  ... статус: {status}, продолжаю ждать")
                continue
            else:
                print(f"  ⚠ Неизвестный статус: {status}")
        except Exception as e:
            print(f"  ⚠ Ошибка при проверке: {e}, продолжаю ждать")
    print(f"  ✗ Превышен таймаут ожидания ({IMAGE_WAIT_TIMEOUT} сек)")
    return None


def download_and_save_image(image_url, guid):
    """Скачивает картинку, сохраняет в IMAGES_DIR/{guid}.jpg. Возвращает локальный путь и публичный URL."""
    print(f"\nСкачиваю картинку: {image_url}")
    os.makedirs(IMAGES_DIR, exist_ok=True)

    # Формируем имя файла по guid
    filename = f"{guid}.jpg"
    local_path = os.path.join(IMAGES_DIR, filename)

    try:
        r = requests.get(image_url, timeout=60, stream=True)
        if r.status_code != 200:
            print(f"  ✗ HTTP {r.status_code} при скачивании")
            return None, None
        with open(local_path, "wb") as f:
            for chunk in r.iter_content(8192):
                f.write(chunk)
        size_kb = os.path.getsize(local_path) // 1024
        public_url = f"{IMAGE_PUBLIC_BASE}/{filename}"
        print(f"  ✓ Сохранена: {local_path} ({size_kb} КБ)")
        print(f"  ✓ Публичный URL: {public_url}")
        return local_path, public_url
    except Exception as e:
        print(f"  ✗ Ошибка скачивания: {e}")
        return None, None


def cleanup_old_images():
    """Удаляет картинки старше IMAGES_RETENTION_DAYS дней."""
    if not os.path.exists(IMAGES_DIR):
        return
    cutoff = time.time() - IMAGES_RETENTION_DAYS * 24 * 3600
    removed = 0
    for filename in os.listdir(IMAGES_DIR):
        filepath = os.path.join(IMAGES_DIR, filename)
        if not os.path.isfile(filepath):
            continue
        if os.path.getmtime(filepath) < cutoff:
            try:
                os.remove(filepath)
                removed += 1
            except Exception as e:
                print(f"  ⚠ Не смог удалить {filepath}: {e}")
    if removed:
        print(f"\nОчистка: удалено {removed} картинок старше {IMAGES_RETENTION_DAYS} дней")

def add_to_feed(item, post_text, image_url=None):
    """Добавляет одну запись в feed.xml. Если есть image_url — встраивает <img> в description."""
    # Загружаем существующие записи
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

    # Формируем тело description: если есть картинка — ставим её первым
    if image_url:
        full_description = f'<img src="{image_url}" />\n\n{post_text}'
    else:
        full_description = post_text

    new_entry = {
        "title": item["title"],
        "guid": item["guid"],
        "pubdate": datetime.now(timezone.utc),
        "fulltext": full_description,
    }

    all_items = [new_entry] + [
        e for e in existing if e["guid"] != new_entry["guid"]
    ]
    all_items.sort(key=lambda x: x["pubdate"], reverse=True)
    all_items = all_items[:MAX_ITEMS_IN_FEED]

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

    # Очищаем старые картинки в начале каждого запуска
    cleanup_old_images()

    pool = load_json(POOL_FILE, {"items": []})
    pool_items = pool.get("items", [])
    if not pool_items:
        print("Пул пустой, нечего публиковать")
        return

    published = load_json(PUBLISHED_FILE, {"guids": []})
    published_guids = set(published.get("guids", []))

    # 1. Куратор выбирает статью
    chosen = curator_pick(pool_items, published_guids, api_key)
    if not chosen:
        print("Куратор не смог выбрать. Завершаю.")
        return

    # 2. Скачиваем полный текст выбранной
    print(f"\nСкачиваю полный текст: {chosen['original_url']}")
    fulltext, _ = get_full_text(chosen["original_url"])
    if not fulltext:
        print("✗ Не удалось скачать текст. Помечу как опубликованную.")
        published_guids.add(chosen["guid"])
        save_json(PUBLISHED_FILE, {"guids": list(published_guids)[-100:]})
        return

    # 3. Рерайтер пишет пост на русском
    rewrite_result = rewrite_article(fulltext, api_key)
    if not rewrite_result:
        print("✗ Рерайт не получился. Не публикую, не помечаю.")
        return
    post_title, post_text = rewrite_result

    # 4. Генерируем картинку (опционально — если упадёт, публикуем без неё)
    image_public_url = None
    image_prompt = generate_image_prompt(post_title, post_text, api_key)
    if image_prompt:
        content_id = request_image_generation(image_prompt, api_key)
        if content_id:
            image_temp_url = wait_for_image(content_id, api_key)
            if image_temp_url:
                _, image_public_url = download_and_save_image(image_temp_url, chosen["guid"])
    if not image_public_url:
        print("\n⚠ Картинку получить не удалось — пост уйдёт без картинки")

    # 5. Добавляем в feed.xml
    chosen_with_ru_title = dict(chosen)
    chosen_with_ru_title["title"] = post_title
    add_to_feed(chosen_with_ru_title, post_text, image_url=image_public_url)

    # 6. Помечаем как опубликованную
    published_guids.add(chosen["guid"])
    save_json(PUBLISHED_FILE, {"guids": list(published_guids)[-100:]})

    print("\n=== Публикатор завершил работу ===")


if __name__ == "__main__":
    main()
