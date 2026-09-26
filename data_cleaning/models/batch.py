"""Persistent batch checkpoints; all batch SQL stays in the repository layer."""

import uuid

from data_cleaning.models.repository import encode, now
from data_cleaning.models.conversion import ConversionRepository
from data_cleaning.schemas.batch import BatchOptions


class BatchRepository(ConversionRepository):
    def create_run(self, options: BatchOptions) -> str:
        run_id = uuid.uuid4().hex
        recipes = self.list_recipes()
        recipes = recipes[: options.recipe_limit] if options.recipe_limit else recipes
        with self.connection(write=True) as db:
            if db.execute("SELECT 1 FROM batch_runs WHERE status IN ('queued','running','stopping')").fetchone():
                raise ValueError("已有批量任务，请先停止或恢复中断任务")
            db.execute(
                "INSERT INTO batch_runs(id,status,options_json,recipe_ids_json,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                (run_id, "queued", options.model_dump_json(), encode([r["recipe_id"] for r in recipes]), now(), now()),
            )
        return run_id

    def runs(self) -> list[dict]:
        with self.connection() as db:
            return [dict(r) for r in db.execute("SELECT * FROM batch_runs ORDER BY created_at DESC LIMIT 30")]

    def run(self, run_id: str) -> dict:
        with self.connection() as db:
            row = db.execute("SELECT * FROM batch_runs WHERE id=?", (run_id,)).fetchone()
            if not row:
                raise ValueError("批次不存在")
            return dict(row)

    def set_run(self, run_id: str, status: str, error: str | None = None) -> None:
        with self.connection(write=True) as db:
            db.execute(
                "UPDATE batch_runs SET status=?,error_message=?,updated_at=? WHERE id=?", (status, error, now(), run_id)
            )

    def request_stop(self, run_id: str) -> None:
        with self.connection(write=True) as db:
            db.execute(
                "UPDATE batch_runs SET status='stopping',updated_at=? WHERE id=? AND status IN ('queued','running')",
                (now(), run_id),
            )

    def resume(self, run_id: str) -> None:
        with self.connection(write=True) as db:
            if db.execute("SELECT 1 FROM batch_runs WHERE status IN ('queued','running','stopping')").fetchone():
                raise ValueError("已有任务运行；若进程中断，请先恢复中断状态")
            db.execute(
                "UPDATE batch_runs SET status='queued',error_message=NULL,updated_at=? WHERE id=?", (now(), run_id)
            )

    def interrupted(self) -> None:
        # Caller must hold the worker lock, proving no worker is processing this DB.
        with self.connection(write=True) as db:
            db.execute(
                "UPDATE batch_runs SET status='paused',updated_at=? WHERE status IN ('queued','running','stopping')",
                (now(),),
            )

    def items(self, run_id: str) -> list[dict]:
        with self.connection() as db:
            return [
                dict(r)
                for r in db.execute("SELECT * FROM batch_items WHERE run_id=? ORDER BY updated_at DESC", (run_id,))
            ]

    def checkpoint(
        self, run_id: str, stage: str, entity: str, version: str, status: str, result=None, error=None
    ) -> None:
        with self.connection(write=True) as db:
            db.execute(
                "INSERT INTO batch_items VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(run_id,stage,entity_id) DO UPDATE SET version=excluded.version,status=excluded.status,result_json=excluded.result_json,error_message=excluded.error_message,updated_at=excluded.updated_at",
                (run_id, stage, entity, version, status, encode(result), error, now()),
            )
            db.execute(
                "UPDATE batch_runs SET stage=?,current_item=?,updated_at=? WHERE id=?", (stage, entity, now(), run_id)
            )

    def raw_scope(self, recipe_ids: list[str]) -> list[dict]:
        selected = set(recipe_ids)
        with self.connection() as db:
            ids = {
                r[0]
                for r in db.execute("SELECT raw_ingredient_id,recipe_id FROM recipe_ingredients")
                if r[1] in selected
            }
        return [self.raw_detail(raw_id) for raw_id in sorted(ids)]

    def set_query(self, ingredient_id: str, query: str, expected: str) -> None:
        with self.connection(write=True) as db:
            count = db.execute(
                "UPDATE standard_ingredients SET usda_query=? WHERE ingredient_id=? AND updated_at=?",
                (query, ingredient_id, expected),
            ).rowcount
            if not count:
                raise ValueError("标准食材已变化，请重试")

    def save_usda_candidates(
        self, ingredient_id: str, candidates: list, confidence: float, reason: str, expected: str
    ) -> None:
        with self.connection(write=True) as db:
            count = db.execute(
                "UPDATE standard_ingredients SET nutrition_candidates_json=?,nutrition_confidence=?,nutrition_reason=? WHERE ingredient_id=? AND updated_at=?",
                (encode(candidates), confidence, reason, ingredient_id, expected),
            ).rowcount
            if not count:
                raise ValueError("标准食材已变化，请重试")
            self._audit(
                db,
                None,
                "batch_usda_candidates",
                {"ingredient_id": ingredient_id, "candidates": candidates, "confidence": confidence, "reason": reason},
            )
