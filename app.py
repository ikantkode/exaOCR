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

# Store markdown content and progress in memory
md_storage = {}
progress_storage = {}
recent_results = {}
progress_lock = threading.Lock()
executor = ThreadPoolExecutor(max_workers=10)

# Batch processing storage
batch_storage = {}
batch_lock = threading.Lock()

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
    """Enhanced markdown cleaning that produces clean, readable output."""
    if not md_text:
        return ""

    lines = md_text.split('\n')
    cleaned_lines = []
    in_table = False
    
    for i, line in enumerate(lines):
        line = line.strip()
        if not line:
            # Preserve single empty lines, remove multiple
            if cleaned_lines and cleaned_lines[-1] != "":
                cleaned_lines.append("")
            continue

        # Detect proper markdown tables (with multiple pipes and consistent structure)
        pipe_count = line.count('|')
        
        # Real table rows have multiple pipes and reasonable structure
        if pipe_count >= 3 and '|' in line:
            # Check if this looks like a real table
            cells = [c.strip() for c in line.split('|') if c.strip()]
            
            # If we have a reasonable number of cells (2-10 typically)
            if 2 <= len(cells) <= 10:
                in_table = True
                # Clean up the table row
                cleaned_line = "| " + " | ".join(cells) + " |"
                cleaned_lines.append(cleaned_line)
            else:
                # Too many cells - probably false positive, treat as text
                in_table = False
                # Convert to regular text
                text = line.replace('|', ' ').strip()
                text = re.sub(r'\s+', ' ', text)  # Normalize spaces
                cleaned_lines.append(text)
        else:
            # Not a table line
            if in_table and pipe_count > 0:
                # End of table
                in_table = False
                cleaned_lines.append("")  # Add spacing after table
            
            # Regular text line - just normalize spacing
            if line.startswith('#'):
                # Preserve headers
                cleaned_lines.append(line)
            else:
                # Clean up regular text
                line = re.sub(r'\s+', ' ', line)
                cleaned_lines.append(line)
    
    # Remove multiple consecutive empty lines
    result = []
    prev_empty = False
    for line in cleaned_lines:
        if line == "":
            if not prev_empty:
                result.append(line)
            prev_empty = True
        else:
            result.append(line)
            prev_empty = False
    
    return "\n".join(result).strip()

