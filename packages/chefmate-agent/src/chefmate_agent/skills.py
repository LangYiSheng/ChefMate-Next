"""Progressive local Skills: explicit directory allowlist, metadata discovery, bounded reads."""

import hashlib
import re
from pathlib import Path
from typing import Annotated, Any, Sequence

import yaml
from pydantic import BaseModel, ConfigDict, Field

SKILL_NAME = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")

# The application opts into these bindings. A standalone agent may provide its
# own tool/skill ownership without depending on the kitchen workflow package.
KITCHEN_TOOL_SKILLS = {
    "set_kitchen_display": "app-preferences",
    **dict.fromkeys(("find_ingredients", "search_recipes", "get_recipe", "get_search_options",
                     "show_preference_form", "update_preferences", "show_recipe_choices",
                     "show_recipe_details", "select_recipes", "set_meal_plan", "show_meal_plan"), "meal-planning"),
    **dict.fromkeys(("show_prep_checklist", "update_prep_items"), "pantry-preparation"),
    **dict.fromkeys(("show_cooking_guide", "update_cooking_guidance", "set_cooking_timer",
                     "show_timers"), "cooking-guide"),
    "advance_cooking_stage": {
        "idea": "meal-planning", "planning": "meal-planning", "prep": "pantry-preparation",
        "cooking": "cooking-guide", "completed": "cooking-guide",
    },
}


class SkillListRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class SkillLoadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    name: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")]


class SkillRegistry:
    """Each enabled directory grants only its own SKILL.md; no scanning or auxiliary file execution."""

    def __init__(self, allowed_directories: Sequence[str | Path] = (), *, max_file_chars: int = 16000):
        if not 1000 <= max_file_chars <= 48000 or len(allowed_directories) > 32:
            raise ValueError("Skill discovery/read budget exceeded")
        self.max_file_chars = max_file_chars
        self._entries: dict[str, dict[str, Any]] = {}
        for directory in allowed_directories:
            root = Path(directory).resolve(strict=True)
            if not root.is_dir():
                raise ValueError("Enabled skill must be a directory")
            path, document = self._read(root)
            metadata, _ = self._parse(document)
            name = metadata["name"]
            if name in self._entries:
                raise ValueError("Duplicate enabled skill name")
            self._entries[name] = {
                **metadata, "root": root, "path": path,
                "digest": hashlib.sha256(document.encode("utf-8")).hexdigest(),
            }

    def _read(self, root: Path) -> tuple[Path, str]:
        path = (root / "SKILL.md").resolve(strict=True)
        if path.parent != root or not path.is_file():
            raise ValueError("Skill file resolves outside its enabled directory")
        if path.stat().st_size > self.max_file_chars * 4:
            raise ValueError("Skill file exceeds read budget")
        document = path.read_text(encoding="utf-8")
        if len(document) > self.max_file_chars:
            raise ValueError("Skill file exceeds read budget")
        return path, document

    @staticmethod
    def _parse(document: str) -> tuple[dict[str, str], str]:
        lines = document.splitlines()
        if not lines or lines[0].strip() != "---":
            raise ValueError("Skill metadata frontmatter is required")
        end = next((index for index, line in enumerate(lines[1:], 1) if line.strip() == "---"), None)
        if end is None or sum(len(line) for line in lines[1:end]) > 6000:
            raise ValueError("Skill metadata exceeds budget or is incomplete")
        metadata = yaml.safe_load("\n".join(lines[1:end]))
        if not isinstance(metadata, dict):
            raise ValueError("Skill metadata must be a mapping")
        name, description = metadata.get("name"), metadata.get("description")
        if (not isinstance(name, str) or not SKILL_NAME.fullmatch(name) or not isinstance(description, str)
                or not description.strip() or len(description) > 1200):
            raise ValueError("Skill name or description is invalid")
        return {"name": name, "description": description.strip()}, "\n".join(lines[end + 1:]).strip()

    def list_metadata(self) -> list[dict[str, str]]:
        return [{"name": name, "description": entry["description"]}
                for name, entry in sorted(self._entries.items())]

    def enabled(self, name: str) -> bool:
        return name in self._entries

    def load(self, name: str) -> dict[str, Any]:
        if not SKILL_NAME.fullmatch(name) or name not in self._entries:
            return {"status": "not_found", "message": "Skill 未启用或名称无效。"}
        entry = self._entries[name]
        try:
            _, document = self._read(entry["root"])
            metadata, body = self._parse(document)
        except (ValueError, OSError, UnicodeError, yaml.YAMLError):
            return {"status": "skill_unavailable", "message": "Skill 文件不可读取或超过允许范围。"}
        digest = hashlib.sha256(document.encode("utf-8")).hexdigest()
        if metadata["name"] != name or digest != entry["digest"]:
            return {"status": "skill_changed", "message": "Skill 已变更，请刷新已启用的Skills配置。"}
        return {"status": "ok", **metadata, "content": body, "digest": digest}
