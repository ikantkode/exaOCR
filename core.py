import subprocess
import os
import tempfile
import img2pdf
import pymupdf4llm
import pymupdf
import time
import re
from typing import Tuple, Optional

def process_file(file_content: bytes, filename: str, force_ocr: bool) -> Tuple[str, Optional[str], Optional[bytes], Optional[str], float, int]:
    """
    Process a single file: Convert to PDF, apply OCR, convert to Markdown.
    Returns (filename, md_text, ocr_pdf_bytes, error, processing_time, page_count).
    """
    start_time = time.time()
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            # Save uploaded file to temp path
            input_path = os.path.join(tmpdir, filename)
            with open(input_path, "wb") as f:
                f.write(file_content)

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
                converted_pdf_name = os.path.splitext(filename)[0] + '.pdf'
                pdf_path = os.path.join(tmpdir, converted_pdf_name)
                if not os.path.exists(pdf_path):
                    raise FileNotFoundError(f"Converted PDF not found: {pdf_path}")
            else:
                return filename, None, None, "Unsupported file type.", time.time() - start_time, 0

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
                return filename, md_text, ocr_pdf_bytes, None, time.time() - start_time, page_count
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
                    return filename, fallback_text, ocr_pdf_bytes, f"Markdown conversion failed: {str(md_error)}. Used fallback block-based text extraction. OCR log: {ocr_result.stderr}", time.time() - start_time, page_count
                return filename, None, ocr_pdf_bytes, f"Markdown conversion failed: {str(md_error)}. No text extracted. OCR log: {ocr_result.stderr}", time.time() - start_time, page_count

    except subprocess.CalledProcessError as e:
        return filename, None, None, f"Process failed: {e.stderr.decode() if e.stderr else str(e)}", time.time() - start_time, 0
    except Exception as e:
        return filename, None, None, str(e), time.time() - start_time, 0