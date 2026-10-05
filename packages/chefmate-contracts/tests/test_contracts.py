import pytest

from chefmate_contracts import (
    RecipeDocument, ConvertedRecipe, default_vocabulary, validate_vocabulary,
    validate_recipe_vocabulary, vocabulary_snapshot, VocabularySnapshot,
)


def test_recipe_alias_and_roundtrip_preserve_fields(recipe):
    assert RecipeDocument is ConvertedRecipe
    assert RecipeDocument.model_validate_json(recipe.model_dump_json()).model_dump() == recipe.model_dump()
    with pytest.raises(ValueError):
        RecipeDocument.model_validate({**recipe.model_dump(), "nutrition": {}})
    item = {**recipe.ingredients[0].model_dump(), "amount_evidence": ""}
    with pytest.raises(ValueError):
        RecipeDocument.model_validate({**recipe.model_dump(), "ingredients": [item]})


def test_default_vocabulary_is_independent_and_snapshot_is_verified(recipe):
    first, second = default_vocabulary(), default_vocabulary()
    first[0].label = "修改显示名"
    assert first[0].label != second[0].label
    snapshot = vocabulary_snapshot(second)
    assert vocabulary_snapshot(reversed(second)).sha256 == snapshot.sha256
    changed = snapshot.model_dump()
    changed["entries"][0]["label"] = "changed"
    with pytest.raises(ValueError, match="哈希"):
        VocabularySnapshot.model_validate(changed)
    validate_recipe_vocabulary(recipe, snapshot)


def test_disabled_codes_do_not_prevent_historical_structure_reading(recipe):
    vocabulary = default_vocabulary()
    for entry in vocabulary:
        if entry.kind == "taste" and entry.code == "light":
            entry.enabled = False
    historical = RecipeDocument.model_validate_json(recipe.model_dump_json())
    with pytest.raises(ValueError, match="taste"):
        validate_recipe_vocabulary(historical, vocabulary)


@pytest.mark.parametrize("entries", [
    [{"kind": "unknown", "code": "a", "label": "a"}],
    [{"kind": "difficulty", "code": "impossible", "label": "a"}],
    [{"kind": "taste", "code": "a", "label": "a"}] * 2,
    [{"kind": "taste", "code": "a", "label": " "}],
])
def test_invalid_vocabulary_is_rejected(entries):
    with pytest.raises(ValueError):
        validate_vocabulary(entries)
