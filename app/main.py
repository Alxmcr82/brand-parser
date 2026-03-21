import sys
import asyncio
import os
from dotenv import load_dotenv

load_dotenv()
import json
import re
import httpx
from bs4 import BeautifulSoup
from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Optional
import anthropic

app = FastAPI(
    title="Brand Parser API",
    description="Parses brand description and social media links from any website",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
app.mount("/static", StaticFiles(directory=PROJECT_ROOT / "static"), name="static")


@app.get("/", response_class=FileResponse)
async def serve_frontend():
    return FileResponse(PROJECT_ROOT / "frontend.html")


ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
VK_TOKEN = os.environ.get("VK_TOKEN", "")
YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY", "")
MAX_BOT_TOKEN = os.environ.get("MAX_BOT_TOKEN", "")

SOCIAL_PATTERNS = {
    "Instagram":   r"https?://(?:www\.)?instagram\.com/[a-zA-Z0-9_.]+",
    "Facebook":    r"https?://(?:www\.)?facebook\.com/[a-zA-Z0-9_.]+",
    "Twitter / X": r"https?://(?:www\.)?(?:twitter|x)\.com/[a-zA-Z0-9_]+",
    "LinkedIn":    r"https?://(?:www\.)?linkedin\.com/(?:company|in)/[a-zA-Z0-9_-]+",
    "YouTube":     r"https?://(?:www\.)?youtube\.com/(?:@|channel/|user/)[a-zA-Z0-9_-]+",
    "TikTok":      r"https?://(?:www\.)?tiktok\.com/@[a-zA-Z0-9_.]+",
    "Pinterest":   r"https?://(?:www\.)?pinterest\.(?:com|ru)/[a-zA-Z0-9_]+",
    "Telegram":    r"https?://t\.me/[a-zA-Z0-9_]+",
    "VK":          r"https?://(?:www\.)?vk\.(?:com|ru)/[a-zA-Z0-9_]+",
    "Max":         r"https?://(?:www\.)?max\.ru/[a-zA-Z0-9_]+",
    "WhatsApp":    r"https?://wa\.me/[0-9]+",
    "Behance":     r"https?://(?:www\.)?behance\.net/[a-zA-Z0-9_]+",
    "Dribbble":    r"https?://(?:www\.)?dribbble\.com/[a-zA-Z0-9_]+",
    "GitHub":      r"https?://(?:www\.)?github\.com/[a-zA-Z0-9_-]+",
    "Dzen":        r"https?://(?:www\.)?dzen\.ru/(?:t/)?[a-zA-Z0-9_.-]+",
}


class ParseRequest(BaseModel):
    url: str
    use_ai: bool = True


class SocialLink(BaseModel):
    platform: str
    url: str
    followers: Optional[int] = None
    is_bot: Optional[bool] = None


class ParseResponse(BaseModel):
    url: str
    description: Optional[str]
    socials: list[SocialLink]
    method: str  # "ai", "ai+playwright", "regex", "regex+playwright"


# URL paths that are not real social profiles (JS, API, tracking pixels, etc.)
_SOCIAL_BLACKLIST = re.compile(
    r"/js(?:/|$)|/api(?:/|$)|/rtrg|/share|/widget|/oauth|/login|/signup|/legal|/help|/about$|/terms|/policy",
    re.IGNORECASE,
)


def extract_socials_regex(html: str) -> list[SocialLink]:
    found: list[SocialLink] = []
    seen_urls: set[str] = set()
    for platform, pattern in SOCIAL_PATTERNS.items():
        matches = re.findall(pattern, html, re.IGNORECASE)
        for m in matches:
            url = m.rstrip("/")
            if url.lower() not in seen_urls and not _SOCIAL_BLACKLIST.search(url):
                seen_urls.add(url.lower())
                found.append(SocialLink(platform=platform, url=url))
    return found


def extract_description_heuristic(soup: BeautifulSoup) -> Optional[str]:
    meta = soup.find("meta", attrs={"name": "description"})
    if meta and meta.get("content"):
        return meta["content"].strip()

    og = soup.find("meta", property="og:description")
    if og and og.get("content"):
        return og["content"].strip()

    for tag in ["main", "article", "section"]:
        container = soup.find(tag)
        if container:
            for p in container.find_all("p"):
                text = p.get_text(strip=True)
                if len(text) > 60:
                    return text[:500]

    return None


def _parse_subscriber_text(text: str) -> Optional[int]:
    """Parse strings like '1.5K', '2.3M', '12 345', '1 234 567' into int."""
    text = text.strip().replace("\xa0", " ").replace(",", ".")
    m = re.match(r"([\d\s]+)$", text.replace(" ", ""))
    if m:
        return int(m.group(1).replace(" ", ""))
    m = re.match(r"([\d.]+)\s*([KkКк])", text)
    if m:
        return int(float(m.group(1)) * 1_000)
    m = re.match(r"([\d.]+)\s*([MmМм])", text)
    if m:
        return int(float(m.group(1)) * 1_000_000)
    digits = re.sub(r"[^\d]", "", text)
    if digits:
        return int(digits)
    return None


async def fetch_telegram_info(username: str) -> dict:
    """Fetch type (bot/channel/group) and subscriber count from t.me page."""
    result: dict = {"is_bot": None, "followers": None}
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=10.0) as client:
            resp = await client.get(f"https://t.me/{username}")
            resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        # Detect bot by page action button or username ending with "bot"
        action = soup.find("a", class_="tgme_action_button_new")
        action_text = action.get_text(strip=True).lower() if action else ""
        if "send message" in action_text or "отправить сообщение" in action_text or username.lower().endswith("bot"):
            result["is_bot"] = True
        else:
            result["is_bot"] = False
        # Subscribers count (only for channels/groups)
        extra = soup.find("div", class_="tgme_page_extra")
        if extra:
            text = extra.get_text(strip=True)
            m = re.search(r"([\d\s]+)", text)
            if m:
                result["followers"] = int(m.group(1).replace(" ", ""))
    except Exception:
        pass
    return result


