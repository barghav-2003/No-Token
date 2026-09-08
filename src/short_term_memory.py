"""Module 3: Sliding Window Buffer (Short-Term Memory).

Maintains a FIFO queue of the last N turns.
Automatically pushes evicted turns to ArchivalMemoryManager when capacity is exceeded.
"""
from typing import List, Optional
from collections import deque
from src.models import Turn
from src.archival_memory import ArchivalMemoryManager

class ShortTermMemoryBuffer:
    """Rolling FIFO queue for immediate conversational context window."""

    def __init__(self, max_turns: int = 4, archival_manager: Optional[ArchivalMemoryManager] = None):
        self.max_turns = max_turns
        self.buffer: deque[Turn] = deque()
        self.archival_manager = archival_manager
        self.last_evicted_turn: Optional[Turn] = None

    def add_turn(self, turn: Turn, session_id: str = "default") -> Optional[Turn]:
        """
        Appends turn to sliding window buffer.
        If size exceeds max_turns, pops oldest turn and archives it if archival_manager is attached.
        Returns the evicted turn if any, else None.
        """
        self.buffer.append(turn)
        evicted_turn: Optional[Turn] = None

        if len(self.buffer) > self.max_turns:
            evicted_turn = self.buffer.popleft()
            if self.archival_manager:
                custom_text = evicted_turn.to_transcript_format()
                if self.last_evicted_turn:
                    # Prepend context from immediately preceding turn
                    custom_text = f"Context:\n{self.last_evicted_turn.to_transcript_format()}\n---\n{custom_text}"
                
                self.archival_manager.add_turn(evicted_turn, session_id=session_id, custom_text=custom_text)
                
            self.last_evicted_turn = evicted_turn

        return evicted_turn

    def get_history(self) -> List[Turn]:
        """Returns list of turns currently in short-term buffer."""
        return list(self.buffer)

    def to_formatted_string(self) -> str:
        """Renders turns as formatted history block for prompt assembly."""
        if not self.buffer:
            return "No previous turns."
        
        formatted_turns = []
        for idx, turn in enumerate(self.buffer, 1):
            formatted_turns.append(f"Turn {idx}:\nUser: {turn.user_message}\nAssistant: {turn.assistant_message}")
        
        return "\n\n".join(formatted_turns)

    def clear(self) -> None:
        """Clears the short-term memory buffer."""
        self.buffer.clear()

    def __len__(self) -> int:
        return len(self.buffer)
