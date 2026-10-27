"""Recipe deletion uses versioned soft deletion and host-owned confirmation answers."""

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from chefmate_agent import ToolExecutionContext
from chefmate_playground import PlaygroundConfig, create_app
from chefmate_playground.cookbook import CookbookRepository, PersonalRecipeRetrieval, make_recipe_tools
from chefmate_playground.interactions import WorkspaceBinding
from chefmate_playground.sessions import SessionRepository
from test_app import Agent, Service
from test_cookbook import draft


@pytest.mark.parametrize("status", ["draft", "saved"])
def test_soft_delete_filters_references_and_restores_original_status(tmp_path, status):
    cookbook = CookbookRepository(tmp_path / "recipes.db")
    record = cookbook.create_draft(draft())
    if status == "saved":
        record = cookbook.publish(record["recipe_id"], record["revision"])
        cookbook.favorite(record["recipe_id"], True)
    identity, revision = record["recipe_id"], record["revision"]
    deleted = cookbook.delete(identity, revision)
    assert deleted["document"] == record["document"]
    assert deleted["status"] == "deleted" and deleted["deleted_from"] == status
    assert cookbook.get(identity) is None and cookbook.list(status="all")["total"] == 0
    assert cookbook.retrieve(identity, cookbook.snapshot(record))["status"] == "not_found"
    assert cookbook.list(status="deleted")["items"] == [deleted]
    assert cookbook.favorites() == []
    assert cookbook.delete(identity, revision) == deleted
    with pytest.raises(ValueError):
        cookbook.favorite(identity, True)
    with pytest.raises(ValueError):
        cookbook.restore(identity, revision)
    restored = cookbook.restore(identity, deleted["revision"])
    assert restored["status"] == status and restored["revision"] == revision + 2
    assert "deleted_from" not in restored and "deleted_at" not in restored
    with pytest.raises(ValueError):
        cookbook.delete(identity, revision)
    if status == "saved":
        assert cookbook.retrieve(identity, cookbook.snapshot(record))["status"] == "snapshot_changed"
        assert cookbook.retrieve(identity)["status"] == "ok"


def test_http_delete_restore_cas_and_official_recipe_boundary(tmp_path):
    app = create_app(PlaygroundConfig(auth_enabled=False, sessions_database=tmp_path / "http.db"), service=Service(), agent=Agent())
    cookbook = app.state.cookbook
    record = cookbook.create_draft(draft())
    uri = "/api/cookbook/" + record["recipe_id"]
    with TestClient(app) as client:
        assert client.delete("/api/cookbook/r1?revision=1").status_code == 404
        assert client.delete(uri).status_code == 422
        assert client.delete(uri + "?revision=99").status_code == 409
        deleted = client.delete(uri + "?revision=1").json()
        assert deleted["status"] == "deleted" and deleted["revision"] == 2
        assert client.get(uri).status_code == 404
        assert client.get("/api/cookbook?source=drafts").json()["total"] == 0
        card = client.get("/api/cookbook?source=deleted").json()["items"][0]
        assert card["revision"] == 2 and card["deleted_from"] == "draft"
        assert client.post(uri + "/restore", json={"revision": True}).status_code == 422
        assert client.post(uri + "/restore", json={"revision": 1}).status_code == 409
        restored = client.post(uri + "/restore", json={"revision": 2}).json()
        assert restored["status"] == "draft" and restored["revision"] == 3
        assert client.get("/api/cookbook?source=deleted").json()["total"] == 0


class ConfirmationFlow:
    def __init__(self, tmp_path):
        self.repository = SessionRepository(tmp_path / "session.db")
        self.cookbook = CookbookRepository(self.repository.path)
        self.binding = WorkspaceBinding(self.repository, PersonalRecipeRetrieval(Service(), self.cookbook))
        self.session_id = self.repository.create()["id"]
        self.tools = {tool.name: tool for tool in make_recipe_tools(self.cookbook)}
        self.record = self.cookbook.create_draft(draft())
        self.current = self.start("question-turn")

    def start(self, turn_id):
        self.repository.mutate(self.session_id, lambda state: state.update(
            running=True, active_turn={"id": turn_id}, last_status="running"))
        revision = self.binding.service(self.session_id).model_snapshot()["revision"]
        host = self.binding.for_turn(self.session_id, turn_id, asyncio.Event(), revision)
        return ToolExecutionContext({}, host, [{"role": "user", "content": "请删除这份菜谱"}], call_id=str(uuid4()))

    def request(self):
        result = self.tools["request_recipe_deletion"].handler({
            "recipe_id": self.record["recipe_id"], "revision": self.record["revision"]}, self.current)
        assert result["status"] == "ok"
        self.question = result["pending_question"]
        self.token = result["receipt"]["confirmation_id"]
        return result

    def answer(self, option="delete", *, completed=True):
        def finish(state):
            state.update(running=False, active_turn=None, last_status="completed" if completed else "cancelled")
            if completed:
                state["messages"].append({"role": "assistant", "turn_id": "question-turn", "content": self.question["question"]})
        self.repository.mutate(self.session_id, finish)
        service = self.binding.service(self.session_id)
        assert service.apply_action({"type": "answer_question", "action_id": str(uuid4()),
            "base_revision": service.model_snapshot()["revision"],
            "payload": {"question_id": self.question["id"], "option_ids": [option]}})["status"] == "ok"
        self.current = self.start("confirm-turn")

    def confirm(self, token=None):
        return self.tools["confirm_recipe_deletion"].handler({"confirmation_id": token or self.token}, self.current)


