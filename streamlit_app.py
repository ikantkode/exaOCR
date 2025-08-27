import streamlit as st
import httpx
import time
import io
import os

st.title("OCR and Convert to Markdown")

st.write("Upload one or more PDFs, images (JPG/PNG/JPEG/TIFF/BMP), TXT, CSV, or Word (DOCX/DOC) files. The app will send them to the FastAPI service for processing, converting to PDF if needed, applying OCR, and converting to Markdown. Results are shown in a table and available as a ZIP file.")

force_ocr = st.checkbox("Force OCR on all pages (may overwrite existing text)", value=True)

uploaded_files = st.file_uploader("Choose files", type=['pdf', 'jpg', 'png', 'jpeg', 'tiff', 'bmp', 'txt', 'csv', 'docx', 'doc'], accept_multiple_files=True)

if uploaded_files:
    num_files = len(uploaded_files)
    progress_text = st.empty()
    progress_bar = st.progress(0)
    timer_text = st.empty()

    # Start total timer
    total_start_time = time.time()

    # Prepare files for FastAPI
    files = [("files", (file.name, file.getvalue(), file.type)) for file in uploaded_files]
    with httpx.Client(timeout=600.0) as client:
        response = client.post(
            "http://fastapi:8000/upload/",
            files=files,
            params={"force_ocr": force_ocr}
        )

    if response.status_code != 200:
        st.error(f"API error: {response.json().get('detail', 'Unknown error')}")
        st.stop()

    data = response.json()
    total_time = data["total_processing_time_seconds"]
    results = data["results"]
    zip_download_url = data["zip_download_url"]

    completed = len(results)
    progress_text.text("Processing complete!")
    progress_bar.progress(1.0)
    timer_text.text(f"Total elapsed time: {total_time:.2f} seconds")

    # Display results in a Markdown table
    table = "| File Name | Pages | Processing Time (s) | Status | Content Preview |\n"
    table += "|-----------|-------|---------------------|--------|-----------------|\n"
    for result in results:
        table += f"| {result['file_name']} | {result['page_count']} | {result['processing_time_seconds']} | {result['status']} | {result['content_preview']} |\n"
    st.markdown(table)

    # Display full content and OCR PDF downloads
    for result in results:
        if result["status"] == "Success":
            st.subheader(f"Converted Text for {result['file_name']}")
            st.text_area(f"Content for {result['file_name']}", result["markdown_content"], height=300)

        # Download OCRed PDF
        if result["ocr_pdf_id"]:
            with httpx.Client() as client:
                pdf_response = client.get(f"http://fastapi:8000/download-ocr-pdf/{result['ocr_pdf_id']}")
            if pdf_response.status_code == 200:
                st.download_button(
                    label=f"Download OCRed PDF for {result['file_name']}",
                    data=pdf_response.content,
                    file_name=f"ocr_{result['file_name']}",
                    mime="application/pdf"
                )

    # Download ZIP
    with httpx.Client() as client:
        zip_response = client.get(f"http://fastapi:8000{zip_download_url}")
    if zip_response.status_code == 200:
        st.download_button(
            label="Download All Markdowns as ZIP",
            data=zip_response.content,
            file_name="markdowns.zip",
            mime="application/zip"
        )