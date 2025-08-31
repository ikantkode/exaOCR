# exaOCR - Ultra-Fast OCR to Markdown Pipeline

## 📖 Overview
exaOCR is a **production-ready, Docker-native OCR pipeline** that transforms **any file** (PDF, image, office document) into clean, LLM-ready Markdown in seconds.  

Built with **FastAPI + Streamlit**, optimized for **CPU-only environments**, and battle-tested on **800+ page contracts**, exaOCR delivers sub-3-minute processing while preserving **tables, forms, and layout structure**.

> ✅ **Live Demo**: Deploy & view at http://localhost:7601.
> [![Video Title](http://img.youtube.com/vi/FfBQg5JXk5E/0.jpg)](https://www.youtube.com/watch?v=FfBQg5JXk5E "Video Title")
> 🏗️ **Next Evolution**: [pdfLLM](https://github.com/ikantkode/pdfLLM) – plug-and-play RAG ingestion.

---

## 🚀 Key Results
| Metric              | 800-page Contract | 50-page Report |
|---------------------|-------------------|----------------|
| **Wall Time**       | ~250 s            | ~15 s          |
| **Parallel Pages**  | 8 cores           | 8 cores        |
| **Memory Peak**     | <2 GB             | <500 MB        |
| **Table Accuracy**  | 95 %+             | 95 %+          |

---

## 📁 Supported Formats
| Category   | Extensions                       | Conversion Path       |
|------------|----------------------------------|-----------------------|
| **PDF**    | `.pdf`                           | Direct                |
| **Images** | `.jpg .jpeg .png .tiff .bmp`     | `img2pdf` → PDF       |
| **Office** | `.doc .docx .txt .csv`           | LibreOffice → PDF     |
| **Future** | `.xlsx .pptx .rtf`               | Planned               |

---

## ⚙️ Architecture
```text
┌─────────────┐      ┌─────────────┐      ┌─────────────┐
│  Streamlit  │◄────►│  FastAPI    │◄────►│  OCR Core   │
│   :7601     │      │   :8000     │      │ 12 threads  │
└─────────────┘      └─────────────┘      └─────────────┘

- FastAPI handles file upload, progress, and download.
- OCRmyPDF + Tesseract adds searchable text.
- PyMuPDF4LLM extracts Markdown with table preservation.
- concurrent.futures parallelizes pages across CPU cores.
```

---

## 📦 Quick Start

### 1. Clone & Spin Up
```bash
git clone https://github.com/ikantkode/exaOCR.git
cd exaOCR
docker compose up --build
```
- Browse to [http://localhost:7601](http://localhost:7601)  
- Upload files → watch progress bar → download Markdown ZIP.

### 2. Production Deploy
```bash
docker compose up -d --build
```

---

## 🛠 Hardware Tuning
| Server Spec   | Recommended max_workers | Batch Size |
|---------------|-------------------------|------------|
| 4-core / 8 GB | 4                       | 5 files    |
| 8-core / 16 GB| 8                       | 10 files   |
| 24-core / 64 GB| 12                     | 25 files   |

In `app.py`:
```python
executor = ThreadPoolExecutor(max_workers=12)  # 🔧 Tune here
```

Monitor with:
```bash
htop      # CPU usage
free -m   # RAM usage
```

---

## 🧩 API Endpoints
| Endpoint                  | Method | Purpose              |
|---------------------------|--------|----------------------|
| `/upload/`                | POST   | Upload files         |
| `/progress/{file_id}`     | GET    | Real-time progress   |
| `/download-markdown/{id}` | GET    | Download Markdown    |
| `/health`                 | GET    | Health check         |

---

## 🌍 Docker Compose (Production)
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
          memory: 4G  # Adjust for your box
  streamlit:
    build: .
    ports:
      - "7601:7601"
    depends_on:
      - fastapi
```

---

## 🧪 Performance Baselines
| Test Case               | Pages | Time   | CPU Threads |
|--------------------------|-------|--------|-------------|
| 10 × PDFs (avg 50 p)    | 500   | 45 s   | 8           |
| 1 × 800-page contract   | 800   | 250 s  | 8           |
| 50 × images             | 50    | 30 s   | 8           |

---

## 🧰 Tech Stack
| Layer       | Technology                    |
|-------------|-------------------------------|
| Frontend    | Streamlit 1.38                |
| API         | FastAPI                       |
| OCR         | OCRmyPDF 16.5 + Tesseract     |
| Markdown    | PyMuPDF4LLM 0.0.17            |
| Parallelism | concurrent.futures            |
| Container   | Ubuntu 24.04, Python 3.12     |

---

## 🚦 Roadmap
- [ ] Excel/PowerPoint support  
- ?? Request

---

## 📄 License
MIT – free for personal and commercial use.  
Dependencies follow their own licenses.

💡 Issues or PRs? Submit via [GitHub Issues](https://github.com/ikantkode/exaOCR).
