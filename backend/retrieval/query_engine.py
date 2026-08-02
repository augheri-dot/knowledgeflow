import os
import sys
import re
import logging
import ftfy
from typing import List, Dict, Any, Tuple
from dotenv import load_dotenv
from qdrant_client import QdrantClient
from openai import OpenAI

from backend.retrieval.search import hybrid_search

# Load environment variables
load_dotenv()

# Configure structured logging
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("KnowledgeFlow.Retrieval")

EMBEDDING_MODEL = "text-embedding-3-small"
CHAT_MODEL = "gpt-4o-mini"

def batch_clean_and_classify_chunks(openai_client: OpenAI, raw_texts: List[str], default_refs: List[str]) -> Tuple[List[str], List[str]]:
    """
    LLM Metadata Classifier & Text Cleaner.
    Cleans OCR artifacts and infers precise Article/Recital references in a single batch pass.
    """
    if not raw_texts:
        return [], []

    formatted_chunks = []
    for idx, text in enumerate(raw_texts):
        formatted_chunks.append(f"--- CHUNK {idx+1} (Default Metadata: {default_refs[idx]}) ---\n{text.strip()}")

    combined_raw_prompt = "\n\n".join(formatted_chunks)

    system_prompt = (
        "You are an expert Regulatory Knowledge Engine assistant. "
        "Your task for each CHUNK is twofold:\n"
        "1. Fix broken spaces, split words, and OCR artifacts without altering legal meaning.\n"
        "2. Identify the specific Article, Section, Recital, or Clause number that this chunk belongs to based on context.\n\n"
        "STRICT OUTPUT FORMAT for each chunk:\n"
        "--- CHUNK X | ARTICLE: <Extracted Article/Recital/Section or 'General Provision'> ---\n"
        "<Cleaned text content>"
    )

    try:
        logger.info(f"Executing LLM batch text cleanup & article classification for {len(raw_texts)} chunks...")
        response = openai_client.chat.completions.create(
            model=CHAT_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Process these legal text chunks:\n\n{combined_raw_prompt}"}
            ],
            temperature=0.0
        )
        cleaned_output = response.choices[0].message.content.strip()

        cleaned_chunks = list(raw_texts)
        extracted_articles = list(default_refs)

        matches = re.findall(
            r'---\s*CHUNK\s+(\d+)\s*\|\s*ARTICLE:\s*(.*?)\s*---\n?(.*?)(?=(?:---\s*CHUNK\s+\d+\s*\||$))',
            cleaned_output,
            re.DOTALL
        )

        for match_id, detected_art, chunk_content in matches:
            try:
                idx = int(match_id) - 1
                if 0 <= idx < len(raw_texts):
                    if len(chunk_content.strip()) > 10:
                        cleaned_chunks[idx] = chunk_content.strip()
                    if detected_art.strip() and detected_art.strip() not in ["General Provision", "N/A", ""]:
                        extracted_articles[idx] = detected_art.strip()
            except ValueError:
                continue

        return cleaned_chunks, extracted_articles

    except Exception as err:
        logger.warning(f"LLM classification/cleanup failed: {err}. Falling back to default metadata.")
        fallback_texts = []
        for text in raw_texts:
            cleaned = ftfy.fix_text(text)
            cleaned = re.sub(r'\s+', ' ', cleaned)
            fallback_texts.append(cleaned.strip())
        return fallback_texts, default_refs

def query_knowledgeflow(user_query: str, top_k: int = 5):
    logger.info(f"Received query: '{user_query}' with top_k={top_k}")

    qdrant_url = os.getenv("QDRANT_URL", "http://localhost:6333")
    qdrant_key = os.getenv("QDRANT_API_KEY", None)
    openai_key = os.getenv("OPENAI_API_KEY")

    if not openai_key:
        raise ValueError("OPENAI_API_KEY is missing in environment variables.")

    qdrant = QdrantClient(url=qdrant_url, api_key=qdrant_key if qdrant_key else None)
    openai_client = OpenAI(api_key=openai_key)

    clean_query = user_query.strip()

    # 1. Execute Two-Stage Hybrid Search with Cross-Encoder Reranker
    search_results = hybrid_search(qdrant, openai_client, clean_query, top_k=top_k)

    raw_texts = []
    default_refs = []
    metadata_list = []

    for hit in search_results:
        payload = hit.get("payload", {}) or {}
        score = float(hit.get("score", 0.90))

        raw_content = payload.get('content') or payload.get('chunk_text') or payload.get('text') or ''

        if not raw_content.strip():
            str_candidates = [str(v) for v in payload.values() if isinstance(v, str) and len(str(v)) > 50]
            if str_candidates:
                raw_content = max(str_candidates, key=len)

        if raw_content.strip():
            raw_texts.append(raw_content)
            doc_title = payload.get('document_title') or payload.get('title') or 'EU Regulation'
            raw_art = (
                payload.get('article_reference') or 
                payload.get('section_id') or 
                payload.get('clause') or 
                'General Provision'
            )

            default_refs.append(raw_art)

            metadata_list.append({
                "title": doc_title,
                "celex_id": str(payload.get('celex_id', '32024R1689')).strip(),
                "doc_type": payload.get('doc_type', 'Regulation'),
                "score": round(score, 4)
            })

    if not raw_texts:
        return {
            "query": user_query,
            "answer": "I could not find any relevant legal provisions or articles in the indexed regulations.",
            "sources": []
        }

    # 2. LLM Batch Cleanup & Dynamic Article Classifier
    cleaned_texts, classified_articles = batch_clean_and_classify_chunks(openai_client, raw_texts, default_refs)

    context_blocks = []
    sources = []

    for idx, full_content in enumerate(cleaned_texts):
        meta = metadata_list[idx]
        article_tag = classified_articles[idx]

        if full_content.strip():
            sources.append({
                "title": meta["title"],
                "celex_id": meta["celex_id"],
                "doc_type": meta["doc_type"],
                "article": article_tag,
                "score": meta["score"],
                "text_snippet": full_content
            })

            ref_tag = f"[{meta['title']} | {article_tag} | Score: {meta['score']:.3f}]"
            context_blocks.append(f"--- SOURCE: {ref_tag} ---\n{full_content}")

    combined_context = "\n\n".join(context_blocks)

    # 3. Final Answer Synthesis
    system_prompt = (
        "You are KnowledgeFlow, an expert AI Legal Assistant specializing in Regulatory Intelligence. "
        "Answer the user's question accurately using ONLY the provided legal context. "
        "Always cite specific Articles and Regulation titles in your answer."
    )

    user_prompt = f"LEGAL CONTEXT:\n{combined_context}\n\nUSER QUESTION:\n{user_query}"

    response = openai_client.chat.completions.create(
        model=CHAT_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        temperature=0.2
    )

    answer = response.choices[0].message.content

    return {
        "query": user_query,
        "answer": answer,
        "sources": sources
    }
