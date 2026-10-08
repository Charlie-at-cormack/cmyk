"""FastAPI app. Binds to localhost only (see __main__); a server install sits behind a reverse proxy."""
from __future__ import annotations

import os
import shutil
from contextlib import asynccontextmanager
import threading
import time
from pathlib import Path

import pymupdf
from fastapi import APIRouter, Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import __version__, auth, jobs, mailer, report
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
PUBLIC_PREFIXES = ("/brand/", "/fonts/")  # logo, photo and font of the sign-in page


def _public_path(path: str) -> bool:
    return path in PUBLIC_PATHS or (path.startswith(PUBLIC_PREFIXES) and ".." not in path)


@asynccontextmanager
async def lifespan(_: FastAPI):
    jobs.cleanup_old()
    if SERVER_HOSTS and auth.needs_setup():
        print(f"CMYK first start: open the site and create the admin login with setup code {auth.setup_code()}",
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
    if (SERVER_HOSTS and not _public_path(request.url.path)
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


def _require_server() -> None:
    if not SERVER_HOSTS:
        raise HTTPException(404, "Sign-in is only used on a server install")


def _server_only(request: Request) -> str:
    """For routes that check a password: server install only, with the per-IP brake."""
    _require_server()
    ip = _client(request)
    if auth.throttled(ip):
        raise HTTPException(429, "Too many attempts. Try again in 15 minutes.")
    return ip


def _me(request: Request) -> str:
    """The signed-in email. The middleware already turns away requests without a session; this is the backstop."""
    _require_server()
    email = auth.session_email(request.cookies.get(auth.COOKIE))
    if not email:
        raise HTTPException(401, "Sign in required")
    return email


def _admin(request: Request) -> str:
    email = _me(request)
    user = auth.get_user(email)
    if not user or user["role"] != "admin":
        raise HTTPException(403, "Admins only")
    return email


@app.get("/api/auth/state")
def auth_state(request: Request) -> dict:
    if not SERVER_HOSTS:
        return {"server": False, "setup": False, "email": None, "role": None, "name": None}
    setup = auth.needs_setup()
    if setup:
        auth.setup_code()  # make sure the code the setup page asks for exists
    email = auth.session_email(request.cookies.get(auth.COOKIE))
    user = auth.get_user(email) if email else None
    if not user:
        return {"server": True, "setup": setup, "email": None, "role": None, "name": None}
    return {"server": True, "setup": setup, "email": user["email"], "role": user["role"], "name": user["name"]}


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


class PasswordChange(BaseModel):
    current: str = ""
    new: str = ""


@app.post("/api/auth/password")
def auth_password(req: PasswordChange, request: Request) -> JSONResponse:
    ip = _server_only(request)
    email = _me(request)
    try:
        auth.change_password(email, req.current, req.new)
    except PermissionError:
        auth.record_failure(ip)
        raise HTTPException(403, "Current password is not right.")
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    auth.clear_failures(ip)
    return _signed_in(request, email)  # the old cookie was signed with the old hash


# -- admin: logins and outgoing email (server install only) --------------------
admin = APIRouter(prefix="/api/admin", dependencies=[Depends(_admin)])


class NewUser(BaseModel):
    email: str = ""
    name: str = ""
    role: str = "user"
    send_email: bool = False


class UserChanges(BaseModel):
    name: str | None = None
    role: str | None = None


class SendEmail(BaseModel):
    send_email: bool = False


class SmtpSettings(BaseModel):
    host: str = mailer.DEFAULTS["host"]
    port: int = mailer.DEFAULTS["port"]
    security: str = mailer.DEFAULTS["security"]
    username: str = mailer.DEFAULTS["username"]
    from_email: str = mailer.DEFAULTS["from_email"]
    from_name: str = mailer.DEFAULTS["from_name"]
    password: str | None = None
    clear_password: bool = False


class EmailTo(BaseModel):
    to: str = ""


def _send_failed(exc: Exception) -> str:
    return f"Could not send: {type(exc).__name__}: {exc}"[:300]


def _email(to: str, message: tuple[str, str], wanted: bool) -> tuple[bool, str | None]:
    """(emailed, email_error). A failure is reported, not raised: the login already exists either way."""
    if not wanted:
        return False, None
    cfg = mailer.settings()
    if not mailer.configured(cfg):
        return False, mailer.NOT_SET_UP
    try:
        mailer.send(cfg, to, *message)
    except Exception as exc:  # SMTP, network and TLS errors alike
        return False, _send_failed(exc)
    return True, None


def _no_login() -> HTTPException:
    return HTTPException(404, "There is no login for that email.")


@admin.get("/users")
def users_list() -> dict:
    return {"users": auth.list_users(), "smtp_configured": mailer.configured(mailer.settings())}


@admin.post("/users")
def users_add(req: NewUser, request: Request, me: str = Depends(_admin)) -> dict:
    try:
        user, password = auth.add_user(req.email, req.name, req.role, created_by=me)
    except FileExistsError:
        raise HTTPException(409, "That email already has a login.")
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    message = mailer.welcome(str(request.base_url), user["email"], password, user["name"])
    emailed, error = _email(user["email"], message, req.send_email)
    return {"user": user, "password": password, "emailed": emailed, "email_error": error}


# {email:path} so an address with a "/" in it (allowed, if rare) still reaches the route.
@admin.put("/users/{email:path}")
def users_update(email: str, req: UserChanges) -> dict:
    try:
        return auth.update_user(email, req.name, req.role)
    except KeyError:
        raise _no_login()
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except auth.LastAdminError:
        raise HTTPException(409, "Keep at least one admin.")


@admin.delete("/users/{email:path}")
def users_remove(email: str, me: str = Depends(_admin)) -> dict:
    if auth.norm_email(email) == me:
        raise HTTPException(400, "You can't remove your own login.")
    try:
        auth.remove_user(email)
    except KeyError:
        raise _no_login()
    except auth.LastAdminError:
        raise HTTPException(409, "Keep at least one admin.")
    return {"deleted": True}


@admin.post("/users/{email:path}/reset")
def users_reset(email: str, request: Request, req: SendEmail = SendEmail(), me: str = Depends(_admin)) -> dict:
    if auth.norm_email(email) == me:
        raise HTTPException(400, "Use Change password for your own login.")
    try:
        user, password = auth.reset_password(email)
    except KeyError:
        raise _no_login()
    message = mailer.reset(str(request.base_url), user["email"], password, user["name"])
    emailed, error = _email(user["email"], message, req.send_email)
    return {"password": password, "emailed": emailed, "email_error": error}


@admin.get("/smtp")
def smtp_get() -> dict:
    return mailer.public(mailer.settings())


@admin.put("/smtp")
def smtp_put(req: SmtpSettings) -> dict:
    try:
        cfg = mailer.update(req.model_dump(exclude={"password", "clear_password"}), req.password,
                            req.clear_password)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return mailer.public(cfg)


@admin.post("/smtp/test")
def smtp_test(req: EmailTo) -> dict:
    cfg = mailer.settings()
    if not mailer.configured(cfg):
        raise HTTPException(409, mailer.NOT_SET_UP)
    to = req.to.strip()
    if not auth.valid_email(to):
        raise HTTPException(400, "Enter a valid email address.")
    try:
        mailer.send(cfg, to, *mailer.smtp_check())
    except Exception as exc:
        raise HTTPException(502, _send_failed(exc))
    return {"sent": True}


app.include_router(admin)  # after its routes are defined: including copies them


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
