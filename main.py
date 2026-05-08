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

# Куда направлять <link> каждого item — нейтральная ссылка для всех постов
ITEM_LINK_PLACEHOLDER = "https://www.resetera.com/"
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
    text = fetch_with_jina(url)
    if text:
        print(f"  ✓ jina (fallback): {len(text)} знаков")
        return text, []
    print("  ✗ Не удалось извлечь текст")
    return None, []


def cut_paywall_tail(text):
    """
    Обрезает хвост статьи с призывом подписаться/залогиниться.
    Ищем характерные фразы платных порталов и режем всё начиная с первого совпадения.
    """
    paywall_markers = [
        # Subscription tiers (Patreon-подобные)
        r"Subscribe at\s+\w+",
        r"Subscribe to read",
        r"Subscribe to (?:our|the) newsletter",
        # Login walls
        r"Already have an account\?\s*Sign in",
        r"Sign up now",
        r"Sign in to (?:read|continue)",
        r"Log in to (?:read|continue)",
        # Generic paywall phrases
        r"This (?:article|story|content) is for (?:subscribers|members)",
        r"Continue reading\s*(?:with|by)",
        r"Become a (?:member|subscriber)",
        r"Read the (?:full|rest of the) (?:article|story)",
        r"(?:Library|Zeal|Foundation)\s+tier",  # типичные ярусы Patreon
        # Призывы поддержать/донатить (часто идут после статьи)
        r"Support (?:our|us|the site)",
        r"If you enjoyed this",
    ]

    earliest_cut = len(text)
    for pattern in paywall_markers:
        m = re.search(pattern, text, flags=re.IGNORECASE)
        if m and m.start() < earliest_cut:
            earliest_cut = m.start()

    if earliest_cut < len(text):
        cut_text = text[:earliest_cut].strip()
        # Убираем висящие знаки препинания на стыке
        cut_text = re.sub(r"[\s\.,;:\-]+$", "", cut_text).strip()
        if cut_text:
            cut_text += "."
        return cut_text
    return text


def clean_text(text, video_urls=None, max_length=8000):
    """
    Чистим текст:
    1. Обрезаем paywall-хвост
    2. Убираем фразы-обрубки про "watch below"
    3. Убираем хвосты с языковыми ярлыками без видео
    4. Сжимаем пустые строки
    5. Если видео ровно одно — добавляем его в конец
    """
    if video_urls is None:
        video_urls = []

    # 1. Обрезаем paywall ПЕРВЫМ — пока текст ещё в исходном виде
    text = cut_paywall_tail(text)
