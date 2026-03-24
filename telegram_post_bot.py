import os
import json
import asyncio
import logging
import hashlib
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from io import BytesIO
from urllib.parse import quote
import re

# Telegram
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, 
    CallbackQueryHandler, ConversationHandler, filters, ContextTypes
)

# Pillow + HTTP
from PIL import Image, ImageOps
import requests

# ====================
# CONFIG
# ====================
TZ = ZoneInfo("Africa/Casablanca")

# Bot tokens
ADMIN_BOT_TOKEN = "8431670547:AAEo7_J_YTm5fKgrKN1hDUcCJg9cV3DYsd8"
MAIN_BOT_TOKEN = os.getenv("TELEGRAM_TOKEN")  # Your existing bot token

TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# Admin user IDs (add your Telegram user ID here)
ADMIN_USER_IDS = [5798206513]  # Replace with your actual Telegram user ID

# Same repo (fixed)
GITHUB_REPO_SLUG = "bergham123/anime-news-bot"
GITHUB_REPO_BRANCH = "main"

# Your GitHub Pages site
SITE_BASE_URL = "https://bergham123.github.io/anime-news-bot"
ARTICLE_PAGE = "article.html"

# Paths
DATA_BASE = Path("data")  # data/YYYY/MM/DD-MM.json
GLOBAL_INDEX = Path("global_index")  # index_1.json, index_2.json, pagination.json, stats.json
IMAGES_DIR = Path("images")  # images/YYYY/MM/*.webp

# Global Index settings
GLOBAL_PAGE_SIZE = 500

# Logo overlay settings
LOGO_PATH = "logo.png"
LOGO_MIN_WIDTH_RATIO = 0.10
LOGO_MAX_WIDTH_RATIO = 0.20
LOGO_MARGIN = 10

# Image processing limits
MAX_IMAGE_WIDTH = 1280
MAX_IMAGE_HEIGHT = 1280
JPEG_QUALITY = 85
WEBP_QUALITY = 85
HTTP_TIMEOUT = 25

# Conversation states
(
    WAITING_TITLE,
    WAITING_DESCRIPTION,
    WAITING_FULL_DESCRIPTION,
    WAITING_IMAGE,
    WAITING_CATEGORY,
    WAITING_CONFIRM
) = range(6)

# Categories
CATEGORIES = [
    "آخر أخبار الأنمي",
    "مقالات وتحليلات",
    "مراجعات",
    "فيديو",
    "مقابلات",
    "إعلانات",
    "أخرى"
]

# Logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")


# ====================
# Utility Functions
# ====================
def now_local() -> datetime:
    return datetime.now(TZ)

def iso_now() -> str:
    return now_local().isoformat()

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
        logging.error(f"Failed reading {path}: {e}")
        return []

def save_json_list(path: Path, data: list):
    try:
        ensure_dir(path.parent)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logging.error(f"Failed writing {path}: {e}")

def slugify(text: str, max_len: int = 60) -> str:
    text = (text or "").strip().lower()
    text = re.sub(r"\s+", "-", text)
    text = re.sub(r"[^a-z0-9\u0600-\u06FF\-]+", "", text)
    text = re.sub(r"-{2,}", "-", text).strip("-")
    return text[:max_len] if text else "image"

def stable_article_id(title: str, image_url: str) -> str:
    """Stable ID based on title + image url."""
    key = f"{(title or '').strip()}|{(image_url or '').strip()}"
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]

def stable_image_filename(title: str, image_url: str) -> str:
    """Stable filename based on hash(title + image_url)."""
    base = slugify(title)
    h = stable_article_id(title, image_url)
    return f"{base}-{h}.webp"

def build_raw_github_url(rel_path: str) -> str:
    return f"https://raw.githubusercontent.com/{GITHUB_REPO_SLUG}/{GITHUB_REPO_BRANCH}/{rel_path}"

def build_article_url(day_path: str, idx: int) -> str:
    raw = f"{day_path}#{idx}"
    encoded = quote(raw, safe="")
    return f"{SITE_BASE_URL}/{ARTICLE_PAGE}?path={encoded}"


