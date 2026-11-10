"""Utility to search, download, convert, and upload novels from Syosetu.

Workflow:
1. Search novels via the public Syosetu API (see ``pipeline.search_novels``).
2. Download the novel as a plain ``.txt`` file (``pipeline.download_novel``).
3. Convert the ``.txt`` to ``.epub`` using Calibre's ``ebook-convert`` CLI.
   * The conversion step is a placeholder for a *translation* step –
     Calibre itself does not translate, but you can plug in any external
     translation tool before conversion if desired.
4. Upload the resulting ``.epub`` to the MyNovels backend via a multipart
   POST request (``config.BACKEND_URL`` + ``config.UPLOAD_ENDPOINT``).

The script can be used as a library or executed directly via a simple CLI:

    python -m ncode_pipe.translate_and_upload \
        --keyword "凍心の竜" \
        --exclude-genres 5 \
        --target-lang en \
        --out-dir "C:/temp/ncode"

The CLI will pick the first search result, process it, and report the
uploaded URL (or error).  For bulk processing, feed a CSV/JSON file with
``ncode`` values to the ``--bulk-file`` option.
"""

import argparse
import os
import pathlib
import subprocess
import sys
from typing import List, Optional

# Local imports – the pipeline provides ``search_novels`` and ``download_novel``
from .pipeline import search_novels, download_novel
from .config import BACKEND_URL, UPLOAD_ENDPOINT

# ``requests`` is used for the HTTP upload. It is a lightweight dependency and
# should already be available in the environment.
import requests

# ---------------------------------------------------------------------------
# Helper: locate Calibre's ``ebook-convert`` binary.
# ---------------------------------------------------------------------------
def _find_ebook_convert() -> str:
    """Return an absolute path to ``ebook-convert``.

    Calibre installs the binary in ``C:/Program Files/Calibre2`` on Windows.
    If it is not found there we fall back to the command name – assuming the
    executable is on ``PATH``.
    """
    possible_paths = [
        r"C:/Program Files/Calibre2/ebook-convert.exe",
        r"C:/Program Files (x86)/Calibre2/ebook-convert.exe",
    ]
    for p in possible_paths:
        if os.path.isfile(p):
            return p
    # Fallback – rely on PATH
    return "ebook-convert"

# ---------------------------------------------------------------------------
# Core processing function.
# ---------------------------------------------------------------------------
def translate_and_upload(
    ncode: str,
    out_dir: str,
    target_lang: str = "en",
) -> str:
    """Download a novel, convert to EPUB, and upload.

    Parameters
    ----------
    ncode: str
        The Syosetu novel code (e.g. ``n5005ev``).
    out_dir: str
        Directory where temporary files will be stored.
    target_lang: str, optional
        Target language code for translation – currently a placeholder.

    Returns
    -------
    str
        The URL returned by the backend API on success.
    """
    out_path = pathlib.Path(out_dir).expanduser().resolve()
    out_path.mkdir(parents=True, exist_ok=True)

    # 1. Download the raw text file.
    txt_path = download_novel(ncode, str(out_path))

    # 2. Convert to EPUB using Calibre.
    epub_path = out_path / f"{ncode}.epub"
    ebook_convert = _find_ebook_convert()
    # ``ebook-convert`` arguments – we keep it simple. ``--language`` sets the
    # language metadata; any actual translation must be performed before this
    # step (e.g. by editing ``txt_path``).
    cmd = [
        ebook_convert,
        txt_path,
        str(epub_path),
        f"--language={target_lang}",
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True)
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Calibre conversion failed: {e.stderr.decode()}") from e

    # 3. Upload the EPUB to the MyNovels backend.
    upload_url = f"{BACKEND_URL.rstrip('/')}{UPLOAD_ENDPOINT}"
    with open(epub_path, "rb") as f:
        files = {"file": (epub_path.name, f, "application/epub+zip")}
        # Additional metadata can be sent via ``data`` if the API expects it.
        response = requests.post(upload_url, files=files)
    if not response.ok:
        raise RuntimeError(f"Upload failed [{response.status_code}]: {response.text}")
    # Assume the API returns JSON with a ``url`` field.
    try:
        json_resp = response.json()
        return json_resp.get("url", "<no url returned>")
    except Exception:
        return response.text

# ---------------------------------------------------------------------------
# CLI entry point.
# ---------------------------------------------------------------------------
def _cli() -> None:
    parser = argparse.ArgumentParser(
        description="Search, download, convert, and upload Syosetu novels."
    )
    subparsers = parser.add_subparsers(dest="cmd", required=True)

    # ---- search sub‑command -------------------------------------------------
    search_parser = subparsers.add_parser("search", help="Search novels")
    search_parser.add_argument("keyword", help="Keyword to search for")
    search_parser.add_argument(
        "--exclude-genres",
        type=int,
        nargs="*",
        help="Genre IDs to exclude (e.g. 5 for Girls Love)",
    )
    search_parser.add_argument(
        "--limit", type=int, default=20, help="Maximum number of results"
    )

    # ---- process sub‑command (single ncode) --------------------------------
    proc_parser = subparsers.add_parser(
        "process", help="Process a single ncode (download → convert → upload)"
    )
    proc_parser.add_argument("ncode", help="Novel code, e.g. n5005ev")
    proc_parser.add_argument(
        "--out-dir",
        default="./ncode_output",
        help="Directory for intermediate files",
    )
    proc_parser.add_argument(
        "--target-lang",
        default="en",
        help="Target language code for EPUB metadata",
    )

    # ---- bulk sub‑command ---------------------------------------------------
    bulk_parser = subparsers.add_parser(
        "bulk", help="Process multiple ncodes from a file"
    )
    bulk_parser.add_argument(
        "bulk_file",
        help="Path to a text file containing one ncode per line",
    )
    bulk_parser.add_argument(
        "--out-dir",
        default="./ncode_bulk_output",
        help="Directory for intermediate files",
    )
    bulk_parser.add_argument(
        "--target-lang",
        default="en",
        help="Target language code for EPUB metadata",
    )

    args = parser.parse_args()

    if args.cmd == "search":
        results = search_novels(
            args.keyword, exclude_genres=args.exclude_genres, limit=args.limit
        )
        if not results:
            print("No results found.")
            return
        # Simple table output – three‑level tagging could be added later.
        for i, novel in enumerate(results, 1):
            print(
                f"{i}. {novel.get('title')} [{novel.get('ncode')}] –"
                f" {novel.get('writer')}"
            )
        return

    if args.cmd == "process":
        url = translate_and_upload(
            args.ncode, out_dir=args.out_dir, target_lang=args.target_lang
        )
        print(f"Uploaded EPUB URL: {url}")
        return

    if args.cmd == "bulk":
        bulk_path = pathlib.Path(args.bulk_file).expanduser().resolve()
        if not bulk_path.is_file():
            print(f"Bulk file not found: {bulk_path}", file=sys.stderr)
            sys.exit(1)
        with open(bulk_path, "r", encoding="utf-8") as f:
            ncodes = [line.strip() for line in f if line.strip()]
        for n in ncodes:
            try:
                url = translate_and_upload(
                    n, out_dir=args.out_dir, target_lang=args.target_lang
                )
                print(f"{n}: {url}")
            except Exception as exc:
                print(f"{n}: error – {exc}", file=sys.stderr)
        return

if __name__ == "__main__":
    _cli()
