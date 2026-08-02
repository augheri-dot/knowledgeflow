import os
import sys
import logging
from dotenv import load_dotenv
from qdrant_client import QdrantClient
from qdrant_client.http import models
from openai import OpenAI

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
logger = logging.getLogger("KnowledgeFlow.Eval")

load_dotenv()

TEST_CASES = [
    {
        "query": "What restrictions apply when processing sensitive personal data under EU law?",
        "expected_celex": ["32016R0679", "32023R2854"]
    },
    {
        "query": "What are the mandatory risk management requirements for high-risk AI systems?",
        "expected_celex": ["32024R1689"]
    },
    {
        "query": "How does Article 89 of GDPR handle personal data processing for archiving or research purposes?",
        "expected_celex": ["32016R0679"]
    },
    {
        "query": "What obligations apply to data sharing in user connected products under the Data Act?",
        "expected_celex": ["32023R2854"]
    },
    {
        "query": "What is the scope of Article 2 regarding material applicability?",
        "expected_celex": ["32016R0679"]
    }
]

def hybrid_search(qdrant: QdrantClient, openai_client: OpenAI, query_text: str, top_k: int = 5) -> list[dict]:
    # 1. Dense Vector Search
    res = openai_client.embeddings.create(input=query_text, model="text-embedding-3-small")
    query_vector = res.data[0].embedding
    
    # Dual compatibility check for qdrant-client API versions (.query_points vs .search)
    if hasattr(qdrant, "query_points"):
        vector_res = qdrant.query_points(
            collection_name="eu_regulations_v1",
            query=query_vector,
            limit=20
        )
        vector_results = vector_res.points
    else:
        vector_results = qdrant.search(
            collection_name="eu_regulations_v1",
            query_vector=query_vector,
            limit=20
        )

    # 2. Sparse Lexical Search (Full-Text BM25 Keyword Match)
    keywords = [word for word in query_text.split() if len(word) > 3][:5]
    keyword_filter = models.Filter(
        should=[
            models.FieldCondition(
                key="content",
                match=models.MatchText(text=" ".join(keywords))
            )
        ]
    )
    
    if hasattr(qdrant, "query_points"):
        lexical_res = qdrant.query_points(
            collection_name="eu_regulations_v1",
            query=query_vector,
            query_filter=keyword_filter,
            limit=20
        )
        lexical_results = lexical_res.points
    else:
        lexical_results = qdrant.search(
            collection_name="eu_regulations_v1",
            query_vector=query_vector,
            query_filter=keyword_filter,
            limit=20
        )

    # 3. Reciprocal Rank Fusion (RRF)
    rrf_scores = {}
    k_constant = 60

    def add_ranks(results, weight=1.0):
        for rank, hit in enumerate(results):
            doc_id = hit.id
            score = weight * (1.0 / (k_constant + rank + 1))
            if doc_id not in rrf_scores:
                rrf_scores[doc_id] = {"score": 0.0, "payload": hit.payload}
            rrf_scores[doc_id]["score"] += score

    add_ranks(vector_results, weight=1.0)
    add_ranks(lexical_results, weight=1.5)  # Berikan bobot lebih pada pencarian frasa kata kunci eksak

    fused_results = sorted(rrf_scores.values(), key=lambda x: x["score"], reverse=True)
    return fused_results[:top_k]

def main():
    qdrant_host = os.getenv("QDRANT_HOST", "localhost")
    qdrant_port = int(os.getenv("QDRANT_PORT", 6333))
    
    qdrant = QdrantClient(host=qdrant_host, port=qdrant_port)
    openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    logger.info("Starting Hybrid RAG Evaluation (Dense Vector + BM25 Lexical RRF) on 5 test samples...\n")

    hits = 0
    reciprocal_ranks = []

    for idx, test in enumerate(TEST_CASES, 1):
        query = test["query"]
        expected = test["expected_celex"]
        
        logger.info(f"--- Query {idx}: {query[:50]}... ---")
        logger.info(f" Expected : {expected}")

        top_hits = hybrid_search(qdrant, openai_client, query, top_k=5)
        retrieved_celex = [h["payload"].get("celex_id", "UNKNOWN") for h in top_hits]
        
        logger.info(f" Retrieved: {retrieved_celex}")

        rank_found = None
        for r, celex in enumerate(retrieved_celex, 1):
            if celex in expected:
                rank_found = r
                break

        if rank_found:
            hits += 1
            rr = 1.0 / rank_found
            reciprocal_ranks.append(rr)
            logger.info(f"[INFO] Result: HIT at rank {rank_found}\n")
        else:
            reciprocal_ranks.append(0.0)
            logger.warning(f"[WARNING] Result: MISS\n")

    hit_rate = hits / len(TEST_CASES)
    mrr = sum(reciprocal_ranks) / len(TEST_CASES)

    print("==========================================")
    print("   KNOWLEDGEFLOW HYBRID RAG EVALUATION    ")
    print("==========================================")
    print(f" Total Test Queries : {len(TEST_CASES)}")
    print(f" Hit Rate @ top_k=5 : {hit_rate:.4f} ({hit_rate*100:.1f}%)")
    print(f" MRR (Rank Score)   : {mrr:.4f}")
    print("==========================================\n")

if __name__ == "__main__":
    main()
