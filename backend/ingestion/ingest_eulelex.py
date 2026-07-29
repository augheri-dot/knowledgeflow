import os
import sys
import time
import logging
from io import BytesIO
import requests
from dotenv import load_dotenv
from azure.storage.blob import BlobServiceClient

# Configure clean enterprise logging
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("KnowledgeFlow.Ingestion")

# Suppress verbose internal logging
logging.getLogger("azure.core.pipeline.policies.http_logging_policy").setLevel(logging.WARNING)
logging.getLogger("azure.storage.blob").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)

TARGET_REGULATIONS = [
    {"celex_id": "32024R1689", "title": "EU_AI_Act"},
    {"celex_id": "32016R0679", "title": "EU_GDPR"},
    {"celex_id": "32023R2854", "title": "EU_Data_Act"}
]

def fetch_with_session_handshake(celex_id: str) -> tuple[bytes, str]:
    """
    Advanced Ingestion Engine: Performs session handshake to bypass WAF challenges
    and stream official EU legal documents directly to memory.
    """
    session = requests.Session()
    
    # Advanced Enterprise Browser Headers
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,application/pdf,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "cross-site"
    })

    try:
        # Step 1: Session Handshake (Get official session cookies from EUR-Lex portal)
        landing_url = f"https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:{celex_id}"
        logger.info(f"Initiating Session Handshake for CELEX: {celex_id}...")
        handshake_resp = session.get(landing_url, allow_redirects=True, timeout=20)
        time.sleep(1)  # Respectful pause to let session register

        # Step 2: Endpoint fallback strategies
        target_endpoints = [
            (f"https://eur-lex.europa.eu/legal-content/EN/TXT/PDF/?uri=CELEX:{celex_id}", "pdf"),
            (f"https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=CELEX:{celex_id}", "html"),
            (f"http://publications.europa.eu/resource/celex/{celex_id}.ENG", "html")
        ]

        for url, fmt in target_endpoints:
            logger.info(f"Executing authenticated stream fetch: {url}")
            session.headers.update({"Referer": landing_url})
            
            resp = session.get(url, allow_redirects=True, timeout=30)
            
            # Check for valid content size (> 15 KB)
            if resp.status_code == 200 and len(resp.content) > 15000:
                # Check for PDF magic bytes if PDF format
                if fmt == "pdf" and not resp.content.startswith(b"%PDF"):
                    logger.warning("Retrieved payload is not a valid PDF binary. Trying next endpoint...")
                    continue
                return resp.content, fmt
            elif resp.status_code == 202:
                logger.info("Received HTTP 202 Async status. Retrying in 2 seconds...")
                time.sleep(2)
                
    except Exception as err:
        logger.error(f"Handshake connection exception for {celex_id}: {str(err)}")

    return None, None

def ingest_eurlex_documents():
    logger.info("Starting KnowledgeFlow Ingestion Pipeline: EUR-Lex to Azure Blob Storage")
    
    # 1. Load configuration
    load_dotenv()
    connection_string = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
    container_name = os.getenv("AZURE_CONTAINER_NAME", "raw-eu-regulations")

    if not connection_string:
        logger.error("Configuration error: AZURE_STORAGE_CONNECTION_STRING is missing in .env")
        sys.exit(1)

    try:
        # 2. Connect to Azure Blob Storage
        logger.info("Initializing Azure Blob Service Client...")
        blob_service_client = BlobServiceClient.from_connection_string(connection_string)
        container_client = blob_service_client.get_container_client(container_name)

        if not container_client.exists():
            logger.error(f"Target container '{container_name}' does not exist.")
            sys.exit(1)

        successful_ingestions = 0

        # 3. Process each targeted regulation
        for doc in TARGET_REGULATIONS:
            celex = doc["celex_id"]
            title = doc["title"]
            logger.info(f"Processing target document: {title} (CELEX: {celex})")

            content_bytes, doc_format = fetch_with_session_handshake(celex)

            if content_bytes:
                content_stream = BytesIO(content_bytes)
                stream_size_kb = len(content_bytes) / 1024
                logger.info(f"Successfully retrieved content stream ({doc_format.upper()}). Size: {stream_size_kb:.2f} KB")

                blob_name = f"raw/{title}_{celex}_EN.{doc_format}"
                blob_client = container_client.get_blob_client(blob_name)

                metadata = {
                    "celex_id": celex,
                    "document_title": title,
                    "language": "EN",
                    "jurisdiction": "EU",
                    "format": doc_format.upper(),
                    "source": "EUR-Lex Authenticated API Stream"
                }

                logger.info(f"Uploading stream directly to Azure Blob: '{blob_name}'...")
                blob_client.upload_blob(content_stream, overwrite=True, metadata=metadata)
                logger.info(f"Ingestion completed for '{blob_name}'.")
                successful_ingestions += 1
            else:
                logger.error(f"Failed to fetch content stream for {celex} ({title}).")

        logger.info(f"Ingestion Pipeline Summary: {successful_ingestions}/{len(TARGET_REGULATIONS)} documents successfully ingested.")
        logger.info("KnowledgeFlow Ingestion Pipeline execution completed.")

    except Exception as e:
        logger.error(f"Ingestion pipeline failed with exception: {str(e)}")
        sys.exit(1)

if __name__ == "__main__":
    ingest_eurlex_documents()
