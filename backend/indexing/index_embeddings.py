import os
import sys
import json
import logging
from dotenv import load_dotenv
from azure.storage.blob import BlobServiceClient
from qdrant_client import QdrantClient
from qdrant_client.models import VectorParams, Distance, PointStruct
from openai import OpenAI

# Configure clean enterprise logging
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("KnowledgeFlow.Indexing")

# Suppress internal library logs
logging.getLogger("azure.core.pipeline.policies.http_logging_policy").setLevel(logging.WARNING)
logging.getLogger("azure.storage.blob").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)

COLLECTION_NAME = "eu_regulations_v1"
EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIMENSION = 1536  # Dimension for OpenAI text-embedding-3-small

def get_openai_embeddings(texts: list[str], client: OpenAI) -> list[list[float]]:
    """Generates dense vector embeddings in batch using OpenAI API."""
    try:
        response = client.embeddings.create(
            input=texts,
            model=EMBEDDING_MODEL
        )
        return [item.embedding for item in response.data]
    except Exception as e:
        logger.error(f"Error generating embeddings via OpenAI API: {str(e)}")
        raise e

def index_chunks_to_qdrant():
    logger.info("Starting KnowledgeFlow Vector Indexing Pipeline...")
    
    # 1. Load Configurations
    load_dotenv()
    azure_conn = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
    qdrant_url = os.getenv("QDRANT_URL", "http://localhost:6333")
    qdrant_key = os.getenv("QDRANT_API_KEY", None)
    openai_key = os.getenv("OPENAI_API_KEY")

    if not azure_conn:
        logger.error("Missing AZURE_STORAGE_CONNECTION_STRING in .env configuration.")
        sys.exit(1)
        
    if not openai_key:
        logger.error("Missing OPENAI_API_KEY in .env. Required for generating vector embeddings.")
        sys.exit(1)

    try:
        # 2. Initialize Clients
        logger.info("Initializing Azure Blob Service & Qdrant Clients...")
        blob_service_client = BlobServiceClient.from_connection_string(azure_conn)
        processed_container_client = blob_service_client.get_container_client("processed-chunks")
        
        qdrant = QdrantClient(url=qdrant_url, api_key=qdrant_key if qdrant_key else None)
        openai_client = OpenAI(api_key=openai_key)

        # 3. Ensure Qdrant Collection Exists
        collections = [col.name for col in qdrant.get_collections().collections]
        if COLLECTION_NAME not in collections:
            logger.info(f"Creating Qdrant Collection '{COLLECTION_NAME}' (dim={EMBEDDING_DIMENSION}, metric=Cosine)...")
            qdrant.create_collection(
                collection_name=COLLECTION_NAME,
                vectors_config=VectorParams(size=EMBEDDING_DIMENSION, distance=Distance.COSINE)
            )
        else:
            logger.info(f"Target Qdrant Collection '{COLLECTION_NAME}' already exists.")

        # 4. Fetch JSON Chunk Files from Azure Storage
        blobs = [b for b in processed_container_client.list_blobs() if b.name.endswith("_chunks.json")]
        logger.info(f"Found {len(blobs)} processed chunks JSON file(s) in Azure Blob Storage.")

        total_indexed_points = 0

        for blob in blobs:
            blob_name = blob.name
            logger.info(f"Reading processed chunk payload: '{blob_name}'...")
            
            blob_client = processed_container_client.get_blob_client(blob_name)
            content_bytes = blob_client.download_blob().readall()
            chunks = json.loads(content_bytes.decode("utf-8"))
            
            logger.info(f"Loaded {len(chunks)} chunks from '{blob_name}'. Batch generating embeddings...")

            # Batch processing for efficiency (50 chunks per batch)
            batch_size = 50
            for i in range(0, len(chunks), batch_size):
                batch = chunks[i:i + batch_size]
                batch_texts = [c["content"] for c in batch]
                
                # Generate embeddings
                embeddings = get_openai_embeddings(batch_texts, openai_client)
                
                # Prepare Qdrant Points
                points = []
                for idx, (chunk_data, vector) in enumerate(zip(batch, embeddings)):
                    point_id = i + idx + total_indexed_points + 1
                    points.append(
                        PointStruct(
                            id=point_id,
                            vector=vector,
                            payload={
                                "chunk_id": chunk_data["chunk_id"],
                                "celex_id": chunk_data["celex_id"],
                                "document_title": chunk_data["document_title"],
                                "article_reference": chunk_data.get("article_reference", "General"),
                                "content": chunk_data["content"],
                                "char_count": chunk_data["char_count"]
                            }
                        )
                    )

                # Upsert into Qdrant DB
                qdrant.upsert(
                    collection_name=COLLECTION_NAME,
                    points=points
                )
                logger.info(f"  Indexed batch {i // batch_size + 1}/{(len(chunks) - 1) // batch_size + 1} ({len(points)} vectors) into Qdrant.")

            total_indexed_points += len(chunks)
            logger.info(f"Successfully indexed all chunks from '{blob_name}'.")

        logger.info(f"Indexing Pipeline Completed: Successfully vector indexed {total_indexed_points} total chunks into Qdrant collection '{COLLECTION_NAME}'.")

    except Exception as e:
        logger.error(f"Indexing pipeline failed with exception: {str(e)}")
        sys.exit(1)

if __name__ == "__main__":
    index_chunks_to_qdrant()