# ====================
# Image Processing
# ====================
def fetch_image(url: str) -> Image.Image | None:
    try:
        r = requests.get(url, timeout=HTTP_TIMEOUT)
        r.raise_for_status()
        im = Image.open(BytesIO(r.content))
        im = ImageOps.exif_transpose(im)
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
    if not Path(LOGO_PATH).exists():
        return im
    try:
        logo = Image.open(LOGO_PATH).convert("RGBA")
    except Exception as e:
        logging.error(f"Failed to open logo: {e}")
        return im

    pw, _ = im.size
    lw_ratio = LOGO_MIN_WIDTH_RATIO if pw < 600 else LOGO_MAX_WIDTH_RATIO
    lw = int(max(1, min(pw - 2 * LOGO_MARGIN, pw * lw_ratio)))
    ratio = lw / logo.width
    lh = int(max(1, logo.height * ratio))
    logo_resized = logo.resize((lw, lh), Image.LANCZOS)

    x = pw - lw - LOGO_MARGIN
    y = LOGO_MARGIN
    im.paste(logo_resized, (x, y), logo_resized)
    return im

def process_image_with_logo(url: str, out_format: str = "WEBP") -> BytesIO | None:
    base = fetch_image(url)
    if base is None:
        return None

    base = downscale_to_fit(base)
    base = overlay_logo(base)

    out = BytesIO()
    fmt = out_format.upper().strip()

    try:
        if fmt == "WEBP":
            base.convert("RGB").save(out, format="WEBP", quality=WEBP_QUALITY, method=6)
        else:
            base.convert("RGB").save(out, format="JPEG", quality=JPEG_QUALITY, optimize=True)
        out.seek(0)
        return out
    except Exception as e:
        logging.error(f"Failed to save image: {e}")
        return None

def save_webp_into_repo(title: str, original_url: str, webp_bytes: BytesIO, dt: datetime) -> tuple[str, str, bool]:
    y, m = dt.year, dt.month
    out_dir = IMAGES_DIR / f"{y}" / f"{m:02d}"
    ensure_dir(out_dir)

    filename = stable_image_filename(title, original_url)
    file_path = out_dir / filename
    rel_path = file_path.as_posix()
    raw_url = build_raw_github_url(rel_path)

    if file_path.exists():
        return rel_path, raw_url, False

    webp_bytes.seek(0)
    file_path.write_bytes(webp_bytes.read())
    return rel_path, raw_url, True


# ====================
# Article Management
# ====================
def save_article(article_data: dict) -> tuple[str, int]:
    """
    Save article to daily JSON and return (day_path_str, idx)
    """
    today = now_local()
    path = daily_path(today)
    existing = load_json_list(path)
    
    # Add metadata
    now_iso = iso_now()
    article_data["id"] = stable_article_id(article_data["title"], article_data.get("image", ""))
    article_data["created_at"] = now_iso
    article_data["updated_at"] = now_iso
    
    existing.append(article_data)
    save_json_list(path, existing)
    idx = len(existing) - 1
    
    return str(path), idx

def update_manifests(dt: datetime):
    """Update month and year manifests"""
    # Update month manifest
    y, m = dt.year, dt.month
    month_dir = DATA_BASE / f"{y}" / f"{m:02d}"
    ensure_dir(month_dir)
    manifest_path = month_dir / "month_manifest.json"
    
    days = {}
    for p in sorted(month_dir.glob("*.json")):
        if p.name == "month_manifest.json":
            continue
        day_key = p.stem
        days[day_key.split("-")[0]] = str(p.as_posix())
    
    manifest = {
        "year": str(y),
        "month": f"{m:02d}",
        "days": dict(sorted(days.items(), key=lambda kv: kv[0], reverse=True))
    }
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    
    # Update year manifest
    year_dir = DATA_BASE / f"{y}"
    ensure_dir(year_dir)
    manifest_path = year_dir / "year_manifest.json"
    
    months = {}
    for p in sorted(year_dir.glob("[0-1][0-9]")):
        m_name = p.name
        months[m_name] = f"{(p / 'month_manifest.json').as_posix()}"
    
    manifest = {
        "year": str(y),
        "months": dict(sorted(months.items(), key=lambda kv: kv[0], reverse=True))
    }
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

