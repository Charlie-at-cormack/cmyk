"""Deterministic preflight checks.

All geometry returned to the UI is in *page space*: PDF points, origin at the
top-left of the visible page (the CropBox), y pointing down. That is exactly the
coordinate system of the rendered preview, so the browser can draw overlays by
just scaling.
"""
from __future__ import annotations

import hashlib
import re
from typing import Callable, Optional

import pymupdf

MM = 72.0 / 25.4

STAGES = [
    ("setup", "Document setup"),
    ("bleed", "Bleed & trim"),
    ("margins", "Margins & safe area"),
    ("images", "Images"),
    ("colour", "Colour"),
    ("fonts", "Fonts"),
    ("print", "Print marks & overprint"),
]

MAX_ISSUES_PER_STAGE_PAGE = 25
_CMYK_PROCESS = {"cyan", "magenta", "yellow", "black", "none", "all"}


def mm(pt: float) -> float:
    return pt / MM


class _Issues:
    def __init__(self) -> None:
        self.items: list[dict] = []
        self._seen: dict[str, int] = {}

    def add(self, stage: str, severity: str, title: str, *, page: Optional[int] = None,
            detail: str = "", measured: str = "", expected: str = "",
            bbox: Optional[list] = None) -> None:
        # Stable id (hash of what the issue *is*) so reviewer decisions survive a re-run.
        key = f"{stage}|{page}|{title}|{measured}|{[round(v) for v in bbox] if bbox else ''}"
        base = hashlib.md5(key.encode("utf-8")).hexdigest()[:10]
        self._seen[base] = self._seen.get(base, 0) + 1
        iid = base if self._seen[base] == 1 else f"{base}{self._seen[base]}"
        self.items.append({
            "id": iid, "stage": stage, "severity": severity,
            "page": page, "title": title, "detail": detail,
            "measured": measured, "expected": expected,
            "bbox": [round(v, 2) for v in bbox] if bbox else None,
        })


def _page_geometry(doc: pymupdf.Document, page: pymupdf.Page) -> dict:
    """Return all boxes converted to page space (see module docstring)."""
    cb = page.cropbox  # in MediaBox-top-left coordinates
    ox, oy = cb.x0, cb.y0

    def conv(r: pymupdf.Rect) -> list:
        return [r.x0 - ox, r.y0 - oy, r.x1 - ox, r.y1 - oy]

    def defined(key: str) -> bool:
        return doc.xref_get_key(page.xref, key)[0] != "null"

    trim_defined = defined("TrimBox")
    bleed_defined = defined("BleedBox")
    return {
        "width": page.rect.width, "height": page.rect.height,
        "media": conv(page.mediabox),
        "page": [0.0, 0.0, page.rect.width, page.rect.height],
        "trim": conv(page.trimbox) if trim_defined else None,
        "bleed": conv(page.bleedbox) if bleed_defined else None,
        "rotation": page.rotation,
    }


def _scan_objects(doc: pymupdf.Document) -> tuple[set, bool]:
    """Document-wide scan for spot colours and overprint flags (one pass over objects)."""
    spots: set[str] = set()
    overprint = False
    sep = re.compile(r"/Separation\s*/([^\s/\[\]<>()]+)")
    devn = re.compile(r"/DeviceN\s*\[([^\]]*)\]")
    op = re.compile(r"/(?:OP|op)\s+true")
    for xref in range(1, doc.xref_length()):
        try:
            obj = doc.xref_object(xref, compressed=True)
        except Exception:
            continue
        if "/Separation" in obj or "/DeviceN" in obj:
            for m in sep.finditer(obj):
                spots.add(m.group(1))
            for m in devn.finditer(obj):
                for name in re.findall(r"/([^\s/\[\]<>()]+)", m.group(1)):
                    spots.add(name)
        if ("/OP" in obj or "/op" in obj) and op.search(obj):
            overprint = True

    def clean(n: str) -> str:
        return re.sub(r"#([0-9A-Fa-f]{2})", lambda m: chr(int(m.group(1), 16)), n)

    return {clean(s) for s in spots if clean(s).lower() not in _CMYK_PROCESS}, overprint


