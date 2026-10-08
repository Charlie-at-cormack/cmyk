"""Server-install logins: email + password accounts with an "admin" or "user" role, signed session cookies.

Files live in <data dir>/auth/ (0600): secret.key signs cookies, users.json holds each login's scrypt hash,
role and name, and setup-code.txt holds the one-time code needed to create the first (admin) login, removed
once it is used. Admins then add, change and remove the other logins; there is always at least one admin.
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
import string
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
ROLES = ("admin", "user")
# Generated passwords are read out or copied from an email, so leave out look-alike characters.
PASSWORD_CHARS = "".join(c for c in string.ascii_letters + string.digits if c not in "0Oo1lI")
PASSWORD_LENGTH = 16

_lock = threading.RLock()
_failures: dict[str, list[float]] = {}


class LastAdminError(Exception):
    """The change would leave nobody able to manage the logins."""


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


def _save_users(users: dict) -> None:
    _write_private(_dir() / "users.json", json.dumps(users))


def _hash(password: str, salt: Optional[bytes] = None) -> str:
    salt = salt or secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, dklen=32, **SCRYPT)
    return f"scrypt${salt.hex()}${dk.hex()}"


def _verify(password: str, stored: str) -> bool:
    salt = bytes.fromhex(stored.split("$")[1])
    return hmac.compare_digest(_hash(password, salt).encode(), stored.encode())


def norm_email(email: str) -> str:
    return email.strip().lower()


def valid_email(email: str) -> bool:
    return len(email) <= 254 and bool(EMAIL_RE.fullmatch(email))


def generate_password() -> str:
    return "".join(secrets.choice(PASSWORD_CHARS) for _ in range(PASSWORD_LENGTH))


def _role(record: dict) -> str:
    return record.get("role", "admin")  # a login from before roles existed was the only (admin) one


def _record(password: str, role: str, name: str = "", created_by: Optional[str] = None) -> dict:
    return {"password": _hash(password), "role": role, "name": name,
            "created": time.strftime("%Y-%m-%d %H:%M"), "created_by": created_by}


def _public(email: str, record: dict) -> dict:
    return {"email": email, "name": record.get("name", ""), "role": _role(record),
            "created": record.get("created"), "created_by": record.get("created_by")}


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
    email = norm_email(email)
    with _lock:
        if not needs_setup():
            raise FileExistsError
        if not hmac.compare_digest(_norm_code(code), _norm_code(setup_code())):
            raise PermissionError
        if not valid_email(email):
            raise ValueError("Enter a valid email address.")
        if len(password) < MIN_PASSWORD:
            raise ValueError(f"Use at least {MIN_PASSWORD} characters for the password.")
        _save_users({email: _record(password, "admin")})
        (_dir() / "setup-code.txt").unlink(missing_ok=True)
    return email


# -- managing logins -----------------------------------------------------------
def get_user(email: str) -> Optional[dict]:
    """The login without its password hash, or None."""
    email = norm_email(email)
    record = _users().get(email)
    return _public(email, record) if record else None


def list_users() -> list[dict]:
    return [_public(email, record) for email, record in sorted(_users().items())]


def _admins(users: dict) -> int:
    return sum(_role(record) == "admin" for record in users.values())


def _check_role(role: str) -> None:
    if role not in ROLES:
        raise ValueError("Choose admin or user for the role.")


def add_user(email: str, name: str, role: str, created_by: str) -> tuple[dict, str]:
    """Returns the new login and its generated password. Raises ValueError or FileExistsError."""
    email = norm_email(email)
    if not valid_email(email):
        raise ValueError("Enter a valid email address.")
    _check_role(role)
    password = generate_password()
    record = _record(password, role, name.strip(), created_by)
    with _lock:
        users = _users()
        if email in users:
            raise FileExistsError
        users[email] = record
        _save_users(users)
    return _public(email, record), password


def update_user(email: str, name: Optional[str] = None, role: Optional[str] = None) -> dict:
    """Changes the given fields. Raises KeyError (unknown), ValueError (bad role) or LastAdminError."""
    email = norm_email(email)
    with _lock:
        users = _users()
        record = users[email]
        if role is not None:
            _check_role(role)
            if role != "admin" and _role(record) == "admin" and _admins(users) == 1:
                raise LastAdminError
            record["role"] = role
        if name is not None:
            record["name"] = name.strip()
        _save_users(users)
    return _public(email, record)


def remove_user(email: str) -> None:
    """Raises KeyError (unknown) or LastAdminError. The login's sessions stop working at once."""
    email = norm_email(email)
    with _lock:
        users = _users()
        if _role(users[email]) == "admin" and _admins(users) == 1:
            raise LastAdminError
        del users[email]
        _save_users(users)


def _store_password(email: str, password: str) -> dict:
    hashed = _hash(password)  # outside the lock: scrypt is deliberately slow
    with _lock:
        users = _users()
        users[email]["password"] = hashed
        _save_users(users)
    return _public(email, users[email])


def reset_password(email: str) -> tuple[dict, str]:
    """Gives the login a new generated password, which also ends its sessions. Raises KeyError."""
    email = norm_email(email)
    if email not in _users():
        raise KeyError(email)
    password = generate_password()
    return _store_password(email, password), password


def change_password(email: str, current: str, new: str) -> None:
    """Raises PermissionError (current password wrong) or ValueError (new one too short)."""
    if not check_login(email, current):
        raise PermissionError
    if len(new) < MIN_PASSWORD:
        raise ValueError(f"Use at least {MIN_PASSWORD} characters for the password.")
    _store_password(norm_email(email), new)


# -- sign-in -----------------------------------------------------------------
def check_login(email: str, password: str) -> bool:
    user = _users().get(norm_email(email))
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
