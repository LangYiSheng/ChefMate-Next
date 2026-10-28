"""Explicit durations are complete user/source expressions, never fragments."""

import pytest

from chefmate_workflow.support import _explicit_durations
import test_workflow
from test_workflow import prepare, tool

kitchen = test_workflow.kitchen


@pytest.mark.parametrize("text,seconds", [
    ("计时1小时30分钟", 5400),
    ("计时1分30秒", 90),
    ("小火煮一小时三十分钟", 5400),
    ("计时一个半小时", 5400),
    ("计时半个小时", 1800),
    ("焖一分半", 90),
    ("wait 1 hour and 30 minutes", 5400),
    ("小火煮1小时30分钟5秒", 5405),
    ("计时0.5分钟", 30),
    ("定时24小时", 86400),
])
def test_a_compound_duration_is_one_complete_duration(text, seconds):
    assert _explicit_durations(text) == {seconds}


@pytest.mark.parametrize("text", ["煮3-5分钟", "煮1小时到2小时", "煮三至五分钟", "计时-5分钟", "计时负五分钟", "煮到五分熟", "焖25小时30分钟", "焖一百小时30分钟", "计时0.5秒"])
def test_ranges_negative_durations_and_doneness_are_not_exact_timers(text):
    assert _explicit_durations(text) == set()


@pytest.mark.parametrize("text", [
    "小火煮1小时～2小时", "小火煮1～2小时", "小火煮1小时〜2小时", "焖1－2小时",
    "焖1小时——2小时", "焖1小时至2小时之间", "焖一到两小时", "焖两三分钟",
    "计时1分～2分", "计时1分到2分", "焖一个半小时～两个小时", "焖1小时半至2小时",
    "焖半小时～1小时", "煮1分30秒～2分钟",
])
def test_chinese_range_markers_never_authorize_either_endpoint(text):
    assert _explicit_durations(text) == set()


@pytest.mark.parametrize("text", [
    "计时-一个半小时", "计时负半个小时", "计时-1个小时", "计时−一个半小时",
    "计时－1个小时", "计时−1分30秒", "计时负 1 个半小时", "煮1小时−2小时",
])
def test_negative_compound_durations_never_become_positive_fragments(text):
    assert _explicit_durations(text) == set()


def test_a_range_cannot_become_an_exact_user_timer_or_recipe_step_timer(kitchen):
    service, _, retrieval, _ = kitchen
    retrieval.recipes["r1"]["steps"] = ["小火焖1小时～2小时。", "盛盘"]
    prepare(kitchen)
    guide = next(view for view in service.get_workspace()["views"] if view["kind"] == "cooking_guide")
    assert guide["data"]["steps"][0]["timer_seconds"] is None
    for seconds in (3600, 7200):
        request = {"seconds": seconds, "label": "不可擅选端点"}
        assert tool(kitchen, "set_cooking_timer", request, user_text="帮我计时1小时～2小时")["status"] == "unverified_duration"
        assert tool(kitchen, "set_cooking_timer", {**request, "basis": "recipe_text", "recipe_id": "r1", "step_id": "r1:step:0"})["status"] == "unverified_duration"


def test_distinct_instructions_remain_distinct_durations():
    assert _explicit_durations("先炒1分钟，再焖30秒") == {60, 30}


def test_user_timer_accepts_the_whole_duration_and_refuses_a_fragment(kitchen):
    prepare(kitchen)
    request = {"seconds": 90, "label": "用户明确计时"}
    assert tool(kitchen, "set_cooking_timer", request, user_text="帮我计时1分30秒")["status"] == "ok"
    assert tool(kitchen, "set_cooking_timer", {**request, "seconds": 30}, user_text="帮我计时1分30秒")["status"] == "unverified_duration"


def test_source_step_projects_and_verifies_one_compound_timer(kitchen):
    service, _, retrieval, _ = kitchen
    retrieval.recipes["r1"]["steps"] = ["小火煮1小时30分钟。", "盛盘"]
    prepare(kitchen)
    guide = next(view for view in service.get_workspace()["views"] if view["kind"] == "cooking_guide")
    assert guide["data"]["steps"][0]["timer_seconds"] == 5400
    request = {"recipe_id": "r1", "step_id": "r1:step:0", "seconds": 5400, "label": "原步骤计时", "basis": "recipe_text"}
    assert tool(kitchen, "set_cooking_timer", request)["status"] == "ok"
    assert tool(kitchen, "set_cooking_timer", {**request, "seconds": 3600})["status"] == "unverified_duration"