def enhance_table_detection(page: pymupdf.Page) -> str:
    """Enhanced table detection and formatting with better structure."""
    text_dict = page.get_text("dict")
    blocks = text_dict["blocks"]

    output_lines = []
    
    for block in blocks:
        if "lines" not in block:
            continue

        block_text = []
        for line in block["lines"]:
            spans = line["spans"]
            if not spans:
                continue

            # Get all text spans in this line
            line_texts = []
            span_positions = []
            
            for span in spans:
                text = span["text"].strip()
                if text:
                    line_texts.append(text)
                    span_positions.append(span["bbox"][0])  # x-coordinate
            
            if not line_texts:
                continue
            
            # Check if this looks like a form/table row with distinct columns
            # Multiple spans with significant horizontal separation
            if len(line_texts) > 1:
                # Calculate gaps between spans
                gaps = []
                for i in range(len(span_positions) - 1):
                    gaps.append(span_positions[i+1] - span_positions[i])
                
                avg_gap = sum(gaps) / len(gaps) if gaps else 0
                
                # If gaps are relatively large and consistent, treat as table
                if avg_gap > 50:  # Significant horizontal spacing
                    # Join spans with clear separation for readability
                    line_text = " | ".join(line_texts)
                    block_text.append(line_text)
                else:
                    # Close spacing - treat as normal text
                    line_text = " ".join(line_texts)
                    block_text.append(line_text)
            else:
                # Single span - normal text
                block_text.append(line_texts[0])
        
        if block_text:
            # Join lines in this block with newlines
            output_lines.append("\n".join(block_text))
            output_lines.append("")  # Empty line between blocks
    
    return "\n".join(output_lines)

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
        # OCR processing with corrected logic
        ocr_args = [
            'ocrmypdf', '-l', 'eng', 
            '--tesseract-timeout', '300',
            '--jobs', '1', 
            '--optimize', '0', 
            '--output-type', 'pdf',
            '--tesseract-pagesegmode', '1',
        ]

        # FIXED: Properly handle force_ocr flag
        if force_ocr:
            # Force OCR on all pages regardless of existing text
            ocr_args.append('--force-ocr')
            logger.info(f"Page {page_num + 1}: Forcing OCR (--force-ocr)")
        elif has_text:
            # Skip OCR if page already has text and force_ocr is False
            ocr_args.append('--skip-text')
            logger.info(f"Page {page_num + 1}: Skipping OCR (--skip-text)")
        else:
            # Page has no text, need OCR with preprocessing
            ocr_args.extend(['--deskew', '--clean'])
            logger.info(f"Page {page_num + 1}: Performing OCR with cleanup")

        ocr_args.extend([page_pdf_path, ocr_page_pdf_path])

        # Run OCRmyPDF
        try:
            result = subprocess.run(
                ocr_args, 
                check=True, 
                capture_output=True, 
                text=True, 
                timeout=600
            )
            logger.info(f"Page {page_num + 1}: OCR completed successfully")
            
        except subprocess.CalledProcessError as e:
            # Exit code 15 means "pages already had text" - this is SUCCESS, not failure!
            if e.returncode == 15:
                logger.info(f"Page {page_num + 1}: Exit code 15 - page already has text (this is normal)")
                # Continue to markdown extraction - the output PDF was still created
            else:
                # Other exit codes are actual errors
                logger.warning(f"Page {page_num + 1}: OCR failed with exit code {e.returncode}, attempting fallback")
                
                # Fallback: use original page without OCR
                try:
                    fallback_doc = pymupdf.open(page_pdf_path)
                    enhanced_text = enhance_table_detection(fallback_doc[0])
                    fallback_doc.close()
                    
                    if enhanced_text.strip():
                        return page_num, f"# Page {page_num + 1}\n\n{enhanced_text}\n\n---\n\n", None
                    else:
                        return page_num, f"# Page {page_num + 1}\n\n[OCR failed - no text extracted]\n\n---\n\n", f"Page {page_num + 1}: OCR failed with exit code {e.returncode}"
                except Exception as fallback_error:
                    logger.error(f"Page {page_num + 1}: Fallback extraction failed: {fallback_error}")
                    return page_num, None, f"Page {page_num + 1}: {str(e)}"

        # Enhanced markdown extraction
        try:
            # Check if OCR output exists
            if not os.path.exists(ocr_page_pdf_path):
                logger.warning(f"Page {page_num + 1}: OCR output file not found, using original")
                ocr_page_pdf_path = page_pdf_path
                
            page_markdown = pymupdf4llm.to_markdown(ocr_page_pdf_path, write_images=False, dpi=300)

            # Try enhanced table detection
            fallback_doc = pymupdf.open(ocr_page_pdf_path)
            enhanced_text = enhance_table_detection(fallback_doc[0])
            fallback_doc.close()

            # Prefer pymupdf4llm output, only use enhanced if it's significantly better
            # (i.e., has clear table structure with reasonable cell counts)
            use_enhanced = False
            if enhanced_text:
                enhanced_lines = [l for l in enhanced_text.split('\n') if '|' in l]
                markdown_lines = [l for l in page_markdown.split('\n') if '|' in l]
                
                # Use enhanced if it has tables and fewer false positives
                if enhanced_lines and len(enhanced_lines) < len(enhanced_text.split('\n')) * 0.3:
                    # Less than 30% of lines are tables - probably real tables
                    use_enhanced = True
            
            final_markdown = enhanced_text if use_enhanced else page_markdown

            if final_markdown and final_markdown.strip():
                return page_num, f"# Page {page_num + 1}\n\n{final_markdown}\n\n---\n\n", None
            else:
                return page_num, "", None

        except Exception as md_error:
            logger.warning(f"Page {page_num + 1}: Markdown extraction failed, using fallback: {md_error}")
            # Fallback to enhanced text extraction
            try:
                fallback_doc = pymupdf.open(ocr_page_pdf_path if os.path.exists(ocr_page_pdf_path) else page_pdf_path)
                enhanced_text = enhance_table_detection(fallback_doc[0])
                fallback_doc.close()

                if enhanced_text.strip():
                    return page_num, f"# Page {page_num + 1}\n\n{enhanced_text}\n\n---\n\n", None
                else:
                    return page_num, "", None
            except Exception as fallback_error:
                logger.error(f"Page {page_num + 1}: All extraction methods failed: {fallback_error}")
                return page_num, "", f"Page {page_num + 1}: Extraction failed"

    except subprocess.TimeoutExpired as e:
        logger.error(f"Page {page_num + 1}: OCR timeout after 10 minutes")
        return page_num, None, f"Page {page_num + 1}: OCR timeout"
    except Exception as e:
        logger.error(f"Page {page_num + 1}: Unexpected error: {str(e)}")
        return page_num, None, f"Page {page_num + 1}: {str(e)}"

