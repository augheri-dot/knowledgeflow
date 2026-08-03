import os
import sys
import json
import logging
from typing import List, Dict, Any
from dotenv import load_dotenv
from qdrant_client import QdrantClient
from qdrant_client.http import models
from openai import OpenAI
from stopwordsiso import stopwords
from langdetect import detect
from sentence_transformers import CrossEncoder

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
logger = logging.getLogger("KnowledgeFlow.Eval")

load_dotenv()

# Dynamic Environment Configurations
QDRANT_HOST = os.getenv("QDRANT_HOST", "localhost")
QDRANT_PORT = int(os.getenv("QDRANT_PORT", 6333))
COLLECTION_NAME = os.getenv("QDRANT_COLLECTION_NAME", "eu_regulations_v1")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")
PAYLOAD_TEXT_KEY = os.getenv("QDRANT_PAYLOAD_TEXT_KEY", "content")
RERANKER_MODEL_NAME = os.getenv("RERANKER_MODEL_NAME", "cross-encoder/ms-marco-MiniLM-L-6-v2")

dataset_path = os.path.join(os.path.dirname(__file__), "test_dataset.json")

if os.path.exists(dataset_path):
    with open(dataset_path, "r", encoding="utf-8") as f:
        TEST_CASES = json.load(f)
    logger.info(f"Loaded {len(TEST_CASES)} evaluation samples from {os.path.basename(dataset_path)}")
else:
    logger.error(f"Dataset file {dataset_path} not found!")
    sys.exit(1)

# Lazy initialization for Cross-Encoder model
_RERANKER_INSTANCE = None

def get_reranker_model() -> CrossEncoder:
    global _RERANKER_INSTANCE
    if _RERANKER_INSTANCE is None:
        logger.info(f"Initializing Cross-Encoder Reranker model [{RERANKER_MODEL_NAME}]...")
        _RERANKER_INSTANCE = CrossEncoder(RERANKER_MODEL_NAME)
    return _RERANKER_INSTANCE

def extract_meaningful_keywords(query_text: str, max_keywords: int = 5) -> List[str]:
    """Dynamically detects query language and filters out stopwords without hardcoding."""
    raw_words = [w.strip("?,.:;\"'()[]{}").lower() for w in query_text.split()]
    try:
        detected_lang = detect(query_text)
        dynamic_stopwords = stopwords(detected_lang)
    except Exception:
        dynamic_stopwords = set()

    meaningful = [w for w in raw_words if len(w) > 2 and w not in dynamic_stopwords]
    return meaningful[:max_keywords] if meaningful else raw_words[:max_keywords]

def hybrid_search_with_rerank(
    qdrant: QdrantClient, 
    openai_client: OpenAI, 
    query_text: str, 
    top_k: int = 5
) -> List[Dict[str, Any]]:
    # -------------------------------------------------------------
    # PHASE 1: RETRIEVAL (Fetch Top 20 Candidates using Dense + Lexical RRF)
    # -------------------------------------------------------------
    res = openai_client.embeddings.create(input=query_text, model=EMBEDDING_MODEL)
    query_vector = res.data[0].embedding

    try:
        vector_res = qdrant.query_points(
            collection_name=COLLECTION_NAME,
            query=query_vector,
            limit=20
        )
        vector_results = vector_res.points
    except Exception as e:
        logger.error(f"Dense vector search failed on collection '{COLLECTION_NAME}': {e}")
        vector_results = []

    keywords = extract_meaningful_keywords(query_text)
    if keywords:
        keyword_filter = models.Filter(
            should=[
                models.FieldCondition(
                    key=PAYLOAD_TEXT_KEY,
                    match=models.MatchText(text=" ".join(keywords))
                )
            ]
        )
        try:
            lexical_res = qdrant.query_points(
                collection_name=COLLECTION_NAME,
                query=query_vector,
                query_filter=keyword_filter,
                limit=20
            )
            lexical_results = lexical_res.points
        except Exception:
            lexical_results = []
    else:
        lexical_results = []

    rrf_scores = {}
    k_constant = 60

    def add_ranks(results, weight=1.0):
        for rank, hit in enumerate(results):
            doc_id = hit.id
            score = weight * (1.0 / (k_constant + rank + 1))
            if doc_id not in rrf_scores:
                rrf_scores[doc_id] = {"score": 0.0, "payload": hit.payload}
            rrf_scores[doc_id]["score"] += score

    add_ranks(vector_results, weight=1.2)
    add_ranks(lexical_results, weight=1.0)

    candidates = sorted(rrf_scores.values(), key=lambda x: x["score"], reverse=True)[:20]

    if not candidates:
        return []

    # -------------------------------------------------------------
    # PHASE 2: CROSS-ENCODER RERANKING (Precision Filtering)
    # -------------------------------------------------------------
    reranker = get_reranker_model()
    pairs = []
    for candidate in candidates:
        content = candidate["payload"].get(PAYLOAD_TEXT_KEY, "")
        pairs.append([query_text, content])

    rerank_scores = reranker.predict(pairs)

    for idx, candidate in enumerate(candidates):
        candidate["rerank_score"] = float(rerank_scores[idx])

    reranked_results = sorted(candidates, key=lambda x: x["rerank_score"], reverse=True)
    return reranked_results[:top_k]

