import json
import sqlite3
import zipfile

import pytest
import requests

from chefmate_core import RecipeProcessor, calculate, TagThresholds
from chefmate_core.datasets import download, import_datasets, sha256
from chefmate_core.fooddata import FoodDataError, LocalFoodData
from chefmate_core.nutrition import food_key, parse_nutrients
from data_cleaning.tests.test_conversion import document, AI


def test_import_count_nulls_idempotent_and_raw_detail(local_catalog):
    local, folder, datasets = local_catalog
    before = local.path.stat().st_mtime_ns
    result = import_datasets(folder, local.path, datasets=datasets)
    assert result["unchanged"]
    assert local.path.stat().st_mtime_ns == before
    assert result["count"] == 5
    assert result["datasets"][0]["skipped_nulls"] == 1
    assert local.food("123")["foodNutrients"][0]["amount"] == 100
    assert local.provenance("123")["sha256"] == sha256(folder / datasets[0].filename)


@pytest.mark.parametrize("failure", ["zip", "duplicate", "record", "count", "root"])
def test_failed_import_leaves_old_catalog(local_catalog, failure):
    local, folder, datasets = local_catalog
    original = sha256(local.path)
    source = folder / datasets[0].filename
    if failure == "zip":
        source.write_bytes(b"broken")
    else:
        with zipfile.ZipFile(source) as archive:
            payload = json.loads(archive.read("foods.json"))
        rows = payload[datasets[0].root]
        if failure == "duplicate":
            rows[1] = rows[0]
        elif failure == "record":
            rows[0]["fdcId"] = "bad"
        elif failure == "count":
            rows.pop()
        else:
            payload = {"WrongRoot": rows}
        with zipfile.ZipFile(source, "w") as archive:
            archive.writestr("foods.json", json.dumps(payload))
    with pytest.raises((ValueError, zipfile.BadZipFile, sqlite3.IntegrityError)):
        import_datasets(folder, local.path, datasets=datasets)
    assert sha256(local.path) == original
    assert local.status()["ready"]
    assert not list(local.path.parent.glob("usda-build-*.db"))


def test_rank_plural_state_id_and_punctuation(local_catalog):
    local, _, _ = local_catalog
    assert local.search("potatoes raw")[0]["fdcId"] == 123
    assert local.search("peeled potato raw")[0]["fdcId"] == 123
    assert local.search("chicken breast cooked")[0]["fdcId"] == 125
    assert local.search("chicken breast raw")[0]["fdcId"] == 124
    assert local.search("nonexistentxyz") == []
    assert local.search('"; --') == []
    with pytest.raises(ValueError):
        local.food("abc")
    with pytest.raises(FoodDataError):
        local.food("999")
    with pytest.raises(ValueError):
        local.search(" ")


class Response:
    def __init__(self, content, status=200, headers=None, interrupt=False):
        self.content, self.status_code, self.interrupt = content, status, interrupt
        self.headers = headers or {"Content-Length": str(len(content))}
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass
    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError("test")
    def iter_content(self, size):
        if self.interrupt:
            yield self.content[:20]
            raise requests.ConnectionError("interrupted")
        yield self.content


class Session:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.headers = []
    def get(self, url, **kwargs):
        self.headers.append(kwargs["headers"])
        return next(self.responses)


@pytest.mark.parametrize("mode", ["resume", "ignore_range", "interrupt", "finished", "bad_range"])
def test_download_resume_and_server_variations(local_catalog, tmp_path, mode):
    _, source, datasets = local_catalog
    data = (source / datasets[0].filename).read_bytes()
    folder = tmp_path / "download"
    folder.mkdir()
    part = folder / (datasets[0].filename + ".part")
    responses = []
    if mode == "interrupt":
        responses.append(Response(data, interrupt=True))
    else:
        part.write_bytes(data if mode == "finished" else data[:20])
    if mode in ("resume", "interrupt"):
        responses.append(Response(data[20:], 206, {"Content-Length": str(len(data)-20),
                                                  "Content-Range": f"bytes 20-{len(data)-1}/{len(data)}"}))
    elif mode == "finished":
        responses.append(Response(b"", 416))
    elif mode == "bad_range":
        responses.extend([Response(data, 206, {"Content-Range": "invalid"}), Response(data)])
    else:
        responses.append(Response(data))
    session = Session(responses)
    paths = download(folder, datasets=datasets[:1], session=session, sleeper=lambda _: None, progress=lambda _: None)
    assert paths[0].read_bytes() == data
    assert not part.exists()
    if mode != "interrupt":
        assert "Range" in session.headers[0]


