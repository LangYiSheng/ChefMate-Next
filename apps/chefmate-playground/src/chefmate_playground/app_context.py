"""Mutable application dependencies shared by route groups.

Only configuration and the configured agent change while the app is alive.
Keeping their owner explicit prevents routes from retaining old closure values.
"""

from dataclasses import dataclass, field
from typing import Any

from fastapi import FastAPI, HTTPException

from .config import PlaygroundConfig
from .cookbook import CookbookRepository
from .images import SessionImages
from .interactions import WorkspaceBinding
from .journal import JournalRepository
from .memory import MemoryRepository
from .runtime import TurnManager
from .sessions import SessionRepository


@dataclass
class AppContext:
    app: FastAPI
    config: PlaygroundConfig
    repository: SessionRepository
    cookbook: CookbookRepository
    journal: JournalRepository
    memory: MemoryRepository
    retrieval: Any = None
    agent: Any = None
    components: Any = None
    preferences: Any = None
    setup_error: str | None = None
    workspace: WorkspaceBinding | None = None
    images: SessionImages = field(init=False)
    manager: TurnManager = field(init=False)

    def require_session(self, session_id: str):
        state = self.repository.get(session_id)
        if state is None:
            raise HTTPException(404, "对话不存在。")
        return state
