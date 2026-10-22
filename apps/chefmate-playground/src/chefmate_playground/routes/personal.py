"""Personal HTTP endpoints; domain work stays in application services."""

import asyncio
from fastapi import APIRouter, HTTPException, Query
from ..memory import MemoryConflict
from ..app_context import AppContext


def build_router(context: AppContext) -> APIRouter:
    router = APIRouter()

    @router.get("/api/memory")
    async def get_memory():
        return context.memory.snapshot()

    @router.patch("/api/memory")
    async def update_memory(values: dict):
        if (
            set(values) - {"user_notes", "revision", "enabled"}
            or type(values.get("revision")) is not int
            or not isinstance(values.get("user_notes"), str)
            or "enabled" in values
            and type(values["enabled"]) is not bool
        ):
            raise HTTPException(422, "记忆设置无效。")
        try:
            return context.memory.update_user(values["user_notes"], values["revision"], enabled=values.get("enabled"))
        except MemoryConflict:
            raise HTTPException(409, "记忆已更新，你的草稿仍保留；请重新读取并核对。") from None
        except ValueError:
            raise HTTPException(422, "记忆内容过长或无效。") from None

    @router.delete("/api/memory")
    async def clear_memory(revision: int = Query(ge=0)):
        try:
            return context.memory.clear(revision)
        except MemoryConflict:
            raise HTTPException(409, "记忆已更新，请重新读取后再清空。") from None

    @router.get("/api/journal")
    async def food_journal(days: int = Query(default=7, ge=1, le=366), date: str | None = None):
        try:
            if context.workspace:
                for state in context.repository.list():
                    if (state.get("workspace") or {}).get("meal_id"):
                        await asyncio.to_thread(context.workspace.record_journal, state["id"])
            return {
                "summary": context.journal.summary(days=days),
                "entries": context.journal.entries(date, days=days),
                "food_logs": context.journal.food_logs.list(status="saved", days=days),
                "drafts": context.journal.food_logs.list(status="draft", days=days),
                "deleted_food_logs": context.journal.food_logs.list(status="deleted", days=days),
            }
        except ValueError:
            raise HTTPException(422, "请选择有效日期。") from None

    @router.get("/api/food-logs")
    async def food_logs(
        status: str = Query(default="saved", pattern="^(draft|saved|all|deleted)$"),
        days: int = Query(default=30, ge=1, le=366),
        offset: int = Query(default=0, ge=0, le=100000),
        limit: int = Query(default=100, ge=1, le=100),
        query: str = Query(default="", max_length=200),
    ):
        return context.journal.food_logs.list(status=status, days=days, limit=limit, offset=offset, query=query)

    @router.get("/api/food-logs/{identity}")
    async def food_log(identity: str):
        record = context.journal.food_logs.get(identity)
        if record is None:
            raise HTTPException(404, "饮食记录不存在。")
        return record

    @router.put("/api/food-logs/{identity}")
    async def edit_food_log(identity: str, values: dict):
        if (
            set(values) != {"revision", "record"}
            or type(values["revision"]) is not int
            or not isinstance(values["record"], dict)
        ):
            raise HTTPException(422, "饮食草稿字段无效。")
        try:
            return context.journal.food_logs.update_draft(identity, values["revision"], values["record"])
        except KeyError:
            raise HTTPException(404, "饮食草稿不存在。") from None
        except ValueError as error:
            if "版本" in str(error) or "已更新" in str(error):
                raise HTTPException(409, "草稿已更新，请重新读取后核对。") from None
            raise HTTPException(422, "请核对份量、营养依据与记录日期。") from None

    @router.post("/api/food-logs/{identity}/publish")
    async def publish_food_log(identity: str, values: dict):
        if set(values) != {"revision"} or type(values["revision"]) is not int or values["revision"] < 1:
            raise HTTPException(422, "请核对记录版本。")
        try:
            return context.journal.food_logs.publish(identity, values["revision"])
        except KeyError:
            raise HTTPException(404, "饮食草稿不存在。") from None
        except ValueError:
            raise HTTPException(409, "记录已更新，请刷新后核对。") from None

    @router.delete("/api/food-logs/{identity}")
    async def delete_food_log(identity: str, revision: int = Query(ge=1)):
        try:
            return context.journal.food_logs.delete(identity, revision)
        except KeyError:
            raise HTTPException(404, "饮食记录不存在。") from None
        except ValueError:
            raise HTTPException(409, "记录已更新，请刷新后核对。") from None

    @router.post("/api/food-logs/{identity}/restore")
    async def restore_food_log(identity: str, values: dict):
        if set(values) != {"revision"} or type(values["revision"]) is not int or values["revision"] < 1:
            raise HTTPException(422, "请核对记录版本。")
        try:
            return context.journal.food_logs.restore(identity, values["revision"])
        except KeyError:
            raise HTTPException(404, "饮食记录不存在。") from None
        except ValueError:
            raise HTTPException(409, "记录已更新，请刷新后核对。") from None

    @router.patch("/api/journal/{entry_id}")
    async def journal_feedback(entry_id: str, values: dict):
        if set(values) - {"rating", "notes"}:
            raise HTTPException(422, "反馈字段无效。")
        try:
            previous = context.journal.get(entry_id)
            if previous is None:
                raise KeyError(entry_id)
            return context.journal.feedback(
                entry_id, rating=values.get("rating", previous["rating"]), notes=values.get("notes", previous["notes"])
            )
        except KeyError:
            raise HTTPException(404, "饮食记录不存在。") from None
        except ValueError:
            raise HTTPException(422, "请填写有效的评分和笔记。") from None

    return router
