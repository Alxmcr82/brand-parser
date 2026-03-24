import sys
import asyncio
import os
import json
import re
import time as _time
from collections import defaultdict
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
load_dotenv()

import httpx
from bs4 import BeautifulSoup
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
import anthropic

app = FastAPI(
    title="Brand Parser API",
    description="Parses brand description and social media links from any website",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://parcer.salo.ru", "http://localhost:8000"],
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
app.mount("/static", StaticFiles(directory=PROJECT_ROOT / "static"), name="static")


@app.get("/", response_class=FileResponse)
async def serve_frontend():
    return FileResponse(PROJECT_ROOT / "frontend.html")


@app.get("/how", response_class=FileResponse)
async def serve_how():
    return FileResponse(PROJECT_ROOT / "how.html")


ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
VK_TOKEN = os.environ.get("VK_TOKEN", "")
YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY", "")
MAX_BOT_TOKEN = os.environ.get("MAX_BOT_TOKEN", "")
ACCESS_KEY = os.environ.get("ACCESS_KEY", "")

_P = r"(?:https?:)?//"  # matches https://, http://, and protocol-relative //

SOCIAL_PATTERNS = {
    "Instagram":   _P + r"(?:www\.)?instagram\.com/[a-zA-Z0-9_.]+",
    "Facebook":    _P + r"(?:www\.)?facebook\.com/[a-zA-Z0-9_.]+",
    "Twitter / X": _P + r"(?:www\.)?(?:twitter|x)\.com/[a-zA-Z0-9_]+",
    "LinkedIn":    _P + r"(?:www\.)?linkedin\.com/(?:company|in)/[a-zA-Z0-9_-]+",
    "YouTube":     _P + r"(?:www\.)?youtube\.com/(?:@|channel/|user/)[a-zA-Z0-9_-]+",
    "TikTok":      _P + r"(?:www\.)?tiktok\.com/@[a-zA-Z0-9_.]+",
    "Pinterest":   _P + r"(?:www\.)?pinterest\.(?:com|ru)/[a-zA-Z0-9_]+",
    "Telegram":    _P + r"t\.me/[a-zA-Z0-9_]+",
    "VK Чат":      _P + r"(?:www\.)?vk\.(?:com|ru)/im\?sel=-?\d+",
    "VK":          _P + r"(?:www\.)?vk\.(?:com|ru)/[a-zA-Z0-9_]+",
    "Max":         _P + r"(?:www\.)?max\.ru/(?:u/)?[a-zA-Z0-9_-]+",
    "WhatsApp":    _P + r"wa\.me/[0-9]+",
    "Behance":     _P + r"(?:www\.)?behance\.net/[a-zA-Z0-9_]+",
    "Dribbble":    _P + r"(?:www\.)?dribbble\.com/[a-zA-Z0-9_]+",
    "GitHub":      _P + r"(?:www\.)?github\.com/[a-zA-Z0-9_-]+",
    "Dzen":        _P + r"(?:www\.)?dzen\.ru/(?:t/)?[a-zA-Z0-9_.-]+",
    "Rutube":      _P + r"(?:www\.)?rutube\.ru/(?:channel/|u/)[a-zA-Z0-9_-]+",
    "OK":          _P + r"(?:www\.)?ok\.ru/(?:group/)?[a-zA-Z0-9_.]+",
    "VC":          _P + r"(?:www\.)?vc\.ru/[a-zA-Z0-9_-]+",
    "HeadHunter":  _P + r"(?:www\.)?hh\.ru/employer/[0-9]+",
    "Habr":        _P + r"(?:www\.)?habr\.com/ru/(?:companies|users)/[a-zA-Z0-9_-]+",
}


class ParseRequest(BaseModel):
    url: str
    use_ai: bool = True
    access_key: str = ""


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
    r"/js(?:/|$)|/api(?:/|$)|/rtrg|/share|/widget|/oauth|/login|/signup|/legal|/help|/about$|/terms|/policy|/im$",
    re.IGNORECASE,
)