async def fetch_vk_followers(group_slug: str) -> Optional[int]:
    """Fetch member count via VK API (needs VK_TOKEN)."""
    if not VK_TOKEN:
        return None
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                "https://api.vk.com/method/groups.getById",
                params={
                    "group_id": group_slug,
                    "fields": "members_count",
                    "access_token": VK_TOKEN,
                    "v": "5.199",
                },
            )
            data = resp.json()
        groups = data.get("response", {}).get("groups", [])
        if groups:
            return groups[0].get("members_count")
    except Exception:
        pass
    return None


async def fetch_youtube_followers(channel_path: str) -> Optional[int]:
    """Fetch subscriber count via YouTube Data API v3 (needs YOUTUBE_API_KEY)."""
    if not YOUTUBE_API_KEY:
        return None
    try:
        # channel_path can be @handle, channel/ID, or user/name
        async with httpx.AsyncClient(timeout=10.0) as client:
            if channel_path.startswith("@"):
                params = {"forHandle": channel_path, "part": "statistics", "key": YOUTUBE_API_KEY}
            elif channel_path.startswith("channel/"):
                params = {"id": channel_path[8:], "part": "statistics", "key": YOUTUBE_API_KEY}
            else:  # user/name
                params = {"forUsername": channel_path.split("/")[-1], "part": "statistics", "key": YOUTUBE_API_KEY}
            resp = await client.get("https://www.googleapis.com/youtube/v3/channels", params=params)
            data = resp.json()
        items = data.get("items", [])
        if items:
            return int(items[0]["statistics"]["subscriberCount"])
    except Exception:
        pass
    return None


async def fetch_max_info(channel_slug: str) -> dict:
    """Fetch type and participants count via Max API (needs MAX_BOT_TOKEN)."""
    result: dict = {"is_bot": None, "followers": None}
    if not MAX_BOT_TOKEN:
        return result
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"https://platform-api.max.ru/chats/{channel_slug}",
                headers={"Authorization": MAX_BOT_TOKEN},
            )
            data = resp.json()
        chat_type = data.get("type", "")
        result["is_bot"] = chat_type == "bot"
        result["followers"] = data.get("participants_count")
    except Exception:
        pass
    return result


async def fetch_dzen_followers(slug: str) -> Optional[int]:
    """Fetch subscriber count from Dzen channel page via Playwright (JS-rendered)."""
    dzen_url = f"https://dzen.ru/{slug}"
    script = f'''
import sys
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(
        headless=True,
        args=["--no-sandbox", "--disable-setuid-sandbox"],
    )
    context = browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
        locale="ru-RU",
    )
    page = context.new_page()
    page.goto("{dzen_url}", wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(3000)
    html = page.content()
    browser.close()
    sys.stdout.buffer.write(html.encode("utf-8"))
'''
    import subprocess as _sp

    def _run():
        result = _sp.run(
            [sys.executable, "-c", script],
            capture_output=True, timeout=45,
        )
        if result.returncode != 0:
            return None
        return result.stdout.decode("utf-8", errors="replace")

    loop = asyncio.get_event_loop()
    html = await loop.run_in_executor(None, _run)
    if not html:
        return None

    soup = BeautifulSoup(html, "html.parser")
    # Dzen shows subscribers as "X подписчиков" or "X подписчик"
    for el in soup.find_all(string=re.compile(r"подписчик", re.IGNORECASE)):
        text = el.strip()
        m = re.search(r"([\d\s.,]+)\s*подписчик", text, re.IGNORECASE)
        if m:
            return _parse_subscriber_text(m.group(1))
    # Fallback: look in nearby elements
    for el in soup.find_all(attrs={"class": re.compile(r"subscriber|follow", re.IGNORECASE)}):
        text = el.get_text(strip=True)
        m = re.search(r"([\d\s.,]+)", text)
        if m:
            return _parse_subscriber_text(m.group(1))
    return None


