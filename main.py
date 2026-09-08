"""Hybrid Memory Engine - Main Entry Point & CLI Demonstration.

Demonstrates:
1. Ingesting raw transcript text (Module 1).
2. Setting up Working Memory and loading history into Archival Vector Store (Module 2).
3. Sliding Window FIFO queue management and automatic archiving (Module 3).
4. Prompt payload construction & Gemini LLM execution (Module 4).
"""
import os
import sys
import argparse
from pathlib import Path

from src.engine import HybridMemoryEngine
from src.state_extractor import StateExtractor

def run_demo():
    print("=" * 70)
    print("      HYBRID MEMORY ENGINE - SYSTEM DEMONSTRATION")
    print("=" * 70)

    # 1. Initialize Engine
    engine = HybridMemoryEngine(short_term_window=4, db_dir="./chroma_db")
    print("\n[STEP 1] Engine Initialized with Short-Term Window N = 4.")
    print(f"ChromaDB Storage Directory: {os.path.abspath('./chroma_db')}")

    # 2. Ingest Sample Transcript
    sample_file = Path("sample_transcript.txt")
    if sample_file.exists():
        raw_text = sample_file.read_text(encoding="utf-8")
        num_turns = engine.ingest_transcript(raw_text)
        print(f"\n[STEP 2] Ingested raw transcript '{sample_file}' ({num_turns} turns total).")
        print(f"  - Extracted Persona: {engine.working_memory.persona}")
        print(f"  - Working Memory Rules ({len(engine.working_memory.rules)} rules):")
        for r in engine.working_memory.rules:
            print(f"    * {r}")
        print(f"  - Short-Term Buffer Count: {len(engine.short_term_memory)} turns")
        print(f"  - Archival Vector Store Count: {engine.archival_memory.count()} turns")

    # 3. Simulate User Turn that triggers Archival Vector RAG Lookup
    print("\n" + "=" * 70)
    print(" [STEP 3] Running RAG Query against Archival Memory")
    print("=" * 70)

    query = "What was the secret security protocol code name for Project-Aura?"
    print(f"\nUser Query: '{query}'")

    response_text, payload, metadata = engine.process_turn(query)

    print("\n--- COMPILED CONTEXT PAYLOAD SENT TO GEMINI API ---")
    print(payload.full_prompt)
    print("---------------------------------------------------")
    print(f"\nEngine Output:\n{response_text}")

    # 4. Demonstrate Sliding Window Buffer Eviction into Archival Store
    print("\n" + "=" * 70)
    print(" [STEP 4] Demonstrating Sliding Window FIFO Buffer Eviction")
    print("=" * 70)
    print(f"Initial Short-Term Queue size: {len(engine.short_term_memory)} / 4")
    print(f"Initial Archival Vector Store size: {engine.archival_memory.count()}")

    print("\nSimulating 3 new consecutive conversation turns...")
    dummy_queries = [
        "How do we configure the circuit simulation limits?",
        "Can you verify the gate fidelity threshold standard?",
        "What is the system procedure for emergency shutdown?"
    ]

    for q in dummy_queries:
        res, _, _ = engine.process_turn(q)
        print(f"\nProcessed turn: User: '{q}'")
        print(f"  -> Short-Term Queue size: {len(engine.short_term_memory)} / 4")
        print(f"  -> Archival Vector Store size: {engine.archival_memory.count()}")

    print("\n" + "=" * 70)
    print("      DEMONSTRATION COMPLETED SUCCESSFULLY")
    print("=" * 70)

def main():
    parser = argparse.ArgumentParser(description="Hybrid Memory Engine for Gemini LLM")
    parser.add_argument("--demo", action="store_true", help="Run automated demonstration")
    parser.add_argument("--interactive", action="store_true", help="Run interactive CLI chat loop")
    args = parser.parse_args()

    if args.interactive:
        engine = HybridMemoryEngine()
        sample_file = Path("sample_transcript.txt")
        if sample_file.exists():
            engine.ingest_transcript(sample_file.read_text(encoding="utf-8"))
        
        print("Hybrid Memory Engine REPL. Type 'exit' or 'quit' to end.\n")
        while True:
            try:
                user_input = input("\nUser> ")
                if user_input.strip().lower() in ("exit", "quit"):
                    break
                if not user_input.strip():
                    continue
                response, payload, _ = engine.process_turn(user_input)
                print(f"\nAssistant> {response}")
            except KeyboardInterrupt:
                break
    else:
        run_demo()

if __name__ == "__main__":
    main()
