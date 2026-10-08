import time

import pytest
from fastapi.testclient import TestClient

from tests.make_fixtures import make_brochure, make_clean


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CMYK_DATA_DIR", str(tmp_path / "data"))
    import importlib
    from cmyk import auth, jobs
    importlib.reload(jobs)
    importlib.reload(auth)
    from cmyk import app as appmod
    importlib.reload(appmod)
    return TestClient(appmod.app, base_url="http://localhost:8000")


def run_job(client, path, profile="brochure"):
    with open(path, "rb") as f:
        job = client.post("/api/jobs", files={"file": ("test.pdf", f, "application/pdf")}).json()
    client.post(f"/api/jobs/{job['id']}/check", json={"profile_id": profile})
    for _ in range(100):
        j = client.get(f"/api/jobs/{job['id']}").json()
        if j["status"] in ("done", "error"):
            return j
        time.sleep(0.1)
    raise AssertionError("timeout")


def test_full_flow(client, tmp_path):
    make_brochure(tmp_path / "f.pdf")
    j = run_job(client, tmp_path / "f.pdf")
    assert j["status"] == "done"
    assert j["result"]["stages"]
    assert j["blockers"]  # unreviewed findings + manual checks

    png = client.get(f"/api/jobs/{j['id']}/pages/1.png?scale=1")
    assert png.status_code == 200 and png.content[:4] == b"\x89PNG"

    # sign-off blocked until everything is reviewed
    rev = j["review"]
    rev["outcome"] = "signed_off"
    assert client.put(f"/api/jobs/{j['id']}/review", json=rev).status_code == 409

    for it in j["result"]["issues"]:
        if it["severity"] != "info":
            rev["issues"][it["id"]] = {"state": "intentional", "note": "ok"}
    for mc in j["result"]["profile"]["manual_checks"]:
        rev["manual"][mc["id"]] = {"status": "correct", "comment": ""}
    rev["reviewer"] = "Tester"
    r = client.put(f"/api/jobs/{j['id']}/review", json=rev)
    assert r.status_code == 200 and r.json()["review"]["outcome"] == "signed_off"
    assert r.json()["review"]["outcome_at"]

    html = client.get(f"/api/jobs/{j['id']}/report.html").text
    assert "SIGNED OFF" in html and "Tester" in html and "data:image/png;base64" in html

    assert client.delete(f"/api/jobs/{j['id']}").json()["deleted"] is True
    assert client.get(f"/api/jobs/{j['id']}").status_code == 404


def test_rejects_non_pdf(client):
    r = client.post("/api/jobs", files={"file": ("x.pdf", b"not a pdf", "application/pdf")})
    assert r.status_code == 400


def test_refuses_foreign_host_and_origin(client, tmp_path):
    from fastapi.testclient import TestClient
    bad = TestClient(client.app, base_url="http://evil.example")
    assert bad.get("/api/profiles").status_code == 403
    r = client.post("/api/jobs/abc/check", json={}, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403


@pytest.fixture()
def server(client, monkeypatch):
    """A server install on cmyk.example.com (login on), sharing the client fixture's data dir."""
    import importlib
    from cmyk import app as appmod
    monkeypatch.setenv("CMYK_ALLOWED_HOSTS", "cmyk.example.com")
    importlib.reload(appmod)
    return lambda: TestClient(appmod.app, base_url="https://cmyk.example.com")


def test_local_run_has_no_login(client):
    assert client.get("/api/auth/state").json() == {"server": False, "setup": False, "email": None,
                                                    "role": None, "name": None}
    assert client.post("/api/auth/login", json={}).status_code == 404


def test_server_first_start_setup_then_sign_in(server, tmp_path):
    srv = server()
    r = srv.get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login.html"
    assert srv.get("/api/profiles").status_code == 401
    assert srv.get("/login.html").status_code == 200
    for asset in ("/brand/cormack-logo.svg", "/brand/background.jpg", "/fonts/jost.woff2"):
        assert srv.get(asset, follow_redirects=False).status_code == 200  # the sign-in page needs them
    assert srv.get("/settings.html", follow_redirects=False).status_code == 303
    assert srv.get("/brand/../app.js", follow_redirects=False).status_code == 303
    assert srv.get("/api/auth/state").json()["setup"] is True

    code = (tmp_path / "data" / "auth" / "setup-code.txt").read_text().strip()
    good = {"code": code, "email": " Studio@Example.com ", "password": "long enough pass"}
    assert srv.post("/api/auth/setup", json={**good, "code": "0000-0000-0000"}).status_code == 403
    assert srv.post("/api/auth/setup", json={**good, "password": "short"}).status_code == 400
    assert srv.post("/api/auth/setup", json={**good, "email": "nope"}).status_code == 400
    r = srv.post("/api/auth/setup", json={**good, "code": code.upper()})
    assert r.status_code == 200 and r.json()["email"] == "studio@example.com"
    assert "httponly" in r.headers["set-cookie"].lower() and "secure" in r.headers["set-cookie"].lower()
    assert srv.get("/api/profiles").status_code == 200
    assert srv.get("/api/auth/state").json() == {"server": True, "setup": False, "email": "studio@example.com",
                                                 "role": "admin", "name": ""}
    assert not (tmp_path / "data" / "auth" / "setup-code.txt").exists()
    assert srv.post("/api/auth/setup", json=good).status_code == 409

    other = server()
    assert other.post("/api/auth/login", json={"email": "studio@example.com", "password": "wrong"}).status_code == 401
    assert other.post("/api/auth/login", json={"email": "studio@example.com",
                                               "password": "long enough pass"}).status_code == 200
    assert other.get("/", follow_redirects=False).status_code == 200
    r = other.post("/api/jobs/abc/check", json={}, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    assert other.post("/api/auth/logout").status_code == 200
    assert other.get("/api/profiles").status_code == 401

    forged = server()
    tok = srv.cookies.get("cmyk_session")
    forged.cookies.set("cmyk_session", tok[:-1] + ("1" if tok[-1] == "0" else "0"))
    assert forged.get("/api/profiles").status_code == 401
    assert TestClient(srv.app, base_url="http://evil.example").get("/api/profiles").status_code == 403


def test_server_sign_in_is_throttled(server):
    srv = server()
    bad = {"email": "a@example.com", "password": "wrong password"}
    for _ in range(5):
        assert srv.post("/api/auth/login", json=bad).status_code == 401
    assert srv.post("/api/auth/login", json=bad).status_code == 429


def test_clean_pdf_only_needs_manual_checks(client, tmp_path):
    make_clean(tmp_path / "c.pdf")
    j = run_job(client, tmp_path / "c.pdf")
    assert all(i["severity"] == "info" for i in j["result"]["issues"])
    assert all("Manual check" in b for b in j["blockers"])
