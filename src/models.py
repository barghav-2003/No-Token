"""Data models for Hybrid Memory Engine using Pydantic."""
from typing import List, Optional
from pydantic import BaseModel, Field
import uuid
import datetime
import hashlib

class WorkingMemory(BaseModel):
    """Core persona, static rules, scope, and non-negotiable constraints."""
    persona: str = Field(description="Active AI persona and identity description.")
    rules: List[str] = Field(default_factory=list, description="Non-negotiable system rules.")
    project_scope: str = Field(default="", description="Boundaries and scope of the project/conversation.")
    constraints: List[str] = Field(default_factory=list, description="System limits and mandatory operational constraints.")
    map_index: str = Field(default="", description="Markdown Table of Contents of user topics.")

class Message(BaseModel):
    """Single message in conversation."""
    role: str = Field(description="'user', 'model', or 'system'")
    content: str = Field(description="Message body text.")
    timestamp: str = Field(default_factory=lambda: datetime.datetime.now(datetime.timezone.utc).isoformat())

class Turn(BaseModel):
    """Complete conversational turn (User query + Assistant response)."""
    turn_id: Optional[str] = Field(default=None)
    turn_index: Optional[int] = Field(default=None, description="Sequential index of the turn in the session.")
    session_id: str = Field(default="default", description="Session identifier for multi-session isolation.")
    user_message: str = Field(description="User prompt for this turn.")
    assistant_message: str = Field(description="Assistant response for this turn.")
    timestamp: str = Field(default_factory=lambda: datetime.datetime.now(datetime.timezone.utc).isoformat())

    def model_post_init(self, __context):
        if not self.turn_id:
            idx_prefix = f"{self.turn_index}||" if self.turn_index is not None else ""
            raw_content = f"{self.session_id}||{idx_prefix}{self.user_message.strip()}||{self.assistant_message.strip()}"
            self.turn_id = hashlib.md5(raw_content.encode('utf-8')).hexdigest()

    def to_transcript_format(self) -> str:
        """Formats turn as plain text for context assembly."""
        return f"User: {self.user_message}\nAssistant: {self.assistant_message}"

class ContextPayload(BaseModel):
    """Compiled context payload ready to send to LLM."""
    system_instruction: str
    archival_context: Optional[str] = None
    recent_history: List[Turn] = Field(default_factory=list)
    user_input: str
    full_prompt: str
