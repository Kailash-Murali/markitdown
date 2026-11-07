# Markitdown.

A clean, database-free document-to-Markdown converter powered by Microsoft's `markitdown` library and FastAPI.

Convert documents into clean Markdown with zero account creation, no external databases, no cloud storage persistence, and no third-party tracking.

---

## Features

- **Single File Conversion**: Convert individual files (`.pdf`, `.docx`, `.xlsx`, `.pptx`, `.html`, `.csv`, `.txt`) into Markdown (`.md`).
- **Batch Conversion (`.zip`)**: Upload multiple documents at once and receive individual converted `.md` files packaged neatly inside a `.zip` archive.
- **Merge Conversion (`1 .md`)**: Combine multiple files into a single structured Markdown file, preserving the exact upload sequence with clear dividers.
- **Zero Data Retention**: Uploads are streamed to temporary files, parsed in memory, and immediately deleted upon completion or error.
- **Fluid & Accessible Interface**:
  - Smooth multi-mode selector slider (Single, Batch `.zip`, Merge `1 .md`).
  - Glassmorphic frosted cards with floating format illustrations.
  - One-click file download and copy to clipboard.
  - Fail-fast error handling with clear feedback.
  - Accessible "How it works" guide modal.
- **Safety Guards**: 25 MB per-file size guard and worker concurrency limits.

---

## Supported File Types

| Format | Extensions | Engine / Parser |
| :--- | :--- | :--- |
| **PDF** | `.pdf` | MarkItDown (PDF text layer) |
| **Word** | `.docx` | MarkItDown (python-docx) |
| **Excel** | `.xlsx` | MarkItDown (openpyxl) |
| **PowerPoint** | `.pptx` | MarkItDown (python-pptx) |
| **HTML** | `.html` | MarkItDown (BeautifulSoup4) |
| **CSV** | `.csv` | MarkItDown (tabular plain text) |
| **Text** | `.txt` | UTF-8 plain text |

*Note: Scanned PDFs without an OCR text layer and password-protected documents are not supported.*

---

## Getting Started

### Prerequisites

- Python 3.11+
- Node.js 18+ (optional, only required if rebuilding the frontend bundle)

### 1. Backend Setup (FastAPI)

```bash
# Create and activate virtual environment
python -m venv .venv

# On Windows:
.venv\Scripts\activate
# On macOS / Linux:
source .venv/bin/activate

# Install dependencies
python -m pip install -r requirements.txt

# Start the server
python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

The application will be live at **http://localhost:8000**. FastAPI serves the complete self-contained frontend directly.

### 2. Frontend Development (Optional)

If developing or building the static assets via Vite:

```bash
# Install Node dependencies
npm install

# Start Vite dev server (proxies /api to 127.0.0.1:8000)
npm run dev

# Or build for production
npm run build
```

---

## API Reference

### Health Check

```http
GET /api/health
```

**Response**:
```json
{
  "status": "ok",
  "converters": ["pdf", "docx", "xlsx", "pptx", "html", "csv", "txt"],
  "max_bytes": 26214400
}
```

### Single File Conversion

```http
POST /api/convert
```

**Request**: Multipart form data with `file`.

```bash
curl -F "file=@report.docx" http://localhost:8000/api/convert
```

**Response**:
```json
{
  "filename": "report.md",
  "markdown": "# Report Title\n\nDocument contents..."
}
```

### Batch & Merged Conversion

```http
POST /api/convert-batch
```

**Request**: Multipart form data with multiple `files` and a `mode` field (`separate` or `merge`).

#### Batch Mode (`mode=separate`):
Returns individual converted files packaged inside a Base64-encoded `.zip` archive.

```bash
curl -F "files=@doc1.pdf" -F "files=@doc2.docx" -F "mode=separate" http://localhost:8000/api/convert-batch
```

**Response**:
```json
{
  "mode": "separate",
  "filename": "converted_documents.zip",
  "zip_base64": "UEsDBBQA...",
  "count": 2,
  "files": [
    { "filename": "doc1.md", "markdown": "..." },
    { "filename": "doc2.md", "markdown": "..." }
  ]
}
```

#### Merge Mode (`mode=merge`):
Returns a single merged Markdown file maintaining the order of upload.

```bash
curl -F "files=@part1.docx" -F "files=@part2.docx" -F "mode=merge" http://localhost:8000/api/convert-batch
```

**Response**:
```json
{
  "mode": "merge",
  "filename": "merged_documents.md",
  "markdown": "# part1\n\n...\n\n---\n\n# part2\n\n...",
  "count": 2,
  "file_names": ["part1.docx", "part2.docx"]
}
```

---

## Verification & Testing

```bash
# Install test dependencies
python -m pip install -r requirements-dev.txt

# Run backend regression tests
python -m pytest -q

# Verify frontend build
npm run build
```

---

## License

MIT
