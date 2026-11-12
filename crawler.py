"""Syosetu (Ncode) crawler.

Search:
  - Narou public API (https://api.syosetu.com/novelapi/api/).
  - Multi-keyword: mode "all" (AND, one query) or "any" (OR, one query per keyword, merged).
  - Exclude keywords, genre include/exclude, boolean flags, type, order, length, paging.
Download:
  - TOC scraping with pagination (?p=N, ~100 episodes per page), same selectors as
    the Tampermonkey downloader (.p-eplist / .index_box, .p-novel__body / #novel_honbun).
  - Per-chapter cache in cache/<ncode>/NNNNN.json so interrupted runs resume.
  - Delay + jitter, retry with cooldown on 403/429/5xx, cancel/pause via CrawlControl.
  - Output EPUB3 (no external deps) and/or TXT into downloads/.

Public helpers kept for older callers:
  - search_novels(keyword, exclude_genres=None, limit=20)
  - download_novel(ncode, out_dir) -> path to .txt
"""
from __future__ import annotations

import html
import json
import pathlib
import random
import re
import threading
import time
import uuid
import zipfile
from datetime import datetime, timezone
from typing import Callable, Dict, Iterable, List, Optional

import requests
from bs4 import BeautifulSoup

from . import config

NCODE_RE = re.compile(r"\bn\d{4}[a-z]{1,3}\b", re.IGNORECASE)
ProgressFn = Callable[[Dict], None]


# ---------------------------------------------------------------------------
# Errors / control
# ---------------------------------------------------------------------------
class CrawlCancelled(Exception):
    """Raised when the user cancels a running crawl."""


class CrawlControl:
    """Cooperative cancel/pause flags shared between the worker and the UI."""

    def __init__(self) -> None:
        self._cancel = threading.Event()
        self._running = threading.Event()
        self._running.set()  # set = not paused

    def cancel(self) -> None:
        self._cancel.set()
        self._running.set()  # wake a paused worker so it can exit

    def pause(self) -> None:
        self._running.clear()

    def resume(self) -> None:
        self._running.set()

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    @property
    def paused(self) -> bool:
        return not self._running.is_set()

    def checkpoint(self) -> None:
        """Block while paused; raise if cancelled."""
        while not self._running.wait(timeout=0.5):
            pass
        if self._cancel.is_set():
            raise CrawlCancelled("Cancelled by user")

    def sleep(self, seconds: float) -> None:
        """Interruptible sleep."""
        end = time.monotonic() + max(0.0, seconds)
        while True:
            self.checkpoint()
            left = end - time.monotonic()
            if left <= 0:
                return
            time.sleep(min(0.25, left))


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------
def load_settings() -> Dict:
    """Defaults merged with settings.json (unknown keys ignored)."""
    data = dict(config.DEFAULT_SETTINGS)
    try:
        saved = json.loads(config.SETTINGS_PATH.read_text(encoding="utf-8"))
        data.update({k: v for k, v in saved.items() if k in config.DEFAULT_SETTINGS})
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return data


def save_settings(values: Dict) -> Dict:
    """Validate, persist and return the merged settings."""
    current = load_settings()
    ints = {"delay_ms": 300, "jitter_ms": 0, "toc_delay_ms": 0, "max_retries": 1,
            "cooldown_s": 1, "timeout_s": 5, "range_start": 1, "range_end": 0,
            "novel_delay_s": 0}
    for key, minimum in ints.items():
        if key in values:
            try:
                current[key] = max(minimum, int(values[key]))
            except (TypeError, ValueError):
                pass
    for key in ("include_preface", "include_afterword", "resume", "skip_existing"):
        if key in values:
            current[key] = bool(values[key])
    if "formats" in values:
        fmts = [f for f in values["formats"] if f in ("epub", "txt")]
        current["formats"] = fmts or ["epub"]
    if "custom_tags" in values:
        tags = [str(t).strip() for t in values["custom_tags"] if str(t).strip()]
        current["custom_tags"] = list(dict.fromkeys(tags))
    config.SETTINGS_PATH.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
    return current


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
_session_lock = threading.Lock()
_session: Optional[requests.Session] = None


def _http() -> requests.Session:
    global _session
    with _session_lock:
        if _session is None:
            s = requests.Session()
            s.headers.update({"User-Agent": config.USER_AGENT, "Accept-Language": "ja,en;q=0.8"})
            s.cookies.set("over18", "yes", domain=".syosetu.com")
            _session = s
        return _session


