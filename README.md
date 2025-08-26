# exaOCR

## Overview

exaOCR is a web application built with Streamlit and hosted in a Docker container, designed to process multiple files (PDFs, images, TXT, CSV, or Word documents) by converting them to PDF, applying Optical Character Recognition (OCR) using OCRmyPDF, and generating Markdown output using PyMuPDF4LLM. The output is sanitized for clean text and table formatting, making it ideal for feeding into a Large Language Model (LLM) for embedding generation. The app supports parallel processing for efficiency, displays results in a Markdown table, and provides a ZIP file of all Markdown outputs. It includes a real-time progress bar, total elapsed time, per-file processing time, and page counts for PDFs.

The app is optimized for deployment on an Ubuntu 24.04 LTS server with a multi-core CPU (e.g., 24-thread Intel Xeon E5-2630 v2) and 64GB RAM, but can be adjusted for other hardware configurations.

## Features

- **File Support**: Upload multiple PDFs, images (JPG, PNG, JPEG, TIFF, BMP), TXT, CSV, and Word (DOCX, DOC) files (up to 200MB each).
- **Conversion Pipeline**:
  - Converts non-PDF files to PDF using `img2pdf` (images) or LibreOffice (text-based files).
  - Applies OCR with OCRmyPDF and Tesseract to add a searchable text layer.
  - Converts PDFs to Markdown with table support using PyMuPDF4LLM.
- **Parallel Processing**: Uses Python's `ProcessPoolExecutor` with 12 workers to process files concurrently, optimized for multi-core CPUs.
- **Output**:
  - Displays results in a Markdown table with file name, page count, processing time, status, and content preview.
  - Provides downloadable OCRed PDFs for debugging.
  - Exports all Markdown files as a ZIP for easy download.
  - Sanitizes output (removes non-ASCII characters, normalizes whitespace) for LLM compatibility.
- **User Interface**: Streamlit-based web app running on port 7601, with a progress bar, real-time timer, and a checkbox to force OCR on all pages.
- **Error Handling**: Includes fallback text extraction for complex PDFs (e.g., tables, diagrams) and detailed error messages with OCR logs.

## Libraries and Tools

- **Python 3.12**: Core programming language.
- **Streamlit (1.38.0)**: Web framework for the user interface.
- **OCRmyPDF (16.5.0)**: Applies OCR with options like `--force-ocr`, `--deskew`, `--clean`, and `--jobs 2`.
- **PyMuPDF4LLM (0.0.17)**: Converts OCRed PDFs to Markdown, with table detection and image-skipping options.
- **PyMuPDF**: Used for fallback text extraction (block-based for tables) and page counting.
- **img2pdf (0.5.1)**: Converts images to PDFs.
- **Pillow (10.4.0)**: Dependency for image processing in `img2pdf`.
- **Tesseract-OCR**: OCR engine for OCRmyPDF (English language pack included).
- **LibreOffice**: Converts TXT, CSV, and Word files to PDF.
- **Ghostscript**: Handles PDF processing for OCRmyPDF.
- **unpaper**: Cleans images during OCR.
- **fonts-dejavu**: Ensures consistent font rendering for LibreOffice.
- **concurrent.futures**: Python library for parallel processing.
- **zipfile, io, re**: Python libraries for ZIP creation and text sanitization.

## File Structure

```
exaOCR/
├── Dockerfile              # Defines the Docker image with dependencies
├── requirements.txt        # Python package dependencies
├── app.py                 # Main Streamlit application code
├── docker-compose.yml     # Docker Compose configuration for deployment
└── README.md              # This file
```

## Prerequisites

- **Operating System**: Ubuntu 24.04 LTS or any Linux distribution with Docker support.
- **Hardware**: Multi-core CPU (4+ cores recommended) and at least 8GB RAM (32GB+ for large batches).
- **Software**:
  - Docker: Install with `sudo apt install docker.io` (Ubuntu) or equivalent.
  - Docker Compose: Install with `sudo apt install docker-compose` or equivalent.
