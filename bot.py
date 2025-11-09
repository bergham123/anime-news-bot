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
import telegram
from telegram import InputMediaPhoto
from PIL import Image, ImageOps
from io import BytesIO
import requests

# ====================
# CONFIG
# ====================
TZ = ZoneInfo("Africa/Casablanca")

TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

CRUNCHYROLL_RSS_URL = "https://cr-news-api-service.prd.crunchyrollsvc.com/v1/ar-SA/rss"
CHANNEL_ID = "UC1WGYjPeHHc_3nRXqbW3OcQ"
YOUTUBE_RSS_URL = f"https://www.youtube.com/feeds/videos.xml?channel_id={CHANNEL_ID}"
YOUTUBE_SENT_FILE = Path("sent_videos.txt")

DATA_BASE = Path("data")
GLOBAL_INDEX = Path("global_index")
GLOBAL_PAGE_SIZE = 500

LOGO_PATH = "logo.png"
LOGO_MIN_WIDTH_RATIO = 0.10
LOGO_MAX_WIDTH_RATIO = 0.20
LOGO_MARGIN = 10
MAX_IMAGE_WIDTH = 1280
MAX_IMAGE_HEIGHT = 1280
JPEG_QUALITY = 85
HTTP_TIMEOUT = 25

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")


# ==================== UTILS ====================
def now_local():
    return datetime.now(TZ)

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def daily_path(dt: datetime) -> Path:
    y, m, d = dt.year, dt.month, dt.day
    out_dir = DATA_BASE / f"{y}" / f"{m:02d}"
    ensure_dir(out_dir)
    return out_dir / f"{d:02d}-{m:02d}.json"

def load_json_list(path: Path) -> list:
    if not path.exists():
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception as e:
        logging.error(f"Error reading {path}: {e}")
        return []

def save_json_list(path: Path, data: list):
    ensure_dir(path.parent)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logging.error(f"Error writing {path}: {e}")


# ==================== RSS ====================
def extract_full_text(entry):
    try:
        if hasattr(entry, "content") and entry.content and isinstance(entry.content, list):
            raw = entry.content[0].get("value") or ""
            if raw:
                return BeautifulSoup(raw, "html.parser").get_text(separator=" ", strip=True)
    except Exception:
        pass
    raw = getattr(entry, "description", "") or ""
    return BeautifulSoup(raw, "html.parser").get_text(separator=" ", strip=True)

def extract_image(entry):
    if hasattr(entry, "media_thumbnail") and entry.media_thumbnail:
        try:
            return entry.media_thumbnail[0].get("url") or entry.media_thumbnail[0]["url"]
        except Exception:
            pass
    raw = getattr(entry, "description", "") or ""
    soup = BeautifulSoup(raw, "html.parser")
    img = soup.find("img")
    if img and img.has_attr("src"):
        return img["src"]
    return None

def extract_categories(entry):
    cats = []
    if hasattr(entry, "tags"):
        for t in entry.tags:
            term = getattr(t, "term", None)
            if term:
                cats.append(str(term))
    return cats

def build_daily_record(entry):
    return {
        "title": getattr(entry, "title", ""),
        "description_full": extract_full_text(entry),
        "image": extract_image(entry),
        "categories": extract_categories(entry)
    }

def get_entry_identity(entry):
    title = getattr(entry, "title", "") or ""
    image = extract_image(entry)
    return f"{title.strip()}|{(image or '').strip()}"


# ==================== IMAGE ====================
def fetch_image(url):
    try:
        r = requests.get(url, timeout=HTTP_TIMEOUT)
        r.raise_for_status()
        im = Image.open(BytesIO(r.content))
        im = ImageOps.exif_transpose(im)
        return im.convert("RGBA")
    except Exception as e:
        logging.error(f"fetch_image failed: {e}")
        return None

def downscale_to_fit(im):
    w, h = im.size
    scale = min(MAX_IMAGE_WIDTH / w, MAX_IMAGE_HEIGHT / h, 1)
    if scale < 1:
        im = im.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
    return im

