# Cormack Media Yield Kontrol (CMYK): Local PDF Preflight Tool — Development Plan

## 1. Goal

Build a local, offline-first prepress checking application for print-ready PDFs.

It runs on an Apple Silicon Mac (target: M3, 32 GB unified memory). A Python service analyses PDFs locally, and the user works in a browser-based interface on localhost. Large PDFs (around 150 MB) stay on the local machine and are never uploaded to a remote server.

## 2. Proposed Architecture

### Local Python engine (FastAPI)

- Opens and parses PDFs
- Reads page, trim, bleed and media boxes
- Measures margins and safe areas
- Inspects images and effective DPI
- Checks colour spaces (RGB, CMYK, spot)
- Inspects embedded fonts
- Extracts text and runs spelling checks
- Detects objects outside expected boundaries
- Detects measurable overlaps and clipping
- Renders page previews
- Produces issue coordinates for the UI
- Generates the final preflight report

### Browser interface

Runs locally at `http://localhost:8000`. The browser is only the interface; the PDF is processed by the local Python application.

- HTML, CSS / Tailwind, JavaScript
- PDF.js or rendered page images for previews
- SVG or HTML overlay layer for issue markers

### Optional local AI

A local vision-language model can provide subjective visual checks that deterministic rules can't:

- Suspicious visual overlaps
- Poor readability over images
- Unusual alignment
- Inconsistent spacing
- Layout anomalies
- Visual hierarchy problems
- Possible design mistakes needing human review

The AI must **not** replace deterministic measurements (bleed, trim, dimensions, DPI). A suitable quantised vision model should run locally on the M3/32 GB machine; model selection is to be benchmarked during development.

## 3. User Workflow

1. **Start application** — launching it starts the local Python service, opens the default browser and loads the interface. No internet connection required for normal operation.
2. **Select PDF** — drag and drop or choose from disk. The file stays local. The app records filename, file size, page count, page dimensions, PDF version and detected trim/bleed information.
3. **Initial scan** — the engine runs the deterministic checks and builds an issue index. The UI shows progress (e.g. "Analysing page 14 of 48"). Results are classified as **Pass, Warning, Fail, Needs review**.

## 4. Guided Preflight Pipeline

Results are presented as a guided checklist rather than one large error list.

| Stage | Checks |
|---|---|
| 1. Document setup | Page dimensions, orientation, page consistency, MediaBox, CropBox, TrimBox, BleedBox |
| 2. Bleed & trim | Required bleed exists; artwork reaches bleed where expected; nothing accidentally extends beyond intended boundaries; trim dimensions correct |
| 3. Margins & safe area | Text, logos and important artwork distance from trim; configurable safe-area threshold. Example: *Warning — text is 2.1 mm from trim; configured minimum is 3 mm.* |
| 4. Images | Effective DPI; very low-resolution images; oversized source images where useful; missing/corrupt resources. Example: *Warning — effective resolution 184 DPI; recommended minimum 300 DPI.* |
| 5. Colour | RGB objects/images, CMYK, spot colours, unexpected colour spaces, optional rich-black rules. Configurable, as printers differ |
| 6. Fonts | Embedded / non-embedded fonts, substitution risks, problematic font resources |
| 7. Text & spelling | Extract text and run an offline spelling check. UK English dictionary; custom client/project dictionary; ignore word; add approved word; mark as intentional |
| 8. Objects, clipping & overlaps | Objects outside page boundaries, clipped content, text/object intersections, overlapping bounding boxes. Overlap can be intentional, so many findings are *Needs review* rather than automatic fails |
| 9. AI visual review (optional) | Local vision model reviews rendered pages for accidental-looking overlaps, awkward spacing, poor contrast/readability, inconsistent elements, alignment mistakes and other layout anomalies. Always presented as suggestions requiring human confirmation |
| 10. Manual checks | Two checks the tool can't automate, confirmed by the operator: **Is Misrep updated?** and **Custom cover artwork reversed?** (check with printer / printer's artwork guide). Each is marked Done – Correct or Done – Incorrect with a comment (see Manual Checks below) |
| 11. Final review | Summary, e.g. *Preflight complete — 43 passed, 3 warnings, 1 failure.* The operator reviews and resolves findings before sign-off |

