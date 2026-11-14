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
| **4. Upload API** | `ncode_pipe.pipeline` | POST ke `/api/upload/` menghasilkan HTTP 200/201. | OK |
| **5. Download** | `MyNovels/frontend` | Unduhan via Fetch Blob + Header `ngrok-skip-browser-warning`. | OK (Patched) |
