import os
import sys
import time
import logging
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

load_dotenv()

TARGET_REGULATIONS = [
    {"celex_id": "32024R1689", "title": "EU_AI_Act"},
    {"celex_id": "32016R0679", "title": "EU_GDPR"},
    {"celex_id": "32023R2854", "title": "EU_Data_Act"},
    {"celex_id": "32022R2065", "title": "EU_Digital_Services_Act"},
    {"celex_id": "32022R0868", "title": "EU_Data_Governance_Act"}
]

def get_azure_container_client():
    connection_string = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
    container_name = os.getenv("AZURE_STORAGE_CONTAINER_NAME", "raw-eu-regulations")
    
    if not connection_string:
        logger.error("Missing AZURE_STORAGE_CONNECTION_STRING in .env")
        sys.exit(1)
        
    blob_service_client = BlobServiceClient.from_connection_string(connection_string)
    container_client = blob_service_client.get_container_client(container_name)
    
    # Auto-create container if it doesn't exist
    try:
        if not container_client.exists():
            container_client.create_container()
            logger.info(f"Container '{container_name}' created successfully.")
    except Exception as e:
        logger.warning(f"Container check/creation warning: {e}")
        
    return container_client

def fetch_and_upload(container_client, celex_id: str, title: str):
    logger.info(f"Processing {title} (CELEX: {celex_id})...")
    url = f"https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=CELEX:{celex_id}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    
    try:
        response = requests.get(url, headers=headers, timeout=30)
        if response.status_code == 200:
            content = response.content
            blob_name = f"{celex_id}_{title}.html"
            
            blob_client = container_client.get_blob_client(blob_name)
            blob_client.upload_blob(content, overwrite=True)
            logger.info(f"[SUCCESS] Successfully uploaded {blob_name} to Azure Blob Storage!")
        else:
            logger.error(f"[FAILED] Failed to fetch CELEX {celex_id}, status code: {response.status_code}")
    except Exception as e:
        logger.error(f"[ERROR] Exception during ingestion for {celex_id}: {e}")

def main():
    logger.info("Starting KnowledgeFlow Ingestion Pipeline for EU Regulations...")
    container_client = get_azure_container_client()
    
    for reg in TARGET_REGULATIONS:
        fetch_and_upload(container_client, reg["celex_id"], reg["title"])
        time.sleep(2)  # Respectful rate limiting
        
    logger.info("Ingestion Pipeline Completed Successfully!")

if __name__ == "__main__":
    main()
