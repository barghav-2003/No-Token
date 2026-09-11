"""Module 4: Dynamic Prompt Assembler.

Compiles Working Memory, Archival Context, Short-Term Memory, and User Input
into a structured XML-tagged payload. Uses exact token counting and multi-tier
safety guardrails to prevent context overflow.
"""
import re
import math
import logging
from typing import List, Optional
# pyrefly: ignore [missing-import]
import tiktoken
from src.models import WorkingMemory, Turn, ContextPayload

try:
    _ENCODER = tiktoken.get_encoding("cl100k_base")
except Exception as e:
    logging.error(f"Tiktoken init error: {e}")
    _ENCODER = None


class DynamicPromptAssembler:
    """Assembles prompt payload matching specification schema with dynamic token limits."""

    @staticmethod
    def _count_tokens(text: str) -> int:
        """Accurately counts tokens using cl100k_base tokenizer with conservative fallback."""
        global _ENCODER
        if _ENCODER:
            try:
                return len(_ENCODER.encode(text, disallowed_special=()))
            except Exception as e:
                logging.error(f"Tiktoken counting error: {e}")
        # Conservative fallback: ~3.3 chars per token for code/identifiers/SentencePiece
        return math.ceil(len(text) / 3.3)

    @staticmethod
    def sanitize_input(text: str) -> str:
        """
        Strips structural XML tags to prevent prompt injection and wrapper spoofing.
        Handles case insensitivity and whitespace variations.
        """
        pattern = re.compile(
            r"<\s*/?\s*(?:system_working_rules|retrieved_archival_context|recent_ui_session_history|current_user_prompt|archive)\s*/?>",
            re.IGNORECASE
        )
        return pattern.sub("", text)

    @staticmethod
    def calculate_required_tokens(
        working_memory: WorkingMemory,
        user_input: str,
        short_term_turns: List[Turn],
        archival_snippets: Optional[List[str]] = None
    ) -> int:
        """Calculates total tokens required if zero context was dropped."""
        payload = DynamicPromptAssembler.assemble(
            working_memory, user_input, short_term_turns, archival_snippets, max_prompt_tokens=None
        )
        return DynamicPromptAssembler._count_tokens(payload.full_prompt)

    @staticmethod
    def assemble(
        working_memory: WorkingMemory,
        user_input: str,
        short_term_turns: List[Turn],
        archival_snippets: Optional[List[str]] = None,
        max_prompt_tokens: Optional[int] = None
    ) -> ContextPayload:
        """
        Constructs XML-tagged prompt string with multi-tier token limiting:
        1. Strips injection tags from user input.
        2. Guards against base token overflow (compresses TOC or truncates huge inputs if needed).
        3. Allocates remaining budget to short-term history (reverse chronological priority).
        4. Allocates remaining budget to archival RAG snippets.
        """
        user_input = DynamicPromptAssembler.sanitize_input(user_input)

        # 1. BASE SYSTEM INSTRUCTION SETUP
        rules_formatted = "\n".join([f"- {r}" for r in working_memory.rules])
        constraints_formatted = (
            "\n".join([f"- {c}" for c in working_memory.constraints])
            if working_memory.constraints else ""
        )

        sys_core = f"Role: {working_memory.persona}\nConstraints:\n{rules_formatted}"
        if constraints_formatted:
            sys_core += f"\n{constraints_formatted}"

        grounding_instruction = (
            "You are an intelligent, context-aware AI assistant operating in dual mode:\n"
            "1. TRANSCRIPT & FILE INQUIRIES: If the user's query refers to, asks about, or is based on the uploaded transcript or past conversation (e.g., questions about the document, previous topics, or specific details from the chat), answer STRICTLY based on the provided <retrieved_archival_context> and <recent_ui_session_history>. Do not hallucinate or invent details not present in the context. If specific information asked about the transcript is missing, explicitly state that it was not found in the uploaded text.\n"
            "2. GENERAL KNOWLEDGE & OPEN INQUIRIES: If the user asks a general question, advice, or topic unrelated to the uploaded transcript or past discussion (e.g., diet plans, coding help, creative writing, science, or general explanations), answer normally, helpfully, and comprehensively using your full pre-trained capabilities as an expert AI assistant. Do NOT claim you cannot answer or apologize simply because the topic was not mentioned in the transcript."
        )

        map_index = working_memory.map_index
        sys_body = sys_core
        if map_index:
            sys_body += f"\n\n{map_index}"
        sys_body += f"\n\n{grounding_instruction}"

        system_instruction = f"<system_working_rules>\n{sys_body}\n</system_working_rules>"
        user_input_text = f"<current_user_prompt>\nUser: {user_input}\n</current_user_prompt>"

        # Pre-account for structural wrapper tags
        history_wrapper_tokens = DynamicPromptAssembler._count_tokens(
            "<recent_ui_session_history>\n\n</recent_ui_session_history>"
        )
        archival_wrapper_tokens = DynamicPromptAssembler._count_tokens(
            "<retrieved_archival_context>\nThe following past conversation snippet was retrieved from local storage because it is relevant to the user's query:\n\n</retrieved_archival_context>"
        )

        base_tokens = (
            DynamicPromptAssembler._count_tokens(system_instruction + "\n\n" + user_input_text)
            + history_wrapper_tokens
            + archival_wrapper_tokens
        )

        # --- BASE TOKEN OVERFLOW GUARDRAILS ---
        if max_prompt_tokens is not None:
            # Level 1: If base tokens exceed 70% of max tokens and map_index is present, prune map_index
            if base_tokens > max_prompt_tokens * 0.70 and map_index:
                logging.warning("Base tokens exceed 70% of prompt budget. Pruning Table of Contents.")
                compact_toc = "## Table of Contents\n- *[Table of contents pruned to preserve conversation history]*"
                sys_body = f"{sys_core}\n\n{compact_toc}\n\n{grounding_instruction}"
                system_instruction = f"<system_working_rules>\n{sys_body}\n</system_working_rules>"
                base_tokens = (
                    DynamicPromptAssembler._count_tokens(system_instruction + "\n\n" + user_input_text)
                    + history_wrapper_tokens
                    + archival_wrapper_tokens
                )

            # Level 2: If user input itself is massive and exceeds 85% of budget, truncate user input
            if base_tokens > max_prompt_tokens * 0.85:
                logging.warning("Base tokens exceed 85% of prompt budget. Truncating oversized user input.")
                allowed_chars = int(max_prompt_tokens * 0.40 * 3.3)
                truncated_user = user_input[:allowed_chars] + "\n[... user input truncated for context limit ...]"
                user_input_text = f"<current_user_prompt>\nUser: {truncated_user}\n</current_user_prompt>"
                base_tokens = (
                    DynamicPromptAssembler._count_tokens(system_instruction + "\n\n" + user_input_text)
                    + history_wrapper_tokens
                    + archival_wrapper_tokens
                )

        remaining_tokens = (
            max(0, max_prompt_tokens - base_tokens)
            if max_prompt_tokens is not None
            else float('inf')
        )

        # 2. SHORT TERM HISTORY (Drop oldest if we run out of tokens)
        history_blocks = []
        for turn in reversed(short_term_turns):
            turn_text = turn.to_transcript_format()
            turn_tokens = DynamicPromptAssembler._count_tokens(turn_text + "\n\n")
            if remaining_tokens - turn_tokens >= 0:
                history_blocks.insert(0, turn_text)  # chronological order
                remaining_tokens -= turn_tokens
            else:
                logging.warning("Dropping older short-term turn due to token limit.")

        if history_blocks:
            history_body = "\n\n".join(history_blocks)
            recent_history_text = f"<recent_ui_session_history>\n{history_body}\n</recent_ui_session_history>"
        else:
            recent_history_text = "<recent_ui_session_history>\n(No prior history in sliding window)\n</recent_ui_session_history>"

        # 3. ARCHIVAL CONTEXT (Drop if we still run out of tokens)
        archival_text = ""
        if archival_snippets:
            accepted_snippets = []
            for snippet in archival_snippets:
                snippet_tokens = DynamicPromptAssembler._count_tokens(snippet + "\n---\n")
                if remaining_tokens - snippet_tokens >= 0:
                    accepted_snippets.append(snippet)
                    remaining_tokens -= snippet_tokens
                else:
                    logging.warning("Dropping archival snippet due to token limit.")

            if accepted_snippets:
                archive_body = "\n---\n".join(accepted_snippets)
                archival_text = (
                    "<retrieved_archival_context>\n"
                    "The following past conversation snippet was retrieved from local storage because it is relevant to the user's query:\n"
                    f"{archive_body}\n"
                    "</retrieved_archival_context>"
                )

        # 4. COMPOSE PAYLOAD
        prompt_sections = [system_instruction]
        if archival_text:
            prompt_sections.append(archival_text)
        prompt_sections.append(recent_history_text)
        prompt_sections.append(user_input_text)

        full_prompt = "\n\n".join(prompt_sections)

        return ContextPayload(
            system_instruction=system_instruction,
            archival_context=archival_text if archival_text else None,
            recent_history=short_term_turns,
            user_input=user_input,
            full_prompt=full_prompt
        )
