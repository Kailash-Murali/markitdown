"""Database-free document-to-Markdown API and single-file web frontend."""

import asyncio
import logging
import os
from pathlib import Path
import re
import tempfile
import unicodedata
import zipfile

from fastapi import FastAPI, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from markitdown import MarkItDown
from starlette.concurrency import run_in_threadpool

app = FastAPI(title="Markitdown", docs_url=None, redoc_url=None)
logger = logging.getLogger("markitdown.api")
MAX_BYTES = 25 * 1024 * 1024
MAX_EXPANDED_BYTES = 150 * 1024 * 1024
ALLOWED = {".pdf", ".docx", ".xlsx", ".pptx", ".html", ".csv", ".txt"}
conversion_slots = asyncio.Semaphore(2)


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


@app.middleware("http")
async def response_headers(request: Request, call_next):
    # Reject oversized requests early; the streamed file limit below is authoritative.
    if request.url.path == "/api/convert":
        length = request.headers.get("content-length", "")
        if length.isdigit() and int(length) > MAX_BYTES + 1024 * 1024:
            return JSONResponse(status_code=413, content={"detail": "Please choose a file smaller than 25 MB."})
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
    temp_path = None
    try:
        filename = file.filename or ""
        ext = Path(filename).suffix.lower()
        if ext not in ALLOWED:
            raise HTTPException(415, "Unsupported format. Use PDF, DOCX, XLSX, PPTX, HTML, CSV, or TXT.")
        # Limit active parsers and fail promptly rather than queue unlimited uploads.
        try:
            await asyncio.wait_for(conversion_slots.acquire(), timeout=0.1)
        except TimeoutError:
            raise HTTPException(503, "The converter is busy. Please try again in a moment.") from None
        try:
            total = 0
            with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as temporary:
                temp_path = temporary.name
                while chunk := await file.read(1024 * 1024):
                    total += len(chunk)
                    if total > MAX_BYTES:
                        raise HTTPException(413, "Please choose a file smaller than 25 MB.")
                    temporary.write(chunk)
            if total == 0:
                raise HTTPException(400, "This file is empty. Choose a document with some content.")
            try:
                # Shield the worker on client disconnect: it must finish before unlink.
                worker = asyncio.create_task(run_in_threadpool(convert_document, temp_path, ext))
                try:
                    markdown = await asyncio.shield(worker)
                except asyncio.CancelledError:
                    try:
                        await worker
                    except Exception:
                        pass
                    raise
            except ValueError as error:
                raise HTTPException(422, str(error)) from None
            except Exception:
                logger.warning("Document conversion failed (%s)", ext)
                raise HTTPException(422, "We couldn’t read this document. It may be damaged, password-protected, or contain no extractable text.") from None
            return {"markdown": markdown, "filename": markdown_filename(filename)}
        finally:
            conversion_slots.release()
    finally:
        if temp_path:
            try:
                os.unlink(temp_path)
            except FileNotFoundError:
                pass
        await file.close()