def update_global_index(article: dict, day_path_str: str, idx: int):
    """Add article to global index"""
    # Load pagination
    pag_path = GLOBAL_INDEX / "pagination.json"
    ensure_dir(GLOBAL_INDEX)
    
    if not pag_path.exists():
        pagination = {"total_articles": 0, "files": []}
    else:
        with open(pag_path, "r", encoding="utf-8") as f:
            pagination = json.load(f)
    
    # Get current index file
    if not pagination["files"]:
        first = GLOBAL_INDEX / "index_1.json"
        save_json_list(first, [])
        pagination["files"].append("index_1.json")
    
    current_filename = pagination["files"][-1]
    current_file = GLOBAL_INDEX / current_filename
    items = load_json_list(current_file)
    
    # Create slim record
    slim_record = {
        "id": article.get("id"),
        "title": article.get("title"),
        "image": article.get("image"),
        "categories": article.get("categories", []),
        "created_at": article.get("created_at"),
        "updated_at": article.get("updated_at"),
        "path": f"{day_path_str}#{idx}"
    }
    
    # Rotate if needed
    if len(items) >= GLOBAL_PAGE_SIZE:
        next_idx = len(pagination["files"]) + 1
        current_filename = f"index_{next_idx}.json"
        current_file = GLOBAL_INDEX / current_filename
        save_json_list(current_file, [])
        pagination["files"].append(current_filename)
        items = []
    
    items.append(slim_record)
    save_json_list(current_file, items)
    
    # Update pagination
    pagination["total_articles"] = len(items) + sum(len(load_json_list(GLOBAL_INDEX / f)) for f in pagination["files"][:-1])
    
    with open(pag_path, "w", encoding="utf-8") as f:
        json.dump(pagination, f, ensure_ascii=False, indent=2)
    
    # Update stats
    stats_path = GLOBAL_INDEX / "stats.json"
    stats = {
        "total_articles": pagination["total_articles"],
        "added_today": 1,  # Simplified
        "last_update": iso_now()
    }
    with open(stats_path, "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)


# ====================
# Telegram Bot Handlers
# ====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start command handler"""
    user_id = update.effective_user.id
    
    if user_id not in ADMIN_USER_IDS:
        await update.message.reply_text("⛔ عذراً، هذا البوت مخصص للمشرفين فقط.")
        return
    
    await update.message.reply_text(
        "🎬 *مرحباً بك في بوت إدارة المحتوى*\n\n"
        "يمكنك إضافة مقالات جديدة باستخدام الأزرار أدناه.\n\n"
        "📝 *الأوامر المتاحة:*\n"
        "/add_article - إضافة مقال جديد\n"
        "/cancel - إلغاء العملية الحالية\n"
        "/help - عرض المساعدة",
        parse_mode='Markdown'
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Help command handler"""
    user_id = update.effective_user.id
    
    if user_id not in ADMIN_USER_IDS:
        await update.message.reply_text("⛔ عذراً، هذا البوت مخصص للمشرفين فقط.")
        return
    
    await update.message.reply_text(
        "📖 *طريقة استخدام البوت:*\n\n"
        "1. استخدم /add_article لبدء إضافة مقال جديد\n"
        "2. أدخل عنوان المقال\n"
        "3. أدخل وصف قصير للمقال\n"
        "4. أدخل الوصف الكامل (اختياري)\n"
        "5. أرسل صورة للمقال (اختياري)\n"
        "6. اختر التصنيف المناسب\n"
        "7. قم بتأكيد الإضافة\n\n"
        "يمكنك استخدام /cancel في أي وقت لإلغاء العملية.",
        parse_mode='Markdown'
    )

async def add_article_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start article addition process"""
    user_id = update.effective_user.id
    
    if user_id not in ADMIN_USER_IDS:
        await update.message.reply_text("⛔ عذراً، هذا البوت مخصص للمشرفين فقط.")
        return
    
    context.user_data.clear()
    await update.message.reply_text(
        "📝 *إضافة مقال جديد*\n\n"
        "الرجاء إرسال عنوان المقال:\n"
        "(يمكن أن يكون بالعربية أو الإنجليزية)",
        parse_mode='Markdown'
    )
    return WAITING_TITLE

async def receive_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive article title"""
    title = update.message.text.strip()
    
    if len(title) < 3:
        await update.message.reply_text("❌ العنوان قصير جداً. الرجاء إدخال عنوان أطول (3 أحرف على الأقل).")
        return WAITING_TITLE
    
    context.user_data['title'] = title
    
    await update.message.reply_text(
        f"✅ تم حفظ العنوان: *{title}*\n\n"
        "الآن الرجاء إرسال الوصف القصير للمقال:\n"
        "(سيظهر في المعاينة)",
        parse_mode='Markdown'
    )
    return WAITING_DESCRIPTION

async def receive_description(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive article description"""
    description = update.message.text.strip()
    
    if len(description) < 10:
        await update.message.reply_text("❌ الوصف قصير جداً. الرجاء إدخال وصف أطول (10 أحرف على الأقل).")
        return WAITING_DESCRIPTION
    
    context.user_data['description'] = description
    
    await update.message.reply_text(
        f"✅ تم حفظ الوصف القصير.\n\n"
        "الآن الرجاء إرسال الوصف الكامل للمقال (اختياري):\n"
        "(يمكنك إرسال /skip للتخطي)",
        parse_mode='Markdown'
    )
    return WAITING_FULL_DESCRIPTION

async def receive_full_description(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive full article description"""
    full_description = update.message.text.strip()
    context.user_data['full_description'] = full_description
    
    await update.message.reply_text(
        "✅ تم حفظ الوصف الكامل.\n\n"
        "الآن الرجاء إرسال صورة للمقال (اختياري):\n"
        "(أرسل الصورة أو استخدم /skip للتخطي)",
        parse_mode='Markdown'
    )
    return WAITING_IMAGE

async def skip_full_description(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Skip full description"""
    context.user_data['full_description'] = ""
    
    await update.message.reply_text(
        "⏭️ تم تخطي الوصف الكامل.\n\n"
        "الآن الرجاء إرسال صورة للمقال (اختياري):\n"
        "(أرسل الصورة أو استخدم /skip للتخطي)",
        parse_mode='Markdown'
    )
    return WAITING_IMAGE

async def receive_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive article image"""
    photo = update.message.photo[-1]  # Get highest quality
    file = await photo.get_file()
    
    # Download image
    image_bytes = BytesIO()
    await file.download_to_memory(image_bytes)
    image_bytes.seek(0)
    
    # Save to temporary location or process directly
    context.user_data['image_bytes'] = image_bytes
    context.user_data['image_url'] = None  # Will be processed later
    
    await update.message.reply_text(
        "✅ تم استلام الصورة.\n\n"
        "الآن الرجاء اختيار التصنيف المناسب:",
        reply_markup=get_category_keyboard(),
        parse_mode='Markdown'
    )
    return WAITING_CATEGORY

async def skip_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Skip image upload"""
    context.user_data['image_bytes'] = None
    context.user_data['image_url'] = None
    
    await update.message.reply_text(
        "⏭️ تم تخطي الصورة.\n\n"
        "الآن الرجاء اختيار التصنيف المناسب:",
        reply_markup=get_category_keyboard(),
        parse_mode='Markdown'
    )
    return WAITING_CATEGORY

def get_category_keyboard():
    """Create inline keyboard for categories"""
    keyboard = []
    for i in range(0, len(CATEGORIES), 2):
        row = []
        for cat in CATEGORIES[i:i+2]:
            row.append(InlineKeyboardButton(cat, callback_data=f"cat_{cat}"))
        keyboard.append(row)
    
    return InlineKeyboardMarkup(keyboard)

async def receive_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive article category via callback"""
    query = update.callback_query
    await query.answer()
    
    category = query.data.replace("cat_", "")
    context.user_data['category'] = category
    
    # Show preview and confirmation
    title = context.user_data.get('title', 'N/A')
    description = context.user_data.get('description', 'N/A')
    full_desc = context.user_data.get('full_description', 'غير موجود')
    
    preview_text = (
        f"📝 *معاينة المقال*\n\n"
        f"*العنوان:* {title}\n"
        f"*الوصف القصير:* {description}\n"
        f"*الوصف الكامل:* {full_desc[:100]}...\n"
        f"*التصنيف:* {category}\n"
        f"*الصورة:* {'✅ موجودة' if context.user_data.get('image_bytes') else '❌ غير موجودة'}\n\n"
        f"هل تريد حفظ هذا المقال؟"
    )
    
    keyboard = [
        [
            InlineKeyboardButton("✅ نعم، حفظ", callback_data="confirm_yes"),
            InlineKeyboardButton("❌ لا، إلغاء", callback_data="confirm_no")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        preview_text,
        parse_mode='Markdown',
        reply_markup=reply_markup
    )
    return WAITING_CONFIRM

async def confirm_article(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Confirm and save article"""
    query = update.callback_query
    await query.answer()
    
    if query.data == "confirm_no":
        await query.edit_message_text("❌ تم إلغاء إضافة المقال.")
        context.user_data.clear()
        return ConversationHandler.END
    
    # Process and save article
    try:
        # Prepare article data
        article_data = {
            "title": context.user_data.get('title'),
            "description_full": context.user_data.get('full_description', ''),
            "description_short": context.user_data.get('description'),
            "categories": [context.user_data.get('category')],
            "image": None
        }
        
        # Process image if exists
        image_url = None
        if context.user_data.get('image_bytes'):
            # Save temporary image to process
            temp_image = BytesIO(context.user_data['image_bytes'].getvalue())
            temp_image.seek(0)
            
            # Upload to temporary service or process directly
            # For now, we'll assume we have a direct URL or skip
            # In production, you'd want to upload to a service or use GitHub directly
            
            # Create WebP with logo
            # Since we have the image bytes, we can process it directly
            # This part needs adjustment based on your image hosting solution
            
            # For now, we'll set a placeholder
            image_url = "https://via.placeholder.com/800x400?text=Image+Processing"
        
        article_data["image"] = image_url
        
        # Save to daily JSON
        day_path_str, idx = save_article(article_data)
        
        # Process image if needed (you'll need to implement image upload to GitHub)
        if context.user_data.get('image_bytes'):
            # Here you would upload the image to GitHub via API
            # For now, we'll just log
            logging.info(f"Image would be uploaded for article: {article_data['title']}")
        
        # Update manifests
        update_manifests(now_local())
        
        # Update global index
        update_global_index(article_data, day_path_str, idx)
        
        # Send confirmation
        article_url = build_article_url(day_path_str, idx)
        await query.edit_message_text(
            f"✅ *تم حفظ المقال بنجاح!*\n\n"
            f"📄 *العنوان:* {article_data['title']}\n"
            f"📂 *التصنيف:* {article_data['categories'][0]}\n"
            f"🔗 *رابط المقال:*\n{article_url}\n\n"
            f"سيظهر المقال قريباً في الموقع.",
            parse_mode='Markdown',
            disable_web_page_preview=True
        )
        
        # Optionally send to main channel
        if MAIN_BOT_TOKEN and TELEGRAM_CHAT_ID:
            try:
                main_bot = Application.builder().token(MAIN_BOT_TOKEN).build()
                await main_bot.bot.send_message(
                    chat_id=TELEGRAM_CHAT_ID,
                    text=f"📢 *مقال جديد*\n\n{article_data['title']}\n\n{article_url}",
                    parse_mode='Markdown',
                    disable_web_page_preview=True
                )
            except Exception as e:
                logging.error(f"Failed to send to main channel: {e}")
        
    except Exception as e:
        logging.error(f"Error saving article: {e}")
        await query.edit_message_text(
            f"❌ حدث خطأ أثناء حفظ المقال: {str(e)}\n"
            f"الرجاء المحاولة مرة أخرى لاحقاً."
        )
    
    context.user_data.clear()
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel the conversation"""
    user_id = update.effective_user.id
    
    if user_id not in ADMIN_USER_IDS:
        await update.message.reply_text("⛔ عذراً، هذا البوت مخصص للمشرفين فقط.")
        return
    
    await update.message.reply_text("❌ تم إلغاء العملية.")
    context.user_data.clear()
    return ConversationHandler.END


# ====================
# Main Function
# ====================
def main():
    """Start the bot"""
    # Create application
    application = Application.builder().token(ADMIN_BOT_TOKEN).build()
    
    # Create conversation handler
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('add_article', add_article_start)],
        states={
            WAITING_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_title)],
            WAITING_DESCRIPTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_description)],
            WAITING_FULL_DESCRIPTION: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_full_description),
                CommandHandler('skip', skip_full_description)
            ],
            WAITING_IMAGE: [
                MessageHandler(filters.PHOTO, receive_image),
                CommandHandler('skip', skip_image)
            ],
            WAITING_CATEGORY: [CallbackQueryHandler(receive_category, pattern='^cat_')],
            WAITING_CONFIRM: [CallbackQueryHandler(confirm_article, pattern='^confirm_')],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
    )
    
    # Add handlers
    application.add_handler(CommandHandler('start', start))
    application.add_handler(CommandHandler('help', help_command))
    application.add_handler(conv_handler)
    
    # Start the bot
    print("🤖 Admin bot is running...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == '__main__':
    main()
