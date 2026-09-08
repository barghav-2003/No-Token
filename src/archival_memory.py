"""Module 2: Archival Vector Store & Embedder (Archival Memory).

Uses ChromaDB to store older conversation turns locally as searchable vector embeddings.
Executes RAG similarity lookups when processing new user queries.
"""
import threading
from typing import List, Dict, Any, Optional
from pathlib import Path
# pyrefly: ignore [missing-import]
import chromadb
from src.models import Turn

class ArchivalMemoryManager:
    """Manages local vector storage using ChromaDB for long-term memory retrieval."""


    def __init__(self, db_dir: str = "./chroma_db", collection_name: str = "archival_memory"):
        self.db_dir = Path(db_dir)
        self.db_dir.mkdir(parents=True, exist_ok=True)
        self.client = chromadb.PersistentClient(path=str(self.db_dir))
        self.collection = self.client.get_or_create_collection(
            name=collection_name,
            metadata={"hnsw:space": "cosine"}
        )
        self.lock = threading.Lock()

    def add_turn(self, turn: Turn, session_id: str = "default", metadata: Optional[Dict[str, Any]] = None, custom_text: Optional[str] = None) -> str:
        """Indexes a conversation turn into ChromaDB archival vector store."""
        meta = metadata or {}
        meta["timestamp"] = turn.timestamp
        meta["session_id"] = session_id
        if turn.turn_index is not None:
            meta["turn_index"] = turn.turn_index
        text_content = custom_text if custom_text else turn.to_transcript_format()

        with self.lock:
            self.collection.upsert(
                ids=[turn.turn_id],
                documents=[text_content],
                metadatas=[meta]
            )
        return turn.turn_id

    def add_turns(self, turns: List[Turn], session_id: str = "default") -> None:
        """Bulk indexes multiple conversation turns."""
        if not turns:
            return
        ids = [t.turn_id for t in turns]
        documents = [t.to_transcript_format() for t in turns]
        
        metadatas = []
        for t in turns:
            meta = {"timestamp": t.timestamp, "session_id": session_id}
            if t.turn_index is not None:
                meta["turn_index"] = t.turn_index
            metadatas.append(meta)

        with self.lock:
            self.collection.upsert(
                ids=ids,
                documents=documents,
                metadatas=metadatas
            )

    def search_relevant(
        self,
        query: str,
        session_id: str = "default",
        top_k: int = 2,
        max_distance: float = 0.70
    ) -> List[str]:
        """
        Executes semantic search against ChromaDB collection.
        Implements Neighbor Context Expansion to preserve narrative context.
        """
        if self.collection.count() == 0:
            return []

        where_filter = {"session_id": session_id}

        results = self.collection.query(
            query_texts=[query],
            n_results=top_k,
            where=where_filter,
            include=["documents", "distances", "metadatas"]
        )

        retrieved_snippets = []
        neighbor_indices = set()
        seen_indices = set()

        if results and "documents" in results and results["documents"]:
            docs = results["documents"][0]
            distances = results.get("distances", [[]])[0]
            metas = results.get("metadatas", [[]])[0]

            for doc, dist, meta in zip(docs, distances, metas):
                if dist <= max_distance:
                    retrieved_snippets.append(doc)
                    if meta and "turn_index" in meta:
                        idx = meta["turn_index"]
                        seen_indices.add(idx)
                        neighbor_indices.update([idx - 1, idx + 1])

        # Fetch neighbor context if any valid indices exist
        valid_neighbors = [idx for idx in neighbor_indices if idx > 0 and idx not in seen_indices]
        if valid_neighbors:
            neighbor_results = self.collection.get(
                where={
                    "$and": [
                        {"session_id": session_id},
                        {"turn_index": {"$in": valid_neighbors}}
                    ]
                },
                include=["documents", "metadatas"]
            )
            
            if neighbor_results and "documents" in neighbor_results and neighbor_results["documents"]:
                for doc, meta in zip(neighbor_results["documents"], neighbor_results["metadatas"]):
                    idx = meta.get("turn_index", "?")
                    retrieved_snippets.append(f"[Neighbor Context Turn {idx}]\n{doc}")

        return retrieved_snippets

    def clear(self) -> None:
        """Empties the archival vector store collection."""
        self.client.delete_collection(self.collection.name)
        self.collection = self.client.get_or_create_collection(
            name=self.collection.name,
            metadata={"hnsw:space": "cosine"}
        )

    def reset_archival_session(self, session_id: str) -> None:
        """Purges specific session records to prevent database pollution."""
        self.collection.delete(where={"session_id": session_id})

    def count(self, session_id: Optional[str] = None) -> int:
        """Returns total indexed document count in archival store, optionally filtered by session_id."""
        if session_id:
            results = self.collection.get(where={"session_id": session_id}, include=[])
            return len(results.get("ids", []))
        return self.collection.count()
