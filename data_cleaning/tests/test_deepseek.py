import json

import httpx
from openai import OpenAI

from data_cleaning.models.workflow import WorkflowRepository
from data_cleaning.schemas.cleaning import MatchChoice
from data_cleaning.services.howtocook import parse
from data_cleaning.services.openai_service import OpenAIService


def test_deepseek_json_retry_and_sdk_contract(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_MODE", "deepseek_json")
    monkeypatch.setenv("OPENAI_MODEL", "deepseek-flash")
    calls = []
    valid = dict(selected_ingredient_id=None, confidence=0, reason="无匹配", suggested_name=None)

    def handler(request):
        body = json.loads(request.content)
        calls.append(body)
        assert str(request.url) == "https://api.deepseek.com/chat/completions"
        assert body["model"] == "deepseek-flash"
        assert body["response_format"] == {"type": "json_object"}
        assert "JSON Schema" in body["messages"][0]["content"]
        assert "text_format" not in body and "store" not in body
        content = "{}" if len(calls) == 1 else json.dumps(valid)
        return httpx.Response(
            200,
            json={
                "id": "test",
                "object": "chat.completion",
                "created": 0,
                "model": "deepseek-flash",
                "choices": [
                    {"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": content}}
                ],
            },
        )

    repo = WorkflowRepository(tmp_path / "test.db")
    with OpenAI(
        api_key="test",
        base_url="https://api.deepseek.com",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    ) as client:
        result = OpenAIService(repo, client, sleeper=lambda _: None).request("match", {}, MatchChoice, "test")
    assert result.model_dump() == valid
    assert len(calls) == 2


def test_nested_howtocook_steps_keep_parent_context():
    recipe = parse(
        "# 蛋\n## 原料\n- 鸡蛋\n## 操作\n### 方案一\n1. 打蛋\n### 方案二\n1. 蒸蛋\n## 附加内容\n不要当步骤", "r", "test"
    )
    assert recipe.steps == ["打蛋", "蒸蛋"]
    assert "操作 / 方案二" in recipe.raw_sections
