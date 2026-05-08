"""
Сборщик заголовков из RSS resetera в pool.json.
Работает часто (раз в час), не вызывает ИИ, не качает оригиналы.
Просто собирает: title, original_url, guid, pubdate.
"""

from datetime import datetime, timedelta, timezone

from common import (
    SEEN_FILE, POOL_FILE, POOL_RETENTION_HOURS,
    fetch_source_rss, extract_original_url, parse_pubdate,
    load_json, save_json,
)


def main():
    print(f"=== Сборщик: {datetime.now(timezone.utc).isoformat()} ===")

    seen_data = load_json(SEEN_FILE, {"seen": []})
    seen = set(seen_data.get("seen", []))
    print(f"В seen.json: {len(seen)} GUID")

    pool = load_json(POOL_FILE, {"items": []})
    pool_items = pool.get("items", [])
    pool_guids = {it["guid"] for it in pool_items}
    print(f"В pool.json: {len(pool_items)} записей")

    # 1. Скачиваем RSS, добавляем новые в пул
    entries = fetch_source_rss()
    added = 0
    for entry in entries:
        guid = entry.get("id") or entry.get("link")
        if guid in seen or guid in pool_guids:
            continue

        content_html = ""
        if entry.get("content"):
            content_html = entry.content[0].value
        elif entry.get("summary"):
            content_html = entry.summary
        original_url = extract_original_url(content_html)
        if not original_url:
            seen.add(guid)
            continue

        pubdate = parse_pubdate(entry)
        pool_items.append({
            "guid": guid,
            "title": entry.get("title", "Без заголовка"),
            "original_url": original_url,
            "pubdate": pubdate.isoformat(),
        })
        seen.add(guid)
        added += 1
        print(f"  + {entry.get('title', '')[:80]}")

    print(f"\nДобавлено в пул: {added}")

    # 2. Чистим старые записи из пула (старше POOL_RETENTION_HOURS)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=POOL_RETENTION_HOURS)
    fresh_items = []
    expired = 0
    for it in pool_items:
        try:
            item_date = datetime.fromisoformat(it["pubdate"])
            if item_date >= cutoff:
                fresh_items.append(it)
            else:
                expired += 1
        except Exception:
            fresh_items.append(it)
    if expired:
        print(f"Удалено устаревших: {expired}")

    # 3. Сохраняем
    save_json(POOL_FILE, {"items": fresh_items})
    # seen храним только последние 1000
    seen_list = list(seen)[-1000:]
    save_json(SEEN_FILE, {"seen": seen_list})

    print(f"Итог: {len(fresh_items)} в пуле, {len(seen_list)} в seen")
    print("=== Готово ===")


if __name__ == "__main__":
    main()
