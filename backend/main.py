import os
import sys
import logging
from typing import List, Optional
from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

# Append root directory to sys.path to ensure proper module imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.retrieval.query_engine import query_knowledgeflow

# Logging Configuration
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("KnowledgeFlow-API")

# Initialize FastAPI Application
app = FastAPI(
    title="KnowledgeFlow Enterprise RAG API",
    description="REST API Service for Legal & Regulatory Intelligence Pipeline (EU-Lex)",
    version="1.0.0"
)

# CORS Configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Pydantic Data Models (Request & Response Schemas) ---

class QueryRequest(BaseModel):
    query: str = Field(
        ..., 
        description="Query regarding EU regulations (e.g., EU AI Act, GDPR, Data Act)", 
        example="What are the high-risk AI requirements under the EU AI Act?"
    )
    top_k: Optional[int] = Field(
        default=5, 
        description="Number of reference documents to retrieve", 
        example=5
    )

class SourceCitation(BaseModel):
    title: str
    celex_id: str
    doc_type: str
    article: str
    score: float
    text_snippet: str

class QueryResponse(BaseModel):
    query: str
    answer: str
    sources: List[SourceCitation]

class HealthResponse(BaseModel):
    status: str
    engine_ready: bool
    version: str

# --- API Endpoints ---

@app.get("/health", response_model=HealthResponse, tags=["System"])
def health_check():
    """
    Check server health status and Query Engine readiness.
    """
    return HealthResponse(
        status="healthy",
        engine_ready=True,
        version="1.0.0"
    )

@app.post("/api/v1/query", response_model=QueryResponse, tags=["RAG Retrieval"])
def query_knowledgebase(request: QueryRequest):
    """
    Primary RAG Retrieval endpoint for submitting regulatory and legal queries.
    """
    if not request.query.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Query text cannot be empty."
        )
    
    try:
        # Execute RAG Query Function
        result = query_knowledgeflow(
            user_query=request.query,
            top_k=request.top_k
        )
        
        # Format source citations
        formatted_sources = []
        for src in result.get("sources", []):
            formatted_sources.append(
                SourceCitation(
                    title=src.get("title", "Unknown Document"),
                    celex_id=src.get("celex_id", "N/A"),
                    doc_type=src.get("doc_type", "N/A"),
                    article=src.get("article", "General"),
                    score=float(src.get("score", 0.0)),
                    text_snippet=src.get("text_snippet", "")
                )
            )
            
        return QueryResponse(
            query=result.get("query", request.query),
            answer=result.get("answer", "No answer generated."),
            sources=formatted_sources
        )
        
    except Exception as e:
        logger.error(f"Error processing query: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"An error occurred while processing the RAG query: {str(e)}"
        )

if __name__ == "__main__":
    import uvicorn
    # Execute ASGI server on port 8000
    uvicorn.run("backend.main:app", host="0.0.0.0", port=8000, reload=True)
