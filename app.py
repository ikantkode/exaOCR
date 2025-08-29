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

# Store OCRed PDFs and Markdown content in memory
ocr_pdf_storage = {}
md_storage = {}

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

def clean_markdown(md_text: str) -> str:
    """Clean Markdown output: normalize spaces, remove excessive dashes, format tables."""
    # Remove non-ASCII characters
    md_text = re.sub(r'[^\x00-\x7F]+', '', md_text)
    # Normalize multiple spaces to single space
    md_text = re.sub(r'\s+', ' ', md_text).strip()
    # Replace excessive dashes with single section break
    md_text = re.sub(r'-{3,}', '\n---\n', md_text)
    # Clean table formatting: ensure consistent pipes and align columns
    lines = md_text.split('\n')
    cleaned_lines = []
    in_table = False
    table_rows = []
    for line in lines:
        line = line.strip()
        if line.startswith('|'):
            in_table = True
            # Normalize table row: remove extra spaces around pipes
            parts = [part.strip() for part in line.split('|') if part.strip()]
            if parts:
                cleaned_line = '|' + '|'.join(parts) + '|'
                table_rows.append(cleaned_line)
        else:
            if in_table and line:
                in_table = False
                # Ensure table has header separator
                if table_rows and len(table_rows) > 1:
                    header = table_rows[0]
                    num_cols = len(header.split('|')) - 2  # Exclude leading/trailing pipes
                    table_rows.insert(1, '|' + '|'.join(['---'] * num_cols) + '|')
                cleaned_lines.extend(table_rows)
                table_rows = []
                cleaned_lines.append('---')
            if line:
                cleaned_lines.append(line)
    if in_table and table_rows:
        # Finalize any remaining table
        if len(table_rows) > 1:
            header = table_rows[0]
            num_cols = len(header.split('|')) - 2
            table_rows.insert(1, '|' + '|'.join(['---'] * num_cols) + '|')
        cleaned_lines.extend(table_rows)
    return '\n'.join(cleaned_lines).strip()

def process_page(page_pdf_path: str, output_path: str, force_ocr: bool, has_text: bool) -> Optional[str]:
    """Process a single page with OCRmyPDF, return error message if failed."""
    logger.info(f"Processing page: {page_pdf_path} -> {output_path}")
    ocr_args = ['ocrmypdf', '-l', 'eng', '--tesseract-timeout', '100', '--jobs', '2', '--optimize', '0']
    if not has_text and force_ocr:
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

