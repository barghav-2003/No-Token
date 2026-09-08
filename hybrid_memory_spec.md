# Hybrid Memory Engine - System Specification

## 1. High-Level System Architecture

This custom Python script sits between the user and the LLM API (e.g., Gemini), managing three distinct data stores in real time. This architecture guarantees that the LLM maintains 100% conversational fidelity over massive chats without ever exceeding the token limit context window.

                      +-----------------------------------+
                      |         User Input Prompt         |
                      +-----------------------------------+
                                        |
                                        v
    +-----------------------------------------------------------------------+
    |                         CUSTOM PYTHON ENGINE                          |
    |                                                                       |
    |  1. Extract Working Memory (Core Persona & Non-Negotiable Rules)      |
    |  2. Fetch Short-Term Memory (Sliding Window: Last N turns)            |
    |  3. Run Semantic Search against Local Archival Memory (RAG lookup)    |
    |  4. Assemble Compact Context Payload                                  |
    +-----------------------------------------------------------------------+
                                        |
                                        v
                      +-----------------------------------+
                      |       LLM API (e.g., Gemini)      |
                      +-----------------------------------+

## 2. Core Modules to Build

### Module 1: The Initial State Extractor
When migrating a heavy chat transcript into the system for the first time, this module ingests the raw transcript text and splits it into two initial datasets:
*   **Static System Rules (Working Memory):** A structured dictionary containing the active persona, non-negotiable rules, project boundaries, and constraints.
*   **Raw Chat Transcript (Archival & Short-Term):** The entire sequence of raw user and assistant messages.

### Module 2: The Local Vector Store & Embedder (Archival Memory)
Instead of feeding the entire past transcript to the LLM, older conversational turns are stored locally as searchable vector embeddings.
*   **Chunking:** Split the transcript (excluding the last N turns) into distinct conversation turns or small thematic blocks.
*   **Embedding:** Convert each text chunk into a vector embedding using a fast, local embedding model or the Gemini Embeddings API.
*   **Vector Search:** Store these vectors locally using **ChromaDB**.

### Module 3: The Sliding Window Buffer (Short-Term Memory)
This module maintains a rolling queue (FIFO - First In, First Out) of the last N conversational turns (e.g., N = 4 or 5).
*   When a new turn occurs, it gets appended to the short-term queue.
*   When the queue exceeds N turns, the oldest turn automatically gets pushed out into the **Archival Vector Store**.

### Module 4: The Dynamic Prompt Assembler
This is the core engine that constructs the prompt sent to the LLM on every single turn. On each user input, it executes the following logic:
1.  **Read User Input:** Take the new message from the user.
2.  **Query Archival Memory:** Generate an embedding for the user's input and perform a similarity search on the local Vector Store. If a relevant match is found (above a similarity threshold), retrieve that small past snippet.
3.  **Construct System Instruction:** Combine Working Memory rules + the retrieved archival snippet (if present).
4.  **Append Short-Term Queue:** Attach the last N turns to preserve immediate conversational continuity.
5.  **Send & Receive:** Pass the final lightweight payload to the Gemini API, capture the response, and update both the short-term queue and the archival store.

## 3. Data Schema & Prompt Structure

To ensure the LLM receives the context cleanly without getting confused, structure the compiled prompt sent to the API using clear Markdown tags:

    [SYSTEM INSTRUCTION]
    Role: [Insert Persona]
    Constraints: 
    - [Insert Non-Negotiable Rules]
    
    [RETRIEVED ARCHIVAL CONTEXT]
    The following past conversation snippet was retrieved from local storage because it is relevant to the user's query:
    <archive>
    [Insert retrieved chunk from ChromaDB, if any]
    </archive>
    
    [RECENT CONVERSATION HISTORY]
    [Insert Sliding Window Buffer of last N turns]
    
    [CURRENT USER INPUT]
    User: [Insert new prompt]

## 4. Required Python Libraries

The application must utilize the following libraries to function successfully:
*   **`google-genai`**: Official SDK to interact with Gemini models.
*   **`chromadb`**: Lightweight local vector database to store and query the raw conversation history without needing cloud services.
*   **`sentence-transformers`**: For generating fast, local text embeddings completely offline (optional, depending on embedding implementation choice).
*   **`pydantic`**: For strictly validating data schemas when parsing Working Memory state.