### Manual checks (Stage 10)

Some items on the design team's artwork checklist can't be detected from the PDF. They appear as their own checklist step so they can't be forgotten:

1. **Is Misrep updated?**
2. **Custom cover artwork reversed?** — separately supplied cover artwork usually needs to be reversed; check with the printer / the printer's artwork guide.

For each manual check the operator:

- Sets a status: **Not checked** (default) → **Done – Correct** or **Done – Incorrect**
- Adds an optional **comment** (required when marking Incorrect)
- Can change the status later; the change is recorded

The app stores the status, comment, reviewer name and date/time with the job. Both items always appear in the final report, including any left as Not checked.

- Manual checks belong to the profile, so they only show when relevant (for example both appear for the Brochures, Books, Magazines profile) and the list can be extended with further manual items later.
- Final sign-off is blocked while a manual check is Not checked (configurable per profile).
- A manual check marked Incorrect counts as a Fail in the summary count.

## 5. Interactive PDF Preview

The preview is one of the main features of the application.

**Page viewer** — current page with zoom and page navigation, with technical guide overlays for trim, bleed and safe area.

**Issue overlays** — Python returns issue coordinates in PDF space; the front end converts them to preview coordinates and draws interactive overlays. Examples:

- Dashed box around a low-DPI image
- Dashed boundary around text too close to trim
- Highlight around a spelling issue
- Highlight around a suspected overlap

**Interactive tooltip** — clicking an issue shows details, e.g. *Image resolution 184 DPI · Recommended 300 DPI minimum · Severity: Warning*.

**Two-way navigation**

- Clicking an item in the issue list opens the correct page, zooms/scrolls to the area and highlights the affected object.
- Clicking an overlay on the page selects the issue, shows its details and highlights it in the checklist.

**Overlay filters** — toggle categories on/off: Bleed, Safe area, DPI, Colour, Fonts, Spelling, Overlaps, AI review.

## 6. Human Review

Not every warning is an error. Each finding supports:

- Confirm issue
- Mark as intentional
- Ignore once
- Ignore for document
- Add to approved dictionary/rules
- Add reviewer note

This is particularly important for overlaps and AI-generated findings.

## 7. Final Report

Contents:

- Document information
- Date/time checked
- Overall result
- Passed checks, warnings, failures
- Items marked intentional
- Manual check results (status, comment, reviewer, date/time)
- Page number for each issue
- Description
- Measured and expected values where applicable
- Optional preview thumbnail
- Reviewer notes

Exports: PDF report, HTML report, JSON report (for automation/integration).

## 8. Offline-First Design

Normal preflight needs no external service.

```
PDF → Python engine → deterministic checks → local AI (optional) → browser UI → report
```

Benefits: no 150 MB uploads, no VPS bandwidth, works without internet, faster access to large local files, sensitive client artwork stays on the workstation, no per-request AI cost.

## 9. Development Phases

**Phase 1 — MVP** (deterministic core)

- Local FastAPI application
- Drag-and-drop PDF selection
- PDF metadata
- Page preview
- Trim/BleedBox inspection
- Safe-area checking
- Image DPI checks
- Font checks
- Basic colour-space checks
- Text extraction
- Basic spelling
- Issue list
- Pass / Warning / Fail states

**Phase 2 — Interactive inspection**

- Interactive page overlays
- Dashed issue boxes
- Tooltips
- Zoom/navigation
- Issue-to-page navigation
- Overlay filters
- Reviewer actions
- Final report

**Phase 3 — Advanced preflight**

- Better overlap detection
- More sophisticated PDF object analysis
- Configurable printer profiles
- Project presets
- Client-specific dictionaries
- Saved preflight configurations

**Phase 4 — Local AI**

