import os
import math
import logging
import torch
from qdrant_client import QdrantClient
from qdrant_client.http import models
from openai import OpenAI
from sentence_transformers import CrossEncoder

# Optimize PyTorch CPU Threading for Faster Inference
torch.set_num_threads(os.cpu_count() or 4)

logger = logging.getLogger("KnowledgeFlow.Search")

_RERANKER_MODEL = None

def get_reranker_model():
    """Lazy loads the Cross-Encoder model locally on first call."""
    global _RERANKER_MODEL
    if _RERANKER_MODEL is None:
        logger.info("Loading Cross-Encoder Reranker model (BAAI/bge-reranker-base)...")
        _RERANKER_MODEL = CrossEncoder("BAAI/bge-reranker-base")
        logger.info("Cross-Encoder Reranker successfully loaded.")
    return _RERANKER_MODEL

def sigmoid(x: float) -> float:
    """Normalizes raw Cross-Encoder logits to [0.0, 1.0] range."""
    return 1.0 / (1.0 + math.exp(-x))

def hybrid_search(qdrant: QdrantClient, openai_client: OpenAI, query_text: str, top_k: int = 5) -> list[dict]:
    """
    Document-Agnostic Two-Stage Hybrid Retrieval Engine:
    - Stage 1: Expanded Candidate Retrieval via Dense Vector + BM25 Lexical RRF (Top 60)
    - Stage 2: Cross-Encoder Deep Relevance Reranking (Top 60 -> Top K)
    - Stage 3: Dynamic Fallback Diversity Filter (Agostic across EU Regulations, SOPs, Contracts, ISO Standards)
    """
    CANDIDATE_LIMIT = 60
    
    # -------------------------------------------------------------
    # STAGE 1: EXPANDED CANDIDATE RETRIEVAL
    # -------------------------------------------------------------
    # 1. Dense Vector Search
    res = openai_client.embeddings.create(input=query_text, model="text-embedding-3-small")
    query_vector = res.data[0].embedding
    
    if hasattr(qdrant, "query_points"):
        vector_res = qdrant.query_points(
            collection_name="eu_regulations_v1",
            query=query_vector,
            limit=CANDIDATE_LIMIT
        )
        vector_results = vector_res.points
    else:
        vector_results = qdrant.search(
            collection_name="eu_regulations_v1",
            query_vector=query_vector,
            limit=CANDIDATE_LIMIT
        )

    # 2. Sparse Lexical Search (BM25 Keyword Matching)
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
            limit=CANDIDATE_LIMIT
        )
        lexical_results = lexical_res.points
    else:
        lexical_results = qdrant.search(
            collection_name="eu_regulations_v1",
            query_vector=query_vector,
            query_filter=keyword_filter,
            limit=CANDIDATE_LIMIT
        )

    # 3. Reciprocal Rank Fusion (RRF)
    rrf_scores = {}
    k_constant = 60

    def add_ranks(results, weight=1.0):
        for rank, hit in enumerate(results):
            doc_id = hit.id
            payload = hit.payload or {}
            score = weight * (1.0 / (k_constant + rank + 1))
            
            if doc_id not in rrf_scores:
                rrf_scores[doc_id] = {"score": 0.0, "payload": payload, "id": doc_id}
            rrf_scores[doc_id]["score"] += score

    add_ranks(vector_results, weight=1.0)
    add_ranks(lexical_results, weight=1.5)

    candidates = sorted(rrf_scores.values(), key=lambda x: x["score"], reverse=True)[:CANDIDATE_LIMIT]

    if not candidates:
        return []

    # -------------------------------------------------------------
    # STAGE 2: CROSS-ENCODER RERANKING
    # -------------------------------------------------------------
    reranker = get_reranker_model()
    pairs = [[query_text, c["payload"].get("content", "")] for c in candidates]
    raw_scores = reranker.predict(pairs, batch_size=32)

    for idx, candidate in enumerate(candidates):
        candidate["score"] = sigmoid(float(raw_scores[idx]))

    reranked_results = sorted(candidates, key=lambda x: x["score"], reverse=True)

    # -------------------------------------------------------------
    # STAGE 3: AGNOSTIC DIVERSITY & BALANCING FILTER
    # -------------------------------------------------------------
    primary_chunks = []
    secondary_chunks = []

    for item in reranked_results:
        payload = item.get("payload", {}) or {}
        # Support multiple naming conventions safely across document types
        ref = str(
            payload.get("article_reference") or 
            payload.get("section_id") or 
            payload.get("clause") or ""
        ).lower()

        if "recital" in ref or "preamble" in ref or "introduction" in ref:
            secondary_chunks.append(item)
        else:
            primary_chunks.append(item)

    # Fill primary chunks first up to top_k, then backfill with secondary chunks if needed
    balanced_results = primary_chunks[:top_k]
    remaining_slots = top_k - len(balanced_results)
    
    if remaining_slots > 0 and secondary_chunks:
        balanced_results.extend(secondary_chunks[:remaining_slots])

    # Final sort by Cross-Encoder score to ensure top quality ordering
    final_selection = sorted(balanced_results, key=lambda x: x["score"], reverse=True)

    logger.info(f"Stage 1 Candidates: {len(candidates)} -> Stage 2 Reranked Top {len(final_selection)}")
    return final_selection
