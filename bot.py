# bot.py
import os
import json
import asyncio
import logging
from pathlib import Path
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import feedparser
from bs4 import BeautifulSoup
import telegram
from telegram import InputMediaPhoto

# ====================
# CONFIG
# ====================
TZ = ZoneInfo("Africa/Casablanca")

TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# Sources
CRUNCHYROLL_RSS_URL = "https://cr-news-api-service.prd.crunchyrollsvc.com/v1/ar-SA/rss"

# YouTube
CHANNEL_ID         = "UC1WGYjPeHHc_3nRXqbW3OcQ"
YOUTUBE_RSS_URL    = f"https://www.youtube.com/feeds/videos.xml?channel_id={CHANNEL_ID}"
YOUTUBE_SENT_FILE  = Path("sent_videos.txt")

# Paths
DATA_BASE    = Path("data")            # data/YYYY/MM/DD-MM.json
GLOBAL_INDEX = Path("global_index")    # index_1.json, pagination.json, stats.json

# Global Index settings
GLOBAL_PAGE_SIZE = 500

# Logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")


# ====================
# Utils
# ====================
def now_local() -> datetime:
    return datetime.now(TZ)

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def daily_path(dt: datetime) -> Path:
    y, m, d = dt.year, dt.month, dt.day
    out_dir = DATA_BASE / f"{y}" / f"{m:02d}"
    ensure_dir(out_dir)
    return out_dir / f"{d:02d}-{m:02d}.json"   # example: data/2025/11/09-11.json

def load_json_list(path: Path) -> list:
    if not path.exists():
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception as e:
        logging.error(f"Failed reading {path}: {e}")
        return []

def save_json_list(path: Path, data: list):
    try:
        ensure_dir(path.parent)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logging.error(f"Failed writing {path}: {e}")


# ====================
# RSS extraction helpers
# ====================
def extract_full_text(entry) -> str:
    """
    يرجع النص الكامل للمقال (بدون HTML):
    - يفضّل content:encoded (entry.content[0].value)
    - وإلا يستخدم description
    - ثم ينظّف كل الوسوم
    """
    # 1) content:encoded
    try:
        if hasattr(entry, "content") and entry.content and isinstance(entry.content, list):
            raw = entry.content[0].get("value") or ""
            if raw:
                return BeautifulSoup(raw, "html.parser").get_text(separator=" ", strip=True)
    except Exception:
        pass

    # 2) description
    raw = getattr(entry, "description", "") or ""
    if raw:
        return BeautifulSoup(raw, "html.parser").get_text(separator=" ", strip=True)

    return ""

def extract_image(entry) -> str | None:
    # 1) media:thumbnail
    if hasattr(entry, "media_thumbnail") and entry.media_thumbnail:
        try:
            return entry.media_thumbnail[0].get("url") or entry.media_thumbnail[0]["url"]
        except Exception:
            pass
    # 2) من داخل المحتوى/الوصف
    raw = ""
    try:
        if hasattr(entry, "content") and entry.content and isinstance(entry.content, list):
            raw = entry.content[0].get("value") or ""
    except Exception:
        pass
    if not raw:
        raw = getattr(entry, "description", "") or ""
    if raw:
        soup = BeautifulSoup(raw, "html.parser")
        img = soup.find("img")
        if img and img.has_attr("src"):
            return img["src"]
    return None

def extract_categories(entry) -> list:
    cats = []
    tags = getattr(entry, "tags", None)
    if tags:
        for t in tags:
            term = getattr(t, "term", None)
            if term:
                cats.append(str(term))
    return cats

def build_daily_record(entry) -> dict:
    """
    سجل اليوم كما طلبت:
    - title
    - description_full: النص الكامل بلا HTML (من content:encoded إن وُجد)
    - image
    - categories
    (بدون id/author/published/language/url)
    """
    title = getattr(entry, "title", "") or ""
    description_full = extract_full_text(entry)
    image = extract_image(entry)
    categories = extract_categories(entry)
    return {
        "title": title,
        "description_full": description_full,
        "image": image,
        "categories": categories
    }

def get_entry_identity(entry) -> tuple[str, str | None]:
    """
    بصمة منع التكرار: (title + image)
    لا نخزّنها في الملف، تُستخدم فقط للمقارنة.
    """
    title = getattr(entry, "title", "") or ""
    image = extract_image(entry)
    return (title.strip(), (image or "").strip())


