"""Standalone pure-Python translation engines mimicking Bookfere without Calibre dependency.

Engines included:
- Google (Free) - New: translate-pa.googleapis.com
- Google (Free) - HTML: translate-pa.googleapis.com/v1/translateHtml (preserves HTML tags)
- Microsoft Edge (Free): edge.microsoft.com/translate/translatetext
- Custom / OpenAI Compatible: For Local LLM Proxy, DeepSeek, Gemini, ChatGPT
"""

import base64
import concurrent.futures
import html
import json
import logging
import re
import time
from typing import Any, Dict, List, Optional, Tuple
import requests

logger = logging.getLogger("ncode_pipe.engines")

# Obfuscated API keys from Bookfere
_K1 = base64.b64decode(b"QUl6YVN5RExFZUZJNU90RkJ3WUJJb0tfamo1bTMyclpLNUNrQ1hB").decode("utf-8")
_K2 = base64.b64decode(b"QUl6YVN5QVRCWGFqdnpRTFRESEVRYmNwcTBJaGUwdldESG1PNTIw").decode("utf-8")

UA_CHROME = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36"
)

LANG_MAP = {
    "en": "en",
    "english": "en",
    "id": "id",
    "indonesian": "id",
    "indonesia": "id",
    "ja": "ja",
    "japanese": "ja",
    "zh": "zh-CN",
    "chinese": "zh-CN",
    "ko": "ko",
    "korean": "ko",
}


def normalize_lang(code: str) -> str:
    return LANG_MAP.get(code.lower().strip(), code.lower().strip())


class ChunkMerger:
    """Mimics Bookfere's ElementHandlerMerge (lib/element.py) for fast chunk batching and precise paragraph alignment."""

    @staticmethod
    def pack(
        items: List[Dict[str, str]],
        max_length: int = 1800,
        separator: str = "\n\n",
    ) -> Tuple[List[Dict[str, Any]], Dict[int, List[Dict[str, str]]]]:
        """Packs a list of {'id': ..., 'text': ...} items into merged text chunks under max_length."""
        chunks: List[Dict[str, Any]] = []
        chunk_map: Dict[int, List[Dict[str, str]]] = {}

        current_items: List[Dict[str, str]] = []
        current_text = ""
        chunk_id = 0

        for item in items:
            raw_text = item.get("text", "")
            if not raw_text or not raw_text.strip():
                # Keep empty items in current chunk if present, or pack as zero-length
                current_items.append(item)
                continue

            text_to_add = raw_text.strip()
            tentative = (current_text + separator + text_to_add) if current_text else text_to_add

            if len(tentative) <= max_length:
                current_text = tentative
                current_items.append(item)
            else:
                if current_text:
                    chunks.append({"chunk_id": chunk_id, "text": current_text})
                    chunk_map[chunk_id] = list(current_items)
                    chunk_id += 1
                current_text = text_to_add
                current_items = [item]

        if current_text or current_items:
            chunks.append({"chunk_id": chunk_id, "text": current_text})
            chunk_map[chunk_id] = list(current_items)

        return chunks, chunk_map

    @staticmethod
    def unpack(
        translated_chunks: Dict[int, str],
        chunk_map: Dict[int, List[Dict[str, str]]],
        separator: str = "\n\n",
    ) -> List[Dict[str, str]]:
        """De-merges translated chunks back to original item structure with alignment fallback."""
        results: List[Dict[str, str]] = []

        for chunk_id, original_items in sorted(chunk_map.items()):
            trans_text = translated_chunks.get(chunk_id, "").strip()
            num_expected = len(original_items)

            if num_expected == 0:
                continue

            if num_expected == 1:
                results.append({"id": original_items[0]["id"], "text": trans_text or original_items[0].get("text", "")})
                continue

            # Step 1: Split by standard double newline
            parts = [p.strip() for p in re.split(r"\n{2,}", trans_text) if p.strip()]

            # Step 2: Check if single newline matches or is closer to expected elements count
            if len(parts) != num_expected:
                single_parts = [p.strip() for p in trans_text.split("\n") if p.strip()]
                if len(single_parts) == num_expected:
                    parts = single_parts
                elif len(parts) == 1 and len(single_parts) > 1:
                    parts = single_parts
                elif abs(len(single_parts) - num_expected) <= abs(len(parts) - num_expected) and len(single_parts) > len(parts):
                    parts = single_parts

            # Step 3: Exact alignment matching
            if len(parts) == num_expected:
                for item, t in zip(original_items, parts):
                    results.append({"id": item["id"], "text": t})
            elif len(parts) > num_expected:
                # Excess parts: join trailing parts to the last element
                for i in range(num_expected - 1):
                    results.append({"id": original_items[i]["id"], "text": parts[i]})
                tail_joined = "\n\n".join(parts[num_expected - 1:])
                results.append({"id": original_items[-1]["id"], "text": tail_joined})
            else:
                # Fewer parts: delimiter loss! Fall back to translating remaining items directly
                num_parts = len(parts)
                for i in range(num_parts):
                    results.append({"id": original_items[i]["id"], "text": parts[i]})
                # Translate missing items individually so 0 lines are left untranslated
                for i in range(num_parts, num_expected):
                    missing_item = original_items[i]
                    txt = missing_item.get("text", "")
                    if not txt or not txt.strip():
                        results.append({"id": missing_item["id"], "text": ""})
                    else:
                        try:
                            direct_tl = self.translate(txt, source_lang=source_lang, target_lang=target_lang)
                            results.append({"id": missing_item["id"], "text": direct_tl})
                        except Exception:
                            results.append({"id": missing_item["id"], "text": txt})

        return results


