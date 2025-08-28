from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
import subprocess
import os
import tempfile
import img2pdf
import pymupdf4llm
import pymupdf
import concurrent.futures
import io
import time
import re
from typing import Tuple, Optional, List
import uuid
import logging

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="OCR and Markdown Conversion API")

# Store OCRed PDFs temporarily using UUIDs
ocr_pdf_storage = {}

def has_embedded_text(pdf_path: str) -> bool:
    """Check if PDF has embedded text."""
    doc = pymupdf.open(pdf_path)
    for page in doc:
        text = page.get_text("text").strip()
        if text:
            doc.close()
            return True
    doc.close()
    return False

def process_page(page_pdf_path: str, output_path: str, force_ocr: bool, has_text: bool) -> Optional[str]:
    """Process a single page with OCRmyPDF, return error message if failed."""
    logger.info(f"Processing page: {page_pdf_path} -> {output_path}")
    ocr_args = ['ocrmypdf', '-l', 'eng', '--tesseract-timeout', '100', '--jobs', '2']
    if not has_text or force_ocr:
        ocr_args.append('--force-ocr')
    else:
        ocr_args.append('--skip-text')
    if not has_text:
        ocr_args.extend(['--deskew', '--clean'])
    ocr_args.extend([page_pdf_path, output_path])
    try:
        start_time = time.time()
        result = subprocess.run(ocr_args, check=True, capture_output=True, text=True)
        logger.info(f"OCR completed for {page_pdf_path} in {time.time() - start_time:.2f}s")
        return None
    except subprocess.CalledProcessError as e:
        error_msg = e.stderr if isinstance(e.stderr, str) else e.stderr.decode('utf-8') if e.stderr else str(e)
        logger.error(f"OCR failed for {page_pdf_path}: {error_msg}")
        return f"OCR failed: {error_msg}"

def process_file(uploaded_file: UploadFile, force_ocr: bool) -> Tuple[str, Optional[str], Optional[bytes], Optional[str], float, int]:
    """
    Process a single file: Convert to PDF, apply OCR per page concurrently, convert to Markdown.
    Returns (original_name, md_text, ocr_pdf_bytes, error, processing_time, page_count).
    """
    start_time = time.time()
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            # Save uploaded file to temp path
            input_path = os.path.join(tmpdir, uploaded_file.filename)
            logger.info(f"Saving uploaded file: {input_path}")
            with open(input_path, "wb") as f:
                f.write(uploaded_file.file.read())

            ext = os.path.splitext(input_path)[1].lower()
            pdf_path = os.path.join(tmpdir, "input.pdf")

            render_start = time.time()
            if ext == '.pdf':
                pdf_path = input_path
            elif ext in ['.jpg', '.jpeg', '.png', '.tiff', '.bmp']:
                # Convert image to PDF using img2pdf
                logger.info(f"Converting image to PDF: {input_path}")
                with open(pdf_path, "wb") as f:
                    f.write(img2pdf.convert(input_path))
            elif ext in ['.txt', '.csv', '.docx', '.doc']:
                # Convert to PDF using LibreOffice
                logger.info(f"Converting document to PDF: {input_path}")
                subprocess.run([
                    'libreoffice', '--headless', '--convert-to', 'pdf',
                    '--outdir', tmpdir, input_path
                ], check=True, capture_output=True)
                converted_pdf_name = os.path.splitext(uploaded_file.filename)[0] + '.pdf'
                pdf_path = os.path.join(tmpdir, converted_pdf_name)
                if not os.path.exists(pdf_path):
                    raise FileNotFoundError(f"Converted PDF not found: {pdf_path}")
            else:
                return uploaded_file.filename, None, None, "Unsupported file type.", time.time() - start_time, 0
            logger.info(f"Rendering completed in {time.time() - render_start:.2f}s")

            # Check for embedded text
            has_text = has_embedded_text(pdf_path)
            logger.info(f"PDF has embedded text: {has_text}")

            # Split PDF into pages
            split_start = time.time()
            doc = pymupdf.open(pdf_path)
            page_count = doc.page_count
            page_paths = []
            for page_num in range(page_count):
                page_pdf = os.path.join(tmpdir, f"page_{page_num + 1}.pdf")
                page_doc = pymupdf.open()
                page_doc.insert_pdf(doc, from_page=page_num, to_page=page_num)
                page_doc.save(page_pdf)
                page_doc.close()
                page_paths.append((page_pdf, os.path.join(tmpdir, f"ocr_page_{page_num + 1}.pdf")))
            doc.close()
            logger.info(f"Splitting PDF into {page_count} pages in {time.time() - split_start:.2f}s")

            # Process pages concurrently
            ocr_start = time.time()
            logger.info(f"Processing {page_count} pages concurrently")
            with concurrent.futures.ThreadPoolExecutor(max_workers=min(page_count, 6)) as executor:
                futures = [executor.submit(process_page, page_path, ocr_path, force_ocr, has_text) for page_path, ocr_path in page_paths]
                results = concurrent.futures.wait(futures)
                for future, (page_path, ocr_path) in zip(results.done, page_paths):
                    error = future.result()
                    if error:
                        return uploaded_file.filename, None, None, f"Page processing failed: {error} for {ocr_path}", time.time() - start_time, page_count
                    if not os.path.exists(ocr_path):
                        return uploaded_file.filename, None, None, f"no such file: '{ocr_path}'", time.time() - start_time, page_count
            logger.info(f"OCR completed in {time.time() - ocr_start:.2f}s")

            # Reassemble OCRed pages
            reassemble_start = time.time()
            ocr_pdf = os.path.join(tmpdir, "ocr_output.pdf")
            logger.info(f"Reassembling OCRed pages into: {ocr_pdf}")
            final_doc = pymupdf.open()
            for _, ocr_path in page_paths:
                page_doc = pymupdf.open(ocr_path)
                final_doc.insert_pdf(page_doc)
                page_doc.close()
            final_doc.save(ocr_pdf)
            final_doc.close()
            logger.info(f"Reassembly completed in {time.time() - reassemble_start:.2f}s")

            # Read OCRed PDF
            logger.info(f"Reading OCRed PDF: {ocr_pdf}")
            with open(ocr_pdf, "rb") as f:
                ocr_pdf_bytes = f.read()

            # Try Markdown conversion with table support
            markdown_start = time.time()
            try:
                logger.info(f"Converting to Markdown: {ocr_pdf}")
                md_text = pymupdf4llm.to_markdown(ocr_pdf, write_images=False)
                # Sanitize Markdown: remove garbled chars, normalize whitespace
                md_text = re.sub(r'[^\x00-\x7F]+', '', md_text)  # Remove non-ASCII chars
                md_text = re.sub(r'\s+', ' ', md_text).strip()  # Normalize whitespace
                logger.info(f"Markdown conversion completed in {time.time() - markdown_start:.2f}s")
                return uploaded_file.filename, md_text, ocr_pdf_bytes, None, time.time() - start_time, page_count
            except Exception as md_error:
                # Fallback to direct pymupdf text extraction
                logger.info(f"Falling back to pymupdf text extraction: {ocr_pdf}")
                doc = pymupdf.open(ocr_pdf)
                fallback_text = ""
                for page in doc:
                    try:
                        text = page.get_text("text").strip()
                        if text:
                            fallback_text += f"[Page {page.number + 1}]\n{text}\n\n"
                        else:
                            fallback_text += f"[Page {page.number + 1}: No extractable text]\n\n"
                    except Exception as page_error:
                        fallback_text += f"[Page {page.number + 1}: Error extracting text: {str(page_error)}]\n\n"
                doc.close()
                if fallback_text.strip():
                    # Sanitize fallback text
                    fallback_text = re.sub(r'[^\x00-\x7F]+', '', fallback_text)
                    fallback_text = re.sub(r'\s+', ' ', fallback_text).strip()
                    logger.info(f"Fallback Markdown completed in {time.time() - markdown_start:.2f}s")
                    return uploaded_file.filename, fallback_text, ocr_pdf_bytes, f"Markdown conversion failed: {str(md_error)}. Used fallback text extraction.", time.time() - start_time, page_count
                logger.info(f"No text extracted in fallback: {time.time() - markdown_start:.2f}s")
                return uploaded_file.filename, None, ocr_pdf_bytes, f"Markdown conversion failed: {str(md_error)}. No text extracted.", time.time() - start_time, page_count

    except subprocess.CalledProcessError as e:
        error_msg = e.stderr if isinstance(e.stderr, str) else e.stderr.decode('utf-8') if e.stderr else str(e)
        logger.error(f"Process failed: {error_msg}")
        return uploaded_file.filename, None, None, f"Process failed: {error_msg}", time.time() - start_time, 0
    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}")
        return uploaded_file.filename, None, None, str(e), time.time() - start_time, 0