def extract_doc_identifier(payload: dict) -> str:
    """Agnostically searches payload for document metadata IDs."""
    if not isinstance(payload, dict):
        return ""
    for key in ["celex_id", "doc_id", "source", "filename", "document_name", "id"]:
        val = payload.get(key)
        if val:
            return str(val)
    return ""

def main():
    qdrant = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT)
    openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    logger.info(f"Starting Reranked Advanced Hybrid RAG Evaluation on Collection: [{COLLECTION_NAME}]...")

    hits = 0
    reciprocal_ranks = []

    for idx, test in enumerate(TEST_CASES, 1):
        query = test.get("query") or test.get("question") or test.get("user_query") or test.get("prompt") or ""
        expected = test.get("expected_celex") or test.get("expected_doc_id") or test.get("celex_id") or test.get("expected_sources") or []

        if isinstance(expected, str):
            expected = [expected]

        if not query:
            logger.warning(f"Skipping sample #{idx}: No valid query key found.")
            continue

        logger.info(f"Query {idx}: {query[:50]}...")
        
        top_hits = hybrid_search_with_rerank(qdrant, openai_client, query, top_k=5)
        retrieved_ids = [extract_doc_identifier(h.get("payload", {})) for h in top_hits]

        rank_found = None
        for r, doc_id in enumerate(retrieved_ids, 1):
            if any(str(exp).strip().lower() in doc_id.lower() for exp in expected):
                rank_found = r
                break

        if rank_found:
            hits += 1
            rr = 1.0 / rank_found
            reciprocal_ranks.append(rr)
            logger.info(f"  [HIT] Rank: {rank_found}")
        else:
            reciprocal_ranks.append(0.0)
            logger.warning(f"  [MISS] Retrieved IDs: {retrieved_ids} vs Expected: {expected}")

    total_queries = len(TEST_CASES)
    hit_rate = hits / total_queries if total_queries > 0 else 0.0
    mrr = sum(reciprocal_ranks) / total_queries if total_queries > 0 else 0.0

    report_text = f"""
==================================================
KNOWLEDGEFLOW ADVANCED RAG EVALUATION REPORT
==================================================
Target Collection  : {COLLECTION_NAME}
Retrieval Strategy : Hybrid (Dense + BM25 RRF) + Cross-Encoder Rerank
Total Test Queries : {total_queries}
Hit Rate @ top_k   : {hit_rate:.4f} ({hit_rate * 100:.2f}%)
MRR (Rank Score)   : {mrr:.4f}
==================================================
"""
    print(report_text)

    output_report_path = os.path.join(os.path.dirname(__file__), "../reports/evaluation_results.txt")
    with open(output_report_path, "w", encoding="utf-8") as rf:
        rf.write(report_text)
    logger.info(f"Evaluation report saved to {output_report_path}")

if __name__ == "__main__":
    main()
