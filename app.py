from fastapi import FastAPI, File, UploadFile, HTTPException
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

# Store markdown content and progress in memory
md_storage = {}
progress_storage = {}
progress_lock = threading.Lock()
executor = ThreadPoolExecutor(max_workers=10)

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
    """Enhanced markdown cleaning that preserves tables."""
    if not md_text:
        return ""
    
    lines = md_text.split('\n')
    cleaned_lines = []
    
    for line in lines:
        line = line.strip()
        if not line:
            continue
            
        # Fix table formatting
        if '|' in line and not line.startswith('#'):
            line = re.sub(r'\s*\|\s*', '|', line)
            line = re.sub(r'\|{2,}', '|', line)
            line = re.sub(r'^\||\|$', '', line)
            line = '| ' + line + ' |'
            
            # Fix table separators
            if re.match(r'^\|[-:\s]+\|', line):
                line = re.sub(r'[-:\s]+', '-', line)
        
        # Clean up but preserve table separators
        if not (line.startswith('|') and '---' in line):
            line = re.sub(r'-{4,}', '---', line)
        
        cleaned_lines.append(line)
    
    md_text = '\n'.join(cleaned_lines)
    md_text = re.sub(r'\n{3,}', '\n\n', md_text)
    return md_text.strip()

def enhance_table_detection(page: pymupdf.Page) -> str:
    """Enhanced table detection and formatting."""
    text_dict = page.get_text("dict")
    blocks = text_dict["blocks"]
    
    table_lines = []
    
    for block in blocks:
        if "lines" not in block:
            continue
            
        for line in block["lines"]:
            spans = line["spans"]
            if not spans:
                continue
                
            text = spans[0]["text"].strip()
            if not text:
                continue
                
            # Detect table-like structures
            vertical_positions = [span["bbox"][1] for span in spans]
            horizontal_positions = [span["bbox"][0] for span in spans]
            
            # If multiple spans are vertically aligned, likely a table
            if len(spans) > 1 and abs(max(vertical_positions) - min(vertical_positions)) < 20:
                cells = [span["text"].strip() for span in spans]
                table_line = "| " + " | ".join(cells) + " |"
                table_lines.append(table_line)
            else:
                if table_lines:  # End of table
                    table_lines.append("")  # Add spacing
                table_lines.append(text)
    
    return "\n".join(table_lines)

def update_progress(file_id: str, pages_processed: int, status: str = "processing"):
    """Update progress for a file."""
    with progress_lock:
        if file_id in progress_storage:
            progress_storage[file_id]["pages_processed"] = pages_processed
            progress_storage[file_id]["status"] = status

def process_single_page(page_data: Tuple[int, str, str, bool, str]) -> Tuple[int, str, Optional[str]]:
    """Process a single page with enhanced table formatting."""
    page_num, page_pdf_path, ocr_page_pdf_path, force_ocr, has_text = page_data
    
    try:
        # OCR processing
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
        
        ocr_args.extend([page_pdf_path, ocr_page_pdf_path])
        
        result = subprocess.run(ocr_args, check=True, capture_output=True, text=True, timeout=300)
        
        # Enhanced markdown extraction
        try:
            page_markdown = pymupdf4llm.to_markdown(ocr_page_pdf_path, write_images=False, dpi=300)
            
            # Try enhanced table detection
            fallback_doc = pymupdf.open(ocr_page_pdf_path)
            enhanced_text = enhance_table_detection(fallback_doc[0])
            fallback_doc.close()
            
            # Use enhanced text if it has better table formatting
            if '|' in enhanced_text and enhanced_text.count('|') > 2:
                page_markdown = enhanced_text
            
            if page_markdown and page_markdown.strip():
                return page_num, f"# Page {page_num + 1}\n\n{page_markdown}\n\n---\n\n", None
            else:
                return page_num, "", None
                
        except Exception:
            # Fallback to enhanced text extraction
            fallback_doc = pymupdf.open(ocr_page_pdf_path)
            enhanced_text = enhance_table_detection(fallback_doc[0])
            fallback_doc.close()
            
            if enhanced_text.strip():
                return page_num, f"# Page {page_num + 1}\n\n{enhanced_text}\n\n---\n\n", None
            else:
                return page_num, "", None
        
    except Exception as e:
        return page_num, None, f"Page {page_num + 1}: {str(e)}"

