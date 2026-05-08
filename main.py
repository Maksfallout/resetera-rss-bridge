"""
RSS-обогатитель для resetera.com
Читает короткий RSS, идёт по ссылкам в оригинальные статьи,
извлекает полный текст и публикует в новый RSS-файл feed.xml.
"""

import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import feedparser
import requests
import trafilatura
from bs4 import BeautifulSoup
from feedgen.feed import FeedGenerator

# ====== НАСТРОЙКИ ======
SOURCE_RSS = "https://www.resetera.com/forums/gaming-headlines.54/index.rss"
OUTPUT_FEED = "feed.xml"
SEEN_FILE = "seen.json"
MAX_ITEMS_IN_FEED = 50      # сколько последних записей держать в выдаваемом RSS
MAX_NEW_PER_RUN = 20        # ограничение на один запуск, чтобы не зависнуть
REQUEST_TIMEOUT = 25        # секунд на одну загрузку статьи
USER_AGENT = "Mozilla/5.0 (compatible; RSSBridge/1.0; +https://github.com)"

# Заголовки нашей выходной ленты
FEED_TITLE = "Gaming Headlines (full text)"
FEED_LINK = "https://www.resetera.com/forums/gaming-headlines.54/"
FEED_DESCRIPTION = "Полные тексты статей по ссылкам из Resetera Gaming Headlines"
# =======================


def load_seen():
    """Загружает множество уже обработанных GUID."""
    if not os.path.exists(SEEN_FILE):
        return set()
    try:
        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return set(data.get("seen", []))
    except Exception:
        return set()


def save_seen(seen_set):
    """Сохраняет множество обработанных GUID, держим только последние 1000."""
    seen_list = list(seen_set)[-1000:]
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump({"seen": seen_list}, f, ensure_ascii=False, indent=2)


def extract_original_url(content_html):
    """
    Достаёт первую внешнюю ссылку из <content:encoded>.
    В RSS resetera первая <a> в первом сообщении — это и есть ссылка на оригинал.
    """
    if not content_html:
        return None
    soup = BeautifulSoup(content_html, "lxml")
    for a in soup.find_all("a", href=True):
        href = a["href"]
        # пропускаем ссылки внутри resetera
        if "resetera.com" in href:
            continue
        if href.startswith("http"):
            return href
    return None

def extract_video_urls(html_content):
    """
    Достаёт ссылки на видео из HTML страницы.
    Ищем YouTube/Vimeo iframe-плееры и youtu.be в обычных ссылках.
    Возвращает список уникальных URL.
    """
    if not html_content:
        return []
    soup = BeautifulSoup(html_content, "lxml")
    video_urls = []
    seen_urls = set()

    # 1. iframe-плееры YouTube/Vimeo
    for iframe in soup.find_all("iframe", src=True):
        src = iframe["src"]
        # Превращаем embed-ссылку в обычную смотрибельную
        # YouTube: https://www.youtube.com/embed/VIDEO_ID -> https://youtu.be/VIDEO_ID
        m = re.search(r"youtube\.com/embed/([a-zA-Z0-9_-]+)", src)
        if m:
            url = f"https://youtu.be/{m.group(1)}"
            if url not in seen_urls:
                video_urls.append(url)
                seen_urls.add(url)
            continue
        # Vimeo: https://player.vimeo.com/video/VIDEO_ID
        m = re.search(r"player\.vimeo\.com/video/(\d+)", src)
        if m:
            url = f"https://vimeo.com/{m.group(1)}"
            if url not in seen_urls:
                video_urls.append(url)
                seen_urls.add(url)
            continue

    # 2. Обычные ссылки на youtu.be и youtube.com/watch
    for a in soup.find_all("a", href=True):
        href = a["href"]
        m = re.search(r"(?:youtu\.be/|youtube\.com/watch\?v=)([a-zA-Z0-9_-]+)", href)
        if m:
            url = f"https://youtu.be/{m.group(1)}"
            if url not in seen_urls:
                video_urls.append(url)
                seen_urls.add(url)

    return video_urls

