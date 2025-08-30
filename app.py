from fastapi import FastAPI, File, UploadFile, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
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
import threading
import asyncio
from concurrent.futures import ThreadPoolExecutor

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="OCR and Markdown Conversion API")

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Store OCRed PDFs, Markdown content, and progress in memory
ocr_pdf_storage = {}
md_storage = {}
progress_storage = {}  # {file_id: {"page_count": int, "pages_processed": int, "failed_pages": list, "status": str}}
progress_lock = threading.Lock()

# Thread pool for background processing
executor = ThreadPoolExecutor(max_workers=2)

# Store recent results for progress fallback
recent_results = {}

def has_embedded_text(pdf_path: str) -> bool:
    """Check if PDF has embedded text."""
    try:
        doc = pymupdf.open(pdf_path)
        for page in doc:
            text = page.get_text("text").strip()
            if text:
                doc.close()
                return True
        doc.close()
        return False
    except Exception as e:
        logger.error(f"Error checking embedded text: {e}")
        return False

def clean_markdown(md_text: str) -> str:
    """Clean Markdown output: normalize spaces, remove excessive dashes, format tables, remove artifacts."""
    if not md_text:
        return ""
    
    # Remove non-ASCII characters
    md_text = re.sub(r'[^\x00-\x7F]+', '', md_text)
    # Remove common artifacts (asterisks, repeating characters)
    md_text = re.sub(r'[\*\+\-=]{2,}', '', md_text)
    # Normalize multiple spaces to single space
    md_text = re.sub(r'\s+', ' ', md_text).strip()
    # Replace excessive dashes with single section break
    md_text = re.sub(r'-{3,}', '\n---\n', md_text)
    
    return md_text

def update_progress(file_id: str, pages_processed: int, status: str = "processing"):
    """Update progress for a file."""
    with progress_lock:
        if file_id in progress_storage:
            progress_storage[file_id]["pages_processed"] = pages_processed
            progress_storage[file_id]["status"] = status

def process_page(page_pdf_path: str, output_path: str, force_ocr: bool, has_text: bool, file_id: str, page_num: int) -> Optional[str]:
    """Process a single page with OCRmyPDF."""
    logger.info(f"Processing page {page_num + 1}: {page_pdf_path}")
    
    ocr_args = [
        'ocrmypdf', '-l', 'eng', '--tesseract-timeout', '100', 
        '--jobs', '1', '--optimize', '0', '--output-type', 'pdf'
    ]
    
    if not has_text and force_ocr:
        ocr_args.append('--force-ocr')
    else:
        ocr_args.append('--skip-text')
    
    if not has_text:
        ocr_args.extend(['--deskew', '--clean'])
    
    ocr_args.extend([page_pdf_path, output_path])
    
    try:
        start_time = time.time()
        result = subprocess.run(ocr_args, check=True, capture_output=True, text=True, timeout=300)
        logger.info(f"OCR completed for page {page_num + 1} in {time.time() - start_time:.2f}s")
        
        # Update progress
        update_progress(file_id, page_num + 1, "processing")
        return None
        
    except subprocess.CalledProcessError as e:
        error_msg = e.stderr if e.stderr else str(e)
        logger.warning(f"Initial OCR failed for page {page_num + 1}: {error_msg}")
        
        # Retry with --force-ocr if skip-text failed
        if '--skip-text' in ocr_args:
            logger.info(f"Retrying with --force-ocr for page {page_num + 1}")
            ocr_args = [
                'ocrmypdf', '-l', 'eng', '--tesseract-timeout', '100', 
                '--jobs', '1', '--optimize', '0', '--output-type', 'pdf', 
                '--force-ocr', page_pdf_path, output_path
            ]
            
            try:
                start_time = time.time()
                result = subprocess.run(ocr_args, check=True, capture_output=True, text=True, timeout=300)
                logger.info(f"OCR retry succeeded for page {page_num + 1} in {time.time() - start_time:.2f}s")
                update_progress(file_id, page_num + 1, "processing")
                return None
            except subprocess.CalledProcessError as retry_e:
                retry_error_msg = retry_e.stderr if retry_e.stderr else str(retry_e)
                logger.error(f"OCR retry failed for page {page_num + 1}: {retry_error_msg}")
                return f"Page {page_num + 1}: OCR retry failed: {retry_error_msg}"
        else:
            logger.error(f"OCR failed for page {page_num + 1}: {error_msg}")
            return f"Page {page_num + 1}: OCR failed: {error_msg}"
    
    except subprocess.TimeoutExpired:
        logger.error(f"OCR timed out for page {page_num + 1}")
        return f"Page {page_num + 1}: OCR timed out"

