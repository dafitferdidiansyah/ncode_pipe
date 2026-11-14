# Industry Standard Multi-Agent Architecture for Novel Pipeline (Agentic AI)

Dokumen ini mendefinisikan arsitektur dan pembagian peran sub-agent (Agentic AI) dalam ekosistem `ncode_pipe` dan `MyNovels` sesuai standar industri otomatisasi pemrosesan teks.

---

## 1. Motivasi & Prinsip Desain
- **Isolation of Concerns**: Setiap tugas spesifik dijalankan oleh agen terisolasi (Scraping, Translasi, Quality Control, Deployment).
- **Graceful Degradation (Circuit Breaking)**: Kegagalan pada engine translasi tertentu (misal DeepL rate-limited 429) tidak boleh menghentikan pipeline, melainkan transparan beralih ke engine cadangan.
- **Strict Quality Gates**: Output artefak (EPUB) ditolak jika mengandung teks bahasa sumber yang belum diterjemahkan.

---

## 2. Definisi Peran Sub-Agent

```
   ┌───────────────────────────────────────────────┐
   │            Agent 1: Sentinel Crawler          │
   │  - Narou API & Multi-page TOC Verification    │
   │  - Incremental Chapter Scraping               │
   └──────────────────────┬────────────────────────┘
                          │ (Raw JP JSON/HTML)
                          ▼
   ┌───────────────────────────────────────────────┐
   │            Agent 2: Translation Worker        │
   │  - Safe Sentence Chunking (<= 1400 chars)     │
   │  - Circuit Breaker: DeepLX -> Google -> Edge  │
   │  - Exponential Backoff & Proxy Rotation       │
   └──────────────────────┬────────────────────────┘
                          │ (Translated JSON)
                          ▼
   ┌───────────────────────────────────────────────┐
   │            Agent 3: Quality Gate (QC)         │
   │  - Japanese Character Residual Detection (<1%)│
   │  - Layout Standardization & Typography        │
   │  - Title & Synopsis Integrity                 │
   └──────────────────────┬────────────────────────┘
                          │ (Validated Clean Book)
                          ▼
   ┌───────────────────────────────────────────────┐
   │            Agent 4: Publisher & Deployer      │
   │  - EPUB3 Packaging (Dublin Core Metadata)     │
   │  - MyNovels API Ingestion & Cache Sync        │
   └───────────────────────────────────────────────┘
```

### Agent 1: Sentinel Crawler Agent
- **Tanggung Jawab**:
  - Mengambil data metadata dan bab novel dari Syosetu (`api.syosetu.com` dan `ncode.syosetu.com`).
  - Menjaga rate limit (delay + jitter) agar IP tidak terblokir.
  - Memastikan seluruh bab pada daftar isi (TOC) terunduh secara lengkap.
- **Standar Output**: File cache terstruktur di `cache/<ncode>/toc.json` dan `cache/<ncode>/ch_<id>.json`.

### Agent 2: Translation Worker Agent
- **Tanggung Jawab**:
  - Menerjemahkan bab per bab menggunakan engine `SmartAuto`.
  - Menerapkan pemotongan chunk paragraf maksimal 1400 karakter.
  - Mengaktifkan exponential backoff pada status HTTP 429/5xx.
  - Menggunakan DeepLX jika diaktifkan, dan otomatis fallback ke GoogleFreeNew / MicrosoftEdgeFree jika terjadi error.
- **Standar Output**: File bab terjemahan di `cache/<ncode>/translated_<lang>/`.

### Agent 3: Quality Gate (QC) Agent
- **Tanggung Jawab**:
  - Melakukan scanning regex terhadap seluruh teks bab untuk memastikan tidak ada karakter Hiragana/Katakana/Kanji yang tertinggal pada narasi utama.
  - Memeriksa kelengkapan elemen `preface`, `body`, dan `afterword`.
  - Menerapkan auto-repair loop jika ditemukan kebocoran bahasa sumber.
- **Standar Output**: Status `PASSED` atau `RETRY_REQUIRED`.

### Agent 4: Publisher & Deployer Agent
- **Tanggung Jawab**:
  - Mengompilasi bab yang lolos QC menjadi berkas standar EPUB3.
  - Mengirim berkas ke REST API backend MyNovels (`/api/books/` atau `/api/upload/`).
  - Menangani bypass proteksi jaringan (seperti header `ngrok-skip-browser-warning`).