def _fetch(url: str, settings: Dict, control: Optional[CrawlControl] = None,
           progress: Optional[ProgressFn] = None) -> str:
    """GET with retry + cooldown (403/429/5xx), same policy as the userscript."""
    control = control or CrawlControl()
    retries = int(settings.get("max_retries", 3))
    cooldown = int(settings.get("cooldown_s", 12))
    timeout = int(settings.get("timeout_s", 30))
    last_err = "unknown error"
    for attempt in range(1, retries + 1):
        control.checkpoint()
        try:
            resp = _http().get(url, timeout=timeout)
            if resp.ok:
                resp.encoding = resp.encoding or "utf-8"
                return resp.text
            last_err = f"HTTP {resp.status_code}"
            if resp.status_code not in (403, 429) and resp.status_code < 500:
                break  # 404 etc. -> no point retrying
        except requests.RequestException as exc:
            last_err = f"{type(exc).__name__}: {exc}"
        if attempt < retries:
            wait = attempt * cooldown
            if progress:
                progress({"type": "log", "level": "warn",
                          "message": f"{last_err}. Cooldown {wait}s (retry {attempt}/{retries - 1})"})
            control.sleep(wait)
    raise RuntimeError(f"{last_err} for {url}")


# ---------------------------------------------------------------------------
# Search (Narou API)
# ---------------------------------------------------------------------------
def _split_terms(value) -> List[str]:
    if not value:
        return []
    if isinstance(value, str):
        value = re.split(r"[,\s、　]+", value)
    return [v.strip() for v in value if v and v.strip()]


def _join_ids(value) -> str:
    if not value:
        return ""
    if isinstance(value, str):
        value = re.split(r"[,\-\s]+", value)
    return "-".join(str(int(v)) for v in value if str(v).strip().isdigit())


def _normalize_novel(raw: Dict) -> Dict:
    ncode = (raw.get("ncode") or "").lower()
    is_short = raw.get("noveltype") == 2
    return {
        "ncode": ncode,
        "title": raw.get("title", ""),
        "writer": raw.get("writer", ""),
        "story": raw.get("story", ""),
        "genre": raw.get("genre"),
        "genre_name": config.GENRE_MAP.get(raw.get("genre"), str(raw.get("genre", ""))),
        "keywords": (raw.get("keyword") or "").split(),
        "type": "short" if is_short else "serial",
        "complete": is_short or raw.get("end") == 0,
        "chapters": raw.get("general_all_no", 0),
        "length": raw.get("length", 0),
        "points": raw.get("global_point", 0),
        "bookmarks": raw.get("fav_novel_cnt", 0),
        "first_up": raw.get("general_firstup", ""),
        "last_up": raw.get("general_lastup", ""),
        "hiatus": bool(raw.get("isstop")),
        "url": f"{config.NOVEL_BASE_URL}/{ncode}/",
    }


