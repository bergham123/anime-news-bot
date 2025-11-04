import feedparser
import telegram
import asyncio
import os
import logging
from bs4 import BeautifulSoup
from PIL import Image
from io import BytesIO
import requests

# --- CONFIGURATION ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# Crunchyroll News
CRUNCHYROLL_RSS_URL = "https://cr-news-api-service.prd.crunchyrollsvc.com/v1/ar-SA/rss"
CRUNCHYROLL_SENT_FILE = "sent_posts.txt"

# YouTube
CHANNEL_ID = "UC1WGYjPeHHc_3nRXqbW3OcQ"
YOUTUBE_RSS_URL = f"https://www.youtube.com/feeds/videos.xml?channel_id={CHANNEL_ID}"
YOUTUBE_SENT_FILE = "sent_videos.txt"

# Logo settings
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

def load_first_sent_post(sent_file):
    """Loads only the first post ID from a sent file."""
    logging.info(f"Attempting to load first sent post from '{sent_file}'...")
    
    if not os.path.exists(sent_file):
        logging.warning(f"'{sent_file}' not found. This might be the first run.")
        # Create the file if it doesn't exist
        with open(sent_file, "w", encoding="utf-8") as f:
            pass
        return None
        
    try:
        with open(sent_file, "r", encoding="utf-8") as f:
            first_line = f.readline().strip()
            if first_line:
                logging.info(f"First sent post found: '{first_line}'")
                return first_line
            return None
    except Exception as e:
        logging.error(f"Error reading {sent_file}: {e}")
        return None

def save_sent_post(post_id, sent_file):
    """Prepends a new post ID to the file."""
    logging.info(f"Attempting to save new post ID '{post_id}' to '{sent_file}'...")
    try:
        # Read all existing content
        content = ""
        if os.path.exists(sent_file):
            with open(sent_file, "r", encoding="utf-8") as f:
                content = f.read()
        
        # Write new ID first, then existing content
        with open(sent_file, "w", encoding="utf-8") as f:
            f.write(str(post_id) + "\n")
            if content:
                f.write(content)
        
        logging.info(f"Successfully saved post ID to '{sent_file}'.")
    except Exception as e:
        logging.error(f"Error writing to {sent_file}: {e}")

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

# --- CRUNCHYROLL NEWS LOGIC ---

async def check_and_send_crunchyroll_news(bot):
    """Checks the Crunchyroll RSS feed for the latest entry and sends it to Telegram if new."""
    logging.info("===== Checking Crunchyroll News =====")
    first_sent_post = load_first_sent_post(CRUNCHYROLL_SENT_FILE)

    try:
        news_feed = feedparser.parse(CRUNCHYROLL_RSS_URL)
        
        if not news_feed.entries:
            logging.warning("No entries found in the Crunchyroll RSS feed.")
            return

        # Get only the latest entry
        entry = news_feed.entries[0]
        post_id = entry.id
        title = entry.title
        logging.info(f"Latest Crunchyroll post found: '{title}' with ID: '{post_id}'")

        # Check if this post has already been sent
        if first_sent_post and post_id == first_sent_post:
            logging.info("Latest Crunchyroll post was already sent. Nothing to do.")
            return

        logging.info("This Crunchyroll post is NEW. Preparing to send...")
        
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
        caption = f"📰 *{title}*\n\n{short_desc}"
        
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
                    logging.info(f"Successfully sent Crunchyroll photo with logo to Telegram.")
                else:
                    # If logo addition failed, send original image
                    await bot.send_photo(
                        chat_id=TELEGRAM_CHAT_ID, 
                        photo=image_url, 
                        caption=caption, 
                        parse_mode='Markdown'
                    )
                    logging.info(f"Successfully sent original Crunchyroll photo to Telegram.")
            else:
                await bot.send_message(
                    chat_id=TELEGRAM_CHAT_ID, 
                    text=caption, 
                    parse_mode='Markdown'
                )
                logging.info(f"Successfully sent Crunchyroll text message to Telegram.")
            
            # Save the post ID to sent_posts.txt
            save_sent_post(post_id, CRUNCHYROLL_SENT_FILE)
            
        except Exception as e:
            logging.error(f"Failed to send Crunchyroll message to Telegram: {e}")

    except Exception as e:
        logging.error(f"Error parsing Crunchyroll RSS feed: {e}")

    logging.info("===== Finished checking Crunchyroll News =====")

