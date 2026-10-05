"""Resumable stage runner. The UI launches it in a separate local process."""

import json
import os
import subprocess
import sys

from data_cleaning.config import ROOT, service_settings
from data_cleaning.models.batch import BatchRepository
from data_cleaning.schemas.batch import BatchOptions
from data_cleaning.services.batch_catalog import build_catalog, match_usda
from data_cleaning.services.batch_lock import worker_lock
from data_cleaning.services.normalization import ai_extract, normalize_one
from data_cleaning.services.openai_service import OpenAIService
from data_cleaning.services.usda import USDAService


def preflight(options: BatchOptions) -> None:
    settings = service_settings()
    if not any((options.extract, options.catalog, options.usda, options.tags)):
        raise ValueError("至少选择一个处理阶段")
    if not settings["openai_key"] or not settings["model"]:
        raise ValueError("请先在 .env 填入 DeepSeek / OpenAI 密钥与模型，重启后台后再运行")
    if options.usda and not (options.pipeline == "unified" and options.conversion and options.conversion.estimate_missing):
        USDAService().require_ready()


def launch(repo: BatchRepository, run_id: str) -> int:
    options = BatchOptions.model_validate_json(repo.run(run_id)["options_json"])
    preflight(options)
    env = os.environ.copy()
    env["CLEANING_DB_PATH"] = str(repo.path)
    (ROOT / "logs").mkdir(exist_ok=True)
    try:
        with (ROOT / "logs/batch_worker.log").open("ab") as output:
            process = subprocess.Popen(
                [sys.executable, str(ROOT / "scripts/batch_worker.py"), run_id],
                cwd=ROOT,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=output,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        return process.pid
    except Exception:
        repo.set_run(run_id, "failed", "后台进程启动失败，请检查本地运行环境")
        raise


def execute(repo: BatchRepository, run_id: str, *, ai=None, usda=None) -> None:
    with worker_lock(repo.path):
        if repo.run(run_id)["status"] == "stopping":
            repo.set_run(run_id, "paused")
            return
        if repo.run(run_id)["status"] != "queued":
            return
        options = BatchOptions.model_validate_json(repo.run(run_id)["options_json"])
        repo.set_run(run_id, "running")
        try:
            _execute(repo, run_id, options, ai or OpenAIService(repo), usda or USDAService(repo))
        except Exception as exc:
            repo.set_run(run_id, "failed", f"批量任务异常：{type(exc).__name__}；详情请检查服务日志")


def _execute(repo: BatchRepository, run_id: str, options: BatchOptions, ai: OpenAIService, usda: USDAService) -> None:
    recipe_ids = json.loads(repo.run(run_id)["recipe_ids_json"])
    previous = {(i["stage"], i["entity_id"]): i for i in repo.items(run_id)}

    def item(stage, entity, version, work) -> bool:
        if repo.run(run_id)["status"] == "stopping":
            repo.set_run(run_id, "paused")
            return False
        old = previous.get((stage, entity))
        if old and old["version"] == version and old["status"] in ("done", "skipped"):
            return True
        repo.checkpoint(run_id, stage, entity, version, "processing")
        try:
            result = work()
            if result == "failed":
                raise ValueError("阶段处理失败")
            repo.checkpoint(run_id, stage, entity, version, "skipped" if result == "skipped" else "done", result)
        except Exception as exc:
            error = f"{type(exc).__name__}：处理失败，请查看服务日志或人工处理"
            repo.checkpoint(run_id, stage, entity, version, "failed", error=error)
            if stage == "usda":
                current = repo.standard(entity)
                if current and current["nutrition_match_status"] not in ("approved", "rejected"):
                    repo.nutrition_error(entity, current["updated_at"], error)
            if stage == "catalog":
                current = repo.raw_detail(int(entity))
                if current["status"] not in ("approved", "rejected"):
                    repo.match_raw(
                        int(entity),
                        current["canonical_ingredient_id"],
                        expected=current["updated_at"],
                        status="pending_review",
                        method="ai_catalog",
                        error=error,
                    )
        return True

    if options.pipeline == "unified":
        from data_cleaning.services.conversion import convert_one

        settings = options.conversion or repo.conversion_settings()
        ai.settings.update(
            retries=settings.retries, timeout=settings.timeout, max_output_tokens=settings.max_output_tokens
        ) if hasattr(ai, "settings") else None
        usda.settings.update(retries=settings.retries, timeout=settings.timeout) if hasattr(usda, "settings") else None
        for recipe_id in recipe_ids:
            row = repo.get_recipe(recipe_id)

            def unified_work(recipe_id=recipe_id, source=row):
                saved = repo.conversion(recipe_id)
                if (
                    saved
                    and saved["source_hash"] == source["content_hash"]
                    and saved["status"] in ("approved", "rejected")
                ):
                    return "skipped"
                return convert_one(
                    repo,
                    recipe_id,
                    settings=settings,
                    ai=ai,
                    with_nutrition=options.usda,
                    usda=usda,
                    force=options.redo_existing,
                )

            if not item(
                "convert",
                recipe_id,
                row["content_hash"],
                unified_work,
            ):
                return
        failed = any(i["status"] == "failed" for i in repo.items(run_id))
        repo.set_run(run_id, "completed_with_errors" if failed else "completed")
        return
    if options.extract:
        for recipe_id in recipe_ids:
            row = repo.get_recipe(recipe_id)

            def extract(row=row):
                if row["status"] in ("approved", "rejected"):
                    return "skipped"
                if (
                    row["extracted_json"]
                    and not row["extract_error"]
                    and "AI 提取，必须对照原文核实所有字段" in row["extracted_json"]
                ):
                    return "skipped"
                ai_extract(repo, row["recipe_id"], ai)
                return "pending_review"

            if not item("extract", recipe_id, row["content_hash"], extract):
                return
    raw_rows = repo.raw_scope(recipe_ids)
    if options.catalog:
        for raw in raw_rows:
            if not item("catalog", str(raw["id"]), raw["raw_name"], lambda raw=raw: build_catalog(repo, raw["id"], ai)):
                return
    if options.usda:
        ids = sorted({r["canonical_ingredient_id"] for r in repo.raw_scope(recipe_ids) if r["canonical_ingredient_id"]})
        for ingredient_id in ids:
            row = repo.standard(ingredient_id)
            version = json.dumps([row["canonical_name"], row["category"], row["usda_query"]], ensure_ascii=False)
            if not item(
                "usda",
                ingredient_id,
                version,
                lambda ingredient_id=ingredient_id: match_usda(repo, ingredient_id, ai, usda),
            ):
                return
    if options.tags:
        for recipe_id in recipe_ids:
            row = repo.get_recipe(recipe_id)

            def tags(recipe_id=recipe_id):
                current = repo.get_recipe(recipe_id)
                profile = repo.profile(recipe_id)
                if profile and profile["source_updated_at"] == current["updated_at"] and not profile["error_message"]:
                    return "skipped"
                return normalize_one(repo, recipe_id, ai=ai)

            if not item("tags", recipe_id, row["updated_at"], tags):
                return
    failed = any(i["status"] == "failed" for i in repo.items(run_id))
    repo.set_run(run_id, "completed_with_errors" if failed else "completed")
