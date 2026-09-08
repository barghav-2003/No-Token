# pyrefly: ignore [missing-import]
import streamlit as st
import os
import uuid
from pathlib import Path
from src.engine import HybridMemoryEngine, CloudMeshRouter
from src.archival_memory import ArchivalMemoryManager

st.set_page_config(page_title="Hybrid Memory Chat", page_icon="🧠", layout="wide")

st.title("🧠 Hybrid Memory Engine Chat")

@st.cache_resource
def get_global_archival():
    return ArchivalMemoryManager(db_dir="./chroma_db")

@st.cache_resource
def get_global_router():
    return CloudMeshRouter()

if "session_id" not in st.session_state:
    st.session_state.session_id = str(uuid.uuid4())

# Initialize engine in session state using global singletons for heavy resources
if "engine" not in st.session_state:
    st.session_state.engine = HybridMemoryEngine(
        short_term_window=4, 
        archival_manager=get_global_archival(),
        mesh_router=get_global_router()
    )

# Initialize chat history for UI
if "messages" not in st.session_state:
    st.session_state.messages = []

if "ingested_files" not in st.session_state:
    st.session_state.ingested_files = set()

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
    uploaded_file = st.file_uploader("Upload a chat transcript (.txt, .md, .pdf)", type=["txt", "md", "pdf"])
    if uploaded_file is not None:
        if uploaded_file.name not in st.session_state.ingested_files:
            with st.spinner(f"Ingesting {uploaded_file.name}..."):
                # Reset archival memory to clear old data for this specific session
                st.session_state.engine.archival_memory.reset_archival_session(st.session_state.session_id)
                st.session_state.engine.short_term_memory.clear()
                st.session_state.messages = []
                
                if uploaded_file.name.lower().endswith('.pdf'):
                    import pypdf
                    reader = pypdf.PdfReader(uploaded_file)
                    raw_text = "\n".join([page.extract_text() for page in reader.pages if page.extract_text()])
                else:
                    raw_text = uploaded_file.read().decode("utf-8")
                num_turns = st.session_state.engine.ingest_transcript(raw_text, session_id=st.session_state.session_id)
                st.session_state.ingested_files.add(uploaded_file.name)
            if num_turns == 0:
                st.warning(f"File '{uploaded_file.name}' was read, but 0 conversation turns were found! Ensure your file uses 'User:' and 'Assistant:' formatting.")
            else:
                st.success(f"Successfully ingested {num_turns} turns from {uploaded_file.name}!")
            
    st.divider()
    
    st.header("Reset System")
    if st.button("Clear Memory & Start Fresh", type="primary"):
        st.session_state.engine.archival_memory.reset_archival_session(st.session_state.session_id)
        st.session_state.engine = HybridMemoryEngine(
            short_term_window=4, 
            archival_manager=get_global_archival(),
            mesh_router=get_global_router()
        )
        st.session_state.messages = []
        st.session_state.ingested_files = set()
        st.rerun()

    st.divider()
    
    st.header("System Statistics")
    engine = st.session_state.engine
    
    st.metric("Archival Memory (Chunks)", engine.archival_memory.count(session_id=st.session_state.session_id))
    st.metric("Short-Term Buffer Size", f"{len(engine.short_term_memory)} / {engine.short_term_memory.max_turns}")
    
    st.subheader("Working Memory")
    st.text(f"Persona: {engine.working_memory.persona}")
    st.text(f"Scope: {engine.working_memory.project_scope}")
    with st.expander("Active Rules", expanded=True):
        if not engine.working_memory.rules:
            st.caption("No active rules.")
        for r in engine.working_memory.rules:
            st.markdown(f"- {r}")
    
    st.subheader("Token Management")
    st.info("The engine dynamically compiles lightweight prompt payloads on every turn, protecting your token limits while maintaining full context.")

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
        
        st.caption(f"✨ Processed by **{provider}** (`{model}`) using key `...{key_suffix}`")
        
        # New Progress Bar for Token Visualization
        max_t = metadata.get("context_limit", 8000)
        pct = min(token_count / max_t, 1.0)
        st.progress(pct, text=f"Context Window Used: {token_count} / {max_t} tokens")
        
        st.toast(f"Responded via {provider} Mesh", icon="☁️")
        
        with st.expander("View Constructed Prompt Payload"):
            st.code(payload.full_prompt, language="markdown")
            
    # Add assistant response to UI
    st.session_state.messages.append({"role": "assistant", "content": response})

    # Rerun to update sidebar stats
    st.rerun()
