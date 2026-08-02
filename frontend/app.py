import os
import requests
import streamlit as st
from datetime import datetime, timezone
from fpdf import FPDF
from fpdf.enums import XPos, YPos

BACKEND_URL = os.getenv("BACKEND_URL", "http://backend:8000")

st.set_page_config(
    page_title="KnowledgeFlow | Automated EU Compliance Assistant",
    page_icon="⚖️",
    layout="wide"
)

# Bulletproof Text Sanitizer for Standard PDF Fonts
def sanitize_text(text: str) -> str:
    if not text:
        return ""
    text = text.replace('\t', '    ')
    replacements = {
        '•': '-', '–': '-', '—': '-',
        '“': '"', '”': '"', '‘': "'", '’': "'",
        '…': '...', '™': 'TM', '©': '(c)', '®': '(R)'
    }
    for char, repl in replacements.items():
        text = text.replace(char, repl)
    return text.encode('latin-1', 'ignore').decode('latin-1')

# Custom FPDF Class with Dynamic UTC Footer & Page Numbers
class KnowledgeFlowPDF(FPDF):
    def footer(self):
        self.set_y(-15)
        self.set_font("Helvetica", style="I", size=8)
        self.set_text_color(100, 100, 100)
        
        utc_now = datetime.now(timezone.utc).strftime("%d %b %Y, %H:%M:%S UTC")
        # Fixed formatting for dynamic total page count substitution
        footer_text = f"KnowledgeFlow Legal Audit Trail | Generated on: {utc_now} | Page {self.page_no()}/" + "%s" % self.alias_nb_pages_default
        self.cell(0, 10, footer_text, new_x=XPos.LMARGIN, new_y=YPos.NEXT, align="C")