def extract_socials_regex(html: str) -> list[SocialLink]:
    found: list[SocialLink] = []
    seen_urls: set[str] = set()
    for platform, pattern in SOCIAL_PATTERNS.items():
        matches = re.findall(pattern, html, re.IGNORECASE)
        for m in matches:
            url = m.rstrip("/")
            if url.startswith("//"):
                url = "https:" + url
            elif url.startswith("http://"):
                url = "https://" + url[7:]
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

    if soup.title and soup.title.string and soup.title.string.strip():
        return soup.title.string.strip()

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
        # Detect bot only by username ending with "bot"
        # "Send Message" button appears for both personal accounts and bots
        if username.lower().endswith("bot"):
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
    """Fetch type and participants count by parsing max.ru page."""
    result: dict = {"is_bot": None, "followers": None}
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=10.0) as client:
            resp = await client.get(
                f"https://max.ru/{channel_slug}",
                headers={"User-Agent": "Mozilla/5.0"},
            )
            html = resp.text
        # participantsCount is embedded in JS data
        m = re.search(r'participantsCount["\s]*:\s*(\d+)', html)
        if m:
            result["followers"] = int(m.group(1))
        # Detect bot by slug name or page markers
        if channel_slug.rstrip("/").lower().endswith("bot") or channel_slug.rstrip("/").lower().endswith("_bot"):
            result["is_bot"] = True
        elif re.search(r'type["\s]*:\s*["\']?bot', html):
            result["is_bot"] = True
        else:
            result["is_bot"] = False
    except Exception:
        pass
    return result


async def fetch_dzen_followers(slug: str) -> Optional[int]:
    """Fetch subscriber count via Dzen API."""
    # Strip "t/" prefix if present
    channel_name = slug.removeprefix("t/")
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                "https://dzen.ru/api/v3/launcher/more",
                params={"channel_name": channel_name},
                headers={"User-Agent": "Mozilla/5.0"},
            )
            data = resp.json()
        # Subscriber count is in channel info within the response
        # Search recursively in the JSON for subscribers_count
        return _find_subscribers_in_dzen(data)
    except Exception:
        pass
    return None


def _find_subscribers_in_dzen(obj) -> Optional[int]:
    """Recursively search Dzen API response for subscriber count."""
    if isinstance(obj, dict):
        for key in ("subscribers", "subscribersCount", "subscribers_count"):
            if key in obj and isinstance(obj[key], int):
                return obj[key]
        for v in obj.values():
            result = _find_subscribers_in_dzen(v)
            if result is not None:
                return result
    elif isinstance(obj, list):
        for item in obj:
            result = _find_subscribers_in_dzen(item)
            if result is not None:
                return result
    return None


async def fetch_rutube_followers(url: str) -> Optional[int]:
    """Fetch subscriber count from Rutube channel/user page."""
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=10.0) as client:
            resp = await client.get(
                url,
                headers={"User-Agent": "Mozilla/5.0"},
            )
            html = resp.text
        m = re.search(r'"subscribers_count"\s*:\s*(\d+)', html)
        if m:
            return int(m.group(1))
    except Exception:
        pass
    return None


async def fetch_ok_followers(slug: str) -> Optional[int]:
    """Fetch member count from OK (Odnoklassniki) mobile page."""
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=10.0) as client:
            resp = await client.get(
                f"https://m.ok.ru/{slug}",
                headers={"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X)"},
            )
            html = resp.text
        # Mobile page has: участники&nbsp;<span class="clgry">440\xa0798</span>
        m = re.search(r"участник[а-яё]*[^<]*<[^>]*>([\d\s\xa0]+)<", html, re.IGNORECASE)
        if m:
            return int(m.group(1).replace(" ", "").replace("\xa0", ""))
        # Fallback: any number before участник
        m = re.search(r"([\d\s\xa0]+)\s*(?:участник|подписчик)", html, re.IGNORECASE)
        if m:
            return int(m.group(1).replace(" ", "").replace("\xa0", ""))
    except Exception:
        pass
    return None


async def fetch_vc_followers(slug: str) -> Optional[int]:
    """Fetch subscriber count from vc.ru profile page."""
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=10.0) as client:
            resp = await client.get(
                f"https://vc.ru/{slug}",
                headers={"User-Agent": "Mozilla/5.0"},
            )
            html = resp.text
        # Find the JSON object for this specific profile by matching the slug,
        # then grab its "subscribers" field. The page has many "subscribers"
        # values for different communities — we need the one for our slug.
        # Pattern: "url":"https://vc.ru/slug",...,"subscribers":N
        pattern = rf'"url"\s*:\s*"https?://vc\.ru/{re.escape(slug)}"[^}}]*?"subscribers"\s*:\s*(\d+)'
        m = re.search(pattern, html)
        if m:
            return int(m.group(1))
        # Fallback: visible text "N подписчиков"
        m = re.search(r'(\d[\d\s]*)\s*подписчик', html)
        if m:
            return int(m.group(1).replace(" ", ""))
    except Exception:
        pass
    return None


