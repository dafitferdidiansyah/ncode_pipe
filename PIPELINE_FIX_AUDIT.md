# PIPELINE FIX & INTEGRATION MONITORING AUDIT
Dokumen rujukan standar industri operasional sistem pipeline novel (`ncode_pipe` -> `MyNovels` -> `WTR-Lab`).
Dibuat sebagai referensi persisten agar tidak hilang saat transisi session context.

---

## 1. Log Perbaikan yang Telah Diterapkan (Permanen)

### A. Bypass Error Ngrok Interstitial (`ERR_NGROK_6024`)
- **Masalah:** Saat tombol download EPUB diklik di browser via `<a href="...">`, Ngrok free-tier mencegat dengan halaman interstitial warning.
- **Root Cause:** Direct navigation HTTP request tidak membawa header custom bypass Ngrok.
- **Fix:**
  - File: `MyNovels/frontend/src/api.js`
  - Ditambahkan helper `downloadBookFile(id, title)`.
  - Mengunduh file via `fetch()` dengan header wajib `ngrok-skip-browser-warning: true`, kemudian dikonversi menjadi local `Blob URL` dan di-trigger download otomatis.
  - File terkait diupdate: `BookCard.jsx` dan `BookModal.jsx`.

### B. Standardisasi Sinopsis Tunggal Bahasa (QC Layer)
- **Masalah:** Halaman info EPUB menampilkan label ganda `"Synopsis / Sinopsis"` yang tidak konsisten.
- **Root Cause:** String hardcoded di template XML generator.
- **Fix:**
  - File: `ncode_pipe/qc.py` (`build_info_card_xhtml`)
  - Ditambahkan logika pemilihan dinamis berdasarkan `target_lang`:
    - `id` / `ind` / `indonesian` -> Menampilkan `"Sinopsis"`
    - Bahasa lainnya (default `en`) -> Menampilkan `"Synopsis"`

### C. Eliminasi Kebocoran Teks Jepang pada Catatan Penulis (Preface & Afterword)
- **Masalah:** Teks Jepang masih tersisa di kata pengantar (`preface`) dan catatan akhir (`afterword`) seperti ucapan terima kasih pembaca 150 chapter.
- **Root Cause:** Pemeriksaan regex karakter kanji/kana (`jp_regex`) dan fungsi `auto-repair` di `translator.py` sebelumnya hanya mengecek `translated_body`, sementara `preface` dan `afterword` tidak dicek ulang.
- **Fix:**
  - File: `ncode_pipe/translator.py` (`translate_novel_cached`)
  - Ditambahkan blok auto-repair spesifik untuk `tl_ch["preface"]` dan `tl_ch["afterword"]`.
  - Setiap baris yang terdeteksi masih mengandung karakter Jepang akan otomatis di-translate ulang secara individual via `translator.translate_single()`.

### D. Normalisasi Spasi Sinopsis & Pencegahan Terpotong (Synopsis Delimiter Leak)
- **Masalah:** Sinopsis novel pada hasil crawling terpotong pendek dan baris lanjutannya tumpah mengotori daftar tag/keywords.
- **Root Cause:**
  1. Penulis di Syosetu kerap menyisipkan 2-4 baris kosong beruntun (`\n\n\n\n`) di `#novel_ex`.
  2. Batch translator memecah elemen dengan delimiter `\n\n`. Baris kosong ekstra memicu false-split sehingga sisa sinopsis dianggap elemen batch berikutnya (tags).
- **Fix:**
  - File: `ncode_pipe/crawler.py` (`_normalize_novel` & `_parse_meta`)
    - Baris kosong multi-line dinormalisasi rapat ke satu pemisah paragraf standar via regex: `re.sub(r"\n\s*\n+", "\n\n", raw_story).strip()`.
  - File: `ncode_pipe/translator.py`
    - Sinopsis (`story`) dipisahkan dari batch keywords dan diterjemahkan terpisah via `translate_single()` agar formatting paragraf terjaga utuh 100%.

### E. Penutupan Kebocoran Domain Backend Ngrok pada Media Cover
- **Masalah:** Di DevTools Network/Sources browser muncul domain `playset-setting-supermom.ngrok-free.dev` di bawah folder `media/covers/`, membocorkan endpoint tunnel dan mem-bypass Cloudflare Edge Caching.
- **Root Cause:** Serializer Django `EpubBookListSerializer` dan `EpubBookDetailSerializer` menggunakan `request.build_absolute_uri()`. Frontend juga menerima absolute URL tersebut.
- **Fix:**
  - File: `MyNovels/backend/books/serializers.py`
    - `get_cover` diubah selalu mengembalikan path relatif: `/media/covers/...`.
  - File: `MyNovels/frontend/src/api.js`, `BookCard.jsx`, `BookModal.jsx`, `CoverImage.jsx`, `App.jsx`
    - Diterapkan `normalizeCoverUrl()` yang otomatis memangkas domain host lokal/ngrok menjadi path relatif `/media/...`.
    - Semua request media kini 100% dialirkan lewat Cloudflare Pages Functions (`/media/[[path]].js`) dengan Edge Cache 30 hari.

---

## 2. Status 10 Novel yang Sudah Terupload (Mitigasi Risiko)

Untuk memitigasi risiko beban komputasi dan tidak membebani konteks:
- **Keputusan:** 10 novel yang sudah ada di database `MyNovels` dibiarkan tetap aktif untuk saat ini.
- **Strategi Re-process:** Tidak perlu buru-buru re-crawl manual. Kapan pun dibutuhkan, jalankan script batch update dengan membersihkan cache episode terkait:
  ```bash
  # Pola clean cache per novel jika ingin men-generate ulang EPUB yang 100% bersih:
  python -m ncode_pipe.pipeline --ncode <NCODE> --force-qc
  ```

---

## 3. Checklist Monitoring Pipeline (Hulu ke Hilir)

| Tahap | Tool / Komponen | Titik Verifikasi Monitoring | Status |
|---|---|---|---|
| **1. Crawl** | `ncode_pipe.crawler` | Pastikan chapter count sesuai TOC Syosetu (pagination 100 ch/page). | OK |
| **2. Translate** | `ncode_pipe.translator` | Deteksi `jp_regex` di title, body, preface, afterword via auto-repair. | OK (Patched) |
| **3. QC & EPUB** | `ncode_pipe.qc` | Card metadata 1 halaman, sinopsis satu bahasa, margin rapi. | OK (Patched) |
| **4. Metadata & Sinopsis** | `ncode_pipe.crawler` + `translator` | Normalisasi regex spasi rapat (`\n\n`) & isolasi translasi sinopsis. | OK (Patched) |
| **5. Upload API** | `ncode_pipe.pipeline` | POST ke `/api/upload/` menghasilkan HTTP 200/201. | OK |
| **6. Media & Cover Security** | `Django Serializers` + `Cloudflare` | Relative path `/media/` + Cloudflare Edge Cache (Zero Ngrok leak). | OK (Patched) |
| **7. Download** | `MyNovels/frontend` | Unduhan via Fetch Blob + Header `ngrok-skip-browser-warning`. | OK (Patched) |
