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
        'file_ids': {},
        'uploaded_files': [],
        'uploaded_files_data': {},
        'processing_complete': False,
        'results': [],
        'total_time': 0.0,
        'last_poll_time': 0,
        'force_ocr': True,
        'start_time': 0,
        'processing_id': str(uuid.uuid4())
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
            "progress": 0.0, "page_count": 0, "pages_processed": 0, "file_id": None, 
            "stage": "waiting", "status": "Waiting to start", "status_class": "status-waiting"
        } for file in uploaded_files
    }
    st.session_state.file_ids = {}
    st.session_state.processing_started = False
    st.session_state.processing_complete = False
    st.session_state.results = []
    st.session_state.total_time = 0.0
    st.session_state.last_poll_time = 0
    st.session_state.start_time = 0
    st.session_state.processing_id = str(uuid.uuid4())

# Display uploaded files
if uploaded_files:
    st.markdown("### 📋 Uploaded Files")
    for file in uploaded_files:
        file_size_mb = file.size / (1024 * 1024)
        status = st.session_state.file_progress[file.name]["status"]
        status_class = st.session_state.file_progress[file.name]["status_class"]
        
        st.markdown(f"""
        <div class="file-item">
            <strong>{file.name}</strong> ({file_size_mb:.2f} MB)<br>
            <span class="{status_class}">{status}</span>
        </div>
        """, unsafe_allow_html=True)

# Processing controls
if uploaded_files and not st.session_state.processing_started and not st.session_state.processing_complete:
    if st.button("🚀 Start Conversion", type="primary", use_container_width=True):
        st.session_state.processing_started = True
        st.session_state.file_ids = {file.name: str(uuid.uuid4()) for file in uploaded_files}
        for file in uploaded_files:
            st.session_state.file_progress[file.name].update({
                "file_id": st.session_state.file_ids[file.name],
                "stage": "uploading",
                "status": "Uploading to server...",
                "status_class": "status-processing"
            })
        st.session_state.start_time = time.time()
        st.rerun()

# Progress display
if st.session_state.processing_started and not st.session_state.processing_complete:
    st.markdown("### ⏳ Processing Status")
    
    # Display progress for each file
    for file_name, progress_data in st.session_state.file_progress.items():
        col1, col2, col3 = st.columns([2, 3, 1])
        with col1:
            st.markdown(f"**{file_name}**")
        with col2:
            progress_bar = st.progress(progress_data["progress"], text=progress_data["status"])
        with col3:
            if progress_data["page_count"] > 0:
                st.markdown(f"**{progress_data['pages_processed']}/{progress_data['page_count']}** pages")
            else:
                st.markdown("—")

    # Overall progress
    total_files = len(st.session_state.file_progress)
    completed_files = sum(1 for data in st.session_state.file_progress.values() if data["stage"] == "completed")
    overall_progress = completed_files / total_files if total_files > 0 else 0
    st.progress(overall_progress, text=f"Overall progress: {completed_files}/{total_files} files completed")

# Main processing logic - runs synchronously within Streamlit
if st.session_state.processing_started and not st.session_state.processing_complete:
    try:
        files = []
        for file_name, file_data in st.session_state.uploaded_files_data.items():
            files.append(("files", (file_name, file_data['content'], file_data['type'])))
        
        logger.info(f"Starting processing for {len(files)} files")
        
        with st.spinner("Processing files... This may take a few minutes."):
            with httpx.Client(timeout=7200.0) as client:
                response = client.post(
                    "http://fastapi:8000/upload/",
                    files=files,
                    params={"force_ocr": st.session_state.force_ocr}
                )
                
                if response.status_code == 200:
                    data = response.json()
                    st.session_state.results = data.get("results", [])
                    st.session_state.total_time = data.get("total_processing_time_seconds", 0.0)
                    st.session_state.processing_complete = True
                    
                    # Update progress for completed files
                    for result in st.session_state.results:
                        file_name = result["file_name"]
                        if file_name in st.session_state.file_progress:
                            status_class = "status-completed" if result["status"] == "Success" else "status-error"
                            st.session_state.file_progress[file_name].update({
                                "progress": 1.0,
                                "stage": "completed",
                                "status": result["status"],
                                "status_class": status_class,
                                "page_count": result.get("page_count", 0),
                                "pages_processed": result.get("page_count", 0)
                            })
                            
                    logger.info(f"Processing completed successfully for {len(st.session_state.results)} files")
                    st.rerun()
                    
                else:
                    error_msg = f"Server error: {response.status_code}"
                    st.session_state.results = [{"file_name": file_name, "status": error_msg} 
                                              for file_name in st.session_state.uploaded_files_data.keys()]
                    st.session_state.total_time = 0.0
                    st.session_state.processing_complete = True
                    st.error(f"Processing failed: {error_msg}")
                    st.rerun()
                    
    except Exception as e:
        logger.error(f"Processing failed: {e}")
        error_msg = f"Processing error: {str(e)}"
        st.session_state.results = [{"file_name": file_name, "status": error_msg} 
                                  for file_name in st.session_state.uploaded_files_data.keys()]
        st.session_state.total_time = 0.0
        st.session_state.processing_complete = True
        st.error(f"Processing failed: {error_msg}")
        st.rerun()

