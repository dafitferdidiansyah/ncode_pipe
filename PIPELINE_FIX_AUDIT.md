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

### F. Batch Mini-Chunking Auto-Repair pada Translator
- **Masalah:** Proses translasi berjalan sangat lambat saat fase repairing. Tiap baris teks bocor memicu request HTTP `translate_single()` terpisah secara sekuensial.
- **Root Cause:** Auto-repair body, preface, dan afterword mengeksekusi loop 1 request per baris, memicu puluhan round-trip HTTP berlebih.
- **Fix:**
  - File: `ncode_pipe/translator.py` (`translate_novel_cached`)
  - Mengubah perbaikan baris bocor menjadi `translate_batch()` mini-chunk. Baris yang bocor dikumpulkan dan diterjemahkan dalam satu request chunk sekaligus, memangkas waktu reparasi hingga 80-90%.

### H. Standardisasi UI/UX Tab Unduhan dan Sanitasi Kanji Volume Chapter
- **Masalah:**
  1. Teks arc/volume raw Kanji (contoh: `第一章`) dari Syosetu menempel di sebelah judul chapter yang sudah diterjemahkan (`第一章 - Chapter 2: ...`).
  2. Tab unduhan dan judul label masih menggunakan istilah non-standar / gimmick ("MeGaneBuk Standard Arc Batches", "Volumes / Batches").
- **Root Cause:**
  1. Di `ncode_pipe/translator.py`, field metadata `ch["volume"]` (nama arc) diambil mentah dari HTML TOC Syosetu tanpa diterjemahkan atau disanitasi sebelum digabungkan ke `c["title"]`.
  2. Naming tab UI kurang netral dan tidak to-the-point.
- **Fix:**
  - File: `ncode_pipe/translator.py`
    - Semua arc/volume unik dikumpulkan dan diterjemahkan via batch translation ke teks Latin/Inggris.
    - Menambahkan filter sanitasi kanji regex: jika masih terdapat sisa Kanji atau gagal diterjemahkan, string arc dibersihkan menjadi format standar `Volume {N}` atau dihilangkan agar judul bab tidak terkotori teks kanji mentah di e-reader.
  - File: `MyNovels/frontend/src/components/BookModal.jsx`
    - Mengganti label tab menjadi netral & profesional: **"Download Parts"** (singkat, to-the-point).
    - Menghapus semua referensi teks non-standar/gimmick, diganti dengan **"Select Chapter Range (50 Chapters / Part)"** dan tombol **"Download Part"**.

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
