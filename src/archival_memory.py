"""Module 2: Archival Vector Store & Hybrid Retrieval Engine.

Implements Hybrid Search combining:
1. Dense Vector Similarity (ChromaDB cosine distance)
2. In-Memory Sparse Lexical Search (BM25Okapi)
3. Reciprocal Rank Fusion (RRF) with k=60
4. Token-bounded Neighbor Context Expansion (Turns K-1, K+1)
"""
import re
import math
import threading
import logging
from typing import List, Dict, Any, Optional, Tuple, Set
from pathlib import Path
# pyrefly: ignore [missing-import]
import chromadb
from src.models import Turn


class BM25SessionIndex:
    """Lightweight in-memory BM25Okapi index for exact lexical matching per session."""

    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.documents: Dict[str, str] = {}  # doc_id -> raw text
        self.doc_tokens: Dict[str, List[str]] = {}  # doc_id -> list of tokens
        self.doc_lengths: Dict[str, int] = {}  # doc_id -> token length
        self.metadatas: Dict[str, Dict[str, Any]] = {}
        self.df: Dict[str, int] = {}  # term -> document frequency
        self.total_docs: int = 0
        self.avg_doc_len: float = 0.0

    @staticmethod
    def tokenize(text: str) -> List[str]:
        """Tokenizes text preserving identifiers, kebab-case, snake_case, and numbers."""
        return [
            t.lower()
            for t in re.findall(r"[a-zA-Z0-9_\-\./]+", text)
            if len(t) > 1
        ]

    def add_document(self, doc_id: str, text: str, metadata: Optional[Dict[str, Any]] = None) -> None:
        """Indexes or updates a document in the BM25 collection."""
        tokens = self.tokenize(text)
        if doc_id in self.doc_tokens:
            self.remove_document(doc_id)

        self.documents[doc_id] = text
        self.doc_tokens[doc_id] = tokens
        self.doc_lengths[doc_id] = len(tokens)
        self.metadatas[doc_id] = metadata or {}

        # Update document frequencies
        unique_terms = set(tokens)
        for term in unique_terms:
            self.df[term] = self.df.get(term, 0) + 1

        self.total_docs += 1
        total_len = sum(self.doc_lengths.values())
        self.avg_doc_len = total_len / self.total_docs if self.total_docs > 0 else 0.0

    def remove_document(self, doc_id: str) -> None:
        """Removes a document and updates internal term stats."""
        if doc_id not in self.doc_tokens:
            return
        tokens = self.doc_tokens.pop(doc_id)
        self.documents.pop(doc_id, None)
        self.doc_lengths.pop(doc_id, None)
        self.metadatas.pop(doc_id, None)

        unique_terms = set(tokens)
        for term in unique_terms:
            if term in self.df:
                self.df[term] -= 1
                if self.df[term] <= 0:
                    del self.df[term]

        self.total_docs -= 1
        total_len = sum(self.doc_lengths.values())
        self.avg_doc_len = total_len / self.total_docs if self.total_docs > 0 else 0.0

    def search(self, query: str, top_k: int = 5) -> List[Tuple[str, float, str, Dict[str, Any]]]:
        """
        Executes BM25 search for the given query.
        Returns list of (doc_id, score, text, metadata) sorted descending by score.
        """
        if self.total_docs == 0:
            return []

        query_tokens = self.tokenize(query)
        if not query_tokens:
            return []

        scores: Dict[str, float] = {}

        for term in query_tokens:
            if term not in self.df:
                continue

            # Standard Robertson-Spärck Jones IDF
            n_q = self.df[term]
            idf = math.log((self.total_docs - n_q + 0.5) / (n_q + 0.5) + 1.0)
            if idf <= 0:
                idf = 0.05  # Floor small/negative IDFs for common terms

            for doc_id, tokens in self.doc_tokens.items():
                f_qd = tokens.count(term)
                if f_qd == 0:
                    continue
                d_len = self.doc_lengths[doc_id]
                denom = f_qd + self.k1 * (1.0 - self.b + self.b * (d_len / max(self.avg_doc_len, 1.0)))
                term_score = idf * (f_qd * (self.k1 + 1.0)) / denom
                scores[doc_id] = scores.get(doc_id, 0.0) + term_score

        if not scores:
            return []

        sorted_docs = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:top_k]
        return [
            (doc_id, score, self.documents[doc_id], self.metadatas.get(doc_id, {}))
            for doc_id, score in sorted_docs
            if score > 0.0
        ]

    def clear(self) -> None:
        """Clears all indexed documents."""
        self.documents.clear()
        self.doc_tokens.clear()
        self.doc_lengths.clear()
        self.metadatas.clear()
        self.df.clear()
        self.total_docs = 0
        self.avg_doc_len = 0.0


