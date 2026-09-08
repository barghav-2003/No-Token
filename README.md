# 🧠 Dual‑Track Hybrid Memory Engine

**“A Dual‑Track Hybrid Memory Engine that fuses RAG with structural indexing to give LLMs full timeline context—built in Python, orchestrated through Antigravity.”**

---

## 📖 Project Pitch

This project reimagines retrieval‑augmented generation by building a **Dual‑Track Hybrid Memory Engine** that fuses structural indexing with turn‑pair chunking. Designed in Python and orchestrated through Antigravity, it overcomes context blindness in long transcripts by ensuring the LLM sees the *entire timeline* rather than just the tail end. With a Streamlit UI, a cloud API mesh spanning Gemini, Groq, Cerebras, OpenRouter, and Mistral, and ongoing debugging of ingestion logic, the engine is engineered to deliver native‑like context awareness across multi‑turn conversations.

---

## 🚀 Features

- **Streamlit Web UI** – Clean browser interface replacing the original PowerShell CLI.  
- **Cloud API Mesh** – Multi‑key, multi‑provider router cycles through Gemini, Groq, Cerebras, OpenRouter, and Mistral to bypass token limits and rate blocks.  
- **Dual‑Track Hybrid Memory** – Combines a **Structural Map Index** with **Turn‑Pair Chunking** to give LLMs full timeline visibility.  
- **Vector Blindness Fix** – Ensures early document sections (like LIC Share, TCS, Hindustan Zinc analyses) are indexed and retrievable.  
- **Context‑Aware RAG** – Designed to behave with native LLM‑style awareness across multi‑turn transcripts.  
- **Active Debugging** – Current focus on fixing ingestion truncation in `state_extractor.py` (list slicing/regex parsing) so all turns are preserved.  

---

## ⚙️ Getting Started

### Prerequisites
- Python 3.9+
- Pip / virtualenv
- Git

### Installation
```bash
# Clone the repo
git clone https://github.com/barghav-2003/No-Token.git
cd No-Token

# Create virtual environment
python -m venv venv
source venv/bin/activate   # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