def overlay_logo(im):
    if not Path(LOGO_PATH).exists():
        return im
    try:
        logo = Image.open(LOGO_PATH).convert("RGBA")
        pw, ph = im.size
        lw_ratio = LOGO_MIN_WIDTH_RATIO if pw < 600 else LOGO_MAX_WIDTH_RATIO
        lw = int(pw * lw_ratio)
        ratio = lw / logo.width
        lh = int(logo.height * ratio)
        logo = logo.resize((lw, lh), Image.LANCZOS)
        x = pw - lw - LOGO_MARGIN
        y = LOGO_MARGIN
        im.paste(logo, (x, y), logo)
        return im
    except Exception as e:
        logging.error(f"overlay_logo failed: {e}")
        return im

def process_image_with_logo(url):
    base = fetch_image(url)
    if base is None:
        return None
    base = downscale_to_fit(base)
    base = overlay_logo(base)
    out = BytesIO()
    base.convert("RGB").save(out, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    out.seek(0)
    return out


# ==================== STORAGE ====================
def save_full_news_of_today(entries):
    today = now_local()
    path = daily_path(today)
    existing = load_json_list(path)
    existing_fp = {f"{x.get('title')}|{x.get('image')}" for x in existing}

    added = []
    for e in entries:
        fp = get_entry_identity(e)
        if fp in existing_fp:
            continue
        rec = build_daily_record(e)
        existing.append(rec)
        added.append(rec)
        existing_fp.add(fp)

    if added:
        save_json_list(path, existing)
    return added, str(path)


# ==================== GLOBAL INDEX ====================
def convert_full_to_slim(records, source_path=None):
    out = []
    for i, r in enumerate(records):
        path = f"{source_path}#{i}" if source_path else None
        out.append({
            "title": r.get("title"),
            "image": r.get("image"),
            "categories": r.get("categories") or [],
            "path": path
        })
    return out

def gi_append_records(new_records):
    ensure_dir(GLOBAL_INDEX)
    pag_path = GLOBAL_INDEX / "index_1.json"
    items = load_json_list(pag_path)
    items.extend(new_records)
    save_json_list(pag_path, items)


# ==================== TELEGRAM ====================
async def send_crunchyroll_album(bot, added_records):
    if not added_records:
        return
    candidates = added_records[:4]
    media = []
    for rec in candidates:
        img = rec.get("image")
        title = rec.get("title")
        if not img:
            continue
        processed = process_image_with_logo(img)
        if processed:
            media.append(InputMediaPhoto(media=processed, caption=title))
        else:
            media.append(InputMediaPhoto(media=img, caption=title))
    if len(media) >= 2:
        await bot.send_media_group(chat_id=TELEGRAM_CHAT_ID, media=media)
    elif len(media) == 1:
        await bot.send_photo(chat_id=TELEGRAM_CHAT_ID, photo=media[0].media, caption=media[0].caption)
    else:
        text = "📰 أحدث أخبار الأنمي:\n\n" + "\n".join([f"• {r['title']}" for r in candidates])
        await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=text)


async def send_youtube_if_new(bot):
    feed = feedparser.parse(YOUTUBE_RSS_URL)
    if not feed.entries:
        return
    entry = feed.entries[0]
    vid = getattr(entry, "yt_videoid", None)
    title = getattr(entry, "title", "")
    link = getattr(entry, "link", "")
    thumb = None
    if hasattr(entry, "media_thumbnail") and entry.media_thumbnail:
        thumb = entry.media_thumbnail[0].get("url")

    if not YOUTUBE_SENT_FILE.exists():
        YOUTUBE_SENT_FILE.write_text("", encoding="utf-8")
        last = None
    else:
        last = YOUTUBE_SENT_FILE.read_text(encoding="utf-8").splitlines()[0:1]
        last = last[0] if last else None

    if vid == last:
        return

    caption = f"🎥 {title}\n{link}"
    if thumb:
        await bot.send_photo(chat_id=TELEGRAM_CHAT_ID, photo=thumb, caption=caption)
    else:
        await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=caption)

    YOUTUBE_SENT_FILE.write_text(vid + "\n", encoding="utf-8")


# ==================== MAIN ====================
async def run():
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        logging.error("Missing TELEGRAM credentials.")
        return
    bot = telegram.Bot(token=TELEGRAM_TOKEN)

    feed = feedparser.parse(CRUNCHYROLL_RSS_URL)
    if feed.entries:
        added, day_path = save_full_news_of_today(feed.entries)
        await send_crunchyroll_album(bot, added)
        slim = convert_full_to_slim(added, day_path)
        gi_append_records(slim)
    await send_youtube_if_new(bot)


if __name__ == "__main__":
    asyncio.run(run())
