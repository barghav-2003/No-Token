"""Module 1: Initial State Extractor.

Ingests raw transcript text using a linear, line-by-line state machine parser:
1. Working Memory (Core persona, non-negotiable rules, project boundaries).
2. Chat Transcript Turns for Archival/Short-term memory processing.
"""
import json
import re
from typing import Tuple, List
from pathlib import Path
from src.models import WorkingMemory, Turn

class StateExtractor:
    """Ingests legacy heavy chat transcripts and extracts structured state efficiently."""
    
    @staticmethod
    def extract_working_memory_from_dict(data: dict) -> WorkingMemory:
        """Parses and validates working memory state using Pydantic."""
        return WorkingMemory(**data)

    @staticmethod
    def parse_transcript_text(raw_text: str) -> Tuple[WorkingMemory, List[Turn]]:
        """
        Parses raw text transcript line-by-line via a linear O(N) state machine parser.
        Extracts persona/rules from preamble and splits conversation turns safely.
        """
        persona = "Expert AI Assistant and Software Architect"
        rules = [
            "Maintain high technical accuracy and exactness.",
            "Do not exceed token context window limits.",
            "Always follow provided persona constraints."
        ]
        project_scope = "Building LLM Hybrid Memory Engine"
        constraints = [
            "Never hallucinate memory details outside retrieved context.",
            "Ensure short-term memory adheres to FIFO sliding window."
        ]

        try:
            lines = raw_text.splitlines()
            
            # User and Assistant regex prefixes for header line detection
            user_pattern = re.compile(r"^\s*(?:#{1,4}\s*)?(?:\*{1,2})?(?:User|Human|Client|Prompt|You)(?:\s*\d+)?(?:\*{1,2})?\s*:\s*(.*)$", re.IGNORECASE)
            assistant_pattern = re.compile(r"^\s*(?:#{1,4}\s*)?(?:\*{1,2})?(?:Assistant|Model|AI|Bot|ChatGPT|Response)(?:\*{1,2})?\s*:\s*(.*)$", re.IGNORECASE)

            state = "PREAMBLE" # States: PREAMBLE, USER, ASSISTANT
            preamble_lines = []
            turns: List[Turn] = []
            
            curr_user_lines = []
            curr_assistant_lines = []

            for line in lines:
                user_match = user_pattern.match(line)
                assistant_match = assistant_pattern.match(line)

                if user_match:
                    # If we were building a turn, save it before starting new turn
                    if curr_user_lines and curr_assistant_lines:
                        u_text = "\n".join(curr_user_lines).strip()
                        a_text = "\n".join(curr_assistant_lines).strip()
                        if u_text and a_text:
                            turns.append(Turn(user_message=u_text, assistant_message=a_text))
                        curr_user_lines = []
                        curr_assistant_lines = []
                    elif curr_user_lines and not curr_assistant_lines:
                        preamble_lines.extend(curr_user_lines)
                        curr_user_lines = []

                    state = "USER"
                    inline_content = user_match.group(1).strip()
                    if inline_content:
                        curr_user_lines.append(inline_content)

                elif assistant_match and state in ("USER", "ASSISTANT"):
                    state = "ASSISTANT"
                    inline_content = assistant_match.group(1).strip()
                    if inline_content:
                        curr_assistant_lines.append(inline_content)

                else:
                    if state == "PREAMBLE":
                        preamble_lines.append(line)
                    elif state == "USER":
                        curr_user_lines.append(line)
                    elif state == "ASSISTANT":
                        curr_assistant_lines.append(line)

            # Flush the final pending turn
            if curr_user_lines and curr_assistant_lines:
                u_text = "\n".join(curr_user_lines).strip()
                a_text = "\n".join(curr_assistant_lines).strip()
                if u_text and a_text:
                    turns.append(Turn(user_message=u_text, assistant_message=a_text))

            # Process Preamble for Persona & Rules
            preamble_text = "\n".join(preamble_lines)
            persona_match = re.search(r"(?:Persona|ROLE):\s*(.+)", preamble_text, re.IGNORECASE)
            if persona_match:
                persona = persona_match.group(1).strip()

            rules_match = re.findall(r"(?:Rule|Constraint):\s*(.+)", preamble_text, re.IGNORECASE)
            if rules_match:
                rules.extend([r.strip() for r in rules_match if len(r.strip()) > 3])
                rules = list(dict.fromkeys(rules))

            map_index_lines = ["## Table of Contents"]
            for idx, turn in enumerate(turns, start=1):
                turn.turn_index = idx
                topic = turn.user_message[:50].replace('\n', ' ').strip()
                if len(turn.user_message) > 50:
                    topic += "..."
                map_index_lines.append(f"- **Turn {idx}**: {topic}")
            map_index = "\n".join(map_index_lines)

            working_memory = WorkingMemory(
                persona=persona,
                rules=rules,
                project_scope=project_scope,
                constraints=constraints,
                map_index=map_index
            )

            return working_memory, turns

        except Exception as e:
            print(f"[Warning] Transcript parsing failed, using defaults: {e}")
            working_memory = WorkingMemory(
                persona=persona,
                rules=rules,
                project_scope=project_scope,
                constraints=constraints
            )
            return working_memory, []

    @staticmethod
    def save_working_memory(wm: WorkingMemory, filepath: str) -> None:
        """Persists WorkingMemory to JSON file."""
        Path(filepath).parent.mkdir(parents=True, exist_ok=True)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(wm.model_dump_json(indent=2))

    @staticmethod
    def load_working_memory(filepath: str) -> WorkingMemory:
        """Loads WorkingMemory from JSON file."""
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
        return WorkingMemory(**data)
