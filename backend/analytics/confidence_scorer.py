import math

def calculate_confidence_score(retrieved_chunks: list[dict], max_expected_rrf: float = 0.040) -> dict:
    """
    Calculates a multi-factor confidence score for RAG retrieval results.
    Fully compatible with multi-document queries and dynamic RRF search.
    """
    if not retrieved_chunks:
        return {
            "score": 0.0,
            "level": "LOW",
            "message": "No relevant regulatory contexts were retrieved.",
            "dominant_celex": None
        }

    # 1. Top RRF Score Normalization (Bounded between 0.0 and 1.0)
    top_rrf = retrieved_chunks[0].get("score", 0.0)
    rrf_factor = min(max(top_rrf / max_expected_rrf, 0.0), 1.0)

    # 2. Source Alignment Density (Accounts for single & multi-doc legal queries)
    celex_counts = {}
    for chunk in retrieved_chunks:
        celex = chunk.get("payload", {}).get("celex_id", "UNKNOWN")
        celex_counts[celex] = celex_counts.get(celex, 0) + 1
    
    sorted_counts = sorted(celex_counts.values(), reverse=True)
    
    # Support multi-regulation cross-queries (Top-1 or Top-2 combined coverage ratio)
    if len(sorted_counts) > 1:
        coverage_ratio = (sorted_counts[0] + sorted_counts[1]) / len(retrieved_chunks)
    else:
        coverage_ratio = sorted_counts[0] / len(retrieved_chunks)

    # 3. Weighted Final Score Calculation
    final_score = round((rrf_factor * 0.65) + (coverage_ratio * 0.35), 4)

    # Categorize Confidence Level
    if final_score >= 0.65:
        level = "HIGH"
        msg = "High confidence: Retrieved context grounds the legal query precisely."
    elif final_score >= 0.40:
        level = "MODERATE"
        msg = "Moderate confidence: Context covers general principles; review references."
    else:
        level = "LOW"
        msg = "Low confidence: Context may be insufficient or loosely related."

    dominant_celex = max(celex_counts, key=celex_counts.get)

    return {
        "score": final_score,
        "level": level,
        "message": msg,
        "dominant_celex": dominant_celex,
        "retrieved_count": len(retrieved_chunks)
    }

if __name__ == "__main__":
    # Test Single-Doc Retrieval
    sample_chunks_single = [
        {"score": 0.038, "payload": {"celex_id": "32016R0679"}},
        {"score": 0.032, "payload": {"celex_id": "32016R0679"}},
        {"score": 0.025, "payload": {"celex_id": "32016R0679"}}
    ]
    print("Single-Doc Test:", calculate_confidence_score(sample_chunks_single))

    # Test Multi-Doc Retrieval (e.g. GDPR + AI Act)
    sample_chunks_multi = [
        {"score": 0.039, "payload": {"celex_id": "32024R1689"}},
        {"score": 0.035, "payload": {"celex_id": "32016R0679"}},
        {"score": 0.031, "payload": {"celex_id": "32024R1689"}},
        {"score": 0.028, "payload": {"celex_id": "32016R0679"}}
    ]
    print("Multi-Doc Test :", calculate_confidence_score(sample_chunks_multi))