def _api_query(params: Dict) -> Dict:
    params = {k: v for k, v in params.items() if v not in (None, "", [], 0) or k == "st"}
    params["out"] = "json"
    resp = _http().get(config.API_URL, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if not data:
        return {"allcount": 0, "items": []}
    return {"allcount": data[0].get("allcount", 0), "items": [_normalize_novel(r) for r in data[1:]]}


def search(query: Dict) -> Dict:
    """Advanced search.

    query keys (all optional):
      keywords: list|str   include terms
      mode: "all"|"any"    AND vs OR across include terms
      exclude: list|str    excluded terms (notword)
      fields: list         subset of title/ex/keyword/wname (where to match)
      genres / not_genres: list of genre ids
      flags: list          keys from config.SEARCH_FLAGS
      type: str            config.NOVEL_TYPES key
      order: str           config.ORDERS key
      min_len / max_len:   character count range
      min_chapters / max_chapters: post-filter on episode count
      limit (1-500), page (1-based)
    """
    keywords = _split_terms(query.get("keywords"))
    exclude = _split_terms(query.get("exclude"))
    limit = max(1, min(500, int(query.get("limit") or 20)))
    page = max(1, int(query.get("page") or 1))
    base: Dict = {
        "lim": limit,
        "st": min(2000, (page - 1) * limit + 1),
        "order": query.get("order") or "hyoka",
        "notword": " ".join(exclude),
        "genre": _join_ids(query.get("genres")),
        "notgenre": _join_ids(query.get("not_genres")),
        "type": query.get("type") or "",
    }
    for field in query.get("fields") or []:
        if field in ("title", "ex", "keyword", "wname"):
            base[field] = 1
    for flag in query.get("flags") or []:
        if flag in config.SEARCH_FLAGS:
            base[flag] = 1
    min_len, max_len = int(query.get("min_len") or 0), int(query.get("max_len") or 0)
    if min_len or max_len:
        base["length"] = f"{min_len or ''}-{max_len or ''}"

    mode = query.get("mode", "all")
    per_term: Dict[str, int] = {}
    if mode == "any" and len(keywords) > 1:
        merged: Dict[str, Dict] = {}
        allcount = 0
        for term in keywords:
            res = _api_query({**base, "word": term})
            per_term[term] = res["allcount"]
            allcount += res["allcount"]
            for item in res["items"]:
                entry = merged.setdefault(item["ncode"], {**item, "matched": []})
                entry["matched"].append(term)
        items = sorted(merged.values(), key=lambda x: -len(x["matched"]))
    else:
        res = _api_query({**base, "word": " ".join(keywords)})
        allcount, items = res["allcount"], res["items"]
        for item in items:
            item["matched"] = keywords

    min_ch, max_ch = int(query.get("min_chapters") or 0), int(query.get("max_chapters") or 0)
    if min_ch or max_ch:
        items = [i for i in items if i["chapters"] >= min_ch and (not max_ch or i["chapters"] <= max_ch)]

    return {"allcount": allcount, "per_term": per_term, "page": page, "limit": limit, "items": items}


def search_novels(keyword: str, exclude_genres: Optional[List[int]] = None, limit: int = 20) -> List[Dict]:
    """Legacy wrapper used by pipeline.py / translate_and_upload.py."""
    return search({"keywords": keyword, "not_genres": exclude_genres or [], "limit": limit})["items"]


def fetch_meta(ncode: str, settings: Optional[Dict] = None) -> Dict:
    """Fetch combined metadata for a single ncode (API + web fallback)."""
    ncode = ncode.strip().lower()
    api_meta = {}
    try:
        api_meta = lookup_ncodes([ncode]).get(ncode, {})
    except Exception:
        pass

    try:
        first_html = _fetch(f"{config.NOVEL_BASE_URL}/{ncode}/", settings or load_settings(), CrawlControl())
        first = _soup(first_html)
        web_meta = _parse_meta(first, ncode)
    except Exception:
        web_meta = {"ncode": ncode, "title": ncode, "writer": "Unknown", "url": f"{config.NOVEL_BASE_URL}/{ncode}/"}

    return {**web_meta, **{k: v for k, v in api_meta.items() if v}}


def lookup_ncodes(ncodes: Iterable[str]) -> Dict[str, Dict]:
    """Fetch API metadata for many ncodes (batched, 100 per request)."""
    codes = [c.lower() for c in ncodes]
    found: Dict[str, Dict] = {}
    for i in range(0, len(codes), 100):
        chunk = codes[i:i + 100]
        res = _api_query({"ncode": "-".join(chunk), "lim": len(chunk), "st": 1})
        for item in res["items"]:
            found[item["ncode"]] = item
    return found


def parse_ncode_list(text: str) -> List[str]:
    """Extract unique ncodes (order kept) from free text / URLs / .txt contents.

    Lines starting with '#' are treated as comments.
    """
    lines = [ln for ln in (text or "").splitlines() if not ln.lstrip().startswith("#")]
    found = NCODE_RE.findall("\n".join(lines))
    return list(dict.fromkeys(c.lower() for c in found))


# ---------------------------------------------------------------------------
# Scraping
# ---------------------------------------------------------------------------
_PARSER: Optional[str] = None


def _get_parser() -> str:
    global _PARSER
    if _PARSER is None:
        try:
            import lxml  # noqa: F401
            _PARSER = "lxml"
        except ImportError:
            _PARSER = "html.parser"
    return _PARSER


def _soup(markup: str) -> BeautifulSoup:
    return BeautifulSoup(markup, _get_parser())


def _text_or(soup: BeautifulSoup, selector: str, default: str = "") -> str:
    el = soup.select_one(selector)
    return el.get_text(" ", strip=True) if el else default


def _parse_meta(soup: BeautifulSoup, ncode: str) -> Dict:
    author = _text_or(soup, ".p-novel__author, .novel_writername", "Unknown")
    author = re.sub(r"^作者[：:]\s*", "", author).strip()
    return {
        "ncode": ncode,
        "title": _text_or(soup, ".p-novel__title, .novel_title, h1", ncode),
        "writer": author,
        "story": _text_or(soup, "#novel_ex, .p-novel__summary"),
        "url": f"{config.NOVEL_BASE_URL}/{ncode}/",
    }


def _parse_toc(soup: BeautifulSoup, ncode: str) -> List[Dict]:
    chapters: List[Dict] = []
    volume = ""
    container = soup.select_one(".p-eplist") or soup.select_one(".index_box")
    if not container:
        return chapters
    for el in container.select(".p-eplist__chapter-title, .chapter_title, .p-eplist__subtitle, .subtitle a"):
        classes = el.get("class") or []
        if "p-eplist__chapter-title" in classes or "chapter_title" in classes:
            volume = el.get_text(strip=True)
            continue
        href = el.get("href") or ""
        m = re.search(r"/(\d+)/?$", href)
        if not m:
            continue
        chapters.append({
            "episode": int(m.group(1)),
            "title": el.get_text(strip=True),
            "volume": volume,
            "url": f"{config.NOVEL_BASE_URL}/{ncode}/{m.group(1)}/",
        })
    return chapters


def _toc_pages(soup: BeautifulSoup, total_chapters: int = 0) -> int:
    """Detect total TOC pages.
    Narou displays up to 100 chapters per page.
    If total_chapters is known from API (general_all_no), use ceil(total_chapters / 100).
    Otherwise inspect pager links.
    """
    if total_chapters and total_chapters > 0:
        return (total_chapters + 99) // 100

    pages = [1]
    for sel in (".c-pager__item--last[href]", ".novelview_pager-box a[href*='p=']", "a[href*='?p=']"):
        for link in soup.select(sel):
            m = re.search(r"[?&]p=(\d+)", link.get("href", ""))
            if m:
                pages.append(int(m.group(1)))
    return max(pages)


def _clean_block(el, keep: bool = True) -> List[str]:
    """Return paragraph strings from a content element (ruby readings dropped)."""
    if el is None or not keep:
        return []
    for tag in el.select("rt, rp, script, style"):
        tag.decompose()
    for img in el.select("img"):
        img.replace_with(f"[挿絵: {img.get('src', '')}]")
    paras = el.select("p")
    if not paras:
        return [ln.strip() for ln in el.get_text("\n").splitlines()]
    return [p.get_text("", strip=False).strip("\n") for p in paras]


def _parse_chapter(soup: BeautifulSoup, settings: Dict) -> Dict:
    preface = soup.select_one(".p-novel__text--preface, #novel_p")
    afterword = soup.select_one(".p-novel__text--afterword, #novel_a")
    body = soup.select_one(".p-novel__body, #novel_honbun")
    body_paras: List[str] = []
    if body is not None:
        for el in body.select(".p-novel__text--preface, #novel_p, .p-novel__text--afterword, #novel_a"):
            el.extract()
        body_paras = _clean_block(body)
    return {
        "title": _text_or(soup, ".p-novel__title--rensai, .p-novel__title, .novel_subtitle"),
        "preface": _clean_block(preface, settings.get("include_preface", True)),
        "body": body_paras,
        "afterword": _clean_block(afterword, settings.get("include_afterword", True)),
    }


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------
def _cache_dir(ncode: str) -> pathlib.Path:
    path = config.CACHE_DIR / ncode
    path.mkdir(parents=True, exist_ok=True)
    return path


def cached_count(ncode: str) -> int:
    path = config.CACHE_DIR / ncode.lower()
    return len(list(path.glob("[0-9]*.json"))) if path.is_dir() else 0


def clear_cache(ncode: str) -> int:
    path = config.CACHE_DIR / ncode.lower()
    removed = 0
    if path.is_dir():
        for f in path.glob("*.json"):
            f.unlink()
            removed += 1
    return removed


# ---------------------------------------------------------------------------
# Output writers
# ---------------------------------------------------------------------------
def _safe_name(name: str, max_bytes: int = 120) -> str:
    name = re.sub(r'[\\/:*?"<>|\r\n\t]+', " ", name).strip().rstrip(".")
    # Truncate by UTF-8 bytes to prevent Linux filesystem 255-byte limit errors
    encoded = name.encode("utf-8")
    if len(encoded) > max_bytes:
        name = encoded[:max_bytes].decode("utf-8", errors="ignore").strip().rstrip(".")
    return name or "novel"


def output_basename(meta: Dict) -> str:
    return f"{meta['ncode']} - {_safe_name(meta['title'])}"


def _xhtml(title: str, body: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE html>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" '
        'xml:lang="ja" lang="ja">\n<head><meta charset="UTF-8"/>'
        f"<title>{html.escape(title)}</title>"
        '<link rel="stylesheet" type="text/css" href="style.css"/></head>\n'
        f"<body>\n{body}\n</body>\n</html>\n"
    )


def _paras_html(paras: List[str], css: str = "", clean_blank: bool = True) -> str:
    out = []
    prev_blank = False
    for p in paras:
        p_str = p.strip()
        if p_str:
            out.append(f"<p>{html.escape(p_str)}</p>")
            prev_blank = False
        else:
            if not clean_blank:
                out.append('<p class="blank"><br/></p>')
            elif not prev_blank:
                out.append('<p class="blank"></p>')
                prev_blank = True
    inner = "\n".join(out)
    return f'<div class="{css}">\n{inner}\n</div>' if css else inner


def _generate_retro_cover_jpg(title: str, author: str, ncode: str = "", tag: str = "READ THIS • WEB NOVEL") -> bytes:
    from PIL import Image, ImageDraw, ImageFont
    import io

    img = Image.new("RGB", (800, 1200), color="#101014")
    draw = ImageDraw.Draw(img)

    # Outer & Inner Retro Frames
    draw.rectangle([36, 36, 764, 1164], outline="#27272a", width=4)
    draw.rectangle([48, 48, 752, 1152], outline="#3f3f46", width=2)

    # Corner notches
    draw.line([(40, 70), (70, 70), (70, 40)], fill="#71717a", width=2)
    draw.line([(760, 70), (730, 70), (730, 40)], fill="#71717a", width=2)
    draw.line([(40, 1130), (70, 1130), (70, 1160)], fill="#71717a", width=2)
    draw.line([(760, 1130), (730, 1130), (730, 1160)], fill="#71717a", width=2)

    try:
        font_tag = ImageFont.truetype("arial.ttf", 22)
        font_author = ImageFont.truetype("msgothic.ttc", 28)
        font_sub = ImageFont.truetype("arial.ttf", 18)
    except Exception:
        font_tag = font_author = font_sub = ImageFont.load_default()

    # Top Tag
    draw.text((400, 125), tag.upper(), fill="#a1a1aa", font=font_tag, anchor="mm")
    draw.line([(310, 150), (490, 150)], fill="#3f3f46", width=2)
    draw.polygon([(400, 145), (405, 150), (400, 155), (395, 150)], fill="#71717a")

    # Center Art Emblem based on seed
    seed = abs(hash((title or "") + (author or ""))) % 4

    if seed == 0:
        # Astrolabe & Orbit
        draw.ellipse([(220, 370), (580, 730)], outline="#27272a", width=4)
        draw.ellipse([(260, 410), (540, 690)], outline="#3f3f46", width=2)
        draw.ellipse([(310, 460), (490, 640)], outline="#52525b", width=3)
        draw.line([(190, 550), (610, 550)], fill="#3f3f46", width=2)
        draw.line([(400, 340), (400, 760)], fill="#3f3f46", width=2)
        draw.ellipse([(240, 490), (560, 610)], outline="#71717a", width=3)
        draw.polygon([(400, 480), (412, 535), (470, 550), (412, 565), (400, 620), (388, 565), (330, 550), (388, 535)], fill="#f4f4f5")
        draw.polygon([(490, 460), (500, 470), (490, 480), (480, 470)], fill="#71717a")
        draw.polygon([(310, 620), (320, 630), (310, 640), (300, 630)], fill="#71717a")
    elif seed == 1:
        # Open Grimoire & Portal
        draw.polygon([(400, 350), (570, 550), (400, 750), (230, 550)], outline="#27272a", width=4)
        draw.ellipse([(270, 420), (530, 680)], outline="#3f3f46", width=2)
        draw.polygon([(400, 410), (435, 455), (400, 500), (365, 455)], outline="#71717a", fill="#18181b", width=2)
        draw.polygon([(290, 570), (400, 550), (510, 570), (510, 630), (400, 610), (290, 630)], outline="#e4e4e7", fill="#18181b", width=3)
        draw.line([(400, 550), (400, 610)], fill="#e4e4e7", width=3)
        draw.polygon([(400, 440), (406, 470), (440, 478), (406, 486), (400, 515), (394, 486), (360, 478), (394, 470)], fill="#f4f4f5")
    elif seed == 2:
        # Solar Enso & Mountain Horizon
        draw.ellipse([(230, 380), (570, 720)], outline="#27272a", width=4)
        draw.ellipse([(300, 450), (500, 650)], outline="#e4e4e7", width=5)
        draw.line([(250, 600), (550, 600)], fill="#52525b", width=2)
        draw.line([(280, 620), (520, 620)], fill="#3f3f46", width=2)
        draw.polygon([(300, 600), (360, 530), (410, 580), (460, 540), (500, 600)], fill="#111114", outline="#e4e4e7", width=3)
        draw.polygon([(400, 360), (405, 365), (400, 370), (395, 365)], fill="#f4f4f5")
    else:
        # Heraldic Blade & Shield
        draw.polygon([(260, 470), (400, 370), (540, 470), (540, 620), (400, 740), (260, 620)], outline="#27272a", width=4)
        draw.line([(400, 400), (400, 680)], fill="#f4f4f5", width=5)
        draw.line([(340, 500), (460, 500)], fill="#f4f4f5", width=5)
        draw.ellipse([(392, 680), (408, 696)], fill="#e4e4e7")
        draw.polygon([(400, 380), (390, 410), (410, 410)], fill="#f4f4f5")
        draw.polygon([(320, 450), (330, 460), (320, 470), (310, 460)], fill="#a1a1aa")
        draw.polygon([(480, 450), (490, 460), (480, 470), (470, 460)], fill="#a1a1aa")

    # Author & Bottom Metadata Section
    author_y = 920
    draw.line([(310, author_y - 30), (490, author_y - 30)], fill="#3f3f46", width=2)
    draw.polygon([(400, author_y - 35), (405, author_y - 30), (400, author_y - 25), (395, author_y - 30)], fill="#71717a")
    draw.text((400, author_y - 5), "AUTHOR", fill="#71717a", font=font_sub, anchor="mm")
    
    clean_author = (author or "Unknown").strip()
    if len(clean_author) > 22:
        clean_author = clean_author[:21] + "..."
    draw.text((400, author_y + 35), clean_author, fill="#e4e4e7", font=font_author, anchor="mm")

    bottom_tag = f"{ncode.upper()} • DIGITAL ARCHIVE" if ncode else "ARCHIVE COLLECTION • DIGITAL EDITION"
    draw.text((400, 1100), bottom_tag, fill="#52525b", font=font_sub, anchor="mm")

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


def write_epub(meta: Dict, chapters: List[Dict], path: pathlib.Path, settings: Optional[Dict] = None) -> pathlib.Path:
    """Standardized EPUB3 with clean WTR typography and single-page synopsis QC."""
    book_id = f"urn:uuid:{uuid.uuid5(uuid.NAMESPACE_URL, meta['url'])}"
    modified = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    clean_blank = True if settings is None else settings.get("clean_blank_lines", True)

    lang = (meta.get("language") or meta.get("target_lang") or "ja").lower()
    if lang in ("id", "indonesia", "indonesian"):
        lang = "id"
    elif lang in ("en", "english"):
        lang = "en"
    elif lang in ("ja", "jp", "japanese"):
        lang = "ja"

    ncode_tag = meta.get("ncode", "")
    target_tag = f"{lang.upper()} EDITION" if lang != "ja" else "ORIGINAL EDITION"
    cover_jpg = _generate_retro_cover_jpg(meta.get("title", ""), meta.get("writer", ""), ncode=ncode_tag, tag=target_tag)
    cover_page = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<!DOCTYPE html>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">\n'
        '<head><title>Cover</title><style>body,html{margin:0;padding:0;height:100%;text-align:center;background:#111114;}img{width:100%;height:100%;max-width:100%;object-fit:contain;}</style></head>\n'
        '<body><div><img src="cover.jpg" alt="Cover" /></div></body>\n</html>'
    )

    css = (
        "body{font-family:\"Georgia\",\"Times New Roman\",serif;line-height:1.65;margin:0 4%;text-align:justify;color:#111;}\n"
        "h1{text-align:center;font-size:1.6em;margin-top:1.5em;margin-bottom:0.8em;color:#222;}\n"
        "h2{text-align:center;font-size:1.3em;margin-top:1.8em;margin-bottom:1.2em;page-break-before:always;color:#222;}\n"
        "p{margin-top:0;margin-bottom:0.8em;text-indent:0;}\n"
        "p.blank{height:0.5em;margin:0;}\n"
        ".preface,.afterword{font-size:.9em;color:#555;border-top:1px solid #ccc;border-bottom:1px solid #ccc;padding:.8em 0;margin:1.2em 0;}\n"
        ".info-card{text-align:center;padding:1.5em 0.5em;page-break-after:always;}\n"
        ".info-meta{font-size:.9em;color:#666;margin:1em 0;line-height:1.4;}\n"
        ".story{text-align:justify;border-top:1px solid #ddd;padding-top:1.2em;margin-top:1.2em;}\n"
    )
    info_body = (
        f'<div class="info-card">'
        f'<h1>{html.escape(meta["title"])}</h1>'
        f'<div class="info-meta">'
        f'<p><b>Author:</b> {html.escape(meta.get("writer", "Unknown"))}</p>'
        f'<p><b>Source:</b> {html.escape(meta.get("url", ""))}</p>'
        f'</div>'
        f'<div class="story">{_paras_html(meta.get("story", "").splitlines(), clean_blank=clean_blank)}</div>'
        f'</div>'
    )
    files = [("info.xhtml", "Info", _xhtml(meta["title"], info_body))]
    for idx, ch in enumerate(chapters, 1):
        parts = [f"<h2>{html.escape(ch['title'])}</h2>"]
        if ch.get("preface"):
            parts.append(_paras_html(ch["preface"], "preface", clean_blank=clean_blank))
        parts.append(_paras_html(ch.get("body", []), clean_blank=clean_blank))
        if ch.get("afterword"):
            parts.append(_paras_html(ch["afterword"], "afterword", clean_blank=clean_blank))
        files.append((f"ch{idx:05d}.xhtml", ch["title"], _xhtml(ch["title"], "\n".join(parts))))

    nav_items = "\n".join(f'<li><a href="{f}">{html.escape(t)}</a></li>' for f, t, _ in files)
    nav = _xhtml("Contents", f'<nav epub:type="toc" id="toc"><h1>Contents</h1><ol>\n{nav_items}\n</ol></nav>')
    ncx_points = "\n".join(
        f'<navPoint id="np{i}" playOrder="{i}"><navLabel><text>{html.escape(t)}</text></navLabel>'
        f'<content src="{f}"/></navPoint>' for i, (f, t, _) in enumerate(files, 1))
    ncx = ('<?xml version="1.0" encoding="UTF-8"?>\n<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">'
           f'<head><meta name="dtb:uid" content="{book_id}"/></head>'
           f'<docTitle><text>{html.escape(meta["title"])}</text></docTitle><navMap>\n{ncx_points}\n</navMap></ncx>')
    subjects = "".join(f"<dc:subject>{html.escape(k)}</dc:subject>" for k in meta.get("keywords", []))
    manifest = "\n".join(f'<item id="f{i}" href="{f}" media-type="application/xhtml+xml"/>'
                         for i, (f, _, _) in enumerate(files))
    spine = "\n".join(f'<itemref idref="f{i}"/>' for i in range(len(files)))
    opf = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid" xml:lang="{lang}">\n'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
        f'<dc:identifier id="bookid">{book_id}</dc:identifier>'
        f'<dc:title>{html.escape(meta["title"])}</dc:title>'
        + (f'<dc:title id="original_title">{html.escape(meta["original_title"])}</dc:title>' if meta.get("original_title") else '') +
        f'<dc:creator>{html.escape(meta["writer"])}</dc:creator>'
        f'<dc:language>{lang}</dc:language><dc:publisher>小説家になろう</dc:publisher>'
        f'<dc:source>{html.escape(meta["url"])}</dc:source>'
        f'<dc:description>{html.escape(meta.get("story", ""))}</dc:description>{subjects}'
        f'<meta property="dcterms:modified">{modified}</meta>'
        '<meta name="cover" content="cover-image"/>'
        '</metadata>\n'
        '<manifest><item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>'
        '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>'
        '<item id="cover-page" href="cover.xhtml" media-type="application/xhtml+xml"/>'
        '<item id="cover-image" href="cover.jpg" media-type="image/jpeg" properties="cover-image"/>'
        f'<item id="css" href="style.css" media-type="text/css"/>\n{manifest}</manifest>\n'
        f'<spine toc="ncx">\n<itemref idref="cover-page"/>\n{spine}\n</spine>\n</package>'
    )
    container = ('<?xml version="1.0" encoding="UTF-8"?>\n<container version="1.0" '
                 'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
                 '<rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>'
                 '</rootfiles></container>')
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".epub.part")
    with zipfile.ZipFile(tmp, "w") as zf:
        zf.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        zf.writestr("META-INF/container.xml", container, compress_type=zipfile.ZIP_DEFLATED)
        zf.writestr("OEBPS/content.opf", opf, compress_type=zipfile.ZIP_DEFLATED)
        zf.writestr("OEBPS/cover.jpg", cover_jpg, compress_type=zipfile.ZIP_DEFLATED)
        zf.writestr("OEBPS/cover.xhtml", cover_page, compress_type=zipfile.ZIP_DEFLATED)
        zf.writestr("OEBPS/nav.xhtml", nav, compress_type=zipfile.ZIP_DEFLATED)
        zf.writestr("OEBPS/toc.ncx", ncx, compress_type=zipfile.ZIP_DEFLATED)
        zf.writestr("OEBPS/style.css", css, compress_type=zipfile.ZIP_DEFLATED)
        for fname, _, content in files:
            zf.writestr(f"OEBPS/{fname}", content, compress_type=zipfile.ZIP_DEFLATED)
    tmp.replace(path)
    return path


