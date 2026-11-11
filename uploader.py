"""Uploader to push translated EPUBs to MyNovels backend.
Supports local development and remote Proxmox / ngrok backend endpoints.
"""

import os
import pathlib
import requests
from typing import Dict, Optional

from . import config


def upload_to_mynovels(
    epub_path: str,
    title: str,
    original_title: str = "",
    language: str = "",
    backend_url: Optional[str] = None,
    upload_endpoint: Optional[str] = None,
    timeout: int = 60,
) -> Dict:
    """POST an EPUB file to MyNovels /api/upload/ endpoint.
    backend_url: default config.BACKEND_URL
    upload_endpoint: default '/api/upload/'
    """
    path = pathlib.Path(epub_path)
    if not path.is_file():
        raise FileNotFoundError(f"EPUB file not found: {epub_path}")

    base_url = (backend_url or config.BACKEND_URL).rstrip("/")
    endpoint = upload_endpoint or "/api/upload/"
    if not endpoint.startswith("/"):
        endpoint = f"/{endpoint}"
    if not endpoint.endswith("/"):
        endpoint = f"{endpoint}/"
    url = f"{base_url}{endpoint}"

    headers = {
        "User-Agent": "ncode_pipe/1.0",
        "ngrok-skip-browser-warning": "true",
    }

    filename = path.name
    with open(path, "rb") as fp:
        files = {"epub_file": (filename, fp, "application/epub+zip")}
        data = {
            "title": title,
            "original_title": original_title,
        }
        if language:
            data["language"] = language
        resp = requests.post(url, files=files, data=data, headers=headers, timeout=timeout)

    if resp.status_code not in (200, 201):
        raise RuntimeError(f"Upload failed [{resp.status_code}]: {resp.text}")

    try:
        return resp.json()
    except Exception:
        return {"ok": True, "raw": resp.text}
