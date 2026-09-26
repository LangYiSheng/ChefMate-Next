from difflib import SequenceMatcher

from data_cleaning.models.catalog import CatalogRepository
from data_cleaning.schemas.cleaning import MatchChoice
from data_cleaning.services.logging_service import log_event
from data_cleaning.services.openai_service import OpenAIService


def candidates(name: str, standards: list[dict], limit: int = 8) -> list[dict]:
    scored = []
    for item in standards:
        score = max(SequenceMatcher(None, name, n).ratio() for n in [item["canonical_name"], *item["aliases"]])
        if score > 0:
            scored.append(
                {
                    "ingredient_id": item["ingredient_id"],
                    "canonical_name": item["canonical_name"],
                    "aliases": item["aliases"],
                    "score": round(score, 4),
                }
            )
    return sorted(scored, key=lambda c: (-c["score"], c["ingredient_id"]))[:limit]


def match_one(
    repo: CatalogRepository, raw_id: int, *, use_ai: bool = True, force: bool = False, ai: OpenAIService | None = None
) -> str:
    row = repo.raw_detail(raw_id)
    if not force and row["status"] in ("approved", "rejected"):
        return "skipped"
    standards = repo.standards(True)
    name = row["raw_name"]
    target, method = None, "unmatched"
    for item in standards:
        if name == item["canonical_name"]:
            target, method = item["ingredient_id"], "canonical"
            break
    if not target:
        for item in standards:
            if name in item["aliases"]:
                target, method = item["ingredient_id"], "alias"
                break
    if not target:
        valid = {s["ingredient_id"] for s in standards}
        for rule in repo.fixed_mappings():
            if rule["raw_name"] == name and rule["status"] == "approved" and rule["ingredient_id"] in valid:
                target, method = rule["ingredient_id"], "fixed"
                break
    if target:
        repo.match_raw(
            raw_id,
            target,
            expected=row["updated_at"],
            status="approved",
            method=method,
            confidence=1,
            reason="已审核词表精确匹配",
        )
        return "approved"
    options = candidates(name, standards)
    if not options or not use_ai:
        repo.match_raw(
            raw_id,
            None,
            expected=row["updated_at"],
            status="pending_review",
            method="candidates",
            candidates=options,
            reason="请人工选择或新增标准食材",
        )
        return "pending_review"

    def validate(choice: MatchChoice) -> None:
        if choice.selected_ingredient_id is not None and choice.selected_ingredient_id not in {
            c["ingredient_id"] for c in options
        }:
            raise ValueError("AI 选择了候选范围以外的 ID")

    try:
        choice = (ai or OpenAIService(repo)).request(
            "match", {"raw_name": name, "candidates": options}, MatchChoice, str(raw_id), validate
        )
        repo.match_raw(
            raw_id,
            choice.selected_ingredient_id,
            expected=row["updated_at"],
            status="pending_review",
            method="ai",
            confidence=choice.confidence,
            reason=choice.reason + (f"；建议名称：{choice.suggested_name}" if choice.suggested_name else ""),
            candidates=options,
        )
        return "pending_review"
    except Exception as exc:
        log_event("ingredient_match_failed", str(raw_id), exc)
        repo.match_raw(
            raw_id,
            None,
            expected=row["updated_at"],
            status="pending_review",
            method="ai",
            candidates=options,
            error="AI 匹配失败，请检查配置/服务记录后重试或人工确认",
        )
        return "failed"


def match_all(repo: CatalogRepository, *, use_ai: bool = True, failed_only: bool = False) -> dict[str, int]:
    counts = {"approved": 0, "pending_review": 0, "skipped": 0, "failed": 0}
    ai = OpenAIService(repo) if use_ai else None
    for raw in repo.ingredient_list():
        if failed_only and not repo.raw_detail(raw["id"])["error_message"]:
            continue
        try:
            counts[match_one(repo, raw["id"], use_ai=use_ai, ai=ai)] += 1
        except Exception as exc:
            log_event("ingredient_match_failed", str(raw["id"]), exc)
            counts["failed"] += 1
    return counts