def write_txt(meta: Dict, chapters: List[Dict], path: pathlib.Path) -> pathlib.Path:
    lines = [meta["title"], f"作者：{meta['writer']}", meta["url"], "", meta.get("story", ""), ""]
    for ch in chapters:
        lines += ["", "=" * 40, ch["title"], "=" * 40, ""]
        if ch.get("preface"):
            lines += ch["preface"] + ["", "-" * 20, ""]
        lines += ch.get("body", [])
        if ch.get("afterword"):
            lines += ["", "-" * 20, ""] + ch["afterword"]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Main download routine
# ---------------------------------------------------------------------------
def crawl_novel(ncode: str, settings: Optional[Dict] = None, control: Optional[CrawlControl] = None,
                progress: Optional[ProgressFn] = None, out_dir: Optional[str] = None) -> Dict:
    """Download one novel with progress events.

    progress receives dicts:
      {"type": "stage", "stage": "meta|toc|chapters|build|done", "message": ...}
      {"type": "meta", "meta": {...}}
      {"type": "progress", "done": n, "total": t, "episode": e, "title": ..., "cached": bool}
      {"type": "log", "level": "info|warn|error", "message": ...}
    Returns {"meta", "files", "chapters", "fetched", "cached"}.
    """
    ncode = ncode.strip().lower()
    if not NCODE_RE.fullmatch(ncode):
        raise ValueError(f"Invalid ncode: {ncode!r}")
    settings = {**load_settings(), **(settings or {})}
    control = control or CrawlControl()
    emit = progress or (lambda _e: None)
    out_path = pathlib.Path(out_dir).expanduser().resolve() if out_dir else config.DOWNLOAD_DIR
    out_path.mkdir(parents=True, exist_ok=True)

    # 1. Metadata (API gives keywords/genre; TOC page gives fallback)
    emit({"type": "stage", "stage": "meta", "message": "Fetching metadata"})
    api_meta = {}
    try:
        api_meta = lookup_ncodes([ncode]).get(ncode, {})
    except Exception as exc:  # API outage should not stop the scrape
        emit({"type": "log", "level": "warn", "message": f"API metadata failed: {exc}"})

    first_html = _fetch(f"{config.NOVEL_BASE_URL}/{ncode}/", settings, control, emit)
    first = _soup(first_html)
    meta = {**_parse_meta(first, ncode), **{k: v for k, v in api_meta.items() if v}}
    emit({"type": "meta", "meta": meta})

    if settings.get("skip_existing"):
        base = out_path / output_basename(meta)
        existing = [str(base.with_suffix(f".{f}")) for f in settings["formats"] if base.with_suffix(f".{f}").exists()]
        if existing and len(existing) == len(settings["formats"]):
            emit({"type": "log", "level": "info", "message": "Output exists, skipped"})
            return {"meta": meta, "files": existing, "chapters": 0, "fetched": 0, "cached": 0, "skipped": True}

    # 2. TOC
    emit({"type": "stage", "stage": "toc", "message": "Scanning table of contents"})
    is_short = first.select_one(".p-eplist, .index_box") is None and first.select_one(".p-novel__body, #novel_honbun")
    if is_short:
        toc = [{"episode": 1, "title": meta["title"], "volume": "", "url": meta["url"], "short": True}]
    else:
        toc = _parse_toc(first, ncode)
        pages = _toc_pages(first, total_chapters=meta.get("chapters", 0))
        for p in range(2, pages + 1):
            emit({"type": "stage", "stage": "toc", "message": f"Scanning TOC page {p}/{pages}"})
            control.sleep(settings["toc_delay_ms"] / 1000)
            toc += _parse_toc(_soup(_fetch(f"{meta['url']}?p={p}", settings, control, emit)), ncode)
    if not toc:
        raise RuntimeError("No chapters found (layout changed or novel removed)")

    start = max(1, int(settings.get("range_start") or 1))
    end = int(settings.get("range_end") or 0) or len(toc)
    targets = toc[start - 1:end]
    total = len(targets)
    emit({"type": "log", "level": "info", "message": f"{len(toc)} episodes found, downloading {start}-{start + total - 1}"})

    # 3. Chapters
    emit({"type": "stage", "stage": "chapters", "message": "Downloading chapters"})
    cache = _cache_dir(ncode)
    chapters: List[Dict] = []
    fetched = cached = 0
    for i, item in enumerate(targets, 1):
        control.checkpoint()
        cfile = cache / f"{item['episode']:05d}.json"
        data = None
        if settings.get("resume") and cfile.exists():
            try:
                data = json.loads(cfile.read_text(encoding="utf-8"))
                cached += 1
            except json.JSONDecodeError:
                data = None
        if data is None:
            soup = first if item.get("short") else _soup(_fetch(item["url"], settings, control, emit))
            data = _parse_chapter(soup, settings)
            data["title"] = item["title"] if not item.get("short") else (data["title"] or item["title"])
            data["episode"] = item["episode"]
            data["volume"] = item["volume"]
            cfile.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            fetched += 1
            is_cached = False
        else:
            is_cached = True
        chapters.append(data)
        emit({"type": "progress", "done": i, "total": total, "episode": item["episode"],
              "title": item["title"], "cached": is_cached})
        if not is_cached and i < total:
            jitter = random.randint(0, int(settings.get("jitter_ms", 0))) if settings.get("jitter_ms") else 0
            control.sleep((int(settings["delay_ms"]) + jitter) / 1000)

    # 4. Build outputs
    emit({"type": "stage", "stage": "build", "message": "Building output files"})
    for ch in chapters:  # prefix volume name so it shows in TOC
        if ch.get("volume") and not ch["title"].startswith(ch["volume"]):
            ch["display_title"] = f"{ch['volume']} - {ch['title']}"
    view = [{**c, "title": c.get("display_title", c["title"])} for c in chapters]
    base = out_path / output_basename(meta)
    files = []
    if "epub" in settings["formats"]:
        ep_path = write_epub(meta, view, base.with_suffix(".epub"))
        if settings.get("standardize_epub", True) or settings.get("clean_blank_lines", True):
            try:
                from . import qc
                qc.standardize_epub_file(ep_path)
            except Exception as e:
                logger.warning(f"QC pass failed on {ep_path}: {e}")
        files.append(str(ep_path))
    if "txt" in settings["formats"]:
        files.append(str(write_txt(meta, view, base.with_suffix(".txt"))))
    emit({"type": "stage", "stage": "done", "message": f"Saved {len(files)} file(s)"})
    return {"meta": meta, "files": files, "chapters": total, "fetched": fetched, "cached": cached}


def download_novel(ncode: str, out_dir: str) -> str:
    """Legacy API: download a novel and return the .txt path."""
    settings = load_settings()
    settings["formats"] = list(dict.fromkeys(settings["formats"] + ["txt"]))
    result = crawl_novel(ncode, settings=settings, out_dir=out_dir)
    return next(f for f in result["files"] if f.endswith(".txt"))
