# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

exaOCR is a production-ready OCR pipeline that converts documents (PDF, images, office files) into clean Markdown. It uses FastAPI for the backend API, Streamlit for the web UI, and processes files in parallel using **PaddleOCR PP-OCRv5** (CPU-only) with PyMuPDF4LLM for markdown extraction.

**Architecture:** `Streamlit <--> FastAPI <--> OCR Core` (docker-compose orchestrates both services)

**OCR Engine**: PaddleOCR PP-OCRv5 for handwritten text recognition and OCR (migrated from OCRmyPDF in 2026)

## Development Commands

### Build and Run
```bash
# Build and start all services
docker compose up --build

# Production mode (detached)
docker compose up -d --build

# Stop services
docker compose down
```

### Running Locally (without Docker)
```bash
# Install dependencies
pip install -r requirements.txt

# Run FastAPI backend (port 8000)
uvicorn app:app --host 0.0.0.0 --port 8000 --reload

# Run Streamlit frontend (port 7601)
streamlit run streamlit_app.py --server.port 7601
```

### System Dependencies (for local development)
- Ubuntu/Debian: `tesseract-ocr`, `tesseract-ocr-eng`, `ghostscript`, `libreoffice`, `unpaper`
- Python 3.12+

## Code Architecture

### Core Components

**`app.py`** - FastAPI backend (main OCR processing engine)
- Entry point: `app = FastAPI(...)`
- Thread-safe in-memory storage: `md_storage`, `progress_storage`, `batch_storage` with locks
- Parallel processing: `ThreadPoolExecutor(max_workers=10)` for batch jobs, `max_workers=8` for page-level parallelism
- Key functions:
  - `process_file()` - Main processing orchestrator for a single file
  - `process_single_page()` - Process one page with PaddleOCR PP-OCRv5
  - `process_batch_background()` - Async batch processor using `run_in_executor()`
  - `clean_markdown()` - Post-processing to clean up extracted markdown
  - `enhance_table_detection()` - Fallback for better table formatting
  - **Startup event**: Initializes PaddleOCR model at app startup

**`paddleocr_processor.py`** - PaddleOCR integration module (new)
- `init_paddleocr()` - Initialize PP-OCRv5 model (call at startup)
- `process_page_with_ppocr()` - Process PDF page with PaddleOCR
- `convert_pdf_page_to_image()` - Convert PDF page to PIL Image for OCR
- `convert_ocr_result_to_markdown()` - Convert OCR results to markdown
- `is_paddleocr_ready()` - Check if model is initialized

**`streamlit_app.py`** - Streamlit frontend
- Session state management for upload tracking and progress
- Calls FastAPI at `http://fastapi:8000` (docker) or `http://localhost:8000` (local)
- Polls `/progress/{file_id}` endpoint for real-time updates

### Processing Pipeline

1. **Input Conversion**: Images → PDF (img2pdf), Office docs → PDF (LibreOffice)
2. **Page Splitting**: PDF split into single-page files using PyMuPDF
3. **OCR Processing** (2-tier smart routing):
   - **Tier 1** (fast): If page has embedded text and not `force_ocr` → PyMuPDF4LLM (1-3 sec)
   - **Tier 2** (OCR): If no text or `force_ocr=true` → PaddleOCR PP-OCRv5 (10-30 sec)
4. **Markdown Extraction**: PaddleOCR returns text, converted to markdown
5. **Table Enhancement**: Fallback `enhance_table_detection()` for better table formatting
6. **Cleaning**: `clean_markdown()` post-processes output

### Batch Processing Flow

- POST `/upload/` → Returns `batch_id` immediately (background task starts)
- GET `/batch/{batch_id}/status` → Real-time progress tracking
- GET `/batch/{batch_id}/results` → Final results when complete
- DELETE `/batch/{batch_id}/cleanup` → Clean up stored data

### Storage Architecture

All data is stored in-memory (thread-safe dictionaries):
- `md_storage` - Final markdown content (keyed by UUID)
- `progress_storage` - Per-file progress (page counts, status)
- `batch_storage` - Batch metadata and aggregated results
- `recent_results` - Cached results for recently completed files

**Important**: No persistent storage - cleanup endpoints provided to free memory.

## Configuration

### PaddleOCR Model Initialization

Model is initialized once at FastAPI startup (see `app.py:49-58`):
```python
@app.on_event("startup")
async def startup_event():
    init_paddleocr(lang='en', use_gpu=False, show_log=False)
```

**Parameters:**
- `lang='en'`: Language (supports 109 languages)
- `use_gpu=False`: CPU-only mode
- `show_log=False`: Suppress verbose PaddleOCR logging

### Parallelism Tuning

Edit `app.py:42` for batch-level concurrency:
```python
executor = ThreadPoolExecutor(max_workers=10)
```

Edit `app.py:387` for page-level concurrency:
```python
with concurrent.futures.ThreadPoolExecutor(max_workers=min(page_count, 8)) as page_executor:
```

### OCR Behavior

Control via `force_ocr` query parameter:
- `force_ocr=true`: Always run PaddleOCR on all pages (slower, enables handwriting recognition)
- `force_ocr=false`: Use PyMuPDF4LLM for pages with existing text (fast), PaddleOCR for others

## API Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/upload/` | POST | Upload files, returns `batch_id` |
| `/batch/{batch_id}/status` | GET | Real-time batch progress |
| `/batch/{batch_id}/results` | GET | Final results when complete |
| `/progress/{file_id}` | GET | Per-file progress |
| `/download-markdown/{id}` | GET | Download markdown content |
| `/batch/{batch_id}/cleanup` | DELETE | Clean up batch resources |
| `/health` | GET | Health check |

## Common Issues

### PaddleOCR Model Not Initialized
If PaddleOCR fails to initialize at startup, pages will fall back to PyMuPDF4LLM. Check logs for:
```
Initializing PaddleOCR PP-OCRv5 model (CPU mode)...
PaddleOCR model loaded successfully
```

If initialization fails, the app will continue but OCR won't work properly.

### Thread Safety
Always use locks when accessing shared storage:
- `progress_lock` for `progress_storage`
- `batch_lock` for `batch_storage`

### Timeout Handling
- PaddleOCR: `timeout=600` seconds per page (10 minutes)
- LibreOffice conversion: `timeout=300` seconds
- HTTP client: `timeout=7200` seconds for batch upload

### Memory Management
Large documents consume significant memory during parallel processing. Monitor with `htop` and adjust `max_workers` based on available RAM. PaddleOCR models are ~100-200MB and loaded once at startup.

### Handwriting Recognition Quality
PP-OCRv5 is optimized for printed text. For complex/cursive handwriting:
- Quality may vary significantly
- Consider PaddleOCR-VL with GPU for better accuracy
- Test with real handwriting samples before production use

## File Formats

| Extension | Conversion Method |
|-----------|-------------------|
| `.pdf` | Direct processing |
| `.jpg`, `.png`, `.tiff`, `.bmp` | img2pdf → PDF |
| `.txt`, `.csv`, `.docx`, `.doc` | LibreOffice → PDF |
| `.xlsx`, `.pptx`, `.rtf` | Not yet supported |
