"""Job storage: one folder per uploaded PDF, outside the repo, deleted when the job is closed."""
from __future__ import annotations

import json
import os
import shutil
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Optional


def data_dir() -> Path:
    env = os.environ.get("CMYK_DATA_DIR")
    if env:
        base = Path(env)
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / "CMYK"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "cmyk"
    (base / "jobs").mkdir(parents=True, exist_ok=True)
    return base


JOBS_DIR = data_dir() / "jobs"
MAX_AGE_DAYS = 7

EMPTY_REVIEW = {
    "reviewer": "", "issues": {}, "markers": [], "manual": {},
    "outcome": "draft", "outcome_at": None, "comment": "",
}


class Job:
    def __init__(self, job_id: str) -> None:
        self.id = job_id
        self.dir = JOBS_DIR / job_id
        self.pdf = self.dir / "file.pdf"
        self.lock = threading.Lock()
        self.status = "uploaded"          # uploaded | analysing | done | error
        self.progress = {"page": 0, "total": 0}
        self.error = ""
        self.meta: dict = {}
        self.result: Optional[dict] = None
        self.review: dict = json.loads(json.dumps(EMPTY_REVIEW))

    # -- persistence -------------------------------------------------------
    def _write(self, name: str, obj) -> None:
        tmp = self.dir / (name + ".tmp")
        tmp.write_text(json.dumps(obj), encoding="utf-8")
        tmp.replace(self.dir / name)

    def save_meta(self) -> None:
        self._write("meta.json", self.meta)

    def save_result(self) -> None:
        self._write("result.json", self.result)

    def save_review(self) -> None:
        self._write("review.json", self.review)

    @classmethod
    def load(cls, job_id: str) -> Optional["Job"]:
        if not job_id.isalnum():
            return None
        job = cls(job_id)
        if not job.pdf.exists():
            return None
        for name, attr in (("meta.json", "meta"), ("result.json", "result"), ("review.json", "review")):
            f = job.dir / name
            if f.exists():
                try:
                    setattr(job, attr, json.loads(f.read_text(encoding="utf-8")))
                except ValueError:
                    pass
        if job.result:
            job.status = "done"
        return job


_jobs: dict[str, Job] = {}
_reg_lock = threading.Lock()


def create_job() -> Job:
    job = Job(uuid.uuid4().hex[:12])
    job.dir.mkdir(parents=True)
    with _reg_lock:
        _jobs[job.id] = job
    return job


def get_job(job_id: str) -> Optional[Job]:
    with _reg_lock:
        job = _jobs.get(job_id)
        if job is None:
            job = Job.load(job_id)
            if job:
                _jobs[job_id] = job
        return job


def delete_job(job_id: str) -> bool:
    with _reg_lock:
        job = _jobs.pop(job_id, None) or Job.load(job_id)
    if not job:
        return False
    shutil.rmtree(job.dir, ignore_errors=True)
    return True


def cleanup_old(max_age_days: int = MAX_AGE_DAYS) -> None:
    cutoff = time.time() - max_age_days * 86400
    for d in JOBS_DIR.iterdir():
        try:
            if d.is_dir() and d.stat().st_mtime < cutoff:
                shutil.rmtree(d, ignore_errors=True)
        except OSError:
            pass


def blockers(job: Job) -> list[str]:
    """Why sign-off is not yet possible (empty list = ready)."""
    out: list[str] = []
    if not job.result:
        return ["Analysis has not run yet."]
    rev = job.review
    open_n = fail_n = 0
    for it in job.result["issues"]:
        if it["severity"] == "info":
            continue
        state = rev["issues"].get(it["id"], {}).get("state", "open")
        if state == "open":
            open_n += 1
        elif state == "confirmed" and it["severity"] == "fail":
            fail_n += 1
    if open_n:
        out.append(f"{open_n} finding(s) not reviewed yet.")
    if fail_n:
        out.append(f"{fail_n} confirmed failure(s) still unresolved.")
    for mc in job.result["profile"].get("manual_checks", []):
        st = rev["manual"].get(mc["id"], {}).get("status", "unchecked")
        if st != "correct":
            out.append(f"Manual check not marked correct: {mc['label']}")
    return out