- **Network**: Ensure port 7601 is open for Streamlit access.

## Deployment Instructions

1. **Clone the Repository**:
   ```bash
   git clone https://github.com/<your-username>/exaOCR.git
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
  - **Adjustment**: Set `max_workers` to half your CPU threads (e.g., 4 for an 8-thread CPU) to avoid overloading. For example:
    ```python
    with concurrent.futures.ProcessPoolExecutor(max_workers=4) as executor:
    ```
  - **Guidance**: Use `htop` to monitor CPU usage. If usage is <80%, increase `max_workers`. If memory swapping occurs, reduce it.

- **Batch Processing**:
  - **When to Use**: For low-memory servers (<16GB) or large batches (>20 files), process files in smaller groups to reduce resource usage.
  - **How to Add**: In `app.py`, replace the `ProcessPoolExecutor` loop with batching:
    ```python
    batch_size = 8  # Adjust based on memory
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
  - **Guidance**: Set `batch_size` to 4-10 for low-memory systems. Test with `htop` to ensure memory usage stays below 80%.

- **OCR Settings**:
  - **Location**: In `app.py`, search for `ocr_args = [...]`.
  - **Adjustment**: For slower CPUs, reduce Tesseract threads with `--jobs 1` (default is 2). For higher quality on complex PDFs, add `--tesseract-downsample 600`:
    ```python
    ocr_args = ['ocrmypdf', '-l', 'eng', '--deskew', '--clean', '--tesseract-timeout', '300', '--jobs', '1', '--tesseract-downsample', '600']
    ```
  - **Guidance**: Use `--jobs 1` on 4-core CPUs; keep `--jobs 2` for 8+ cores. Increase downsample for scanned PDFs with small text.

- **Memory Constraints**:
  - **Check**: Use `free -m` to monitor RAM. If free memory drops below 1GB, reduce `max_workers` or add batching.
  - **Docker Limits**: In `docker-compose.yml`, limit container resources:
    ```yaml
    services:
      ocr-app:
        build: .
        ports:
          - "7601:7601"
        command: ["streamlit", "run", "app.py", "--server.port=7601", "--server.address=0.0.0.0"]
        deploy:
          resources:
            limits:
              cpus: '4'
              memory: 8G
    ```
  - **Guidance**: Set `cpus` to your core count and `memory` to ~50% of available RAM.

## Performance Notes

- **Baseline**: On a 24-thread CPU with 64GB RAM, processing 54 pages (1, 7, and 46-page PDFs) took ~115 seconds with 12 workers.
- **Optimization**:
  - Increase `max_workers` to 16-20 for high-core CPUs if CPU usage is low.
  - Add batching for large batches (>20 files) or low-memory systems.
  - Adjust OCR settings (e.g., `--tesseract-downsample 600`) for complex PDFs.
- **Monitoring**: Use `htop` and `free -m` to check CPU/memory usage during processing.

## Troubleshooting

- **Errors like `not a textpage of this page`**: Download OCRed PDFs to verify text layers. If empty, add `--tesseract-downsample 600` or install `tesseract-ocr-all` in `Dockerfile`.
- **Slow Processing**: Reduce `max_workers` or add batching. Check CPU usage with `htop`.
- **Poor Table Output**: If tables are not formatted correctly, consider switching to `marker-pdf` by updating `requirements.txt` and `app.py`.
- **Memory Issues**: Limit Docker resources in `docker-compose.yml` or reduce `max_workers`.

## LLM Embedding Considerations

- Markdown output is sanitized (no non-ASCII characters, normalized whitespace) and supports tables (`| Col1 | Col2 |`) for clean LLM input.
- For complex PDFs (e.g., table-heavy TOCs), verify table formatting in the ZIP output. If needed, customize `app.py` for specific LLM formatting requirements.

## License

This project is for personal or internal use. Ensure compliance with licenses for dependencies (e.g., Tesseract, LibreOffice).

For issues or contributions, submit a pull request or open an issue on the GitHub repository.
