"""Run explicit real-model kitchen journeys against an isolated application database.

Uses the configured model and read-only base recipe service. Session, cookbook,
journal, memory and settings writes go only into the output directory. No keys,
connection strings or model request headers are written to the report.
"""

import argparse
import json
import sys
import threading
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from chefmate_playground import PlaygroundConfig, create_app
from fastapi.testclient import TestClient

SCENARIOS = {
    "inventory": [
        "我想炒个菜晚上吃，我家里有西红柿土豆金针菇牛肉，别的菜可以买，家里有烤箱炖锅炒锅。",
        "优先用我家里的材料，不要求一道菜包含全部材料。请推荐两道合适的家常炒菜。",
        "就做你推荐的第一道，1人吃。直接列出备料清单，家里现有食材标已备齐，其他材料标待购买，不用先问我。",
    ],
    "authoring": [
        "我想做一道番茄土豆金针菇炒牛肉。请创作个人菜谱草稿，供1人，难度、整道菜总耗时和食材用量由你给合理建议并标明是建议，先不要发布。",
        "食材份量也补全建议。难度份数总耗时也要更新一下。",
        "按建议：普通、1人份、30分钟。请更新同一份草稿。",
        "确认这三个值：难度普通，总耗时30分钟，1人份。不要再让我换一句话重复确认。",
        "这份菜谱我核对过了，请发起保存确认。",
        "@publish",
        "我要做刚才保存的菜，1人吃。直接进入备菜：番茄、金针菇和所有调味料已备齐，牛肉和土豆还没买。记录到清单，不用再逐项提问。",
    ],
    "cooking": [
        "我一个人做西红柿鸡蛋汤，所有食材都已备齐。请查询真实菜谱、安排一人餐单，直接进入烹饪，从第一步开始，不要启动计时。",
        "下一步吧。",
        "上一步。",
        "我已经把当前这一步做完了，帮我标记完成，再切换下一步。",
    ],
    "specific": [
        "我想做地三鲜。",
        "1个人吃，直接列备料。茄子、土豆、青椒已经有了，别的先标未知，不要替我假设调味料齐。",
    ],
    "confirmation": [
        "请创作番茄炒鸡蛋的个人菜谱草稿，食材配比可以给建议。难度、份数和整道菜总耗时先留空，再用一个确认题让我选择；建议选项的完整标签写成‘按建议：普通、1人份、30分钟’。先不要保存发布。",
        "按建议：普通、1人份、30分钟",
    ],
    "food": [
        "今天我喝完了一瓶净含量1升的冰糖雪梨饮品，没有营养标签。请估算热量和碳水，明确标为估算，其他不知道的营养留空，先建饮食记录草稿。",
        "整瓶都喝完了，热量和碳水先按建议估算，确认保存这份饮食记录。",
        "看看最近7天的饮食统计，只统计已确认记录，说明哪些是估算、哪些营养未知。",
    ],
}
PRINT_LOCK = threading.Lock()


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def log(value):
    with PRINT_LOCK:
        print(json.dumps(value, ensure_ascii=False), flush=True)


def snapshot(client, session_id):
    value = client.get(f"/api/sessions/{session_id}/workspace").json()
    return value, value.get("ui_snapshot") or value.get("workspace") or {}


def answer_pending(client, session_id, text):
    _, state = snapshot(client, session_id)
    question = state.get("pending_question")
    if not question:
        if text == "@publish":
            return "确认保存刚才核对的当前版本，请保存。", None
        return text, None
    if text == "@publish":
        chosen = next((item for item in question.get("options", []) if item["id"] == "publish"), None)
        if not chosen:
            raise ValueError("No revision-bound publication question is available")
        option_ids, answer, continuation = [chosen["id"]], "", chosen["label"]
    else:
        chosen = next((item for item in question.get("options", []) if item["label"] == text), None)
        if not chosen and not question.get("allow_custom"):
            raise ValueError("Scenario needs a custom answer but current question disallows it")
        option_ids, answer, continuation = ([chosen["id"]], "", chosen["label"]) if chosen else ([], text, text)
    action = {"action_id": str(uuid4()), "base_revision": state["revision"], "type": "answer_question",
              "payload": {"question_id": question["id"], "option_ids": option_ids, "text": answer}}
    response = client.post(f"/api/sessions/{session_id}/actions", json=action)
    response.raise_for_status()
    receipt = response.json()
    if receipt.get("status") != "ok":
        raise ValueError("Question answer was not accepted")
    return f'关于“{question["question"]}”，我的回答是：{continuation}。请按这个答案继续。', action


def summarize(session, state):
    message = next((item for item in reversed(session["messages"]) if item["role"] == "assistant"), {})
    calls = message.get("tool_calls") or []
    return {"text": message.get("content", ""), "stage": state.get("stage"),
            "pending_question": (state.get("pending_question") or {}).get("question"),
            "active_step_id": state.get("active_step_id"),
            "tools": [{"name": call.get("name") or call.get("tool_name"),
                       "status": (call.get("result") or {}).get("status"),
                       "errors": (call.get("result") or {}).get("errors"),
                       "message": (call.get("result") or {}).get("message")} for call in calls]}


