"""API regression tests, including real converters and temporary-file cleanup."""

import asyncio
from io import BytesIO
from pathlib import Path
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient
from fastapi import UploadFile
import pytest

import main


@pytest.fixture
def client():
    with TestClient(main.app) as client:
        yield client


@pytest.fixture
def temporary_paths(monkeypatch, tmp_path):
    original = tempfile.NamedTemporaryFile
    paths = []

    def tracked(*args, **kwargs):
        kwargs["dir"] = tmp_path
        result = original(*args, **kwargs)
        paths.append(Path(result.name))
        return result

    monkeypatch.setattr(main.tempfile, "NamedTemporaryFile", tracked)
    yield paths
    assert all(not path.exists() for path in paths), "Temporary uploads leaked"


def upload(client, name, data):
    return client.post("/api/convert", files={"file": (name, data)})


@pytest.mark.parametrize(
    ("name", "content", "expected"),
    [
        ("notes.txt", b"Hello, Markdown!", "Hello, Markdown!"),
        ("table.csv", b"name,value\nalpha,42\n", "alpha"),
        ("page.html", b"<h1>Clear ideas</h1><p>Hello <b>world</b>.</p>", "# Clear ideas"),
        ("UPPER.TXT", "Unicode: café — 日本語".encode(), "café"),
    ],
)
def test_real_text_conversions(client, temporary_paths, name, content, expected):
    response = upload(client, name, content)
    assert response.status_code == 200, response.text
    assert expected in response.json()["markdown"]
    assert response.json()["filename"].endswith(".md")
    assert temporary_paths
    assert response.headers["cache-control"] == "no-store"


def test_real_word_conversion(client, temporary_paths):
    from docx import Document

    document = Document()
    document.add_heading("A clear heading", level=1)
    document.add_paragraph("Words worth keeping.")
    buffer = BytesIO()
    document.save(buffer)
    response = upload(client, "document.docx", buffer.getvalue())
    assert response.status_code == 200, response.text
    assert "A clear heading" in response.json()["markdown"]
    assert "Words worth keeping." in response.json()["markdown"]


def test_real_excel_conversion(client, temporary_paths):
    from openpyxl import Workbook

    workbook = Workbook()
    workbook.active.append(["Project", "Status"])
    workbook.active.append(["Markitdown", "Ready"])
    buffer = BytesIO()
    workbook.save(buffer)
    response = upload(client, "workbook.xlsx", buffer.getvalue())
    assert response.status_code == 200, response.text
    assert "Markitdown" in response.json()["markdown"]


def test_real_powerpoint_conversion(client, temporary_paths):
    from pptx import Presentation

    presentation = Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[0])
    slide.shapes.title.text = "Big ideas"
    buffer = BytesIO()
    presentation.save(buffer)
    response = upload(client, "presentation.pptx", buffer.getvalue())
    assert response.status_code == 200, response.text
    assert "Big ideas" in response.json()["markdown"]


def test_real_pdf_conversion(client, temporary_paths):
    # A complete one-page PDF with a text layer, no external fixture needed.
    stream = b"BT /F1 18 Tf 40 200 Td (Hello Markdown) Tj ET"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 300] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
    ]
    data = b"%PDF-1.4\n"
    offsets = [0]
    for index, obj in enumerate(objects, 1):
        offsets.append(len(data))
        data += f"{index} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref = len(data)
    data += b"xref\n0 6\n0000000000 65535 f \n"
    for offset in offsets[1:]:
        data += f"{offset:010d} 00000 n \n".encode()
    data += f"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode()
    response = upload(client, "paper.pdf", data)
    assert response.status_code == 200, response.text
    assert "Hello Markdown" in response.json()["markdown"]


@pytest.mark.parametrize(
    ("name", "data", "status"),
    [
        ("document.exe", b"not allowed", 415),
        ("document.txt", b"", 400),
        ("document.txt", b" \n\t ", 422),
        ("document.pdf", b"not a PDF", 422),
        ("document.docx", b"not an archive", 422),
    ],
)
def test_rejections_and_cleanup(client, temporary_paths, name, data, status):
    response = upload(client, name, data)
    assert response.status_code == status, response.text
    assert isinstance(response.json()["detail"], str)


