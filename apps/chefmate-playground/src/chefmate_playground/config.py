"""Application-owned config; shared libraries never read the cleaning app's env."""

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import dotenv_values

from .components import DEFAULT_COMPONENTS


APP_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class PlaygroundConfig:
    app_database_url: str = field(default="", repr=False)
    app_database_schema: str = "chefmate"
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
    max_history_chars: int = 400000
    max_tool_result_chars: int = 60000
    stream_tool_calls: bool = False
    supports_images: bool = False
    max_images: int = 3
    reply_suggestions: bool = True
    reply_suggestion_count: int = 3
    ui_preferences: dict = field(default_factory=dict)
    timer_limit: int = 3
    clear_timer_on_step_change: bool = False
    api_format: str = "chat_completions"
    port: int = 18765
    sessions_database: Path = APP_ROOT / ".runtime" / "sessions.db"
    enabled_components: tuple[str, ...] = DEFAULT_COMPONENTS
    # Authentication is always enabled by the CLI. The explicit constructor
    # switch exists for isolated package/HTTP fixtures, never for an env bypass.
    auth_enabled: bool = True
    managed_user: bool = False
    host: str = "127.0.0.1"
    allowed_hosts: tuple[str, ...] = ("127.0.0.1", "localhost", "::1", "testserver")
    cookie_secure: bool = False
    admin_username: str = ""
    admin_password: str = field(default="", repr=False)
    new_user_cost_limit_micros: int = 5_000_000

    def __post_init__(self):
        if type(self.new_user_cost_limit_micros) is not int or not 0 <= self.new_user_cost_limit_micros <= 10**15:
            raise ValueError("新用户初始金额额度必须是非负整数微元。")
        if not isinstance(self.app_database_schema, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", self.app_database_schema):
            raise ValueError("应用数据库 schema 名称无效。")
        if self.auth_enabled and not self.managed_user and len(self.app_database_schema) > 25:
            raise ValueError("应用数据库 schema 前缀最多 25 位，需为用户命名空间预留长度。")
        from .ui_preferences import AppearancePreferences
        AppearancePreferences.model_validate(self.ui_preferences)
        if (not isinstance(self.enabled_components, tuple)
                or any(not isinstance(name, str) or name not in DEFAULT_COMPONENTS for name in self.enabled_components)
                or len(self.enabled_components) != len(set(self.enabled_components))):
            raise ValueError("系统组件配置无效")
        if type(self.max_images) is not int or not 1 <= self.max_images <= 12:
            raise ValueError("模型图片上限必须是 1 到 12 之间的整数")
        if type(self.reply_suggestions) is not bool:
            raise ValueError("推荐回复开关必须是布尔值")
        if type(self.reply_suggestion_count) is not int or not 1 <= self.reply_suggestion_count <= 6:
            raise ValueError("推荐回复数量必须为1到6之间的整数")
        if type(self.timer_limit) is not int or self.timer_limit not in {1, 3}:
            raise ValueError("同时计时上限必须为1或3")
        if type(self.clear_timer_on_step_change) is not bool:
            raise ValueError("切换步骤清理计时器开关必须是布尔值")

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
        component_names = tuple(name.strip() for name in values.get(
            "CHEFMATE_ENABLED_COMPONENTS", ",".join(DEFAULT_COMPONENTS)).split(",") if name.strip())
        if (len(component_names) != len(set(component_names))
                or any(name not in DEFAULT_COMPONENTS for name in component_names)):
            raise ValueError("系统组件配置无效")
        runtime_dir = Path(values.get("CHEFMATE_RUNTIME_DIR") or APP_ROOT / ".runtime").expanduser()
        if not runtime_dir.is_absolute():
            runtime_dir = (source.parent / runtime_dir).resolve()
        config = cls(
            app_database_url=values.get("CHEFMATE_APP_DATABASE_URL", "") or "",
            app_database_schema=values.get("CHEFMATE_APP_DATABASE_SCHEMA", "chefmate") or "chefmate",
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
            max_history_chars=int(values.get("CHEFMATE_AGENT_MAX_HISTORY_CHARS", "400000")),
            max_tool_result_chars=int(values.get("CHEFMATE_AGENT_MAX_TOOL_RESULT_CHARS", "60000")),
            stream_tool_calls=(values.get("CHEFMATE_AGENT_STREAM_TOOL_CALLS", "false") or "false").lower() == "true",
            supports_images=(values.get("CHEFMATE_MODEL_SUPPORTS_IMAGES", "false") or "false").lower() == "true",
            max_images=int(values.get("CHEFMATE_MODEL_MAX_IMAGES", "3")),
            timer_limit=int(values.get("CHEFMATE_TIMER_LIMIT", "3")),
            clear_timer_on_step_change=(values.get("CHEFMATE_CLEAR_TIMER_ON_STEP_CHANGE", "false") or "false").lower() == "true",
            port=int(values.get("CHEFMATE_PLAYGROUND_PORT", "18765")),
            host=values.get("CHEFMATE_PLAYGROUND_HOST", "127.0.0.1") or "127.0.0.1",
            sessions_database=runtime_dir / "sessions.db",
            allowed_hosts=tuple(value.strip().lower() for value in values.get(
                "CHEFMATE_ALLOWED_HOSTS", "127.0.0.1,localhost,::1,testserver").split(",") if value.strip()),
            cookie_secure=(values.get("CHEFMATE_COOKIE_SECURE", "false") or "false").lower() == "true",
            admin_username=values.get("CHEFMATE_ADMIN_USERNAME", "") or "",
            admin_password=values.get("CHEFMATE_ADMIN_PASSWORD", "") or "",
            new_user_cost_limit_micros=int(values.get("CHEFMATE_NEW_USER_COST_LIMIT_MICROS", "5000000")),
            enabled_components=component_names,
        )
        from .settings import load_overrides
        return load_overrides(config)
