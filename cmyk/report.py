"""Self-contained HTML report (print it to PDF from the browser, or save the HTML)."""
from __future__ import annotations

import base64
import html
from datetime import datetime

import pymupdf

from .jobs import Job, blockers

MM = 72 / 25.4
OUTCOME = {"draft": "Draft (not signed off)", "signed_off": "SIGNED OFF",
           "changes_required": "CHANGES REQUIRED"}
STATE = {"open": "Not reviewed", "confirmed": "Confirmed issue",
         "intentional": "Marked intentional", "ignored": "Ignored"}


def _thumb(doc: pymupdf.Document, page_no: int, bbox: list) -> str:
    page = doc[page_no - 1]
    pad = 14
    clip = pymupdf.Rect(bbox[0] - pad, bbox[1] - pad, bbox[2] + pad, bbox[3] + pad) & page.rect
    if clip.is_empty:
        return ""
    # clip coordinates are page space (CropBox origin), which is what get_pixmap expects
    pix = page.get_pixmap(clip=clip, dpi=110)
    data = base64.b64encode(pix.tobytes("png")).decode()
    return f'<img class="thumb" src="data:image/png;base64,{data}" alt="">'


def build(job: Job) -> str:
    e = html.escape
    res, rev = job.result, job.review
    prof = res["profile"]
    blk = blockers(job)
    outcome = rev.get("outcome", "draft")
    doc = pymupdf.open(job.pdf)
    thumbs = 0

    rows = []
    for it in res["issues"]:
        if it["severity"] == "info":
            continue
        st = rev["issues"].get(it["id"], {})
        img = ""
        if it["bbox"] and it["page"] and thumbs < 60:
            img = _thumb(doc, it["page"], it["bbox"])
            thumbs += 1
        rows.append(f"""<tr class="{it['severity']}"><td>{e(it['severity'].upper())}</td>
<td>{it['page'] or '&ndash;'}</td><td><b>{e(it['title'])}</b><br>{e(it['detail'])}
<div class="m">{e(it['measured'])}{' &rarr; expected ' + e(it['expected']) if it['expected'] else ''}</div></td>
<td>{e(STATE.get(st.get('state', 'open'), ''))}<br><i>{e(st.get('note', ''))}</i></td><td>{img}</td></tr>""")

    marks = []
    for m in rev.get("markers", []):
        img = _thumb(doc, m["page"], m["bbox"]) if thumbs < 60 else ""
        thumbs += 1
        marks.append(f"<tr><td>{m['page']}</td><td>{e(m.get('comment', ''))}</td><td>{img}</td></tr>")

    manual = []
    for mc in prof.get("manual_checks", []):
        st = rev["manual"].get(mc["id"], {})
        label = {"correct": "Done &ndash; Correct", "incorrect": "Done &ndash; Incorrect"}.get(
            st.get("status", ""), "Not checked")
        manual.append(f"<tr><td>{e(mc['label'])}</td><td>{label}</td><td>{e(st.get('comment', ''))}</td></tr>")

    stage_rows = "".join(
        f"<tr><td>{e(s['label'])}</td><td class='{s['status']}'>{s['status'].upper()}</td></tr>"
        for s in res["stages"])
    doc.close()

    warn = ""
    if outcome == "signed_off" and blk:
        warn = "<p class='warn'>Warning: sign-off recorded but blockers exist: " + e("; ".join(blk)) + "</p>"
    when = rev.get("outcome_at") or datetime.now().strftime("%Y-%m-%d %H:%M")
    checked = datetime.fromtimestamp(job.meta.get("checked_at", 0)).strftime("%Y-%m-%d %H:%M") \
        if job.meta.get("checked_at") else "-"

    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>CMYK report - {e(job.meta.get('filename', ''))}</title><style>
body{{font:14px/1.45 -apple-system,Segoe UI,sans-serif;margin:32px auto;max-width:1000px;color:#1b1b1f}}
h1{{margin:0 0 4px}} h2{{margin-top:28px;border-bottom:2px solid #ddd;padding-bottom:4px}}
.outcome{{display:inline-block;padding:6px 14px;border-radius:6px;font-weight:700;color:#fff;background:#777}}
.outcome.signed_off{{background:#1f8a3b}} .outcome.changes_required{{background:#c62828}}
table{{border-collapse:collapse;width:100%}} td,th{{border:1px solid #ddd;padding:6px 8px;vertical-align:top;text-align:left}}
tr.fail td:first-child,td.fail{{color:#c62828;font-weight:700}} tr.warning td:first-child,td.warning{{color:#b26a00;font-weight:700}}
tr.review td:first-child,td.review{{color:#6a1b9a;font-weight:700}} td.pass{{color:#1f8a3b;font-weight:700}}
.m{{color:#555;font-size:12px}} .thumb{{max-width:220px;border:1px solid #ccc}} .warn{{color:#c62828;font-weight:700}}
.meta td:first-child{{width:200px;color:#555}}
</style></head><body>
<h1>Preflight report</h1>
<p><span class="outcome {outcome}">{e(OUTCOME.get(outcome, outcome))}</span></p>{warn}
<table class="meta">
<tr><td>File</td><td>{e(job.meta.get('filename', ''))}</td></tr>
<tr><td>Pages / size</td><td>{res['meta']['pages']} pages &middot; {job.meta.get('size_mb', 0):.1f} MB</td></tr>
<tr><td>Profile</td><td>{e(prof['name'])} (bleed {prof['bleed_mm']} mm, safe area {prof['safe_margin_mm']} mm,
min {prof['dpi_warn']} DPI, max spot colours {prof['max_spot_colours']})</td></tr>
<tr><td>Checked</td><td>{checked}</td></tr>
<tr><td>Reviewer</td><td>{e(rev.get('reviewer') or '-')}</td></tr>
<tr><td>Outcome recorded</td><td>{e(str(when))}</td></tr></table>
{('<h2>Reviewer comment</h2><p>' + e(rev['comment']) + '</p>') if rev.get('comment') else ''}
<h2>Checklist</h2><table>{stage_rows}</table>
<h2>Manual checks</h2><table><tr><th>Check</th><th>Result</th><th>Comment</th></tr>
{''.join(manual) or '<tr><td colspan=3>None required for this profile</td></tr>'}</table>
<h2>Findings</h2><table><tr><th>Severity</th><th>Page</th><th>Finding</th><th>Review</th><th>Preview</th></tr>
{''.join(rows) or '<tr><td colspan=5>No findings</td></tr>'}</table>
<h2>Reviewer markers</h2><table><tr><th>Page</th><th>Comment</th><th>Preview</th></tr>
{''.join(marks) or '<tr><td colspan=3>None</td></tr>'}</table>
</body></html>"""
