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
from typing import Dict, List, Optional, Callable, Any

# Language mapping: UI code -> Bookfere engine name
LANG_MAP = {
    "en": "English",
    "id": "Indonesian",
}

# Python script template executed via calibre-debug
CALIBRE_TRANSLATE_SCRIPT = r"""
import sys
import json
import re

source_lang = sys.argv[1]
target_lang = sys.argv[2]
input_raw = sys.stdin.read()
payload = json.loads(input_raw)

if isinstance(payload, dict):
    items = payload.get("items", [])
    merge_enabled = payload.get("merge_enabled", True)
    merge_length = int(payload.get("merge_length", 1800))
    engine_name = payload.get("engine_name")
else:
    items = payload
    merge_enabled = True
    merge_length = 1800
    engine_name = None

from calibre.customize.ui import initialized_plugins
[p for p in initialized_plugins() if p.name == 'Ebook Translator']

from calibre_plugins.ebook_translator.engines import builtin_engines
from calibre_plugins.ebook_translator.lib.config import get_config

cfg = get_config()
if not engine_name:
    engine_name = cfg.get('translate_engine', 'Google(Free)New')

engine = None
for cls in builtin_engines:
    if cls.name == engine_name:
        try:
            engine = cls()
            break
        except Exception:
            pass

if engine is None:
    from calibre_plugins.ebook_translator.engines.google import GoogleFreeTranslateNew
    engine = GoogleFreeTranslateNew()

engine.set_source_lang(source_lang)
engine.set_target_lang(target_lang)

results = []
sep = "\n\n"

if not merge_enabled:
    for item in items:
        text = (item.get("text") or "").strip()
        if not text:
            results.append({"id": item.get("id"), "text": ""})
            continue
        try:
            translated = engine.translate(text)
            results.append({"id": item.get("id"), "text": translated})
        except Exception as e:
            results.append({"id": item.get("id"), "text": text, "error": str(e)})
else:
    # Chunk merging mode for ultra-fast "sat-set" Bookfere translation
    chunks = []
    current_chunk = []
    current_len = 0
    for item in items:
        text = (item.get("text") or "").strip()
        if not text:
            results.append({"id": item.get("id"), "text": ""})
            continue
        item_len = len(text)
        if current_chunk and (current_len + item_len + len(sep) > merge_length):
            chunks.append(current_chunk)
            current_chunk = [item]
            current_len = item_len
        else:
            current_chunk.append(item)
            current_len += item_len + len(sep)
    if current_chunk:
        chunks.append(current_chunk)

    for chunk in chunks:
        if len(chunk) == 1:
            try:
                translated = engine.translate(chunk[0]["text"])
                results.append({"id": chunk[0]["id"], "text": translated})
            except Exception as e:
                results.append({"id": chunk[0]["id"], "text": chunk[0]["text"], "error": str(e)})
            continue

        merged_text = sep.join([item["text"] for item in chunk])
        try:
            translated = engine.translate(merged_text)
            split_trans = [t.strip() for t in re.split(r'\n{2,}', translated) if t.strip()]
            if len(split_trans) == len(chunk):
                for item, trans in zip(chunk, split_trans):
                    results.append({"id": item["id"], "text": trans})
            else:
                single_split = [t.strip() for t in translated.split('\n') if t.strip()]
                if len(single_split) == len(chunk):
                    for item, trans in zip(chunk, single_split):
                        results.append({"id": item["id"], "text": trans})
                else:
                    # Delimiter mismatch! Fallback to translating each item individually
                    # so that ZERO lines are ever left in original Japanese!
                    for item in chunk:
                        try:
                            item_text = (item.get("text") or "").strip()
                            if not item_text:
                                results.append({"id": item["id"], "text": ""})
                            else:
                                trans_item = engine.translate(item_text)
                                results.append({"id": item["id"], "text": trans_item})
                        except Exception as e:
                            results.append({"id": item["id"], "text": item["text"], "error": str(e)})
        except Exception as e:
            for item in chunk:
                try:
                    item_text = (item.get("text") or "").strip()
                    if not item_text:
                        results.append({"id": item["id"], "text": ""})
                    else:
                        translated_single = engine.translate(item_text)
                        results.append({"id": item["id"], "text": translated_single})
                except Exception as sub_e:
                    results.append({"id": item["id"], "text": item["text"], "error": str(sub_e)})

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


LOCAL_CONFIG_PATH = pathlib.Path(__file__).resolve().parent / "bookfere_config.json"


def get_calibre_config_path() -> Optional[pathlib.Path]:
    """Finds Calibre Ebook Translator plugin config path on Windows or Linux."""
    # 1. Windows APPDATA
    appdata = os.environ.get("APPDATA", "")
    if appdata:
        p = pathlib.Path(appdata) / "calibre" / "plugins" / "ebook_translator.json"
        if p.is_file():
            return p

    # 2. Linux / macOS standard config paths
    home = os.environ.get("HOME", "")
    if home:
        p_linux = pathlib.Path(home) / ".config" / "calibre" / "plugins" / "ebook_translator.json"
        if p_linux.is_file():
            return p_linux
        p_mac = pathlib.Path(home) / "Library" / "Preferences" / "calibre" / "plugins" / "ebook_translator.json"
        if p_mac.is_file():
            return p_mac

    return None


def get_bookfere_config_path() -> pathlib.Path:
    """Returns local bookfere_config.json path as primary source-of-truth."""
    if not LOCAL_CONFIG_PATH.is_file():
        # Auto-seed from Calibre if available, or create default
        calibre_cfg = get_calibre_config_path()
        if calibre_cfg and calibre_cfg.is_file():
            try:
                shutil.copy2(str(calibre_cfg), str(LOCAL_CONFIG_PATH))
            except Exception:
                pass
    return LOCAL_CONFIG_PATH


def import_from_calibre() -> Dict[str, Any]:
    """Imports settings from Calibre's ebook_translator.json to local config."""
    calibre_cfg = get_calibre_config_path()
    if not calibre_cfg or not calibre_cfg.is_file():
        return {"ok": False, "error": "Calibre ebook_translator.json not found on this system"}
    try:
        data = json.loads(calibre_cfg.read_text(encoding="utf-8"))
        LOCAL_CONFIG_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        return {"ok": True, "source": str(calibre_cfg), "settings": get_bookfere_settings()}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def sync_to_calibre() -> Dict[str, Any]:
    """Writes current local settings back to Calibre's ebook_translator.json."""
    calibre_cfg = get_calibre_config_path()
    if not calibre_cfg:
        return {"ok": False, "error": "Calibre plugin directory not found on this system"}
    try:
        if not LOCAL_CONFIG_PATH.is_file():
            return {"ok": False, "error": "Local bookfere_config.json not found"}
        data = json.loads(LOCAL_CONFIG_PATH.read_text(encoding="utf-8"))
        calibre_cfg.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        return {"ok": True, "destination": str(calibre_cfg)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def get_bookfere_settings() -> Dict[str, Any]:
    """Reads live settings from local bookfere_config.json or Calibre."""
    calibre_bin = get_calibre_debug()
    import shutil
    has_calibre = os.path.isfile(calibre_bin) or (shutil.which(calibre_bin) is not None)

    calibre_cfg = get_calibre_config_path()
    cfg_path = get_bookfere_config_path()

    builtin_list = [
        "SmartAuto",
        "Google(Free)New",
        "Google(Free)Html",
        "MicrosoftEdge(Free)",
        "DeepL(Free)",
        "Google(Free)",
        "Google(Basic)",
        "Gemini",
        "ChatGPT",
    ]

    res = {
        "ok": True,
        "calibre_installed": has_calibre,
        "calibre_path": calibre_bin,
        "calibre_config_path": str(calibre_cfg) if calibre_cfg else "",
        "local_config_path": str(cfg_path),
        "active_engine": "SmartAuto",
        "merge_enabled": True,
        "merge_length": 1800,
        "translation_position": "only",
        "translation_color": None,
        "original_color": None,
        "request_interval": 1.0,
        "concurrency_limit": 1,
        "request_timeout": 90.0,
        "request_attempt": 8,
        "source_lang": "Japanese",
        "target_lang": "English",
        "available_engines": list(builtin_list),
        "custom_engines": {},
        "engine_preferences": {},
        "active_engine_pref": {},
    }

    raw_data = {}
    if cfg_path and cfg_path.is_file():
        try:
            raw_data = json.loads(cfg_path.read_text(encoding="utf-8"))
        except Exception as e:
            res["read_error"] = str(e)
    elif calibre_cfg and calibre_cfg.is_file():
        try:
            raw_data = json.loads(calibre_cfg.read_text(encoding="utf-8"))
        except Exception:
            pass

    if raw_data:
        active_eng = raw_data.get("translate_engine", "Google(Free)New")
        res["active_engine"] = active_eng
        res["merge_enabled"] = raw_data.get("merge_enabled", True)
        res["merge_length"] = raw_data.get("merge_length", 1800)
        res["translation_position"] = raw_data.get("translation_position", "only")
        res["translation_color"] = raw_data.get("translation_color")
        res["original_color"] = raw_data.get("original_color")

        # Custom engines list
        custom_engs = raw_data.get("custom_engines", {})
        res["custom_engines"] = custom_engs
        for cname in custom_engs.keys():
            if cname not in res["available_engines"]:
                res["available_engines"].append(cname)

        # Engine preferences
        engine_prefs = raw_data.get("engine_preferences", {})
        res["engine_preferences"] = engine_prefs

        active_pref = engine_prefs.get(active_eng, {})
        res["active_engine_pref"] = active_pref
        res["request_interval"] = active_pref.get("request_interval", raw_data.get("request_interval", 1.0))
        res["concurrency_limit"] = active_pref.get("concurrency_limit", 1)
        res["request_timeout"] = active_pref.get("request_timeout", 90.0)
        res["request_attempt"] = active_pref.get("request_attempt", 8)
        res["model"] = active_pref.get("model", "")
        res["prompt"] = active_pref.get("prompt", "")
        # Mask API keys for safety
        api_keys = active_pref.get("api_keys", [])
        res["has_api_key"] = bool(api_keys)
        res["source_lang"] = active_pref.get("source_lang", "Japanese")
        res["target_lang"] = active_pref.get("target_lang", "Indonesian")

    return res


def save_bookfere_settings(updates: Dict[str, Any], sync_calibre: bool = False) -> Dict[str, Any]:
    """Updates bookfere_config.json and optionally syncs with Calibre."""
    cfg_path = get_bookfere_config_path()
    try:
        data = {}
        if cfg_path.is_file():
            data = json.loads(cfg_path.read_text(encoding="utf-8"))

        # Global keys
        if "translate_engine" in updates:
            data["translate_engine"] = updates["translate_engine"]
        if "merge_enabled" in updates:
            data["merge_enabled"] = bool(updates["merge_enabled"])
        if "merge_length" in updates:
            data["merge_length"] = int(updates["merge_length"])
        if "translation_position" in updates:
            data["translation_position"] = updates["translation_position"]
        if "translation_color" in updates:
            data["translation_color"] = updates["translation_color"] or None
        if "original_color" in updates:
            data["original_color"] = updates["original_color"] or None

        # Per-engine preferences
        active_eng = data.get("translate_engine", updates.get("translate_engine", "Google(Free)New"))
        if "engine_preferences" not in data:
            data["engine_preferences"] = {}
        if active_eng not in data["engine_preferences"]:
            data["engine_preferences"][active_eng] = {}

        pref = data["engine_preferences"][active_eng]
        if "request_interval" in updates:
            pref["request_interval"] = float(updates["request_interval"])
            data["request_interval"] = float(updates["request_interval"])
        if "concurrency_limit" in updates:
            pref["concurrency_limit"] = int(updates["concurrency_limit"])
        if "request_timeout" in updates:
            pref["request_timeout"] = float(updates["request_timeout"])
        if "request_attempt" in updates:
            pref["request_attempt"] = int(updates["request_attempt"])
        if "model" in updates:
            pref["model"] = str(updates["model"]).strip()
        if "prompt" in updates:
            pref["prompt"] = str(updates["prompt"]).strip()
        if updates.get("api_key"):
            pref["api_keys"] = [str(updates["api_key"]).strip()]

        cfg_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

        # Sync to Calibre if requested or if Calibre config exists
        calibre_synced = False
        calibre_cfg = get_calibre_config_path()
        if sync_calibre and calibre_cfg:
            try:
                calibre_cfg.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
                calibre_synced = True
            except Exception:
                pass

        return {"ok": True, "saved": updates, "calibre_synced": calibre_synced, "settings": get_bookfere_settings()}
    except Exception as e:
        return {"ok": False, "error": str(e)}


class BookfereTranslator:
    def __init__(
        self,
        target_lang: str = "en",
        delay_s: float = 0.5,
        engine_name: Optional[str] = None,
        merge_enabled: bool = True,
        merge_length: int = 1800,
        use_native: bool = True,
    ):
        self.target_lang_code = target_lang if target_lang in LANG_MAP else "en"
        self.target_lang_name = LANG_MAP.get(self.target_lang_code, "English")
        self.source_lang_name = "Japanese"
        self.delay_s = delay_s
        self.engine_name = engine_name
        self.merge_enabled = merge_enabled
        self.merge_length = merge_length
        self.use_native = use_native
        self.calibre_debug = get_calibre_debug()

    def translate_batch(
        self,
        items: List[Dict[str, str]],
        log_callback: Optional[Callable[[str], None]] = None,
    ) -> List[Dict[str, str]]:
        """items: [{"id": ..., "text": "..."}] -> [{"id": ..., "text": "..."}]"""
        if not items:
            return []

        # 1. Native High-Speed Pure-Python Engine (Mimicking Bookfere headless)
        if self.use_native:
            try:
                from . import engines
                eng = engines.get_engine(self.engine_name or "SmartAuto")
                res = eng.translate_batch(
                    items=items,
                    source_lang="ja",
                    target_lang=self.target_lang_code,
                    max_workers=4,
                )
                if res and any(r.get("text") for r in res):
                    return res
            except Exception as e:
                if log_callback:
                    log_callback(f"[Native Engine Warning] {e}, falling back to Calibre...")

        # 2. Fallback to Calibre Plugin Subprocess if native engine unavailable
        payload = {
            "items": items,
            "merge_enabled": self.merge_enabled,
            "merge_length": self.merge_length,
            "engine_name": self.engine_name,
        }

        cmd = [self.calibre_debug, "-c", CALIBRE_TRANSLATE_SCRIPT, self.source_lang_name, self.target_lang_name]
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        )
        stdout, stderr = proc.communicate(input=json.dumps(payload, ensure_ascii=False))

        # Forward any engine diagnostics / stderr to logger
        if stderr and log_callback:
            for sline in stderr.strip().splitlines():
                sline = sline.strip()
                if sline and not sline.startswith("DEBUG:"):
                    log_callback(f"[Bookfere] {sline}")

        if proc.returncode != 0:
            raise RuntimeError(f"Calibre translate error: {stderr.strip() or stdout.strip()}")

        # Clean stdout: find JSON line
        for line in stdout.splitlines():
            line = line.strip()
            if line.startswith("[") and line.endswith("]"):
                parsed = json.loads(line)
                if log_callback:
                    err_items = [it for it in parsed if it.get("error")]
                    if err_items:
                        log_callback(f"[Bookfere Warn] {len(err_items)} item(s) had translation fallback: {err_items[0].get('error')}")
                return parsed

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
    target_lang: str = "en",
    progress: Optional[Callable[[Dict], None]] = None,
    control: Optional[Any] = None,
) -> Dict:
    """Translates metadata and chapters using cache files with fast Bookfere chunk merging."""
    from . import config, crawler

    emit = progress or (lambda ev: None)
    cache_dir = config.CACHE_DIR / ncode.lower() / f"tl_{target_lang}"
    cache_dir.mkdir(parents=True, exist_ok=True)

    # Load live Bookfere settings
    bf_settings = get_bookfere_settings()
    merge_enabled = bf_settings.get("merge_enabled", True)
    merge_length = bf_settings.get("merge_length", 1800)
    engine_name = bf_settings.get("active_engine", "Google(Free)New")

    translator = BookfereTranslator(
        target_lang=target_lang,
        engine_name=engine_name,
        merge_enabled=merge_enabled,
        merge_length=merge_length,
        delay_s=0.5,
    )

    # 1. Translate metadata
    meta_cache_file = cache_dir / "meta.json"
    if meta_cache_file.exists():
        tl_meta = json.loads(meta_cache_file.read_text(encoding="utf-8"))
        emit({"type": "stage", "stage": "tl_meta", "message": "Using cached translated metadata"})
    else:
        emit({"type": "stage", "stage": "tl_meta", "message": f"Translating title, synopsis & tags to {target_lang.upper()} ({engine_name})..."})
        batch = [
            {"id": "title", "text": meta.get("title", "")},
            {"id": "story", "text": meta.get("story", "")},
        ]
        keywords = meta.get("keywords", [])
        for k_idx, kw in enumerate(keywords):
            batch.append({"id": f"kw_{k_idx}", "text": kw})

        res = translator.translate_batch(batch)
        lookup = {r["id"]: r.get("text", "") for r in res}
        tl_meta = dict(meta)
        tl_meta["original_title"] = meta.get("title", "")
        tl_meta["title"] = lookup.get("title") or meta.get("title", "")
        # Preserve original author name (names should not be machine translated)
        tl_meta["original_writer"] = meta.get("writer", "")
        tl_meta["writer"] = meta.get("writer", "")
        tl_meta["story"] = lookup.get("story") or meta.get("story", "")
        tl_meta["target_lang"] = target_lang
        tl_meta["keywords"] = [lookup.get(f"kw_{k_idx}") or kw for k_idx, kw in enumerate(keywords)]
        meta_cache_file.write_text(json.dumps(tl_meta, ensure_ascii=False, indent=2), encoding="utf-8")

    # 2. Translate chapters
    total = len(chapters)
    tl_chapters = []
    emit({"type": "stage", "stage": "tl_chapters", "message": f"Translating {total} chapters via Bookfere ({engine_name}, sat-set chunks)..."})

    for i, ch in enumerate(chapters, 1):
        if control:
            control.checkpoint()

        ep = ch.get("episode", i)
        ch_cache_file = cache_dir / f"{ep:05d}.json"

        if ch_cache_file.exists():
            try:
                tl_ch = json.loads(ch_cache_file.read_text(encoding="utf-8"))
                # Auto-repair: check if cache contains untranslated Japanese leftovers
                jp_regex = re.compile(r'[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff]')
                chk_lines = tl_ch.get("body") or tl_ch.get("lines") or []
                has_leak = any(jp_regex.search(l) for l in chk_lines if len(l.strip()) > 2)
                if not has_leak:
                    tl_chapters.append(tl_ch)
                    emit({"type": "progress_tl", "done": i, "total": total, "episode": ep, "title": tl_ch.get("title", ""), "cached": True})
                    continue
                else:
                    emit({"type": "stage", "stage": "tl_chunk", "message": f"Ch {ep}: fixing untranslated Japanese in cache..."})
            except Exception:
                pass

        # Prepare paragraphs to translate
        from . import glossary
        terms = glossary.glossary_manager.get_terms_for_novel(ncode)

        orig_title = ch.get("title", "")
        p_title, title_map = glossary.glossary_manager.protect_terms(orig_title, terms)

        # ISOLATE TITLE: Translate chapter title independently to avoid chunk merger misalignment
        raw_tl_title = translator.translate_single(p_title) if p_title.strip() else orig_title
        restored_title = glossary.glossary_manager.restore_terms(raw_tl_title, title_map)
        from . import qc
        final_chapter_title = qc.normalize_chapter_title(restored_title, ep)

        # Body and notes are packed into chunks
        batch = []
        lines = ch.get("body") or ch.get("lines") or []
        body_maps = {}
        for idx, line in enumerate(lines):
            p_line, line_map = glossary.glossary_manager.protect_terms(line, terms)
            body_maps[f"p_{idx}"] = line_map
            batch.append({"id": f"p_{idx}", "text": p_line})

        pref_maps = {}
        if ch.get("preface"):
            for pidx, pline in enumerate(ch["preface"]):
                p_pref, pref_map = glossary.glossary_manager.protect_terms(pline, terms)
                pref_maps[f"pref_{pidx}"] = pref_map
                batch.append({"id": f"pref_{pidx}", "text": p_pref})

        aft_maps = {}
        if ch.get("afterword"):
            for aidx, aline in enumerate(ch["afterword"]):
                p_aft, aft_map = glossary.glossary_manager.protect_terms(aline, terms)
                aft_maps[f"aft_{aidx}"] = aft_map
                batch.append({"id": f"aft_{aidx}", "text": p_aft})

        emit({"type": "stage", "stage": "tl_chunk", "message": f"Ch {ep}: translating {len(batch)} lines via Bookfere chunks..."})

        # Translate body & notes in chunk mode
        sub_res = translator.translate_batch(
            batch,
            log_callback=lambda msg: emit({"type": "stage", "stage": "tl_engine_log", "message": msg}),
        ) if batch else []
        res_map = {r["id"]: r.get("text", "") for r in sub_res}

        # Restore protected terms
        tl_ch = dict(ch)
        tl_ch["original_title"] = orig_title
        tl_ch["title"] = final_chapter_title

        translated_body = []
        for idx in range(len(lines)):
            raw_tl_line = res_map.get(f"p_{idx}", lines[idx])
            restored_line = glossary.glossary_manager.restore_terms(raw_tl_line, body_maps.get(f"p_{idx}", {}))
            translated_body.append(restored_line)

        tl_ch["body"] = translated_body
        tl_ch["lines"] = translated_body

        if ch.get("preface"):
            translated_pref = []
            for pidx in range(len(ch["preface"])):
                raw_pref = res_map.get(f"pref_{pidx}", ch["preface"][pidx])
                restored_pref = glossary.glossary_manager.restore_terms(raw_pref, pref_maps.get(f"pref_{pidx}", {}))
                translated_pref.append(restored_pref)
            tl_ch["preface"] = translated_pref

        if ch.get("afterword"):
            translated_aft = []
            for aidx in range(len(ch["afterword"])):
                raw_aft = res_map.get(f"aft_{aidx}", ch["afterword"][aidx])
                restored_aft = glossary.glossary_manager.restore_terms(raw_aft, aft_maps.get(f"aft_{aidx}", {}))
                translated_aft.append(restored_aft)
            tl_ch["afterword"] = translated_aft

        # Auto-repair check: check if any line in chapter still contains Japanese characters
        jp_regex = re.compile(r'[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff]')
        leak_indices = [idx for idx, line in enumerate(translated_body) if len(line.strip()) > 1 and jp_regex.search(line)]
        if leak_indices:
            emit({"type": "stage", "stage": "tl_chunk", "message": f"Ch {ep}: repairing {len(leak_indices)} leaked untranslated body lines..."})
            for l_idx in leak_indices:
                orig_txt = lines[l_idx].strip()
                if orig_txt:
                    try:
                        translated_body[l_idx] = translator.translate_single(orig_txt)
                    except Exception:
                        pass
            tl_ch["body"] = translated_body
            tl_ch["lines"] = translated_body

        # Auto-repair check for preface (catatan awal / notice)
        if ch.get("preface") and tl_ch.get("preface"):
            pref_leak_indices = [idx for idx, line in enumerate(tl_ch["preface"]) if len(line.strip()) > 1 and jp_regex.search(line)]
            if pref_leak_indices:
                emit({"type": "stage", "stage": "tl_chunk", "message": f"Ch {ep}: repairing {len(pref_leak_indices)} leaked preface lines..."})
                repaired_pref = list(tl_ch["preface"])
                for p_idx in pref_leak_indices:
                    orig_p = ch["preface"][p_idx].strip()
                    if orig_p:
                        try:
                            repaired_pref[p_idx] = translator.translate_single(orig_p)
                        except Exception:
                            pass
                tl_ch["preface"] = repaired_pref

        # Auto-repair check for afterword (catatan akhir / author note)
        if ch.get("afterword") and tl_ch.get("afterword"):
            aft_leak_indices = [idx for idx, line in enumerate(tl_ch["afterword"]) if len(line.strip()) > 1 and jp_regex.search(line)]
            if aft_leak_indices:
                emit({"type": "stage", "stage": "tl_chunk", "message": f"Ch {ep}: repairing {len(aft_leak_indices)} leaked afterword lines..."})
                repaired_aft = list(tl_ch["afterword"])
                for a_idx in aft_leak_indices:
                    orig_a = ch["afterword"][a_idx].strip()
                    if orig_a:
                        try:
                            repaired_aft[a_idx] = translator.translate_single(orig_a)
                        except Exception:
                            pass
                tl_ch["afterword"] = repaired_aft
        ch_cache_file.write_text(json.dumps(tl_ch, ensure_ascii=False), encoding="utf-8")
        tl_chapters.append(tl_ch)

        emit({"type": "progress_tl", "done": i, "total": total, "episode": ep, "title": tl_ch["title"], "cached": False})
        time.sleep(translator.delay_s)

    return {"meta": tl_meta, "chapters": tl_chapters}