def _extract_slug(url: str, platform: str) -> str:
    """Extract username/slug from social URL."""
    url = url.rstrip("/")
    if platform == "YouTube":
        # https://youtube.com/@handle or /channel/ID or /user/name
        m = re.search(r"youtube\.com/((?:@|channel/|user/)[a-zA-Z0-9_-]+)", url)
        return m.group(1) if m else ""
    if platform == "Max":
        # https://max.ru/channelname or https://max.ru/u/token
        m = re.search(r"max\.ru/((?:u/)?[a-zA-Z0-9_-]+)", url)
        return m.group(1) if m else ""
    if platform == "OK":
        # https://ok.ru/groupname or https://ok.ru/group/123456
        m = re.search(r"ok\.ru/((?:group/)?[a-zA-Z0-9_.]+)", url)
        return m.group(1) if m else ""
    if platform == "Dzen":
        # https://dzen.ru/name or https://dzen.ru/t/name
        m = re.search(r"dzen\.ru/((?:t/)?[a-zA-Z0-9_.-]+)", url)
        return m.group(1) if m else ""
    if platform == "Rutube":
        # https://rutube.ru/u/name/ or https://rutube.ru/channel/ID/
        return url.split("/")[-1] or url.split("/")[-2]
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
        elif s.platform == "OK" and slug:
            async def _ok(sl=slug):
                return {"followers": await fetch_ok_followers(sl), "is_bot": None}
            tasks.append((s, _ok()))
        elif s.platform == "Rutube":
            async def _rutube(u=s.url):
                return {"followers": await fetch_rutube_followers(u), "is_bot": None}
            tasks.append((s, _rutube()))
        elif s.platform == "VC" and slug:
            async def _vc(sl=slug):
                return {"followers": await fetch_vc_followers(sl), "is_bot": None}
            tasks.append((s, _vc()))
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
    # URL is passed via env var to prevent code injection through f-string
    script = '''
import os, sys
from playwright.sync_api import sync_playwright

target_url = os.environ["PLAYWRIGHT_TARGET_URL"]

with sync_playwright() as p:
    browser = p.chromium.launch(
        headless=True,
        args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-blink-features=AutomationControlled"],
    )
    context = browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
        locale="ru-RU",
        viewport={"width": 1280, "height": 800},
    )
    page = context.new_page()
    page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
    page.goto(target_url, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(3000)
    html = page.content()
    browser.close()
    sys.stdout.buffer.write(html.encode("utf-8"))
'''

    import subprocess as _sp

    def _run():
        env = os.environ.copy()
        env["PLAYWRIGHT_TARGET_URL"] = url
        result = _sp.run(
            [sys.executable, "-c", script],
            capture_output=True, timeout=60, env=env,
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
        # If page looks like a JS-rendered shell, try Playwright
        has_title = soup.title and soup.title.string and soup.title.string.strip()
        has_meta = soup.find("meta", attrs={"name": "description"}) is not None
        has_socials = bool(extract_socials_regex(html))
        has_description = extract_description_heuristic(soup) is not None
        if not has_title and not has_meta and len(html) < 100_000:
            html, soup = await fetch_with_playwright(url)
            return html, soup, True
        # If we got a page but found nothing useful, try Playwright for JS-rendered content
        if not has_socials and not has_description:
            try:
                html2, soup2 = await fetch_with_playwright(url)
                return html2, soup2, True
            except Exception:
                pass  # fallback to original httpx result
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
    api_key = OPENROUTER_API_KEY or ANTHROPIC_API_KEY
    if not api_key:
        raise HTTPException(status_code=500, detail="No AI API key configured")

    trimmed_html = html[:15000]

    system = (
        "Ты — веб-аналитик. "
        "Извлекай описание бренда и ссылки на соцсети из HTML. "
        "Описание бренда ВСЕГДА пиши на русском языке. "
        "Возвращай ТОЛЬКО валидный JSON, без markdown, без лишнего текста."
    )

    user_prompt = f"""Проанализируй HTML с сайта {url} и верни JSON:
1. "description": описание бренда на русском языке, 2-4 предложения (из раздела О компании, мета-тегов, заголовков). null если не найдено.
2. "socials": массив {{"platform": "...", "url": "..."}} для каждой найденной ссылки на соцсеть.

HTML:
{trimmed_html}

Верни только JSON в формате:
{{"description": "Описание на русском...", "socials": [{{"platform": "Instagram", "url": "https://instagram.com/brand"}}]}}"""

    if OPENROUTER_API_KEY:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}"},
                json={
                    "model": "deepseek/deepseek-chat-v3-0324",
                    "max_tokens": 1000,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user_prompt},
                    ],
                },
            )
            resp.raise_for_status()
            raw = resp.json()["choices"][0]["message"]["content"].strip()
    else:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        message = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1000,
            system=system,
            messages=[{"role": "user", "content": user_prompt}],
        )
        raw = message.content[0].text.strip()

    raw = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.MULTILINE).strip()

    data = json.loads(raw)
    ai_socials = [SocialLink(**s) for s in data.get("socials", [])]

    # Merge: regex finds links AI may have missed (e.g. in footer beyond 15k chars)
    regex_socials = extract_socials_regex(html)
    seen_urls = {s.url.lower().rstrip("/") for s in ai_socials}
    for rs in regex_socials:
        if rs.url.lower().rstrip("/") not in seen_urls:
            ai_socials.append(rs)
            seen_urls.add(rs.url.lower().rstrip("/"))

    socials = await enrich_with_followers(ai_socials)
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
    """Check that url looks like a real domain and is not an internal/reserved address."""
    from urllib.parse import urlparse
    import ipaddress
    try:
        parsed = urlparse(url)
        host = parsed.hostname or ""
        if not ("." in host and " " not in host):
            return False
        # Block internal/reserved IPs (SSRF protection)
        try:
            ip = ipaddress.ip_address(host)
            if ip.is_private or ip.is_loopback or ip.is_reserved or ip.is_link_local:
                return False
        except ValueError:
            pass  # Not an IP — it's a domain, that's fine
        # Block common internal hostnames
        blocked = ("localhost", "127.0.0.1", "0.0.0.0", "169.254.169.254", "[::1]")
        if host.lower() in blocked:
            return False
        return True
    except Exception:
        return False