def fetch_with_trafilatura(url):
    """
    Извлекаем текст и список видео.
    Возвращает кортеж (text, video_urls) или (None, []) при неудаче.
    """
    try:
        downloaded = trafilatura.fetch_url(url)
        if not downloaded:
            return None, []
        text = trafilatura.extract(
            downloaded,
            include_comments=False,
            include_tables=False,
            favor_precision=True,
        )
        videos = extract_video_urls(downloaded)
        if text and len(text) > 200:
            return text, videos
    except Exception as e:
        print(f"  trafilatura упал: {e}")
    return None, []


def fetch_with_jina(url):
    """Запасной вариант — сервис r.jina.ai."""
    try:
        jina_url = f"https://r.jina.ai/{url}"
        r = requests.get(
            jina_url,
            headers={"User-Agent": USER_AGENT, "Accept": "text/plain"},
            timeout=REQUEST_TIMEOUT,
        )
        if r.status_code == 200 and len(r.text) > 200:
            return r.text
    except Exception as e:
        print(f"  jina упал: {e}")
    return None


def get_full_text(url):
    """
    Двухуровневая стратегия: trafilatura → jina.
    Возвращает кортеж (text, video_urls).
    """
    print(f"  Загружаю: {url}")
    text, videos = fetch_with_trafilatura(url)
    if text:
        print(f"  ✓ trafilatura: {len(text)} знаков, видео: {len(videos)}")
        return text, videos
    # fallback на jina — без извлечения видео (там только текст)
    text = fetch_with_jina(url)
    if text:
        print(f"  ✓ jina (fallback): {len(text)} знаков")
        return text, []
    print("  ✗ Не удалось извлечь текст")
    return None, []


def clean_text(text, video_urls=None, max_length=8000):
    """
    Чистим текст:
    1. Убираем фразы-обрубки типа 'Watch the trailers below.' если после них пусто
    2. Убираем хвосты вроде 'Meet the Crew: X English Japanese ...' без содержимого
    3. Сжимаем множественные пустые строки
    4. Если видео ровно одно — добавляем его в конец
    """
    if video_urls is None:
        video_urls = []

    # Убираем повторяющиеся пробелы и пустые строки
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)

    # Подвешенные фразы-обрубки в конце текста
    # (после удаления видео остаются "Watch X below" без X)
    dangling_patterns = [
        r"Watch (?:the |it )?(?:trailers?|videos?|below)[^.]*\.?\s*$",
        r"You can watch[^.]*below\.?\s*$",
        r"Check (?:it |them )?out below\.?\s*$",
        r"See (?:the |it )?below\.?\s*$",
    ]
    for pattern in dangling_patterns:
        text = re.sub(pattern, "", text, flags=re.IGNORECASE | re.MULTILINE)

    # Хвосты с языковыми ярлыками без видео:
    # "Meet the Crew: X English Japanese Meet the Crew: Y English Japanese"
    # Признак: подряд идут короткие фразы с English/Japanese/Trailer/Subtitled и т.п.
    # Срезаем всё начиная с такой последовательности до конца текста.
    text = re.sub(
        r"\n*(?:[A-Z][^\n.]{0,80}\s+(?:English|Japanese|Subtitled|Dubbed|Trailer|Teaser)\b[^\n.]{0,200}){1,}\s*$",
        "",
        text,
        flags=re.MULTILINE,
    )

    text = text.strip()

    # Если видео ровно одно — добавляем его
    if len(video_urls) == 1:
        text = text + f"\n\nВидео: {video_urls[0]}"

    # Обрезаем по длине (мало ли что прислали огромное)
    if len(text) > max_length:
        text = text[:max_length].rsplit(" ", 1)[0] + "…"

    return text


