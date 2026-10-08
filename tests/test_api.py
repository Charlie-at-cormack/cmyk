import time

import pytest
from fastapi.testclient import TestClient

from tests.make_fixtures import make_brochure, make_clean


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CMYK_DATA_DIR", str(tmp_path / "data"))
    import importlib
    from cmyk import jobs
    importlib.reload(jobs)
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


def test_server_install_needs_password(client, monkeypatch):
    import importlib
    from cmyk import app as appmod
    monkeypatch.setenv("CMYK_ALLOWED_HOSTS", "cmyk.example.com")
    monkeypatch.delenv("CMYK_PASSWORD", raising=False)
    with pytest.raises(RuntimeError):
        importlib.reload(appmod)


def test_server_host_with_login(client, monkeypatch):
    import importlib
    from cmyk import app as appmod
    monkeypatch.setenv("CMYK_ALLOWED_HOSTS", "cmyk.example.com")
    monkeypatch.setenv("CMYK_USER", "studio")
    monkeypatch.setenv("CMYK_PASSWORD", "s3cret pass")
    importlib.reload(appmod)
    srv = TestClient(appmod.app, base_url="https://cmyk.example.com")
    r = srv.get("/api/profiles")
    assert r.status_code == 401 and r.headers["www-authenticate"].startswith("Basic")
    assert srv.get("/api/profiles", auth=("studio", "wrong")).status_code == 401
    assert srv.get("/api/profiles", auth=("studio", "s3cret pass")).status_code == 200
    assert srv.get("/", auth=("studio", "s3cret pass")).status_code == 200
    r = srv.post("/api/jobs/abc/check", json={}, auth=("studio", "s3cret pass"),
                 headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    assert TestClient(appmod.app, base_url="http://evil.example").get(
        "/api/profiles", auth=("studio", "s3cret pass")).status_code == 403


def test_clean_pdf_only_needs_manual_checks(client, tmp_path):
    make_clean(tmp_path / "c.pdf")
    j = run_job(client, tmp_path / "c.pdf")
    assert all(i["severity"] == "info" for i in j["result"]["issues"])
    assert all("Manual check" in b for b in j["blockers"])
