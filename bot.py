# bot.py
import os
import json
import asyncio
import logging
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

import feedparser
from bs4 import BeautifulSoup

# Telegram
import telegram
from telegram import InputMediaPhoto

# Pillow + HTTP
from PIL import Image, ImageOps
from io import BytesIO
import requests

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

# Logo overlay settings
LOGO_PATH = "logo.png"
LOGO_MIN_WIDTH_RATIO = 0.10  # 10% of image width for small images
LOGO_MAX_WIDTH_RATIO = 0.20  # 20% of image width for large images
LOGO_MARGIN = 10             # px margin from top-right

# Image processing limits
MAX_IMAGE_WIDTH  = 1280
MAX_IMAGE_HEIGHT = 1280
JPEG_QUALITY     = 85
HTTP_TIMEOUT     = 25

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
    return out_dir / f"{d:02d}-{m:02d}.json"   # e.g., data/2025/11/09-11.json

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
    Full text without HTML:
    - prefer content:encoded (entry.content[0].value)
    - fallback to description
    """
    try:
        if hasattr(entry, "content") and entry.content and isinstance(entry.content, list):
            raw = entry.content[0].get("value") or ""
            if raw:
                return BeautifulSoup(raw, "html.parser").get_text(separator=" ", strip=True)
    except Exception:
        pass

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
    # 2) from content or description
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
    Daily record (no id/author/published/language/url):
    - title
    - description_full (plain text, full)
    - image
    - categories
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
    """Dedup fingerprint: (title + image)."""
    title = getattr(entry, "title", "") or ""
    image = extract_image(entry)
    return (title.strip(), (image or "").strip())


# ====================
# Image processing (logo + resize)
# ====================
def fetch_image(url: str) -> Image.Image | None:
    try:
        r = requests.get(url, timeout=HTTP_TIMEOUT)
        r.raise_for_status()
        im = Image.open(BytesIO(r.content))
        im = ImageOps.exif_transpose(im)  # fix orientation
        return im.convert("RGBA")
    except Exception as e:
        logging.error(f"fetch_image failed for {url}: {e}")
        return None

def downscale_to_fit(im: Image.Image) -> Image.Image:
    w, h = im.size
    scale = min(
        (MAX_IMAGE_WIDTH / w) if w > 0 else 1,
        (MAX_IMAGE_HEIGHT / h) if h > 0 else 1,
        1
    )
    if scale < 1:
        new_w = max(1, int(w * scale))
        new_h = max(1, int(h * scale))
        im = im.resize((new_w, new_h), Image.LANCZOS)
    return im

def overlay_logo(im: Image.Image) -> Image.Image:
    """Overlay logo top-right with adaptive size."""
    if not Path(LOGO_PATH).exists():
        return im
    try:
        logo = Image.open(LOGO_PATH).convert("RGBA")
    except Exception as e:
        logging.error(f"Failed to open logo: {e}")
        return im

    pw, ph = im.size
    lw_ratio = LOGO_MIN_WIDTH_RATIO if pw < 600 else LOGO_MAX_WIDTH_RATIO
    lw = int(max(1, min(pw - 2 * LOGO_MARGIN, pw * lw_ratio)))
    ratio = lw / logo.width
    lh = int(max(1, logo.height * ratio))
    logo_resized = logo.resize((lw, lh), Image.LANCZOS)

    x = pw - lw - LOGO_MARGIN
    y = LOGO_MARGIN
    im.paste(logo_resized, (x, y), logo_resized)
    return im

def process_image_with_logo(url: str) -> BytesIO | None:
    """
    - download
    - exif transpose
    - smart downscale
    - overlay logo
    - export JPEG
    """
    base = fetch_image(url)
    if base is None:
        return None

    base = downscale_to_fit(base)
    base = overlay_logo(base)

    out = BytesIO()
    base.convert("RGB").save(out, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    out.seek(0)
    return out


# ====================
# Persist Daily (Crunchyroll)
# ====================
def save_full_news_of_today(entries):
    """
    Build today's records (no id/author/published/language/url).
    Dedup by (title + image).
    Return (added_records, path_str).
    """
    today = now_local()
    path = daily_path(today)
    existing = load_json_list(path)

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
# Global Index (no URL)
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


def convert_full_to_slim(records: list, source_path: str = None) -> list:
    """
    تحويل سجلات اليوم إلى سجلات خفيفة:
    - title, image, categories, path
    """
    out = []
    for i, r in enumerate(records):
        path = None
        if source_path:
            path = f"{source_path}#{i}"
        out.append({
            "title": r.get("title"),
            "image": r.get("image"),
            "categories": r.get("categories") or [],
            "path": path
        })
    return out




# ====================
# Telegram Senders
# ====================
async def send_crunchyroll_album(bot: telegram.Bot, added_records: list):
    """
    Send up to 4 new items:
    - >=2 images: media group (album) with logo
    - 1 image: a single photo with logo
    - 0 images: text list of titles
    (no links)
    """
    if not added_records:
        return

    candidates = added_records[:4]

    # prepare media with logo
    media_list = []
    for rec in candidates:
        img_url = rec.get("image")
        title   = rec.get("title") or ""
        if not img_url:
            continue

        processed = process_image_with_logo(img_url)
        if processed:
            media_list.append(InputMediaPhoto(media=processed, caption=title))
        else:
            media_list.append(InputMediaPhoto(media=img_url, caption=title))

        if len(media_list) >= 4:
            break

    if len(media_list) >= 2:
        try:
            await bot.send_media_group(chat_id=TELEGRAM_CHAT_ID, media=media_list)
            return
        except Exception as e:
            logging.error(f"send_media_group failed: {e}")

    if len(media_list) == 1:
        try:
            await bot.send_photo(chat_id=TELEGRAM_CHAT_ID,
                                 photo=media_list[0].media,
                                 caption=media_list[0].caption)
            return
        except Exception as e:
            logging.error(f"send_photo(single) failed: {e}")

    # no images → text only
    lines = [f"• {rec.get('title')}" for rec in candidates]
    text = "📰 أحدث أخبار الأنمي من Crunchyroll\n\n" + "\n".join(lines)
    await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=text)


async def send_youtube_if_new(bot: telegram.Bot):
    """
    Send latest YouTube video if new:
    - no data/ storage
    - prepend id to sent_videos.txt
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

        # send up to 4 new items (with logo)
        await send_crunchyroll_album(bot, added_records)

        # manifests
        today = now_local()
        update_month_manifest(today)
        update_year_manifest(today)

        # global index (no URL)
       slim = convert_full_to_slim(added_records, day_path)
gi_append_records(slim)

    else:
        logging.warning("No entries in Crunchyroll feed.")

    # 2) YouTube (send-only)
    await send_youtube_if_new(bot)

if __name__ == "__main__":
    asyncio.run(run())
