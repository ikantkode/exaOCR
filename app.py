from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.responses import JSONResponse, FileResponse
import subprocess
import os
import tempfile
import img2pdf
import pymupdf4llm
import pymupdf
import concurrent.futures
import zipfile
import io
import time
import re
from typing import Tuple, Optional, List
import uuid

app = FastAPI(title="OCR and Markdown Conversion API")

# Store OCRed PDFs temporarily using UUIDs
ocr_pdf_storage = {}

def process_file(uploaded_file: UploadFile, force_ocr: bool) -> Tuple[str, Optional[str], Optional[bytes], Optional[str], float, int]:
    """
    Process a single file: Convert to PDF, apply OCR, convert to Markdown.
    Returns (original_name, md_text, ocr_pdf_bytes, error, processing_time, page_count).
    """
    start_time = time.time()
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            # Save uploaded file to temp path
            input_path = os.path.join(tmpdir, uploaded_file.filename)
            with open(input_path, "wb") as f:
                f.write(uploaded_file.file.read())

            ext = os.path.splitext(input_path)[1].lower()
            pdf_path = os.path.join(tmpdir, "input.pdf")

            if ext == '.pdf':
                pdf_path = input_path
            elif ext in ['.jpg', '.jpeg', '.png', '.tiff', '.bmp']:
                # Convert image to PDF using img2pdf
                with open(pdf_path, "wb") as f:
                    f.write(img2pdf.convert(input_path))
            elif ext in ['.txt', '.csv', '.docx', '.doc']:
                # Convert to PDF using LibreOffice
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

            # Run OCRmyPDF
            ocr_pdf = os.path.join(tmpdir, "ocr_output.pdf")
            ocr_args = ['ocrmypdf', '-l', 'eng', '--deskew', '--clean', '--tesseract-timeout', '300', '--jobs', '2']
            ocr_args.append('--force-ocr' if force_ocr else '--skip-text')
            ocr_args.extend([pdf_path, ocr_pdf])
            ocr_result = subprocess.run(ocr_args, check=True, capture_output=True, text=True)

            # Read OCRed PDF and get page count
            with open(ocr_pdf, "rb") as f:
                ocr_pdf_bytes = f.read()
            doc = pymupdf.open(ocr_pdf)
            page_count = doc.page_count

            # Try Markdown conversion with table support
            try:
                md_text = pymupdf4llm.to_markdown(ocr_pdf, write_images=False)
                # Sanitize Markdown: remove garbled chars, normalize whitespace
                md_text = re.sub(r'[^\x00-\x7F]+', '', md_text)  # Remove non-ASCII chars
                md_text = re.sub(r'\s+', ' ', md_text).strip()  # Normalize whitespace
                doc.close()
                return uploaded_file.filename, md_text, ocr_pdf_bytes, None, time.time() - start_time, page_count
            except Exception as md_error:
                # Fallback to block-based text extraction for better table structure
                fallback_text = ""
                for page in doc:
                    try:
                        blocks = page.get_text("blocks", flags=pymupdf.TEXTFLAGS_TEXT)
                        for block in blocks:
                            text = block[4].strip()  # Block tuple: (x0, y0, x1, y1, text, ...)
                            if text:
                                fallback_text += f"[Page {page.number + 1} Block]\n{text}\n\n"
                            else:
                                fallback_text += f"[Page {page.number + 1}: No extractable text in block]\n\n"
                    except Exception as page_error:
                        fallback_text += f"[Page {page.number + 1}: Error extracting text: {str(page_error)}]\n\n"
                doc.close()
                if fallback_text.strip():
                    # Sanitize fallback text
                    fallback_text = re.sub(r'[^\x00-\x7F]+', '', fallback_text)
                    fallback_text = re.sub(r'\s+', ' ', fallback_text).strip()
                    return uploaded_file.filename, fallback_text, ocr_pdf_bytes, f"Markdown conversion failed: {str(md_error)}. Used fallback block-based text extraction. OCR log: {ocr_result.stderr}", time.time() - start_time, page_count
                return uploaded_file.filename, None, ocr_pdf_bytes, f"Markdown conversion failed: {str(md_error)}. No text extracted. OCR log: {ocr_result.stderr}", time.time() - start_time, page_count

    except subprocess.CalledProcessError as e:
        return uploaded_file.filename, None, None, f"Process failed: {e.stderr.decode() if e.stderr else str(e)}", time.time() - start_time, 0
    except Exception as e:
        return uploaded_file.filename, None, None, str(e), time.time() - start_time, 0

@app.post("/upload/")
async def upload_files(force_ocr: bool = True, files: List[UploadFile] = File(...)):
    if not files:
        raise HTTPException(status_code=400, detail="No files uploaded")

    total_start_time = time.time()
    results = []
    ocr_pdf_ids = {}

    # Process files in parallel with processes
    with concurrent.futures.ProcessPoolExecutor(max_workers=12) as executor:
        future_to_file = {executor.submit(process_file, file, force_ocr): file for file in files}
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

    # Create ZIP file in memory for Markdowns
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
        for result in results:
            if result["markdown_content"]:
                md_name = os.path.splitext(result["file_name"])[0] + '.md'
                zip_file.writestr(md_name, result["markdown_content"])

    zip_buffer.seek(0)
    zip_filename = f"markdowns_{int(total_start_time)}.zip"
    zip_path = os.path.join("/app/output", zip_filename)
    with open(zip_path, "wb") as f:
        f.write(zip_buffer.getvalue())

    total_time = time.time() - total_start_time

    return {
        "total_processing_time_seconds": round(total_time, 2),
        "results": results,
        "zip_download_url": f"/download-zip/{zip_filename}"
    }

@app.get("/download-zip/{filename}")
async def download_zip(filename: str):
    file_path = os.path.join("/app/output", filename)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="ZIP file not found")
    return FileResponse(file_path, media_type="application/zip", filename=filename)

@app.get("/download-ocr-pdf/{pdf_id}")
async def download_ocr_pdf(pdf_id: str):
    ocr_pdf_bytes = ocr_pdf_storage.get(pdf_id)
    if not ocr_pdf_bytes:
        raise HTTPException(status_code=404, detail="OCR PDF not found")
    return FileResponse(
        path=io.BytesIO(ocr_pdf_bytes),
        media_type="application/pdf",
        filename=f"ocr_{pdf_id}.pdf"
    )

@app.delete("/cleanup/{pdf_id}")
async def cleanup_ocr_pdf(pdf_id: str):
    if pdf_id in ocr_pdf_storage:
        del ocr_pdf_storage[pdf_id]
        return {"message": f"OCR PDF {pdf_id} removed from storage"}
    raise HTTPException(status_code=404, detail="OCR PDF not found")