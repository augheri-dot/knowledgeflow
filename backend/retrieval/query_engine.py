import os
import sys
import logging
from dotenv import load_dotenv
from qdrant_client import QdrantClient
from openai import OpenAI

# Configure clean enterprise logging
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("KnowledgeFlow.Retrieval")

COLLECTION_NAME = "eu_regulations_v1"
EMBEDDING_MODEL = "text-embedding-3-small"
CHAT_MODEL = "gpt-4o-mini"  # atau gpt-4o

def query_knowledgeflow(user_query: str, top_k: int = 5):
    logger.info(f"Received query: '{user_query}'")
    
    load_dotenv()
    qdrant_url = os.getenv("QDRANT_URL", "http://localhost:6333")
    qdrant_key = os.getenv("QDRANT_API_KEY", None)
    openai_key = os.getenv("OPENAI_API_KEY")

    if not openai_key:
        logger.error("OPENAI_API_KEY is missing in .env configuration.")
        sys.exit(1)

    # Initialize Clients
    qdrant = QdrantClient(url=qdrant_url, api_key=qdrant_key if qdrant_key else None)
    openai_client = OpenAI(api_key=openai_key)

    # 1. Embed user query
    logger.info("Generating query embedding...")
    query_vector = openai_client.embeddings.create(
        input=[user_query],
        model=EMBEDDING_MODEL
    ).data[0].embedding

    # 2. Perform Semantic Search in Qdrant (Compatible with qdrant-client v1.10+)
    logger.info(f"Searching top {top_k} relevant legal context chunks in Qdrant...")
    
    if hasattr(qdrant, "query_points"):
        response = qdrant.query_points(
            collection_name=COLLECTION_NAME,
            query=query_vector,
            limit=top_k
        )
        search_results = response.points
    else:
        search_results = qdrant.search(
            collection_name=COLLECTION_NAME,
            query_vector=query_vector,
            limit=top_k
        )

    # 3. Format Context for LLM
    context_blocks = []
    sources = []
    for hit in search_results:
        payload = hit.payload
        score = getattr(hit, "score", 0.0)
        ref = f"[{payload.get('document_title', 'EU Regulation')} | {payload.get('article_reference', 'General')} | Score: {score:.3f}]"
        sources.append(ref)
        context_blocks.append(f"--- SOURCE: {ref} ---\n{payload.get('content', '')}")

    combined_context = "\n\n".join(context_blocks)

    # 4. Synthesize Answer using GPT Model with strict Legal Prompt
    system_prompt = (
        "You are KnowledgeFlow, an expert AI Legal Assistant specializing in EU Digital Regulations "
        "(EU AI Act, GDPR, Data Act). Answer the user's question accurately using ONLY the provided legal context. "
        "Always cite specific Articles and Regulation titles in your answer."
    )

    user_prompt = f"LEGAL CONTEXT:\n{combined_context}\n\nUSER QUESTION:\n{user_query}"

    logger.info("Synthesizing answer via OpenAI LLM...")
    response = openai_client.chat.completions.create(
        model=CHAT_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        temperature=0.2
    )

    answer = response.choices[0].message.content

    print("\n" + "="*80)
    print("KNOWLEDGEFLOW LEGAL QUERY RESPONSE")
    print("="*80)
    print(f"\nQUERY: {user_query}\n")
    print("RETRIEVED LEGAL SOURCES:")
    for src in sources:
        print(f" - {src}")
    print("\nANSWER:")
    print(answer)
    print("="*80 + "\n")

if __name__ == "__main__":
    # Test query
    sample_query = "What are the transparency requirements for high-risk AI systems under the EU AI Act?"
    query_knowledgeflow(sample_query)
