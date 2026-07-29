import os
import re
import sys
import json
import logging
from io import BytesIO
from bs4 import BeautifulSoup
from pypdf import PdfReader
from dotenv import load_dotenv
from azure.storage.blob import BlobServiceClient

# Configure clean enterprise logging
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("KnowledgeFlow.Processing")

# Suppress internal Azure logs
logging.getLogger("azure.core.pipeline.policies.http_logging_policy").setLevel(logging.WARNING)
logging.getLogger("azure.storage.blob").setLevel(logging.WARNING)

def extract_text_from_pdf(pdf_bytes: bytes) -> str:
    """Extracts raw plain text from PDF stream using PyPDF."""
    reader = PdfReader(BytesIO(pdf_bytes))
    extracted_text = []
    for page in reader.pages:
        text = page.extract_text()
        if text:
            extracted_text.append(text)
    return "\n".join(extracted_text)

def extract_text_from_html(html_bytes: bytes) -> str:
    """Extracts clean readable text from HTML stream using BeautifulSoup."""
    soup = BeautifulSoup(html_bytes, "html.parser")
    for element in soup(["script", "style", "head", "footer"]):
        element.decompose()
    return soup.get_text(separator="\n")

def structural_article_chunking(raw_text: str, doc_metadata: dict) -> list[dict]:
    """
    Performs robust structural legal chunking.
    Handles 'Article/ARTICLE', 'Chapter/CHAPTER', and falls back to character windowing if needed.
    """
    # Regex matching variations: 'Article 1', 'ARTICLE 1', 'Article 1a', 'Chapter I'
    article_pattern = re.compile(r'(?=\b(?:Article|ARTICLE|Chapter|CHAPTER)\s+\d+[a-zA-Z]?\b)', re.MULTILINE)
    
    raw_splits = article_pattern.split(raw_text)
    
    # Fallback to paragraph splitting if article regex failed to split the document
    if len(raw_splits) <= 2:
        logger.info(f"Explicit article pattern low match for '{doc_metadata['document_title']}'. Applying Smart Paragraph Chunking fallback...")
        raw_splits = raw_text.split("\n\n")

    chunks = []
    chunk_index = 0
    current_buffer = ""

    for block in raw_splits:
        block_cleaned = block.strip()
        if not block_cleaned or len(block_cleaned) < 30:
            continue

        # Extract specific header if match found
        header_match = re.search(r'\b(?:Article|ARTICLE|Chapter|CHAPTER)\s+\d+[a-zA-Z]?\b', block_cleaned)
        article_ref = header_match.group(0) if header_match else "General/Recital"

        # Manage chunk size (~1200 - 1500 chars ideal for embedding models)
        if len(block_cleaned) > 1800:
            sub_paragraphs = block_cleaned.split("\n")
            for p in sub_paragraphs:
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
                current_buffer = ""
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

def process_and_chunk_documents():
    logger.info("Starting KnowledgeFlow Document Processing & Chunking Pipeline...")
    
    load_dotenv()
    connection_string = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
    raw_container = os.getenv("AZURE_CONTAINER_NAME", "raw-eu-regulations")
    processed_container = "processed-chunks"

    if not connection_string:
        logger.error("AZURE_STORAGE_CONNECTION_STRING is missing in .env configuration.")
        sys.exit(1)

    try:
        blob_service_client = BlobServiceClient.from_connection_string(connection_string)
        raw_client = blob_service_client.get_container_client(raw_container)
        
        processed_client = blob_service_client.get_container_client(processed_container)
        if not processed_client.exists():
            logger.info(f"Creating container '{processed_container}' in Azure Storage...")
            processed_client.create_container()

        blobs = list(raw_client.list_blobs())
        logger.info(f"Found {len(blobs)} raw document(s) in Azure Blob container '{raw_container}'.")

        total_chunks_created = 0

        for blob in blobs:
            blob_name = blob.name
            logger.info(f"Processing raw document: '{blob_name}'...")
            
            blob_client = raw_client.get_blob_client(blob_name)
            blob_data = blob_client.download_blob().readall()
            
            blob_properties = blob_client.get_blob_properties()
            metadata = blob_properties.metadata or {}
            
            celex_id = metadata.get("celex_id", "UNKNOWN_CELEX")
            title = metadata.get("document_title", blob_name.split("/")[-1].split(".")[0])

            if blob_name.endswith(".pdf"):
                raw_text = extract_text_from_pdf(blob_data)
            elif blob_name.endswith(".html") or blob_name.endswith(".xhtml"):
                raw_text = extract_text_from_html(blob_data)
            else:
                logger.warning(f"Unsupported format for blob '{blob_name}'. Skipping...")
                continue

            doc_meta = {"celex_id": celex_id, "document_title": title}
            
            chunks = structural_article_chunking(raw_text, doc_meta)
            logger.info(f"Generated {len(chunks)} structural chunks for '{title}'.")

            output_blob_name = f"processed/{title}_{celex_id}_chunks.json"
            output_client = processed_client.get_blob_client(output_blob_name)
            
            json_payload = json.dumps(chunks, indent=2, ensure_ascii=False)
            output_client.upload_blob(json_payload.encode('utf-8'), overwrite=True)
            
            logger.info(f"Successfully saved chunks JSON to '{processed_container}/{output_blob_name}'.")
            total_chunks_created += len(chunks)

        logger.info(f"Processing Pipeline Summary: Successfully generated and stored {total_chunks_created} total chunks.")

    except Exception as e:
        logger.error(f"Processing pipeline failed with exception: {str(e)}")
        sys.exit(1)

if __name__ == "__main__":
    process_and_chunk_documents()