def process_file(file_content: bytes, filename: str, force_ocr: bool, file_id: str, batch_id: Optional[str] = None) -> Tuple[str, Optional[str], Optional[str], float, int]:
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
            logger.info(f"Force OCR setting: {force_ocr}")

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
                        with progress_lock:
                            if file_id in progress_storage:
                                progress_storage[file_id]["failed_pages"].append(page_num + 1)
                    
                    if page_markdown:
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

            with progress_lock:
                recent_results[file_id] = {
                    "page_count": page_count,
                    "status": "completed" if not error else "error"
                }

            # Update batch progress
            if batch_id:
                with batch_lock:
                    if batch_id in batch_storage:
                        batch_storage[batch_id]["completed_files"] += 1
                        batch_storage[batch_id]["file_statuses"][file_id] = "completed" if not error else "error"

    except subprocess.CalledProcessError as e:
        error_msg = e.stderr if e.stderr else str(e)
        logger.error(f"Process failed: {error_msg}")
        error = f"Process failed: {error_msg}"
        update_progress(file_id, 0, "error")
        if batch_id:
            with batch_lock:
                if batch_id in batch_storage:
                    batch_storage[batch_id]["completed_files"] += 1
                    batch_storage[batch_id]["file_statuses"][file_id] = "error"
    except subprocess.TimeoutExpired:
        logger.error("Process timed out")
        error = "Process timed out"
        update_progress(file_id, 0, "error")
        if batch_id:
            with batch_lock:
                if batch_id in batch_storage:
                    batch_storage[batch_id]["completed_files"] += 1
                    batch_storage[batch_id]["file_statuses"][file_id] = "error"
    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}")
        error = f"Unexpected error: {str(e)}"
        update_progress(file_id, 0, "error")
        if batch_id:
            with batch_lock:
                if batch_id in batch_storage:
                    batch_storage[batch_id]["completed_files"] += 1
                    batch_storage[batch_id]["file_statuses"][file_id] = "error"

    processing_time = time.time() - start_time
    return filename, md_text, error, processing_time, page_count

