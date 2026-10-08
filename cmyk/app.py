"""FastAPI app. Binds to localhost only (see __main__); a server install sits behind a reverse proxy."""
from __future__ import annotations

import os
import shutil
from contextlib import asynccontextmanager
import threading
import time
from pathlib import Path

import pymupdf
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import __version__, auth, jobs, report
from .engine import STAGES, analyse, load_profile, load_profiles
from .engine.profiles import merge_overrides, DEFAULT_PROFILE

STATIC = Path(__file__).parent / "static"
LOCAL_HOSTS = {"localhost", "127.0.0.1", "[::1]"}

# Server install (deploy/PLESK.md): the public host name(s) the proxy forwards. Setting any of them
# turns on the login (cmyk/auth.py); a plain localhost run stays open.
SERVER_HOSTS = {h.strip().lower() for h in os.environ.get("CMYK_ALLOWED_HOSTS", "").split(",") if h.strip()}
ALLOWED_HOSTS = LOCAL_HOSTS | SERVER_HOSTS
PUBLIC_PATHS = {"/login.html", "/login.js", "/style.css", "/favicon.ico",
                "/api/auth/state", "/api/auth/setup", "/api/auth/login", "/api/auth/logout"}


@asynccontextmanager
async def lifespan(_: FastAPI):
    jobs.cleanup_old()
    if SERVER_HOSTS and auth.needs_setup():
        print(f"CMYK first start: open the site and create the login with setup code {auth.setup_code()}",
              flush=True)
    yield


app = FastAPI(title="Cormack Media Yield Kontrol (CMYK)", version=__version__, lifespan=lifespan)


@app.middleware("http")
async def local_only(request: Request, call_next):
    """Defence in depth: refuse foreign Host headers (DNS rebinding), missing logins and cross-site writes."""
    host = (request.headers.get("host") or "").rsplit(":", 1)[0] if not (
        request.headers.get("host") or "").startswith("[") else "[::1]"
    if host.lower() not in ALLOWED_HOSTS:
        return JSONResponse({"detail": "Local access only"}, status_code=403)
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        origin = request.headers.get("origin")
        if origin and origin.split("://", 1)[-1].rsplit(":", 1)[0].lower() not in ALLOWED_HOSTS:
            return JSONResponse({"detail": "Cross-site request refused"}, status_code=403)
    if (SERVER_HOSTS and request.url.path not in PUBLIC_PATHS
            and not auth.session_email(request.cookies.get(auth.COOKIE))):
        if request.url.path.startswith("/api/"):
            return JSONResponse({"detail": "Sign in required"}, status_code=401)
        return RedirectResponse("/login.html", status_code=303)
    return await call_next(request)


class Credentials(BaseModel):
    email: str = ""
    password: str = ""
    code: str = ""


def _client(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _signed_in(request: Request, email: str) -> JSONResponse:
    resp = JSONResponse({"email": email})
    resp.set_cookie(auth.COOKIE, auth.make_session(email), max_age=auth.SESSION_DAYS * 86400,
                    httponly=True, samesite="lax", secure=request.url.scheme == "https")
    return resp


def _server_only(request: Request) -> str:
    if not SERVER_HOSTS:
        raise HTTPException(404, "Sign-in is only used on a server install")
    ip = _client(request)
    if auth.throttled(ip):
        raise HTTPException(429, "Too many attempts. Try again in 15 minutes.")
    return ip


@app.get("/api/auth/state")
def auth_state(request: Request) -> dict:
    if not SERVER_HOSTS:
        return {"server": False, "setup": False, "email": None}
    setup = auth.needs_setup()
    if setup:
        auth.setup_code()  # make sure the code the setup page asks for exists
    return {"server": True, "setup": setup, "email": auth.session_email(request.cookies.get(auth.COOKIE))}


@app.post("/api/auth/setup")
def auth_setup(req: Credentials, request: Request) -> JSONResponse:
    ip = _server_only(request)
    try:
        email = auth.create_account(req.code, req.email, req.password)
    except FileExistsError:
        raise HTTPException(409, "The login has already been created. Reload the page to sign in.")
    except PermissionError:
        auth.record_failure(ip)
        raise HTTPException(403, "That setup code is not right.")
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    auth.clear_failures(ip)
    return _signed_in(request, email)


@app.post("/api/auth/login")
def auth_login(req: Credentials, request: Request) -> JSONResponse:
    ip = _server_only(request)
    if not auth.check_login(req.email, req.password):
        auth.record_failure(ip)
        raise HTTPException(401, "Email or password is not right.")
    auth.clear_failures(ip)
    return _signed_in(request, req.email.strip().lower())


@app.post("/api/auth/logout")
def auth_logout(request: Request) -> JSONResponse:
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(auth.COOKIE, httponly=True, samesite="lax", secure=request.url.scheme == "https")
    return resp


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
