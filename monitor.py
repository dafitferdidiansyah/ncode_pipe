"""ncode_pipe dashboard (Flask).

Run:  python -m ncode_pipe.monitor   ->  http://127.0.0.1:5005

API
  GET  /api/options            genres, tag groups, flags, types, orders, settings
  POST /api/search             advanced multi-keyword search (see crawler.search)
  POST /api/ncodes/parse       {"text"} or multipart "file" (.txt) -> ncodes + metadata
  POST /api/jobs               {"items": [{"ncode","title"?}], "overrides"?: {...}}
  GET  /api/jobs               queue snapshot with live progress
  POST /api/jobs/<id>/<action> pause|resume|cancel|retry|remove|up
  POST /api/queue/<action>     pause_queue|resume_queue|cancel_all|retry_failed|clear_finished
  GET/POST /api/settings       crawl settings (persisted to settings.json)
  POST /api/cache/<ncode>/clear
  GET  /files/<name>           download a generated file
"""
from __future__ import annotations

import pathlib

from flask import Flask, abort, jsonify, request, send_from_directory

try:
    from . import config, crawler, jobs, translator, qc
    from .jobs import get_manager
except ImportError:
    import config, crawler, jobs, translator, qc
    from jobs import get_manager

STATIC_DIR = pathlib.Path(__file__).resolve().parent / "static"
app = Flask(__name__, static_folder=str(STATIC_DIR), static_url_path="/static")
app.json.ensure_ascii = False


def _err(message: str, code: int = 400):
    return jsonify({"error": message}), code


@app.get("/")
def index():
    return send_from_directory(STATIC_DIR, "index.html")


@app.get("/api/options")
def options():
    return jsonify({
        "genres": config.GENRE_MAP,
        "tag_groups": config.TAG_GROUPS,
        "flags": config.SEARCH_FLAGS,
        "types": config.NOVEL_TYPES,
        "orders": config.ORDERS,
        "settings": crawler.load_settings(),
        "download_dir": str(config.DOWNLOAD_DIR),
    })


@app.post("/api/search")
def search():
    query = request.get_json(silent=True) or {}
    logger.info(f"Incoming search query: {query}")
    try:
        res = crawler.search(query)
        logger.info(f"Search result: allcount={res.get('allcount')}, items_count={len(res.get('items', []))}")
        return jsonify(res)
    except Exception as exc:
        logger.error(f"Search failed: {exc}", exc_info=True)
        return _err(f"Search failed: {exc}", 502)


@app.post("/api/ncodes/parse")
def parse_ncodes():
    text = ""
    upload = request.files.get("file")
    if upload:
        raw = upload.read()
        for enc in ("utf-8-sig", "cp932", "latin-1"):
            try:
                text = raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue
    else:
        text = (request.get_json(silent=True) or {}).get("text", "")
    codes = crawler.parse_ncode_list(text)
    if not codes:
        return jsonify({"items": [], "missing": []})
    try:
        meta = crawler.lookup_ncodes(codes)
    except Exception:
        meta = {}
    items = []
    for c in codes:
        info = meta.get(c, {"ncode": c, "title": "", "writer": "", "chapters": 0, "url": f"{config.NOVEL_BASE_URL}/{c}/"})
        items.append({**info, "found": c in meta, "cached": crawler.cached_count(c)})
    return jsonify({"items": items, "missing": [c for c in codes if c not in meta]})


@app.get("/api/jobs")
def jobs_list():
    return jsonify(get_manager().snapshot())


@app.post("/api/jobs")
def jobs_add():
    body = request.get_json(silent=True) or {}
    overrides = body.get("overrides") or {}
    items = [{**it, "overrides": {**overrides, **(it.get("overrides") or {})}} for it in body.get("items", [])]
    added = get_manager().add(items)
    return jsonify({"added": added, "skipped": len(items) - len(added)})


@app.post("/api/jobs/<job_id>/<action>")
def jobs_action(job_id: str, action: str):
    if not get_manager().action(job_id, action):
        return _err(f"Cannot {action} this job")
    return jsonify({"ok": True})


@app.post("/api/queue/<action>")
def queue_action(action: str):
    allowed = {"pause_queue", "resume_queue", "cancel_all", "retry_failed", "clear_finished"}
    if action not in allowed:
        return _err("Unknown action")
    return jsonify({"ok": True, "count": get_manager().bulk(action)})


@app.route("/api/settings", methods=["GET", "POST"])
def settings():
    if request.method == "POST":
        return jsonify(crawler.save_settings(request.get_json(silent=True) or {}))
    return jsonify(crawler.load_settings())


@app.route("/api/bookfere", methods=["GET", "POST"])
def bookfere_config():
    if request.method == "POST":
        payload = request.get_json(silent=True) or {}
        sync = bool(payload.pop("sync_calibre", False))
        return jsonify(translator.save_bookfere_settings(payload, sync_calibre=sync))
    return jsonify(translator.get_bookfere_settings())


@app.post("/api/bookfere/import_calibre")
def bookfere_import_calibre():
    return jsonify(translator.import_from_calibre())


@app.post("/api/bookfere/sync_calibre")
def bookfere_sync_calibre():
    return jsonify(translator.sync_to_calibre())


@app.get("/api/bookfere/export")
def bookfere_export():
    cfg_path = translator.get_bookfere_config_path()
    if not cfg_path.is_file():
        return _err("No config file found to export")
    return send_from_directory(cfg_path.parent, cfg_path.name, as_attachment=True)


@app.post("/api/bookfere/raw")
def bookfere_raw():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return _err("Invalid JSON configuration")
    cfg_path = translator.get_bookfere_config_path()
    import json
    cfg_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return jsonify({"ok": True, "settings": translator.get_bookfere_settings()})


@app.route("/api/qc", methods=["POST"])
def run_qc():
    payload = request.get_json(silent=True) or {}
    target_file = payload.get("file")
    if payload.get("all") or not target_file:
        res = qc.run_batch_qc(config.DOWNLOAD_DIR)
        return jsonify({"ok": True, "results": res})

    target_path = (config.DOWNLOAD_DIR / target_file).resolve()
    if config.DOWNLOAD_DIR.resolve() not in target_path.parents or not target_path.is_file():
        return _err("File not found in downloads")
    try:
        _, stats = qc.standardize_epub_file(target_path)
        return jsonify({"ok": True, "file": target_file, "stats": stats})
    except Exception as e:
        return _err(str(e))


@app.post("/api/cache/<ncode>/clear")
def cache_clear(ncode: str):
    if not crawler.NCODE_RE.fullmatch(ncode.lower()):
        return _err("Invalid ncode")
    return jsonify({"removed": crawler.clear_cache(ncode)})


@app.get("/files/<path:name>")
def files(name: str):
    target = (config.DOWNLOAD_DIR / name).resolve()
    if config.DOWNLOAD_DIR.resolve() not in target.parents or not target.is_file():
        abort(404)
    return send_from_directory(config.DOWNLOAD_DIR, name, as_attachment=True)


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="ncode_pipe dashboard")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5005)
    args = parser.parse_args()
    config.DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    get_manager()  # start worker
    print(f"ncode_pipe dashboard: http://{args.host}:{args.port}")
    app.run(host=args.host, port=args.port, threaded=True, use_reloader=False)


if __name__ == "__main__":
    main()