def process_file(file_content: bytes, filename: str, force_ocr: bool) -> Tuple[str, Optional[str], Optional[bytes], Optional[str], float, int]:
    """
    Process a single file: Convert to PDF, apply OCR per page concurrently, convert to Markdown.
    Returns (filename, md_text, ocr_pdf_bytes, error, processing_time, page_count).
    """
    start_time = time.time()
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            # Save file content to temp path
            input_path = os.path.join(tmpdir, filename)
            logger.info(f"Saving file: {input_path}")
            with open(input_path, "wb") as f:
                f.write(file_content)

            ext = os.path.splitext(input_path)[1].lower()
            pdf_path = os.path.join(tmpdir, "input.pdf")

            render_start = time.time()
            if ext == '.pdf':
                pdf_path = input_path
            elif ext in ['.jpg', '.jpeg', '.png', '.tiff', '.bmp']:
                logger.info(f"Converting image to PDF: {input_path}")
                with open(pdf_path, "wb") as f:
                    f.write(img2pdf.convert(input_path))
            elif ext in ['.txt', '.csv', '.docx', '.doc']:
                logger.info(f"Converting document to PDF: {input_path}")
                subprocess.run([
                    'libreoffice', '--headless', '--convert-to', 'pdf',
                    '--outdir', tmpdir, input_path
                ], check=True, capture_output=True)
                converted_pdf_name = os.path.splitext(filename)[0] + '.pdf'
                pdf_path = os.path.join(tmpdir, converted_pdf_name)
                if not os.path.exists(pdf_path):
                    raise FileNotFoundError(f"Converted PDF not found: {pdf_path}")
            else:
                return filename, None, None, "Unsupported file type.", time.time() - start_time, 0
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
            with concurrent.futures.ThreadPoolExecutor(max_workers=min(page_count, 8)) as executor:
                futures = [executor.submit(process_page, page_path, ocr_path, force_ocr, has_text) for page_path, ocr_path in page_paths]
                results = concurrent.futures.wait(futures)
                for future, (page_path, ocr_path) in zip(results.done, page_paths):
                    error = future.result()
                    if error:
                        return filename, None, None, f"Page processing failed: {error} for {ocr_path}", time.time() - start_time, page_count
                    if not os.path.exists(ocr_path):
                        return filename, None, None, f"no such file: '{ocr_path}'", time.time() - start_time, page_count
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

            # Try Markdown conversion with improved settings
            markdown_start = time.time()
            try:
                logger.info(f"Converting to Markdown: {ocr_pdf}")
                md_text = pymupdf4llm.to_markdown(ocr_pdf, write_images=False, dpi=300)
                md_text = clean_markdown(md_text)
                logger.info(f"Markdown conversion completed in {time.time() - markdown_start:.2f}s")
                return filename, md_text, ocr_pdf_bytes, None, time.time() - start_time, page_count
            except Exception as md_error:
                # Fallback to block-based text extraction
                logger.info(f"Falling back to pymupdf block-based text extraction: {ocr_pdf}")
                doc = pymupdf.open(ocr_pdf)
                fallback_text = ""
                for page in doc:
                    try:
                        blocks = page.get_text("blocks", flags=pymupdf.TEXTFLAGS_TEXT)
                        for block in blocks:
                            text = block[4].strip()
                            if text:
                                # Attempt to detect table-like structures
                                if '|' in text:
                                    fallback_text += f"[Page {page.number + 1} Table]\n{text}\n\n"
                                else:
                                    fallback_text += f"[Page {page.number + 1} Block]\n{text}\n\n"
                            else:
                                fallback_text += f"[Page {page.number + 1}: No extractable text in block]\n\n"
                    except Exception as page_error:
                        fallback_text += f"[Page {page.number + 1}: Error extracting text: {str(page_error)}]\n\n"
                doc.close()
                if fallback_text.strip():
                    fallback_text = clean_markdown(fallback_text)
                    logger.info(f"Fallback Markdown completed in {time.time() - markdown_start:.2f}s")
                    return filename, fallback_text, ocr_pdf_bytes, f"Markdown conversion failed: {str(md_error)}. Used fallback block-based text extraction.", time.time() - start_time, page_count
                logger.info(f"No text extracted in fallback: {time.time() - markdown_start:.2f}s")
                return filename, None, ocr_pdf_bytes, f"Markdown conversion failed: {str(md_error)}. No text extracted.", time.time() - start_time, page_count

    except subprocess.CalledProcessError as e:
        error_msg = e.stderr if isinstance(e.stderr, str) else e.stderr.decode('utf-8') if e.stderr else str(e)
        logger.error(f"Process failed: {error_msg}")
        return filename, None, None, f"Process failed: {error_msg}", time.time() - start_time, 0
    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}")
        return filename, None, None, str(e), time.time() - start_time, 0

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
        file_data = [(await file.read(), file.filename) for file in batch]
        with concurrent.futures.ProcessPoolExecutor(max_workers=4) as executor:
            futures = [executor.submit(process_file, content, filename, force_ocr) for content, filename in file_data]
            for future, (content, filename) in zip(concurrent.futures.as_completed(futures), file_data):
                name, md_text, ocr_pdf_bytes, error, proc_time, page_count = future.result()
                pdf_id = str(uuid.uuid4()) if ocr_pdf_bytes else None
                md_id = str(uuid.uuid4()) if md_text else None
                if pdf_id:
                    ocr_pdf_storage[pdf_id] = ocr_pdf_bytes
                if md_id:
                    md_storage[md_id] = md_text.encode('utf-8')
                results.append({
                    "file_name": name,
                    "page_count": page_count,
                    "processing_time_seconds": round(proc_time, 2),
                    "status": "Success" if not error else f"Error: {error}",
                    "content_preview": md_text[:100].replace('\n', ' ') + "..." if md_text else "No content",
                    "markdown_content": md_text,
                    "ocr_pdf_id": pdf_id,
                    "markdown_id": md_id
                })
                logger.info(f"Completed processing file: {name} in {proc_time:.2f}s")

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

@app.get("/download-markdown/{md_id}")
async def download_markdown(md_id: str):
    md_bytes = md_storage.get(md_id)
    if not md_bytes:
        raise HTTPException(status_code=404, detail="Markdown file not found")
    return StreamingResponse(
        content=io.BytesIO(md_bytes),
        media_type="text/markdown",
        headers={"Content-Disposition": f"attachment; filename=converted_{md_id}.md"}
    )

@app.delete("/cleanup/{pdf_id}")
async def cleanup_ocr_pdf(pdf_id: str):
    if pdf_id in ocr_pdf_storage:
        del ocr_pdf_storage[pdf_id]
    if pdf_id in md_storage:
        del md_storage[pdf_id]
    return {"message": f"OCR PDF and Markdown {pdf_id} removed from storage"}