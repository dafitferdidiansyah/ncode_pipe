"""Configuration constants for ncode_pipe.

- BACKEND_URL / UPLOAD_ENDPOINT: MyNovels Django API target.
- DEFAULT_SETTINGS: crawl settings (editable from the dashboard, persisted to settings.json).
- GENRE_MAP / TAG_GROUPS / SEARCH_FLAGS / NOVEL_TYPES / ORDERS: option lists for the search UI.
"""
import os
import pathlib

BASE_DIR = pathlib.Path(__file__).resolve().parent

BACKEND_URL = "https://playset-setting-supermom.ngrok-free.dev"  # adjust to actual ngrok URL
UPLOAD_ENDPOINT = "/api/books/"  # placeholder endpoint for POST multipart

# Narou API + site root
API_URL = "https://api.syosetu.com/novelapi/api/"
NOVEL_BASE_URL = "https://ncode.syosetu.com"

# Legacy crawl constants (kept for older imports; runtime uses DEFAULT_SETTINGS)
CHAPTER_DELAY = 1.5
MAX_RETRIES = 3
RETRY_DELAY = 5
REQUEST_TIMEOUT = 30
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

# Working folders
DOWNLOAD_DIR = BASE_DIR / "downloads"
CACHE_DIR = BASE_DIR / "cache"
SETTINGS_PATH = BASE_DIR / "settings.json"
STATUS_PATH = BASE_DIR / "status.json"

# Crawl settings (mirrors the Tampermonkey Syosetu downloader panel)
DEFAULT_SETTINGS = {
    "delay_ms": 1200,          # base delay between chapter requests (safe: 1200+)
    "jitter_ms": 300,          # random extra delay 0..jitter_ms
    "toc_delay_ms": 500,       # delay between TOC pages
    "max_retries": 3,          # attempts per request
    "cooldown_s": 12,          # 403/429/5xx cooldown = attempt * cooldown_s
    "timeout_s": 30,           # HTTP timeout
    "range_start": 1,          # first chapter (1-based)
    "range_end": 0,            # last chapter, 0 = until the end
    "formats": ["epub", "txt"],
    "target_lang": "en",       # Target translation language: 'en' (English) or 'id' (Indonesian)
    "auto_qc": True,           # Auto-run EPUB Quality Control (QC & Clean Layout)
    "auto_translate": True,    # Auto-translate after crawl completes
    "auto_upload": True,       # Auto-upload translated EPUB to MyNovels
    "mynovels_url": "https://playset-setting-supermom.ngrok-free.dev", # MyNovels Django backend base URL (or ngrok / Proxmox IP)
    "clean_blank_lines": True, # Quality Control: remove empty <p> & excessive blank line spaces
    "standardize_epub": True,  # Quality Control: 1-page synopsis, WTR Lab typography & layout
    "merge_chunks": True,      # Bookfere speed mode: batch/merge paragraphs into chunks (sat-set)
    "chunk_size": 1800,        # Default chunk threshold (chars) matching Bookfere standard (1800)
    "include_preface": True,
    "include_afterword": True,
    "resume": True,            # reuse cached chapters (CONTINUE LATEST behaviour)
    "skip_existing": False,    # skip ncode when output file already exists
    "novel_delay_s": 5,        # pause between novels in a bulk queue
    "custom_tags": [],         # user-added keyword chips for the search UI
    # DeepLX & Proxy Settings
    "deeplx_url": os.getenv("DEEPLX_URL", "http://127.0.0.1:1188/translate"),
    "translation_proxy": os.getenv("TRANSLATION_PROXY", ""),
    "prefer_deeplx": False,    # Default False: stable on GoogleFreeNew
}

# Narou genre codes (current system)
GENRE_MAP = {
    101: "異世界〔恋愛〕", 102: "現実世界〔恋愛〕",
    201: "ハイファンタジー", 202: "ローファンタジー",
    301: "純文学", 302: "ヒューマンドラマ", 303: "歴史", 304: "推理",
    305: "ホラー", 306: "アクション", 307: "コメディー",
    401: "VRゲーム", 402: "宇宙", 403: "空想科学", 404: "パニック",
    9901: "童話", 9902: "詩", 9903: "エッセイ", 9904: "リプレイ",
    9999: "その他", 9801: "ノンジャンル",
}

# Keyword chips grouped for the dashboard (click cycles: include -> exclude -> off)
TAG_GROUPS = {
    "Setting": ["異世界", "異世界転生", "異世界転移", "現代", "学園", "ダンジョン", "VRMMO", "ゲーム", "中世", "西洋", "和風", "ファンタジー"],
    "Protagonist": ["男主人公", "女主人公", "TS", "人外", "チート", "最強", "主人公最強", "勇者", "魔王", "悪役令嬢", "聖女", "転生"],
    "Plot": ["成り上がり", "復讐", "ざまぁ", "追放", "スローライフ", "内政", "冒険", "バトル", "戦記", "ミステリー", "ほのぼの", "シリアス"],
    "Romance": ["ハーレム", "ラブコメ", "純愛", "溺愛", "婚約破棄", "身分差", "ハッピーエンド", "ボーイズラブ", "ガールズラブ", "BL", "GL", "百合"],
    "Elements": ["魔法", "スキル", "ステータス", "レベル", "モンスター", "テイム", "奴隷", "料理", "錬金術", "精霊", "ドラゴン", "ギャグ"],
}
# Legacy flat list
COMMON_TAGS = [t for group in TAG_GROUPS.values() for t in group]

# Narou API boolean filters
SEARCH_FLAGS = {
    "isgl": "Only Girls Love (GL/百合)",
    "notgl": "Exclude Girls Love",
    "isbl": "Only Boys Love (BL)",
    "notbl": "Exclude Boys Love",
    "isr15": "Only R15",
    "notr15": "Exclude R15",
    "iszankoku": "Only 残酷な描写",
    "notzankoku": "Exclude 残酷な描写",
    "istensei": "Only 異世界転生",
    "istenni": "Only 異世界転移",
    "stop": "Exclude long hiatus",
}

NOVEL_TYPES = {
    "": "All types",
    "t": "Short story",
    "r": "Serial (ongoing)",
    "er": "Serial (complete)",
    "re": "Serial (all)",
    "ter": "Short + complete serial",
}

ORDERS = {
    "hyoka": "Total points",
    "favnovelcnt": "Bookmarks",
    "weekly": "Weekly points",
    "monthly": "Monthly points",
    "new": "Latest update",
    "old": "Oldest update",
    "lengthdesc": "Longest",
    "reviewcnt": "Reviews",
    "impressioncnt": "Comments",
    "ncodedesc": "Newest posted",
}

# Calibre binary locations (fallback to PATH)
CALIBRE_PATHS = [
    r"C:/Program Files/Calibre2/ebook-convert.exe",
    r"C:/Program Files (x86)/Calibre2/ebook-convert.exe",
]