def process_file(file_content: bytes, filename: str, force_ocr: bool, file_id: str) -> Tuple[str, Optional[str], Optional[bytes], Optional[str], float, int]:
    """
    Process a single file: Convert to PDF, apply OCR per page concurrently, convert to Markdown.
    """
    start_time = time.time()
    md_text = None
    ocr_pdf_bytes = None
    error = None
    page_count = 0
    
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            # Save file content to temp path
            input_path = os.path.join(tmpdir, filename)
            logger.info(f"Saving file: {input_path}")
            with open(input_path, "wb") as f:
                f.write(file_content)

            ext = os.path.splitext(input_path)[1].lower()
            pdf_path = os.path.join(tmpdir, "input.pdf")

            # Convert to PDF if needed
            if ext == '.pdf':
                pdf_path = input_path
            elif ext in ['.jpg', '.jpeg', '.png', '.tiff', '.bmp']:
                logger.info(f"Converting image to PDF: {input_path}")
                with open(pdf_path, "wb") as f:
                    f.write(img2pdf.convert(input_path))
            elif ext in ['.txt', '.csv', '.docx', '.doc']:
                logger.info(f"Converting document to PDF: {input_path}")
                result = subprocess.run([
                    'libreoffice', '--headless', '--convert-to', 'pdf',
                    '--outdir', tmpdir, input_path
                ], check=True, capture_output=True, text=True, timeout=300)
                converted_pdf_name = os.path.splitext(filename)[0] + '.pdf'
                pdf_path = os.path.join(tmpdir, converted_pdf_name)
                if not os.path.exists(pdf_path):
                    raise FileNotFoundError(f"Converted PDF not found: {pdf_path}")
            else:
                return filename, None, None, "Unsupported file type.", time.time() - start_time, 0

            # Check for embedded text
            has_text = has_embedded_text(pdf_path)
            logger.info(f"PDF has embedded text: {has_text}")

            # Split PDF into pages
            doc = pymupdf.open(pdf_path)
            page_count = doc.page_count
            
            # Initialize progress for this file
            with progress_lock:
                progress_storage[file_id] = {
                    "page_count": page_count, 
                    "pages_processed": 0, 
                    "failed_pages": [],
                    "status": "processing"
                }
            
            page_paths = []
            for page_num in range(page_count):
                page_pdf = os.path.join(tmpdir, f"page_{page_num + 1}.pdf")
                page_doc = pymupdf.open()
                page_doc.insert_pdf(doc, from_page=page_num, to_page=page_num)
                page_doc.save(page_pdf)
                page_doc.close()
                page_paths.append((page_pdf, os.path.join(tmpdir, f"ocr_page_{page_num + 1}.pdf"), page_num))
            
            doc.close()

            # Process pages concurrently with limited workers
            page_errors = []
            with concurrent.futures.ThreadPoolExecutor(max_workers=min(page_count, 4)) as page_executor:
                futures = {
                    page_executor.submit(process_page, page_path, ocr_path, force_ocr, has_text, file_id, page_num): 
                    (page_path, ocr_path, page_num) 
                    for page_path, ocr_path, page_num in page_paths
                }
                
                for future in concurrent.futures.as_completed(futures):
                    page_path, ocr_path, page_num = futures[future]
                    try:
                        error_msg = future.result()
                        if error_msg:
                            page_errors.append(error_msg)
                            logger.warning(f"Page error: {error_msg}")
                        if not os.path.exists(ocr_path):
                            page_errors.append(f"Page {page_num + 1}: No output file created")
                    except Exception as e:
                        page_errors.append(f"Page {page_num + 1}: {str(e)}")
                        logger.error(f"Page processing exception: {e}")

            if page_errors:
                error = "; ".join(page_errors[:3])  # Show first 3 errors only
                if len(page_errors) > 3:
                    error += f"... and {len(page_errors) - 3} more errors"

            # Update status to indicate markdown conversion
            update_progress(file_id, page_count, "converting")
            
            # Reassemble OCRed pages
            final_doc = pymupdf.open()
            for _, ocr_path, _ in page_paths:
                if os.path.exists(ocr_path):
                    page_doc = pymupdf.open(ocr_path)
                    final_doc.insert_pdf(page_doc)
                    page_doc.close()
            
            ocr_pdf_path = os.path.join(tmpdir, "ocr_output.pdf")
            final_doc.save(ocr_pdf_path)
            final_doc.close()

            # Read OCRed PDF
            with open(ocr_pdf_path, "rb") as f:
                ocr_pdf_bytes = f.read()

            # Convert to Markdown
            try:
                md_text = pymupdf4llm.to_markdown(ocr_pdf_path, write_images=False, dpi=300)
                md_text = clean_markdown(md_text)
            except Exception as md_error:
                logger.warning(f"Markdown conversion failed, using fallback: {md_error}")
                # Fallback to simple text extraction
                doc = pymupdf.open(ocr_pdf_path)
                md_text = ""
                for page_num, page in enumerate(doc):
                    text = page.get_text("text").strip()
                    if text:
                        md_text += f"# Page {page_num + 1}\n\n{text}\n\n---\n\n"
                doc.close()
                md_text = clean_markdown(md_text)

            # Mark as completed
            update_progress(file_id, page_count, "completed")

    except subprocess.CalledProcessError as e:
        error_msg = e.stderr if e.stderr else str(e)
        logger.error(f"Process failed: {error_msg}")
        error = f"Process failed: {error_msg}"
        update_progress(file_id, 0, "error")
    except subprocess.TimeoutExpired:
        logger.error("Process timed out")
        error = "Process timed out"
        update_progress(file_id, 0, "error")
    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}")
        error = f"Unexpected error: {str(e)}"
        update_progress(file_id, 0, "error")
    
    processing_time = time.time() - start_time
    return filename, md_text, ocr_pdf_bytes, error, processing_time, page_count

