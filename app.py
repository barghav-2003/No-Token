# pyrefly: ignore [missing-import]
import streamlit as st
import os
import uuid
import hashlib
import io
from pathlib import Path
import json
from src.engine import HybridMemoryEngine, CloudMeshRouter
from src.archival_memory import ArchivalMemoryManager
from src.state_extractor import StateExtractor

st.set_page_config(page_title="Hybrid Memory Chat", page_icon="🧠", layout="wide")

st.title("🧠 Hybrid Memory Engine Chat")

@st.cache_resource
def get_global_archival():
    return ArchivalMemoryManager(db_dir="./chroma_db")


if "session_id" not in st.session_state:
    st.session_state.session_id = "local_user_session"

def save_state():
    os.makedirs("./local_state", exist_ok=True)
    with open("./local_state/chat_history.json", "w", encoding="utf-8") as f:
        json.dump(st.session_state.messages, f, indent=2)
    with open("./local_state/ingested_files.json", "w", encoding="utf-8") as f:
        json.dump(list(st.session_state.ingested_files), f, indent=2)
    if "engine" in st.session_state and hasattr(st.session_state.engine, "working_memory"):
        StateExtractor.save_working_memory(st.session_state.engine.working_memory, "./local_state/working_memory.json")

# Initialize engine in session state using global singletons for heavy resources
if "engine" not in st.session_state:
    st.session_state.engine = HybridMemoryEngine(
        short_term_window=4, 
        archival_manager=get_global_archival(),
        mesh_router=CloudMeshRouter()
    )
    if os.path.exists("./local_state/working_memory.json"):
        try:
            wm = StateExtractor.load_working_memory("./local_state/working_memory.json")
            st.session_state.engine.working_memory = wm
        except Exception as e:
            st.error(f"Failed to load working memory: {e}")

# Initialize chat history for UI
if "messages" not in st.session_state:
    st.session_state.messages = []
    if os.path.exists("./local_state/chat_history.json"):
        try:
            with open("./local_state/chat_history.json", "r", encoding="utf-8") as f:
                all_msgs = json.load(f)
                st.session_state.messages = all_msgs[-50:]  # only load last 50 for UI speed
                
                # Hydrate ShortTermMemoryBuffer with the last 4 exchanges
                from src.models import Turn
                recent_msgs = all_msgs[-8:] # last 4 turns = 8 messages
                for i in range(0, len(recent_msgs)-1, 2):
                    if recent_msgs[i]["role"] == "user" and recent_msgs[i+1]["role"] == "assistant":
                        t = Turn(
                            user_message=recent_msgs[i]["content"],
                            assistant_message=recent_msgs[i+1]["content"],
                            session_id=st.session_state.session_id
                        )
                        st.session_state.engine.short_term_memory.buffer.append(t)
        except Exception as e:
            st.error(f"Failed to load chat history: {e}")

if "ingested_files" not in st.session_state:
    st.session_state.ingested_files = set()
    if os.path.exists("./local_state/ingested_files.json"):
        try:
            with open("./local_state/ingested_files.json", "r", encoding="utf-8") as f:
                st.session_state.ingested_files = set(json.load(f))
        except Exception as e:
            st.error(f"Failed to load ingested files: {e}")