class BaseEngine:
    name: str = "Base"

    def translate(self, text: str, source_lang: str = "ja", target_lang: str = "en") -> str:
        raise NotImplementedError

    def translate_batch(
        self,
        items: List[Dict[str, str]],
        source_lang: str = "ja",
        target_lang: str = "en",
        max_workers: int = 3,
        delay_s: float = 0.3,
        merge_chunks: bool = True,
        max_length: int = 1800,
    ) -> List[Dict[str, str]]:
        """Translate a batch of [{'id': ..., 'text': ...}] with high-speed chunk merging."""
        if not items:
            return []

        # If chunk merging enabled and multiple items, pack into chunks like Bookfere
        if merge_chunks and len(items) > 1:
            chunks, chunk_map = ChunkMerger.pack(items, max_length=max_length)
            translated_chunks: Dict[int, str] = {}

            def _translate_chunk(chk: Dict[str, Any]) -> Tuple[int, str]:
                cid = chk["chunk_id"]
                ctext = chk.get("text", "")
                if not ctext or not ctext.strip():
                    return cid, ""
                try:
                    res = self.translate(ctext, source_lang=source_lang, target_lang=target_lang)
                    return cid, res
                except Exception as e:
                    logger.warning(f"Engine {self.name} chunk {cid} error: {e}")
                    return cid, ctext

            with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = [executor.submit(_translate_chunk, chk) for chk in chunks]
                for f in concurrent.futures.as_completed(futures):
                    cid, cres = f.result()
                    translated_chunks[cid] = cres

            return ChunkMerger.unpack(translated_chunks, chunk_map)

        # Non-merged translation (1:1 item mapping)
        results: List[Dict[str, str]] = []

        def _do(item: Dict[str, str]) -> Dict[str, str]:
            txt = item.get("text", "")
            if not txt or not txt.strip():
                return {"id": item["id"], "text": txt}
            try:
                res = self.translate(txt, source_lang=source_lang, target_lang=target_lang)
                return {"id": item["id"], "text": res}
            except Exception as e:
                logger.warning(f"Engine {self.name} failed on item {item.get('id')}: {e}")
                return {"id": item["id"], "text": txt, "error": str(e)}

        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(_do, item) for item in items]
            for f in concurrent.futures.as_completed(futures):
                results.append(f.result())

        order = {it["id"]: idx for idx, it in enumerate(items)}
        results.sort(key=lambda r: order.get(r["id"], 0))
        return results


class GoogleFreeNew(BaseEngine):
    name = "Google(Free)New"

    def translate(self, text: str, source_lang: str = "ja", target_lang: str = "en") -> str:
        src = normalize_lang(source_lang)
        tgt = normalize_lang(target_lang)
        url = "https://translate-pa.googleapis.com/v1/translate"
        params = {
            "params.client": "gtx",
            "query.source_language": src,
            "query.target_language": tgt,
            "query.display_language": "en-US",
            "data_types": "TRANSLATION",
            "key": _K1,
            "query.text": text,
        }
        headers = {
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "User-Agent": UA_CHROME,
        }
        resp = requests.get(url, params=params, headers=headers, timeout=25)
        if resp.status_code == 200:
            return resp.json().get("translation", "")
        raise RuntimeError(f"Google(Free)New error HTTP {resp.status_code}: {resp.text[:200]}")