async def process_files_async(files_data, force_ocr):
    """Process files asynchronously in thread pool"""
    loop = asyncio.get_event_loop()
    
    tasks = []
    for file_info in files_data:
        task = loop.run_in_executor(
            executor,
            process_file,
            file_info['content'],
            file_info['filename'],
            force_ocr,
            file_info['file_id']
        )
        tasks.append(task)
    
    return await asyncio.gather(*tasks)

@app.post("/upload/")
async def upload_files(background_tasks: BackgroundTasks, force_ocr: bool = True, files: List[UploadFile] = File(...)):
    if not files:
        raise HTTPException(status_code=400, detail="No files uploaded")

    # Prepare file data
    files_data = []
    for file in files:
        file_content = await file.read()
        file_id = str(uuid.uuid4())
        files_data.append({
            'content': file_content,
            'filename': file.filename,
            'file_id': file_id
        })

    # Process files asynchronously
    results = await process_files_async(files_data, force_ocr)
    
    # Format results
    formatted_results = []
    total_time = 0
    
    for filename, md_text, ocr_pdf_bytes, error, proc_time, page_count in results:
        total_time += proc_time
        
        pdf_id = str(uuid.uuid4()) if ocr_pdf_bytes else None
        md_id = str(uuid.uuid4()) if md_text else None
        
        if pdf_id:
            ocr_pdf_storage[pdf_id] = ocr_pdf_bytes
        if md_id:
            md_storage[md_id] = md_text.encode('utf-8') if md_text else b''
        
        formatted_results.append({
            "file_name": filename,
            "page_count": page_count,
            "processing_time_seconds": round(proc_time, 2),
            "status": "Success" if not error else f"Error: {error}",
            "content_preview": (md_text[:100] + "...") if md_text and len(md_text) > 100 else (md_text or "No content"),
            "markdown_content": md_text,
            "ocr_pdf_id": pdf_id,
            "markdown_id": md_id,
            "file_id": next(f['file_id'] for f in files_data if f['filename'] == filename)
        })

    return {
        "total_processing_time_seconds": round(total_time, 2),
        "results": formatted_results
    }

@app.get("/progress/{file_id}")
async def get_progress(file_id: str):
    with progress_lock:
        progress = progress_storage.get(file_id)
    
    if not progress:
        # If progress data doesn't exist, check recent results
        result = recent_results.get(file_id)
        if result:
            page_count = result.get("page_count", 0)
            return {
                "file_id": file_id,
                "page_count": page_count,
                "pages_processed": page_count,  # All pages processed
                "failed_pages": []
            }
        
        # If we can't find the file, return default values
        return {
            "file_id": file_id,
            "page_count": 0,
            "pages_processed": 0,
            "failed_pages": []
        }
    
    return {
        "file_id": file_id,
        "page_count": progress["page_count"],
        "pages_processed": progress["pages_processed"],
        "failed_pages": progress["failed_pages"]
    }

@app.get("/download-ocr-pdf/{pdf_id}")
async def download_ocr_pdf(pdf_id: str):
    ocr_pdf_bytes = ocr_pdf_storage.get(pdf_id)
    if not ocr_pdf_bytes:
        raise HTTPException(status_code=404, detail="OCR PDF not found")
    return StreamingResponse(
        content=io.BytesIO(ocr_pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename=ocr_document.pdf"}
    )

@app.get("/download-markdown/{md_id}")
async def download_markdown(md_id: str):
    md_bytes = md_storage.get(md_id)
    if not md_bytes:
        raise HTTPException(status_code=404, detail="Markdown file not found")
    return StreamingResponse(
        content=io.BytesIO(md_bytes),
        media_type="text/markdown",
        headers={"Content-Disposition": f"attachment; filename=converted_document.md"}
    )

@app.delete("/cleanup/{file_id}")
async def cleanup_file(file_id: str):
    """Clean up stored files by file ID"""
    if file_id in ocr_pdf_storage:
        del ocr_pdf_storage[file_id]
    if file_id in md_storage:
        del md_storage[file_id]
    if file_id in progress_storage:
        with progress_lock:
            del progress_storage[file_id]
    if file_id in recent_results:
        del recent_results[file_id]
    return {"message": f"Cleaned up resources for {file_id}"}

@app.get("/health")
async def health_check():
    return {"status": "healthy", "timestamp": time.time()}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)