# Sidebar
with st.sidebar:
    st.header("🌐 Provider Configuration")
    with st.expander("API Keys Config"):
        st.write("Enter comma-separated API keys. Falls back to `.env` if empty.")
        custom_keys = {}
        for tier in st.session_state.engine.mesh_router.tiers:
            provider = tier["provider"]
            env_var = tier["env_var"]
            
            # Default to whatever is currently loaded if we want, but let's keep it simple
            val = st.text_input(f"{provider} Keys", key=f"input_{env_var}", type="password")
            if val:
                custom_keys[env_var] = val
                
        if st.button("Update Keys"):
            st.session_state.engine.mesh_router.reload_keys_from_env(custom_keys)
            st.success("Keys updated in Provider Mesh!")

    st.header("📊 Provider Mesh Dashboard")
    mesh = st.session_state.engine.mesh_router
    for tier in mesh.tiers:
        provider = tier["provider"]
        keys = mesh.provider_keys.get(provider, [])
        active_model = mesh.discovered_models.get(provider, tier.get("fallback_model", "Unknown"))
        st.markdown(f"**{provider}** (`{active_model}`)")
        
        if not keys:
            st.caption("No keys configured.")
        else:
            for key in keys:
                status_info = mesh.key_status.get((provider, key), {})
                status = status_info.get("status", "Unknown")
                key_suffix = key[-4:] if len(key) > 4 else key
                
                if status == "Online":
                    icon = "✅"
                elif status == "Cooling Down":
                    icon = "⏳"
                else:
                    icon = "❌"
                    
                st.caption(f"{icon} `...{key_suffix}` - {status}")
                
    st.divider()

    st.header("Upload Context")
    append_mode = st.checkbox("Append to existing session context", value=True, help="If unchecked, uploading a new file will completely wipe the current session's memory.")
    uploaded_file = st.file_uploader("Upload a chat transcript (.txt, .md, .pdf)", type=["txt", "md", "pdf"])
    if uploaded_file is not None:
        file_bytes = uploaded_file.read()
        file_hash = hashlib.md5(file_bytes).hexdigest()
        
        if file_hash not in st.session_state.ingested_files:
            with st.spinner(f"Ingesting {uploaded_file.name}..."):
                if not append_mode:
                    # Reset archival memory to clear old data for this specific session
                    st.session_state.engine.archival_memory.reset_archival_session(st.session_state.session_id)
                    st.session_state.engine.short_term_memory.clear()
                    st.session_state.messages = []
                
                if uploaded_file.name.lower().endswith('.pdf'):
                    import pypdf
                    reader = pypdf.PdfReader(io.BytesIO(file_bytes))
                    raw_text = "\n".join([page.extract_text() for page in reader.pages if page.extract_text()])
                else:
                    raw_text = file_bytes.decode("utf-8", errors="replace")
                num_turns = st.session_state.engine.ingest_transcript(raw_text, session_id=st.session_state.session_id)
                st.session_state.ingested_files.add(file_hash)
                save_state()
            if num_turns == 0:
                st.warning(f"File '{uploaded_file.name}' was read, but 0 conversation turns were found! Ensure your file uses 'User:' and 'Assistant:' formatting.")
            else:
                st.success(f"Successfully ingested {num_turns} turns from {uploaded_file.name}!")
            
    st.divider()
    
    st.header("Reset System")
    if st.button("Clear Memory & Start Fresh", type="primary"):
        st.session_state.engine.archival_memory.reset_archival_session(st.session_state.session_id)
        import shutil
        if os.path.exists("./local_state"):
            shutil.rmtree("./local_state")
            
        old_router = st.session_state.engine.mesh_router
        st.session_state.engine = HybridMemoryEngine(
            short_term_window=4, 
            archival_manager=get_global_archival(),
            mesh_router=old_router
        )
        st.session_state.messages = []
        st.session_state.ingested_files = set()
        st.rerun()

    st.divider()
    
    st.header("Maintenance")
    if st.button("🧹 Prune Entire Database", help="Wipes all vector data across all sessions"):
        st.session_state.engine.archival_memory.clear()
        st.session_state.messages = []
        st.session_state.ingested_files = set()
        st.success("All ChromaDB data has been completely wiped.")
        st.rerun()

    st.divider()
    
    st.header("System Statistics")
    engine = st.session_state.engine
    
    col1, col2 = st.columns(2)
    with col1:
        st.metric("ChromaDB Vectors", engine.archival_memory.count(session_id=st.session_state.session_id))
    with col2:
        bm25_count = engine.archival_memory._get_bm25(st.session_state.session_id).total_docs
        st.metric("BM25 Lexical Docs", bm25_count)
    
    st.metric("Short-Term Buffer", f"{len(engine.short_term_memory)} / {engine.short_term_memory.max_turns} turns")
    st.caption(f"Active Session: `{st.session_state.session_id[:8]}...`")
    
    st.subheader("Working Memory")
    st.text(f"Persona: {engine.working_memory.persona}")
    st.text(f"Scope: {engine.working_memory.project_scope}")
    with st.expander("Active Rules", expanded=False):
        if not engine.working_memory.rules:
            st.caption("No active rules.")
        for r in engine.working_memory.rules:
            st.markdown(f"- {r}")
    
    with st.expander("Table of Contents (Map Index)", expanded=False):
        if engine.working_memory.map_index:
            st.markdown(engine.working_memory.map_index)
        else:
            st.caption("No Table of Contents generated yet.")
    
    st.subheader("Token Management")
    st.info("Dynamic prompt assembly safeguards your context window with provider-aware safety margins (85% Gemini / 90% Groq) and hybrid RAG.")

# Display chat history
for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

# Chat input
if prompt := st.chat_input("Ask a question or continue the conversation..."):
    # Add user message to UI
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    # Call engine
    with st.chat_message("assistant"):
        response_generator, payload, metadata = st.session_state.engine.process_turn_stream(prompt, session_id=st.session_state.session_id)
        
        # Async stream directly to screen!
        response = st.write_stream(response_generator)
        
        provider = metadata.get("provider", "Unknown")
        model = metadata.get("model", "Unknown")
        key_suffix = metadata.get("key_suffix", "Unknown")
        token_count = metadata.get("token_count", 0)
        safety_margin = metadata.get("safety_margin", 0.90)
        max_context = metadata.get("context_limit", 8000)
        safe_budget = int(max_context * safety_margin)
        
        st.caption(f"✨ Processed by **{provider}** (`{model}`) via key `...{key_suffix}` (Safety Budget: `{int(safety_margin * 100)}%`)")
        
        # Token Progress Bar
        pct = max(0.0, min(token_count / max(safe_budget, 1), 1.0))
        st.progress(pct, text=f"Context Budget Used: {token_count} / {safe_budget} tokens ({pct * 100:.1f}%)")
        
        st.toast(f"Responded via {provider} Mesh", icon="☁️")
        
        with st.expander("View Constructed Prompt Payload"):
            if payload.archival_context:
                st.info("🔍 **Hybrid Retrieval Active**: Context synthesized using BM25 Lexical + ChromaDB Semantic search with Reciprocal Rank Fusion.")
            st.code(payload.full_prompt, language="markdown")
            
    # Add assistant response to UI
    st.session_state.messages.append({"role": "assistant", "content": response})
    save_state()

    # Rerun to update sidebar stats
    st.rerun()