def _extract_slug(url: str, platform: str) -> str:
    """Extract username/slug from social URL."""
    url = url.rstrip("/")
    if platform == "YouTube":
        # https://youtube.com/@handle or /channel/ID or /user/name
        m = re.search(r"youtube\.com/((?:@|channel/|user/)[a-zA-Z0-9_-]+)", url)
        return m.group(1) if m else ""
    if platform == "Dzen":
        # https://dzen.ru/name or https://dzen.ru/t/name
        m = re.search(r"dzen\.ru/((?:t/)?[a-zA-Z0-9_.-]+)", url)
        return m.group(1) if m else ""
    # For VK, Telegram — last path segment
    return url.split("/")[-1]


async def enrich_with_followers(socials: list[SocialLink]) -> list[SocialLink]:
    """Fetch follower counts and detect bots for supported platforms."""
    tasks = []
    for s in socials:
        slug = _extract_slug(s.url, s.platform)
        if s.platform == "Telegram" and slug:
            tasks.append((s, fetch_telegram_info(slug)))
        elif s.platform == "VK" and slug:
            async def _vk(sl=slug):
                return {"followers": await fetch_vk_followers(sl), "is_bot": None}
            tasks.append((s, _vk()))
        elif s.platform == "YouTube" and slug:
            async def _yt(sl=slug):
                return {"followers": await fetch_youtube_followers(sl), "is_bot": None}
            tasks.append((s, _yt()))
        elif s.platform == "Max" and slug:
            tasks.append((s, fetch_max_info(slug)))
        elif s.platform == "Dzen" and slug:
            async def _dzen(sl=slug):
                return {"followers": await fetch_dzen_followers(sl), "is_bot": None}
            tasks.append((s, _dzen()))
        else:
            async def _noop():
                return {"followers": None, "is_bot": None}
            tasks.append((s, _noop()))

    results = await asyncio.gather(*[t[1] for t in tasks], return_exceptions=True)

    enriched = []
    for i, (social, _) in enumerate(tasks):
        info = results[i] if not isinstance(results[i], (Exception, BaseException)) else {}
        enriched.append(SocialLink(
            platform=social.platform,
            url=social.url,
            followers=info.get("followers"),
            is_bot=info.get("is_bot"),
        ))
    return enriched


async def fetch_with_httpx(url: str) -> tuple[str, BeautifulSoup]:
    """Fast fetch — works for most sites."""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/123.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Cache-Control": "max-age=0",
    }
    async with httpx.AsyncClient(follow_redirects=True, timeout=20.0, http2=True) as client:
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()
    html = resp.text
    soup = BeautifulSoup(html, "html.parser")
    return html, soup


async def fetch_with_playwright(url: str) -> tuple[str, BeautifulSoup]:
    """Full browser fetch — bypasses Cloudflare and JS-rendered pages."""
    script = f'''
import json
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(
        headless=True,
        args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-blink-features=AutomationControlled"],
    )
    context = browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
        locale="ru-RU",
        viewport={{"width": 1280, "height": 800}},
    )
    page = context.new_page()
    page.add_init_script("Object.defineProperty(navigator, \'webdriver\', {{get: () => undefined}})")
    page.goto("{url}", wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(3000)
    html = page.content()
    browser.close()
    import sys
    sys.stdout.buffer.write(html.encode("utf-8"))
'''

    import subprocess as _sp

    def _run():
        result = _sp.run(
            [sys.executable, "-c", script],
            capture_output=True, timeout=60,
        )
        if result.returncode != 0:
            raise RuntimeError(f"Playwright subprocess failed: {result.stderr.decode(errors='replace')}")
        return result.stdout.decode("utf-8", errors="replace")

    loop = asyncio.get_event_loop()
    html = await loop.run_in_executor(None, _run)
    soup = BeautifulSoup(html, "html.parser")
    return html, soup


