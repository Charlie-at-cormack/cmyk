import pytest

from cmyk.engine import analyse, load_profile
from tests.make_fixtures import make_brochure, make_clean


@pytest.fixture(scope="session")
def pdfs(tmp_path_factory):
    d = tmp_path_factory.mktemp("pdfs")
    make_brochure(d / "flawed.pdf")
    make_clean(d / "clean.pdf")
    return d


def titles(result, sev=None):
    return [i["title"] for i in result["issues"] if sev is None or i["severity"] == sev]


def test_flawed_brochure_findings(pdfs):
    r = analyse(str(pdfs / "flawed.pdf"), load_profile("brochure"))
    t = titles(r)
    assert "Low image resolution" in t
    assert "RGB image" in t
    assert "Text too close to trim" in t
    assert "Overprint enabled" in t
    assert any("PANTONE 123 C" in i["measured"] for i in r["issues"] if i["title"] == "Spot colours used")
    # exactly one image is low-res, and the issue box lies on the page
    low = [i for i in r["issues"] if i["title"] == "Low image resolution"]
    assert len(low) == 1 and 45 < float(low[0]["measured"].split()[0]) < 60
    w, h = r["pages"][0]["width"], r["pages"][0]["height"]
    x0, y0, x1, y1 = low[0]["bbox"]
    assert 0 <= x0 < x1 <= w and 0 <= y0 < y1 <= h


def test_text_issue_distance(pdfs):
    r = analyse(str(pdfs / "flawed.pdf"), load_profile("brochure"))
    near = [i for i in r["issues"] if i["title"] == "Text too close to trim"]
    assert len(near) == 1
    assert 1.0 < float(near[0]["measured"].split()[0]) < 2.0


def test_clean_brochure_passes(pdfs):
    r = analyse(str(pdfs / "clean.pdf"), load_profile("brochure"))
    bad = [i for i in r["issues"] if i["severity"] in ("fail", "warning", "review")]
    assert bad == [], bad


def test_bleed_too_small(pdfs, tmp_path):
    make_brochure(tmp_path / "b.pdf", bleed_mm=1)
    r = analyse(str(tmp_path / "b.pdf"), load_profile("brochure"))
    assert any(i["title"].startswith("Bleed too small") for i in r["issues"])


def test_signage_profile_flags_crop_marks_and_live_text(pdfs):
    r = analyse(str(pdfs / "flawed.pdf"), load_profile("signage_above_a0"))
    t = titles(r)
    assert "Crop marks / slug area present" in t
    assert "Live (unoutlined) text present" in t
    assert "Too many spot colours" in t