def process_batch_background(files_data: list, force_ocr: bool, batch_id: str):
    """Process batch in background and store results."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    try:
        tasks = []
        for file_info in files_data:
            task = loop.run_in_executor(
                executor,
                process_file,
                file_info['content'],
                file_info['filename'],
                force_ocr,
                file_info['file_id'],
                batch_id  # Pass batch_id for progress tracking
            )
            tasks.append(task)

        results = loop.run_until_complete(asyncio.gather(*tasks, return_exceptions=True))

        # Store results for batch
        batch_results = []
        total_time = 0

        for i, result in enumerate(results):
            if isinstance(result, Exception):
                filename = files_data[i]['filename']
                file_id = files_data[i]['file_id']
                batch_results.append({
                    "file_name": filename,
                    "page_count": 0,
                    "processing_time_seconds": 0,
                    "status": f"Error: {str(result)}",
                    "content_preview": "No content",
                    "markdown_content": None,
                    "markdown_id": None,
                    "file_id": file_id
                })
            else:
                filename, md_text, error, proc_time, page_count = result
                file_id = files_data[i]['file_id']
                total_time += proc_time

                md_id = str(uuid.uuid4()) if md_text else None
                if md_id:
                    md_storage[md_id] = md_text.encode('utf-8') if md_text else b''

                batch_results.append({
                    "file_name": filename,
                    "page_count": page_count,
                    "processing_time_seconds": round(proc_time, 2),
                    "status": "Success" if not error else f"Error: {error}",
                    "content_preview": (md_text[:100] + "...") if md_text and len(md_text) > 100 else (md_text or "No content"),
                    "markdown_content": md_text,
                    "markdown_id": md_id,
                    "file_id": file_id
                })

        # Store results in batch_storage
        with batch_lock:
            if batch_id in batch_storage:
                batch_storage[batch_id]["results"] = batch_results
                batch_storage[batch_id]["total_processing_time"] = round(total_time, 2)
                batch_storage[batch_id]["status"] = "completed"

        logger.info(f"Batch {batch_id} completed: {len(batch_results)} files processed in {total_time:.2f}s")

    finally:
        loop.close()

@app.post("/upload/")
async def upload_files(force_ocr: bool = True, background_tasks: BackgroundTasks = BackgroundTasks(), files: List[UploadFile] = File(...)):
    """
    Upload files for batch OCR processing.

    Returns immediately with a batch_id. Use GET /batch/{batch_id}/status to check progress.
    """
    if not files:
        raise HTTPException(status_code=400, detail="No files uploaded")

    # Generate batch_id
    batch_id = str(uuid.uuid4())

    # Prepare file data
    files_data = []
    file_ids = []
    for file in files:
        file_content = await file.read()
        file_id = str(uuid.uuid4())
        file_ids.append(file_id)
        files_data.append({
            'content': file_content,
            'filename': file.filename,
            'file_id': file_id
        })

    # Initialize batch storage
    with batch_lock:
        batch_storage[batch_id] = {
            "batch_id": batch_id,
            "total_files": len(files),
            "completed_files": 0,
            "file_ids": file_ids,
            "file_statuses": {fid: "pending" for fid in file_ids},
            "status": "processing",
            "start_time": time.time(),
            "results": None,
            "total_processing_time": None
        }

    # Start background processing
    background_tasks.add_task(process_batch_background, files_data, force_ocr, batch_id)

    logger.info(f"Batch {batch_id} started with {len(files)} files")

    # Return immediately
    return {
        "batch_id": batch_id,
        "total_files": len(files),
        "file_ids": file_ids,
        "status": "processing",
        "message": "Batch processing started. Use GET /batch/{batch_id}/status to check progress."
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
                "failed_pages": [],
                "status": result.get("status", "completed")
            }

        return {
            "file_id": file_id,
            "page_count": 0,
            "pages_processed": 0,
            "failed_pages": [],
            "status": "unknown"
        }

    return {
        "file_id": file_id,
        "page_count": progress["page_count"],
        "pages_processed": progress["pages_processed"],
        "failed_pages": progress["failed_pages"],
        "status": progress["status"]
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
    cleaned = []
    if file_id in md_storage:
        del md_storage[file_id]
        cleaned.append("md_storage")
    if file_id in progress_storage:
        with progress_lock:
            del progress_storage[file_id]
        cleaned.append("progress_storage")
    if file_id in recent_results:
        del recent_results[file_id]
        cleaned.append("recent_results")
    
    return {"message": f"Cleaned up resources for {file_id}", "cleaned": cleaned}

@app.get("/batch/{batch_id}/status")
async def get_batch_status(batch_id: str):
    """
    Get the status of a batch processing job.

    Returns aggregate progress and per-file status.
    """
    with batch_lock:
        batch = batch_storage.get(batch_id)

    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")

    # Calculate progress percentage
    total = batch["total_files"]
    completed = batch["completed_files"]
    progress_pct = (completed / total * 100) if total > 0 else 0

    # Get per-file progress details
    file_progress = []
    for file_id in batch["file_ids"]:
        with progress_lock:
            file_prog = progress_storage.get(file_id)

        if file_prog:
            file_progress.append({
                "file_id": file_id,
                "page_count": file_prog["page_count"],
                "pages_processed": file_prog["pages_processed"],
                "failed_pages": file_prog["failed_pages"],
                "status": file_prog["status"]
            })
        else:
            # File hasn't started yet
            file_progress.append({
                "file_id": file_id,
                "page_count": 0,
                "pages_processed": 0,
                "failed_pages": [],
                "status": batch["file_statuses"].get(file_id, "pending")
            })

    return {
        "batch_id": batch_id,
        "total_files": total,
        "completed_files": completed,
        "in_progress_files": total - completed,
        "failed_files": sum(1 for s in batch["file_statuses"].values() if s == "error"),
        "progress_percent": round(progress_pct, 2),
        "status": batch["status"],
        "elapsed_time_seconds": round(time.time() - batch["start_time"], 2),
        "files": file_progress
    }

@app.get("/batch/{batch_id}/results")
async def get_batch_results(batch_id: str):
    """
    Get the final results of a completed batch.

    Returns all processed files with markdown content.
    """
    with batch_lock:
        batch = batch_storage.get(batch_id)

    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")

    if batch["status"] != "completed":
        return {
            "batch_id": batch_id,
            "status": batch["status"],
            "message": "Batch not completed yet. Use GET /batch/{batch_id}/status to check progress.",
            "total_files": batch["total_files"],
            "completed_files": batch["completed_files"]
        }

    return {
        "batch_id": batch_id,
        "total_files": batch["total_files"],
        "completed_files": batch["completed_files"],
        "total_processing_time_seconds": batch["total_processing_time"],
        "status": batch["status"],
        "results": batch["results"]
    }

@app.delete("/batch/{batch_id}/cleanup")
async def cleanup_batch(batch_id: str):
    """Clean up stored batch data by batch ID"""
    cleaned = []
    with batch_lock:
        if batch_id in batch_storage:
            batch = batch_storage[batch_id]

            # Clean up all markdown files in this batch
            if batch.get("results"):
                for result in batch["results"]:
                    md_id = result.get("markdown_id")
                    if md_id and md_id in md_storage:
                        del md_storage[md_id]
                        cleaned.append(f"md_id:{md_id}")

            # Clean up batch storage
            del batch_storage[batch_id]
            cleaned.append("batch_storage")

            # Clean up progress storage for files in this batch
            for file_id in batch.get("file_ids", []):
                if file_id in progress_storage:
                    with progress_lock:
                        del progress_storage[file_id]
                    cleaned.append(f"progress:{file_id}")
                if file_id in recent_results:
                    del recent_results[file_id]
                    cleaned.append(f"recent_results:{file_id}")

    return {"message": f"Cleaned up resources for batch {batch_id}", "cleaned": cleaned}

@app.get("/health")
async def health_check():
    with batch_lock:
        active_batches = sum(1 for b in batch_storage.values() if b["status"] == "processing")

    return {
        "status": "healthy",
        "timestamp": time.time(),
        "active_files": len(progress_storage),
        "stored_results": len(recent_results),
        "active_batches": active_batches,
        "total_batches": len(batch_storage)
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)