def build_translated_epub(tl_meta: Dict, tl_chapters: List[Dict], out_dir: pathlib.Path, range_label: Optional[str] = None) -> str:
    """Builds EPUB with translated metadata, contents, and QC standardization.
    Supports range_label (e.g. 'Ch 001-050') for direct partial EPUB downloads.
    """
    from . import crawler

    lang_tag = tl_meta.get("target_lang", "id").upper()
    title = tl_meta.get("title", "novel")
    writer = tl_meta.get("writer", "Unknown")

    title_clean = crawler._safe_name(title, max_bytes=140)
    writer_clean = crawler._safe_name(writer, max_bytes=60)
    if range_label:
        filename = f"[{lang_tag}] {title_clean} ({range_label}) - {writer_clean}.epub"
    else:
        filename = f"[{lang_tag}] {title_clean} - {writer_clean}.epub"
    out_file = out_dir / filename

    # Set language tag in metadata
    meta_copy = dict(tl_meta)
    target_l = tl_meta.get("target_lang", "en").lower()
    meta_copy["language"] = target_l

    # Ensure translated keywords/tags are used for OPF dc:subject
    if "keywords" in tl_meta:
        meta_copy["keywords"] = tl_meta["keywords"]

    # Add display title to chapters
    view = []
    for ch in tl_chapters:
        c = dict(ch)
        if c.get("volume") and c["volume"] != tl_meta.get("title") and not c["title"].startswith(c["volume"]):
            c["title"] = f"{c['volume']} - {c['title']}"
        view.append(c)

    # Pass QC settings (clean blank spaces, standard 1-page synopsis)
    settings = crawler.load_settings()
    saved_path = crawler.write_epub(meta_copy, view, out_file, settings=settings)
    if pathlib.Path(saved_path).is_file():
        try:
            from . import qc
            qc.standardize_epub_file(saved_path)
        except Exception as e:
            logger.warning(f"QC pass failed on {saved_path}: {e}")
    return str(saved_path)
