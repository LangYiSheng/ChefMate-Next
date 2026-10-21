"""Application components bind guidance, tools and UI contracts in one manifest.

The reusable agent still has a small progressive instruction loader. This layer
owns the product's larger unit of configuration, dependency wiring and testing.
"""

from dataclasses import dataclass, replace
from pathlib import Path
from types import MappingProxyType
from typing import Mapping, Sequence


@dataclass(frozen=True)
class ComponentSpec:
    id: str
    name: str
    description: str
    tools: tuple[str, ...]
    ui_kinds: tuple[str, ...] = ()
    pages: tuple[str, ...] = ()
    factories: tuple[str, ...] = ()
    dependencies: tuple[str, ...] = ()
    stage_targets: tuple[str, ...] = ()
    version: str = "1"


COMPONENTS = (
    ComponentSpec("meal-planning", "选菜与餐单", "按食材、时间和整餐人数安排菜量。",
                  ("find_ingredients", "search_recipes", "get_recipe", "get_search_options",
                   "show_preference_form", "update_preferences", "show_recipe_choices", "show_recipe_details",
                   "select_recipes", "set_meal_plan", "show_meal_plan"),
                  ("preference_form", "recipe_choices", "recipe_detail", "meal_plan"),
                  factories=("workflow",), dependencies=("retrieval",), stage_targets=("idea", "planning")),
    ComponentSpec("pantry-preparation", "备菜与准备", "逐项核对食材与准备情况。",
                  ("show_prep_checklist", "update_prep_items"), ("prep_checklist",),
                  factories=("workflow",), dependencies=("retrieval",), stage_targets=("prep",)),
    ComponentSpec("cooking-guide", "烹饪引导", "跟随做法、计时并确认完成。",
                  ("show_cooking_guide", "update_cooking_guidance", "set_cooking_timer", "show_timers"),
                  ("cooking_guide", "timers", "task_progress"), factories=("workflow",),
                  dependencies=("retrieval",), stage_targets=("cooking", "completed")),
    ComponentSpec("recipe-authoring", "个人菜谱", "整理、核对并管理自己的菜谱。",
                  ("create_recipe_draft", "search_custom_recipes", "request_recipe_deletion", "confirm_recipe_deletion"),
                  pages=("recipes",), factories=("cookbook",), dependencies=("cookbook",)),
    ComponentSpec("personal-memory", "长期记忆", "维护口味偏好并按需查找历史。",
                  ("read_long_term_memory", "maintain_memory_summary", "search_personal_history"),
                  pages=("memory",), factories=("memory",), dependencies=("memory",)),
    ComponentSpec("food-journal", "饮食记录", "在对话里拍照记餐、查看营养、修改份量并确认保存。",
                  ("create_food_log_draft", "search_food_logs", "get_food_log", "revise_food_log_draft", "confirm_food_log"),
                  ui_kinds=("food_log", "nutrition_summary"), pages=("journal",),
                  factories=("journal",), dependencies=("journal",)),
)
DEFAULT_COMPONENTS = tuple(item.id for item in COMPONENTS)
# These transport/workspace primitives are intentionally shared by components.
SHARED_TOOLS = frozenset({"get_cooking_workspace", "ask_user", "suggest_replies", "list_skills", "load_skill"})
RETRIEVAL_TOOLS = frozenset({"find_ingredients", "search_recipes", "get_recipe", "get_search_options"})


def component_metadata(enabled, *, resources=None, specs=COMPONENTS):
    return [{"id": spec.id, "name": spec.name, "description": spec.description, "version": spec.version,
             "enabled": spec.id in enabled,
             "available": (all(resources.get(name) is not None for name in spec.dependencies)
                           if resources is not None else None),
             "instructions": {"entrypoint": f"skills/{spec.id}/SKILL.md", "loading": "on_demand"},
             "tools": list(spec.tools), "stage_targets": list(spec.stage_targets),
             "ui": {"kinds": list(spec.ui_kinds), "pages": list(spec.pages)},
             "dependencies": list(spec.dependencies)} for spec in specs]


