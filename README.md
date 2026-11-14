# ncode_pipe

A lightweight, automated web crawler, translation pipeline, and monitoring dashboard for archiving and reading web novels from [Syosetu (Shousetsuka ni Narou)](https://syosetu.com/).

Designed for local desktop workflows and remote server deployment (e.g. Proxmox LXC/VM).

---

## Key Features

1. **Hybrid Narou Engine & Search Filtering**:
   - Official **Narou Public API** (`api.syosetu.com`) integration for instantaneous metadata resolution, keyword queries, and bulk ncode lookups.
   - Comprehensive romance & genre filtering: supports `isbl` (Boys Love), `isgl` (Girls Love / Yuri), `notbl`, `notgl`, `isr15`, and `iszankoku`.
   - Multi-keyword query engine with `ALL (AND)` and `ANY (OR)` ranked modes.

2. **Robust Multi-Page Crawler (Inspired by lightnovel-crawler)**:
   - Automatic multi-page Table of Contents (TOC) scraping (up to 100 chapters per page) cross-referenced with API publication statistics (`general_all_no`).
   - Clean, structured chapter serialization & filesystem organization mirroring standards from [lncrawl/lightnovel-crawler](https://github.com/lncrawl/lightnovel-crawler).
   - Polite crawling rate limiter: configurable base delay (`delay_ms`), jitter (`jitter_ms`), and exponential backoff on HTTP 429/5xx.
   - Resumable file caching in `cache/<ncode>/` prevents duplicate requests.

3. **Retro Minimalist Web Dashboard**:
   - Single-page interface (runs on port `5005`) with live queue monitoring, job pause/resume/cancel controls, and streaming activity logs.
   - Optimized incremental DOM updates with `translate="no"` protection to prevent browser translation flicker.

4. **Zero-Token Ebook Translation (JP → ID & JP → EN)**:
   - Headless background integration with Calibre's **Bookfere Ebook Translator** engine (`GoogleFreeTranslateNew`).
   - Translates novel titles, synopses, and all chapters into **Indonesian (`id`)** or **English (`en`)** without consuming paid AI API tokens.
   - Circuit-breaker architecture with auto-repair loops, sentence chunk boundaries, and exponential backoff.
   - Outputs valid EPUB3 books with original Japanese titles preserved in Dublin Core metadata (`original_title`).

5. **MyNovels Remote Integration**:
   - One-click or automated dispatch pushing compiled EPUBs to [MyNovels](https://github.com/dafitferdidiansyah/MyNovels) (`/api/upload/`).

---

## Acknowledgements & Inspirations

Proyek ini menghargai kontribusi open-source dan dibangun atas etika atribusi komunitas software:

- **Ebook-Translator (Bookfere)** ([bookfere/Ebook-Translator-Calibre-Plugin](https://github.com/bookfere/Ebook-Translator-Calibre-Plugin)): Referensi utama logika translasi headless Calibre, mekanisme chunking paragraf, dan koneksi engine Google Free tanpa token API.
- **Lightnovel-Crawler (lncrawl)** ([lncrawl/lightnovel-crawler](https://github.com/lncrawl/lightnovel-crawler)): Inspirasi utama dalam arsitektur penataan crawler novel, parsing metadata chapter yang rapi, dan sistem caching bertahap.
- **DeepLX / DLX** ([OwO-Network/DLX](https://github.com/OwO-Network/DLX)): Referensi teknik bypass terjemahan DeepL bebas API key untuk kualitas terjemahan tingkat lanjut.
- **Lara Novelku UI Layout** ([taufiq-azr/lara-novelku](https://github.com/taufiq-azr/lara-novelku) by [@taufiq-azr](https://github.com/taufiq-azr)): Inspirasi dan referensi estetika card layout antarmuka pembaca/katalog novel.

> *Catatan Etika & Disclaimer*: Seluruh referensi, plugin pihak ketiga, dan desain UI yang diadopsi tetap menjadi hak cipta masing-masing pemilik aslinya. Proyek ini tidak berafiliasi resmi dengan Bookfere, DeepL SE, maupun penyedia sumber web novel terkait, dan semata-mata dikembangkan untuk studi teknis, otomatisasi arsip pribadi, serta edukasi.

---

## Directory Structure

```text
ncode_pipe/
├── config.py              # Crawler settings, search flags, and tag definitions
├── crawler.py             # Syosetu API client, HTML scraper, and EPUB3 compiler
├── translator.py          # Calibre Bookfere Google Free translation pipeline
├── uploader.py            # MyNovels REST API client
├── jobs.py                # Job queue state machine and sequenced logging
├── monitor.py             # Flask dashboard web server and JSON endpoints
├── static/
│   └── index.html         # Dashboard frontend UI
├── requirements.txt       # Dependencies
├── run_dashboard.bat      # Windows dashboard launcher
└── README.md
```

---

## Requirements

- Python 3.10+ (or [uv](https://github.com/astral-sh/uv))
- [Calibre](https://calibre-ebook.com/) with **Ebook Translator** (Bookfere) plugin installed (for translation pipeline)

### Installation

```bash
uv pip install -r requirements.txt
# or
pip install -r requirements.txt
```

---

## Usage

### 1. Launch Dashboard

**Windows:**
Double-click `run_dashboard.bat` or run:
```bash
python -m ncode_pipe.monitor --host 0.0.0.0 --port 5005
```
Open `http://127.0.0.1:5005` in your browser.

### 2. Crawl & Download

- **Search Tab**: Search novels with tag chips and search flags (`isbl`, `isgl`). Click **Queue** to enqueue.
- **Direct Tab**: Paste multiple ncodes (e.g. `n1932ia`, `n9669bk`) to fetch metadata and bulk queue.

### 3. Translate & Upload

- In the **Queue** table:
  - Click **TL (ID)** to translate to Indonesian.
  - Click **TL (EN)** to translate to English.
  - Click **PUSH** to upload to your MyNovels server.
- Turn on `Auto-translate` and `Auto-upload` in **Settings** for hands-free automation.

---

## License

MIT License.
