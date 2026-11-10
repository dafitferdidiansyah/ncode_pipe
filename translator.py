"""Bookfere Calibre-based translator engine for ncode_pipe.
Translates Japanese text/chapters/metadata to English or Indonesian
using the built-in 'Google(Free)New' engine from Calibre Ebook Translator plugin.
Does not consume AI API tokens.
"""

import os
import sys
import time
import json
import re
import html
import pathlib
import subprocess
from typing import Dict, List, Optional, Callable

# Language mapping: UI code -> Bookfere engine name
LANG_MAP = {
    "en": "English",
    "id": "Indonesian",
}

# Python script template executed via calibre-debug
CALIBRE_TRANSLATE_SCRIPT = r"""
import sys
import json

source_lang = sys.argv[1]
target_lang = sys.argv[2]
input_json = sys.stdin.read()
items = json.loads(input_json)

from calibre.customize.ui import initialized_plugins
[p for p in initialized_plugins() if p.name == 'Ebook Translator']

from calibre_plugins.ebook_translator.engines.google import GoogleFreeTranslateNew
engine = GoogleFreeTranslateNew()
engine.set_source_lang(source_lang)
engine.set_target_lang(target_lang)

results = []
for item in items:
    text = item.get("text", "")
    if not text.strip():
        results.append({"id": item.get("id"), "text": ""})
        continue
    try:
        translated = engine.translate(text)
        results.append({"id": item.get("id"), "text": translated})
    except Exception as e:
        results.append({"id": item.get("id"), "text": text, "error": str(e)})

print(json.dumps(results, ensure_ascii=False))
"""


def get_calibre_debug() -> str:
    possible = [
        "calibre-debug",
        r"C:\Program Files\Calibre2\calibre-debug.exe",
        r"C:\Program Files (x86)\Calibre2\calibre-debug.exe",
    ]
    for p in possible:
        if os.path.isfile(p):
            return p
    return "calibre-debug"


class BookfereTranslator:
    def __init__(self, target_lang: str = "id", delay_s: float = 1.2):
        self.target_lang_code = target_lang if target_lang in LANG_MAP else "id"
        self.target_lang_name = LANG_MAP[self.target_lang_code]
        self.source_lang_name = "Japanese"
        self.delay_s = delay_s
        self.calibre_debug = get_calibre_debug()

    def translate_batch(self, items: List[Dict[str, str]]) -> List[Dict[str, str]]:
        """items: [{"id": ..., "text": "..."}] -> [{"id": ..., "text": "..."}]"""
        if not items:
            return []

        cmd = [self.calibre_debug, "-c", CALIBRE_TRANSLATE_SCRIPT, self.source_lang_name, self.target_lang_name]
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        )
        stdout, stderr = proc.communicate(input=json.dumps(items, ensure_ascii=False))
        if proc.returncode != 0:
            raise RuntimeError(f"Calibre translate error: {stderr.strip() or stdout.strip()}")

        # Clean stdout: find JSON line
        for line in stdout.splitlines():
            line = line.strip()
            if line.startswith("[") and line.endswith("]"):
                return json.loads(line)

        # Fallback regex parse
        m = re.search(r"(\[.*\])", stdout, re.DOTALL)
        if m:
            return json.loads(m.group(1))
        raise RuntimeError(f"Failed to parse translator response: {stdout}")

    def translate_single(self, text: str) -> str:
        if not text.strip():
            return ""
        res = self.translate_batch([{"id": "single", "text": text}])
        return res[0].get("text", text)