class GoogleFreeHtml(BaseEngine):
    name = "Google(Free)Html"

    def translate(self, text: str, source_lang: str = "ja", target_lang: str = "en") -> str:
        src = normalize_lang(source_lang)
        tgt = normalize_lang(target_lang)
        url = "https://translate-pa.googleapis.com/v1/translateHtml"
        headers = {
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "Content-Type": "application/json+protobuf",
            "X-Goog-Api-Key": _K2,
            "User-Agent": UA_CHROME,
        }
        payload = [[[text], src, tgt], "wt_lib"]
        resp = requests.post(url, data=json.dumps(payload), headers=headers, timeout=30)
        if resp.status_code == 200:
            res_data = resp.json()
            return res_data[0][0]
        raise RuntimeError(f"Google(Free)Html error HTTP {resp.status_code}: {resp.text[:200]}")


class MicrosoftEdgeFree(BaseEngine):
    name = "MicrosoftEdge(Free)"

    def translate(self, text: str, source_lang: str = "ja", target_lang: str = "en") -> str:
        src = normalize_lang(source_lang)
        tgt = normalize_lang(target_lang)
        url = f"https://edge.microsoft.com/translate/translatetext?isEnterpriseClient=False&to={tgt}&from={src}"
        headers = {
            "Content-Type": "application/json",
            "User-Agent": UA_CHROME,
        }
        payload = [text]
        resp = requests.post(url, data=json.dumps(payload), headers=headers, timeout=25)
        if resp.status_code == 200:
            res = resp.json()
            return res[0]["translations"][0]["text"]
        raise RuntimeError(f"MicrosoftEdge error HTTP {resp.status_code}: {resp.text[:200]}")


class CustomLLMEngine(BaseEngine):
    name = "CustomLLM"

    def __init__(self, endpoint: str, api_key: str = "", model: str = "", prompt: str = ""):
        self.endpoint = endpoint
        self.api_key = api_key
        self.model = model or "gpt-3.5-turbo"
        self.prompt = prompt or (
            "You are a professional novel translator. Translate the text from {source} to {target}. "
            "Preserve formatting and HTML tags. Output only translated text."
        )

    def translate(self, text: str, source_lang: str = "ja", target_lang: str = "en") -> str:
        src = normalize_lang(source_lang)
        tgt = normalize_lang(target_lang)
        system_msg = self.prompt.replace("{source}", src).replace("{target}", tgt)
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_msg},
                {"role": "user", "content": text},
            ],
            "temperature": 0.3,
            "stream": False,
        }
        resp = requests.post(self.endpoint, json=payload, headers=headers, timeout=90)
        if resp.status_code == 200:
            data = resp.json()
            return data["choices"][0]["message"]["content"].strip()
        raise RuntimeError(f"CustomLLM error HTTP {resp.status_code}: {resp.text[:200]}")


class SmartAutoEngine(BaseEngine):
    """Auto-cascading engine: tries Google(Free)New -> Google(Free)Html -> MicrosoftEdge(Free)."""
    name = "SmartAuto"

    def __init__(self):
        self.google_new = GoogleFreeNew()
        self.google_html = GoogleFreeHtml()
        self.edge = MicrosoftEdgeFree()

    def translate(self, text: str, source_lang: str = "ja", target_lang: str = "en") -> str:
        # If text contains HTML tags (<p>, <ruby>, <span>), prefer GoogleFreeHtml
        has_tags = bool(re.search(r"<[a-zA-Z/][^>]*>", text))
        primary = self.google_html if has_tags else self.google_new
        secondary = self.google_new if has_tags else self.google_html

        for eng in (primary, secondary, self.edge):
            try:
                res = eng.translate(text, source_lang=source_lang, target_lang=target_lang)
                if res and res.strip():
                    return res
            except Exception as e:
                logger.info(f"Engine {eng.name} failed ({e}), falling back to next engine...")
                time.sleep(0.3)
                continue

        # Final attempt Edge
        return self.edge.translate(text, source_lang=source_lang, target_lang=target_lang)


def get_engine(engine_name: str = "SmartAuto", custom_config: Optional[Dict] = None) -> BaseEngine:
    name_clean = engine_name.strip()
    if name_clean in ("Google(Free)New", "Google (Free) - New"):
        return GoogleFreeNew()
    if name_clean in ("Google(Free)Html", "Google (Free) - HTML"):
        return GoogleFreeHtml()
    if name_clean in ("MicrosoftEdge(Free)", "Microsoft Edge (Free)", "edge"):
        return MicrosoftEdgeFree()
    if custom_config and "url" in custom_config:
        return CustomLLMEngine(
            endpoint=custom_config["url"],
            api_key=custom_config.get("api_key", ""),
            model=custom_config.get("model", ""),
            prompt=custom_config.get("prompt", ""),
        )
    return SmartAutoEngine()
