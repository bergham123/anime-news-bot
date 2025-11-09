# scrape_article.py
# -*- coding: utf-8 -*-
import os
import re
import json
import argparse
from pathlib import Path
from datetime import datetime
from urllib.parse import urlparse, urljoin

import requests
from bs4 import BeautifulSoup

# --- Optional Playwright (for login / dynamic pages) ---
USE_PLAYWRIGHT_DEFAULT = True
try:
    from playwright.sync_api import sync_playwright
    PLAYWRIGHT_AVAILABLE = True
except Exception:
    PLAYWRIGHT_AVAILABLE = False

# ========= Config =========
TZ = "Africa/Casablanca"
BASE_SAVE = Path("data/scraped")
SAVE_HTML_IN_JSON = False   # اجعلها True لو تريد html الخام
TIMEOUT = 30

# بيئة تسجيل الدخول (اختياري):
CR_EMAIL    = os.getenv("CR_EMAIL")       # بريد كرانشي رول
CR_PASSWORD = os.getenv("CR_PASSWORD")    # كلمة المرور
CR_COUNTRY  = os.getenv("CR_COUNTRY", "ar-SA")  # قد لا تحتاجها

# ========= Helpers =========
def slugify(text: str) -> str:
    text = text.strip()
    text = re.sub(r"\s+", "-", text)
    text = re.sub(r"[^0-9A-Za-z\u0600-\u06FF\-\_]+", "", text)  # عربي + إنجليزي
    return text[:120] or "article"

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def today_paths() -> Path:
    now = datetime.now()
    out_dir = BASE_SAVE / f"{now.year}" / f"{now.month:02d}" / f"{now.day:02d}"
    ensure_dir(out_dir)
    return out_dir

def abs_url(base: str, src: str) -> str:
    if not src:
        return ""
    return urljoin(base, src)

def text_clean(html: str) -> str:
    soup = BeautifulSoup(html or "", "html.parser")
    # إزالة سكريبت/ستايل
    for tag in soup(["script", "style", "noscript"]):
        tag.extract()
    return soup.get_text(separator=" ", strip=True)

def extract_from_article_html(url: str, html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")

    # ---- Title ----
    title_tag = soup.find("h1") or soup.find("title")
    title = (title_tag.get_text(strip=True) if title_tag else "").strip()

    # ---- Categories (بدائية) ----
    categories = []
    # بعض الصفحات تضعها كرابط ضمن رأس الصفحة أو “breadcrumbs”
    for crumb in soup.select('nav a, .breadcrumb a, a[rel="category tag"]'):
        t = crumb.get_text(strip=True)
        if t and len(t) < 60:
            categories.append(t)
    categories = list(dict.fromkeys(categories))  # unique

    # ---- Author / Published (أفضل محاولة) ----
    author = ""
    published = ""
    # محاولات عامة:
    meta_author = soup.find("meta", attrs={"name": "author"}) or soup.find("meta", attrs={"property": "article:author"})
    if meta_author:
        author = meta_author.get("content", "").strip()

    meta_pub = soup.find("meta", attrs={"property": "article:published_time"}) or soup.find("time")
    if meta_pub:
        published = (meta_pub.get("content") or meta_pub.get_text(strip=True) or "").strip()

    # ---- Images ----
    images = []
    for img in soup.find_all("img"):
        src = img.get("src") or img.get("data-src") or ""
        if not src:
            continue
        # تجاهل الأيقونات الصغيرة
        absu = abs_url(url, src)
        if absu and absu not in images:
            images.append(absu)

    # ---- Videos (iframe, video, source) ----
    videos = []
    # YouTube iframes / storyblok / jwplayer إلخ
    for ifr in soup.find_all("iframe"):
        src = ifr.get("src", "")
        if src:
            videos.append(abs_url(url, src))
    for v in soup.find_all("video"):
        src = v.get("src", "")
        if src:
            videos.append(abs_url(url, src))
        for s in v.find_all("source"):
            ssrc = s.get("src", "")
            if ssrc:
                videos.append(abs_url(url, ssrc))
    videos = list(dict.fromkeys(videos))

    # ---- Main text (جلب النص من جسد المقال) ----
    # نحاول إيجاد حاويات شائعة للمقال
    candidates = soup.select("article, .content, .post-content, .c-article, .story, .article-body")
    if not candidates:
        candidates = [soup.body or soup]

    # اجمع فقرات نصية
    paragraphs = []
    for cand in candidates:
        for p in cand.find_all(["p", "h2", "h3", "li"]):
            txt = p.get_text(" ", strip=True)
            if txt and len(txt) > 1:
                paragraphs.append(txt)
        if paragraphs:
            break
    description_text = "\n\n".join(paragraphs) if paragraphs else text_clean(html)

    # ---- Assemble ----
    data = {
        "url": url,
        "title": title,
        "categories": categories,
        "author": author,
        "published": published,
        "description_text": description_text,  # النص الكامل الحقيقي من الصفحة
        "images": images,
        "videos": videos
    }
    if SAVE_HTML_IN_JSON:
        data["html"] = html
    return data

# ========= Fetch modes =========
def fetch_with_requests(url: str, cookies: dict | None = None, headers: dict | None = None) -> str | None:
    h = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36",
        "Accept-Language": "ar-SA,ar;q=0.9,en;q=0.8",
    }
    if headers:
        h.update(headers)
    try:
        r = requests.get(url, headers=h, cookies=cookies, timeout=TIMEOUT)
        r.raise_for_status()
        return r.text
    except Exception as e:
        print(f"[requests] fetch failed: {e}")
        return None

