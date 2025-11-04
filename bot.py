import feedparser
import telegram
import asyncio
import os
import logging
import requests
from bs4 import BeautifulSoup
from PIL import Image
from io import BytesIO

# --- CONFIGURATION ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
RSS_FEED_URL = "https://cr-news-api-service.prd.crunchyrollsvc.com/v1/ar-SA/rss"
SENT_FILE = "sent_posts.txt"
LOGO_PATH = "logo.png"  # Path to your logo file
LOGO_MIN_WIDTH_RATIO = 0.10  # 10% of image width
LOGO_MAX_WIDTH_RATIO = 0.20  # 20% of image width
LOGO_MARGIN = 10  # px margin from top-right

# --- Headers to bypass 403 ---
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/140.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ar-SA,ar;q=0.9,en;q=0.8",
    "Accept": "application/xml,application/xhtml+xml,text/html;q=0.9,image/webp,*/*;q=0.8",
    "Connection": "keep-alive"
}

# --- LOGGING ---
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# --- HELPER FUNCTIONS ---

def load_first_sent_post():
    """Loads only the first post title from sent_posts.txt."""
    logging.info(f"Attempting to load first sent post from '{SENT_FILE}'...")
    
    if not os.path.exists(SENT_FILE):
        logging.warning(f"'{SENT_FILE}' not found. This might be the first run.")
        # Create the file if it doesn't exist
        with open(SENT_FILE, "w", encoding="utf-8") as f:
            pass
        return None
        
    try:
        with open(SENT_FILE, "r", encoding="utf-8") as f:
            first_line = f.readline().strip()
            if first_line:
                logging.info(f"First sent post found: '{first_line}'")
                return first_line
            return None
    except Exception as e:
        logging.error(f"Error reading {SENT_FILE}: {e}")
        return None

def save_sent_post(title):
    """Prepends a new post title to the file."""
    logging.info(f"Attempting to save new post title '{title}' to '{SENT_FILE}'...")
    try:
        # Read all existing content
        content = ""
        if os.path.exists(SENT_FILE):
            with open(SENT_FILE, "r", encoding="utf-8") as f:
                content = f.read()
        
        # Write new title first, then existing content
        with open(SENT_FILE, "w", encoding="utf-8") as f:
            f.write(title + "\n")
            if content:
                f.write(content)
        
        logging.info(f"Successfully saved post title to '{SENT_FILE}'.")
    except Exception as e:
        logging.error(f"Error writing to {SENT_FILE}: {e}")

def shorten_text(text, words=25):
    """Shortens text to a specific number of words."""
    if not text:
        return ""
    w = text.split()
    short = ' '.join(w[:words])
    return short + "..." if len(w) > words else short

def clean_description(description_html):
    """Cleans HTML from description and returns plain text."""
    if not description_html:
        return ""
    soup = BeautifulSoup(description_html, "html.parser")
    return soup.get_text().strip()

def add_logo_to_image(image_url):
    """Adds a logo to the image and returns the modified image."""
    try:
        # Check if logo file exists
        if not os.path.exists(LOGO_PATH):
            logging.warning(f"Logo file '{LOGO_PATH}' not found. Skipping logo addition.")
            return None
            
        response = requests.get(image_url, headers=HEADERS)
        response.raise_for_status()
        post_image = Image.open(BytesIO(response.content)).convert("RGBA")
        logo = Image.open(LOGO_PATH).convert("RGBA")

        pw, ph = post_image.size

        # Determine logo width based on image size
        lw = int(pw * LOGO_MIN_WIDTH_RATIO) if pw < 600 else int(pw * LOGO_MAX_WIDTH_RATIO)
        logo_ratio = lw / logo.width
        lh = int(logo.height * logo_ratio)
        logo = logo.resize((lw, lh), Image.LANCZOS)

        # Paste logo top-right with margin
        position = (pw - lw - LOGO_MARGIN, LOGO_MARGIN)
        post_image.paste(logo, position, logo)

        output = BytesIO()
        post_image.save(output, format="PNG")
        output.seek(0)
        return output
    except Exception as e:
        logging.error(f"Error adding logo: {e}")
        return None

# --- MAIN LOGIC ---

async def check_and_send_news():
    """Checks the RSS feed for the latest entry and sends it to Telegram if new."""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        logging.error("FATAL: TELEGRAM_TOKEN or TELEGRAM_CHAT_ID not set in environment variables.")
        return

    # Initialize bot inside the function
    bot = telegram.Bot(token=TELEGRAM_TOKEN)
    
    logging.info("===== Starting new bot run =====")
    first_sent_post = load_first_sent_post()

    try:
        news_feed = feedparser.parse(RSS_FEED_URL)
        
        if not news_feed.entries:
            logging.warning("No entries found in the RSS feed.")
            return

        # Get only the latest entry
        entry = news_feed.entries[0]
        title = entry.title
        logging.info(f"Latest post found: '{title}'")

        # Check if this post has already been sent
        if first_sent_post and title == first_sent_post:
            logging.info("Latest post was already sent. Nothing to do.")
            return

        logging.info("This post is NEW. Preparing to send...")
        
        # Extract image URL
        image_url = None
        if hasattr(entry, 'media_thumbnail') and entry.media_thumbnail:
            image_url = entry.media_thumbnail[0]['url']
            logging.info(f"Found image URL: {image_url}")
        
        # If no image in media_thumbnail, try to extract from description
        if not image_url and hasattr(entry, 'description'):
            desc_soup = BeautifulSoup(entry.description, "html.parser")
            img_tag = desc_soup.find("img")
            if img_tag and img_tag.has_attr("src"):
                image_url = img_tag["src"]
                logging.info(f"Found image URL in description: {image_url}")

        clean_desc = clean_description(entry.description)
        short_desc = shorten_text(clean_desc, words=25)
        caption = f"*{title}*\n\n{short_desc}"
        
        try:
            if image_url:
                # Try to add logo to image
                image_with_logo = add_logo_to_image(image_url)
                if image_with_logo:
                    await bot.send_photo(
                        chat_id=TELEGRAM_CHAT_ID, 
                        photo=image_with_logo, 
                        caption=caption, 
                        parse_mode='Markdown'
                    )
                    logging.info(f"Successfully sent photo with logo to Telegram.")
                else:
                    # If logo addition failed, send original image
                    await bot.send_photo(
                        chat_id=TELEGRAM_CHAT_ID, 
                        photo=image_url, 
                        caption=caption, 
                        parse_mode='Markdown'
                    )
                    logging.info(f"Successfully sent original photo to Telegram.")
            else:
                await bot.send_message(
                    chat_id=TELEGRAM_CHAT_ID, 
                    text=caption, 
                    parse_mode='Markdown'
                )
                logging.info(f"Successfully sent text message to Telegram.")
            
            # Save the post title to sent_posts.txt
            save_sent_post(title)
            
        except Exception as e:
            logging.error(f"Failed to send message to Telegram: {e}")

    except Exception as e:
        logging.error(f"Error parsing RSS feed: {e}")

    logging.info("===== Bot run finished =====")


if __name__ == "__main__":
    asyncio.run(check_and_send_news())