def test_converter_exception_cleanup(client, temporary_paths, monkeypatch):
    def broken(*args):
        raise RuntimeError("internal details must not escape")

    monkeypatch.setattr(main, "convert_document", broken)
    response = upload(client, "private.txt", b"private")
    assert response.status_code == 422
    assert "internal details" not in response.text
    assert temporary_paths


def test_streamed_size_limit_cleanup(client, temporary_paths, monkeypatch):
    monkeypatch.setattr(main, "MAX_BYTES", 10)
    response = upload(client, "big.txt", b"a" * 11)
    assert response.status_code == 413
    assert temporary_paths


def test_early_body_limit(client):
    response = client.post(
        "/api/convert",
        content=b"test",
        headers={"Content-Length": str(main.MAX_BYTES + 1024 * 1024 + 1)},
    )
    assert response.status_code == 413


def test_mismatched_office_archive(client, temporary_paths):
    import zipfile

    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("unrelated.txt", "hello")
    assert upload(client, "fake.docx", buffer.getvalue()).status_code == 422


def test_expanded_archive_limit(client, temporary_paths, monkeypatch):
    import zipfile

    monkeypatch.setattr(main, "MAX_EXPANDED_BYTES", 10)
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml", "x" * 11)
    assert upload(client, "large.docx", buffer.getvalue()).status_code == 422


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("../../secret.txt", "secret.md"),
        (r"C:\Users\private\report.docx", "report.md"),
        ("<script>.txt", "script.md"),
        ("....txt", "document.md"),
        ("my report.PDF", "my report.md"),
    ],
)
def test_filename_sanitization(source, expected):
    assert main.markdown_filename(source) == expected


def test_server_stays_responsive_during_conversion(client, temporary_paths, monkeypatch):
    started, finish = threading.Event(), threading.Event()

    def slow(*args):
        started.set()
        assert finish.wait(5)
        return "done"

    monkeypatch.setattr(main, "convert_document", slow)
    with ThreadPoolExecutor() as pool:
        future = pool.submit(upload, client, "notes.txt", b"hello")
        try:
            assert started.wait(3)
            assert client.get("/api/health").json() == {"status": "ok"}
        finally:
            finish.set()
        assert future.result().status_code == 200


def test_frontend(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "Markitdown." in response.text
    assert response.headers["x-content-type-options"] == "nosniff"


def test_busy_converter_rejects_without_leaking_upload(monkeypatch, temporary_paths):
    async def run():
        monkeypatch.setattr(main, "conversion_slots", asyncio.Semaphore(0))
        file = UploadFile(filename="notes.txt", file=BytesIO(b"hello"))
        with pytest.raises(main.HTTPException) as error:
            await main.convert(file)
        assert error.value.status_code == 503
        assert file.file.closed
        assert not temporary_paths

    asyncio.run(run())


def test_cancel_waits_for_worker_before_cleanup(monkeypatch, temporary_paths):
    started, finish = threading.Event(), threading.Event()

    def slow(*args):
        started.set()
        assert finish.wait(5)
        return "done"

    monkeypatch.setattr(main, "convert_document", slow)

    async def run():
        monkeypatch.setattr(main, "conversion_slots", asyncio.Semaphore(2))
        file = UploadFile(filename="notes.txt", file=BytesIO(b"hello"))
        task = asyncio.create_task(main.convert(file))
        try:
            assert await asyncio.to_thread(started.wait, 3)
            task.cancel()
            await asyncio.sleep(0.02)
            assert not task.done(), "Cleanup must wait until the parser releases its file"
            assert all(path.exists() for path in temporary_paths)
        finally:
            finish.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert file.file.closed
        assert all(not path.exists() for path in temporary_paths)

    asyncio.run(run())
