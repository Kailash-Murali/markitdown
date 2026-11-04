# Markitdown.

A database-free document-to-Markdown converter. A single-file vanilla JavaScript
frontend (`index.html`) and a single-file FastAPI backend (`main.py`). No account,
storage service, external image assets, or API keys.

## Run the complete application

Use Python 3.11 or newer:

```sh
python -m venv .venv
source .venv/bin/activate
# Windows: .venv\Scripts\activate
python -m pip install -r requirements.txt
python -m uvicorn main:app --host 0.0.0.0 --port 8000
```

Open **http://localhost:8000**. FastAPI serves the exact same self-contained
`index.html` used by the Vite preview. It needs no frontend build. Google Fonts
is optional; the page falls back to system sans-serif if unavailable.

The Figma/Vite preview shows the full interface but does not run Python.
Its `/api` requests are proxied to `127.0.0.1:8000`. Run the FastAPI service
in the same environment to enable conversions in that preview. Without the
service, upload attempts show an honest connection error, not simulated output.

## Convert

- Drop a file anywhere on the page, or use **Upload a file**.
- Supports `.pdf`, `.docx`, `.xlsx`, `.pptx`, `.html`, `.csv`, `.txt`.
- Maximum 25 MiB per file; one file at a time.
- Conversion runs in a worker thread, keeping the API event loop responsive.
- The finished document offers **Download .md**, **Copy to clipboard**, and a
  collapsible, safely text-rendered preview. Large previews are capped at 100,000
  characters; the download always contains the entire result.
- Copy requires a secure context (HTTPS or localhost) and clipboard permission.
- Temporary uploads are removed in `finally` on success and error. On a
  cancelled request, an active parser is allowed to finish before its file is
  deleted. No documents or output are saved permanently.
- Password-protected files and scanned PDFs without a text layer are not
  supported. Complex layouts and formulas may not retain their original form.

## API

```sh
curl -F "file=@example.docx" http://localhost:8000/api/convert
```

Returns `{"markdown":"…","filename":"example.md"}`. Filenames are sanitized.
Errors are JSON `{"detail":"…"}` with status 400 (empty), 413 (too large),
415 (unsupported), 422 (unreadable), or 503 (busy).
`GET /api/health` checks availability.

## Verification

```sh
python -m pip install -r requirements-dev.txt
python -m pytest -q
pnpm build
pnpm format --check vite.config.ts
```

## Deployment notes

Run the app behind an HTTPS reverse proxy with request-body limits (26 MiB),
upload/read timeouts, request-rate limits, and restricted network egress.
The application limits concurrent conversions to two per worker and rejects
Office archives expanding beyond 150 MiB. The browser stops waiting after 130
seconds; it does not force-kill a running parser. For a public untrusted-file
service, isolate parsers in resource-limited containers or a job worker and
enforce hard CPU/memory/runtime limits there. Thread isolation is not a sandbox.
Process crashes and forced termination cannot execute Python `finally` blocks;
use an ephemeral temp volume and periodic stale-file cleanup in deployment.
