import os
import re
from typing import Any

USAGE_COUNTS = {
    "completion_tokens",
    "prompt_tokens",
    "total_tokens",
    "input_tokens",
    "output_tokens",
    "cached_tokens",
    "reasoning_tokens",
    "audio_tokens",
    "accepted_prediction_tokens",
    "rejected_prediction_tokens",
    "prompt_cache_hit_tokens",
    "prompt_cache_miss_tokens",
}
USAGE_DETAILS = {"completion_tokens_details", "prompt_tokens_details", "input_tokens_details", "output_tokens_details"}


def sensitive_field(key: str, value: Any) -> bool:
    name = key.lower()
    if name in USAGE_COUNTS and isinstance(value, (int, float)) and not isinstance(value, bool):
        return False
    if name in USAGE_DETAILS and isinstance(value, dict):
        return False
    return any(s in name for s in ("api_key", "authorization", "secret", "token"))


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: "[REDACTED]" if sensitive_field(k, v) else redact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    if isinstance(value, str):
        for name in ("OPENAI_API_KEY", "USDA_API_KEY"):
            if secret := os.getenv(name):
                value = value.replace(secret, "[REDACTED]")
        return re.sub(r"sk-[A-Za-z0-9_-]{8,}", "[REDACTED]", value)
    return value