def process_file(file_content: bytes, filename: str, force_ocr: bool, file_id: str) -> Tuple[str, Optional[str], Optional[str], float, int]:
    """Process file with parallel OCR and enhanced table formatting."""
    start_time = time.time()
    md_text = None
    error = None
    page_count = 0
    
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            # Save file and convert to PDF
            input_path = os.path.join(tmpdir, filename)
            with open(input_path, "wb") as f:
                f.write(file_content)

            ext = os.path.splitext(input_path)[1].lower()
            pdf_path = os.path.join(tmpdir, "input.pdf")

            if ext == '.pdf':
                pdf_path = input_path
            elif ext in ['.jpg', '.jpeg', '.png', '.tiff', '.bmp']:
                with open(pdf_path, "wb") as f:
                    f.write(img2pdf.convert(input_path))
            elif ext in ['.txt', '.csv', '.docx', '.doc']:
                result = subprocess.run([
                    'libreoffice', '--headless', '--convert-to', 'pdf',
                    '--outdir', tmpdir, input_path
                ], check=True, capture_output=True, text=True, timeout=300)
                converted_pdf_name = os.path.splitext(filename)[0] + '.pdf'
                pdf_path = os.path.join(tmpdir, converted_pdf_name)
                if not os.path.exists(pdf_path):
                    raise FileNotFoundError(f"Converted PDF not found: {pdf_path}")
            else:
                return filename, None, "Unsupported file type.", time.time() - start_time, 0

            has_text = has_embedded_text(pdf_path)
            logger.info(f"PDF has embedded text: {has_text}")

            # Split into pages
            doc = pymupdf.open(pdf_path)
            page_count = doc.page_count
            
            with progress_lock:
                progress_storage[file_id] = {
                    "page_count": page_count, 
                    "pages_processed": 0, 
                    "failed_pages": [],
                    "status": "processing"
                }

            # Prepare page data for parallel processing
            page_data_list = []
            for page_num in range(page_count):
                page_pdf = os.path.join(tmpdir, f"page_{page_num + 1}.pdf")
                ocr_page_pdf = os.path.join(tmpdir, f"ocr_page_{page_num + 1}.pdf")
                
                page_doc = pymupdf.open()
                page_doc.insert_pdf(doc, from_page=page_num, to_page=page_num)
                page_doc.save(page_pdf)
                page_doc.close()
                
                page_data_list.append((page_num, page_pdf, ocr_page_pdf, force_ocr, has_text))
            
            doc.close()

            # Process pages in parallel
            page_errors = []
            all_markdown = [""] * page_count
            
            with concurrent.futures.ThreadPoolExecutor(max_workers=min(page_count, 8)) as page_executor:
                future_to_page = {
                    page_executor.submit(process_single_page, page_data): page_data[0]
                    for page_data in page_data_list
                }
                
                for future in concurrent.futures.as_completed(future_to_page):
                    page_num, page_markdown, page_error = future.result()
                    
                    if page_error:
                        page_errors.append(page_error)
                        logger.warning(page_error)
                    elif page_markdown:
                        all_markdown[page_num] = page_markdown
                    
                    update_progress(file_id, page_num + 1, "processing")

            # Combine all markdown
            if all_markdown:
                combined_markdown = "".join(all_markdown)
                md_text = clean_markdown(combined_markdown)
            
            if page_errors:
                error = "; ".join(page_errors[:3])
                if len(page_errors) > 3:
                    error += f"... and {len(page_errors) - 3} more errors"

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
    return filename, md_text, error, processing_time, page_count

@app.post("/upload/")
async def upload_files(force_ocr: bool = True, files: List[UploadFile] = File(...)):
    if not files:
        raise HTTPException(status_code=400, detail="No files uploaded")

    files_data = []
    for file in files:
        file_content = await file.read()
        file_id = str(uuid.uuid4())
        files_data.append({
            'content': file_content,
            'filename': file.filename,
            'file_id': file_id
        })

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
    
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    formatted_results = []
    total_time = 0
    
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            filename = files_data[i]['filename']
            formatted_results.append({
                "file_name": filename,
                "page_count": 0,
                "processing_time_seconds": 0,
                "status": f"Error: {str(result)}",
                "content_preview": "No content",
                "markdown_content": None,
                "ocr_pdf_id": None,
                "markdown_id": None,
                "file_id": files_data[i]['file_id']
            })
        else:
            filename, md_text, error, proc_time, page_count = result
            total_time += proc_time
            
            md_id = str(uuid.uuid4()) if md_text else None
            if md_id:
                md_storage[md_id] = md_text.encode('utf-8') if md_text else b''
            
            formatted_results.append({
                "file_name": filename,
                "page_count": page_count,
                "processing_time_seconds": round(proc_time, 2),
                "status": "Success" if not error else f"Error: {error}",
                "content_preview": (md_text[:100] + "...") if md_text and len(md_text) > 100 else (md_text or "No content"),
                "markdown_content": md_text,
                "ocr_pdf_id": None,
                "markdown_id": md_id,
                "file_id": files_data[i]['file_id']
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
        result = recent_results.get(file_id)
        if result:
            page_count = result.get("page_count", 0)
            return {
                "file_id": file_id,
                "page_count": page_count,
                "pages_processed": page_count,
                "failed_pages": []
            }
        
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