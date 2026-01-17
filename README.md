# exaOCR - Fast OCR to Markdown Pipeline

## Overview
exaOCR is a production-ready OCR pipeline that converts any file (PDF, image, office document) into clean Markdown quickly. Built with FastAPI and Streamlit, exaOCR is optimized for CPU-only systems and preserves tables, forms, and layout structure.

API Documentation Page: [https://ikantkode.github.io/exaOCR](https://ikantkode.github.io/exaOCR)
Live Demo: [http://localhost:7601](http://localhost:7601)
Next Evolution: [pdfLLM](https://github.com/ikantkode/pdfLLM)

---

## Video Demo
[![exaOCR Demo](http://img.youtube.com/vi/FfBQg5JXk5E/0.jpg)](https://www.youtube.com/watch?v=FfBQg5JXk5E)

---

## Key Results
| Metric              | Large Document | Small Document |
|---------------------|----------------|----------------|
| Wall Time           | ~250 s         | ~15 s          |
| Parallel Pages      | 8 cores        | 8 cores        |
| Memory Peak         | <2 GB          | <500 MB        |
| Table Accuracy      | 95%+           | 95%+           |

---

## Supported Formats
| Category   | Extensions                   | Conversion Path       |
|------------|------------------------------|----------------------|
| PDF        | `.pdf`                        | Direct               |
| Images     | `.jpg .jpeg .png .tiff .bmp`  | `img2pdf` → PDF      |
| Office     | `.doc .docx .txt .csv`        | LibreOffice → PDF    |
| Future     | `.xlsx .pptx .rtf`            | Planned              |

---

## Architecture
```
Streamlit <--> FastAPI <--> OCR Core

- FastAPI handles uploads, progress, and downloads
- OCRmyPDF + Tesseract adds searchable text
- PyMuPDF4LLM extracts Markdown with table preservation
- Pages processed in parallel across CPU cores
```

---

## Quick Start

### 1. Clone & Run
```bash
git clone https://github.com/ikantkode/exaOCR.git
cd exaOCR
docker compose up --build
```
- Open [http://localhost:7601](http://localhost:7601)
- Upload files, watch progress, and download Markdown ZIP

### 2. Production
```bash
docker compose up -d --build
```

---

## Hardware Recommendations
| CPU / RAM     | Max Workers | Batch Size |
|---------------|------------|------------|
| 4-core / 8GB  | 4          | 5 files    |
| 8-core / 16GB | 8          | 10 files   |
| 24-core / 64GB| 12         | 25 files   |

Set in `app.py`:
```python
executor = ThreadPoolExecutor(max_workers=12)
```

Monitor resources with:
```bash
htop
free -m
```

---

## API Endpoints
View Documentation: [https://ikantkode.github.io/exaOCR](https://ikantkode.github.io/exaOCR)

| Endpoint                     | Method   | Purpose                          |
|-------------------------------|----------|----------------------------------|
| `/upload/`                    | POST     | Upload files for OCR             |
| `/batch/{batch_id}/status`    | GET      | Get real-time batch progress     |
| `/batch/{batch_id}/results`   | GET      | Get batch results when complete  |
| `/progress/{file_id}`         | GET      | Get per-file progress            |
| `/download-markdown/{id}`     | GET      | Download Markdown                |
| `/batch/{batch_id}/cleanup`   | DELETE   | Clean up batch resources         |
| `/health`                     | GET      | Health check                     |

---

## Batch Processing with Progress Tracking

exaOCR now supports **real-time progress tracking** for batch processing. Upload multiple files and get immediate feedback on processing status.

### How It Works

1. **Upload files** → Get `batch_id` immediately
2. **Poll for progress** → Track completion in real-time
3. **Get results** → Retrieve all processed markdown

### Python Example

```python
import requests
import time

# 1. Start batch processing (returns immediately)
files = [("doc1.pdf", open("doc1.pdf", "rb")),
         ("doc2.pdf", open("doc2.pdf", "rb"))]

response = requests.post(
    "http://localhost:8000/upload/?force_ocr=true",
    files=[("files", f) for f in files]
)

data = response.json()
batch_id = data["batch_id"]
print(f"Batch started: {batch_id}")
print(f"Total files: {data['total_files']}")

# 2. Poll for progress
while True:
    status = requests.get(f"http://localhost:8000/batch/{batch_id}/status").json()
    print(f"Progress: {status['completed_files']}/{status['total_files']} "
          f"({status['progress_percent']:.1f}%)")

    if status["status"] == "completed":
        break

    time.sleep(2)  # Poll every 2 seconds

# 3. Get final results
results = requests.get(f"http://localhost:8000/batch/{batch_id}/results").json()

for result in results["results"]:
    print(f"{result['file_name']}: {result['status']} - {result['page_count']} pages")

# 4. Download markdown for each file
for result in results["results"]:
    if result["markdown_id"]:
        md = requests.get(f"http://localhost:8000/download-markdown/{result['markdown_id']}")
        with open(f"{result['file_name']}.md", "wb") as f:
            f.write(md.content)

# 5. Clean up resources
requests.delete(f"http://localhost:8000/batch/{batch_id}/cleanup")
```

### cURL Example

```bash
# Start batch
UPLOAD_RESPONSE=$(curl -X POST "http://localhost:8000/upload/?force_ocr=true" \
  -F "files=@doc1.pdf" \
  -F "files=@doc2.pdf")

BATCH_ID=$(echo $UPLOAD_RESPONSE | jq -r '.batch_id')
echo "Batch ID: $BATCH_ID"

# Check status
curl "http://localhost:8000/batch/$BATCH_ID/status"

# Get results
curl "http://localhost:8000/batch/$BATCH_ID/results"

# Clean up
curl -X DELETE "http://localhost:8000/batch/$BATCH_ID/cleanup"
```

### Batch Status Response

```json
{
  "batch_id": "abc-123",
  "total_files": 54,
  "completed_files": 23,
  "in_progress_files": 31,
  "failed_files": 0,
  "progress_percent": 42.59,
  "status": "processing",
  "elapsed_time_seconds": 125.5,
  "files": [
    {
      "file_id": "uuid-1",
      "page_count": 10,
      "pages_processed": 10,
      "failed_pages": [],
      "status": "completed"
    }
  ]
}
```

---

---

## Docker Compose (Production)
```yaml
version: "3.8"
services:
  fastapi:
    build: .
    ports:
      - "8000:8000"
    environment:
      - PYTHONUNBUFFERED=1
    deploy:
      resources:
        limits:
          memory: 4G
  streamlit:
    build: .
    ports:
      - "7601:7601"
    depends_on:
      - fastapi
```

---

## Performance Baselines
| Test Case               | Pages | Time   | CPU Threads |
|--------------------------|-------|--------|-------------|
| 10 PDFs (avg 50 pages)   | 500   | 45 s   | 8           |
| 1 × 800-page contract    | 800   | 250 s  | 8           |
| 50 images                | 50    | 30 s   | 8           |

---

## Tech Stack
| Layer       | Technology            |
|------------ |---------------------|
| Frontend    | Streamlit 1.38       |
| API         | FastAPI              |
| OCR         | OCRmyPDF + Tesseract |
| Markdown    | PyMuPDF4LLM          |
| Parallelism | concurrent.futures   |
| Container   | Ubuntu 24.04, Python 3.12 |

---

## License
MIT – free for personal and commercial use. Dependencies follow their own licenses.  

Issues or PRs: [GitHub Issues](https://github.com/ikantkode/exaOCR)