def run_scenario(name, config, root):
    case_root = root / name
    case_root.mkdir(parents=True, exist_ok=False)
    app = create_app(replace(config, sessions_database=case_root / "sessions.db"))
    report = {"scenario": name, "model": config.model, "turns": [], "checks": {}}
    try:
        with TestClient(app) as client:
            status = client.get("/api/status").json()
            if not status.get("ready"):
                raise ValueError("Isolated application is not ready")
            session_id = client.post("/api/sessions").json()["id"]
            report["session_id"] = session_id
            for index, prompt in enumerate(SCENARIOS[name], start=1):
                message, answer = answer_pending(client, session_id, prompt)
                started = time.monotonic()
                log({"scenario": name, "turn": index, "state": "running", "prompt": prompt})
                response = client.post(f"/api/sessions/{session_id}/turn",
                                       json={"message": message, "request_id": str(uuid4())})
                response.raise_for_status()
                events = [json.loads(line[5:].strip()) for line in response.text.splitlines() if line.startswith("data:")]
                session = client.get(f"/api/sessions/{session_id}").json()
                workspace, state = snapshot(client, session_id)
                summary = summarize(session, state)
                item = {"turn": index, "prompt": prompt, "answer_action": answer,
                        "seconds": round(time.monotonic() - started, 2), "summary": summary,
                        "terminal_status": next((event.get("status") for event in reversed(events) if event.get("type") == "done"), None)}
                report["turns"].append(item)
                write_json(case_root / f"turn-{index:02d}.json", {"events": events, "session": session, "workspace": workspace})
                write_json(case_root / "report.json", report)
                log({"scenario": name, **item})
                if item["terminal_status"] != "completed":
                    break
            turns = report["turns"]
            completed = len(turns) == len(SCENARIOS[name]) and all(turn["terminal_status"] == "completed" for turn in turns)
            report["checks"]["all_turns_completed"] = completed
            report["checks"]["no_tool_validation_errors"] = not any(
                call["status"] in {"invalid_arguments", "invalid_selection", "invalid_recipe_provenance", "unverified_reference"}
                for turn in turns for call in turn["summary"]["tools"])
            report["checks"]["no_rejected_tool_calls"] = all(
                call["status"] in {"ok", "duplicate_query"}
                for turn in turns for call in turn["summary"]["tools"])
            if name == "confirmation":
                calls = [call for message in session["messages"] for call in message.get("tool_calls", [])
                         if call.get("name") == "revise_recipe_draft" and (call.get("result") or {}).get("status") == "ok"]
                record = (calls[-1].get("result") or {}).get("draft", {}) if calls else {}
                recipe, basis = record.get("document", {}), record.get("metadata_provenance") or {}
                report["checks"]["grouped_answer_applied"] = recipe.get("difficulty") == "normal" and recipe.get("servings") == 1 and recipe.get("total_time_min") == 30
                report["checks"]["host_answer_bound"] = len(turns) == 2 and bool(turns[1]["answer_action"]) and all(
                    (basis.get(field) or {}).get("question_id") == turns[1]["answer_action"]["payload"]["question_id"]
                    for field in ("difficulty", "servings", "total_time_min"))
                report["checks"]["no_repeat_question"] = completed and not turns[-1]["summary"]["pending_question"]
            if name == "inventory":
                report["checks"]["preparation_opened"] = bool(turns and turns[-1]["summary"]["stage"] == "prep")
                report["checks"]["one_diner_recorded"] = state.get("meal_diners") == 1
            if name == "specific":
                first = json.loads((case_root / "turn-01.json").read_text(encoding="utf-8"))
                first_state = first["workspace"].get("ui_snapshot") or first["workspace"].get("workspace") or {}
                report["checks"]["explicit_dish_selected"] = bool(first_state.get("selected_recipe_ids"))
                report["checks"]["preparation_opened"] = bool(turns and turns[-1]["summary"]["stage"] == "prep")
                report["checks"]["one_diner_recorded"] = state.get("meal_diners") == 1
                prep = next((view for view in workspace.get("views", []) if view["kind"] == "prep_checklist"), {})
                report["checks"]["only_confirmed_ingredients_ready"] = all(
                    item.get("status") != "ready" or item.get("canonical_name") in {"茄子", "土豆", "青椒"}
                    for item in prep.get("data", {}).get("items", []))
            if name == "authoring":
                def revised_record(index):
                    saved_turn = json.loads((case_root / f"turn-{index:02d}.json").read_text(encoding="utf-8"))
                    calls = [call for message in saved_turn["session"]["messages"] for call in message.get("tool_calls", [])
                             if call.get("name") == "revise_recipe_draft" and (call.get("result") or {}).get("status") == "ok"]
                    return (calls[-1].get("result") or {}).get("draft", {}) if calls else {}

                if len(turns) >= 3:
                    before, after = revised_record(2), revised_record(3)
                    document = after.get("document", {})
                    report["checks"]["confirmed_metadata_saved"] = document.get("servings") == 1 and document.get("difficulty") == "normal" and document.get("total_time_min") == 30
                    report["checks"]["only_requested_metadata_changed"] = bool(before and after) and all(
                        before.get("document", {}).get(field) == after.get("document", {}).get(field)
                        for field in ("prep_time_min", "cook_time_min", "ingredients", "steps")) and before.get("source_text") == after.get("source_text")
                report["checks"]["bound_recipe_published"] = any(
                    call["name"] == "confirm_recipe_publication" and call["status"] == "ok"
                    for turn in turns for call in turn["summary"]["tools"])
                report["checks"]["preparation_opened"] = bool(turns and turns[-1]["summary"]["stage"] == "prep")
                prep = next((view for view in workspace.get("views", []) if view["kind"] == "prep_checklist"), {})
                items = prep.get("data", {}).get("items", [])
                report["checks"]["missing_food_preserved"] = any(item.get("availability") == "missing" for item in items)
                report["checks"]["known_food_recorded"] = any(item.get("status") == "ready" for item in items)
            if name == "food":
                journal = client.get("/api/journal?days=7").json()
                write_json(case_root / "journal.json", journal)
                saved = journal.get("food_logs") or {}
                saved = saved.get("items", []) if isinstance(saved, dict) else saved
                report["checks"]["food_record_published"] = len(saved) == 1
                if len(saved) == 1:
                    item = saved[0]["document"]["items"][0]
                    report["checks"]["estimated_nutrition_preserved"] = item.get("nutrition_basis") == "estimated"
                    report["checks"]["unknown_nutrition_preserved"] = all(
                        item.get("nutrition", {}).get(field) is None
                        for field in ("protein_g", "fat_g", "sugar_g", "sodium_mg"))
            if name == "cooking" and len(turns) >= 3:
                positions = [turn["summary"]["active_step_id"] for turn in turns]
                report["checks"]["next_previous_roundtrip"] = positions[0] != positions[1] and positions[0] == positions[2]
                if completed:
                    guide = next((view for view in workspace.get("views", []) if view["kind"] == "cooking_guide"), {})
                    steps = guide.get("data", {}).get("steps", [])
                    report["checks"]["spoken_completion_recorded"] = bool(steps and steps[0]["status"] == "done"
                        and sum(step["status"] == "done" for step in steps) == 1 and positions[-1] == positions[1])
                    navigation = json.loads((case_root / "turn-02.json").read_text(encoding="utf-8"))
                    navigation_guide = next((view for view in navigation["workspace"].get("views", []) if view["kind"] == "cooking_guide"), {})
                    report["checks"]["navigation_did_not_complete_step"] = not any(
                        step["status"] == "done" for step in navigation_guide.get("data", {}).get("steps", []))
            report["passed"] = all(report["checks"].values())
    except Exception as error:  # noqa: BLE001 -- one failed journey must not discard the other live reports
        report["error"] = f"{type(error).__name__}: {error}"[:500]
        report["passed"] = False
        log({"scenario": name, "error": report["error"]})
    write_json(case_root / "report.json", report)
    log({"scenario": name, "passed": report["passed"], "checks": report["checks"]})
    return report


