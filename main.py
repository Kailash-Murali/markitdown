"""Database-free document-to-Markdown API and single-file web frontend."""

import asyncio
import base64
import io
import logging
import os
from pathlib import Path
import re
import tempfile
import unicodedata
import zipfile

from fastapi import FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from markitdown import MarkItDown
from starlette.concurrency import run_in_threadpool

app = FastAPI(title="Markitdown", docs_url=None, redoc_url=None)
logger = logging.getLogger("markitdown.api")
MAX_BYTES = 25 * 1024 * 1024
MAX_EXPANDED_BYTES = 150 * 1024 * 1024
ALLOWED = {".pdf", ".docx", ".xlsx", ".pptx", ".html", ".csv", ".txt"}
_conversion_slots = None


def get_conversion_slots() -> asyncio.Semaphore:
    global _conversion_slots
    if _conversion_slots is None:
        _conversion_slots = asyncio.Semaphore(2)
    return _conversion_slots


def markdown_filename(filename: str) -> str:
    """Strip both styles of path, controls, and special filename characters."""
    basename = filename.replace("\\", "/").rsplit("/", 1)[-1]
    stem = unicodedata.normalize("NFKC", Path(basename).stem)
    stem = re.sub(r"[^\w .-]", "_", stem, flags=re.UNICODE)
    stem = re.sub(r"\s+", " ", stem).strip(" ._-")[:150]
    return f"{stem or 'document'}.md"


def convert_document(path: str, ext: str) -> str:
    """Run blocking parsers in a worker thread, not on the async event loop."""
    if ext in {".docx", ".xlsx", ".pptx"}:
        if not zipfile.is_zipfile(path):
            raise ValueError("This Office document is damaged or isn’t a valid Office file.")
        with zipfile.ZipFile(path) as archive:
            if sum(info.file_size for info in archive.infolist()) > MAX_EXPANDED_BYTES:
                raise ValueError("This document is too large when expanded. Try a smaller file.")
            if any(info.flag_bits & 1 for info in archive.infolist()):
                raise ValueError("Password-protected documents aren’t supported. Unlock the file first.")
            expected = {".docx": "word/document.xml", ".xlsx": "xl/workbook.xml", ".pptx": "ppt/presentation.xml"}[ext]
            if expected not in archive.namelist():
                raise ValueError("The document contents don’t match its file extension.")
    if ext == ".pdf":
        with open(path, "rb") as source:
            if b"%PDF-" not in source.read(1024):
                raise ValueError("This file isn’t a valid PDF. Please check the document.")
    markdown = MarkItDown(enable_plugins=False).convert(path).text_content
    if not markdown or not markdown.strip():
        raise ValueError("No readable text was found. Scanned PDFs need a text layer; try a different document.")
    return markdown


async def process_single_upload(file: UploadFile) -> tuple[str, str]:
    """Helper to safely stream, validate, and convert a single UploadFile."""
    temp_path = None
    filename = file.filename or "document"
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED:
        raise HTTPException(415, f"Unsupported format for '{filename}'. Use PDF, DOCX, XLSX, PPTX, HTML, CSV, or TXT.")

    try:
        total = 0
        with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as temporary:
            temp_path = temporary.name
            while chunk := file.file.read(1024 * 1024):
                total += len(chunk)
                if total > MAX_BYTES:
                    raise HTTPException(413, f"'{filename}' exceeds 25 MB. Please choose smaller files.")
                temporary.write(chunk)

        if total == 0:
            raise HTTPException(400, f"'{filename}' is empty. Choose a document with content.")

        try:
            markdown = convert_document(temp_path, ext)
        except ValueError as error:
            raise HTTPException(422, f"Conversion failed for '{filename}': {error}") from None
        except Exception:
            logger.warning("Document conversion failed (%s)", ext)
            raise HTTPException(422, f"Could not read '{filename}'. It may be damaged or password-protected.") from None

        return filename, markdown
    finally:
        if temp_path:
            try:
                os.unlink(temp_path)
            except FileNotFoundError:
                pass
        file.file.close()


@app.middleware("http")
async def response_headers(request: Request, call_next):
    # Reject oversized requests early; the streamed file limit below is authoritative.
    if request.url.path in {"/api/convert", "/api/convert-batch"}:
        length = request.headers.get("content-length", "")
        if length.isdigit() and int(length) > MAX_BYTES + 1024 * 1024:
            return JSONResponse(status_code=413, content={"detail": "Please choose files within the 25 MB limit."})
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/", include_in_schema=False)
async def frontend():
    return FileResponse(Path(__file__).with_name("index.html"), media_type="text/html")


@app.get("/api/health")
async def health():
    return {"status": "ok"}


@app.post("/api/convert")
async def convert(file: UploadFile):
    """Single file conversion endpoint."""
    slots = get_conversion_slots()
    try:
        await asyncio.wait_for(slots.acquire(), timeout=0.1)
    except TimeoutError:
        raise HTTPException(503, "The converter is busy. Please try again in a moment.") from None

    try:
        filename, markdown = await process_single_upload(file)
        return {"markdown": markdown, "filename": markdown_filename(filename)}
    finally:
        slots.release()


@app.post("/api/convert-batch")
async def convert_batch(files: list[UploadFile], mode: str = Form("separate")):
    """
    Batch conversion endpoint supporting:
    a) mode='separate': returns ZIP of individual .md files (server-side via zipfile).
    b) mode='merge': returns a single combined .md file preserving upload order.
    Both modes follow fail-fast error handling and 25 MB guard.
    """
    if not files:
        raise HTTPException(400, "No files selected.")

    slots = get_conversion_slots()
    try:
        await asyncio.wait_for(slots.acquire(), timeout=0.1)
    except TimeoutError:
        raise HTTPException(503, "The converter is busy. Please try again in a moment.") from None

    try:
        results: list[tuple[str, str]] = []
        for file in files:
            # Fail-fast: if any single file fails, exception is raised immediately
            filename, markdown = await process_single_upload(file)
            results.append((filename, markdown))

        # Mode b: Multiple files -> Single output (Merged .md in upload order)
        if mode == "merge":
            sections: list[str] = []
            for orig_name, md in results:
                stem = unicodedata.normalize("NFKC", Path(orig_name).stem).strip()
                sections.append(f"# {stem}\n\n{md.strip()}\n")
            merged_markdown = "\n\n---\n\n".join(sections)
            return {
                "mode": "merge",
                "filename": "merged_documents.md",
                "markdown": merged_markdown,
                "count": len(results),
                "file_names": [r[0] for r in results],
            }

        # Mode a: Multiple files -> Multiple outputs (.zip archive created server-side)
        zip_buffer = io.BytesIO()
        used_names: set[str] = set()
        files_meta: list[dict[str, str]] = []
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            for orig_name, md in results:
                out_name = markdown_filename(orig_name)
                base = Path(out_name).stem
                suffix = Path(out_name).suffix
                counter = 1
                while out_name.lower() in used_names:
                    out_name = f"{base}_{counter}{suffix}"
                    counter += 1
                used_names.add(out_name.lower())
                zf.writestr(out_name, md.encode("utf-8"))
                files_meta.append({"filename": out_name, "markdown": md})

        zip_base64 = base64.b64encode(zip_buffer.getvalue()).decode("ascii")
        return {
            "mode": "separate",
            "filename": "converted_documents.zip",
            "zip_base64": zip_base64,
            "count": len(results),
            "files": files_meta,
        }
    finally:
        slots.release()