async def fetch_page(url: str) -> tuple[str, BeautifulSoup, bool]:
    """Try httpx first, fall back to Playwright on 401/403/429.
    Returns (html, soup, used_playwright)."""
    try:
        html, soup = await fetch_with_httpx(url)
        # If page looks like a JS-rendered shell (no title, no meaningful content), try Playwright
        has_title = soup.title and soup.title.string and soup.title.string.strip()
        has_meta = soup.find("meta", attrs={"name": "description"}) is not None
        if not has_title and not has_meta and len(html) < 100_000:
            html, soup = await fetch_with_playwright(url)
            return html, soup, True
        return html, soup, False
    except httpx.HTTPStatusError as e:
        if e.response.status_code in (401, 403, 429):
            html, soup = await fetch_with_playwright(url)
            return html, soup, True
        raise
    except (httpx.RemoteProtocolError, httpx.ConnectError, httpx.ReadTimeout):
        html, soup = await fetch_with_playwright(url)
        return html, soup, True


async def parse_with_ai(url: str, html: str, playwright: bool) -> ParseResponse:
    if not ANTHROPIC_API_KEY:
        raise HTTPException(status_code=500, detail="ANTHROPIC_API_KEY is not set")

    trimmed_html = html[:15000]
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    system = (
        "You are a web content analyst. "
        "Extract brand description and social media links from HTML. "
        "Return ONLY valid JSON, no markdown, no extra text."
    )

    user = f"""Analyze this HTML from {url} and return JSON with:
1. "description": 2-4 sentence brand description (from About section, meta tags, hero text). null if not found.
2. "socials": array of {{"platform": "...", "url": "..."}} for every social media link found.

HTML:
{trimmed_html}

Return only JSON like:
{{"description": "...", "socials": [{{"platform": "Instagram", "url": "https://instagram.com/brand"}}]}}"""

    message = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1000,
        system=system,
        messages=[{"role": "user", "content": user}],
    )

    raw = message.content[0].text.strip()
    raw = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.MULTILINE).strip()

    data = json.loads(raw)
    socials = [SocialLink(**s) for s in data.get("socials", [])]

    socials = await enrich_with_followers(socials)
    method = "ai+playwright" if playwright else "ai"
    return ParseResponse(url=url, description=data.get("description"), socials=socials, method=method)


async def parse_with_regex(url: str, html: str, soup: BeautifulSoup, playwright: bool) -> ParseResponse:
    description = extract_description_heuristic(soup)
    socials = extract_socials_regex(html)
    socials = await enrich_with_followers(socials)
    method = "regex+playwright" if playwright else "regex"
    return ParseResponse(url=url, description=description, socials=socials, method=method)


@app.get("/health")
async def health():
    return {"status": "ok"}


def _is_valid_url(url: str) -> bool:
    """Check that url looks like a real domain (has at least one dot and no spaces)."""
    from urllib.parse import urlparse
    try:
        parsed = urlparse(url)
        host = parsed.hostname or ""
        return "." in host and " " not in host
    except Exception:
        return False


@app.post("/parse", response_model=ParseResponse)
async def parse_brand(req: ParseRequest):
    url = req.url.strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    # Trim to domain root (strip path, query, fragment)
    from urllib.parse import urlparse
    parsed = urlparse(url)
    url = f"{parsed.scheme}://{parsed.netloc}/"
    if not _is_valid_url(url):
        raise HTTPException(status_code=400, detail="Введите корректный URL, например: apple.com")
    req = ParseRequest(url=url, use_ai=req.use_ai)
    try:
        html, soup, used_playwright = await fetch_page(req.url)
    except Exception as e:
        import traceback
        traceback.print_exc()  # выведет полный стек в терминал
        raise HTTPException(status_code=422, detail=f"Could not fetch URL: {type(e).__name__}: {str(e)}")

    if req.use_ai and ANTHROPIC_API_KEY:
        try:
            return await parse_with_ai(req.url, html, used_playwright)
        except HTTPException:
            raise
        except Exception:
            return await parse_with_regex(req.url, html, soup, used_playwright)
    else:
        return await parse_with_regex(req.url, html, soup, used_playwright)


@app.post("/parse/batch", response_model=list[ParseResponse])
async def parse_batch(urls: list[str], use_ai: bool = True):
    if len(urls) > 20:
        raise HTTPException(status_code=400, detail="Max 20 URLs per batch request")

    results = []
    for url in urls:
        try:
            result = await parse_brand(ParseRequest(url=url, use_ai=use_ai))
        except HTTPException:
            result = ParseResponse(url=url, description=None, socials=[], method="error")
        results.append(result)
    return results
