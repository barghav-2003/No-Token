"""Unit test suite for Python Hybrid Memory Engine."""
# pyrefly: ignore [missing-import]
import pytest
from src.models import WorkingMemory, Turn
from src.state_extractor import StateExtractor
from src.short_term_memory import ShortTermMemoryBuffer
from src.archival_memory import ArchivalMemoryManager
from src.prompt_assembler import DynamicPromptAssembler

def test_working_memory_model():
    wm = WorkingMemory(
        persona="Test Persona",
        rules=["Rule 1", "Rule 2"]
    )
    assert wm.persona == "Test Persona"
    assert len(wm.rules) == 2

def test_state_extractor():
    raw_text = """
    Persona: Systems Architect
    Rule: Always validate input types.

    User: Hello system!
    Assistant: Hello! How can I assist you?
    """
    wm, turns = StateExtractor.parse_transcript_text(raw_text)
    assert wm.persona == "Systems Architect"
    assert "Always validate input types." in wm.rules
    assert len(turns) == 1
    assert turns[0].user_message == "Hello system!"
    assert turns[0].assistant_message == "Hello! How can I assist you?"

def test_short_term_buffer_and_eviction(tmp_path):
    archival = ArchivalMemoryManager(db_dir=str(tmp_path / "chroma_test"))
    buffer = ShortTermMemoryBuffer(max_turns=2, archival_manager=archival)

    turn1 = Turn(user_message="Msg 1", assistant_message="Ans 1")
    turn2 = Turn(user_message="Msg 2", assistant_message="Ans 2")
    turn3 = Turn(user_message="Msg 3", assistant_message="Ans 3")

    buffer.add_turn(turn1)
    buffer.add_turn(turn2)
    assert len(buffer) == 2
    assert archival.count() == 0

    # Adding 3rd turn evicted turn1 into archival store
    evicted = buffer.add_turn(turn3)
    assert evicted == turn1
    assert len(buffer) == 2
    assert archival.count() == 1

def test_prompt_assembler():
    wm = WorkingMemory(
        persona="Test Bot",
        rules=["Rule 1"]
    )
    turns = [Turn(user_message="Hi", assistant_message="Hello")]
    payload = DynamicPromptAssembler.assemble(
        working_memory=wm,
        user_input="Next question",
        short_term_turns=turns,
        archival_snippets=["Retrieved snippet 1"]
    )

    assert "<system_working_rules>" in payload.full_prompt
    assert "Role: Test Bot" in payload.full_prompt
    assert "<retrieved_archival_context>" in payload.full_prompt
    assert "Retrieved snippet 1" in payload.full_prompt
    assert "<recent_ui_session_history>" in payload.full_prompt
    assert "<current_user_prompt>" in payload.full_prompt
    assert "User: Next question" in payload.full_prompt
