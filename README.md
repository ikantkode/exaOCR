# 📄 exaOCR

> **Lightning-fast OCR pipeline** that converts PDFs, images, and documents into clean Markdown with real-time progress tracking.

[![FastAPI](https://img.shields.io/badge/FastAPI-0.115-green)](https://fastapi.tiangolo.com)
[![Streamlit](https://img.shields.io/badge/Streamlit-1.38-red)](https://streamlit.io)
[![Python](https://img.shields.io/badge/Python-3.12-blue)](https://python.org)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

[API Documentation](https://ikantkode.github.io/exaOCR) • [Live Demo](http://localhost:7601) • [Next Evolution: pdfLLM](https://github.com/ikantkode/pdfLLM)

---

## ✨ Features

- 🚀 **Smart OCR Routing** - Automatically skips OCR for digital PDFs, 10-50x faster
- 📊 **Real-time Progress** - Track pages processed (e.g., "234/481 pages") live
- 🔄 **Batch Processing** - Upload multiple files, process in parallel
- 📑 **Table Preservation** - Maintains table structure and formatting
- ⚡ **Highly Parallel** - Process up to 16 pages simultaneously
- 🐳 **Docker Ready** - Single command deployment
- 💾 **Multiple Formats** - PDF, images, office documents → Markdown

---

## 📺 Video Demo

[![exaOCR Demo](http://img.youtube.com/vi/FfBQg5JXk5E/0.jpg)](https://www.youtube.com/watch?v=FfBQg5JXk5E)

---

## 🚀 Quick Start

### Prerequisites

- Docker and Docker Compose installed
- 4GB+ RAM recommended

### One-Line Launch

```bash
git clone https://github.com/ikantkode/exaOCR.git
cd exaOCR
docker compose up --build
```

**That's it!** Open [http://localhost:7601](http://localhost:7601) and start converting files.

### Production Mode

```bash
docker compose up -d --build
```

---

## 📊 Performance

| Document Type | Pages | Time | Speed |
|--------------|-------|------|-------|
| **Digital PDF** (`force_ocr=false`) | 481 | ~48-144s | **🚀 10-50x faster** |
| **Scanned PDF** (`force_ocr=true`) | 481 | ~40-75 min | Standard OCR |
| **Mixed (50/50)** | 100 | ~200-350s | **2-3x faster** |

> 💡 **Tip**: Uncheck "Force OCR" for digital PDFs to get maximum speed!

### Hardware Recommendations

| CPU | RAM | Max Parallel Pages | Batch Size |
|-----|-----|-------------------|------------|
| 4-core | 8GB | 8 | 5 files |
| 8-core | 16GB | 16 | 10 files |
| 24-core | 64GB | 24 | 25 files |

Edit `app.py:414` to adjust:
```python
with concurrent.futures.ThreadPoolExecutor(max_workers=16) as page_executor:
```

---

## 📁 Supported Formats

| Category | Extensions | Processing |
|----------|-----------|------------|
| **PDF** | `.pdf` | Direct |
| **Images** | `.jpg`, `.png`, `.jpeg`, `.tiff`, `.bmp` | img2pdf → OCR |
| **Office** | `.doc`, `.docx`, `.txt`, `.csv` | LibreOffice → OCR |
| **Planned** | `.xlsx`, `.pptx`, `.rtf` | Coming soon |

---

## 🏗️ Architecture

```
┌─────────────┐     ┌─────────────┐     ┌─────────────────────┐
│  Streamlit  │────▶│   FastAPI   │────▶│     OCR Engine      │
│   (UI)      │     │  (Backend)  │     │  • OCRmyPDF         │
└─────────────┘     └─────────────┘     │  • PyMuPDF4LLM      │
                          │              │  • Tesseract        │
                          │              └─────────────────────┘
                          │
                          ▼
                   ┌─────────────┐
                   │ In-Memory   │
                   │  Storage    │
                   │ • Progress  │
                   │ • Results   │
                   └─────────────┘
```

### Smart OCR Routing

```
Page → Has embedded text?
        ├─ Yes → PyMuPDF4LLM (0.1-0.3 sec) ✅ FAST
        └─ No  → OCRmyPDF + Tesseract (5-15 sec)
```

---

## 📡 API Endpoints

### Upload & Batch Processing

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/upload/` | POST | Upload files, start batch processing |
| `/batch/{id}/status` | GET | Real-time batch progress |
| `/batch/{id}/results` | GET | Get completed batch results |
| `/batch/{id}/cleanup` | DELETE | Free up memory |
| `/download-markdown/{id}` | GET | Download markdown content |
| `/health` | GET | Service health check |

### Batch Processing Flow

```python
import requests
import time

# 1. Upload files (returns immediately with batch_id)
response = requests.post(
    "http://localhost:8000/upload/?force_ocr=false",
    files=[("files", open("doc.pdf", "rb"))]
)
batch_id = response.json()["batch_id"]

# 2. Poll for progress
while True:
    status = requests.get(f"http://localhost:8000/batch/{batch_id}/status").json()
    print(f"Progress: {status['progress_percent']:.1f}%")

    if status["status"] == "completed":
        break
    time.sleep(2)

# 3. Get results
results = requests.get(f"http://localhost:8000/batch/{batch_id}/results").json()

# 4. Download markdown
for result in results["results"]:
    if result["markdown_id"]:
        md = requests.get(f"http://localhost:8000/download-markdown/{result['markdown_id']}")
        with open(f"{result['file_name']}.md", "wb") as f:
            f.write(md.content)

# 5. Clean up
requests.delete(f"http://localhost:8000/batch/{batch_id}/cleanup")
```

### Batch Status Response

```json
{
  "batch_id": "abc-123",
  "total_files": 1,
  "completed_files": 0,
  "in_progress_files": 1,
  "failed_files": 0,
  "progress_percent": 45.3,
  "status": "processing",
  "elapsed_time_seconds": 125.5,
  "files": [
    {
      "file_id": "uuid-1",
      "page_count": 481,
      "pages_processed": 218,
      "failed_pages": [],
      "status": "processing"
    }
  ]
}
```

---

## 🎯 Usage Examples

### Web UI (Streamlit)

1. Open [http://localhost:7601](http://localhost:7601)
2. Drag & drop files
3. (Optional) Uncheck "Force OCR" for digital PDFs
4. Click "Start Conversion"
5. Watch real-time progress: "Processing page 234/481"
6. Download markdown when complete

### cURL

```bash
# Upload file
curl -X POST "http://localhost:8000/upload/?force_ocr=false" \
  -F "files=@document.pdf"

# Check batch status
curl "http://localhost:8000/batch/{batch_id}/status"

# Get results
curl "http://localhost:8000/batch/{batch_id}/results"
```

### Python

```python
import requests

# Upload and process
files = {"files": open("document.pdf", "rb")}
response = requests.post(
    "http://localhost:8000/upload/",
    files=files,
    params={"force_ocr": False}
)
batch_id = response.json()["batch_id"]

# Wait for completion
while True:
    status = requests.get(f"http://localhost:8000/batch/{batch_id}/status").json()
    if status["status"] == "completed":
        break
    time.sleep(2)

# Download results
results = requests.get(f"http://localhost:8000/batch/{batch_id}/results").json()
markdown_id = results["results"][0]["markdown_id"]
markdown = requests.get(f"http://localhost:8000/download-markdown/{markdown_id}").text

print(markdown)
```

---

## 🛠️ Configuration

### Tuning Performance

Edit `app.py`:

```python
# Page-level parallelism (line 414)
with concurrent.futures.ThreadPoolExecutor(max_workers=16) as page_executor:
    # Increase for more parallel page processing
    # Set to your CPU core count for best results

# Batch-level parallelism (line 42)
executor = ThreadPoolExecutor(max_workers=4)
    # Reduced to prevent thread thrashing
    # Outer executor × inner executor = total threads
```

### OCR Settings

```python
# Tesseract parallelism (app.py:238)
'--jobs', '4',  # Number of Tesseract worker threads

# Processing modes
force_ocr=False  # Use smart routing (fast)
force_ocr=True   # Force OCR on all pages (accurate but slow)
```

---

## 📦 Tech Stack

| Layer | Technology |
|-------|-----------|
| **Frontend** | Streamlit 1.38 |
| **Backend** | FastAPI 0.115 |
| **OCR** | OCRmyPDF + Tesseract OCR |
| **Markdown** | PyMuPDF4LLM |
| **Parallelism** | concurrent.futures (ThreadPoolExecutor) |
| **Container** | Ubuntu 24.04, Python 3.12 |

---

## 🐳 Docker Deployment

### Basic

```yaml
services:
  fastapi:
    build: .
    ports:
      - "8000:8000"
  streamlit:
    build: .
    ports:
      - "7601:7601"
```

### With Resource Limits

```yaml
services:
  fastapi:
    build: .
    ports:
      - "8000:8000"
    deploy:
      resources:
        limits:
          memory: 4G
          cpus: '4'
    environment:
      - PYTHONUNBUFFERED=1
```

---

## 📝 License

MIT License - free for personal and commercial use.

Dependencies follow their own licenses.

---

## 🤝 Contributing

Issues and PRs welcome! [GitHub Issues](https://github.com/ikantkode/exaOCR)

---

## 🌟 Star History

If you find this project useful, please consider giving it a ⭐ star!

---

<div align="center">

**Made with ❤️ by [ikantkode](https://github.com/ikantkode)**

[⬆ Back to Top](#-exaocr)

</div>