@app.post("/upload/")
async def upload_files(force_ocr: bool = True, files: List[UploadFile] = File(...)):
    if not files:
        raise HTTPException(status_code=400, detail="No files uploaded")

    total_start_time = time.time()
    results = []

    # Process files in batches of 4 to prevent overload
    batch_size = 4
    for i in range(0, len(files), batch_size):
        batch = files[i:i + batch_size]
        logger.info(f"Processing batch of {len(batch)} files")
        with concurrent.futures.ProcessPoolExecutor(max_workers=4) as executor:
            future_to_file = {executor.submit(process_file, file, force_ocr): file for file in batch}
            for future in concurrent.futures.as_completed(future_to_file):
                name, md_text, ocr_pdf_bytes, error, proc_time, page_count = future.result()
                pdf_id = str(uuid.uuid4()) if ocr_pdf_bytes else None
                if pdf_id:
                    ocr_pdf_storage[pdf_id] = ocr_pdf_bytes
                results.append({
                    "file_name": name,
                    "page_count": page_count,
                    "processing_time_seconds": round(proc_time, 2),
                    "status": "Success" if not error else f"Error: {error}",
                    "content_preview": md_text[:100].replace('\n', ' ') + "..." if md_text else "No content",
                    "markdown_content": md_text,
                    "ocr_pdf_id": pdf_id
                })

    total_time = time.time() - total_start_time
    logger.info(f"Total processing time: {total_time:.2f} seconds for {len(results)} files")

    return {
        "total_processing_time_seconds": round(total_time, 2),
        "results": results
    }

@app.get("/download-ocr-pdf/{pdf_id}")
async def download_ocr_pdf(pdf_id: str):
    ocr_pdf_bytes = ocr_pdf_storage.get(pdf_id)
    if not ocr_pdf_bytes:
        raise HTTPException(status_code=404, detail="OCR PDF not found")
    return StreamingResponse(
        content=io.BytesIO(ocr_pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename=ocr_{pdf_id}.pdf"}
    )

@app.delete("/cleanup/{pdf_id}")
async def cleanup_ocr_pdf(pdf_id: str):
    if pdf_id in ocr_pdf_storage:
        del ocr_pdf_storage[pdf_id]
        return {"message": f"OCR PDF {pdf_id} removed from storage"}
    raise HTTPException(status_code=404, detail="OCR PDF not found")