import logging
import sqlite3
import os
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any

from backend.retrieval.query_engine import query_knowledgeflow

# Configure structured logging
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(message)s"
)
logger = logging.getLogger("KnowledgeFlow.Main")

# Database Path Configuration
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_DIR = os.path.join(BASE_DIR, "data")
DB_PATH = os.path.join(DB_DIR, "feedback.db")

def init_db():
    """Ensure data directory and feedback table exist."""
    try:
        os.makedirs(DB_DIR, exist_ok=True)
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                query TEXT NOT NULL,
                answer TEXT NOT NULL,
                feedback_type TEXT NOT NULL,
                comment TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()
        conn.close()
        logger.info(f"Database initialized successfully at {DB_PATH}")
    except Exception as e:
        logger.error(f"Failed to initialize database: {e}")

init_db()

app = FastAPI(
    title="KnowledgeFlow Regulatory Intelligence API",
    version="1.0.0",
    description="Enterprise API for EU Regulations Retrieval and Analysis"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class QueryRequest(BaseModel):
    query: str = Field(..., min_length=1, description="User legal query string")
    top_k: Optional[int] = Field(default=5, ge=1, le=20, description="Number of context passages to retrieve")

class FeedbackRequest(BaseModel):
    query: str
    answer: str
    feedback_type: str = Field(..., description="'thumbs_up' or 'thumbs_down'")
    comment: Optional[str] = None

@app.get("/")
def read_root():
    return {
        "status": "online",
        "service": "KnowledgeFlow Regulatory Intelligence API",
        "version": "1.0.0"
    }

@app.get("/health")
def health_check():
    return {"status": "healthy"}

@app.post("/api/query")
def handle_query(request: QueryRequest):
    try:
        logger.info(f"Received query request: '{request.query}' (top_k={request.top_k})")
        result = query_knowledgeflow(user_query=request.query, top_k=request.top_k)
        return result
    except Exception as e:
        logger.error(f"Error processing query request: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Internal Server Error: {str(e)}")

@app.post("/api/feedback")
def submit_feedback(request: FeedbackRequest):
    try:
        logger.info(f"Received feedback '{request.feedback_type}' for query: '{request.query[:30]}...'")
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO user_feedback (query, answer, feedback_type, comment) VALUES (?, ?, ?, ?)",
            (request.query, request.answer, request.feedback_type, request.comment or "")
        )
        conn.commit()
        conn.close()
        return {"status": "success", "message": "Feedback recorded successfully."}
    except Exception as e:
        logger.error(f"Error saving feedback: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to record feedback: {str(e)}")
