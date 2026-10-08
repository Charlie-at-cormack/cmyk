"""FastAPI app. Binds to localhost only (see __main__); a server install sits behind a reverse proxy."""
from __future__ import annotations

import base64
import binascii
import os
import secrets
import shutil
from contextlib import asynccontextmanager
import threading
import time
from pathlib import Path

import pymupdf
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import __version__, jobs, report
from .engine import STAGES, analyse, load_profile, load_profiles
from .engine.profiles import merge_overrides, DEFAULT_PROFILE

STATIC = Path(__file__).parent / "static"
LOCAL_HOSTS = {"localhost", "127.0.0.1", "[::1]"}

# Server install (deploy/PLESK.md): the public host name(s) the proxy forwards, plus a login.
SERVER_HOSTS = {h.strip().lower() for h in os.environ.get("CMYK_ALLOWED_HOSTS", "").split(",") if h.strip()}
ALLOWED_HOSTS = LOCAL_HOSTS | SERVER_HOSTS
AUTH_USER = os.environ.get("CMYK_USER", "cmyk")
AUTH_PASSWORD = os.environ.get("CMYK_PASSWORD", "")
if SERVER_HOSTS and not AUTH_PASSWORD:
    raise RuntimeError("CMYK_ALLOWED_HOSTS is set but CMYK_PASSWORD is not: refusing to serve PDFs without a login")


def _authorised(header: str | None) -> bool:
    """HTTP Basic auth, only enforced when CMYK_PASSWORD is set."""
    if not AUTH_PASSWORD:
        return True
    scheme, _, value = (header or "").partition(" ")
    if scheme.lower() != "basic":
        return False
    try:
        user, _, password = base64.b64decode(value, validate=True).decode("utf-8").partition(":")
    except (binascii.Error, UnicodeDecodeError):
        return False
    return (secrets.compare_digest(user.encode(), AUTH_USER.encode())
            & secrets.compare_digest(password.encode(), AUTH_PASSWORD.encode()))


@asynccontextmanager
async def lifespan(_: FastAPI):
    jobs.cleanup_old()
    yield


app = FastAPI(title="Cormack Media Yield Kontrol (CMYK)", version=__version__, lifespan=lifespan)


@app.middleware("http")
async def local_only(request: Request, call_next):
    """Defence in depth: refuse foreign Host headers (DNS rebinding), missing logins and cross-site writes."""
    host = (request.headers.get("host") or "").rsplit(":", 1)[0] if not (
        request.headers.get("host") or "").startswith("[") else "[::1]"
    if host.lower() not in ALLOWED_HOSTS:
        return JSONResponse({"detail": "Local access only"}, status_code=403)
    if not _authorised(request.headers.get("authorization")):
        return Response(status_code=401, headers={"WWW-Authenticate": 'Basic realm="CMYK", charset="UTF-8"'})
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        origin = request.headers.get("origin")
        if origin and origin.split("://", 1)[-1].rsplit(":", 1)[0].lower() not in ALLOWED_HOSTS:
            return JSONResponse({"detail": "Cross-site request refused"}, status_code=403)
    return await call_next(request)


def _job(job_id: str) -> jobs.Job:
    job = jobs.get_job(job_id)
    if not job:
        raise HTTPException(404, "Unknown job")
    return job


def _public(job: jobs.Job) -> dict:
    return {
        "id": job.id, "meta": job.meta, "status": job.status, "progress": job.progress,
        "error": job.error, "result": job.result, "review": job.review,
        "blockers": jobs.blockers(job) if job.result else [],
    }


@app.get("/api/version")
def version() -> dict:
    return {"version": __version__, "stages": [{"id": i, "label": l} for i, l in STAGES]}


@app.get("/api/profiles")
def profiles() -> dict:
    return {"default": DEFAULT_PROFILE, "profiles": load_profiles()}


