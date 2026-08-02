import os
import sys
import logging
from datetime import datetime
from dotenv import load_dotenv
from openai import OpenAI
from qdrant_client import QdrantClient

# Dynamically resolve and inject project root directory into sys.path
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(CURRENT_DIR, "../../"))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from backend.analytics.confidence_scorer import calculate_confidence_score
from backend.retrieval.search import hybrid_search

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] [%(levelname)s] %(message)s")
logger = logging.getLogger("KnowledgeFlow.ReportGen")

load_dotenv()

SYSTEM_PROMPT = """
You are KnowledgeFlow AI, an elite EU Regulatory & Compliance Legal Counsel.
Your task is to generate a comprehensive, highly structured EU Compliance Assessment Report based strictly on the retrieved regulatory context chunks provided below.

Instructions:
1. Cite specific CELEX IDs and Article references for every claim, right, or obligation.
2. Structure the report clearly using Markdown headers, bulleted lists, and key takeaways.
3. If confidence is MODERATE or LOW, include an explicit Legal Risk & Disclaimer section.
4. Strictly avoid hallucinating regulations not present in the provided context.
"""

def generate_compliance_report(query_text: str) -> str:
    qdrant_host = os.getenv("QDRANT_HOST", "localhost")
    qdrant_port = int(os.getenv("QDRANT_PORT", 6333))
    
    qdrant = QdrantClient(host=qdrant_host, port=qdrant_port)
    openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    logger.info(f"Generating Compliance Report for query: '{query_text}'...")

    # 1. Retrieve Context Chunks via Core Hybrid Search Engine
    retrieved_chunks = hybrid_search(qdrant, openai_client, query_text, top_k=5)
    
    # 2. Compute Multi-Factor Confidence Score
    confidence_data = calculate_confidence_score(retrieved_chunks)
    logger.info(f"Confidence Score: {confidence_data['score']} ({confidence_data['level']})")

    # 3. Format Context Payload for Prompting
    formatted_context = ""
    citations = []
    for idx, hit in enumerate(retrieved_chunks, 1):
        payload = hit["payload"]
        celex = payload.get("celex_id", "N/A")
        article = payload.get("article_reference", "N/A")
        title = payload.get("document_title", "N/A")
        content = payload.get("content", "").strip()
        
        citations.append(f"- **{title}** (CELEX: `{celex}`, {article})")
        formatted_context += f"--- CONTEXT CHUNK {idx} ---\nCELEX: {celex} | Title: {title} | Ref: {article}\nContent:\n{content}\n\n"

    # 4. Construct User Prompt
    user_prompt = f"""
USER COMPLIANCE QUERY:
"{query_text}"

RETRIEVED EU REGULATION CONTEXTS:
{formatted_context}

CONFIDENCE ASSESSMENT:
Score: {confidence_data['score']} ({confidence_data['level']})
Message: {confidence_data['message']}

Please generate the formal EU Legal Compliance Assessment Report in Markdown format.
"""

    # 5. Call LLM for Report Synthesis
    response = openai_client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt}
        ],
        temperature=0.2
    )

    llm_report = response.choices[0].message.content

    # 6. Prepend Report Metadata Header
    report_header = f"""# EU Regulatory Compliance Assessment Report
**Engine:** KnowledgeFlow Enterprise Core  
**Generated Date:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  
**Confidence Score:** `{confidence_data['score']}` (**{confidence_data['level']}**)  
**Primary CELEX Anchor:** `{confidence_data.get('dominant_celex', 'N/A')}`  

---

### Referenced Legal Grounds:
{chr(10).join(citations)}

---

"""
    return report_header + llm_report

if __name__ == "__main__":
    test_query = "What mandatory risk management requirements apply to high-risk AI systems under the EU AI Act?"
    report = generate_compliance_report(test_query)
    
    # Save Report Output
    os.makedirs("reports", exist_ok=True)
    out_path = "reports/Sample_Compliance_Report.md"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(report)
        
    print(f"\n[SUCCESS] Compliance report successfully generated and stored at '{out_path}'.\n")
    print("=" * 65)
    print(report[:1200] + "\n\n... [Preview truncated]")
    print("=" * 65)
