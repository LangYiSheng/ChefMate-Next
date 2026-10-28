"""Explicit account consent and independent long-term kitchen profile ownership."""

from fastapi.testclient import TestClient

from chefmate_playground.memory import MemoryRepository
from test_tenancy import MeteredAgent, PROFILE, build, register


KITCHEN = {"display_name": "厨房小杏", "cooking_level": "beginner", "household_size": 2, "preferences": ["清淡"]}


def test_profile_sync_preserves_notes_summary_and_user_clear(tmp_path):
    memory = MemoryRepository(tmp_path / "memory.db")
    notes = memory.update_user("我手写的偏好：不吃香菜", 0)
    summary = memory.update_summary("助手总结：有电饭煲", notes["revision"])
    saved = memory.sync_kitchen_profile(KITCHEN)
    assert saved["user_notes"] == notes["user_notes"]
    assert saved["summary"] == summary["summary"]
    assert saved["kitchen_profile"] == KITCHEN
    assert memory.context()["kitchen_profile"] == KITCHEN
    unchanged = memory.sync_kitchen_profile(KITCHEN)
    assert unchanged["revision"] == saved["revision"]
    cleared = memory.clear(saved["revision"])
    assert cleared["kitchen_profile"] == {}
    assert memory.sync_kitchen_profile(KITCHEN)["kitchen_profile"] == {}
    assert memory.sync_kitchen_profile({**KITCHEN, "household_size": 3})["kitchen_profile"]["household_size"] == 3
    assert memory.sync_kitchen_profile({})["kitchen_profile"] == {}


def test_memory_disable_removes_profile_from_model_context(tmp_path):
    memory = MemoryRepository(tmp_path / "memory.db")
    saved = memory.sync_kitchen_profile(KITCHEN)
    memory.update_user("不吃香菜", saved["revision"], enabled=False)
    assert memory.snapshot()["kitchen_profile"] == KITCHEN
    assert memory.context() == {}


def test_http_consent_controls_actual_attached_context_and_clear_does_not_resurrect(tmp_path):
    class RecordingAgent(MeteredAgent):
        def __init__(self):
            self.contexts = []
            self.identities = []

        async def run_turn(self, messages, *, on_event, **kwargs):
            self.contexts.append(kwargs.get("memory_context") or {})
            self.identities.append(kwargs.get("user_identity") or {})
            return await super().run_turn(messages, on_event=on_event, **kwargs)

    agent = RecordingAgent()
    app = build(tmp_path, agent=agent)
    with TestClient(app) as client:
        register(client, "profile-consent")
        def send(message):
            session = client.post("/api/sessions").json()
            result = client.post(f"/api/sessions/{session['id']}/turn", json={"message": message})
            assert result.status_code == 200, result.text
            assert '"status": "completed"' in result.text
        send("初始不授权")
        assert not agent.contexts[-1].get("kitchen_profile")
        assert agent.identities[-1]["display_name"] == PROFILE["display_name"]
        assert client.patch("/api/auth/onboarding", json={**PROFILE, "use_for_ai": True}).status_code == 200
        send("明确开启个性化")
        assert agent.contexts[-1]["kitchen_profile"]["household_size"] == PROFILE["household_size"]
        assert agent.identities[-1]["display_name"] == PROFILE["display_name"]
        saved = client.get("/api/memory").json()
        assert client.delete("/api/memory", params={"revision": saved["revision"]}).status_code == 200
        send("清空以后")
        assert not agent.contexts[-1].get("kitchen_profile")
        assert client.get("/api/memory").json()["kitchen_profile"] == {}
        assert client.patch("/api/auth/onboarding", json={**PROFILE, "use_for_ai": False}).status_code == 200
        assert client.patch("/api/auth/onboarding", json={**PROFILE, "use_for_ai": True}).status_code == 200
        # The intermediate opt-out is processed in the account route; the next
        # workspace read must see the last explicitly accepted revision.
        send("重新明确开启")
        assert agent.contexts[-1]["kitchen_profile"]["household_size"] == PROFILE["household_size"]
        assert agent.identities[-1]["display_name"] == PROFILE["display_name"]


def test_profile_shape_is_bounded_and_cannot_set_agent_configuration(tmp_path):
    memory = MemoryRepository(tmp_path / "memory.db")
    import pytest
    with pytest.raises(ValueError):
        memory.sync_kitchen_profile({"api_key": "not-allowed"})
    with pytest.raises(ValueError):
        memory.sync_kitchen_profile({"display_name": "a" * 2001})
