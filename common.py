"""
Общие функции, используемые и сборщиком и публикатором.
"""

import json
import os
import re
import time
from datetime import datetime, timedelta, timezone

import feedparser
import requests
import trafilatura
from bs4 import BeautifulSoup

# ====== ОБЩИЕ НАСТРОЙКИ ======
SOURCE_RSS = "https://www.resetera.com/forums/gaming-headlines.54/index.rss"
POOL_FILE = "pool.json"
SEEN_FILE = "seen.json"
PUBLISHED_FILE = "published.json"
OUTPUT_FEED = "feed.xml"
ITEM_LINK_PLACEHOLDER = "https://t.me/ConsoleWorkShop"
USER_AGENT = "Mozilla/5.0 (compatible; RSSBridge/1.0; +https://github.com)"
REQUEST_TIMEOUT = 25
POOL_RETENTION_HOURS = 24   # сколько часов держать заголовки в пуле
# =============================


def load_json(path, default):
    """Универсальный загрузчик JSON. Возвращает default, если файла нет или ошибка."""
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"Не удалось прочитать {path}: {e}")
        return default


def save_json(path, data):
    """Универсальный сейв JSON в файл."""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def extract_original_url(content_html):
    """Достаёт первую внешнюю ссылку из <content:encoded>."""
    if not content_html:
        return None
    soup = BeautifulSoup(content_html, "lxml")
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "resetera.com" in href:
            continue
        if href.startswith("http"):
            return href
    return None


def fetch_source_rss():
    """Скачивает исходный RSS resetera, возвращает разобранные записи."""
    print(f"Качаю RSS: {SOURCE_RSS}")
    headers = {"User-Agent": USER_AGENT}
    r = requests.get(SOURCE_RSS, headers=headers, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    parsed = feedparser.parse(r.content)
    print(f"Получено записей: {len(parsed.entries)}")
    return parsed.entries


def parse_pubdate(entry):
    """Достаёт дату публикации из RSS-записи. Возвращает datetime в UTC."""
    if entry.get("published_parsed"):
        try:
            return datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
        except Exception:
            pass
    return datetime.now(timezone.utc)


def extract_video_urls(html_content):
    """Достаёт ссылки на видео из HTML страницы."""
    if not html_content:
        return []
    soup = BeautifulSoup(html_content, "lxml")
    video_urls = []
    seen_urls = set()
    for iframe in soup.find_all("iframe", src=True):
        src = iframe["src"]
        m = re.search(r"youtube\.com/embed/([a-zA-Z0-9_-]+)", src)
        if m:
            url = f"https://youtu.be/{m.group(1)}"
            if url not in seen_urls:
                video_urls.append(url)
                seen_urls.add(url)
            continue
        m = re.search(r"player\.vimeo\.com/video/(\d+)", src)
        if m:
            url = f"https://vimeo.com/{m.group(1)}"
            if url not in seen_urls:
                video_urls.append(url)
                seen_urls.add(url)
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
    """Извлекаем текст и список видео через trafilatura."""
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
            # Минимальная чистка — убираем шапку метаданных
            text = r.text
            marker = "Markdown Content:"
            if marker in text:
                text = text.split(marker, 1)[1]
            return text.strip()
    except Exception as e:
        print(f"  jina упал: {e}")
    return None


def get_full_text(url):
    """Двухуровневая стратегия: trafilatura → jina. Возвращает (text, video_urls)."""
    print(f"  Загружаю: {url}")
    text, videos = fetch_with_trafilatura(url)
    if text:
        print(f"  ✓ trafilatura: {len(text)} знаков")
        return text, videos
    text = fetch_with_jina(url)
    if text:
        print(f"  ✓ jina (fallback): {len(text)} знаков")
        return text, []
    print("  ✗ Не удалось извлечь текст")
    return None, []