def run_isolated_scenario(arguments):
    # Provider clients may cache asynchronous connection pools globally. Each
    # TestClient owns one event loop; a fresh process prevents cross-loop reuse.
    name, root = arguments
    sys.stdout.reconfigure(encoding="utf-8")
    return run_scenario(name, PlaygroundConfig.load(), root)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=[*SCENARIOS, "all"], default="all")
    parser.add_argument("--output", type=Path, required=True, help="New directory for isolated test state and evidence")
    parser.add_argument("--workers", type=int, choices=[1, 2], default=1)
    arguments = parser.parse_args()
    root = arguments.output.resolve()
    root.mkdir(parents=True, exist_ok=False)
    config = PlaygroundConfig.load()
    if config.sessions_database.resolve().is_relative_to(root) or root == config.sessions_database.parent.resolve():
        parser.error("Output must be isolated from the normal application database")
    names = list(SCENARIOS) if arguments.case == "all" else [arguments.case]
    log({"started": datetime.now().astimezone().isoformat(), "model": config.model, "cases": names})
    with ProcessPoolExecutor(max_workers=arguments.workers, max_tasks_per_child=1) as pool:
        reports = list(pool.map(run_isolated_scenario, [(name, root) for name in names]))
    summary = {"model": config.model, "scenarios": [{key: value for key, value in report.items() if key != "turns"} for report in reports],
               "turns": sum(len(report["turns"]) for report in reports), "passed": all(report["passed"] for report in reports)}
    write_json(root / "summary.json", summary)
    log(summary)
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
