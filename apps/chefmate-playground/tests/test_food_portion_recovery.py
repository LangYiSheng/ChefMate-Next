"""Repair exact portion quotes and volume/mass estimates without rewriting source evidence."""

import copy

from chefmate_agent.failure_policy import FailurePolicy
from chefmate_playground.journal import JournalRepository
from test_food_log_revision import execution, read_and_change
from test_meal_logging import NOW, context, photo, tool


def beverage():
    source = "今天我喝完了一瓶净含量1升的冰糖雪梨饮品，没有营养标签。请估算热量和碳水，其他不知道的营养留空。"
    return {"title": "冰糖雪梨饮品", "source_kind": "text", "source_text": source, "items": [
        {"name": "冰糖雪梨饮品", "portion": "1瓶（净含量1升）", "amount_g": 1000.0,
         "portion_basis": "user_provided", "nutrition": {"calories_kcal": 400.0, "carbohydrate_g": 100.0},
         "nutrition_basis": "estimated", "evidence": "无标签，按同类含糖饮品估算",
         "assumptions": ["每100毫升约40千卡和10克碳水；1升近似1000克"]}]}


def test_volume_record_returns_precise_field_repairs_and_can_keep_mass_unknown(tmp_path):
    journal = JournalRepository(tmp_path / "food.db", now=lambda: NOW)
    values = beverage()
    create = tool(journal)
    rejected = create.handler(values, context(values))
    assert rejected["status"] == "invalid_food_log" and rejected["code"] == "food_portion_not_quoted"
    assert rejected["errors"] == [{"loc": ["items", 0, "portion"], "type": "food_portion_not_quoted"}]
    portion_error = FailurePolicy().observe(create.name, values, rejected)
    assert "portion" in portion_error["errors"][0]["hint"] and "source_text" in portion_error["errors"][0]["hint"]
    assert "source_text" in portion_error["recovery"]["hint"]
    # The quoted portion is repaired without rewriting the original source.
    values["items"][0]["portion"] = "一瓶净含量1升"
    mass_error = create.handler(values, context(values))
    assert mass_error["code"] == "food_grams_not_provided"
    assert mass_error["errors"][0]["loc"] == ["items", 0, "amount_g"]
    projected = FailurePolicy().observe(create.name, values, mass_error)
    assert "amount_g" in projected["recovery"]["hint"] and "estimated" in projected["recovery"]["hint"]
    assert journal.food_logs.list(status="all")["total"] == 0
    values["items"][0]["amount_g"] = None
    values["items"][0]["assumptions"] = ["按1升为10份100毫升，每份约40千卡和10克碳水估算；没有推算质量"]
    saved = create.handler(values, context(values))
    assert saved["status"] == "ok"
    record = journal.food_logs.get(saved["draft"]["id"])
    assert record["document"]["source_text"] == beverage()["source_text"]
    assert record["document"]["items"][0]["amount_g"] is None
    assert record["nutrition"]["total"]["calories_kcal"] == 400
    assert record["nutrition"]["total"]["carbohydrate_g"] == 100
    assert record["nutrition"]["total"]["fat_g"] is None
    assert record["nutrition"]["estimated"] is True


def test_volume_to_mass_can_only_remain_an_explicit_estimate(tmp_path):
    journal = JournalRepository(tmp_path / "food.db", now=lambda: NOW)
    values = beverage()
    values["items"][0].update(portion="一瓶净含量1升", portion_basis="estimated",
                              assumptions=["假设饮品密度接近1克/毫升，1升近似1000克；营养按同类饮品估算"])
    saved = tool(journal).handler(values, context(values))
    assert saved["status"] == "ok"
    item = journal.food_logs.get(saved["draft"]["id"])["document"]["items"][0]
    assert item["amount_g"] == 1000 and item["portion_basis"] == "estimated"
    assert "密度" in item["assumptions"][0]


def test_repairing_portion_keeps_photo_source_and_image_references(tmp_path):
    journal = JournalRepository(tmp_path / "food.db", now=lambda: NOW)
    values = photo()
    values["source_text"] = "照片中可见一小碗米饭，碗的实际大小未知。"
    original = journal.food_logs.create_draft(values, session_id="photo-chat", image_ids=["original-photo"])
    user_text = "我实际吃了150克米饭。"
    available, request, read = read_and_change(journal, original, user_text)
    request["record"]["items"][0].update(portion="150 克", portion_basis="user_provided")
    rejected = available["revise_food_log_draft"].handler(copy.deepcopy(request), execution(user_text, read=read))
    assert rejected["code"] == "food_portion_not_quoted"
    assert rejected["errors"][0]["loc"] == ["record", "items", 0, "portion"]
    request["record"]["items"][0]["portion"] = "150克"
    result = available["revise_food_log_draft"].handler(request, execution(user_text, read=read))
    assert result["status"] == "ok"
    updated = journal.food_logs.get(original["id"])
    assert updated["document"]["source_text"] == values["source_text"]
    assert updated["document"]["source_kind"] == "image" and updated["source_image_ids"] == ["original-photo"]
