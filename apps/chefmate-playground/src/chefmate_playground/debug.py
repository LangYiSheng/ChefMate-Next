"""Presentation-safe developer records. Never expose credentials or image bytes."""

import json
import re

_SECRET_KEYS = re.compile(r"^(?:api_?key|password|secret|token|access_token|authorization|database_url|dsn|connection_string)$", re.I)
_PRIVATE_KEYS = {"reasoning", "reasoning_content", "chain_of_thought", "image_contents"}


def redact_debug(value, secrets=()):
    if isinstance(value, dict):
        return {key: "[已隐藏]" if _SECRET_KEYS.match(key) else redact_debug(item, secrets)
                for key, item in value.items() if key not in _PRIVATE_KEYS}
    if isinstance(value, (list, tuple)):
        return [redact_debug(item, secrets) for item in value]
    if not isinstance(value, str):
        return value
    if value.lstrip().startswith(("{", "[")):
        try:
            parsed = json.loads(value)
        except (ValueError, TypeError):
            pass
        else:
            return json.dumps(redact_debug(parsed, secrets), ensure_ascii=False)
    value = re.sub(r"data:image/[^;\s]+;base64,[A-Za-z0-9+/=]+", "[图片内容已省略]", value)
    value = re.sub(r"(\b(?:postgres(?:ql)?|https?)://)[^\s/@]+:[^\s/@]+@", r"\1[已隐藏]@", value, flags=re.I)
    value = re.sub(r"\bBearer\s+\S+", "Bearer [已隐藏]", value, flags=re.I)
    value = re.sub(r"\bsk-[a-zA-Z0-9_-]{12,}", "[密钥已隐藏]", value)
    value = re.sub(r"((?:api[_ -]?key|password|secret|authorization)\s*[:=]\s*)[^\s,;]+", r"\1[已隐藏]", value, flags=re.I)
    for secret in secrets:
        if isinstance(secret, str) and len(secret) >= 4:
            value = value.replace(secret, "[已隐藏]")
    return value