# Exception-Safe PDF Generator Function
def generate_pdf_report(messages):
    pdf = KnowledgeFlowPDF(orientation='P', unit='mm', format='A4')
    pdf.alias_nb_pages()
    pdf.set_margins(15, 15, 15)
    pdf.set_auto_page_break(auto=True, margin=20)
    pdf.add_page()
    
    # Document Header
    pdf.set_font("Helvetica", style="B", size=14)
    pdf.cell(0, 8, "KnowledgeFlow | Compliance Audit Report", new_x=XPos.LMARGIN, new_y=YPos.NEXT, align="C")
    pdf.set_font("Helvetica", size=9)
    pdf.cell(0, 5, "Automated EU Regulatory Provenance & Reasoning Engine", new_x=XPos.LMARGIN, new_y=YPos.NEXT, align="C")
    pdf.ln(3)
    pdf.line(15, pdf.get_y(), 195, pdf.get_y())
    pdf.ln(5)
    
    # Process Chat History
    for msg in messages:
        role_label = "USER QUERY" if msg["role"] == "user" else "ASSISTANT ANALYSIS"
        pdf.set_font("Helvetica", style="B", size=10)
        pdf.cell(0, 6, f"[{role_label}]", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.ln(1)
        
        pdf.set_font("Helvetica", size=9)
        clean_content = sanitize_text(msg["content"])
        
        for line in clean_content.split('\n'):
            line = line.strip()
            if line:
                try:
                    pdf.multi_cell(0, 5, line, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
                except Exception:
                    pdf.multi_cell(0, 5, line[:200], new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.ln(3)
        
        # Sources & Citations Section
        sources = msg.get("sources") or []
        if msg["role"] == "assistant" and sources:
            pdf.set_font("Helvetica", style="BI", size=9)
            pdf.cell(0, 5, "Retrieved Legal References & Provenance:", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            pdf.set_font("Helvetica", size=8)
            for src in sources:
                if isinstance(src, dict):
                    title = src.get("title", "EU Regulation")
                    art = src.get("article", "General")
                    celex = src.get("celex_id", "N/A")
                    score = src.get("score", 0.0)
                    ref_text = f"  * {title} | Article: {art} | CELEX: {celex} | Score: {score:.4f}"
                    try:
                        pdf.multi_cell(0, 4, sanitize_text(ref_text), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
                    except Exception:
                        pass
            pdf.ln(4)
            
    pdf_out = pdf.output()
    return bytes(pdf_out) if not isinstance(pdf_out, bytes) else pdf_out

# Global Typography CSS
st.markdown("""
<style>
    .stChatMessage p, .stChatMessage li {
        font-size: 0.95rem !important;
        line-height: 1.6 !important;
    }
    .stExpander div[data-testid="stMarkdownContainer"] p {
        font-size: 0.88rem !important;
        line-height: 1.5 !important;
    }
</style>
""", unsafe_allow_html=True)

# Precision Header Layout (HTML-driven for exact font scaling)
st.markdown(
    """
    <div style='margin-bottom: 20px;'>
        <h2 style='font-size: 1.65rem; font-weight: 700; color: #111827; margin: 0; padding: 0;'>
            KnowledgeFlow | Automated EU Compliance Assistant
        </h2>
        <p style='font-size: 0.95rem; color: #4B5563; margin-top: 6px; margin-bottom: 0;'>
            Empowering institutional compliance with automated regulatory retrieval and verifiable legal provenance across EU digital frameworks.
        </p>
    </div>
    """,
    unsafe_allow_html=True
)

# Sidebar Layout
st.sidebar.markdown("### Export Discussion")
if "messages" in st.session_state and st.session_state.messages:
    try:
        pdf_bytes = generate_pdf_report(st.session_state.messages)
        st.sidebar.download_button(
            label="Export Report to PDF",
            data=pdf_bytes,
            file_name="KnowledgeFlow_Compliance_Report.pdf",
            mime="application/pdf"
        )
    except Exception as e:
        st.sidebar.error(f"Error generating PDF: {str(e)}")
else:
    st.sidebar.caption("Start a discussion to generate a downloadable compliance report.")

st.sidebar.divider()

st.sidebar.markdown("### Retrieval Settings")
legal_citations_count = st.sidebar.slider(
    "Legal Citations Count", 
    min_value=1, 
    max_value=10, 
    value=5
)

st.sidebar.divider()

st.sidebar.markdown("**System Status:** :green[Connected]")
st.sidebar.markdown("### Covered Frameworks")
st.sidebar.markdown("""
* **EU AI Act**
* **GDPR** *(General Data Protection Regulation)*
* **Data Act**
* **Digital Services Act (DSA)**
* **Data Governance Act (DGA)**
""")

st.sidebar.divider()
st.sidebar.caption("KnowledgeFlow Enterprise Platform © 2026")

# Chat Engine Interface
if "messages" not in st.session_state:
    st.session_state.messages = []

for idx, message in enumerate(st.session_state.messages):
    with st.chat_message(message["role"]):
        st.markdown(message["content"])
        
        sources = message.get("sources") or []
        if message["role"] == "assistant" and sources:
            with st.expander("View Legal Context & Paragraph References"):
                for src in sources:
                    if not isinstance(src, dict):
                        continue
                    celex = src.get("celex_id") or "N/A"
                    doc_title = src.get("title") or "EU Regulation"
                    article = src.get("article") or "General"
                    score = src.get("score") or 0.0
                    text_snippet = src.get("text_snippet") or ""
                    
                    if celex and celex != "N/A":
                        eulex_link = f"https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:{celex}"
                        celex_display = f"[{celex}]({eulex_link})"
                    else:
                        celex_display = "N/A"
                        
                    st.markdown(
                        f"**{doc_title}** | Article: `{article}` | "
                        f"CELEX: {celex_display} | *Relevance Score: {score:.4f}*"
                    )
                    if text_snippet:
                        st.info(text_snippet)

            feedback_key = f"fb_{idx}"
            feedback = st.feedback("thumbs", key=feedback_key)
            if feedback is not None:
                st.toast("Thank you for your feedback!", icon="👍" if feedback == 1 else "👎")

if user_query := st.chat_input("Ask about EU digital regulations, compliance mandates, or legal provisions..."):
    st.session_state.messages.append({"role": "user", "content": user_query})
    with st.chat_message("user"):
        st.markdown(user_query)

    with st.chat_message("assistant"):
        with st.spinner("Analyzing regulatory corpus & retrieving contexts..."):
            try:
                response = requests.post(
                    f"{BACKEND_URL}/api/query",
                    json={"query": user_query, "top_k": legal_citations_count},
                    timeout=120
                )
                
                if response.status_code == 200:
                    data = response.json()
                    answer = data.get("answer", "No response generated.")
                    sources = data.get("sources") or []
                    
                    st.session_state.messages.append({
                        "role": "assistant",
                        "content": answer,
                        "sources": sources
                    })
                    st.rerun()
                else:
                    st.error(f"Backend API Error: {response.status_code} - {response.text}")
            except Exception as e:
                st.error(f"Failed to connect to backend service: {str(e)}")
