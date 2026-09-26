import hashlib
from collections import Counter
from pathlib import Path

from data_cleaning.models.repository import Repository
from data_cleaning.schemas.recipe import Status
from data_cleaning.services.howtocook import PARSER_VERSION, discover, parse
from data_cleaning.services.logging_service import log_event


def import_directory(repo: Repository, directory: Path, dataset: str = "HowToCook") -> dict[str, int]:
    directory = directory.resolve()
    if not dataset.strip():
        raise ValueError("数据集标识不能为空")
    counts: Counter = Counter(imported=0, skipped=0, failed=0)
    files = discover(directory)
    # Identity is independent of checkout location and stable when passing dishes/ directly.
    base = directory / "dishes" if (directory / "dishes").is_dir() else directory
    for path in files:
        source = f"{dataset}/{path.relative_to(base).as_posix()}"
        recipe_id = hashlib.sha256(source.encode("utf-8")).hexdigest()[:24]
        try:
            raw = path.read_bytes()
            digest = hashlib.sha256(raw).hexdigest()
            try:
                text = raw.decode("utf-8-sig")
                error = None
            except UnicodeDecodeError:
                text, error = None, "文件不是有效 UTF-8；原始字节已保存，请修正源文件编码后重新导入"
            changed = repo.ingest(recipe_id, source, digest, raw, text, error)
            counts["failed" if error else "imported" if changed else "skipped"] += 1
            log_event("import_failed" if error else "imported" if changed else "import_skipped", recipe_id)
        except Exception as exc:
            counts["failed"] += 1
            log_event("import_exception", recipe_id, exc)
    return dict(counts)


def extract_all(
    repo: Repository, *, failed_only: bool = False, force: bool = False, recipe_id: str | None = None
) -> dict[str, int]:
    counts: Counter = Counter(extracted=0, skipped=0, failed=0)
    for brief in repo.list_recipes():
        if recipe_id and brief["recipe_id"] != recipe_id:
            continue
        if not force and brief["status"] == Status.REJECTED:
            counts["skipped"] += 1
            continue
        if failed_only and brief["extract_status"] != Status.FAILED:
            continue
        if not force and brief["extract_status"] not in (Status.PENDING, Status.FAILED):
            counts["skipped"] += 1
            continue
        row = None
        try:
            row = repo.get_recipe(brief["recipe_id"])
            if row["raw_text"] is None:
                raise ValueError("源文件无法解码；请先修复 UTF-8 编码并重新导入")
            recipe = parse(row["raw_text"], row["recipe_id"], row["source"])
            repo.save_extraction(recipe, row["updated_at"], action=PARSER_VERSION)
            counts["extracted"] += 1
            log_event("extracted", row["recipe_id"])
        except Exception as exc:
            counts["failed"] += 1
            log_event("extract_failed", brief["recipe_id"], exc)
            if row is None:
                continue
            try:
                repo.fail_extraction(row["recipe_id"], row["updated_at"], f"{type(exc).__name__}: {exc}")
            except Exception as write_exc:
                log_event("failure_record_conflict", row["recipe_id"], write_exc)
    return dict(counts)
