"""Background job queue for ncode_pipe.

One worker thread processes jobs sequentially (polite crawling, same as the
userscript). Each job exposes live progress (stage, chapter n/total, speed, ETA)
for the dashboard and is persisted to status.json so history and unfinished
jobs survive a restart (chapter cache makes re-runs resume where they stopped).
"""
from __future__ import annotations

import json
import threading
import time
import traceback
import uuid
from collections import deque
from typing import Dict, List, Optional

from . import config, crawler

ACTIVE = ("queued", "running", "paused", "translating", "uploading")
MAX_LOG = 300


class Job:
    def __init__(self, ncode: str, title: str = "", overrides: Optional[Dict] = None, jid: str = "") -> None:
        self.id = jid or uuid.uuid4().hex[:10]
        self.ncode = ncode.lower()
        self.title = title
        self.overrides = overrides or {}
        self.status = "queued"
        self.stage = ""
        self.message = ""
        self.done = 0
        self.total = 0
        self.fetched = 0
        self.cached = 0
        self.current = ""
        self.files: List[str] = []
        self.tl_files: List[str] = []
        self.error = ""
        self.writer = ""
        self.created = time.time()
        self.started: Optional[float] = None
        self.finished: Optional[float] = None
        self.control = crawler.CrawlControl()
        self._fetch_times: deque = deque(maxlen=20)

    # live speed based on the last fetched (non-cached) chapters
    def rate(self) -> float:
        if len(self._fetch_times) < 2:
            return 0.0
        span = self._fetch_times[-1] - self._fetch_times[0]
        return (len(self._fetch_times) - 1) / span if span > 0 else 0.0

    def to_dict(self) -> Dict:
        rate = self.rate()
        remaining = max(0, self.total - self.done)
        return {
            "id": self.id, "ncode": self.ncode, "title": self.title, "writer": self.writer,
            "status": self.status, "stage": self.stage, "message": self.message,
            "done": self.done, "total": self.total, "fetched": self.fetched, "cached": self.cached,
            "percent": round(self.done * 100 / self.total, 1) if self.total else 0,
            "current": self.current, "files": self.files, "tl_files": self.tl_files, "error": self.error,
            "overrides": self.overrides,
            "rate": round(rate * 60, 1),  # chapters per minute
            "eta": int(remaining / rate) if rate and self.status == "running" else None,
            "elapsed": int((self.finished or time.time()) - self.started) if self.started else 0,
            "created": self.created, "started": self.started, "finished": self.finished,
        }

    def to_record(self) -> Dict:
        keep = ("id", "ncode", "title", "writer", "status", "done", "total", "fetched", "cached",
                "files", "tl_files", "error", "overrides", "created", "started", "finished", "message")
        return {k: v for k, v in self.to_dict().items() if k in keep}

    @classmethod
    def from_record(cls, rec: Dict) -> "Job":
        job = cls(rec["ncode"], rec.get("title", ""), rec.get("overrides"), rec.get("id", ""))
        for key in ("writer", "done", "total", "fetched", "cached", "files", "tl_files", "error",
                    "created", "started", "finished", "message"):
            if key in rec:
                setattr(job, key, rec[key])
        status = rec.get("status", "queued")
        # anything interrupted by a restart goes back to the queue
        job.status = "queued" if status in ACTIVE else status
        return job


