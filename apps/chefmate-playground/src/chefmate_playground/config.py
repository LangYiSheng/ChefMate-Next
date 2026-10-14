"""Application-owned config; shared libraries never read the cleaning app's env."""

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import dotenv_values


APP_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class PlaygroundConfig:
    database_url: str = field(default="", repr=False)
    database_schema: str = "chefmate"
    usda_database: Path | None = None
    api_key: str = field(default="", repr=False)
    model: str = ""
    base_url: str = "https://api.openai.com/v1"
    turn_timeout_s: float = 300
    model_timeout_s: float = 120
    model_max_retries: int = 0
    max_model_rounds: int = 10
    max_tool_calls: int = 20
    stream_tool_calls: bool = False
    port: int = 18765
    sessions_database: Path = APP_ROOT / ".runtime" / "sessions.db"
    enabled_skills: tuple[str, ...] = ("meal-planning", "pantry-preparation", "cooking-guide")

    @property
    def model_configured(self) -> bool:
        return bool(self.api_key.strip() and self.model.strip())

    @classmethod
    def load(cls, path: Path | None = None) -> "PlaygroundConfig":
        source = path or APP_ROOT / ".env"
        values = {**dotenv_values(source), **os.environ}
        usda = values.get("CHEFMATE_USDA_DATABASE", "../../data_cleaning/database/usda.db")
        database = Path(usda).expanduser() if usda else None
        if database is not None and not database.is_absolute():
            database = (source.parent / database).resolve()
        skill_names = tuple(name.strip() for name in (values.get("CHEFMATE_ENABLED_SKILLS")
                            if values.get("CHEFMATE_ENABLED_SKILLS") is not None
                            else "meal-planning,pantry-preparation,cooking-guide").split(",") if name.strip())
        if len(skill_names) > 16 or any(not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", name) for name in skill_names):
            raise ValueError("Enabled kitchen skill names are invalid")
        return cls(
            database_url=values.get("CHEFMATE_DATABASE_URL", "") or "",
            database_schema=values.get("CHEFMATE_DATABASE_SCHEMA", "chefmate") or "chefmate",
            usda_database=database,
            api_key=values.get("OPENAI_API_KEY", "") or "",
            model=values.get("OPENAI_MODEL", "") or "",
            base_url=values.get("OPENAI_BASE_URL", "https://api.openai.com/v1") or "https://api.openai.com/v1",
            turn_timeout_s=min(300, max(10, float(values.get("CHEFMATE_AGENT_TIMEOUT", "300")))),
            model_timeout_s=min(300, max(10, float(values.get("CHEFMATE_AGENT_MODEL_TIMEOUT", "120")))),
            model_max_retries=min(2, max(0, int(values.get("CHEFMATE_AGENT_MODEL_RETRIES", "0")))),
            max_model_rounds=min(30, max(2, int(values.get("CHEFMATE_AGENT_MAX_ROUNDS", "10")))),
            max_tool_calls=min(50, max(2, int(values.get("CHEFMATE_AGENT_MAX_TOOL_CALLS", "20")))),
            stream_tool_calls=(values.get("CHEFMATE_AGENT_STREAM_TOOL_CALLS", "false") or "false").lower() == "true",
            port=int(values.get("CHEFMATE_PLAYGROUND_PORT", "18765")),
            enabled_skills=skill_names,
        )
