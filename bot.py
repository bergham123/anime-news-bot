import feedparser
import telegram
import asyncio
import os
import logging
from bs4 import BeautifulSoup

# --- CONFIGURATION ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
RSS_FEED_URL = "https://cr-news-api-service.prd.crunchyrollsvc.com/v1/ar-SA/rss"
SENT_FILE = "sent_posts.txt"

# --- LOGGING ---
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# --- HELPER FUNCTIONS ---

def load_sent_posts():
    """Loads the set of already sent post IDs from a file."""
    logging.info(f"Current working directory: {os.getcwd()}")
    logging.info(f"Attempting to load sent posts from '{SENT_FILE}'...")
    
    if not os.path.exists(SENT_FILE):
        logging.warning(f"'{SENT_FILE}' not found. This might be the first run.")
        return set()
        
    try:
        with open(SENT_FILE, "r", encoding="utf-8") as f:
            posts = set(line.strip() for line in f if line.strip())
            logging.info(f"Successfully loaded {len(posts)} sent post IDs.")
            logging.info(f"Sent posts are: {posts}")
            return posts
    except Exception as e:
        logging.error(f"Error reading {SENT_FILE}: {e}")
        return set()

def save_sent_post(post_id):
    """Appends a new post ID to the file."""
    logging.info(f"Attempting to save new post ID '{post_id}' to '{SENT_FILE}'...")
    try:
        with open(SENT_FILE, "a", encoding="utf-8") as f:
            f.write(str(post_id) + "\n")
        logging.info(f"Successfully saved post ID to '{SENT_FILE}'.")
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

# --- MAIN LOGIC ---

async def check_and_send_news():
    """Checks the RSS feed for the latest entry and sends it to Telegram."""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        logging.error("FATAL: TELEGRAM_TOKEN or TELEGRAM_CHAT_ID not set in environment variables.")
        return

    logging.info("===== Starting new bot run =====")
    sent_posts = load_sent_posts()

    try:
        news_feed = feedparser.parse(RSS_FEED_URL)
        
        if not news_feed.entries:
            logging.warning("No entries found in the RSS feed.")
            return

        entry = news_feed.entries[0]
        post_id = entry.id
        logging.info(f"Latest post found: '{entry.title}' with ID: '{post_id}'")

        if post_id not in sent_posts:
            logging.info("This post is NEW. Preparing to send...")
            
            image_url = None
            if hasattr(entry, 'media_thumbnail') and entry.media_thumbnail:
                image_url = entry.media_thumbnail[0]['url']
                logging.info(f"Found image URL: {image_url}")

            clean_desc = clean_description(entry.description)
            short_desc = shorten_text(clean_desc, words=25)
            caption = f"*{entry.title}*\n\n{short_desc}"
            
            try:
                if image_url:
                    await bot.send_photo(chat_id=TELEGRAM_CHAT_ID, photo=image_url, caption=caption, parse_mode='Markdown')
                    logging.info(f"Successfully sent photo to Telegram.")
                else:
                    await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=caption, parse_mode='Markdown')
                    logging.info(f"Successfully sent text message to Telegram.")
                
                save_sent_post(post_id)
                
            except Exception as e:
                logging.error(f"Failed to send message to Telegram: {e}")
        else:
            logging.info(f"Latest post was already sent. Nothing to do.")

    except Exception as e:
        logging.error(f"Error parsing RSS feed: {e}")

    logging.info("===== Bot run finished =====")


if __name__ == "__main__":
    bot = telegram.Bot(token=os.getenv("TELEGRAM_TOKEN"))
    asyncio.run(check_and_send_news())