def test_failed_download_keeps_partial_for_next_run(local_catalog, tmp_path):
    _, source, datasets = local_catalog
    data = (source / datasets[0].filename).read_bytes()
    with pytest.raises(ValueError, match="下载失败"):
        download(tmp_path, datasets=datasets[:1], session=Session([Response(data, interrupt=True)]), retries=0)
    assert (tmp_path / (datasets[0].filename + ".part")).read_bytes() == data[:20]
    assert not (tmp_path / datasets[0].filename).exists()


def test_standalone_processor_and_snapshot_compatibility(local_catalog):
    local, _, _ = local_catalog
    doc = document()
    vocabulary = [{"kind": kind, "code": code} for kind, code in
                  [("taste", "light"), ("category", "vegetable"), ("tool", "pot"),
                   ("cooking_method", "boil"), ("difficulty", "beginner")]]
    ai = AI()
    processor = RecipeProcessor(ai, local)
    extracted = processor.extract("土豆200g，2人份", vocabulary=vocabulary)
    assert extracted == doc
    enriched = processor.enrich(doc)
    snapshot = enriched["snapshots"][food_key(doc.ingredients[0])]
    assert snapshot["dataset"]["version"] == "test"
    assert snapshot["per_100g"]["calories_kcal"] == 100
    result = calculate(doc, enriched["snapshots"], TagThresholds())
    assert result["total"]["calories_kcal"] == 200
    assert result["per_serving"]["calories_kcal"] == 100
    assert result["total"]["protein_g"] is None
    calls = len(ai.calls)
    snapshot.pop("dataset")  # Old snapshot remains usable without newly added provenance.
    assert processor.enrich(doc, snapshots=enriched["snapshots"])["changed"] is False
    assert len(ai.calls) == calls


def test_invalid_candidate_is_not_fetched(local_catalog):
    local, _, _ = local_catalog
    class BadAI:
        def request(self, purpose, payload, schema, *args, **kwargs):
            return schema(fdc_id="99999", confidence=1, reason="invalid")
    result = RecipeProcessor(BadAI(), local).enrich(document())
    assert next(iter(result["snapshots"].values()))["status"] == "processing_failed"


def test_missing_catalog_retries_historical_failed_snapshot(tmp_path, local_catalog):
    doc = document()
    key = food_key(doc.ingredients[0])
    missing = RecipeProcessor(AI(), LocalFoodData(tmp_path / "missing.db"))
    failed = missing.enrich(doc)["snapshots"]
    assert failed[key]["status"] == "request_failed"
    local, _, _ = local_catalog
    retried = RecipeProcessor(AI(), local).enrich(doc, snapshots=failed, failed_only=True)
    assert retried["snapshots"][key]["fdc_id"] == "123"


def test_nutrient_precedence_and_historical_survey(local_catalog):
    local, _, _ = local_catalog
    food = local.food("123")
    food["foodNutrients"] = [
        {"nutrient": {"id": 2048, "unitName": "KCAL"}, "amount": 123},
        {"nutrient": {"id": 1008, "unitName": "KJ"}, "amount": 999},
    ]
    assert parse_nutrients(food).calories_kcal == 123
    food["foodNutrients"].append({"nutrient": {"id": 1008, "unitName": "kcal"}, "amount": 100})
    assert parse_nutrients(food).calories_kcal == 100
    food["dataType"] = "Survey (FNDDS)"
    assert parse_nutrients(food).calories_kcal == 100


def test_peeled_query_retrieves_identity_before_other_peeled_food(local_catalog):
    local, _, _ = local_catalog
    with sqlite3.connect(local.path) as db:
        for fdc_id, description in [(200, "Potatoes, without skin, raw"), (201, "Mango peeled raw")]:
            db.execute("INSERT INTO foods VALUES(?,?,?,?)", (fdc_id, description, "Foundation", "{}"))
            db.execute("INSERT INTO foods_fts(rowid,description) VALUES(?,?)", (fdc_id, description))
    assert local.search("potato raw peeled", limit=1)[0]["fdcId"] == 200