# ====================
# Persist Daily (Crunchyroll)
# ====================
def save_full_news_of_today(entries):
    """
    - يبني سجلات اليوم بالشكل المطلوب (بدون id/author/published/language/url).
    - يمنع التكرار عبر بصمة (title + image) مقارنة بمحتوى اليوم الحالي.
    - يرجع (added_records, path_str).
    """
    today = now_local()
    path = daily_path(today)
    existing = load_json_list(path)

    # بصمات الموجودين: title|image
    def fp_from_item(item: dict) -> str:
        return f"{(item.get('title') or '').strip()}|{(item.get('image') or '').strip()}"

    existing_fp = { fp_from_item(x) for x in existing }

    added = []
    for e in entries:
        title, image = get_entry_identity(e)
        fp = f"{title}|{image}"
        if fp in existing_fp:
            continue
        rec = build_daily_record(e)
        existing.append(rec)
        added.append(rec)
        existing_fp.add(fp)

    if added:
        save_json_list(path, existing)
    return added, str(path)


# ====================
# Manifests (month/year)
# ====================
def update_month_manifest(dt: datetime):
    y, m = dt.year, dt.month
    month_dir = DATA_BASE / f"{y}" / f"{m:02d}"
    ensure_dir(month_dir)
    manifest_path = month_dir / "month_manifest.json"

    days = {}
    for p in sorted(month_dir.glob("*.json")):
        if p.name == "month_manifest.json":
            continue
        day_key = p.stem  # "DD-MM"
        days[day_key.split("-")[0]] = str(p.as_posix())

    manifest = {
        "year": str(y),
        "month": f"{m:02d}",
        "days": dict(sorted(days.items(), key=lambda kv: kv[0], reverse=True))
    }
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

def update_year_manifest(dt: datetime):
    y = dt.year
    year_dir = DATA_BASE / f"{y}"
    ensure_dir(year_dir)
    manifest_path = year_dir / "year_manifest.json"

    months = {}
    for p in sorted(year_dir.glob("[0-1][0-9]")):
        m = p.name
        months[m] = f"{(p / 'month_manifest.json').as_posix()}"

    manifest = {
        "year": str(y),
        "months": dict(sorted(months.items(), key=lambda kv: kv[0], reverse=True))
    }
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)


# ====================
# Global Index (search-friendly, without URL)
#   - Split every 500 items: index_1.json, index_2.json, ...
#   - Each item: title, image, categories (بدون url)
#   - pagination.json: { total_articles, files: [...] }
#   - stats.json: { total_articles, added_today, last_update }
# ====================
def gi_paths():
    ensure_dir(GLOBAL_INDEX)
    pag_path  = GLOBAL_INDEX / "pagination.json"
    stats_path= GLOBAL_INDEX / "stats.json"
    return pag_path, stats_path

def gi_load_pagination():
    pag_path, _ = gi_paths()
    if not pag_path.exists():
        return {"total_articles": 0, "files": []}
    with open(pag_path, "r", encoding="utf-8") as f:
        return json.load(f)

def gi_save_pagination(pag):
    pag_path, _ = gi_paths()
    with open(pag_path, "w", encoding="utf-8") as f:
        json.dump(pag, f, ensure_ascii=False, indent=2)

def gi_save_stats(total_articles: int, added_today: int):
    _, stats_path = gi_paths()
    stats = {
        "total_articles": total_articles,
        "added_today": added_today,
        "last_update": now_local().isoformat()
    }
    with open(stats_path, "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)

def gi_append_records(new_records: list):
    """
    Append slim records to the latest global_index/index_N.json
    Keep only: title, image, categories
    """
    if not new_records:
        return

    pag = gi_load_pagination()

    # create first file if needed
    if not pag["files"]:
        first = GLOBAL_INDEX / "index_1.json"
        save_json_list(first, [])
        pag["files"].append("index_1.json")

    # ensure current exists
    current_file = GLOBAL_INDEX / pag["files"][-1]
    if not current_file.exists():
        save_json_list(current_file, [])
    items = load_json_list(current_file)

    # rotate if reached page size
    if len(items) >= GLOBAL_PAGE_SIZE:
        next_idx = len(pag["files"]) + 1
        current_file = GLOBAL_INDEX / f"index_{next_idx}.json"
        save_json_list(current_file, [])
        pag["files"].append(f"index_{next_idx}.json")
        items = []

    # append & save
    items.extend(new_records)
    save_json_list(current_file, items)

    total = (pag.get("total_articles") or 0) + len(new_records)
    pag["total_articles"] = total
    gi_save_pagination(pag)
    gi_save_stats(total_articles=total, added_today=len(new_records))

def convert_full_to_slim(records: list) -> list:
    """
    تحويل سجلات اليوم (title, description_full, image, categories)
    إلى سجلات خفيفة للبحث:
    - title, image, categories  (بدون url)
    """
    out = []
    for r in records:
        out.append({
            "title": r.get("title"),
            "image": r.get("image"),
            "categories": r.get("categories") or []
        })
    return out


# ====================
# Telegram Senders
# ====================
async def send_crunchyroll_album(bot: telegram.Bot, added_records: list):
    """
    أرسل حتى 4 عناصر جديدة:
    - >=2 صور: ألبوم صور (media group) كل صورة مع عنوانها
    - 1 صورة: صورة واحدة مع العنوان
    - 0 صور: قائمة نصية بالعناوين
    (بدون روابط)
    """
    if not added_records:
        return

    candidates = added_records[:4]

    # جهّز الصور
    photos = []
    for rec in candidates:
        if rec.get("image"):
            photos.append(InputMediaPhoto(media=rec["image"], caption=(rec.get("title") or "")))

    # >= 2 صور → ألبوم
    if len(photos) >= 2:
        try:
            await bot.send_media_group(chat_id=TELEGRAM_CHAT_ID, media=photos)
            return
        except Exception as e:
            logging.error(f"send_media_group failed: {e}")

    # صورة واحدة فقط
    if len(photos) == 1:
        try:
            await bot.send_photo(chat_id=TELEGRAM_CHAT_ID, photo=photos[0].media, caption=photos[0].caption)
            return
        except Exception as e:
            logging.error(f"send_photo failed: {e}")

    # بدون صور → نص
    lines = [f"• {rec.get('title')}" for rec in candidates]
    text = "📰 أحدث أخبار الأنمي من Crunchyroll\n\n" + "\n".join(lines)
    await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=text)