@app.post("/api/jobs")
async def upload(file: UploadFile = File(...)) -> dict:
    job = jobs.create_job()
    try:
        with open(job.pdf, "wb") as out:
            shutil.copyfileobj(file.file, out, 1024 * 1024)
        doc = pymupdf.open(job.pdf)
        if not doc.is_pdf:
            raise ValueError("Not a PDF")
        pages = doc.page_count
        doc.close()
    except Exception:
        jobs.delete_job(job.id)
        raise HTTPException(400, "That file could not be opened as a PDF")
    job.meta = {"filename": Path(file.filename or "file.pdf").name,
                "size_mb": job.pdf.stat().st_size / 1048576, "pages": pages}
    job.save_meta()
    return _public(job)


class CheckRequest(BaseModel):
    profile_id: str = DEFAULT_PROFILE
    overrides: dict | None = None


def _run(job: jobs.Job, profile: dict) -> None:
    def prog(p: int, t: int) -> None:
        job.progress = {"page": p, "total": t}
    try:
        result = analyse(str(job.pdf), profile, prog)
        job.result = result
        job.meta["checked_at"] = time.time()
        job.save_meta()
        job.save_result()
        job.status = "done"
    except Exception as exc:  # surfaced to the UI
        job.error = f"{type(exc).__name__}: {exc}"
        job.status = "error"


@app.post("/api/jobs/{job_id}/check")
def check(job_id: str, req: CheckRequest) -> dict:
    job = _job(job_id)
    if job.status == "analysing":
        raise HTTPException(409, "Already analysing")
    try:
        base = load_profile(req.profile_id)
    except KeyError:
        raise HTTPException(400, "Unknown profile")
    profile = merge_overrides(base, req.overrides)
    job.status, job.error = "analysing", ""
    job.progress = {"page": 0, "total": job.meta.get("pages", 0)}
    threading.Thread(target=_run, args=(job, profile), daemon=True).start()
    return _public(job)


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    return _public(_job(job_id))


@app.get("/api/jobs/{job_id}/pages/{n}.png")
def page_png(job_id: str, n: int, scale: float = 2.0) -> Response:
    job = _job(job_id)
    scale = min(max(scale, 0.5), 6.0)
    if n < 1 or n > job.meta.get("pages", 0):
        raise HTTPException(404, "No such page")
    cache = job.dir / f"p{n}_{scale:g}.png"
    if not cache.exists():
        with job.lock:
            doc = pymupdf.open(job.pdf)
            pix = doc[n - 1].get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
            pix.save(cache)
            doc.close()
    return FileResponse(cache, media_type="image/png", headers={"Cache-Control": "max-age=3600"})


@app.put("/api/jobs/{job_id}/review")
async def put_review(job_id: str, request: Request) -> dict:
    job = _job(job_id)
    data = await request.json()
    if not isinstance(data, dict):
        raise HTTPException(400, "Bad review payload")
    new = {**jobs.EMPTY_REVIEW, **{k: data[k] for k in jobs.EMPTY_REVIEW if k in data}}
    prev = job.review
    job.review = new
    if new["outcome"] == "signed_off" and jobs.blockers(job):
        job.review = prev
        raise HTTPException(409, "Sign-off blocked: " + " ".join(jobs.blockers(job)))
    if new["outcome"] != prev.get("outcome"):
        new["outcome_at"] = time.strftime("%Y-%m-%d %H:%M") if new["outcome"] != "draft" else None
    else:
        new["outcome_at"] = prev.get("outcome_at")
    job.save_review()
    return _public(job)


@app.get("/api/jobs/{job_id}/report.html")
def report_html(job_id: str) -> HTMLResponse:
    job = _job(job_id)
    if not job.result:
        raise HTTPException(409, "Run the check first")
    return HTMLResponse(report.build(job))


@app.get("/api/jobs/{job_id}/report.json")
def report_json(job_id: str) -> dict:
    job = _job(job_id)
    return {"meta": job.meta, "result": job.result, "review": job.review,
            "blockers": jobs.blockers(job)}


@app.delete("/api/jobs/{job_id}")
def close_job(job_id: str) -> dict:
    return {"deleted": jobs.delete_job(job_id)}


app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")
