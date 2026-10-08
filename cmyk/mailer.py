"""Outgoing email (server install): SMTP settings an admin saves, and the few plain-text messages CMYK sends.

Settings live in <data dir>/auth/smtp.json (0600) next to the logins. The SMTP password has to be stored as
it is, because the mail server needs it back; it is never sent to the browser.
"""
from __future__ import annotations

import json
import smtplib
import ssl
import threading
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid
from pathlib import Path
from typing import Optional

from . import auth

DEFAULTS = {"host": "", "port": 587, "security": "starttls", "username": "",
            "from_email": "", "from_name": "CMYK"}
SECURITY = ("starttls", "ssl", "none")
TIMEOUT = 20
NOT_SET_UP = "Email is not set up."

_lock = threading.Lock()


# -- settings ----------------------------------------------------------------
def _file() -> Path:
    return auth._dir() / "smtp.json"


def settings() -> dict:
    """Saved settings over the defaults, password included: never return this to the browser as it is."""
    f = _file()
    saved = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
    return {**DEFAULTS, "password": "", **saved}


def public(cfg: dict) -> dict:
    return {**{k: cfg[k] for k in DEFAULTS}, "password_set": bool(cfg.get("password"))}


def configured(cfg: dict) -> bool:
    return bool(cfg["host"] and cfg["from_email"])


def update(fields: dict, password: Optional[str] = None, clear_password: bool = False) -> dict:
    """Saves DEFAULTS-shaped fields. An empty password keeps the saved one. Raises ValueError."""
    texts = [v for v in (*fields.values(), password) if isinstance(v, str)]
    if any("\r" in v or "\n" in v for v in texts):  # these end up in SMTP commands and mail headers
        raise ValueError("Line breaks are not allowed in the email settings.")
    cfg = {k: v.strip() if isinstance(v, str) else v for k, v in fields.items()}
    if cfg["security"] not in SECURITY:
        raise ValueError("Choose starttls, ssl or none for the security.")
    if not 1 <= cfg["port"] <= 65535:
        raise ValueError("Use a port between 1 and 65535.")
    if cfg["host"] and not auth.valid_email(cfg["from_email"]):
        raise ValueError("Enter a valid email address to send from.")
    with _lock:
        cfg["password"] = "" if clear_password else settings()["password"]
        if password:
            cfg["password"] = password
        auth._write_private(_file(), json.dumps(cfg))
    return cfg


# -- sending -----------------------------------------------------------------
def send(cfg: dict, to: str, subject: str, body: str) -> None:
    msg = EmailMessage()
    msg["From"] = formataddr((cfg["from_name"], cfg["from_email"]))
    msg["To"] = to
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    # The sender's domain, not the server's host name, which can mean a slow DNS lookup.
    msg["Message-ID"] = make_msgid(domain=cfg["from_email"].rsplit("@", 1)[-1])
    msg.set_content(body)
    context = ssl.create_default_context()  # smtplib's own default doesn't check the certificate
    if cfg["security"] == "ssl":
        smtp = smtplib.SMTP_SSL(cfg["host"], cfg["port"], timeout=TIMEOUT, context=context)
    else:
        smtp = smtplib.SMTP(cfg["host"], cfg["port"], timeout=TIMEOUT)
    with smtp:
        if cfg["security"] == "starttls":
            smtp.starttls(context=context)
        if cfg["username"]:
            smtp.login(cfg["username"], cfg["password"])
        smtp.send_message(msg)


# -- messages: (subject, body) -----------------------------------------------
def _hello(name: str) -> str:
    return f"Hello {name}," if name else "Hello,"


def _details(url: str, email: str, password: str) -> str:
    return (f"Sign in at: {url}\nEmail: {email}\nPassword: {password}\n\n"
            "Please change this password after you sign in (Settings > Your account).\n")


def welcome(url: str, email: str, password: str, name: str = "") -> tuple[str, str]:
    return "Your CMYK login", (
        f"{_hello(name)}\n\nAn account has been created for you on CMYK, the Cormack PDF preflight tool.\n\n"
        + _details(url, email, password))


def reset(url: str, email: str, password: str, name: str = "") -> tuple[str, str]:
    return "Your CMYK password was reset", (
        f"{_hello(name)}\n\nYour password for CMYK, the Cormack PDF preflight tool, has been reset.\n\n"
        + _details(url, email, password))


def smtp_check() -> tuple[str, str]:
    return "CMYK test email", "Hello,\n\nThis is a test email from CMYK. Your email settings work.\n"
