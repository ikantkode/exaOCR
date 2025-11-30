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

| Endpoint                  | Method | Purpose             |
|----------------------------|--------|-------------------|
| `/upload/`                | POST   | Upload files       |
| `/progress/{file_id}`     | GET    | Real-time progress |
| `/download-markdown/{id}` | GET    | Download Markdown  |
| `/health`                 | GET    | Health check       |

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

