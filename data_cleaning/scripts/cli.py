import argparse
import json
import sys
from pathlib import Path

# Supports both: python -m data_cleaning.scripts.cli / python scripts/cli.py
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from data_cleaning.config import database_path, local_path, source_path
from data_cleaning.models.conversion import ConversionRepository as Repository
from data_cleaning.services.conversion import convert_one
from data_cleaning.services.exporter import export_json
from data_cleaning.services.logging_service import log_event
from data_cleaning.services.pipeline import import_directory
from data_cleaning.models.batch import BatchRepository
from data_cleaning.schemas.batch import BatchOptions
from data_cleaning.services.batch import execute, preflight
from data_cleaning.services.batch_lock import worker_lock


def main() -> int:
    # Windows redirected output can otherwise use a legacy code page.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="ChefMate 独立数据清洗工具 · 完整流水线")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init", help="安全初始化数据库")
    imp = commands.add_parser("import", help="导入 HowToCook Markdown，暂不提取")
    imp.add_argument("--source", type=local_path, default=source_path())
    imp.add_argument("--dataset", default="HowToCook", help="稳定的数据集命名空间")
    out = commands.add_parser("export", help="默认导出新流程审阅通过的完整JSON")
    out.add_argument("--all", action="store_true", help="包括尚未审核/拒绝数据，仅用于检查")
    out.add_argument("--stage", choices=["unified"], default="unified")
    convert = commands.add_parser("convert", help="完整Markdown直接AI转换为完整JSON")
    convert.add_argument("--recipe-id", required=True)
    convert.add_argument("--feedback", default="")
    convert.add_argument("--with-nutrition", action="store_true")
    clear = commands.add_parser("clear", help="清空数据并重置枚举，不删除 SQLite 文件")
    clear.add_argument("--confirm", default="")
    commands.add_parser("stats", help="显示统计")
    batch = commands.add_parser("batch", help="批量完整 JSON 转换与可选 USDA 查询；支持断点继续")
    batch.add_argument("--limit", type=int, default=0, help="最多菜谱数，0为全部")
    batch.add_argument("--no-usda", action="store_true")
    batch.add_argument("--resume", help="继续批次ID，跳过已完成项并重试失败")
    batch.add_argument("--recover", action="store_true", help="确认进程已退出后恢复中断状态")
    args = parser.parse_args()
    try:
        repo = Repository(database_path())
        match args.command:
            case "batch":
                batch_repo = BatchRepository(repo.path)
                if args.recover:
                    with worker_lock(repo.path):
                        batch_repo.interrupted()
                options = (
                    BatchOptions.model_validate_json(batch_repo.run(args.resume)["options_json"])
                    if args.resume
                    else BatchOptions(
                        pipeline="unified",
                        conversion=batch_repo.conversion_settings(),
                        recipe_limit=args.limit,
                        usda=not args.no_usda,
                    )
                )
                preflight(options)
                if args.resume:
                    batch_repo.resume(args.resume)
                run_id = args.resume or batch_repo.create_run(options)
                print(f"批次：{run_id}；进度可在后台查看", flush=True)
                execute(batch_repo, run_id)
                result = batch_repo.run(run_id)
                result["failed"] = sum(i["status"] == "failed" for i in batch_repo.items(run_id))
            case "init":
                result = {"database": str(repo.path)}
            case "convert":
                result = {
                    "status": convert_one(
                        repo, args.recipe_id, feedback=args.feedback, force=True, with_nutrition=args.with_nutrition
                    )
                }
            case "import":
                result = import_directory(repo, args.source, args.dataset)
            case "export":
                result = {"exported": str(export_json(repo, not args.all, args.stage))}
            case "clear":
                confirmation = args.confirm or input("此操作清空所有菜谱、原文历史和审核记录。请输入 CLEAR：")
                repo.clear(confirmation)
                result = {"cleared": True}
            case _:
                result = repo.dashboard()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1 if result.get("failed", 0) else 0
    except Exception as exc:
        log_event("command_failed", error=exc)
        print(f"错误：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
