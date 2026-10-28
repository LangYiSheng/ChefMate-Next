"""Library HTTP endpoints; domain work stays in application services."""

import asyncio
from fastapi import APIRouter, HTTPException, Query
from ..cookbook import RecipeDraftRequest
from ..app_context import AppContext


def build_router(context: AppContext) -> APIRouter:
    router = APIRouter()

    @router.get("/api/cookbook")
    async def recipe_center(
        query: str = Query(default="", max_length=200),
        source: str = Query(default="all", pattern="^(all|user|favorites|drafts|deleted)$"),
        offset: int = Query(default=0, ge=0, le=100000),
        limit: int = Query(default=12, ge=1, le=30),
    ):
        favorite_ids = context.cookbook.favorites()
        personal = context.cookbook.list(query=query, status="draft" if source == "drafts" else "saved", limit=100)

        def card(record):
            return {
                **context.cookbook.summary(record),
                "favorite": record["recipe_id"] in favorite_ids,
            }

        if source in {"user", "drafts", "deleted"}:
            status = {"user": "saved", "drafts": "draft", "deleted": "deleted"}[source]
            personal = context.cookbook.list(query=query, status=status, limit=limit, offset=offset)
            return {**personal, "items": [card(item) for item in personal["items"]]}
        if source == "favorites":
            items = []
            for identity in favorite_ids:
                result = (
                    await asyncio.to_thread(context.retrieval.get_recipe, {"recipe_id": identity})
                    if context.retrieval
                    else context.cookbook.retrieve(identity)
                )
                recipe = result.get("recipe")
                if (
                    result.get("status") == "ok"
                    and recipe
                    and (not query or query.casefold() in recipe["name"].casefold())
                ):
                    if identity.startswith("custom-"):
                        record = context.cookbook.get(identity)
                        if record and record["status"] == "saved":
                            items.append(card(record))
                        continue
                    items.append(
                        {
                            **{
                                key: recipe.get(key)
                                for key in ("recipe_id", "name", "servings", "total_time_min", "description", "difficulty")
                            },
                            "origin": recipe.get("origin", "system"),
                            "favorite": True,
                            "status": "saved",
                        }
                    )
            return {"items": items[offset : offset + limit], "total": len(items), "offset": offset, "limit": limit}
        # Page personal records first, then the official catalog without copying its dataset.
        personal_count = personal["total"]
        personal_page = context.cookbook.list(query=query, limit=limit, offset=offset)["items"]
        remaining = limit - len(personal_page)
        official_offset = max(0, offset - personal_count)
        try:
            official = (
                await asyncio.to_thread(
                    context.retrieval.search_recipes,
                    {
                        "name": query or None,
                        "ready_for_recommendation": None,
                        "limit": max(1, remaining),
                        "offset": official_offset,
                    },
                )
                if context.retrieval
                else {"items": [], "total": 0}
            )
        except Exception:
            raise HTTPException(503, "菜谱库暂时无法读取，请稍后重试。") from None
        items = [card(item) for item in personal_page]
        items.extend(
            {
                **{key: item.get(key) for key in ("recipe_id", "name", "servings", "total_time_min", "description", "difficulty")},
                "origin": "system",
                "favorite": item["recipe_id"] in favorite_ids,
                "status": "saved",
            }
            for item in official.get("items", [])[:remaining]
        )
        return {"items": items, "total": personal_count + official.get("total", 0), "offset": offset, "limit": limit}

    @router.get("/api/cookbook/{recipe_id}")
    async def personal_recipe(recipe_id: str):
        record = context.cookbook.get(recipe_id)
        if record is None:
            raise HTTPException(404, "个人菜谱不存在。")
        return record

    @router.delete("/api/cookbook/{recipe_id}")
    async def delete_personal_recipe(recipe_id: str, revision: int = Query(ge=1)):
        try:
            return context.cookbook.delete(recipe_id, revision)
        except KeyError:
            raise HTTPException(404, "个人菜谱不存在，基础菜谱不能删除。") from None
        except ValueError:
            raise HTTPException(409, "菜谱版本已变化，请刷新后重新确认删除。") from None

    @router.post("/api/cookbook/{recipe_id}/restore")
    async def restore_personal_recipe(recipe_id: str, values: dict):
        if set(values) != {"revision"} or type(values["revision"]) is not int or values["revision"] < 1:
            raise HTTPException(422, "请核对菜谱版本。")
        try:
            return context.cookbook.restore(recipe_id, values["revision"])
        except KeyError:
            raise HTTPException(404, "个人菜谱不存在。") from None
        except ValueError:
            raise HTTPException(409, "菜谱状态已变化，请刷新后重试。") from None

    @router.put("/api/cookbook/{recipe_id}")
    async def edit_recipe_draft(recipe_id: str, values: dict):
        if set(values) != {"revision", "recipe"} or type(values["revision"]) is not int:
            raise HTTPException(422, "草稿请求无效。")
        if context.cookbook.get(recipe_id) is None:
            raise HTTPException(404, "个人菜谱不存在。")
        try:
            return context.cookbook.update_draft(
                recipe_id, RecipeDraftRequest.model_validate(values["recipe"]), values["revision"]
            )
        except ValueError:
            raise HTTPException(409, "草稿未保存，请检查填写内容和版本。") from None

    @router.post("/api/cookbook/{recipe_id}/publish")
    async def publish_recipe(recipe_id: str, values: dict):
        if set(values) != {"revision"} or type(values["revision"]) is not int:
            raise HTTPException(422, "请核对草稿版本。")
        try:
            return context.cookbook.publish(recipe_id, values["revision"])
        except KeyError:
            raise HTTPException(404, "草稿不存在。") from None
        except ValueError:
            raise HTTPException(409, "草稿已更新，请重新核对。") from None

    @router.put("/api/favorites/{recipe_id}")
    async def favorite_recipe(recipe_id: str, values: dict):
        if set(values) != {"favorite"} or type(values["favorite"]) is not bool:
            raise HTTPException(422, "收藏设置无效。")
        if values["favorite"]:
            result = (
                await asyncio.to_thread(context.retrieval.get_recipe, {"recipe_id": recipe_id})
                if context.retrieval
                else context.cookbook.retrieve(recipe_id)
            )
            if result.get("status") != "ok":
                raise HTTPException(404, "菜谱不存在，不能收藏。")
        try:
            return context.cookbook.favorite(recipe_id, values["favorite"])
        except ValueError:
            raise HTTPException(409, "菜谱已删除或尚未保存，不能收藏。") from None

    @router.get("/api/recipes/{recipe_id}")
    async def recipe(recipe_id: str):
        if context.retrieval is None:
            raise HTTPException(503, "数据库尚未配置。")
        try:
            result = await asyncio.to_thread(context.retrieval.get_recipe, {"recipe_id": recipe_id})
        except Exception:
            raise HTTPException(503, "暂时无法读取菜谱。") from None
        if result.get("status") == "not_found":
            raise HTTPException(404, "菜谱不存在或数据集已更新。")
        return result

    @router.post("/api/tools/{tool_name}")
    async def tool(tool_name: str, payload: dict):
        if context.retrieval is None:
            raise HTTPException(503, "数据库尚未配置。")
        calls = {
            "find_ingredients": context.retrieval.find_ingredients,
            "search_recipes": context.retrieval.search_recipes,
            "get_recipe": context.retrieval.get_recipe,
            "get_search_options": context.retrieval.get_search_options,
        }
        function = calls.get(tool_name)
        if function is None:
            raise HTTPException(404, "工具不存在。")
        try:
            if tool_name == "get_search_options":
                if payload:
                    raise ValueError("这个工具不接受参数。")
                return await asyncio.to_thread(function)
            return await asyncio.to_thread(function, payload)
        except ValueError as error:
            raise HTTPException(422, str(error)) from None
        except Exception:
            raise HTTPException(503, "查询未完成，请检查数据库连接。") from None

    return router