class ArchivalMemoryManager:
    """
    Manages archival memory with Hybrid Search:
    - ChromaDB dense vector store
    - Per-session BM25 lexical index
    - Reciprocal Rank Fusion (RRF) for optimal consensus ranking
    - Neighbor Context Expansion with token-budget bounding
    """

    def __init__(self, db_dir: str = "./chroma_db", collection_name: str = "archival_memory"):
        self.db_dir = Path(db_dir)
        self.db_dir.mkdir(parents=True, exist_ok=True)
        self.client = chromadb.PersistentClient(path=str(self.db_dir))
        self.collection = self.client.get_or_create_collection(
            name=collection_name,
            metadata={"hnsw:space": "cosine"}
        )
        self.lock = threading.Lock()
        self.bm25_indices: Dict[str, BM25SessionIndex] = {}

    def _get_bm25(self, session_id: str) -> BM25SessionIndex:
        """Retrieves or creates the BM25 index for the given session, lazily syncing from ChromaDB."""
        if session_id not in self.bm25_indices:
            bm25 = BM25SessionIndex()
            # If ChromaDB already contains persistent records for this session, sync them into BM25
            try:
                res = self.collection.get(
                    where={"session_id": session_id},
                    include=["documents", "metadatas"]
                )
                if res and res.get("ids"):
                    for doc_id, doc_text, meta in zip(res["ids"], res["documents"], res["metadatas"]):
                        bm25.add_document(doc_id, doc_text, meta)
            except Exception as e:
                logging.error(f"[ArchivalMemory] Failed to sync session {session_id} from ChromaDB: {e}")
            self.bm25_indices[session_id] = bm25
        return self.bm25_indices[session_id]

    def add_turn(
        self,
        turn: Turn,
        session_id: str = "default",
        metadata: Optional[Dict[str, Any]] = None,
        custom_text: Optional[str] = None
    ) -> str:
        """Indexes a conversation turn into ChromaDB and the session BM25 index."""
        meta = metadata or {}
        meta["timestamp"] = turn.timestamp
        meta["session_id"] = session_id
        if turn.turn_index is not None:
            meta["turn_index"] = turn.turn_index
        text_content = custom_text if custom_text else turn.to_transcript_format()

        with self.lock:
            # 1. Upsert to ChromaDB
            self.collection.upsert(
                ids=[turn.turn_id],
                documents=[text_content],
                metadatas=[meta]
            )
            # 2. Add to session BM25 index
            bm25 = self._get_bm25(session_id)
            bm25.add_document(turn.turn_id, text_content, meta)

        return turn.turn_id

    def add_turns(self, turns: List[Turn], session_id: str = "default") -> None:
        """Bulk indexes multiple conversation turns into ChromaDB and BM25."""
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
            # 1. ChromaDB bulk upsert
            self.collection.upsert(
                ids=ids,
                documents=documents,
                metadatas=metadatas
            )
            # 2. BM25 bulk indexing
            bm25 = self._get_bm25(session_id)
            for doc_id, text_content, meta in zip(ids, documents, metadatas):
                bm25.add_document(doc_id, text_content, meta)

    def search_relevant(
        self,
        query: str,
        session_id: str = "default",
        top_k: int = 2,
        max_distance: float = 0.80
    ) -> List[str]:
        """
        Executes Hybrid Search:
        1. Dense Vector Search (ChromaDB cosine distance <= max_distance).
        2. Sparse Lexical Search (BM25Okapi exact keyword matching).
        3. Reciprocal Rank Fusion (RRF with k=60).
        4. Token-bounded Neighbor Context Expansion (Turns K-1, K+1).
        """
        with self.lock:
            total_docs = self.count(session_id=session_id)
            if total_docs == 0:
                return []

            candidate_limit = max(top_k * 4, 10)

            # --- 1. DENSE VECTOR SEARCH (ChromaDB) ---
            dense_ranks: Dict[str, Tuple[int, str, Dict[str, Any]]] = {}  # doc_id -> (rank, doc_text, meta)
            try:
                results = self.collection.query(
                    query_texts=[query],
                    n_results=min(candidate_limit, total_docs),
                    where={"session_id": session_id},
                    include=["documents", "distances", "metadatas"]
                )
                if results and results.get("ids") and results["ids"][0]:
                    ids = results["ids"][0]
                    docs = results["documents"][0]
                    distances = results["distances"][0]
                    metas = results["metadatas"][0]

                    current_rank = 1
                    for doc_id, doc, dist, meta in zip(ids, docs, distances, metas):
                        if dist <= max_distance:
                            dense_ranks[doc_id] = (current_rank, doc, meta)
                            current_rank += 1
            except Exception as e:
                logging.error(f"[ArchivalMemory] ChromaDB dense vector search failed for query '{query}': {e}")

            # --- 2. SPARSE LEXICAL SEARCH (BM25) ---
            sparse_ranks: Dict[str, Tuple[int, str, Dict[str, Any]]] = {}
            bm25 = self._get_bm25(session_id)
            bm25_results = bm25.search(query, top_k=candidate_limit)
            for rank, (doc_id, score, text, meta) in enumerate(bm25_results, start=1):
                sparse_ranks[doc_id] = (rank, text, meta)

            # --- 3. RECIPROCAL RANK FUSION (RRF) ---
            all_candidate_ids: Set[str] = set(dense_ranks.keys()) | set(sparse_ranks.keys())
            if not all_candidate_ids:
                return []

            rrf_k = 60
            fused_scores: Dict[str, float] = {}
            doc_map: Dict[str, Tuple[str, Dict[str, Any]]] = {}

            for doc_id in all_candidate_ids:
                score = 0.0
                if doc_id in dense_ranks:
                    rank, doc, meta = dense_ranks[doc_id]
                    score += 1.0 / (rrf_k + rank)
                    doc_map[doc_id] = (doc, meta)
                if doc_id in sparse_ranks:
                    rank, doc, meta = sparse_ranks[doc_id]
                    score += 1.0 / (rrf_k + rank)
                    if doc_id not in doc_map:
                        doc_map[doc_id] = (doc, meta)
                fused_scores[doc_id] = score

            # Sort candidate IDs by fused RRF score descending
            ranked_ids = sorted(fused_scores.keys(), key=lambda d: fused_scores[d], reverse=True)[:top_k]

            retrieved_snippets: List[str] = []
            selected_turn_indices: Set[int] = set()
            neighbor_indices: Set[int] = set()

            for doc_id in ranked_ids:
                doc_text, meta = doc_map[doc_id]
                retrieved_snippets.append(doc_text)
                if meta and "turn_index" in meta:
                    idx = meta["turn_index"]
                    selected_turn_indices.add(idx)
                    neighbor_indices.update([idx - 1, idx + 1])

            # --- 4. TOKEN-BOUNDED NEIGHBOR CONTEXT EXPANSION ---
            # Exclude already selected turns and non-positive indices
            valid_neighbors = [
                idx for idx in sorted(neighbor_indices)
                if idx > 0 and idx not in selected_turn_indices
            ][:2]  # Cap to at most 2 neighbor context turns

            if valid_neighbors:
                try:
                    neighbor_results = self.collection.get(
                        where={
                            "$and": [
                                {"session_id": session_id},
                                {"turn_index": {"$in": valid_neighbors}}
                            ]
                        },
                        include=["documents", "metadatas"]
                    )
                    if neighbor_results and neighbor_results.get("documents"):
                        for doc, meta in zip(neighbor_results["documents"], neighbor_results["metadatas"]):
                            idx = meta.get("turn_index", "?")
                            retrieved_snippets.append(f"[Neighbor Context Turn {idx}]\n{doc}")
                except Exception as e:
                    logging.error(f"[ArchivalMemory] ChromaDB neighbor context retrieval failed: {e}")

            return retrieved_snippets

    def clear(self) -> None:
        """Empties the archival vector store collection and all session BM25 indices."""
        with self.lock:
            self.client.delete_collection(self.collection.name)
            self.collection = self.client.get_or_create_collection(
                name=self.collection.name,
                metadata={"hnsw:space": "cosine"}
            )
            self.bm25_indices.clear()

    def reset_archival_session(self, session_id: str) -> None:
        """Purges specific session records from ChromaDB and resets its BM25 index."""
        with self.lock:
            try:
                self.collection.delete(where={"session_id": session_id})
            except Exception as e:
                logging.error(f"[ArchivalMemory] Failed to delete session {session_id} from ChromaDB: {e}")
            if session_id in self.bm25_indices:
                self.bm25_indices[session_id].clear()
                del self.bm25_indices[session_id]

    def count(self, session_id: Optional[str] = None) -> int:
        """Returns total indexed document count in archival store, optionally filtered by session_id."""
        if session_id:
            try:
                results = self.collection.get(where={"session_id": session_id}, include=[])
                return len(results.get("ids", []))
            except Exception as e:
                logging.error(f"[ArchivalMemory] Failed to count documents for session {session_id} in ChromaDB: {e}")
                return 0
        return self.collection.count()
