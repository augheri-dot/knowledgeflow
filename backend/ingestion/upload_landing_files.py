import os
import sys
import logging
from dotenv import load_dotenv
from azure.storage.blob import BlobServiceClient

# Configure clean enterprise logging
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("KnowledgeFlow.LandingIngestion")

# Suppress internal Azure logs
logging.getLogger("azure.core.pipeline.policies.http_logging_policy").setLevel(logging.WARNING)
logging.getLogger("azure.storage.blob").setLevel(logging.WARNING)

LANDING_DIR = "landing_zone"

METADATA_MAPPING = {
    "EU_AI_Act_32024R1689_EN.pdf": {
        "celex_id": "32024R1689",
        "title": "EU_AI_Act",
        "format": "PDF"
    },
    "EU_Data_Act_32023R2854_EN.pdf": {
        "celex_id": "32023R2854",
        "title": "EU_Data_Act",
        "format": "PDF"
    }
}

def upload_landing_zone_files():
    logger.info("Starting Landing Zone Ingestion to Azure Blob Storage...")
    
    load_dotenv()
    connection_string = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
    container_name = os.getenv("AZURE_CONTAINER_NAME", "raw-eu-regulations")

    if not connection_string:
        logger.error("AZURE_STORAGE_CONNECTION_STRING is missing in .env configuration.")
        sys.exit(1)

    try:
        blob_service_client = BlobServiceClient.from_connection_string(connection_string)
        container_client = blob_service_client.get_container_client(container_name)

        if not os.path.exists(LANDING_DIR):
            logger.error(f"Directory '{LANDING_DIR}' does not exist.")
            sys.exit(1)

        uploaded_count = 0
        for filename, meta in METADATA_MAPPING.items():
            file_path = os.path.join(LANDING_DIR, filename)
            
            if os.path.exists(file_path):
                blob_name = f"raw/{filename}"
                blob_client = container_client.get_blob_client(blob_name)

                blob_metadata = {
                    "celex_id": meta["celex_id"],
                    "document_title": meta["title"],
                    "language": "EN",
                    "jurisdiction": "EU",
                    "format": meta["format"],
                    "source": "Landing Zone Upload"
                }

                logger.info(f"Uploading '{filename}' to Azure Blob path '{blob_name}'...")
                with open(file_path, "rb") as data:
                    blob_client.upload_blob(data, overwrite=True, metadata=blob_metadata)
                
                logger.info(f"Successfully uploaded '{filename}'. Removing local copy...")
                os.remove(file_path)  # Keep zero local storage footprint
                uploaded_count += 1
            else:
                logger.warning(f"File '{filename}' not found in '{LANDING_DIR}/'. Skipping...")

        logger.info(f"Landing Zone Ingestion Completed. Uploaded {uploaded_count} file(s).")

    except Exception as e:
        logger.error(f"Upload failed with exception: {str(e)}")
        sys.exit(1)

if __name__ == "__main__":
    upload_landing_zone_files()