# Poll progress during processing
if st.session_state.processing_started and not st.session_state.processing_complete:
    # This will trigger a rerun every 2 seconds to update progress
    time.sleep(2)
    
    def poll_progress():
        try:
            any_updates = False
            for file_name, file_id in st.session_state.file_ids.items():
                if file_name not in st.session_state.file_progress:
                    continue
                    
                try:
                    with httpx.Client(timeout=5.0) as client:
                        response = client.get(f"http://fastapi:8000/progress/{file_id}")
                        
                        if response.status_code == 200:
                            progress_data = response.json()
                            page_count = progress_data["page_count"]
                            pages_processed = progress_data["pages_processed"]
                            
                            # Get current progress
                            current = st.session_state.file_progress[file_name]
                            
                            # Only update if something changed
                            if (page_count != current["page_count"] or 
                                pages_processed != current["pages_processed"]):
                                
                                progress = pages_processed / max(page_count, 1) if page_count > 0 else 0
                                
                                # Determine status based on progress
                                if page_count == 0:
                                    status = "Initializing..."
                                    stage = "uploading"
                                elif pages_processed < page_count:
                                    status = f"Processing page {pages_processed}/{page_count}"
                                    stage = "processing"
                                else:
                                    status = "Converting to Markdown..."
                                    stage = "markdown"
                                
                                # Update progress
                                st.session_state.file_progress[file_name].update({
                                    "page_count": page_count,
                                    "pages_processed": pages_processed,
                                    "progress": progress,
                                    "stage": stage,
                                    "status": status,
                                    "status_class": "status-processing"
                                })
                                any_updates = True
                                
                except Exception as e:
                    logger.warning(f"Error polling progress for {file_id}: {e}")
            
            return any_updates
            
        except Exception as e:
            logger.warning(f"Progress polling failed: {e}")
            return False
    
    poll_progress()
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
                col1, col2 = st.columns(2)
                with col1:
                    if result.get("markdown_content"):
                        st.download_button(
                            "📥 Download Markdown",
                            result["markdown_content"],
                            file_name=f"{result['file_name'].rsplit('.', 1)[0]}.md",
                            mime="text/markdown",
                            use_container_width=True
                        )
                with col2:
                    if result.get("ocr_pdf_id"):
                        try:
                            with httpx.Client(timeout=60.0) as client:
                                pdf_response = client.get(f"http://fastapi:8000/download-ocr-pdf/{result['ocr_pdf_id']}")
                            if pdf_response.status_code == 200:
                                st.download_button(
                                    "📄 Download OCRed PDF",
                                    pdf_response.content,
                                    file_name=f"ocr_{result['file_name']}",
                                    mime="application/pdf",
                                    use_container_width=True
                                )
                        except Exception as e:
                            st.error(f"Failed to download PDF: {e}")
                
                if result.get("markdown_content"):
                    st.markdown("**Preview:**")
                    preview_text = result["markdown_content"][:1000] + "..." if len(result["markdown_content"]) > 1000 else result["markdown_content"]
                    st.text_area("Markdown Content", preview_text, height=200, label_visibility="collapsed")
            else:
                st.error(result["status"])
    
    if st.button("🔄 Process New Files", use_container_width=True):
        for key in list(st.session_state.keys()):
            if key not in ['force_ocr']:
                del st.session_state[key]
        init_session_state()
        st.rerun()

elif not uploaded_files:
    st.info("📁 Please upload files to start processing.")

st.markdown("---")
st.caption("Powered by OCRmyPDF, PyMuPDF, and FastAPI")