def analyse(path: str, profile: dict,
            progress: Optional[Callable[[int, int], None]] = None) -> dict:
    doc = pymupdf.open(path)
    issues = _Issues()
    total = doc.page_count
    page_infos: list[dict] = []
    first = None
    sizes = set()
    fonts_seen: dict[str, dict] = {}
    any_text = False

    bleed_req = float(profile["bleed_mm"] or 0)
    safe_req = float(profile["safe_margin_mm"] or 0)

    for i in range(total):
        page = doc[i]
        pno = i + 1
        geo = _page_geometry(doc, page)
        page_infos.append({"n": pno, **geo})
        sizes.add((round(geo["width"]), round(geo["height"])))
        trim = geo["trim"]
        trim_rect = trim or geo["page"]

        # ---- setup
        if geo["rotation"]:
            issues.add("setup", "review", "Page is rotated",
                       page=pno, detail="Overlays may not line up on rotated pages (not supported yet).")
        if trim is None and (bleed_req > 0 or safe_req > 0):
            issues.add("setup", "review", "No TrimBox defined", page=pno,
                       detail="Bleed cannot be measured; the visible page edge is used as the trim edge "
                              "for the margin check.")
        if profile.get("trim_mm") and trim:
            w, h = mm(trim[2] - trim[0]), mm(trim[3] - trim[1])
            ew, eh = profile["trim_mm"]
            if abs(w - ew) > 0.5 or abs(h - eh) > 0.5:
                issues.add("setup", "fail", "Trim size differs from profile", page=pno, bbox=trim,
                           measured=f"{w:.1f} x {h:.1f} mm", expected=f"{ew} x {eh} mm")

        # ---- bleed & trim
        if bleed_req > 0 and trim is not None:
            b = geo["bleed"] or geo["page"]
            sides = {"left": trim[0] - b[0], "top": trim[1] - b[1],
                     "right": b[2] - trim[2], "bottom": b[3] - trim[3]}
            worst_name, worst = min(sides.items(), key=lambda kv: kv[1])
            if mm(worst) < bleed_req - 0.1:
                issues.add("bleed", "fail", f"Bleed too small ({worst_name} edge)", page=pno,
                           bbox=b, measured=f"{mm(worst):.1f} mm", expected=f"{bleed_req:g} mm minimum",
                           detail="Bleed measured from TrimBox to " +
                                  ("BleedBox." if geo["bleed"] else "the page edge (no BleedBox defined)."))
        elif bleed_req == 0 and trim is not None:
            gap = min(trim[0], trim[1], geo["width"] - trim[2], geo["height"] - trim[3])
            if mm(gap) > 0.5 and profile["crop_marks"] != "required":
                issues.add("bleed", "review", "Page is larger than trim but no bleed is required",
                           page=pno, measured=f"{mm(gap):.1f} mm extra",
                           expected="no bleed", bbox=geo["page"])

        # ---- print marks (heuristic: page larger than trim + bleed implies a slug area)
        if trim is not None:
            gap = min(trim[0], trim[1], geo["width"] - trim[2], geo["height"] - trim[3])
            slug = mm(gap) - bleed_req
            has_slug = slug > 1.0
            if profile["crop_marks"] == "required" and not has_slug:
                issues.add("print", "warning", "Crop marks probably missing", page=pno,
                           measured=f"{max(slug, 0):.1f} mm beyond bleed",
                           expected="slug area for crop marks",
                           detail="Heuristic: the page is not larger than trim + bleed. "
                                  "Mark detection will be refined against real press PDFs.")
            elif profile["crop_marks"] == "forbidden" and has_slug:
                issues.add("print", "review", "Crop marks / slug area present", page=pno,
                           measured=f"{slug:.1f} mm beyond bleed", expected="no crop marks")
        elif profile["crop_marks"] == "required":
            issues.add("print", "review", "Crop marks cannot be assessed without a TrimBox", page=pno)

        # ---- images
        n_img = 0
        for info in page.get_image_info(xrefs=True):
            bbox = info["bbox"]
            bw, bh = bbox[2] - bbox[0], bbox[3] - bbox[1]
            w, h = info.get("width", 0), info.get("height", 0)
            if w <= 1 or h <= 1 or bw <= 0 or bh <= 0:
                continue
            dpi = min(w / (bw / 72.0), h / (bh / 72.0))
            n_img += 1
            if n_img > MAX_ISSUES_PER_STAGE_PAGE * 4:
                break
            cs_name = str(info.get("cs-name", ""))
            ncomp = info.get("colorspace", 0)
            where = list(bbox)
            if dpi < profile["dpi_warn"]:
                sev = "fail" if dpi < profile["dpi_fail"] else "warning"
                issues.add("images", sev, "Low image resolution", page=pno, bbox=where,
                           measured=f"{dpi:.0f} DPI", expected=f"{profile['dpi_warn']} DPI minimum",
                           detail=f"{w} x {h} px placed at {mm(bw):.0f} x {mm(bh):.0f} mm.")
            is_rgb = ncomp == 3 or "RGB" in cs_name.upper()
            if is_rgb and profile["rgb"] != "ignore":
                issues.add("colour", "fail" if profile["rgb"] == "fail" else "warning",
                           "RGB image", page=pno, bbox=where, measured=cs_name or "RGB",
                           expected="CMYK", detail=f"{w} x {h} px image.")

        # ---- fonts + live text
        for f in page.get_fonts(full=True):
            xref, ext, ftype, base = f[0], f[1], f[2], f[3]
            name = re.sub(r"^[A-Z]{6}\+", "", base or f[4] or "unknown")
            embedded = ftype == "Type3" or (ext != "n/a" and xref > 0)
            if name not in fonts_seen:
                fonts_seen[name] = {"page": pno, "embedded": embedded}
        if not any_text and page.get_text("text").strip():
            any_text = True

        # ---- margins / safe area
        if safe_req > 0:
            inner = [trim_rect[0] + safe_req * MM, trim_rect[1] + safe_req * MM,
                     trim_rect[2] - safe_req * MM, trim_rect[3] - safe_req * MM]
            count = 0
            for block in page.get_text("dict")["blocks"]:
                for line in block.get("lines", []):
                    text = "".join(s["text"] for s in line["spans"]).strip()
                    if not text:
                        continue
                    lb = line["bbox"]
                    outside_trim = (lb[2] < trim_rect[0] or lb[0] > trim_rect[2]
                                    or lb[3] < trim_rect[1] or lb[1] > trim_rect[3])
                    if outside_trim:
                        continue  # slug text (crop-mark labels etc.)
                    if lb[0] < inner[0] or lb[1] < inner[1] or lb[2] > inner[2] or lb[3] > inner[3]:
                        count += 1
                        if count > MAX_ISSUES_PER_STAGE_PAGE:
                            continue
                        dist = min(lb[0] - trim_rect[0], lb[1] - trim_rect[1],
                                   trim_rect[2] - lb[2], trim_rect[3] - lb[3])
                        issues.add("margins", "warning", "Text too close to trim", page=pno,
                                   bbox=lb, measured=f"{mm(dist):.1f} mm",
                                   expected=f"{safe_req:g} mm minimum", detail=f"“{text[:60]}”")
            if count > MAX_ISSUES_PER_STAGE_PAGE:
                issues.add("margins", "warning", f"{count - MAX_ISSUES_PER_STAGE_PAGE} more lines too close to trim",
                           page=pno, detail="Only the first lines are listed individually.")

        if progress:
            progress(pno, total)

    if len(sizes) > 1:
        issues.add("setup", "review", "Pages are not all the same size",
                   detail=", ".join(f"{mm(w):.0f} x {mm(h):.0f} mm" for w, h in sorted(sizes)))

    # fonts (document level)
    sev_f = profile["fonts_embedded"]
    for name, f in sorted(fonts_seen.items()):
        if not f["embedded"] and sev_f != "ignore":
            issues.add("fonts", "fail" if sev_f == "fail" else "warning", "Font not embedded",
                       page=f["page"], measured=name, expected="embedded")
    if profile["live_text"] == "review" and any_text:
        issues.add("fonts", "review", "Live (unoutlined) text present",
                   detail="Signage checklist: type should be outlined if the artwork isn't to size. "
                          "Fonts: " + ", ".join(sorted(fonts_seen)[:8]))

    # spot colours + overprint (document level)
    spots, overprint = _scan_objects(doc)
    mx = profile["max_spot_colours"]
    if spots:
        listing = ", ".join(sorted(spots))
        if mx is not None and len(spots) > mx:
            issues.add("colour", "fail", "Too many spot colours", measured=f"{len(spots)}: {listing}",
                       expected=f"{mx} maximum",
                       detail="Extra spot colours only allowed if cost is signed off by the client.")
        else:
            issues.add("colour", "info", "Spot colours used", measured=listing,
                       detail="Foils, debosses etc. should be labelled as spot colours.")
    if overprint and profile["overprint"] != "ignore":
        issues.add("print", "fail" if profile["overprint"] == "fail" else "warning",
                   "Overprint enabled", measured="overprint flag set in graphics state",
                   expected="no overprint",
                   detail="Document-wide detection. Confirm in Acrobat 'Output Preview' if unsure.")

    meta = {
        "pages": total, "pdf_version": f"{getattr(doc, 'metadata', {}).get('format', '')}".replace("PDF ", ""),
        "title": (doc.metadata or {}).get("title", ""),
    }
    doc.close()
    return _assemble(issues.items, page_infos, meta, profile)


def _assemble(items: list, page_infos: list, meta: dict, profile: dict) -> dict:
    order = {"fail": 0, "warning": 1, "review": 2, "info": 3}
    items.sort(key=lambda x: (order[x["severity"]], x["page"] or 0))
    stages = []
    for sid, label in STAGES:
        mine = [x for x in items if x["stage"] == sid]
        counts = {k: sum(1 for x in mine if x["severity"] == k) for k in order}
        status = "pass"
        for k in ("fail", "warning", "review"):
            if counts[k]:
                status = k
                break
        stages.append({"id": sid, "label": label, "status": status, "counts": counts})
    return {"profile": profile, "meta": meta, "pages": page_infos,
            "stages": stages, "issues": items}
