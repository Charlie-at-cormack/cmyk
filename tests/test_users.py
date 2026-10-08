import json
import smtplib
import stat
from types import SimpleNamespace

import pytest

from tests.test_api import client, server  # noqa: F401  (pytest fixtures)

ADMIN, ADMIN_PW = "boss@example.com", "boss password 1"
SMTP = {"host": "smtp.example.com", "port": 587, "security": "starttls", "username": "mailer",
        "from_email": "cmyk@example.com", "from_name": "CMYK"}


def setup_admin(server, tmp_path):
    """The first admin, made through the real first-start setup."""
    srv = server()
    assert srv.get("/api/auth/state").json()["setup"] is True
    code = (tmp_path / "data" / "auth" / "setup-code.txt").read_text().strip()
    assert srv.post("/api/auth/setup", json={"code": code, "email": ADMIN, "password": ADMIN_PW}).status_code == 200
    return srv


def sign_in(server, email, password):
    c = server()
    r = c.post("/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return c


@pytest.fixture()
def outbox(monkeypatch):
    """Replaces smtplib.SMTP; box.sent collects (smtp, message), box.fail makes connecting raise."""
    box = SimpleNamespace(sent=[], fail=None)

    class FakeSMTP:
        def __init__(self, host, port, timeout=None, **kw):
            if box.fail:
                raise box.fail
            self.host, self.port, self.timeout, self.kw, self.calls = host, port, timeout, kw, []

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def starttls(self, context=None):
            self.calls.append(("starttls", context))

        def login(self, user, password):
            self.calls.append(("login", user, password))

        def send_message(self, msg):
            box.sent.append((self, msg))

    box.cls = FakeSMTP
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    return box


def test_generated_passwords():
    from cmyk import auth
    pw = {auth.generate_password() for _ in range(50)}
    assert len(pw) == 50 and all(len(p) == 16 for p in pw)
    assert not set("".join(pw)) & set("0Oo1lI")
    assert set("".join(pw)) <= set(auth.PASSWORD_CHARS)


def test_local_run_has_no_admin(client):
    assert client.get("/api/admin/users").status_code == 404
    assert client.post("/api/admin/users", json={"email": "a@example.com"}).status_code == 404
    assert client.put("/api/admin/users/a@example.com", json={"name": "A"}).status_code == 404
    assert client.delete("/api/admin/users/a@example.com").status_code == 404
    assert client.post("/api/admin/users/a@example.com/reset", json={}).status_code == 404
    assert client.get("/api/admin/smtp").status_code == 404
    assert client.put("/api/admin/smtp", json=SMTP).status_code == 404
    assert client.post("/api/admin/smtp/test", json={"to": "a@example.com"}).status_code == 404
    assert client.post("/api/auth/password", json={"current": "x", "new": "y" * 12}).status_code == 404


def test_login_without_role_is_admin(server, tmp_path):
    srv = setup_admin(server, tmp_path)
    users_file = tmp_path / "data" / "auth" / "users.json"
    record = json.loads(users_file.read_text())[ADMIN]
    assert record["role"] == "admin" and record["created_by"] is None
    # what the single-login version wrote
    users_file.write_text(json.dumps({ADMIN: {"password": record["password"], "created": record["created"]}}))

    assert srv.get("/api/auth/state").json() == {"server": True, "setup": False, "email": ADMIN,
                                                 "role": "admin", "name": ""}
    r = srv.get("/api/admin/users")
    assert r.status_code == 200
    assert r.json()["users"] == [{"email": ADMIN, "name": "", "role": "admin", "created": record["created"],
                                  "created_by": None}]
    assert srv.put(f"/api/admin/users/{ADMIN}", json={"role": "user"}).status_code == 409


def test_admin_adds_lists_updates_resets_and_removes(server, tmp_path):
    srv = setup_admin(server, tmp_path)
    r = srv.post("/api/admin/users", json={"email": " Jane@Example.com ", "name": " Jane Doe "})
    assert r.status_code == 200
    body = r.json()
    assert body["user"]["email"] == "jane@example.com" and body["user"]["name"] == "Jane Doe"
    assert body["user"]["role"] == "user" and body["user"]["created_by"] == ADMIN and body["user"]["created"]
    assert len(body["password"]) == 16 and body["emailed"] is False and body["email_error"] is None
    jane_pw = body["password"]

    assert srv.post("/api/admin/users", json={"email": "JANE@example.com"}).status_code == 409
    assert srv.post("/api/admin/users", json={"email": "nope"}).json()["detail"] == "Enter a valid email address."
    assert srv.post("/api/admin/users", json={"email": "x@example.com", "role": "owner"}).status_code == 400
    assert srv.post("/api/admin/users", json={"email": "amy@example.com", "role": "admin"}).status_code == 200

    listing = srv.get("/api/admin/users").json()
    assert [u["email"] for u in listing["users"]] == ["amy@example.com", ADMIN, "jane@example.com"]
    assert listing["smtp_configured"] is False
    assert all("password" not in u for u in listing["users"])

    jane = sign_in(server, "jane@example.com", jane_pw)
    assert jane.get("/api/auth/state").json() == {"server": True, "setup": False, "email": "jane@example.com",
                                                  "role": "user", "name": "Jane Doe"}
    assert jane.get("/api/profiles").status_code == 200

    r = srv.put("/api/admin/users/JANE%40example.com", json={"name": "Jane D", "role": "admin"})
    assert r.status_code == 200 and r.json()["name"] == "Jane D" and r.json()["role"] == "admin"
    assert jane.get("/api/admin/users").status_code == 200  # role changes apply without signing in again
    assert srv.put("/api/admin/users/jane@example.com", json={"role": "user"}).json()["role"] == "user"
    assert srv.put("/api/admin/users/jane@example.com", json={"role": "boss"}).status_code == 400
    assert srv.put("/api/admin/users/who@example.com", json={"name": "X"}).status_code == 404

    r = srv.post("/api/admin/users/jane@example.com/reset")  # no body: send_email defaults to false
    assert r.status_code == 200 and r.json()["emailed"] is False and r.json()["email_error"] is None
    new_pw = r.json()["password"]
    assert new_pw != jane_pw and len(new_pw) == 16
    assert jane.get("/api/profiles").status_code == 401  # her sessions ended with the old password
    old_login = {"email": "jane@example.com", "password": jane_pw}
    assert server().post("/api/auth/login", json=old_login).status_code == 401
    jane = sign_in(server, "jane@example.com", new_pw)
    assert srv.post(f"/api/admin/users/{ADMIN}/reset", json={}).json()["detail"] == \
        "Use Change password for your own login."
    assert srv.post("/api/admin/users/who@example.com/reset", json={}).status_code == 404

    assert srv.delete(f"/api/admin/users/{ADMIN}").json()["detail"] == "You can't remove your own login."
    assert srv.delete("/api/admin/users/who@example.com").status_code == 404
    assert srv.delete("/api/admin/users/jane@example.com").json() == {"deleted": True}
    assert jane.get("/api/profiles").status_code == 401
    assert jane.get("/api/auth/state").json()["email"] is None
    assert [u["email"] for u in srv.get("/api/admin/users").json()["users"]] == ["amy@example.com", ADMIN]


def test_non_admin_is_refused(server, tmp_path):
    srv = setup_admin(server, tmp_path)
    pw = srv.post("/api/admin/users", json={"email": "user@example.com"}).json()["password"]
    user = sign_in(server, "user@example.com", pw)
    calls = [("get", "/api/admin/users", None), ("post", "/api/admin/users", {"email": "b@example.com"}),
             ("put", f"/api/admin/users/{ADMIN}", {"role": "user"}), ("delete", f"/api/admin/users/{ADMIN}", None),
             ("post", f"/api/admin/users/{ADMIN}/reset", {}), ("get", "/api/admin/smtp", None),
             ("put", "/api/admin/smtp", SMTP), ("post", "/api/admin/smtp/test", {"to": "a@example.com"})]
    for method, url, body in calls:
        kw = {"json": body} if body is not None else {}
        r = user.request(method.upper(), url, **kw)
        assert r.status_code == 403 and r.json() == {"detail": "Admins only"}, (method, url)
        assert server().request(method.upper(), url, **kw).status_code == 401, (method, url)
    assert user.request("POST", "/api/admin/users", content=b"{not json").status_code == 403
    assert len(srv.get("/api/admin/users").json()["users"]) == 2


def test_always_one_admin(server, tmp_path):
    from cmyk import auth
    srv = setup_admin(server, tmp_path)
    r = srv.put(f"/api/admin/users/{ADMIN}", json={"role": "user"})
    assert r.status_code == 409 and r.json()["detail"] == "Keep at least one admin."
    with pytest.raises(auth.LastAdminError):  # unreachable over HTTP (that admin would be removing itself)
        auth.remove_user(ADMIN)

    srv.post("/api/admin/users", json={"email": "two@example.com", "role": "admin"})
    assert srv.put(f"/api/admin/users/{ADMIN}", json={"role": "user"}).json()["role"] == "user"
    assert srv.get("/api/admin/users").status_code == 403
    assert srv.get("/api/auth/state").json()["role"] == "user"
    auth.remove_user(ADMIN)
    with pytest.raises(auth.LastAdminError):
        auth.update_user("two@example.com", role="user")


def test_change_own_password(server, tmp_path):
    srv = setup_admin(server, tmp_path)
    old_cookie = srv.cookies.get("cmyk_session")
    r = srv.post("/api/auth/password", json={"current": "wrong password", "new": "brand new password"})
    assert r.status_code == 403 and r.json()["detail"] == "Current password is not right."
    assert srv.post("/api/auth/password", json={"current": ADMIN_PW, "new": "short"}).status_code == 400
    assert server().post("/api/auth/password", json={"current": ADMIN_PW, "new": "x" * 12}).status_code == 401

    r = srv.post("/api/auth/password", json={"current": ADMIN_PW, "new": "brand new password"})
    assert r.status_code == 200 and r.json() == {"email": ADMIN}
    assert "secure" in r.headers["set-cookie"].lower() and srv.cookies.get("cmyk_session") != old_cookie
    assert srv.get("/api/profiles").status_code == 200  # the reissued cookie works

    stale = server()
    stale.cookies.set("cmyk_session", old_cookie)
    assert stale.get("/api/profiles").status_code == 401
    assert server().post("/api/auth/login", json={"email": ADMIN, "password": ADMIN_PW}).status_code == 401
    sign_in(server, ADMIN, "brand new password")

    for _ in range(5):  # the sign-in above cleared earlier failures
        assert srv.post("/api/auth/password", json={"current": "nope", "new": "x" * 12}).status_code == 403
    assert srv.post("/api/auth/password", json={"current": "nope", "new": "x" * 12}).status_code == 429


def test_smtp_settings_never_return_the_password(server, tmp_path):
    srv = setup_admin(server, tmp_path)
    assert srv.get("/api/admin/smtp").json() == {"host": "", "port": 587, "security": "starttls", "username": "",
                                                 "from_email": "", "from_name": "CMYK", "password_set": False}
    r = srv.put("/api/admin/smtp", json={**SMTP, "password": "smtp secret"})
    assert r.status_code == 200 and r.json() == {**SMTP, "password_set": True}
    assert srv.get("/api/admin/smtp").json() == {**SMTP, "password_set": True}
    f = tmp_path / "data" / "auth" / "smtp.json"
    assert json.loads(f.read_text())["password"] == "smtp secret"
    assert stat.S_IMODE(f.stat().st_mode) == 0o600

    for blank in (None, ""):  # a blank password keeps the saved one
        r = srv.put("/api/admin/smtp", json={**SMTP, "from_name": "Studio", "password": blank})
        assert r.json()["password_set"] is True and r.json()["from_name"] == "Studio"
    assert json.loads(f.read_text())["password"] == "smtp secret"
    assert srv.put("/api/admin/smtp", json={**SMTP, "password": "newer"}).json()["password_set"] is True
    assert json.loads(f.read_text())["password"] == "newer"
    assert srv.put("/api/admin/smtp", json={**SMTP, "clear_password": True}).json()["password_set"] is False
    assert json.loads(f.read_text())["password"] == ""

    for bad in ({"from_name": "CMYK\r\nBcc: x@evil.example"}, {"host": "smtp.example.com\n"},
                {"password": "pw\r\nRCPT TO:<x@evil.example>"}, {"username": "a\nb"},
                {"security": "tls"}, {"port": 0}, {"port": 70000}, {"from_email": "not an email"}):
        assert srv.put("/api/admin/smtp", json={**SMTP, **bad}).status_code == 400, bad
    assert srv.get("/api/admin/smtp").json()["from_name"] == "CMYK"
    # with no host, email is simply switched off and the from address isn't checked
    assert srv.put("/api/admin/smtp", json={**SMTP, "host": "", "from_email": ""}).status_code == 200
    assert srv.get("/api/admin/users").json()["smtp_configured"] is False


def test_test_email_welcome_and_reset_emails(server, tmp_path, outbox):
    srv = setup_admin(server, tmp_path)
    r = srv.post("/api/admin/smtp/test", json={"to": "me@example.com"})
    assert r.status_code == 409 and r.json()["detail"] == "Email is not set up."
    r = srv.post("/api/admin/users", json={"email": "early@example.com", "send_email": True})
    assert r.status_code == 200 and r.json()["emailed"] is False and r.json()["email_error"] == "Email is not set up."

    srv.put("/api/admin/smtp", json={**SMTP, "password": "smtp secret"})
    assert srv.get("/api/admin/users").json()["smtp_configured"] is True
    assert srv.post("/api/admin/smtp/test", json={"to": "not an email"}).status_code == 400
    assert srv.post("/api/admin/smtp/test", json={"to": " me@example.com "}).json() == {"sent": True}
    smtp, msg = outbox.sent[-1]
    assert (smtp.host, smtp.port, smtp.timeout) == ("smtp.example.com", 587, 20)
    assert smtp.calls[0][0] == "starttls" and smtp.calls[0][1] is not None
    assert smtp.calls[1] == ("login", "mailer", "smtp secret")
    assert msg["To"] == "me@example.com" and msg["Subject"] == "CMYK test email"
    assert msg["From"] == "CMYK <cmyk@example.com>" and msg["Date"] and msg["Message-ID"]
    assert "settings work" in msg.get_content()

    r = srv.post("/api/admin/users", json={"email": "new@example.com", "name": "Sam", "send_email": True})
    assert r.status_code == 200 and r.json()["emailed"] is True and r.json()["email_error"] is None
    _, msg = outbox.sent[-1]
    text = msg.get_content()
    assert msg["To"] == "new@example.com" and msg["Subject"] == "Your CMYK login"
    assert r.json()["password"] in text and "new@example.com" in text and "https://cmyk.example.com/" in text
    assert "Hello Sam," in text and "Settings > Your account" in text

    r = srv.post("/api/admin/users/new@example.com/reset", json={"send_email": True})
    assert r.status_code == 200 and r.json()["emailed"] is True
    _, msg = outbox.sent[-1]
    assert msg["To"] == "new@example.com" and msg["Subject"] == "Your CMYK password was reset"
    assert r.json()["password"] in msg.get_content()
    assert len(outbox.sent) == 3

    # no username: no login; "none": no STARTTLS
    srv.put("/api/admin/smtp", json={**SMTP, "username": "", "security": "none", "port": 25})
    srv.post("/api/admin/smtp/test", json={"to": "me@example.com"})
    assert outbox.sent[-1][0].calls == [] and outbox.sent[-1][0].port == 25


def test_ssl_security_uses_smtp_ssl(server, tmp_path, outbox, monkeypatch):
    monkeypatch.setattr(smtplib, "SMTP_SSL", outbox.cls)
    srv = setup_admin(server, tmp_path)
    srv.put("/api/admin/smtp", json={**SMTP, "security": "ssl", "port": 465})
    assert srv.post("/api/admin/smtp/test", json={"to": "me@example.com"}).json() == {"sent": True}
    smtp, _ = outbox.sent[-1]
    assert smtp.port == 465 and smtp.kw["context"] is not None
    assert [c[0] for c in smtp.calls] == ["login"]


def test_email_failure_keeps_the_new_login(server, tmp_path, outbox):
    srv = setup_admin(server, tmp_path)
    srv.put("/api/admin/smtp", json=SMTP)
    outbox.fail = ConnectionRefusedError(61, "Connection refused")
    r = srv.post("/api/admin/users", json={"email": "kept@example.com", "send_email": True})
    assert r.status_code == 200 and r.json()["emailed"] is False
    assert r.json()["email_error"].startswith("Could not send: ConnectionRefusedError:")
    assert "kept@example.com" in [u["email"] for u in srv.get("/api/admin/users").json()["users"]]
    sign_in(server, "kept@example.com", r.json()["password"])

    r = srv.post("/api/admin/users/kept@example.com/reset", json={"send_email": True})
    assert r.status_code == 200 and r.json()["emailed"] is False and r.json()["email_error"]
    r = srv.post("/api/admin/smtp/test", json={"to": "me@example.com"})
    assert r.status_code == 502
    assert r.json()["detail"] == "Could not send: ConnectionRefusedError: [Errno 61] Connection refused"
    assert outbox.sent == []