_rate_limit: dict[str, list[float]] = defaultdict(list)
_RATE_LIMIT_MAX = 10  # requests per window
_RATE_LIMIT_WINDOW = 60  # seconds


_rate_limit_cleanup = 0.0


def _check_rate_limit(client_ip: str) -> bool:
    """Return True if request is allowed."""
    global _rate_limit_cleanup
    now = _time.time()
    # Periodically purge stale IPs (every 5 minutes)
    if now - _rate_limit_cleanup > 300:
        stale = [ip for ip, ts in _rate_limit.items() if not ts or now - ts[-1] > _RATE_LIMIT_WINDOW]
        for ip in stale:
            del _rate_limit[ip]
        _rate_limit_cleanup = now
    _rate_limit[client_ip] = [t for t in _rate_limit[client_ip] if now - t < _RATE_LIMIT_WINDOW]
    if len(_rate_limit[client_ip]) >= _RATE_LIMIT_MAX:
        return False
    _rate_limit[client_ip].append(now)
    return True


@app.post("/parse", response_model=ParseResponse)
async def parse_brand(req: ParseRequest, request: Request):
    if ACCESS_KEY and req.access_key != ACCESS_KEY:
        raise HTTPException(status_code=403, detail="Неверный ключ доступа")
    client_ip = request.client.host if request.client else "unknown"
    if not _check_rate_limit(client_ip):
        raise HTTPException(status_code=429, detail="Слишком много запросов. Подождите минуту.")
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
        err = str(e)
        if "ERR_NAME_NOT_RESOLVED" in err or "Name or service not known" in err:
            msg = f"Сайт {req.url} не найден. Проверьте правильность домена."
        elif "Timeout" in err or "timed out" in err.lower():
            msg = f"Сайт {req.url} не отвечает (таймаут). Попробуйте позже."
        elif "ERR_CONNECTION_REFUSED" in err:
            msg = f"Сайт {req.url} отказал в соединении."
        elif "403" in err or "Forbidden" in err:
            msg = "Сайт заблокировал доступ — не удалось извлечь данные."
        else:
            msg = f"Не удалось загрузить {req.url}: {type(e).__name__}"
        raise HTTPException(status_code=422, detail=msg)

    if req.use_ai and (OPENROUTER_API_KEY or ANTHROPIC_API_KEY):
        try:
            result = await parse_with_ai(req.url, html, used_playwright)
        except HTTPException:
            raise
        except Exception:
            result = await parse_with_regex(req.url, html, soup, used_playwright)
    else:
        result = await parse_with_regex(req.url, html, soup, used_playwright)

    if not result.description and not result.socials:
        result.description = "Сайт заблокировал доступ — не удалось извлечь данные."

    return result


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
