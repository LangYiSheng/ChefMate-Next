"""Checks retrieval provenance and shapes evidence returned to the model and host."""

import asyncio
import copy
import json
from typing import Any

from pydantic import ValidationError

from .boundary import _compact_result, _plain


class RetrievalEvidence:
    """Internal part of ChefMateAgent; the core owns configuration and turn lifecycle."""

    async def _invoke_tool(self, call: dict[str, Any], history: list[dict[str, Any]]) -> dict[str, Any]:
        name = call["name"]
        try:
            request = self._requests[name].model_validate(call["args"])
        except ValidationError as exc:
            return {
                "status": "invalid_arguments",
                "message": "参数不符合工具约束，请按schema修正；没有执行查询。",
                "errors": [{"loc": list(item["loc"]), "type": item["type"]} for item in exc.errors()[:12]],
            }
        arguments = request.model_dump(mode="json", exclude_none=False)
        provenance_error = self._verify_references(name, arguments, history)
        if provenance_error is not None:
            return provenance_error
        try:
            handler = getattr(self.service, name)
            result = await asyncio.to_thread(handler) if name == "get_search_options" else await asyncio.to_thread(
                handler, arguments,
            )
            plain = _plain(result)
            if not isinstance(plain, dict):
                return {"status": "tool_error", "message": "查询服务返回了不支持的结果格式。"}
            return _compact_result(self._retrieval_projection(name, plain), self.config.max_tool_result_chars)
        except Exception:
            return {"status": "tool_error", "message": "数据库查询暂时失败，请重试。"}

    @staticmethod
    def _retrieval_projection(name, result):
        result = copy.deepcopy(result)
        if result.get("status") != "ok":
            return result
        # Native arguments plus explicit normalized_options preserve any label-to-code changes.
        result.pop("normalized_query", None)
        if name == "search_recipes":
            for item in result.get("items", []):
                for key in ("description", "raw_source_json", "source", "ingredient_count"):
                    item.pop(key, None)
                item["matched_ingredients"] = [{key: value for key, value in matched.items()
                    if key in {"ingredient_id", "canonical_name", "raw_name", "matched_groups", "estimated_grams"}}
                    for matched in item.get("matched_ingredients", [])]
        elif name == "find_ingredients":
            for item in result.get("items", []):
                if "examples" in item:
                    item["examples"] = [{key: value for key, value in example.items()
                        if key in {"recipe_id", "recipe_name", "raw_name"}} for example in item["examples"][:3]]
        return result

    @staticmethod
    def _verify_references(name: str, arguments: dict[str, Any], history: list[dict[str, Any]]) -> dict[str, Any] | None:
        """Selections must come from tool evidence at the same data snapshot, including previous turns."""
        if name not in {"search_recipes", "get_recipe"}:
            return None
        if name == "get_recipe" and str(arguments.get("recipe_id", "")).startswith("custom-"):
            identity = arguments["recipe_id"]
            snapshots = []
            for message in history:
                if message.get("role") != "tool" or message.get("name") not in {"search_custom_recipes", "get_recipe"}:
                    continue
                try:
                    result = json.loads(message["content"])
                except (KeyError, TypeError, ValueError):
                    continue
                if not isinstance(result, dict) or result.get("status") != "ok":
                    continue
                if message["name"] == "search_custom_recipes":
                    snapshots.extend(item["snapshot"] for item in result.get("items", [])
                                     if isinstance(item, dict) and item.get("recipe_id") == identity
                                     and isinstance(item.get("snapshot"), dict))
                elif result.get("recipe", {}).get("recipe_id") == identity and isinstance(result.get("snapshot"), dict):
                    snapshots.append(result["snapshot"])
            if not snapshots:
                return {"status": "unverified_reference", "message": "请先search_custom_recipes获取真实个人菜谱引用。"}
            if arguments.get("expected_snapshot") != snapshots[-1]:
                return {"status": "snapshot_required", "snapshot": snapshots[-1],
                        "message": "请使用该道个人菜谱检索结果的真实snapshot。"}
            return None
        snapshot = None
        selectors: set[tuple[str, str | None]] = set()
        recipe_ids: set[str] = set()
        personal_details: set[str] = set()
        for message in history:
            if message.get("role") == "assistant":
                for call in message.get("tool_calls", []):
                    function = call.get("function", {})
                    if function.get("name") != "get_recipe":
                        continue
                    try:
                        values = json.loads(function["arguments"])
                    except (KeyError, TypeError, ValueError):
                        continue
                    if (isinstance(call.get("id"), str) and isinstance(values, dict)
                            and str(values.get("recipe_id", "")).startswith("custom-")):
                        personal_details.add(call["id"])
                continue
            if (message.get("role") != "tool"
                    or message.get("name") not in {"find_ingredients", "search_recipes", "get_recipe", "get_search_options"}):
                continue
            try:
                output = json.loads(message["content"])
            except (KeyError, TypeError, ValueError):
                continue
            if not isinstance(output, dict):
                continue
            recipe = output.get("recipe")
            if (message.get("name") == "get_recipe" and (
                    message.get("tool_call_id") in personal_details
                    or isinstance(recipe, dict) and str(recipe.get("recipe_id", "")).startswith("custom-"))):
                continue  # Personal recipe versions do not replace the official dataset snapshot.
            current_snapshot = output.get("snapshot")
            if current_snapshot is not None and current_snapshot != snapshot:
                snapshot = current_snapshot
                selectors.clear()
                recipe_ids.clear()
            if output.get("status") != "ok":
                continue
            if message.get("name") == "find_ingredients":
                for item in output.get("items", []):
                    if isinstance(item, dict) and item.get("ingredient_id"):
                        selectors.add((item["ingredient_id"], item.get("canonical_name")))
                        selectors.add((item["ingredient_id"], None))
            elif message.get("name") == "search_recipes":
                # Several searches/pages can contribute to one recommendation.
                # Provenance lasts for this snapshot; presentation still uses the latest result set.
                recipe_ids.update(item["recipe_id"] for item in output.get("items", [])
                                  if isinstance(item, dict) and item.get("recipe_id"))
            elif message.get("name") == "get_recipe" and isinstance(output.get("recipe"), dict):
                if output["recipe"].get("recipe_id"):
                    recipe_ids.add(output["recipe"]["recipe_id"])
        if name == "get_recipe":
            if arguments.get("recipe_id") not in recipe_ids:
                return {"status": "unverified_reference", "message": "请先search_recipes获取真实菜谱ID再读取详情。"}
        else:
            selected = [selector for group in arguments.get("ingredient_groups", [])
                        for selector in group.get("any_of", [])]
            selected.extend(arguments.get("exclude_ingredients", []))
            if not selected:
                return None
            if any((item.get("ingredient_id"), item.get("canonical_name")) not in selectors for item in selected):
                return {"status": "unverified_reference", "message": "食材引用未经过验证，请先find_ingredients查真实候选。"}
        if snapshot is None or arguments.get("expected_snapshot") != snapshot:
            return {
                "status": "snapshot_required", "snapshot": snapshot,
                "message": "请传入与真实候选或菜谱结果一致的expected_snapshot，数据变化时重新查候选。",
            }
        return None

    @staticmethod
    def _collect_result(name: str, result: dict[str, Any], state: dict[str, Any]) -> None:
        if result.get("status") == "duplicate_query":
            return
        if name == "search_recipes":
            # A new result set always invalidates the previous detail selection, including empty/error results.
            state["selected_recipe"] = None
        if result.get("status") == "snapshot_changed":
            state.update(recipes=[], selected_recipe=None, snapshot=result.get("snapshot"))
            return
        if result.get("status") != "ok":
            if name == "search_recipes":
                state["recipes"] = []
            elif name == "get_recipe":
                state["selected_recipe"] = None
            return
        snapshot = result.get("snapshot")
        if snapshot is not None and snapshot != state["snapshot"]:
            state.update(recipes=[], selected_recipe=None, snapshot=snapshot)
        if name == "search_recipes":
            state["recipes"] = [item for item in result.get("items", []) if isinstance(item, dict)
                                and item.get("recipe_id")]
        elif name == "get_recipe" and isinstance(result.get("recipe"), dict):
            state["selected_recipe"] = result["recipe"]
