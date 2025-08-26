import streamlit as st
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
from typing import Tuple, Optional

st.title("OCR and Convert to Markdown")

st.write("Upload one or more PDFs, images (JPG/PNG/JPEG/TIFF/BMP), TXT, CSV, or Word (DOCX/DOC) files. The app will convert each to PDF if needed, apply OCR using OCRmyPDF, and convert to Markdown with table support. Results are processed in parallel and available as a ZIP file for LLM embedding.")

force_ocr = st.checkbox("Force OCR on all pages (may overwrite existing text)", value=True)

uploaded_files = st.file_uploader("Choose files", type=['pdf', 'jpg', 'png', 'jpeg', 'tiff', 'bmp', 'txt', 'csv', 'docx', 'doc'], accept_multiple_files=True)

def process_file(uploaded_file, force_ocr: bool) -> Tuple[str, Optional[str], Optional[bytes], Optional[str], float, int]:
    """
    Process a single file: Convert to PDF, apply OCR, convert to Markdown.
    Returns (original_name, md_text, ocr_pdf_bytes, error, processing_time, page_count).
    """
    start_time = time.time()
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            # Save uploaded file to temp path
            input_path = os.path.join(tmpdir, uploaded_file.name)
            with open(input_path, "wb") as f:
                f.write(uploaded_file.getvalue())

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
                converted_pdf_name = os.path.splitext(uploaded_file.name)[0] + '.pdf'
                pdf_path = os.path.join(tmpdir, converted_pdf_name)
                if not os.path.exists(pdf_path):
                    raise FileNotFoundError(f"Converted PDF not found: {pdf_path}")
            else:
                return uploaded_file.name, None, None, "Unsupported file type.", time.time() - start_time, 0

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
                return uploaded_file.name, md_text, ocr_pdf_bytes, None, time.time() - start_time, page_count
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
                    return uploaded_file.name, fallback_text, ocr_pdf_bytes, f"Markdown conversion failed: {str(md_error)}. Used fallback block-based text extraction. OCR log: {ocr_result.stderr}", time.time() - start_time, page_count
                return uploaded_file.name, None, ocr_pdf_bytes, f"Markdown conversion failed: {str(md_error)}. No text extracted. OCR log: {ocr_result.stderr}", time.time() - start_time, page_count

    except subprocess.CalledProcessError as e:
        return uploaded_file.name, None, None, f"Process failed: {e.stderr.decode() if e.stderr else str(e)}", time.time() - start_time, 0
    except Exception as e:
        return uploaded_file.name, None, None, str(e), time.time() - start_time, 0

if uploaded_files:
    num_files = len(uploaded_files)
    progress_text = st.empty()
    progress_bar = st.progress(0)
    timer_text = st.empty()

    # Start total timer
    total_start_time = time.time()
    results = []
    completed = 0

    # Process files in parallel with processes
    with concurrent.futures.ProcessPoolExecutor(max_workers=12) as executor:
        future_to_file = {executor.submit(process_file, file, force_ocr): file for file in uploaded_files}
        # Update total elapsed time in real-time
        while completed < num_files:
            elapsed = time.time() - total_start_time
            timer_text.text(f"Total elapsed time: {elapsed:.2f} seconds")
            time.sleep(1)  # Update every second
            completed = sum(1 for future in future_to_file if future.done())
            progress_text.text(f"Processing {completed}/{num_files} files...")
            progress_bar.progress(completed / num_files)

        # Collect results
        for future in concurrent.futures.as_completed(future_to_file):
            name, md_text, ocr_pdf_bytes, error, proc_time, page_count = future.result()
            results.append((name, md_text, ocr_pdf_bytes, error, proc_time, page_count))

    progress_text.text("Processing complete!")
    timer_text.text(f"Total elapsed time: {time.time() - total_start_time:.2f} seconds")

    # Display results in a Markdown table
    table = "| File Name | Pages | Processing Time (s) | Status | Content Preview |\n"
    table += "|-----------|-------|---------------------|--------|-----------------|\n"
    for name, md_text, ocr_pdf_bytes, error, proc_time, page_count in results:
        status = "Success" if not error else f"Error: {error}"
        preview = md_text[:100].replace('\n', ' ') + "..." if md_text else "No content"
        table += f"| {name} | {page_count} | {proc_time:.2f} | {status} | {preview} |\n"
    st.markdown(table)

    # Create ZIP file in memory for Markdowns
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
        for name, md_text, ocr_pdf_bytes, error, proc_time, page_count in results:
            if error:
                st.error(f"Error processing {name}: {error}")
            else:
                st.subheader(f"Converted Text for {name}")
                st.text_area(f"Content for {name}", md_text, height=300)
                md_name = os.path.splitext(name)[0] + '.md'
                zip_file.writestr(md_name, md_text)

            # Provide download button for OCRed PDF
            if ocr_pdf_bytes:
                st.download_button(
                    label=f"Download OCRed PDF for {name}",
                    data=ocr_pdf_bytes,
                    file_name=f"ocr_{name}",
                    mime="application/pdf"
                )

    # Provide download button for ZIP if there are successful conversions
    if any(md_text for _, md_text, _, _, _, _ in results):
        zip_buffer.seek(0)
        st.download_button(
            label="Download All Markdowns as ZIP",
            data=zip_buffer,
            file_name="markdowns.zip",
            mime="application/zip"
        )