async def send_youtube_if_new(bot: telegram.Bot):
    """
    إرسال أحدث فيديو يوتيوب إن كان جديدًا:
    - لا تخزين داخل data/
    - حفظ ID داخل sent_videos.txt
    """
    feed = feedparser.parse(YOUTUBE_RSS_URL)
    if not feed.entries:
        return

    entry = feed.entries[0]
    vid = getattr(entry, "yt_videoid", None) or getattr(entry, "id", None)
    title = getattr(entry, "title", "")
    url   = getattr(entry, "link", "")
    thumb = None
    if hasattr(entry, "media_thumbnail") and entry.media_thumbnail:
        thumb = entry.media_thumbnail[0].get("url")

    # اقرأ أول سطر من sent_videos.txt
    if not YOUTUBE_SENT_FILE.exists():
        YOUTUBE_SENT_FILE.write_text("", encoding="utf-8")
        last = None
    else:
        with open(YOUTUBE_SENT_FILE, "r", encoding="utf-8") as f:
            last = f.readline().strip() or None

    if last and vid and vid == last:
        return

    caption = f"🎥 {title}\nشاهد على يوتيوب:\n{url}"
    try:
        if thumb:
            await bot.send_photo(chat_id=TELEGRAM_CHAT_ID, photo=thumb, caption=caption)
        else:
            await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=caption)
    except Exception as e:
        logging.error(f"Failed to send YouTube: {e}")
        return

    # prepend id
    try:
        old = ""
        if YOUTUBE_SENT_FILE.exists():
            old = YOUTUBE_SENT_FILE.read_text(encoding="utf-8")
        with open(YOUTUBE_SENT_FILE, "w", encoding="utf-8") as f:
            f.write((vid or "") + "\n")
            if old:
                f.write(old)
    except Exception as e:
        logging.error(f"Failed updating {YOUTUBE_SENT_FILE}: {e}")


# ====================
# Main
# ====================
async def run():
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        logging.error("FATAL: TELEGRAM_TOKEN or TELEGRAM_CHAT_ID not set.")
        return

    bot = telegram.Bot(token=TELEGRAM_TOKEN)

    # 1) Crunchyroll
    news_feed = feedparser.parse(CRUNCHYROLL_RSS_URL)
    if news_feed.entries:
        added_records, day_path = save_full_news_of_today(news_feed.entries)
        logging.info(f"Crun: added {len(added_records)} new record(s) to {day_path}")

        # أرسل حتى 4 عناصر جديدة (عنوان + صورة فقط)
        await send_crunchyroll_album(bot, added_records)

        # تحديث الفهارس
        today = now_local()
        update_month_manifest(today)
        update_year_manifest(today)

        # تحديث global_index (بدون URL)
        slim = convert_full_to_slim(added_records)
        gi_append_records(slim)
    else:
        logging.warning("No entries in Crunchyroll feed.")

    # 2) YouTube (إرسال فقط)
    await send_youtube_if_new(bot)

if __name__ == "__main__":
    asyncio.run(run())