# --- YOUTUBE VIDEO LOGIC ---

async def check_and_send_youtube_video(bot):
    """Checks the YouTube RSS feed for the latest video and sends it to Telegram if new."""
    logging.info("===== Checking YouTube Videos =====")
    first_sent_video = load_first_sent_post(YOUTUBE_SENT_FILE)

    try:
        video_feed = feedparser.parse(YOUTUBE_RSS_URL)
        
        if not video_feed.entries:
            logging.warning("No entries found in the YouTube RSS feed.")
            return

        # Get only the latest entry
        entry = video_feed.entries[0]
        video_id = entry.yt_videoid
        title = entry.title
        logging.info(f"Latest YouTube video found: '{title}' with ID: '{video_id}'")

        # Check if this video has already been sent
        if first_sent_video and video_id == first_sent_video:
            logging.info("Latest YouTube video was already sent. Nothing to do.")
            return

        logging.info("This YouTube video is NEW. Preparing to send...")
        
        # Extract thumbnail URL
        thumbnail_url = entry.media_thumbnail[0]['url'] if hasattr(entry, 'media_thumbnail') and entry.media_thumbnail else None
        
        # Extract video URL
        video_url = entry.link
        
        # Extract description
        description = clean_description(entry.description)
        short_desc = shorten_text(description, words=25)
        
        # Create caption
        caption = f"🎬 *{title}*\n\n{short_desc}\n\n[Watch on YouTube]({video_url})"
        
        try:
            if thumbnail_url:
                # Try to add logo to thumbnail
                image_with_logo = add_logo_to_image(thumbnail_url)
                if image_with_logo:
                    await bot.send_photo(
                        chat_id=TELEGRAM_CHAT_ID, 
                        photo=image_with_logo, 
                        caption=caption, 
                        parse_mode='Markdown'
                    )
                    logging.info(f"Successfully sent YouTube thumbnail with logo to Telegram.")
                else:
                    # If logo addition failed, send original thumbnail
                    await bot.send_photo(
                        chat_id=TELEGRAM_CHAT_ID, 
                        photo=thumbnail_url, 
                        caption=caption, 
                        parse_mode='Markdown'
                    )
                    logging.info(f"Successfully sent original YouTube thumbnail to Telegram.")
            else:
                await bot.send_message(
                    chat_id=TELEGRAM_CHAT_ID, 
                    text=caption, 
                    parse_mode='Markdown'
                )
                logging.info(f"Successfully sent YouTube text message to Telegram.")
            
            # Save the video ID to sent_videos.txt
            save_sent_post(video_id, YOUTUBE_SENT_FILE)
            
        except Exception as e:
            logging.error(f"Failed to send YouTube message to Telegram: {e}")

    except Exception as e:
        logging.error(f"Error parsing YouTube RSS feed: {e}")

    logging.info("===== Finished checking YouTube Videos =====")

# --- MAIN LOGIC ---

async def check_and_send_content():
    """Checks both RSS feeds for new content and sends them to Telegram if new."""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        logging.error("FATAL: TELEGRAM_TOKEN or TELEGRAM_CHAT_ID not set in environment variables.")
        return

    # Initialize bot inside the function
    bot = telegram.Bot(token=TELEGRAM_TOKEN)
    
    logging.info("===== Starting new bot run =====")
    
    # Check Crunchyroll news
    await check_and_send_crunchyroll_news(bot)
    
    # Check YouTube videos
    await check_and_send_youtube_video(bot)
    
    logging.info("===== Bot run finished =====")


if __name__ == "__main__":
    asyncio.run(check_and_send_content())
