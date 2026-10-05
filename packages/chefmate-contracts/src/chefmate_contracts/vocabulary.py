"""Stable codes and versioned vocabulary snapshots, separate from recipe structure."""
import hashlib
import json
from enum import StrEnum
from importlib.resources import files

from pydantic import Field, model_validator

from .schemas import StrictModel, RecipeDocument


class VocabularyKind(StrEnum):
    TASTE = "taste"
    CATEGORY = "category"
    ALLERGEN = "allergen"
    DIFFICULTY = "difficulty"
    TOOL = "tool"
    COOKING_METHOD = "cooking_method"


class Difficulty(StrEnum):
    BEGINNER = "beginner"
    NORMAL = "normal"
    ADVANCED = "advanced"


class VocabularyEntry(StrictModel):
    kind: VocabularyKind
    code: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    label: str = Field(min_length=1)
    enabled: bool = True

    @model_validator(mode="after")
    def valid_entry(self):
        if not self.label.strip():
            raise ValueError("词表显示名不能为空")
        if self.kind == VocabularyKind.DIFFICULTY and self.code not in set(Difficulty):
            raise ValueError("难度只允许 beginner / normal / advanced")
        return self


def default_vocabulary() -> list[VocabularyEntry]:
    data = json.loads(files("chefmate_contracts").joinpath("data/enums.json").read_text(encoding="utf-8"))
    return [VocabularyEntry(kind=kind, code=code, label=label)
            for kind, values in data.items() for code, label in values.items()]


def validate_vocabulary(entries) -> list[VocabularyEntry]:
    if isinstance(entries, VocabularySnapshot):
        entries = entries.entries
    result = []
    seen = set()
    for entry in entries:
        if isinstance(entry, VocabularyEntry):
            value = VocabularyEntry.model_validate(entry.model_dump())
        else:
            entry = dict(entry)
            status = entry.pop("status", None)
            if status is not None:
                if status not in ("approved", "pending_review", "rejected"):
                    raise ValueError("未知词表状态")
                entry.setdefault("enabled", status == "approved")
            # Older callers supplied only kind/code; use the code as a display fallback.
            entry.setdefault("label", entry.get("code", ""))
            value = VocabularyEntry.model_validate(entry)
        key = (value.kind, value.code)
        if key in seen:
            raise ValueError(f"重复词表代码：{value.kind}/{value.code}")
        seen.add(key)
        result.append(value)
    return result


def validate_recipe_vocabulary(document, entries) -> RecipeDocument:
    document = RecipeDocument.model_validate(document)
    allowed = {}
    for entry in validate_vocabulary(entries):
        if entry.enabled:
            allowed.setdefault(entry.kind, set()).add(entry.code)
    groups = [("taste", document.taste_tags), ("tool", document.required_tools),
              ("cooking_method", document.cooking_methods),
              ("difficulty", [document.difficulty] if document.difficulty else [])]
    for ingredient in document.ingredients:
        groups.extend([("category", [ingredient.category]), ("allergen", ingredient.allergens)])
    for kind, codes in groups:
        if any(code not in allowed.get(kind, set()) for code in codes):
            raise ValueError(f"菜谱包含未启用的词表选项：{kind}")
    return document


def _hash(entries):
    values = sorted((e.model_dump(mode="json") for e in entries), key=lambda e: (e["kind"], e["code"]))
    raw = json.dumps(values, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


class VocabularySnapshot(StrictModel):
    schema_version: int = Field(default=1, ge=1, le=1)
    entries: list[VocabularyEntry]
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def valid_snapshot(self):
        entries = validate_vocabulary(self.entries)
        if self.sha256 != _hash(entries):
            raise ValueError("词表快照哈希不一致")
        return self


def vocabulary_snapshot(entries) -> VocabularySnapshot:
    entries = sorted(validate_vocabulary(entries), key=lambda e: (e.kind, e.code))
    return VocabularySnapshot(entries=entries, sha256=_hash(entries))
