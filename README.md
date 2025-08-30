# exaOCR - Simple OCR for PDFs/Images/Word/Excel Files with FastAPI support.

## Overview

exaOCR is a web application built with [Streamlit](https://streamlit.io/) and hosted in a Docker container, designed to process multiple files (PDFs, images, TXT, CSV, or Word documents) by converting them to PDF, applying Optical Character Recognition (OCR) using [OCRmyPDF](https://github.com/ocrmypdf/OCRmyPDF), and generating Markdown output using [PyMuPDF4LLM](https://github.com/yourusername/PyMuPDF4LLM). The output is sanitized for clean text and table formatting, making it ideal for feeding into a Large Language Model (LLM) for embedding generation. The app supports parallel processing for efficiency, displays results in a Markdown table, and provides a ZIP file of all Markdown outputs. It includes a real-time progress bar, total elapsed time, per-file processing time, and page counts for PDFs.

The app is optimized for deployment on an Ubuntu 24.04 LTS server with a multi-core CPU (e.g., 24-thread Intel Xeon E5-2630 v2) and 64GB RAM, but can be adjusted for other hardware configurations.

We have plans to make it so a lot of other formats are supported. Stay tuned.

## Current Stage

- **Proof of Concept**: At this stage, the aim to ensure files are converting the fastest way possible on CPU only. We will be implementing our concepts into [pdfLLM](https://github.com/ikantkode/pdfLLM). Follow pdfLLM for a robust RAG App.
- **Concurrent processing**: Currently, files are sequentially processed - you're now able to send multiple requests for concurrent processing. (not to be confused with concurrent pages processing in the pipeline.)

## Features

- **File Support**: Upload multiple PDFs, images (JPG, PNG, JPEG, TIFF, BMP), TXT, CSV, and Word (DOCX, DOC) files (up to 200MB each).
- **Conversion Pipeline**:
  - Converts non-PDF files to PDF using [img2pdf](https://pypi.org/project/img2pdf/) (images) or [LibreOffice](https://www.libreoffice.org/) (text-based files).
  - Applies OCR with [OCRmyPDF](https://github.com/ocrmypdf/OCRmyPDF) and [Tesseract-OCR](https://github.com/tesseract-ocr/tesseract) to add a searchable text layer.
  - Converts PDFs to Markdown with table support using [PyMuPDF4LLM](https://github.com/yourusername/PyMuPDF4LLM).
- **Parallel Processing**: Uses Python's [`concurrent.futures`](https://docs.python.org/3/library/concurrent.futures.html) with 12 workers to process files concurrently, optimized for multi-core CPUs.
- **Output**:
  - Displays results in a Markdown table with file name, page count, processing time, status, and content preview.
  - Provides downloadable OCRed PDFs for debugging.
  - Exports all Markdown files as a ZIP for easy download.
  - Sanitizes output (removes non-ASCII characters, normalizes whitespace) for LLM compatibility.
- **User Interface**: [Streamlit](https://streamlit.io/) web app running on port 7601, with a progress bar, real-time timer, and a checkbox to force OCR on all pages.
- **Error Handling**: Includes fallback text extraction for complex PDFs (e.g., tables, diagrams) and detailed error messages with OCR logs.

## Libraries and Tools

- **Python 3.12**: [Python](https://www.python.org/)
- **Streamlit (1.38.0)**: [Streamlit](https://streamlit.io/)
- **OCRmyPDF (16.5.0)**: [OCRmyPDF GitHub](https://github.com/ocrmypdf/OCRmyPDF)
- **PyMuPDF4LLM (0.0.17)**: [PyMuPDF4LLM GitHub](https://github.com/yourusername/PyMuPDF4LLM)
- **PyMuPDF**: [PyMuPDF](https://pymupdf.readthedocs.io/en/latest/)
- **img2pdf (0.5.1)**: [img2pdf PyPI](https://pypi.org/project/img2pdf/)
- **Pillow (10.4.0)**: [Pillow](https://pypi.org/project/Pillow/)
- **Tesseract-OCR**: [Tesseract GitHub](https://github.com/tesseract-ocr/tesseract)
- **LibreOffice**: [LibreOffice](https://www.libreoffice.org/)
- **Ghostscript**: [Ghostscript](https://www.ghostscript.com/)
- **unpaper**: [Unpaper](https://www.flameeyes.eu/projects/unpaper/)
- **fonts-dejavu**: [DejaVu Fonts](https://dejavu-fonts.github.io/)
- **concurrent.futures**: [Python concurrent.futures](https://docs.python.org/3/library/concurrent.futures.html)
- **zipfile, io, re**: Standard Python libraries for ZIP creation and text sanitization

## File Structure

```
exaOCR/
├── Dockerfile              # Defines the Docker image with dependencies
├── requirements.txt        # Python package dependencies
├── app.py                  # Main Streamlit application code
├── docker-compose.yml      # Docker Compose configuration for deployment
└── README.md               # This file
```

## Prerequisites

- **Operating System**: Ubuntu 24.04 LTS or any Linux distribution with Docker support.
- **Hardware**: Multi-core CPU (4+ cores recommended) and at least 8GB RAM (32GB+ for large batches).
- **Software**:
  - [Docker](https://docs.docker.com/get-docker/)
  - [Docker Compose](https://docs.docker.com/compose/install/)
- **Network**: Ensure port 7601 is open for Streamlit access.

## Deployment Instructions

1. **Clone the Repository**:

   ```bash
   git clone https://github.com/ikantkode/exaOCR.git
   cd exaOCR
   ```

2. **Build and Run**:

   ```bash
   docker compose up --build
   ```

   - The build takes 10-20 minutes due to LibreOffice and dependency installation.
   - Access the app at `http://localhost:7601` in a web browser.
   - For background mode: `docker compose up -d --build`.
   - Stop with: `docker compose down`.

3. **Usage**:

   - Upload files via the web interface (supports multiple files).
   - Check "Force OCR" to apply OCR to all pages (useful for PDFs with poor text layers).
   - Monitor the progress bar, real-time timer, and results table.
   - Download OCRed PDFs for debugging or the ZIP file with Markdown outputs.

## Adjusting for Different Servers

The app is optimized for a server with a 24-thread CPU and 64GB RAM. To adapt for different hardware (e.g., fewer cores, less memory), modify the following in `app.py`:

- **Parallel Workers**:

  - **Location**: In `app.py`, search for `ProcessPoolExecutor(max_workers=12)`.
  - **Adjustment**: Set `max_workers` to half your CPU threads (e.g., 4 for an 8-thread CPU).
  - **Guidance**: Use `htop` to monitor CPU usage. If usage is <80%, increase `max_workers`. If memory swapping occurs, reduce it.

- **Batch Processing**:

  - **When to Use**: For low-memory servers (<16GB) or large batches (>20 files), process files in smaller groups.
  - **Example**:
    ```python
    batch_size = 8
    for i in range(0, len(uploaded_files), batch_size):
        batch = uploaded_files[i:i + batch_size]
        with concurrent.futures.ProcessPoolExecutor(max_workers=4) as executor:
            future_to_file = {executor.submit(process_file, file, force_ocr): file for file in batch}
            for future in concurrent.futures.as_completed(future_to_file):
                name, md_text, ocr_pdf_bytes, error, proc_time, page_count = future.result()
                results.append((name, md_text, ocr_pdf_bytes, error, proc_time, page_count))
                completed += 1
                progress_text.text(f"Processing {completed}/{num_files} files...")
                progress_bar.progress(completed / num_files)
    ```

- **OCR Settings**:

  - Reduce `--jobs` for slower CPUs and adjust `--tesseract-downsample` for complex PDFs.

- **Memory Constraints**:

  - Monitor with `free -m`.
  - Limit Docker container resources in `docker-compose.yml`.

## Performance Notes

- Baseline: On a 24-thread CPU with 64GB RAM, processing 54 pages took \~115 seconds with 12 workers.
- Optimization:
  - Increase `max_workers` for high-core CPUs.
  - Add batching for large batches or low-memory systems.
  - Adjust OCR settings for complex PDFs.
- Monitoring: Use `htop` and `free -m`.

## LLM Embedding Considerations

- Markdown output is sanitized for clean LLM input.
- Verify table formatting in the ZIP output for complex PDFs.

## License

This project is for personal or internal use. Ensure compliance with licenses for dependencies.

For issues or contributions, submit a pull request or open an issue on the [GitHub repository](https://github.com/ikantkode/exaOCR).