def translate_novel_cached(
    ncode: str,
    meta: Dict,
    chapters: List[Dict],
    target_lang: str = "id",
    progress: Optional[Callable[[Dict], None]] = None,
    control: Optional[any] = None,
) -> Dict:
    """Translates metadata and chapters using cache files (so it can be resumed).
    Returns dict: {"meta": translated_meta, "chapters": translated_chapters}
    """
    from . import config, crawler

    emit = progress or (lambda ev: None)
    cache_dir = config.CACHE_DIR / ncode.lower() / f"tl_{target_lang}"
    cache_dir.mkdir(parents=True, exist_ok=True)

    translator = BookfereTranslator(target_lang=target_lang)

    # 1. Translate metadata
    meta_cache_file = cache_dir / "meta.json"
    if meta_cache_file.exists():
        tl_meta = json.loads(meta_cache_file.read_text(encoding="utf-8"))
        emit({"type": "stage", "stage": "tl_meta", "message": "Using cached translated metadata"})
    else:
        emit({"type": "stage", "stage": "tl_meta", "message": f"Translating title & synopsis to {target_lang.upper()}..."})
        batch = [
            {"id": "title", "text": meta.get("title", "")},
            {"id": "writer", "text": meta.get("writer", "")},
            {"id": "story", "text": meta.get("story", "")},
        ]
        res = translator.translate_batch(batch)
        lookup = {r["id"]: r.get("text", "") for r in res}
        tl_meta = dict(meta)
        tl_meta["original_title"] = meta.get("title", "")
        tl_meta["title"] = lookup.get("title") or meta.get("title", "")
        tl_meta["writer"] = lookup.get("writer") or meta.get("writer", "")
        tl_meta["story"] = lookup.get("story") or meta.get("story", "")
        tl_meta["target_lang"] = target_lang
        meta_cache_file.write_text(json.dumps(tl_meta, ensure_ascii=False, indent=2), encoding="utf-8")

    # 2. Translate chapters
    total = len(chapters)
    tl_chapters = []
    emit({"type": "stage", "stage": "tl_chapters", "message": f"Translating {total} chapters to {target_lang.upper()}..."})

    for i, ch in enumerate(chapters, 1):
        if control:
            control.checkpoint()

        ep = ch.get("episode", i)
        ch_cache_file = cache_dir / f"{ep:05d}.json"

        if ch_cache_file.exists():
            tl_ch = json.loads(ch_cache_file.read_text(encoding="utf-8"))
            tl_chapters.append(tl_ch)
            emit({"type": "progress_tl", "done": i, "total": total, "episode": ep, "title": tl_ch.get("title", ""), "cached": True})
            continue

        # Prepare paragraphs to translate
        # We translate title, lines (paragraphs), preface, afterword
        batch = [{"id": "title", "text": ch.get("title", "")}]
        lines = ch.get("body") or ch.get("lines") or []
        for idx, line in enumerate(lines):
            batch.append({"id": f"p_{idx}", "text": line})

        if ch.get("preface"):
            for pidx, pline in enumerate(ch["preface"]):
                batch.append({"id": f"pref_{pidx}", "text": pline})

        if ch.get("afterword"):
            for aidx, aline in enumerate(ch["afterword"]):
                batch.append({"id": f"aft_{aidx}", "text": aline})

        # Process in sub-chunks to avoid huge payloads (max 25 items per sub-batch)
        res_map = {}
        chunk_size = 25
        for cstart in range(0, len(batch), chunk_size):
            sub_batch = batch[cstart:cstart + chunk_size]
            sub_res = translator.translate_batch(sub_batch)
            for r in sub_res:
                res_map[r["id"]] = r.get("text", "")
            time.sleep(translator.delay_s)

        tl_ch = dict(ch)
        tl_ch["original_title"] = ch.get("title", "")
        tl_ch["title"] = res_map.get("title") or ch.get("title", "")
        translated_body = [res_map.get(f"p_{idx}", lines[idx]) for idx in range(len(lines))]
        tl_ch["body"] = translated_body
        tl_ch["lines"] = translated_body
        if ch.get("preface"):
            tl_ch["preface"] = [res_map.get(f"pref_{pidx}", ch["preface"][pidx]) for pidx in range(len(ch["preface"]))]
        if ch.get("afterword"):
            tl_ch["afterword"] = [res_map.get(f"aft_{aidx}", ch["afterword"][aidx]) for aidx in range(len(ch["afterword"]))]

        ch_cache_file.write_text(json.dumps(tl_ch, ensure_ascii=False), encoding="utf-8")
        tl_chapters.append(tl_ch)

        emit({"type": "progress_tl", "done": i, "total": total, "episode": ep, "title": tl_ch["title"], "cached": False})

    return {"meta": tl_meta, "chapters": tl_chapters}


def build_translated_epub(tl_meta: Dict, tl_chapters: List[Dict], out_dir: pathlib.Path) -> str:
    """Builds EPUB with translated metadata and contents."""
    from . import crawler

    lang_tag = tl_meta.get("target_lang", "id").upper()
    title = tl_meta.get("title", "novel")
    writer = tl_meta.get("writer", "Unknown")

    filename = f"[{lang_tag}] {crawler._safe_name(title)} - {crawler._safe_name(writer)}.epub"
    out_file = out_dir / filename

    # Set language tag in metadata
    meta_copy = dict(tl_meta)
    meta_copy["language"] = tl_meta.get("target_lang", "id")

    # Add display title to chapters
    view = []
    for ch in tl_chapters:
        c = dict(ch)
        if c.get("volume") and not c["title"].startswith(c["volume"]):
            c["title"] = f"{c['volume']} - {c['title']}"
        view.append(c)

    saved_path = crawler.write_epub(meta_copy, view, out_file)
    return str(saved_path)