def fetch_with_playwright(url: str, do_login: bool = False) -> str | None:
    if not PLAYWRIGHT_AVAILABLE:
        print("[playwright] not available, falling back to requests.")
        return None

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()

        try:
            if do_login and CR_EMAIL and CR_PASSWORD:
                # محاولة تسجيل الدخول (المسارات تتغير أحياناً—هذه محاولة عامة)
                page.goto("https://www.crunchyroll.com/login", timeout=60000)
                page.wait_for_load_state("domcontentloaded")
                # الحقول الشائعة:
                # قد تحتاج لتعديل السيلكتورز لو تغيرت الصفحة
                page.fill('input[name="email"]', CR_EMAIL)
                page.fill('input[name="password"]', CR_PASSWORD)
                page.click('button[type="submit"]')
                page.wait_for_load_state("networkidle", timeout=60000)

            page.goto(url, timeout=60000)
            page.wait_for_load_state("networkidle", timeout=60000)
            html = page.content()
            return html
        except Exception as e:
            print(f"[playwright] fetch failed: {e}")
            return None
        finally:
            context.close()
            browser.close()

# ========= Save JSON =========
def save_json(data: dict):
    out_dir = today_paths()
    slug = slugify(data.get("title") or urlparse(data.get("url") or "").path.split("/")[-1])
    out_path = out_dir / f"{slug}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"[saved] {out_path.as_posix()}")

# ========= Optional merge into daily file =========
def merge_into_daily_file(scraped: dict):
    """
    (اختياري) دمج المحتوى داخل ملف اليوم data/YYYY/MM/DD-MM.json
    بالمطابقة على العنوان (title).
    افتراض: هناك ملف يومي مسبقًا من البوت الأصلي.
    """
    now = datetime.now()
    daily_file = Path("data") / f"{now.year}" / f"{now.month:02d}" / f"{now.day:02d}-{now.month:02d}.json"
    if not daily_file.exists():
        return
    try:
        with open(daily_file, "r", encoding="utf-8") as f:
            arr = json.load(f)
        changed = False
        for item in arr:
            if (item.get("title") or "").strip() == (scraped.get("title") or "").strip():
                # نضيف حقول موسّعة
                item["description_full"] = scraped.get("description_text") or item.get("description_full")
                item["images_full"] = scraped.get("images") or []
                item["videos"] = scraped.get("videos") or []
                changed = True
                break
        if changed:
            with open(daily_file, "w", encoding="utf-8") as f:
                json.dump(arr, f, ensure_ascii=False, indent=2)
            print(f"[merged] into daily file: {daily_file.as_posix()}")
    except Exception as e:
        print(f"[merge] failed: {e}")

# ========= Main =========
def scrape_one(url: str, force_playwright: bool = False, try_login: bool = False):
    html = None

    # 1) Requests first (سريع) إن لم نفرض Playwright
    if not force_playwright:
        html = fetch_with_requests(url)
        if html and "Please enable JavaScript" not in html and "access denied" not in html.lower():
            print("[mode] requests")
        else:
            html = None

    # 2) Playwright fallback
    if html is None and (USE_PLAYWRIGHT_DEFAULT or force_playwright):
        html = fetch_with_playwright(url, do_login=try_login)
        if html:
            print("[mode] playwright")

    if not html:
        print("[error] unable to fetch page by requests or playwright.")
        return

    data = extract_from_article_html(url, html)
    save_json(data)

    # (اختياري) دمج داخل ملف اليوم:
    # merge_into_daily_file(data)


def main():
    parser = argparse.ArgumentParser(description="Scrape Crunchyroll news article (full text, images, videos) to JSON.")
    parser.add_argument("--url", required=True, help="Article URL")
    parser.add_argument("--force-browser", action="store_true", help="Force using Playwright browser")
    parser.add_argument("--login", action="store_true", help="Try login (requires CR_EMAIL & CR_PASSWORD env)")
    parser.add_argument("--save-html", action="store_true", help="Include raw HTML in JSON")
    args = parser.parse_args()

    global SAVE_HTML_IN_JSON
    if args.save_html:
        SAVE_HTML_IN_JSON = True

    scrape_one(args.url, force_playwright=args.force_browser, try_login=args.login)

if __name__ == "__main__":
    main()
