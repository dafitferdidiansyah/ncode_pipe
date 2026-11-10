"""Pipeline orchestrator for ncode_pipe.
- `run_pipeline(keyword, exclude_genres, limit)`
  1. Search novels via `crawler.search_novels`.
  2. Take first result (or none).
  3. Download raw text using `crawler.download_novel` (produces .txt).
  4. "Translate" by copying to a new .txt prefixed with '[Translated]'.
     In production replace with Calibre Ebook‑Translator call.
  5. Upload the translated file to MyNovels backend using HTTP POST.
"""
import os, shutil, pathlib, json
import requests
from . import config, crawler


def _translate_placeholder(src_path: str, out_dir: str) -> str:
    """Very naive translation: copy file and prepend a notice.
    Returns path to translated file.
    """
    src = pathlib.Path(src_path)
    out_dir = pathlib.Path(out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    dst = out_dir / f"{src.stem}_translated.txt"
    with src.open('r', encoding='utf-8') as f_in, dst.open('w', encoding='utf-8') as f_out:
        f_out.write('[Translated placeholder]\n')
        shutil.copyfileobj(f_in, f_out)
    return str(dst)


def _upload_file(file_path: str, title: str) -> dict:
    """POST multipart to MyNovels backend.
    Expected endpoint: `{BASE}{UPLOAD_ENDPOINT}`.
    Returns JSON response (or raises).
    """
    url = config.BACKEND_URL.rstrip('/') + config.UPLOAD_ENDPOINT
    with open(file_path, 'rb') as fp:
        files = {'file': (os.path.basename(file_path), fp, 'application/octet-stream')}
        data = {'title': title}
        resp = requests.post(url, files=files, data=data, timeout=30)
    resp.raise_for_status()
    return resp.json()


def run_pipeline(keyword: str, exclude_genres=None, limit=10):
    """Execute full flow.
    Returns dict with keys: search_result, download_path, translated_path, upload_response.
    """
    if exclude_genres is None:
        exclude_genres = [101]
    # 1. Search
    results = crawler.search_novels(keyword, exclude_genres=exclude_genres, limit=limit)
    if not results:
        raise ValueError('No novels found for given query')
    novel = results[0]
    ncode = novel.get('ncode') or novel.get('ncode')
    title = novel.get('title') or f"{ncode}_novel"
    # 2. Download (txt placeholder)
    download_dir = pathlib.Path(__file__).parent / 'downloads'
    download_path = crawler.download_novel(ncode, str(download_dir))
    # 3. Translate placeholder
    translate_dir = pathlib.Path(__file__).parent / 'translated'
    translated_path = _translate_placeholder(download_path, str(translate_dir))
    # 4. Upload
    try:
        upload_resp = _upload_file(translated_path, title)
    except Exception as exc:
        upload_resp = {'error': str(exc), 'note': 'Backend unreachable'}
    return {
        'search_result': novel,
        'download_path': download_path,
        'translated_path': translated_path,
        'upload_response': upload_resp,
    }
