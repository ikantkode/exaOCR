import streamlit as st
import httpx
import time
import threading
import uuid
import logging
import json
import os
from httpx import RequestError

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Set page config
st.set_page_config(page_title="OCR and Convert to Markdown", layout="centered", page_icon="📄")

# Custom CSS
st.markdown("""
<style>
.stProgress > div > div > div { background-color: #4CAF50; }
.upload-box { border: 2px dashed #ccc; border-radius: 10px; padding: 20px; text-align: center; margin: 10px 0; background-color: #fafafa; }
.file-item { padding: 10px; margin: 5px 0; border-radius: 5px; background-color: #f9f9f9; border-left: 4px solid #4CAF50; }
.status-waiting { color: #666; } .status-processing { color: #2196F3; } .status-completed { color: #4CAF50; } .status-error { color: #f44336; }
</style>
""", unsafe_allow_html=True)

# Initialize session state
def init_session_state():
    defaults = {
        'processing_started': False,
        'file_progress': {},
        'uploaded_files': [],
        'uploaded_files_data': {},
        'processing_complete': False,
        'results': [],
        'total_time': 0.0,
        'batch_id': None,
        'force_ocr': True,
        'start_time': 0,
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value

init_session_state()

# UI Components
st.title("📄 OCR and Convert to Markdown")
st.markdown("Convert PDFs, images, and documents to searchable Markdown with OCR technology. **Supported formats:** PDF, JPG, PNG, JPEG, TIFF, BMP, TXT, CSV, DOCX, DOC")

# Upload section
with st.container():
    st.markdown("### 📤 Upload Files")
    st.markdown('<div class="upload-box">', unsafe_allow_html=True)
    force_ocr = st.checkbox("Force OCR on all pages", value=st.session_state.force_ocr, help="Process all pages with OCR even if text is detected")
    st.session_state.force_ocr = force_ocr

    uploaded_files = st.file_uploader(
        "Drag and drop files here or click to browse",
        type=['pdf', 'jpg', 'png', 'jpeg', 'tiff', 'bmp', 'txt', 'csv', 'docx', 'doc'],
        accept_multiple_files=True,
        help="Maximum 200MB per file",
        label_visibility="collapsed"
    )
    st.markdown('</div>', unsafe_allow_html=True)

# Update session state with new files
if uploaded_files and uploaded_files != st.session_state.uploaded_files:
    st.session_state.uploaded_files = uploaded_files
    st.session_state.uploaded_files_data = {
        file.name: {'content': file.getvalue(), 'type': file.type, 'size': file.size}
        for file in uploaded_files
    }

    st.session_state.file_progress = {
        file.name: {
            "progress": 0.0, "page_count": 0, "pages_processed": 0,
            "status": "Waiting to start", "status_class": "status-waiting"
        } for file in uploaded_files
    }
    st.session_state.processing_started = False
    st.session_state.processing_complete = False
    st.session_state.results = []
    st.session_state.total_time = 0.0
    st.session_state.batch_id = None
    st.session_state.start_time = 0

# Display uploaded files with page count info
if uploaded_files:
    st.markdown("### 📋 Uploaded Files")
    for file in uploaded_files:
        file_size_mb = file.size / (1024 * 1024)
        progress_data = st.session_state.file_progress[file.name]

        # Show page count if available
        page_info = ""
        if progress_data["page_count"] > 0:
            page_info = f" • {progress_data['page_count']} pages"

        st.markdown(f"""
        <div class="file-item">
            <strong>{file.name}</strong> ({file_size_mb:.2f} MB{page_info})<br>
            <span class="{progress_data['status_class']}">{progress_data['status']}</span>
        </div>
        """, unsafe_allow_html=True)

# Processing controls
if uploaded_files and not st.session_state.processing_started and not st.session_state.processing_complete:
    if st.button("🚀 Start Conversion", type="primary", use_container_width=True):
        st.session_state.processing_started = True
        st.session_state.start_time = time.time()

        # Update status to uploading
        for file_name in st.session_state.file_progress:
            st.session_state.file_progress[file_name].update({
                "status": "Uploading to server...",
                "status_class": "status-processing"
            })
        st.rerun()

# Start processing - upload files to FastAPI
if st.session_state.processing_started and not st.session_state.batch_id:
    try:
        files = []
        for file_name, file_data in st.session_state.uploaded_files_data.items():
            files.append(("files", (file_name, file_data['content'], file_data['type'])))

        logger.info(f"Uploading {len(files)} files to FastAPI")

        with st.spinner("Uploading files to server..."):
            with httpx.Client(timeout=300.0) as client:
                response = client.post(
                    "http://fastapi:8000/upload/",
                    files=files,
                    params={"force_ocr": st.session_state.force_ocr}
                )

                if response.status_code == 200:
                    data = response.json()
                    st.session_state.batch_id = data["batch_id"]
                    logger.info(f"Batch started: {st.session_state.batch_id}")

                    # Update status to processing
                    for file_name in st.session_state.file_progress:
                        st.session_state.file_progress[file_name].update({
                            "status": "Processing...",
                            "status_class": "status-processing"
                        })
                    st.rerun()
                else:
                    st.error(f"Upload failed: {response.status_code} - {response.text}")
                    st.session_state.processing_started = False
                    st.rerun()

    except Exception as e:
        logger.error(f"Upload failed: {e}")
        st.error(f"Upload error: {str(e)}")
        st.session_state.processing_started = False
        st.rerun()

# Poll for progress and results
if st.session_state.batch_id and not st.session_state.processing_complete:
    try:
        with httpx.Client(timeout=30.0) as client:
            # Check batch status
            status_response = client.get(f"http://fastapi:8000/batch/{st.session_state.batch_id}/status")

            if status_response.status_code == 200:
                batch_status = status_response.json()

                # Update per-file progress
                if "files" in batch_status:
                    for file_progress in batch_status["files"]:
                        file_id = file_progress["file_id"]
                        page_count = file_progress["page_count"]
                        pages_processed = file_progress["pages_processed"]
                        file_status = file_progress["status"]

                        # Find matching file by looking at progress storage
                        # We need to map file_id to file_name - let's iterate
                        for file_name in st.session_state.file_progress:
                            current = st.session_state.file_progress[file_name]

                            # Update page count and progress
                            if page_count > 0:
                                progress = pages_processed / page_count

                                # Determine status message
                                if file_status == "completed":
                                    status_msg = "Completed"
                                    status_class = "status-completed"
                                    progress = 1.0
                                elif file_status == "error":
                                    status_msg = "Error"
                                    status_class = "status-error"
                                    progress = 1.0
                                elif pages_processed == 0:
                                    status_msg = "Initializing..."
                                    status_class = "status-processing"
                                else:
                                    status_msg = f"Processing page {pages_processed}/{page_count}"
                                    status_class = "status-processing"

                                # Only update if changed
                                if (current["page_count"] != page_count or
                                    current["pages_processed"] != pages_processed or
                                    current["status"] != status_msg):

                                    st.session_state.file_progress[file_name].update({
                                        "page_count": page_count,
                                        "pages_processed": pages_processed,
                                        "progress": progress,
                                        "status": status_msg,
                                        "status_class": status_class
                                    })

                # Check if batch is complete
                if batch_status["status"] == "completed":
                    logger.info("Batch processing complete, fetching results...")

                    # Get final results
                    results_response = client.get(f"http://fastapi:8000/batch/{st.session_state.batch_id}/results")

                    if results_response.status_code == 200:
                        results_data = results_response.json()
                        st.session_state.results = results_data.get("results", [])
                        st.session_state.total_time = results_data.get("total_processing_time_seconds", 0.0)
                        st.session_state.processing_complete = True

                        # Update final status for all files
                        for result in st.session_state.results:
                            file_name = result["file_name"]
                            if file_name in st.session_state.file_progress:
                                status_class = "status-completed" if result["status"] == "Success" else "status-error"
                                st.session_state.file_progress[file_name].update({
                                    "progress": 1.0,
                                    "status": result["status"],
                                    "status_class": status_class
                                })

                        logger.info(f"Processing complete: {len(st.session_state.results)} files in {st.session_state.total_time:.2f}s")
                        st.rerun()

    except Exception as e:
        logger.warning(f"Error polling batch status: {e}")

    # Show progress while processing
    if not st.session_state.processing_complete:
        st.markdown("### ⏳ Processing Status")

        # Overall progress
        if st.session_state.batch_id:
            with httpx.Client(timeout=10.0) as client:
                try:
                    batch_resp = client.get(f"http://fastapi:8000/batch/{st.session_state.batch_id}/status")
                    if batch_resp.status_code == 200:
                        batch_data = batch_resp.json()
                        total_files = batch_data.get("total_files", 0)
                        completed_files = batch_data.get("completed_files", 0)
                        progress_pct = batch_data.get("progress_percent", 0)

                        st.progress(progress_pct / 100, text=f"Overall: {completed_files}/{total_files} files ({progress_pct:.1f}%)")

                        # Elapsed time
                        elapsed = time.time() - st.session_state.start_time
                        st.caption(f"⏱️ Elapsed: {elapsed:.1f}s")
                except:
                    pass

        # Per-file progress bars
        for file_name, progress_data in st.session_state.file_progress.items():
            if progress_data["page_count"] > 0:
                col1, col2 = st.columns([3, 1])
                with col1:
                    st.progress(progress_data["progress"], text=f"{file_name}")
                with col2:
                    st.markdown(f"**{progress_data['pages_processed']}/{progress_data['page_count']}**")

        # Auto-refresh every 2 seconds
        time.sleep(2)
        st.rerun()

# Results display
if st.session_state.processing_complete and st.session_state.results:
    st.markdown("### ✅ Conversion Results")

    success_count = sum(1 for result in st.session_state.results if result.get("status") == "Success")
    error_count = len(st.session_state.results) - success_count

    # Calculate total pages processed
    total_pages = sum(result.get("page_count", 0) for result in st.session_state.results if result.get("status") == "Success")

    if success_count > 0:
        st.success(f"✅ {success_count} file(s) processed successfully ({total_pages} pages) in {st.session_state.total_time:.2f} seconds!")
    if error_count > 0:
        st.error(f"❌ {error_count} file(s) had errors")

    for result in st.session_state.results:
        with st.expander(f"{result['file_name']} - {result['status']}", expanded=result.get("status") != "Success"):
            if result.get("status") == "Success":
                # Show stats
                col1, col2, col3 = st.columns(3)
                with col1:
                    st.metric("Pages", result.get("page_count", 0))
                with col2:
                    st.metric("Processing Time", f"{result.get('processing_time_seconds', 0):.2f}s")
                with col3:
                    st.metric("Status", result.get("status", "Success"))

                st.markdown("---")

                # Download button
                if result.get("markdown_id"):
                    try:
                        with httpx.Client(timeout=60.0) as client:
                            md_response = client.get(f"http://fastapi:8000/download-markdown/{result['markdown_id']}")
                        if md_response.status_code == 200:
                            st.download_button(
                                "📥 Download Markdown",
                                md_response.content,
                                file_name=f"{result['file_name'].rsplit('.', 1)[0]}.md",
                                mime="text/markdown",
                                use_container_width=True
                            )

                            # Preview
                            st.markdown("**Preview (first 500 chars):**")
                            preview_text = md_response.text[:500] + "..." if len(md_response.text) > 500 else md_response.text
                            st.text_area("Markdown Content", preview_text, height=150, label_visibility="collapsed")
                    except Exception as e:
                        st.error(f"Failed to download markdown: {e}")
                else:
                    st.warning("No markdown content available")
            else:
                st.error(result.get("status", "Unknown error"))

    if st.button("🔄 Process New Files", use_container_width=True):
        # Reset session state
        for key in list(st.session_state.keys()):
            if key not in ['force_ocr']:
                del st.session_state[key]
        init_session_state()
        st.rerun()

elif not uploaded_files:
    st.info("📁 Please upload files to start processing.")

st.markdown("---")
st.caption("Powered by OCRmyPDF, PyMuPDF, and FastAPI")