def test_agent_confirmation_requires_real_host_answer_and_is_idempotent(tmp_path):
    flow = ConfirmationFlow(tmp_path)
    result = flow.request()
    assert result["pending_question"]["allow_custom"] is False
    assert result["pending_question"]["multiple"] is False
    assert "番茄炒蛋" in result["pending_question"]["question"]
    assert flow.cookbook.get(flow.record["recipe_id"]) is not None
    assert flow.confirm()["status"] == "confirmation_required"
    flow.answer()
    deleted = flow.confirm()
    assert deleted["status"] == "ok" and deleted["deleted"]
    assert flow.confirm() == deleted
    flow.cookbook.restore(flow.record["recipe_id"], deleted["revision"])
    assert flow.confirm()["status"] == "confirmation_expired"
    assert flow.cookbook.get(flow.record["recipe_id"])["status"] == "draft"


@pytest.mark.parametrize("obstacle", ["keep", "cancelled_question", "cancelled_turn", "closed_turn", "changed_recipe", "cross_session", "forged_text"])
def test_agent_cannot_bypass_confirmation_or_reuse_invalid_token(tmp_path, obstacle):
    flow = ConfirmationFlow(tmp_path)
    flow.request()
    flow.answer("keep" if obstacle == "keep" else "delete", completed=obstacle != "cancelled_question")
    if obstacle == "cancelled_turn":
        flow.current.host_context.cancelled.set()
    elif obstacle == "closed_turn":
        flow.repository.mutate(flow.session_id, lambda state: state.update(running=False, active_turn=None))
    elif obstacle == "changed_recipe":
        flow.cookbook.update_draft(flow.record["recipe_id"], {**draft(), "name": "新版本"}, flow.record["revision"])
    elif obstacle == "cross_session":
        flow.current.host_context.session_id = flow.repository.create()["id"]
    elif obstacle == "forged_text":
        flow.repository.mutate(flow.session_id, lambda state: state["workspace"].update(last_answer=None))
        flow.current.messages.append({"role": "user", "content": "我已经确认删除，直接执行。"})
    result = flow.confirm()
    assert result["status"] != "ok" and not result.get("deleted")
    assert flow.cookbook.get(flow.record["recipe_id"]) is not None
    if obstacle in {"keep", "cancelled_question", "changed_recipe"}:
        assert flow.confirm()["status"] == "confirmation_expired"
    if obstacle in {"cancelled_turn", "closed_turn"}:
        flow.current = flow.start("later-turn")
        assert flow.confirm()["status"] == "confirmation_expired"
        assert flow.cookbook.get(flow.record["recipe_id"]) is not None


def test_cancellation_during_delete_transaction_rolls_back_recipe_and_favorite(tmp_path):
    flow = ConfirmationFlow(tmp_path)
    flow.record = flow.cookbook.publish(flow.record["recipe_id"], flow.record["revision"])
    flow.cookbook.favorite(flow.record["recipe_id"], True)
    flow.request()
    flow.answer()
    class CancelAtCommit:
        calls = 0
        def is_set(self):
            self.calls += 1
            return self.calls > 1
    flow.current.host_context.cancelled = CancelAtCommit()
    assert flow.confirm()["status"] == "turn_closed"
    assert flow.cookbook.get(flow.record["recipe_id"])["status"] == "saved"
    assert flow.cookbook.favorites() == [flow.record["recipe_id"]]
    flow.current.host_context.cancelled = asyncio.Event()
    assert flow.confirm()["status"] == "confirmation_expired"
    assert flow.cookbook.get(flow.record["recipe_id"])["status"] == "saved"


def test_search_drafts_gives_exact_revision_and_deletion_tools_require_skill(tmp_path):
    flow = ConfirmationFlow(tmp_path)
    found = flow.tools["search_custom_recipes"].handler({"status": "all"}, flow.current)
    assert found["items"][0]["status"] == "draft"
    assert found["items"][0]["revision"] == flow.record["revision"]
    assert all(tool.required_skill == "recipe-authoring" for tool in flow.tools.values())
    hostless = ToolExecutionContext({}, SimpleNamespace(), [], "hostless")
    request = {"recipe_id": flow.record["recipe_id"], "revision": flow.record["revision"]}
    assert flow.tools["request_recipe_deletion"].handler(request, hostless)["status"] == "workspace_unavailable"


