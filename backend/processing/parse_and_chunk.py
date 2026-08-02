import os
import sys
import re
import json
import logging
from dotenv import load_dotenv
from bs4 import BeautifulSoup
import pypdf
from io import BytesIO
from azure.storage.blob import BlobServiceClient

# Configure clean enterprise logging
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("KnowledgeFlow.Processing")

# Suppress verbose internal logging
logging.getLogger("azure.core.pipeline.policies.http_logging_policy").setLevel(logging.WARNING)
logging.getLogger("azure.storage.blob").setLevel(logging.WARNING)

load_dotenv()

def extract_text_from_html(content_bytes: bytes) -> str:
    soup = BeautifulSoup(content_bytes, "html.parser")
    
    # Remove script and style elements
    for script in soup(["script", "style"]):
        script.decompose()
        
    # Replace block elements with clear newline breaks for article splitting
    for element in soup.find_all(['p', 'div', 'h1', 'h2', 'h3', 'tr', 'li']):
        element.insert_before('\n')
        element.insert_after('\n')
        
    text = soup.get_text(separator=" ")
    # Clean up excess blank lines while preserving paragraph boundaries
    cleaned_lines = [re.sub(r'\s+', ' ', line).strip() for line in text.splitlines()]
    return "\n".join([line for line in cleaned_lines if line])

def extract_text_from_pdf(content_bytes: bytes) -> str:
    pdf_file = BytesIO(content_bytes)
    reader = pypdf.PdfReader(pdf_file)
    text = ""
    for page in reader.pages:
        extracted = page.extract_text()
        if extracted:
            text += extracted + "\n"
    return text

def extract_celex_from_filename(filename: str) -> str:
    # Match standard EU CELEX pattern (e.g., 32016R0679, 32024R1689, 32022R2065) even with underscores
    match = re.search(r'(3\d{4}[A-Za-z]\d{4})', filename)
    if match:
        return match.group(1).upper()
    return "UNKNOWN_CELEX"

def structural_article_chunking(raw_text: str, doc_metadata: dict) -> list[dict]:
    chunks = []
    # Split text into sections by "Article" boundary
    article_blocks = re.split(r'\n(?=Article\s+\d+)', raw_text, flags=re.IGNORECASE)
    
    chunk_index = 1
    for block in article_blocks:
        block_cleaned = block.strip()
        if not block_cleaned:
            continue
            
        # Extract Article Title/Number
        art_match = re.search(r'^(Article\s+\d+)', block_cleaned, re.IGNORECASE)
        article_ref = art_match.group(1).title() if art_match else "General / Recital"
        
        # If block is too large, split into sub-chunks
        if len(block_cleaned) > 1500:
            paragraphs = block_cleaned.split("\n")
            current_buffer = ""
            for p in paragraphs:
                p_clean = p.strip()
                if not p_clean:
                    continue
                if len(current_buffer) + len(p_clean) < 1300:
                    current_buffer += p_clean + " "
                else:
                    if current_buffer.strip():
                        chunks.append({
                            "chunk_id": f"{doc_metadata['celex_id']}_chunk_{chunk_index}",
                            "celex_id": doc_metadata["celex_id"],
                            "document_title": doc_metadata["document_title"],
                            "article_reference": article_ref,
                            "content": current_buffer.strip(),
                            "char_count": len(current_buffer.strip())
                        })
                        chunk_index += 1
                    current_buffer = p_clean + " "
            if current_buffer.strip():
                chunks.append({
                    "chunk_id": f"{doc_metadata['celex_id']}_chunk_{chunk_index}",
                    "celex_id": doc_metadata["celex_id"],
                    "document_title": doc_metadata["document_title"],
                    "article_reference": article_ref,
                    "content": current_buffer.strip(),
                    "char_count": len(current_buffer.strip())
                })
                chunk_index += 1
        else:
            chunks.append({
                "chunk_id": f"{doc_metadata['celex_id']}_chunk_{chunk_index}",
                "celex_id": doc_metadata["celex_id"],
                "document_title": doc_metadata["document_title"],
                "article_reference": article_ref,
                "content": block_cleaned,
                "char_count": len(block_cleaned)
            })
            chunk_index += 1

    return chunks

def main():
    logger.info("Starting KnowledgeFlow Document Processing & Chunking Pipeline...")
    
    connection_string = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
    raw_container_name = os.getenv("AZURE_STORAGE_CONTAINER_NAME", "raw-eu-regulations").strip('"\' ').lower().replace('_', '-')
    processed_container_name = os.getenv("AZURE_PROCESSED_CONTAINER_NAME", "processed-chunks").strip('"\' ').lower().replace('_', '-')

    if not connection_string:
        logger.error("Missing AZURE_STORAGE_CONNECTION_STRING in .env")
        sys.exit(1)

    blob_service_client = BlobServiceClient.from_connection_string(connection_string)
    raw_client = blob_service_client.get_container_client(raw_container_name)
    processed_client = blob_service_client.get_container_client(processed_container_name)

    try:
        if not processed_client.exists():
            processed_client.create_container()
    except Exception as e:
        logger.warning(f"Container check warning: {e}")

    blobs = list(raw_client.list_blobs())
    logger.info(f"Found {len(blobs)} raw document(s) in Azure Blob container '{raw_container_name}'.")

    total_chunks = 0
    for blob in blobs:
        blob_name = blob.name
        base_name = os.path.basename(blob_name)
        if not base_name:
            continue
            
        logger.info(f"Processing raw document: '{blob_name}'...")
        blob_client = raw_client.get_blob_client(blob_name)
        blob_data = blob_client.download_blob().readall()

        blob_properties = blob_client.get_blob_properties()
        metadata = blob_properties.metadata or {}

        # Extract CELEX ID: Metadata -> RegEx from Filename
        celex_id = metadata.get("celex_id")
        if not celex_id or celex_id == "UNKNOWN_CELEX":
            celex_id = extract_celex_from_filename(base_name)

        clean_title = base_name.split(".")[0]
        if blob_name.endswith(".pdf"):
            raw_text = extract_text_from_pdf(blob_data)
        elif blob_name.endswith(".html") or blob_name.endswith(".xhtml"):
            raw_text = extract_text_from_html(blob_data)
        else:
            logger.warning(f"Unsupported format for blob '{blob_name}'. Skipping...")
            continue

        doc_meta = {"celex_id": celex_id, "document_title": clean_title}
        chunks = structural_article_chunking(raw_text, doc_meta)
        
        logger.info(f"Generated {len(chunks)} structural chunks for '{clean_title}' (CELEX: {celex_id}).")

        output_blob_name = f"processed/{clean_title}_chunks.json"
        output_client = processed_client.get_blob_client(output_blob_name)

        json_payload = json.dumps(chunks, indent=2, ensure_ascii=False)
        output_client.upload_blob(json_payload.encode('utf-8'), overwrite=True)
        total_chunks += len(chunks)

    logger.info(f"Processing Pipeline Summary: Successfully generated and stored {total_chunks} total chunks.")

if __name__ == "__main__":
    main()
