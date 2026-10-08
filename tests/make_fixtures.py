"""Generate small synthetic PDFs that exercise the checks (no client artwork needed)."""
from __future__ import annotations

import sys
from pathlib import Path

import pymupdf

MM = 72 / 25.4

_FONT_CANDIDATES = [
    "/System/Library/Fonts/Supplemental/Arial.ttf",            # macOS
    "/Library/Fonts/Arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",         # Linux
    "C:/Windows/Fonts/arial.ttf",                              # Windows
]


def _find_font() -> str:
    for f in _FONT_CANDIDATES:
        if Path(f).exists():
            return f
    raise RuntimeError("No TrueType font found for the fixture; add a path to _FONT_CANDIDATES")


def make_brochure(path: str | Path, *, bleed_mm: float = 3, slug_mm: float = 8) -> None:
    """A5 trim with a deliberately flawed page: low-res RGB image, text near trim,
    a spot colour, an overprint flag."""
    tw, th = 148 * MM, 210 * MM
    off = (bleed_mm + slug_mm) * MM
    doc = pymupdf.open()
    page = doc.new_page(width=tw + 2 * off, height=th + 2 * off)
    trim = pymupdf.Rect(off, off, off + tw, off + th)
    b = bleed_mm * MM
    page.set_trimbox(trim)
    page.set_bleedbox(pymupdf.Rect(trim.x0 - b, trim.y0 - b, trim.x1 + b, trim.y1 + b))

    # text: one fine, one too close to the trim edge (1.5 mm)
    page.insert_text((trim.x0 + 20 * MM, trim.y0 + 30 * MM), "Comfortable headline", fontsize=18)
    page.insert_text((trim.x0 + 1.5 * MM, trim.y0 + 100 * MM), "Text hugging the trim edge", fontsize=10)

    # RGB image, 100x100 px placed at 50x50 mm (~51 dpi)
    rgb = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 100, 100), False)
    rgb.set_rect(rgb.irect, (200, 60, 60))
    page.insert_image(pymupdf.Rect(trim.x0 + 20 * MM, trim.y0 + 120 * MM,
                                   trim.x0 + 70 * MM, trim.y0 + 170 * MM), pixmap=rgb)
    # CMYK image, 900x900 px at 50x50 mm (~457 dpi) - should pass
    cmyk = pymupdf.Pixmap(pymupdf.csCMYK, pymupdf.IRect(0, 0, 900, 900), False)
    cmyk.set_rect(cmyk.irect, (0, 100, 100, 0))
    page.insert_image(pymupdf.Rect(trim.x0 + 80 * MM, trim.y0 + 120 * MM,
                                   trim.x0 + 130 * MM, trim.y0 + 170 * MM), pixmap=cmyk)

    # a spot colour + an overprint graphics state, as raw objects
    sep = doc.get_new_xref()
    doc.update_object(sep, "[/Separation /PANTONE#20123#20C /DeviceCMYK "
                           "<< /FunctionType 2 /Domain [0 1] /C0 [0 0 0 0] /C1 [0 1 1 0] /N 1 >>]")
    gs = doc.get_new_xref()
    doc.update_object(gs, "<< /Type /ExtGState /OP true /op true >>")
    kind, val = doc.xref_get_key(page.xref, "Resources")
    res = int(val.split()[0]) if kind == "xref" else page.xref
    doc.xref_set_key(res, "ColorSpace", f"<</CS99 {sep} 0 R>>")
    doc.xref_set_key(res, "ExtGState", f"<</GS99 {gs} 0 R>>")
    doc.save(str(path))


def make_clean(path: str | Path) -> None:
    """Passes the brochure profile (apart from manual checks)."""
    tw, th = 148 * MM, 210 * MM
    off = 11 * MM
    doc = pymupdf.open()
    page = doc.new_page(width=tw + 2 * off, height=th + 2 * off)
    trim = pymupdf.Rect(off, off, off + tw, off + th)
    b = 3 * MM
    page.set_trimbox(trim)
    page.set_bleedbox(pymupdf.Rect(trim.x0 - b, trim.y0 - b, trim.x1 + b, trim.y1 + b))
    page.insert_font(fontname="emb", fontfile=_find_font())  # embedded, unlike base-14 Helvetica
    page.insert_text((trim.x0 + 20 * MM, trim.y0 + 30 * MM), "All good here", fontsize=18, fontname="emb")
    cmyk = pymupdf.Pixmap(pymupdf.csCMYK, pymupdf.IRect(0, 0, 900, 900), False)
    cmyk.set_rect(cmyk.irect, (100, 0, 0, 0))
    page.insert_image(pymupdf.Rect(trim.x0 + 20 * MM, trim.y0 + 60 * MM,
                                   trim.x0 + 70 * MM, trim.y0 + 110 * MM), pixmap=cmyk)
    doc.save(str(path))


if __name__ == "__main__":
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "testpdfs")
    out.mkdir(parents=True, exist_ok=True)
    make_brochure(out / "flawed_brochure.pdf")
    make_clean(out / "clean_brochure.pdf")
    print("wrote", out)
