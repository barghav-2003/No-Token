"""Module 4: Dynamic Prompt Assembler.

Compiles Working Memory, Archival Context, Short-Term Memory, and User Input
into a structured XML-tagged payload. Uses exact token counting to prevent context overflow.
"""
from typing import List, Optional
# pyrefly: ignore [missing-import]
import tiktoken
import logging
from src.models import WorkingMemory, Turn, ContextPayload

class DynamicPromptAssembler:
    """Assembles prompt payload matching specification schema with dynamic token limits."""

    @staticmethod
    def _count_tokens(text: str) -> int:
        """Accurately counts tokens using standard cl100k_base tokenizer."""
        try:
            encoding = tiktoken.get_encoding("cl100k_base")
            return len(encoding.encode(text, disallowed_special=()))
        except Exception as e:
            logging.error(f"Tiktoken counting error: {e}")
            return len(text) // 4  # Fallback rough estimate

    @staticmethod
    def sanitize_input(text: str) -> str:
        """Strips structural XML tags to prevent prompt injection."""
        reserved_tags = [
            "<system_working_rules>", "</system_working_rules>",
            "<retrieved_archival_context>", "</retrieved_archival_context>",
            "<recent_ui_session_history>", "</recent_ui_session_history>",
            "<current_user_prompt>", "</current_user_prompt>"
        ]
        sanitized = text
        for tag in reserved_tags:
            sanitized = sanitized.replace(tag, "")
        return sanitized

    @staticmethod
    def calculate_required_tokens(
        working_memory: WorkingMemory,
        user_input: str,
        short_term_turns: List[Turn],
        archival_snippets: Optional[List[str]] = None
    ) -> int:
        """Calculates total tokens required if zero context was dropped."""
        # Use assemble with no limit and count its output
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
        """Constructs XML-tagged prompt string with exact token limiting."""
        user_input = DynamicPromptAssembler.sanitize_input(user_input)

        # 1. SYSTEM INSTRUCTION (Must fit, never dropped)
        rules_formatted = "\n".join([f"- {r}" for r in working_memory.rules])
        constraints_formatted = "\n".join([f"- {c}" for c in working_memory.constraints]) if working_memory.constraints else ""
        
        sys_body = f"Role: {working_memory.persona}\nConstraints:\n{rules_formatted}"
        if constraints_formatted:
            sys_body += f"\n{constraints_formatted}"
            
        if working_memory.map_index:
            sys_body += f"\n\n{working_memory.map_index}"
            
        grounding_instruction = "Base answers strictly on the provided XML tags. Do not confuse past transcript content with current UI session prompts. If information is missing, explicitly state so."
        sys_body += f"\n\n{grounding_instruction}"

        system_instruction = f"<system_working_rules>\n{sys_body}\n</system_working_rules>"

        # 4. CURRENT USER INPUT (Must fit, never dropped)
        user_input_text = f"<current_user_prompt>\nUser: {user_input}\n</current_user_prompt>"
        
        base_tokens = DynamicPromptAssembler._count_tokens(system_instruction + "\n\n" + user_input_text)
        remaining_tokens = max_prompt_tokens - base_tokens if max_prompt_tokens is not None else float('inf')

        # 2. SHORT TERM HISTORY (Drop oldest if we run out of tokens)
        history_blocks = []
        # We iterate in reverse (newest first) to prioritize recent context
        for turn in reversed(short_term_turns):
            turn_text = turn.to_transcript_format()
            turn_tokens = DynamicPromptAssembler._count_tokens(turn_text + "\n\n")
            if remaining_tokens - turn_tokens > 0:
                history_blocks.insert(0, turn_text) # insert at start to maintain chronological order
                remaining_tokens -= turn_tokens
            else:
                logging.warning(f"Dropping older short-term turn due to token limit.")
                
        if history_blocks:
            history_body = "\n\n".join(history_blocks)
            recent_history_text = f"<recent_ui_session_history>\n{history_body}\n</recent_ui_session_history>"
        else:
            recent_history_text = "<recent_ui_session_history>\n(No prior history in sliding window)\n</recent_ui_session_history>"
            
        remaining_tokens -= DynamicPromptAssembler._count_tokens("<recent_ui_session_history>\n\n</recent_ui_session_history>")

        # 3. ARCHIVAL CONTEXT (Drop if we still run out of tokens)
        archival_text = ""
        if archival_snippets and len(archival_snippets) > 0:
            accepted_snippets = []
            for snippet in archival_snippets: # Highest relevance first
                snippet_tokens = DynamicPromptAssembler._count_tokens(snippet + "\n---\n")
                if remaining_tokens - snippet_tokens > 0:
                    accepted_snippets.append(snippet)
                    remaining_tokens -= snippet_tokens
                else:
                    logging.warning(f"Dropping archival snippet due to token limit.")
            
            if accepted_snippets:
                archive_body = "\n---\n".join(accepted_snippets)
                archival_text = (
                    "<retrieved_archival_context>\n"
                    "The following past conversation snippet was retrieved from local storage because it is relevant to the user's query:\n"
                    f"{archive_body}\n"
                    "</retrieved_archival_context>"
                )

        # Combine into complete payload
        prompt_sections = [system_instruction]
        if archival_text:
            prompt_sections.append(archival_text)
        prompt_sections.append(recent_history_text)
        prompt_sections.append(user_input_text)

        full_prompt = "\n\n".join(prompt_sections)

        return ContextPayload(
            system_instruction=system_instruction,
            archival_context=archival_text if archival_text else None,
            recent_history=short_term_turns,  # Return original list, even if some were dropped from payload
            user_input=user_input,
            full_prompt=full_prompt
        )
