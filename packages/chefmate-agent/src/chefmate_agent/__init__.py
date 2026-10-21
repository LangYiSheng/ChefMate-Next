"""ChefMate's injectable, read-only conversational agent core."""

from .core import ChefMateAgent, RetrievalServiceProtocol
from .models import AgentConfig, AgentError, AgentTurnResult, ReplySuggestion, ToolCallRecord
from .extensions import InjectedToolSpec, ToolExecutionContext
from .skills import KITCHEN_TOOL_SKILLS, SkillRegistry

__all__ = [
    "AgentConfig", "AgentError", "AgentTurnResult", "ChefMateAgent", "RetrievalServiceProtocol", "ToolCallRecord",
    "InjectedToolSpec", "ToolExecutionContext", "SkillRegistry", "ReplySuggestion", "KITCHEN_TOOL_SKILLS",
]