class ComponentRegistry:
    def __init__(self, enabled: Sequence[str], *, instruction_root: Path,
                 resources: Mapping[str, object] | None = None, specs: Sequence[ComponentSpec] = COMPONENTS):
        from chefmate_agent import SkillRegistry

        self.specs = tuple(specs)
        self.by_id = MappingProxyType({item.id: item for item in self.specs})
        if len(self.by_id) != len(self.specs):
            raise ValueError("Duplicate system component")
        if (not isinstance(enabled, (list, tuple)) or len(enabled) > len(self.by_id)
                or any(not isinstance(name, str) or name not in self.by_id for name in enabled)
                or len(set(enabled)) != len(enabled)):
            raise ValueError("系统组件配置无效")
        self.enabled_ids = tuple(enabled)
        self.resources = dict(resources or {})
        root = instruction_root.resolve(strict=True)
        self.root = root
        directories = [(root / name).resolve(strict=True) for name in self.enabled_ids]
        if any(path.parent != root for path in directories):
            raise ValueError("组件指引目录超出应用范围")
        self.instructions = SkillRegistry(directories)
        self._owners = {}
        self._stage_owners = {}
        for spec in self.specs:
            if not spec.id or any(name in self._owners or name in SHARED_TOOLS for name in spec.tools):
                raise ValueError("Component tools must have one explicit owner")
            self._owners.update(dict.fromkeys(spec.tools, spec.id))
            if any(stage in self._stage_owners for stage in spec.stage_targets):
                raise ValueError("Stage transitions must have one explicit owner")
            self._stage_owners.update(dict.fromkeys(spec.stage_targets, spec.id))

    def enabled(self, component_id: str) -> bool:
        return component_id in self.enabled_ids

    def metadata(self) -> list[dict]:
        return component_metadata(self.enabled_ids, resources=self.resources, specs=self.specs)

    def tool_bindings(self, registered: Sequence[str]) -> dict:
        names = set(registered)
        result = {name: owner for name, owner in self._owners.items() if name in names}
        if "advance_cooking_stage" in names:
            result["advance_cooking_stage"] = dict(self._stage_owners)
        return result

    def bind_tool_specs(self, tools):
        """Fail closed when a factory adds a tool without updating its component."""
        bound, seen = [], set()
        for tool in tools:
            if tool.name in seen:
                raise ValueError("Duplicate component tool")
            seen.add(tool.name)
            owner = self._owners.get(tool.name)
            if not owner and tool.name not in SHARED_TOOLS and tool.name != "advance_cooking_stage":
                raise ValueError(f"Tool has no system component: {tool.name}")
            if tool.required_skill and tool.required_skill != owner:
                raise ValueError(f"Tool component ownership mismatch: {tool.name}")
            bound.append(replace(tool, required_skill=owner))
        return bound

    def build_tools(self):
        from .interactions import make_tool_specs

        factories = {"workflow": make_tool_specs}
        resources = self.resources
        if resources.get("memory") is not None:
            from .memory import make_memory_tools
            factories["memory"] = lambda: make_memory_tools(resources["memory"])
        if resources.get("cookbook") is not None:
            from .cookbook import make_recipe_tools
            factories["cookbook"] = lambda: make_recipe_tools(resources["cookbook"])
        if resources.get("journal") is not None:
            from .meal_logging import make_meal_logging_tools
            factories["journal"] = lambda: make_meal_logging_tools(resources["journal"])
        # Disabled tools stay registered to validate closed historical tool calls.
        # The agent hides and rejects them using the generated ownership bindings.
        tools = []
        for factory in dict.fromkeys(name for spec in self.specs for name in spec.factories):
            if factory in factories:
                tools.extend(factories[factory]())
        bound = self.bind_tool_specs(tools)
        expected = {name for spec in self.specs if spec.factories
                    and all(factory in factories for factory in spec.factories)
                    and all(resources.get(name) is not None for name in spec.dependencies)
                    for name in spec.tools if name not in RETRIEVAL_TOOLS}
        missing = expected - {tool.name for tool in bound}
        if missing:
            raise ValueError("Component factory omitted declared tools: " + ", ".join(sorted(missing)))
        return bound
