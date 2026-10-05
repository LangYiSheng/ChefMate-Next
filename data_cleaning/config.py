"""Configuration anchored to this subproject; never loads the host project's .env."""

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent


def service_settings() -> dict:
    load_dotenv(ROOT / ".env", override=False)
    return {
        "openai_key": os.getenv("OPENAI_API_KEY", ""),
        "model": os.getenv("OPENAI_MODEL", ""),
        "base_url": os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
        "api_mode": os.getenv("OPENAI_API_MODE", "responses"),
        "retries": min(5, max(0, int(os.getenv("OPENAI_MAX_RETRIES", "2")))),
        "timeout": min(120, max(1, float(os.getenv("API_TIMEOUT_SECONDS", "45")))),
        "max_output_tokens": min(16000, max(500, int(os.getenv("OPENAI_MAX_OUTPUT_TOKENS", "6000")))),
    }


def local_path(value: str) -> Path:
    path = Path(value).expanduser()
    return path.resolve() if path.is_absolute() else (ROOT / path).resolve()


def database_path() -> Path:
    load_dotenv(ROOT / ".env", override=False)
    path = local_path(os.getenv("CLEANING_DB_PATH", "database/cleaning.db"))
    if not path.is_relative_to(ROOT):
        raise ValueError("SQLite 必须放在 data_cleaning 目录内")
    return path


def source_path() -> Path:
    load_dotenv(ROOT / ".env", override=False)
    return local_path(os.getenv("HOWTOCOOK_DATA_DIR", "data/raw/HowToCook"))


def usda_database_path() -> Path:
    load_dotenv(ROOT / ".env", override=False)
    return local_path(os.getenv("USDA_DB_PATH", "database/usda.db"))