class JobManager:
    def __init__(self) -> None:
        self.jobs: Dict[str, Job] = {}
        self.order: List[str] = []
        self.logs: deque = deque(maxlen=MAX_LOG)
        self._log_seq = 0
        self.lock = threading.RLock()
        self.wake = threading.Event()
        self.queue_paused = False
        self._last_save = 0.0
        self._load()
        threading.Thread(target=self._worker, name="ncode-worker", daemon=True).start()

    # ------------------------------------------------------------- persistence
    def _load(self) -> None:
        try:
            data = json.loads(config.STATUS_PATH.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return
        for rec in data.get("jobs", []):
            try:
                job = Job.from_record(rec)
            except KeyError:
                continue
            self.jobs[job.id] = job
            self.order.append(job.id)

    def _save(self, force: bool = False) -> None:
        now = time.time()
        if not force and now - self._last_save < 2:
            return
        self._last_save = now
        with self.lock:
            data = {"jobs": [self.jobs[j].to_record() for j in self.order]}
        tmp = config.STATUS_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(config.STATUS_PATH)

    # ------------------------------------------------------------- logging
    def log(self, message: str, level: str = "info", job: Optional[Job] = None) -> None:
        # monotonically increasing id lets the dashboard append only new lines
        with self.lock:
            self._log_seq += 1
            self.logs.append({"id": self._log_seq, "t": time.time(), "level": level,
                              "job": job.ncode if job else "", "message": message})

    # ------------------------------------------------------------- public API
    def add(self, items: List[Dict]) -> List[Dict]:
        """items: [{"ncode", "title"?, "overrides"?}] -> added jobs (duplicates in queue skipped)."""
        added = []
        with self.lock:
            active = {self.jobs[j].ncode for j in self.order if self.jobs[j].status in ACTIVE}
            for item in items:
                ncode = (item.get("ncode") or "").strip().lower()
                if not crawler.NCODE_RE.fullmatch(ncode) or ncode in active:
                    continue
                job = Job(ncode, item.get("title", ""), item.get("overrides") or {})
                self.jobs[job.id] = job
                self.order.append(job.id)
                active.add(ncode)
                added.append(job.to_dict())
            if added:
                self.log(f"Queued {len(added)} novel(s)")
        self._save(force=True)
        self.wake.set()
        return added

    def snapshot(self) -> Dict:
        with self.lock:
            jobs = [self.jobs[j].to_dict() for j in self.order]
        counts: Dict[str, int] = {}
        for j in jobs:
            counts[j["status"]] = counts.get(j["status"], 0) + 1
        batch = [j for j in jobs if j["status"] != "removed"]
        finished = sum(1 for j in batch if j["status"] in ("done", "skipped", "error", "cancelled"))
        return {"jobs": jobs, "counts": counts, "queue_paused": self.queue_paused,
                "overall": {"finished": finished, "total": len(batch)},
                "logs": list(self.logs)[-150:]}

    def action(self, job_id: str, action: str) -> bool:
        with self.lock:
            job = self.jobs.get(job_id)
            if not job:
                return False
            if action == "pause" and job.status == "running":
                job.control.pause()
                job.status = "paused"
            elif action == "resume" and job.status == "paused":
                job.control.resume()
                job.status = "running"
            elif action == "cancel" and job.status in ACTIVE:
                job.control.cancel()
                if job.status == "queued":
                    job.status = "cancelled"
            elif action == "retry" and job.status in ("error", "cancelled", "done", "skipped"):
                fresh = Job(job.ncode, job.title, job.overrides, job.id)
                self.jobs[job.id] = fresh
            elif action in ("translate_id", "translate_en") and job.status in ("done", "skipped"):
                target_lang = "id" if action == "translate_id" else "en"
                threading.Thread(target=self._run_translate, args=(job, target_lang), daemon=True).start()
            elif action == "upload_mynovels" and job.status in ("done", "skipped", "translated"):
                threading.Thread(target=self._run_upload, args=(job,), daemon=True).start()
            elif action == "remove" and job.status not in ("running", "paused", "translating", "uploading"):
                self.jobs.pop(job_id)
                self.order.remove(job_id)
            elif action == "up":
                i = self.order.index(job_id)
                if i > 0:
                    self.order[i - 1], self.order[i] = self.order[i], self.order[i - 1]
            else:
                return False
        self.log(f"{action} {job.ncode}", job=job)
        self._save(force=True)
        self.wake.set()
        return True

    def bulk(self, action: str) -> int:
        n = 0
        with self.lock:
            if action == "clear_finished":
                keep = [j for j in self.order if self.jobs[j].status in ACTIVE]
                n = len(self.order) - len(keep)
                for j in set(self.order) - set(keep):
                    self.jobs.pop(j)
                self.order = keep
            elif action == "cancel_all":
                for j in self.order:
                    job = self.jobs[j]
                    if job.status in ACTIVE:
                        job.control.cancel()
                        if job.status == "queued":
                            job.status = "cancelled"
                        n += 1
            elif action == "retry_failed":
                for j in self.order:
                    job = self.jobs[j]
                    if job.status in ("error", "cancelled"):
                        self.jobs[j] = Job(job.ncode, job.title, job.overrides, job.id)
                        n += 1
            elif action == "pause_queue":
                self.queue_paused = True
            elif action == "resume_queue":
                self.queue_paused = False
        self.log(f"{action.replace('_', ' ')} ({n})")
        self._save(force=True)
        self.wake.set()
        return n

    # ------------------------------------------------------------- worker
    def _next(self) -> Optional[Job]:
        with self.lock:
            if self.queue_paused:
                return None
            for j in self.order:
                if self.jobs[j].status == "queued":
                    return self.jobs[j]
        return None

    def _worker(self) -> None:
        first = True
        while True:
            job = self._next()
            if job is None:
                self.wake.wait(timeout=2)
                self.wake.clear()
                continue
            if not first:
                gap = crawler.load_settings().get("novel_delay_s", 5)
                if gap:
                    job.message = f"Waiting {gap}s before next novel"
                    time.sleep(gap)
                    if job.status != "queued":
                        continue
            first = False
            self._run(job)

    def _on_event(self, job: Job, ev: Dict) -> None:
        kind = ev.get("type")
        if kind == "stage":
            job.stage, job.message = ev["stage"], ev["message"]
            self.log(ev["message"], job=job)
        elif kind == "meta":
            meta = ev["meta"]
            job.title, job.writer = meta.get("title", job.title), meta.get("writer", "")
        elif kind == "progress":
            job.done, job.total, job.current = ev["done"], ev["total"], ev["title"]
            if ev["cached"]:
                job.cached += 1
            else:
                job.fetched += 1
                job._fetch_times.append(time.time())
            job.message = f"Ch {ev['episode']}: {ev['title']}" + (" (cached)" if ev["cached"] else "")
        elif kind == "log":
            job.message = ev["message"]
            self.log(ev["message"], ev.get("level", "info"), job)
        self._save()

    def _run_translate(self, job: Job, target_lang: str) -> None:
        from . import translator
        prev_status = job.status
        job.status = "translating"
        job.stage = "translating"
        job.message = f"Starting translation to {target_lang.upper()}..."
        self.log(f"Translation to {target_lang.upper()} started", job=job)
        self._save(force=True)

        try:
            # Load raw chapters and metadata from cache or fetch minimal
            meta = crawler.fetch_meta(job.ncode)
            cache_dir = crawler._cache_dir(job.ncode)
            chapters = []
            for cfile in sorted(cache_dir.glob("*.json")):
                if cfile.name == "meta.json":
                    continue
                try:
                    chapters.append(json.loads(cfile.read_text(encoding="utf-8")))
                except Exception:
                    pass

            if not chapters:
                raise RuntimeError("No cached chapters found to translate. Please crawl first.")

            def _on_tl_progress(ev):
                if ev.get("type") == "progress_tl":
                    job.done = ev["done"]
                    job.total = ev["total"]
                    job.message = f"TL Ch {ev['episode']}: {ev['title']}" + (" (cached)" if ev["cached"] else "")
                elif ev.get("type") == "stage":
                    job.message = ev["message"]
                self._save()

            tl_res = translator.translate_novel_cached(
                job.ncode, meta, chapters, target_lang=target_lang,
                progress=_on_tl_progress, control=job.control
            )

            out_dir = config.BASE_DIR / "downloads"
            epub_path = translator.build_translated_epub(tl_res["meta"], tl_res["chapters"], out_dir)
            if epub_path not in job.tl_files:
                job.tl_files.append(epub_path)

            job.status = "translated"
            job.message = f"Translated to {target_lang.upper()}: {pathlib.Path(epub_path).name}"
            self.log(f"Translation finished: {job.message}", job=job)

            # Auto upload if enabled
            if crawler.load_settings().get("auto_upload"):
                self._run_upload(job)

        except Exception as exc:
            job.status = prev_status
            job.error = str(exc)
            job.message = f"Translation error: {exc}"
            self.log(f"Translation failed: {exc}", "error", job=job)
            traceback.print_exc()
        finally:
            self._save(force=True)

    def _run_upload(self, job: Job) -> None:
        from . import uploader
        prev_status = job.status
        job.status = "uploading"
        job.stage = "uploading"
        job.message = "Uploading to MyNovels..."
        self.log("Uploading to MyNovels", job=job)
        self._save(force=True)

        try:
            # Find best file to upload: prioritize translated EPUB, fallback to JP EPUB
            target_file = None
            for f in reversed(job.tl_files):
                if f.endswith(".epub") and os.path.isfile(f):
                    target_file = f
                    break
            if not target_file:
                for f in reversed(job.files):
                    if f.endswith(".epub") and os.path.isfile(f):
                        target_file = f
                        break

            if not target_file:
                raise RuntimeError("No EPUB file available to upload.")

            stem = pathlib.Path(target_file).stem
            title = stem
            if " - " in stem:
                title = stem.rsplit(" - ", 1)[0].strip()

            resp = uploader.upload_to_mynovels(
                epub_path=target_file,
                title=title,
                original_title=job.title or job.ncode,
            )
            job.status = "uploaded"
            job.message = f"Uploaded to MyNovels! (Book ID: {resp.get('id', '-')})"
            self.log(f"Uploaded successfully to MyNovels: {title}", job=job)
        except Exception as exc:
            job.status = prev_status
            job.error = str(exc)
            job.message = f"Upload error: {exc}"
            self.log(f"Upload failed: {exc}", "error", job=job)
            traceback.print_exc()
        finally:
            self._save(force=True)

    def _run(self, job: Job) -> None:
        job.status, job.started, job.finished = "running", time.time(), None
        job.error, job.done, job.fetched, job.cached, job.files = "", 0, 0, 0, []
        self.log("Started", job=job)
        try:
            result = crawler.crawl_novel(job.ncode, settings=job.overrides, control=job.control,
                                         progress=lambda ev: self._on_event(job, ev))
            job.files = result["files"]
            job.status = "skipped" if result.get("skipped") else "done"
            job.message = (f"{result['chapters']} ch ({result['fetched']} new, {result['cached']} cached)"
                           if not result.get("skipped") else "Output exists, skipped")
            self.log(f"Finished: {job.message}", job=job)
            # Auto translate if enabled
            if crawler.load_settings().get("auto_translate"):
                tlang = crawler.load_settings().get("target_lang", "id")
                self._run_translate(job, target_lang=tlang)
        except crawler.CrawlCancelled:
            job.status, job.message = "cancelled", "Cancelled, progress cached"
            self.log("Cancelled", "warn", job)
        except Exception as exc:  # keep worker alive on any failure
            job.status, job.error = "error", str(exc)
            job.message = "Failed, cached chapters kept. Retry to continue."
            self.log(f"Error: {exc}", "error", job)
            traceback.print_exc()
        finally:
            job.finished = time.time()
            self._save(force=True)


_manager: Optional[JobManager] = None
_manager_lock = threading.Lock()


def get_manager() -> JobManager:
    global _manager
    with _manager_lock:
        if _manager is None:
            _manager = JobManager()
        return _manager