def build_feed(items):
    """Собираем выходной RSS-файл из списка обработанных записей."""
    fg = FeedGenerator()
    fg.title(FEED_TITLE)
    fg.link(href=FEED_LINK, rel="alternate")
    fg.description(FEED_DESCRIPTION)
    fg.language("en")

    # Записи добавляем в обратном порядке — чтобы свежие были первыми в RSS
    for item in items:
        fe = fg.add_entry()
        fe.title(item["title"])
        fe.link(href=item["url"])
        fe.guid(item["guid"], permalink=False)
        fe.pubDate(item["pubdate"])
        fe.description(item["fulltext"])

    fg.rss_file(OUTPUT_FEED, pretty=True)
    print(f"\n✓ Сохранён {OUTPUT_FEED} с {len(items)} записями")


def load_existing_items():
    """Читаем уже опубликованные записи из feed.xml — чтобы не потерять их."""
    if not os.path.exists(OUTPUT_FEED):
        return []
    try:
        parsed = feedparser.parse(OUTPUT_FEED)
        items = []
        for e in parsed.entries:
            items.append({
                "title": e.get("title", ""),
                "url": e.get("link", ""),
                "guid": e.get("id", e.get("link", "")),
                "pubdate": datetime(*e.published_parsed[:6], tzinfo=timezone.utc),
                "fulltext": e.get("summary", ""),
            })
        return items
    except Exception as e:
        print(f"Не удалось прочитать старый feed.xml: {e}")
        return []


def main():
    print(f"=== Запуск {datetime.now().isoformat()} ===")
    seen = load_seen()
    print(f"В seen.json: {len(seen)} записей")

    # 1. Скачиваем исходный RSS
    print(f"Качаю RSS: {SOURCE_RSS}")
    headers = {"User-Agent": USER_AGENT}
    r = requests.get(SOURCE_RSS, headers=headers, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    parsed = feedparser.parse(r.content)
    print(f"Получено записей: {len(parsed.entries)}")

    # 2. Загружаем то, что уже было в нашем feed.xml
    existing_items = load_existing_items()
    existing_guids = {it["guid"] for it in existing_items}

    # 3. Обрабатываем новые
    new_items = []
    for entry in parsed.entries:
        guid = entry.get("id") or entry.get("link")
        if guid in seen:
            continue
        if len(new_items) >= MAX_NEW_PER_RUN:
            break

        title = entry.get("title", "Без заголовка")
        print(f"\n→ Новая запись: {title}")

        # Достаём ссылку на оригинал из content:encoded
        content_html = ""
        if entry.get("content"):
            content_html = entry.content[0].value
        elif entry.get("summary"):
            content_html = entry.summary
        original_url = extract_original_url(content_html)
        if not original_url:
            print("  ✗ Не нашёл ссылку на оригинал, пропускаю")
            seen.add(guid)
            continue
        print(f"  Оригинал: {original_url}")

        # Скачиваем текст
        fulltext, video_urls = get_full_text(original_url)
        if not fulltext:
            seen.add(guid)
            continue
        fulltext = clean_text(fulltext, video_urls)

        # Дата публикации
        pubdate = datetime.now(timezone.utc)
        if entry.get("published_parsed"):
            try:
                pubdate = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
            except Exception:
                pass

        new_items.append({
            "title": title,
            "url": original_url,
            "guid": guid,
            "pubdate": pubdate,
            "fulltext": fulltext,
        })
        seen.add(guid)

        # Небольшая пауза, чтобы не долбить чужие сайты
        time.sleep(2)

    print(f"\nДобавлено новых: {len(new_items)}")

    # 4. Объединяем со старыми, оставляем последние MAX_ITEMS_IN_FEED
    all_items = new_items + [it for it in existing_items if it["guid"] not in {n["guid"] for n in new_items}]
    all_items.sort(key=lambda x: x["pubdate"], reverse=True)
    all_items = all_items[:MAX_ITEMS_IN_FEED]

    # 5. Пишем feed.xml и seen.json
    build_feed(all_items)
    save_seen(seen)
    print("=== Готово ===")


if __name__ == "__main__":
    main()
