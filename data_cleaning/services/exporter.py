"""Publish complete immutable export batches; manifest marks a successful batch."""

import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from data_cleaning.config import ROOT
from data_cleaning.models.repository import Repository
from data_cleaning.services.logging_service import log_event


def export_json(repo: Repository, approved_only: bool = True, stage: str = "final") -> Path:
    snapshot = (
        repo.conversion_export(approved_only)
        if stage == "unified"
        else (
            Repository.export_snapshot(repo, approved_only)
            if stage == "extraction"
            else repo.export_snapshot(approved_only)
        )
    )
    metadata = snapshot.pop("_meta", {})
    batch = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "_" + uuid.uuid4().hex[:8]
    target = ROOT / "data/exported" / batch
    target.mkdir(parents=True)
    hashes = {}
    for name, value in snapshot.items():
        raw = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8")
        file = target / f"{name}.json"
        temporary = file.with_suffix(".tmp")
        temporary.write_bytes(raw)
        temporary.replace(file)
        hashes[file.name] = hashlib.sha256(raw).hexdigest()
    manifest = {
        "schema_version": 2 if metadata else 1,
        "phase": metadata.get("phase", 1),
        "approved_only": approved_only,
        "ready_for_recommendation": metadata.get("ready_for_recommendation", False),
        "recipe_count": len(snapshot["recipes"]),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "sha256": hashes,
        "notice": metadata.get(
            "notice", "最终导出须通过提取、食材、标准化、USDA 审核且营养完整；诊断导出不能用于主项目。"
        ),
        "filter_report": metadata,
    }
    (target / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    log_event("export_completed")
    return target