Add and benchmark a local vision-language model. AI complements, not replaces, the deterministic engine. Evaluate accuracy, false-positive rate, processing time per page, memory consumption, model size, quantisation, and whether it adds enough value to be enabled by default.

## 10. Printer / Preflight Profiles

Avoid hard-coding one set of rules. Example profile, "Commercial Print": bleed 3 mm, safe area 3 mm, minimum image DPI 300, RGB as warning.

Profile configuration:

- Expected page dimensions
- Required bleed
- Safe margin
- Minimum DPI
- Allowed colour spaces
- Allowed spot colours
- Font requirements
- Rich-black rules
- Spelling language
- Manual checks required (e.g. Misrep updated, custom cover reversed)
- AI review enabled/disabled

Profiles can later be created per printer or client.

### Settings side panel

A side panel lets the user define the parameters supplied by the print house (bleed, margins, etc.). It edits the profile; the same profile drives the checks.

- **Profile selector** — pick a saved profile; duplicate, save as new, import/export as JSON to share between machines
- **Page and trim** — trim size (mm) and orientation; presets (A4/A5/DL) plus custom
- **Bleed** — required bleed in mm; optionally separate top/bottom/inside/outside values (e.g. spine)
- **Margins and safe area** — safe distance from trim; optional larger margin on the spine/binding side
- **Images** — minimum DPI (warning) and hard-fail DPI
- **Colour** — allowed colour spaces; RGB as pass/warn/fail; spot colours allowed or not; optional rich-black limits (total ink coverage)
- **Fonts** — must be embedded; whether outlined text is allowed
- **Spelling** — language and project dictionary
- **AI review** — on/off

Behaviour:

- Changing bleed or margin redraws the trim, bleed and safe-area guides on the preview immediately
- Changing a rule re-runs only the affected check, using cached metadata and renders
- Show values detected in the PDF next to profile values to expose mismatches (e.g. "PDF has 3 mm bleed, profile needs 5 mm")
- Per-job overrides without changing the saved profile
- The profile used is stored in the report

Build order: start with a plain form backed by a `profile.json` in the MVP, with every threshold read from the profile (nothing hard-coded). Add the polished panel, save/load and import/export in Phase 2.

## 11. Performance Strategy

For large PDFs:

- Don't load the entire document into unnecessary in-memory representations
- Process pages incrementally where possible
- Cache rendered previews
- Run expensive checks in background workers
- Stream progress updates to the browser
- Let deterministic checks finish before optional AI analysis
- Process AI pages sequentially or with carefully limited concurrency to protect unified memory
- Delete temporary renders when the job is closed

The UI must stay responsive while analysis runs.

## 12. Python Components to Evaluate

Benchmark suitable libraries for each job rather than committing early:

- PDF parsing and geometry
- PDF rendering
- Image extraction
- Font/resource inspection
- Colour-space inspection
- Text extraction
- Spell checking
- Report generation
- Local model inference

PyMuPDF is a strong candidate for initial parsing/rendering experiments, but complex prepress features should be validated against representative press PDFs before the architecture is locked.

## 13. Packaging for macOS

The finished product should behave like a normal application even though its interface is browser-based.

```
Double-click app → local Python service starts → browser opens → user selects PDF
```

Packaging includes the required Python dependencies so the operator needn't configure Python. The local server binds only to the loopback interface unless LAN access is intentionally enabled.

## 14. Security & Privacy

Client artwork may be confidential:

- Bind the web service to localhost only
- Don't send PDFs externally by default
- Keep AI inference local
- Avoid analytics that transmit document information
- Store temporary files in an application-controlled temporary directory
- Delete temporary data after processing/closing a job
- Clearly label any future feature that requires internet/API access

## 15. Longer-Term Possibilities

Once the local application is stable, the same core engine could support:

- Plesk-hosted web version
- Team/server processing
- Automated watched folders / hot-folder preflight
- Batch checking and multi-PDF drag-and-drop
- Printer-specific profiles and client/project presets
- Preflight history
- Comparison against previous artwork versions
- API access
- Integration with an agency production workflow