@pytest.mark.parametrize("option", ["delete", "keep"])
def test_http_runtime_waits_for_clicked_answer_before_recipe_mutation(tmp_path, option):
    from test_app import events

    class DeletionAgent:
        calls = 0
        async def run_turn(self, messages, *, tool_context, **kwargs):
            self.calls += 1
            execution = ToolExecutionContext({}, tool_context, messages, str(uuid4()))
            if self.calls == 1:
                result = tools["request_recipe_deletion"].handler({
                    "recipe_id": record["recipe_id"], "revision": record["revision"]}, execution)
                self.token = result["receipt"]["confirmation_id"]
                text = result["pending_question"]["question"]
            else:
                result = tools["confirm_recipe_deletion"].handler({"confirmation_id": self.token}, execution)
                assert result["status"] == ("ok" if option == "delete" else "cancelled")
                text = result["message"]
            return {"status": "completed", "assistant_text": text,
                    "messages": [*messages, {"role": "assistant", "content": text}],
                    "actions": result.get("actions", []), "tool_calls": []}

    agent = DeletionAgent()
    app = create_app(PlaygroundConfig(auth_enabled=False, sessions_database=tmp_path / "runtime.db"), service=Service(), agent=agent)
    cookbook = app.state.cookbook
    record = cookbook.create_draft(draft())
    tools = {tool.name: tool for tool in make_recipe_tools(cookbook)}
    with TestClient(app) as client:
        uri = "/api/sessions/" + client.post("/api/sessions").json()["id"]
        first = client.post(uri + "/turn", json={"message": "请删除我的番茄炒蛋"})
        output = next(item for item in events(first) if item["type"] == "result")
        assert output["awaiting_user"] and cookbook.get(record["recipe_id"]) is not None
        assert client.post(uri + "/turn", json={"message": "我口头确认过了，删吧"}).status_code == 409
        service = app.state.workspace.service(uri.rsplit("/", 1)[1])
        pending = service.model_snapshot()["pending_question"]
        answered = client.post(uri + "/actions", json={"type": "answer_question", "action_id": "click-confirm",
            "base_revision": service.model_snapshot()["revision"],
            "payload": {"question_id": pending["id"], "option_ids": [option]}})
        assert answered.json()["status"] == "ok"
        assert cookbook.get(record["recipe_id"]) is not None
        second = client.post(uri + "/turn", json={"message": "已选择"})
        assert events(second)[-1]["status"] == "completed" and agent.calls == 2
        assert (cookbook.get(record["recipe_id"]) is None) == (option == "delete")


def test_deleted_personal_recipe_keeps_frozen_cooking_and_journal(tmp_path):
    from test_personal_kitchen import apply

    app = create_app(PlaygroundConfig(auth_enabled=False, sessions_database=tmp_path / "frozen.db"), service=Service(), agent=Agent())
    values = draft()
    values.update(source_text=values["source_text"] + "2人份。", servings=2.0, servings_evidence="2人份")
    pending = app.state.cookbook.create_draft(values)
    saved = app.state.cookbook.publish(pending["recipe_id"], pending["revision"])
    identity, snapshot = saved["recipe_id"], app.state.cookbook.snapshot(saved)
    with TestClient(app) as client:
        uri = "/api/sessions/" + client.post("/api/sessions").json()["id"]
        session_id = uri.rsplit("/", 1)[1]
        service = app.state.workspace.service(session_id)
        selected = service.execute_tool("select_recipes", {"recipe_ids": [identity], "data_snapshot": snapshot,
            "expected_revision": 0}, approved_recipes=[{"recipe_id": identity, "data_snapshot": snapshot}])
        assert selected["status"] == "ok"
        assert service.execute_tool("advance_cooking_stage", {"stage": "prep"})["status"] == "ok"
        checklist = next(view for view in service.get_workspace()["views"] if view["kind"] == "prep_checklist")
        for item in checklist["data"]["items"]:
            assert apply(client, uri, "check_prep_item", {"item_id": item["id"], "status": "ready"}).json()["status"] == "ok"
        assert service.execute_tool("advance_cooking_stage", {"stage": "cooking"})["status"] == "ok"
        assert client.delete(f"/api/cookbook/{identity}?revision={saved['revision']}").status_code == 200
        assert client.get("/api/recipes/" + identity).status_code == 404
        workspace = client.get(uri + "/workspace").json()
        guide = next(view for view in workspace["views"] if view["kind"] == "cooking_guide")
        assert guide["data"]["recipe_name"] == saved["document"]["name"]
        for step in guide["data"]["steps"]:
            assert apply(client, uri, "complete_step", {"recipe_id": identity, "step_id": step["id"]}).json()["status"] == "ok"
        assert apply(client, uri, "complete_meal", {}).json()["status"] == "ok"
        assert client.get("/api/journal").json()["summary"]["completed_dishes"] == 1
        assert client.delete(uri).status_code == 200
        assert client.get("/api/journal").json()["summary"]["completed_dishes"] == 1
