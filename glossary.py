import os
import json
import re
from pathlib import Path
from typing import Dict, List, Tuple

BASE_DIR = Path(__file__).resolve().parent
GLOBAL_GLOSSARY_PATH = BASE_DIR / "glossary_global.json"

class GlossaryManager:
    """
    Manages global and per-novel translation glossaries and term protection.
    Replaces terms before MT with protected tokens and restores them after MT.
    """
    def __init__(self):
        self.global_terms: Dict[str, str] = self._load_json(GLOBAL_GLOSSARY_PATH)

    def _load_json(self, path: Path) -> Dict[str, str]:
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                return {}
        return {}

    def get_novel_glossary_path(self, ncode: str) -> Path:
        return BASE_DIR / "cache" / ncode.lower() / "glossary.json"

    def get_terms_for_novel(self, ncode: str) -> Dict[str, str]:
        combined = dict(self.global_terms)
        novel_path = self.get_novel_glossary_path(ncode)
        novel_terms = self._load_json(novel_path)
        combined.update(novel_terms)

        # Attempt to sync from Django backend API if reachable
        try:
            import urllib.request
            api_url = f"http://127.0.0.1:8000/api/glossary/?ncode={ncode}"
            req = urllib.request.Request(api_url, headers={"User-Agent": "ncode_pipe"})
            with urllib.request.urlopen(req, timeout=1.5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if data.get("terms"):
                    combined.update(data["terms"])
        except Exception:
            pass

        return combined

    def save_novel_terms(self, ncode: str, terms: Dict[str, str]):
        novel_path = self.get_novel_glossary_path(ncode)
        novel_path.parent.mkdir(parents=True, exist_ok=True)
        novel_path.write_text(json.dumps(terms, indent=2, ensure_ascii=False), encoding="utf-8")

    def protect_terms(self, text: str, terms: Dict[str, str]) -> Tuple[str, Dict[str, str]]:
        """
        Replaces raw terms in Japanese text with protected tags like [[T001]].
        Returns (protected_text, tag_to_target_mapping).
        """
        if not terms:
            return text, {}

        # Sort terms by length descending to match longest phrases first
        sorted_keys = sorted(terms.keys(), key=len, reverse=True)
        tag_map = {}
        protected = text

        for idx, raw_key in enumerate(sorted_keys):
            if raw_key in protected:
                tag = f"[[T{idx:03d}]]"
                protected = protected.replace(raw_key, tag)
                tag_map[tag] = terms[raw_key]

        return protected, tag_map

    def restore_terms(self, translated_text: str, tag_map: Dict[str, str]) -> str:
        """
        Restores protected tags like [[T001]] to their desired translations.
        Also cleans any extra spaces Google Translate might insert around brackets.
        """
        if not tag_map:
            return translated_text

        result = translated_text
        for tag, target_val in tag_map.items():
            # Handle possible variations: [[T001]], [[ T001 ]], [T001]
            raw_tag_inner = tag.strip("[]")
            pattern = re.compile(rf"\[+\s*{re.escape(raw_tag_inner)}\s*\]+", re.IGNORECASE)
            result = pattern.sub(target_val, result)

        return result

glossary_manager = GlossaryManager()
