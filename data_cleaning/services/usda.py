"""FoodData Central only. No LLM can produce or change nutrition values."""

import time
import re
from collections.abc import Callable

import requests

from data_cleaning.config import service_settings
from data_cleaning.models.catalog import CatalogRepository
from data_cleaning.schemas.cleaning import Nutrients
from data_cleaning.services.redaction import redact


class USDARequestError(ValueError):
    """Only carries sanitized diagnostics, never a request URL or headers."""

    def __init__(self, message: str, details: dict):
        super().__init__(message)
        self.details = details


def safe_error_body(response, secret: str) -> str:
    text = getattr(response, "text", "") or ""
    if secret:
        text = text.replace(secret, "[REDACTED]")
    text = redact(text)
    text = re.sub(r'(?i)(["\s?&]*(?:api_key|access_token|authorization)["\s]*[:=]["\s]*)([^\s&"<>]+)', r'\1[REDACTED]', text)
    text = re.sub(r"(?i)Bearer\s+[^\s\"<>]+", "Bearer [REDACTED]", text)
    return text[:2000]

BASE_URL = "https://api.nal.usda.gov/fdc/v1"
DATA_TYPES = ["Foundation", "SR Legacy", "Survey (FNDDS)"]
# USDA nutrient IDs, units and precedence. Absent values remain null.
NUTRIENT_IDS = {
    "calories_kcal": ([1008, 2048, 2047], "KCAL"),
    "protein_g": ([1003], "G"),
    "fat_g": ([1004], "G"),
    "carbohydrate_g": ([1005], "G"),
    "sugar_g": ([2000, 1063], "G"),
    "sodium_mg": ([1093], "MG"),
}


def parse_nutrients(food: dict) -> Nutrients:
    if food.get("dataType") not in DATA_TYPES:
        raise ValueError("仅接受 Foundation、SR Legacy、Survey 的每100g数据，品牌标签数据需另行核实基准")
    if not food.get("fdcId") or not food.get("description"):
        raise ValueError("USDA 响应缺少食物来源标识")
    values = {}
    for field, (ids, unit) in NUTRIENT_IDS.items():
        values[field] = None
        for nutrient_id in ids:
            entries = [n for n in food.get("foodNutrients", []) if n.get("nutrient", {}).get("id") == nutrient_id]
            found = False
            for entry in entries:
                if str(entry["nutrient"].get("unitName", "")).upper() == unit and entry.get("amount") is not None:
                    values[field] = entry["amount"]
                    found = True
                    break
            if found:
                break
    return Nutrients.model_validate(values)


class USDAService:
    def __init__(self, repo: CatalogRepository, session=None, sleeper: Callable = time.sleep):
        self.repo = repo
        self.owns_session = session is None
        self.session = session or requests.Session()
        self.settings = service_settings()
        self.sleeper = sleeper
        self.progress = lambda message: None

    def _get(self, route: str, params: dict) -> dict:
        if not self.settings["usda_key"]:
            raise ValueError("请在 .env 配置 USDA_API_KEY")
        for attempt in range(self.settings["retries"] + 1):
            self.progress(f"USDA {route}：第 {attempt + 1}/{self.settings['retries'] + 1} 次请求")
            response = None
            try:
                response = self.session.get(
                    BASE_URL + route,
                    params={**params, "api_key": self.settings["usda_key"]},
                    timeout=self.settings["timeout"],
                )
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict):
                    raise ValueError("USDA 响应不是 JSON 对象")
                self.repo.service_call("USDA", route, "", "approved", params, payload)
                self.progress("USDA 响应成功")
                return payload
            except Exception as exc:
                code = getattr(getattr(exc, "response", None), "status_code", None)
                error = f"{type(exc).__name__}; HTTP {code}"
                body = safe_error_body(response, self.settings["usda_key"])
                gateway_400 = code == 400 and "<html" in body.lower() and "nginx" in body.lower()
                retryable = gateway_400 or code in (408, 429, 500, 502, 503, 504) or isinstance(
                    exc, (requests.ConnectionError, requests.Timeout)
                )
                details = {
                    "http_status": code,
                    "route": route,
                    "attempt": attempt + 1,
                    "error_type": type(exc).__name__,
                    "response_excerpt": body,
                    "retryable": retryable,
                }
                self.repo.service_call("USDA", route, "", "failed", params, response=details, error=error)
                self.progress(f"USDA 请求失败：{error}")
                if attempt == self.settings["retries"] or not retryable:
                    raise USDARequestError(f"USDA 请求失败（{error}），请查看响应摘要", details) from None
                if self.owns_session:
                    self.session.close()
                    self.session = requests.Session()
                self.progress(f"USDA 等待 {min(2**attempt, 4)} 秒后重试")
                self.sleeper(min(2**attempt, 4))
        raise RuntimeError("unreachable")

    def search(self, query: str) -> list[dict]:
        if not query.strip():
            raise ValueError("请输入 USDA 搜索词（建议英文食材名及生熟状态）")
        result = self._get("/foods/search", {"query": query, "pageSize": 15, "dataType": DATA_TYPES})
        return [
            {"fdcId": f["fdcId"], "description": f.get("description", ""), "dataType": f.get("dataType", "")}
            for f in result.get("foods", [])
            if f.get("dataType") in DATA_TYPES
        ]

    def food(self, fdc_id: str, *, refresh: bool = False) -> dict:
        if not str(fdc_id).isdigit():
            raise ValueError("FDC ID 必须是数字")
        if not refresh and (cached := self.repo.cached_food(str(fdc_id))):
            return cached
        result = self._get(f"/food/{fdc_id}", {"format": "full"})
        if str(result.get("fdcId")) != str(fdc_id):
            raise ValueError("USDA 返回的 FDC ID 与请求不一致")
        parse_nutrients(result)
        self.repo.cache_food(result)
        return result