The Python analysis engine should therefore remain separate from the browser UI and storage layer.

## 16. Deployment & Updates via Git

The whole project lives in a Git repository and is deployed and updated on multiple machines from the terminal.

Initial deploy (per machine):

```bash
git clone git@github.com:<owner>/cmyk.git ~/Apps/cmyk
cd ~/Apps/cmyk
./install.sh     # creates .venv, installs pinned dependencies, downloads models if needed
./run.sh         # starts uvicorn on 127.0.0.1:8000 and opens the browser
```

Update:

```bash
cd ~/Apps/cmyk
git pull
./install.sh     # re-syncs dependencies; fast if nothing changed
```

(Can be wrapped as `./update.sh` or a `cmyk update` command, optionally checking for a newer version.)

Release: commit to `main`, tag a version (`git tag v1.2.0 && git push --tags`). Machines needing stability check out a tag (`git fetch --tags && git checkout v1.2.0`) instead of tracking `main`.

Things to get right:

- **Private repo access** — use a read-only deploy key (or fine-grained token) per machine, not a personal login
- **Pin dependencies** — commit a lockfile so every machine gets the same library versions (important for PDF libraries such as PyMuPDF)
- **Keep large files out of git** — models and test PDFs are downloaded (with checksum) or use Git LFS
- **Keep config and data out of the repo** — profiles, dictionaries and ignore lists live in `~/Library/Application Support/CMYK/` so `git pull` never overwrites local settings
- **Python version** — consistent across machines (`uv`/`pyenv`, `.python-version`)
- **Migrations** — run settings/database migrations at startup so updates don't break existing data

Alternative for non-technical operators: package a built `.app`/`.dmg` (PyInstaller or Briefcase), publish it as a GitHub Release and have a small updater download it. Git remains the source of truth.

## Getting started (development)

The starter project is in this folder.

```bash
./install.sh                 # creates .venv and installs dependencies
./run.sh                     # starts the service on http://localhost:8000 and opens the browser
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest   # engine and API tests (generate synthetic PDFs, no client files needed)
```

Layout:

- `cmyk/engine/` - checks (`checks.py`) and profile loading (`profiles.py`); no web code, so it can be reused later
- `cmyk/app.py` - FastAPI service (localhost only); `cmyk/jobs.py` job storage; `cmyk/report.py` HTML report; `cmyk/auth.py` server-install logins (first-start setup, users and roles, sessions); `cmyk/mailer.py` SMTP settings and emails
- `cmyk/static/` - dashboard UI (left sidebar with checklist and sign-off, page preview with overlays, findings), welcome screen, sign-in and settings pages; `brand/` and `fonts/` hold the Cormack logo, photo and Jost font (SIL OFL)
- `profiles/` - the three print profiles from the design team's artwork checklist (editable JSON)
- `tests/` - fixtures and tests
- `deploy/` - server install on Plesk behind nginx, with a login (`deploy/PLESK.md`)

Implemented so far: upload, page geometry (trim/bleed boxes), image DPI and colour space, fonts, safe-area text check, spot colours, overprint, crop-mark heuristic, preview with overlays, issue review states, reviewer markers, manual checks, sign-off and HTML report.
Not yet built: spelling, overlap/clipping detection, QR code size, vector RGB detection, AI review, PDF export of the report.

## Recommended Starting Point

Start with a small technical proof of concept using real press-ready PDFs. It only needs to prove five things:

1. Open a large PDF reliably.
2. Read page/trim/bleed geometry.
3. Find images and calculate effective DPI.
4. Render a page preview.
5. Return an issue bounding box that the browser can draw accurately over the affected object.

If those five pieces work reliably on real production PDFs, the architecture is validated. Build the guided checklist and additional checks on that foundation before adding AI.

Set up the repo (`install.sh`, `run.sh`, lockfile) at the same time, so every machine gets the same setup from day one. Gather representative test PDFs first, including the largest (~150 MB) and one with awkward bleed or RGB images.
