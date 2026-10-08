"""Server-install login: one email + password account created on first start, signed session cookies.

Files live in <data dir>/auth/ (0600): secret.key signs cookies, users.json holds the scrypt hash and
setup-code.txt holds the one-time code needed to create the first login (removed once it is used).
Deleting users.json and restarting the service brings the setup page back with a new code.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
from pathlib import Path
from typing import Optional

from . import jobs

COOKIE = "cmyk_session"
SESSION_DAYS = 7
MIN_PASSWORD = 10
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MAX_FAILURES, FAILURE_WINDOW = 5, 15 * 60
SCRYPT = {"n": 2 ** 14, "r": 8, "p": 1}

_lock = threading.RLock()
_failures: dict[str, list[float]] = {}


def _dir() -> Path:
    d = jobs.JOBS_DIR.parent / "auth"
    d.mkdir(mode=0o700, exist_ok=True)
    return d


def _write_private(path: Path, text: str) -> None:
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    tmp.replace(path)


def _secret() -> bytes:
    f = _dir() / "secret.key"
    with _lock:
        if not f.exists():
            _write_private(f, secrets.token_hex(32))
        return bytes.fromhex(f.read_text(encoding="utf-8").strip())


def _users() -> dict:
    f = _dir() / "users.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}


def _hash(password: str, salt: Optional[bytes] = None) -> str:
    salt = salt or secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, dklen=32, **SCRYPT)
    return f"scrypt${salt.hex()}${dk.hex()}"


def _verify(password: str, stored: str) -> bool:
    salt = bytes.fromhex(stored.split("$")[1])
    return hmac.compare_digest(_hash(password, salt).encode(), stored.encode())


def _norm_code(code: str) -> bytes:
    return "".join(ch for ch in code.lower() if ch in "0123456789abcdef").encode()


# -- first start -------------------------------------------------------------
def needs_setup() -> bool:
    return not _users()


def setup_code() -> str:
    """One-time code that proves whoever creates the first login can read the server log or data folder."""
    f = _dir() / "setup-code.txt"
    with _lock:
        if not f.exists():
            _write_private(f, "-".join(secrets.token_hex(2) for _ in range(3)) + "\n")
        return f.read_text(encoding="utf-8").strip()


def create_account(code: str, email: str, password: str) -> str:
    """Raises FileExistsError (already set up), PermissionError (wrong code) or ValueError (bad input)."""
    email = email.strip().lower()
    with _lock:
        if not needs_setup():
            raise FileExistsError
        if not hmac.compare_digest(_norm_code(code), _norm_code(setup_code())):
            raise PermissionError
        if len(email) > 254 or not EMAIL_RE.match(email):
            raise ValueError("Enter a valid email address.")
        if len(password) < MIN_PASSWORD:
            raise ValueError(f"Use at least {MIN_PASSWORD} characters for the password.")
        users = {email: {"password": _hash(password), "created": time.strftime("%Y-%m-%d %H:%M")}}
        _write_private(_dir() / "users.json", json.dumps(users))
        (_dir() / "setup-code.txt").unlink(missing_ok=True)
    return email


# -- sign-in -----------------------------------------------------------------
def check_login(email: str, password: str) -> bool:
    user = _users().get(email.strip().lower())
    if not user:
        _hash(password)  # same cost as a real check, so timing doesn't reveal the email
        return False
    return _verify(password, user["password"])


def _sign(payload: str, pw_hash: str) -> str:
    # The password hash is part of the signature, so replacing the account ends every session.
    return hmac.new(_secret(), f"{payload}|{pw_hash}".encode(), hashlib.sha256).hexdigest()


def make_session(email: str) -> str:
    expires = int(time.time()) + SESSION_DAYS * 86400
    payload = base64.urlsafe_b64encode(f"{email}|{expires}".encode()).decode()
    return f"{payload}.{_sign(payload, _users()[email]['password'])}"


def session_email(token: Optional[str]) -> Optional[str]:
    if not token or "." not in token:
        return None
    payload, sig = token.rsplit(".", 1)
    try:
        email, expires = base64.urlsafe_b64decode(payload.encode()).decode().rsplit("|", 1)
        if int(expires) < time.time():
            return None
    except (ValueError, binascii.Error, UnicodeDecodeError):
        return None
    user = _users().get(email)
    if not user or not hmac.compare_digest(sig.encode(), _sign(payload, user["password"]).encode()):
        return None
    return email


# -- brute-force brake (per client IP, in memory) -----------------------------
def throttled(ip: str) -> bool:
    now = time.time()
    with _lock:
        recent = [t for t in _failures.get(ip, []) if now - t < FAILURE_WINDOW]
        _failures[ip] = recent
        return len(recent) >= MAX_FAILURES


def record_failure(ip: str) -> None:
    with _lock:
        _failures.setdefault(ip, []).append(time.time())


def clear_failures(ip: str) -> None:
    with _lock:
        _failures.pop(ip, None)
