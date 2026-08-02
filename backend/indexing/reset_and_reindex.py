import os
import sys
import json
import logging
import uuid
from dotenv import load_dotenv
from azure.storage.blob import BlobServiceClient
from qdrant_client import QdrantClient
from qdrant_client.http import models
from openai import OpenAI

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("KnowledgeFlow.ResetAndIndex")

load_dotenv()

# Fixed namespace UUID for KnowledgeFlow to generate deterministic UUIDs
NAMESPACE_KNOWLEDGEFLOW = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")

def generate_deterministic_uuid(key: str) -> str:
    """Generate a stable UUIDv5 string from key for Qdrant compatibility."""
    return str(uuid.uuid5(NAMESPACE_KNOWLEDGEFLOW, key))

def main():
    connection_string = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
    processed_container = os.getenv("AZURE_PROCESSED_CONTAINER_NAME", "processed-chunks").strip('"\' ').lower().replace('_', '-')
    qdrant_host = os.getenv("QDRANT_HOST", "localhost")
    qdrant_port = int(os.getenv("QDRANT_PORT", 6333))
    collection_name = "eu_regulations_v1"
    
    if not connection_string:
        logger.error("Missing AZURE_STORAGE_CONNECTION_STRING in .env")
        sys.exit(1)

    blob_service = BlobServiceClient.from_connection_string(connection_string)
    processed_client = blob_service.get_container_client(processed_container)
    qdrant = QdrantClient(host=qdrant_host, port=qdrant_port)
    openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    # 1. Purge outdated blobs
    logger.info("Purging outdated chunk files from Azure Blob Storage...")
    blobs = list(processed_client.list_blobs())
    deleted_count = 0
    valid_blobs = []
    
    for b in blobs:
        # Ignore non-json files
        if not b.name.endswith(".json"):
            continue
            
        if "UNKNOWN_CELEX" in b.name or "_EN_chunks.json" in b.name:
            processed_client.delete_blob(b.name)
            logger.info(f"Deleted outdated blob: {b.name}")
            deleted_count += 1
        else:
            valid_blobs.append(b)
            
    logger.info(f"Purged {deleted_count} stale blob files. Remaining valid payload files: {len(valid_blobs)}")

    # 2. Reset Qdrant Collection with Full-Text Payload Index Enabled
    logger.info(f"Re-creating Qdrant collection '{collection_name}' with Full-Text Indexing...")
    try:
        qdrant.delete_collection(collection_name)
    except Exception:
        pass

    qdrant.create_collection(
        collection_name=collection_name,
        vectors_config=models.VectorParams(
            size=1536,
            distance=models.Distance.COSINE
        )
    )

    # Enable Full-Text Indexing on content & metadata for BM25 Keyword Search
    qdrant.create_payload_index(
        collection_name=collection_name,
        field_name="content",
        field_schema=models.TextIndexParams(
            type="text",
            tokenizer=models.TokenizerType.WORD,
            min_token_len=2,
            max_token_len=20,
            lowercase=True
        )
    )
    logger.info(f"Collection '{collection_name}' re-created with Full-Text Payload Index.")

    # 3. Index Clean Payload with Batch Embeddings
    total_indexed = 0
    BATCH_SIZE = 50

    for blob in valid_blobs:
        logger.info(f"Indexing clean payload: '{blob.name}'...")
        blob_client = processed_client.get_blob_client(blob.name)
        payload = json.loads(blob_client.download_blob().readall().decode('utf-8'))
        
        valid_chunks = [item for item in payload if item.get("content", "").strip()]
        
        for i in range(0, len(valid_chunks), BATCH_SIZE):
            batch = valid_chunks[i:i + BATCH_SIZE]
            texts = [item["content"].strip() for item in batch]
            
            res = openai_client.embeddings.create(
                input=texts,
                model="text-embedding-3-small"
            )
            embeddings = [data.embedding for data in res.data]
            
            points = []
            for idx, item in enumerate(batch):
                # Unique deterministic UUID for each chunk
                point_id = generate_deterministic_uuid(f"{item.get('celex_id')}_{item.get('chunk_id')}")
                
                points.append(models.PointStruct(
                    id=point_id,
                    vector=embeddings[idx],
                    payload={
                        "celex_id": item.get("celex_id"),
                        "document_title": item.get("document_title"),
                        "article_reference": item.get("article_reference"),
                        "content": item["content"],
                        "char_count": item.get("char_count")
                    }
                ))

            qdrant.upsert(collection_name=collection_name, points=points)

        total_indexed += len(valid_chunks)
        logger.info(f"Successfully indexed {len(valid_chunks)} chunks from '{blob.name}'.")

    logger.info(f"RESET & HYBRID RE-INDEX COMPLETE: Total clean vectors = {total_indexed}")

if __name__ == "__main__":
    main()
