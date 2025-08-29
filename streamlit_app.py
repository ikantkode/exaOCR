import streamlit as st
import httpx
import time
import io
import os
import json

# Set page config for a clean, centered layout like combinepdf.com
st.set_page_config(page_title="OCR and Convert to Markdown", layout="centered")

# Minimalistic title and description
st.markdown("""
# OCR and Convert to Markdown
Drag and drop your files below to convert PDFs, images (JPG/PNG/JPEG/TIFF/BMP), TXT, CSV, or Word (DOCX/DOC) files to Markdown with OCR.
""")

# Centered upload card
with st.container():
    st.markdown("### Upload Files")
    force_ocr = st.checkbox("Force OCR on all pages (may overwrite existing text)", value=True)
    uploaded_files = st.file_uploader(
        "Select files to upload",
        type=['pdf', 'jpg', 'png', 'jpeg', 'tiff', 'bmp', 'txt', 'csv', 'docx', 'doc'],
        accept_multiple_files=True,
        help="Upload files (max 200MB per file)",
        label_visibility="visible"
    )

# Placeholder when no files are uploaded
if not uploaded_files:
    st.info("Please upload files to start processing.")
else:
    # File preview
    with st.container():
        st.markdown("### Uploaded Files")
        for file in uploaded_files:
            st.markdown(f"- {file.name} ({file.size / 1024:.2f} KB)")

    # Processing section
    with st.container():
        st.markdown("### Processing")
        progress_text = st.empty()
        progress_bar = st.progress(0)
        timer_text = st.empty()

        if st.button("Convert to Markdown", type="primary"):
            # Start total timer
            total_start_time = time.time()

            # Prepare files for FastAPI
            files = [("files", (file.name, file.getvalue(), file.type)) for file in uploaded_files]

            # Show spinner during processing
            with st.spinner("Connecting to FastAPI service..."):
                try:
                    with httpx.Client(timeout=httpx.Timeout(1800.0, connect=60.0, read=600.0, write=60.0)) as client:
                        response = client.post(
                            "http://fastapi:8000/upload/",
                            files=files,
                            params={"force_ocr": force_ocr}
                        )

                    # Check response status
                    if response.status_code != 200:
                        st.error(f"API error: Status {response.status_code}, Content: {response.text[:200]}..., Headers: {response.headers}")
                        progress_text.markdown("**Processing failed!**")
                        st.stop()

                    # Parse JSON response
                    try:
                        data = response.json()
                    except json.JSONDecodeError as e:
                        st.error(f"Failed to parse API response: {str(e)}\nContent: {response.text[:200]}...\nHeaders: {response.headers}")
                        progress_text.markdown("**Processing failed!**")
                        st.stop()

                    total_time = data.get("total_processing_time_seconds", 0.0)
                    results = data.get("results", [])

                    progress_text.markdown(f"**Processing complete!**")
                    progress_bar.progress(1.0)
                    timer_text.markdown(f"**Total elapsed time:** {total_time:.2f} seconds")

                    # Results section
                    with st.container():
                        st.markdown("### Results")
                        # Results table
                        table = "| File Name | Pages | Processing Time (s) | Status | Content Preview |\n"
                        table += "|-----------|-------|---------------------|--------|-----------------|\n"
                        for result in results:
                            table += f"| {result['file_name']} | {result['page_count']} | {result['processing_time_seconds']} | {result['status']} | {result['content_preview']} |\n"
                        st.markdown(table)

                        # Individual file outputs
                        for result in results:
                            st.markdown(f"#### {result['file_name']}")
                            if result["status"] == "Success":
                                st.markdown("**Converted Text**")
                                st.text_area(
                                    f"Content for {result['file_name']}",
                                    result["markdown_content"],
                                    height=300,
                                    key=f"content_{result['file_name']}",
                                    label_visibility="visible"
                                )
                                st.markdown("**Debug: Full Markdown Output**")
                                st.code(result["markdown_content"], language="markdown")
                            else:
                                st.error(f"Error: {result['status']}")

                            # Download OCRed PDF
                            if result.get("ocr_pdf_id"):
                                try:
                                    with httpx.Client(timeout=60.0) as client:
                                        pdf_response = client.get(f"http://fastapi:8000/download-ocr-pdf/{result['ocr_pdf_id']}")
                                    if pdf_response.status_code == 200:
                                        st.download_button(
                                            label="Download OCRed PDF",
                                            data=pdf_response.content,
                                            file_name=f"ocr_{result['file_name']}",
                                            mime="application/pdf"
                                        )
                                    else:
                                        st.error(f"Failed to download OCRed PDF: Status {pdf_response.status_code}, Content: {pdf_response.text[:200]}...")
                                except httpx.RequestError as e:
                                    st.error(f"Failed to download OCRed PDF: {str(e)}")

                            # Download Markdown
                            if result.get("markdown_id"):
                                try:
                                    with httpx.Client(timeout=60.0) as client:
                                        md_response = client.get(f"http://fastapi:8000/download-markdown/{result['markdown_id']}")
                                    if md_response.status_code == 200:
                                        st.download_button(
                                            label="Download Markdown",
                                            data=md_response.content,
                                            file_name=f"{result['file_name'].rsplit('.', 1)[0]}.md",
                                            mime="text/markdown"
                                        )
                                    else:
                                        st.error(f"Failed to download Markdown: Status {md_response.status_code}, Content: {md_response.text[:200]}...")
                                except httpx.RequestError as e:
                                    st.error(f"Failed to download Markdown: {str(e)}")

                except httpx.RequestError as e:
                    st.error(f"Failed to connect to FastAPI service: {str(e)}\nEnsure the FastAPI container is running and accessible at http://fastapi:8000")
                    progress_text.markdown("**Processing failed!**")
                    st.stop()
                except Exception as e:
                    st.error(f"Unexpected error during processing: {str(e)}")
                    progress_text.markdown("**Processing failed!**")
                    st.stop()