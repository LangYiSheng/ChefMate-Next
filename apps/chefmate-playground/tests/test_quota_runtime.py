"""Rolling request bounds, unknown reports, and centrally accounted probes."""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, ToolMessage

from chefmate_agent.boundary import _AgentFailure
from chefmate_playground.accounts import AccountsRepository
from chefmate_playground.runtime import ActiveTurn
from chefmate_playground.tenancy import AccountUsage
from test_tenancy import PASSWORD, build, login


def policy(tmp_path, limit=2700):
    accounts = AccountsRepository(tmp_path / "accounts.db")
    admin = accounts.bootstrap_admin("owner", PASSWORD)
    user = accounts.register("limited", PASSWORD)
    accounts.update_pricing(admin["id"], {"input_per_million": "1", "output_per_million": "1", "cached_input_per_million": "1"})
    accounts.update_user(admin["id"], user["id"], {"cost_limit_micros": limit})
    context = SimpleNamespace(agent=SimpleNamespace(config=SimpleNamespace(max_output_tokens=128, max_model_retries=0)))
    return accounts, user, AccountUsage(accounts, user["id"], context)


def observed(round_number=1, input_tokens=25, output_tokens=5):
    return {"calls": [{"round": round_number, "input_tokens": input_tokens, "output_tokens": output_tokens,
                        "total_tokens": input_tokens + output_tokens}], "attempted_calls": round_number}


def test_reported_round_releases_unused_headroom_for_followup_tool_round(tmp_path):
    accounts, user, meter = policy(tmp_path)
    turn = ActiveTurn("turn-one", "session", "request", "hello")
    meter.begin(turn)
    meter.before_model(turn, {"round": 1, "utf8_bytes": 20})
    assert accounts.get_user(user["id"])["quota"]["reserved_tokens"] == 2196
    first = observed()
    meter.observe(turn, first)
    assert accounts.get_user(user["id"])["quota"]["reserved_tokens"] == 30
    meter.before_model(turn, {"round": 2, "utf8_bytes": 20})
    assert accounts.get_user(user["id"])["quota"]["reserved_tokens"] == 2226
    combined = {"calls": first["calls"] + observed(2, 35, 5)["calls"], "attempted_calls": 2}
    meter.observe(turn, combined)
    meter.finish(turn, combined, model="fixture", status="completed")
    quota = accounts.get_user(user["id"])["quota"]
    assert quota["used_tokens"] == 70 and quota["reserved_tokens"] == 0


def test_missing_output_does_not_release_unknown_bound_or_allow_next_round(tmp_path):
    accounts, user, meter = policy(tmp_path)
    turn = ActiveTurn("unknown-turn", "session", "request", "hello")
    meter.begin(turn)
    meter.before_model(turn, {"round": 1, "utf8_bytes": 20})
    usage = {"calls": [{"round": 1, "input_tokens": 25}], "attempted_calls": 1}
    meter.observe(turn, usage)
    assert accounts.get_user(user["id"])["quota"]["reserved_tokens"] == 2196
    with pytest.raises(_AgentFailure, match="额度"):
        meter.before_model(turn, {"round": 2, "utf8_bytes": 20})
    meter.finish(turn, usage, model="fixture", status="error")
    quota = accounts.get_user(user["id"])["quota"]
    assert quota["used_tokens"] == 0 and quota["estimated_tokens"] == 2196


def test_parallel_turn_still_competes_with_released_headroom(tmp_path):
    accounts, user, meter = policy(tmp_path)
    first = ActiveTurn("first", "session-1", "request-1", "hello")
    second = ActiveTurn("second", "session-2", "request-2", "hello")
    meter.begin(first)
    meter.before_model(first, {"round": 1, "utf8_bytes": 20})
    meter.observe(first, observed())
    meter.begin(second)
    meter.before_model(second, {"round": 1, "utf8_bytes": 20})
    with pytest.raises(_AgentFailure, match="额度"):
        meter.before_model(first, {"round": 2, "utf8_bytes": 20})
    assert accounts.get_user(user["id"])["quota"]["reserved_tokens"] == 2226
    meter.abort(first)
    meter.abort(second)


def test_model_connection_probe_is_counted_and_never_exposes_key(tmp_path, monkeypatch):
    class FakeModel:
        def __init__(self, **kwargs):
            assert kwargs["max_tokens"] == 256

        def bind_tools(self, *args, **kwargs):
            return self

        async def ainvoke(self, messages):
            if isinstance(messages[-1], ToolMessage):
                return AIMessage(content="OK", usage_metadata={"input_tokens": 7, "output_tokens": 2, "total_tokens": 9})
            return AIMessage(content="", tool_calls=[{"name": "connection_echo", "args": {"value": "chefmate"}, "id": "echo-1"}],
                usage_metadata={"input_tokens": 11, "output_tokens": 4, "total_tokens": 15})

    monkeypatch.setattr("chefmate_agent.provider.IntegrityChatOpenAI", FakeModel)
    with TestClient(build(tmp_path)) as client:
        login(client)
        client.patch("/api/admin/pricing", json={"input_per_million": "2", "output_per_million": "4"})
        response = client.post("/api/admin/settings/test-model", json={"model": "probe-fixture",
            "base_url": "https://fixture.invalid/v1", "api_key": "must-not-be-echoed"})
        assert response.status_code == 200 and response.json()["ok"]
        assert "must-not" not in response.text
        report = client.get("/api/admin/usage").json()["summary"]
        assert report["turns"] == 1 and report["total_tokens"] == 24 and report["cost_micros"] == 60
        assert report["estimated_tokens"] == 0


@pytest.mark.parametrize(("attempts", "estimated"), [(1, 0), (2, 2196), (None, 4392)])
def test_sdk_retry_counts_release_only_proven_unused_retry_capacity(tmp_path, attempts, estimated):
    accounts, user, meter = policy(tmp_path, limit=10000)
    meter.context.agent.config.max_model_retries = 2
    turn = ActiveTurn("retry-turn", "session", "request", "hello")
    meter.begin(turn)
    meter.before_model(turn, {"round": 1, "utf8_bytes": 20})
    assert accounts.get_user(user["id"])["quota"]["reserved_tokens"] == 6588
    usage = observed()
    usage["calls"][0]["provider_attempts"] = attempts
    meter.observe(turn, usage)
    assert accounts.get_user(user["id"])["quota"]["reserved_tokens"] == 30 + estimated
    meter.finish(turn, usage, model="fixture", status="completed")
    quota = accounts.get_user(user["id"])["quota"]
    assert quota["used_tokens"] == 30 and quota["estimated_tokens"] == estimated


def test_late_sdk_attempt_metadata_can_refine_unknown_bound_without_losing_tokens(tmp_path):
    accounts, user, meter = policy(tmp_path, limit=10000)
    meter.context.agent.config.max_model_retries = 2
    turn = ActiveTurn("late-retry-turn", "session", "request", "hello")
    meter.begin(turn)
    meter.before_model(turn, {"round": 1, "utf8_bytes": 20})
    usage = observed()
    meter.observe(turn, usage)
    assert accounts.get_user(user["id"])["quota"]["reserved_tokens"] == 4422
    usage["calls"][0]["provider_attempts"] = 1
    meter.observe(turn, usage)
    assert accounts.get_user(user["id"])["quota"]["reserved_tokens"] == 30
    meter.observe(turn, {"calls": [{"round": 1}], "attempted_calls": 1})
    assert accounts.get_user(user["id"])["quota"]["reserved_tokens"] == 30
    meter.finish(turn, usage, model="fixture", status="completed")
