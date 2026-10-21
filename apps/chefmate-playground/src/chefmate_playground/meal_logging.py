"""Personal food intake drafts: provenance first, user confirmation before counting."""

import hashlib
import json
import re
import sqlite3
from datetime import date as Day, datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

LOCAL_ZONE = timezone(timedelta(hours=8))
MACROS = ("calories_kcal", "protein_g", "fat_g", "carbohydrate_g", "sugar_g", "sodium_mg")
INTAKE_NOTICE = "仅汇总已确认的个人饮食记录；照片和描述中的份量、营养可能含估算，不能替代实际称量。未记录不代表没有吃，未知值不补零。"
CHAT_COMPONENT_PRESENTATION = ("本工具的真实回执会由应用在当前对话渲染饮食/营养组件，自动展示食物清单、份量、数值和状态。"
                               "你的最终正文只用一句自然说明或必要建议；不要重复表格、数字清单或逐条记录，不要求用户切换页面。")


class FoodLogModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class FoodLogNutrition(FoodLogModel):
    calories_kcal: Annotated[float | None, Field(ge=0, le=20000)] = None
    protein_g: Annotated[float | None, Field(ge=0, le=3000)] = None
    fat_g: Annotated[float | None, Field(ge=0, le=3000)] = None
    carbohydrate_g: Annotated[float | None, Field(ge=0, le=3000)] = None
    sugar_g: Annotated[float | None, Field(ge=0, le=3000)] = None
    sodium_mg: Annotated[float | None, Field(ge=0, le=100000)] = None


class FoodLogItem(FoodLogModel):
    name: Annotated[str, Field(min_length=1, max_length=120)]
    portion: Annotated[str, Field(max_length=160)] = ""
    amount_g: Annotated[float | None, Field(gt=0, le=10000)] = None
    portion_basis: Literal["unknown", "user_provided", "estimated"] = "unknown"
    nutrition: FoodLogNutrition = Field(default_factory=FoodLogNutrition)
    nutrition_basis: Literal["unknown", "user_provided", "estimated"] = "unknown"
    evidence: Annotated[str, Field(max_length=1000, description="user_provided营养时必须逐字复制本轮用户原文中的连续片段，保持标点，不加外层引号或解释；estimated时说明推算依据。")] = ""
    assumptions: Annotated[list[Annotated[str, Field(min_length=1, max_length=400)]], Field(max_length=12)] = Field(default_factory=list)

    @model_validator(mode="after")
    def evidence_matches_precision(self):
        self.name = self.name.strip()
        self.portion = self.portion.strip()
        if not self.name:
            raise ValueError("请写出识别到的食物名称")
        values = self.nutrition.model_dump()
        has_values = any(value is not None for value in values.values())
        if self.portion_basis == "unknown" or not self.portion:
            if self.amount_g is not None or has_values:
                raise ValueError("份量未知时请保留克重和营养为空，先由用户确认")
            self.portion_basis = "unknown"
        if self.nutrition_basis == "unknown" and has_values:
            raise ValueError("请明确营养数值来自用户还是估算")
        if self.nutrition_basis != "unknown" and not has_values:
            self.nutrition_basis = "unknown"
        if has_values and not self.evidence.strip():
            raise ValueError("营养数值需要来源说明")
        if (self.portion_basis == "estimated" or has_values and self.nutrition_basis == "estimated") and not self.assumptions:
            raise ValueError("估算份量或营养需要说明假设，例如碗的大小、烹饪用油或食材组成")
        return self


class FoodLogDraftRequest(FoodLogModel):
    title: Annotated[str, Field(min_length=1, max_length=120)]
    meal_type: Literal["breakfast", "lunch", "dinner", "snack", "unspecified"] = "unspecified"
    date: Annotated[str | None, Field(pattern=r"^\d{4}-\d{2}-\d{2}$")] = None
    date_evidence: Annotated[str, Field(max_length=160, description="日期来源的本轮原话连续片段，如今天、昨天或2026-10-20；不加外层引号和解释。默认date=null时留空。")] = ""
    source_kind: Literal["text", "image"] = "text"
    source_text: Annotated[str, Field(max_length=8000, description="文字来源直接复制本轮用户消息中的连续原文，保留标点；修订时沿用get_food_log读取的旧原文，除非用户明确更正。")] = ""
    items: Annotated[list[FoodLogItem], Field(min_length=1, max_length=30)]
    notes: Annotated[str, Field(max_length=2000)] = ""

    @model_validator(mode="after")
    def clean_values(self):
        self.title = self.title.strip()
        if not self.title:
            raise ValueError("请给这次饮食记录写一个名称")
        if self.date is not None:
            Day.fromisoformat(self.date)
        return self


class FoodLogSearchRequest(FoodLogModel):
    query: Annotated[str, Field(max_length=200)] = ""
    status: Literal["draft", "saved", "all"] = "saved"
    days: Annotated[int, Field(ge=1, le=366)] = 7
    limit: Annotated[int, Field(ge=1, le=10)] = 6
    offset: Annotated[int, Field(ge=0, le=100000)] = 0


class FoodLogIdentityRequest(FoodLogModel):
    id: Annotated[str, Field(pattern=r"^food-[a-f0-9-]{36}$")]


class ReviseFoodLogDraftRequest(FoodLogIdentityRequest):
    revision: Annotated[int, Field(ge=1)]
    record: FoodLogDraftRequest


class ConfirmFoodLogRequest(FoodLogIdentityRequest):
    revision: Annotated[int, Field(ge=1)]
    confirmation_evidence: Annotated[str, Field(min_length=1, max_length=300,
        description="逐字复制本轮用户明确确认保存/记录/入账这份饮食的原话；不能使用历史或助手生成的文字。")]


def _encoded(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True)


def _nutrition(items):
    totals, known_total, coverage = {}, {}, {}
    estimated_fields = []
    for key in MACROS:
        known = [item["nutrition"][key] for item in items if item["nutrition"].get(key) is not None]
        known_total[key] = round(sum(known), 3) if known else None
        totals[key] = known_total[key] if items and len(known) == len(items) else None
        coverage[key] = {"known": len(known), "total": len(items)}
        if any(item["nutrition"].get(key) is not None and item["nutrition_basis"] == "estimated" for item in items):
            estimated_fields.append(key)
    return {"total": totals, "known_total": known_total, "coverage": coverage,
            "estimated": bool(estimated_fields), "estimated_fields": estimated_fields,
            "incomplete": any(value is None for value in totals.values()), "notice": INTAKE_NOTICE}


def intake_summary(records):
    nutrition = _nutrition([{"nutrition": record["nutrition"]["total"],
                              "nutrition_basis": "estimated" if record["nutrition"]["estimated"] else "user_provided"}
                             for record in records])
    # Partial known foods remain visible without making an incomplete record look complete.
    nutrition["known_total"] = {key: round(sum(values), 3) if values else None for key in MACROS
        for values in [[record["nutrition"]["known_total"][key] for record in records
                        if record["nutrition"]["known_total"][key] is not None]]}
    nutrition.update(record_count=len(records), food_count=sum(len(record["document"]["items"]) for record in records),
                     estimated_records=sum(record["nutrition"]["estimated"] for record in records),
                     estimated=any(record["nutrition"]["estimated"] for record in records),
                     estimated_fields=sorted({key for record in records for key in record["nutrition"]["estimated_fields"]}))
    return nutrition


class FoodLogRepository:
    def __init__(self, path: Path | str, *, now=None):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._now = now or (lambda: datetime.now(timezone.utc))
        with self._connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS personal_food_logs ("
                       "id TEXT PRIMARY KEY,status TEXT NOT NULL,revision INTEGER NOT NULL,day TEXT NOT NULL,"
                       "created_at TEXT NOT NULL,updated_at TEXT NOT NULL,source_session_id TEXT,"
                       "operation_id TEXT UNIQUE,fingerprint TEXT NOT NULL,body TEXT NOT NULL)")
            db.execute("CREATE INDEX IF NOT EXISTS personal_food_logs_day ON personal_food_logs(day,status)")

    def _connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    @staticmethod
    def _public(row):
        return ({**json.loads(row["body"]), "id": row["id"], "status": row["status"], "revision": row["revision"],
                 "date": row["day"], "created_at": row["created_at"], "updated_at": row["updated_at"],
                 "source_session_id": row["source_session_id"], "record_kind": "personal_intake"} if row else None)

    def _day(self, value):
        today = self._now().astimezone(LOCAL_ZONE).date()
        day = Day.fromisoformat(value) if value else today
        if day > today or day < today - timedelta(days=3660):
            raise ValueError("记录日期应在今天或之前十年内")
        return day.isoformat()

    def _body(self, values, *, image_ids=()):
        document = FoodLogDraftRequest.model_validate(values).model_dump(mode="json")
        document["date"] = self._day(document["date"])
        if document["source_kind"] == "image" and not image_ids:
            raise ValueError("本轮没有图片，不能标记为照片记录")
        return {"document": document, "nutrition": _nutrition(document["items"]),
                "source_image_ids": list(image_ids) if document["source_kind"] == "image" else []}

    def get(self, identity, *, include_deleted=False):
        with self._connect() as db:
            record = self._public(db.execute("SELECT * FROM personal_food_logs WHERE id=?", (identity,)).fetchone())
            return record if record and (include_deleted or record["status"] != "deleted") else None

    def create_draft(self, values, *, session_id=None, image_ids=(), operation_id=None, guard=None, active_turn_id=None):
        body = self._body(values, image_ids=image_ids)
        fingerprint = hashlib.sha256(_encoded([session_id, body]).encode()).hexdigest()
        stamp, identity = self._now().isoformat(), "food-" + str(uuid4())
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if active_turn_id is not None:
                row = db.execute("SELECT state FROM sessions WHERE id=?", (session_id,)).fetchone()
                state = json.loads(row[0]) if row else {}
                if not state.get("running") or (state.get("active_turn") or {}).get("id") != active_turn_id:
                    raise ValueError("本轮已结束，没有保存饮食草稿")
            if operation_id:
                row = db.execute("SELECT * FROM personal_food_logs WHERE operation_id=?", (operation_id,)).fetchone()
                if row:
                    if row["fingerprint"] != fingerprint:
                        raise ValueError("同一操作已保存其他饮食内容，请核对已有记录")
                    return self._public(row)
            if guard:
                guard()
            db.execute("INSERT INTO personal_food_logs VALUES(?,?,?,?,?,?,?,?,?,?)", (
                identity, "draft", 1, body["document"]["date"], stamp, stamp, session_id, operation_id, fingerprint, _encoded(body)))
            if guard:
                guard()
        return self.get(identity)

    def update_draft(self, identity, revision, values, *, guard=None, session_id=None, active_turn_id=None,
                     source_image_ids=None, allow_source_change=False):
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if active_turn_id is not None:
                row = db.execute("SELECT state FROM sessions WHERE id=?", (session_id,)).fetchone()
                state = json.loads(row[0]) if row else {}
                if not state.get("running") or (state.get("active_turn") or {}).get("id") != active_turn_id:
                    raise ValueError("本轮已结束，没有修改饮食草稿")
            record = self._public(db.execute("SELECT * FROM personal_food_logs WHERE id=?", (identity,)).fetchone())
            if not record:
                raise KeyError(identity)
            if type(revision) is not int or record["status"] != "draft" or record["revision"] != revision:
                raise ValueError("饮食草稿已更新或已确认，请刷新后核对")
            if not allow_source_change and values.get("source_kind", "text") != record["document"]["source_kind"]:
                raise ValueError("请保留原始来源类型")
            images = record["source_image_ids"] if source_image_ids is None else list(source_image_ids)
            if not set(record["source_image_ids"]).issubset(images):
                raise ValueError("原始图片来源不能移除")
            body = self._body(values, image_ids=images)
            body["source_image_ids"] = images  # Original provenance survives an explicit switch to a text source.
            if guard:
                guard()
            db.execute("UPDATE personal_food_logs SET body=?,day=?,revision=revision+1,updated_at=? WHERE id=?", (
                _encoded(body), body["document"]["date"], self._now().isoformat(), identity))
            if guard:
                guard()
        return self.get(identity)

    def publish(self, identity, revision, *, guard=None, session_id=None, active_turn_id=None):
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if active_turn_id is not None:
                row = db.execute("SELECT state FROM sessions WHERE id=?", (session_id,)).fetchone()
                state = json.loads(row[0]) if row else {}
                if not state.get("running") or (state.get("active_turn") or {}).get("id") != active_turn_id:
                    raise ValueError("本轮已结束，没有确认饮食记录")
            if guard:
                guard()
            record = self._public(db.execute("SELECT * FROM personal_food_logs WHERE id=?", (identity,)).fetchone())
            if not record:
                raise KeyError(identity)
            if record["status"] == "saved" and type(revision) is int and revision == record["revision"] - 1:
                return record
            if type(revision) is not int or record["status"] != "draft" or record["revision"] != revision:
                raise ValueError("饮食记录版本已变化，请刷新后核对")
            db.execute("UPDATE personal_food_logs SET status='saved',revision=revision+1,updated_at=? WHERE id=?",
                       (self._now().isoformat(), identity))
            if guard:
                guard()
        return self.get(identity)

    def delete(self, identity, revision):
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            record = self._public(db.execute("SELECT * FROM personal_food_logs WHERE id=?", (identity,)).fetchone())
            if not record:
                raise KeyError(identity)
            if record["status"] == "deleted" and type(revision) is int and revision == record["revision"] - 1:
                return record
            if type(revision) is not int or record["status"] not in {"draft", "saved"} or record["revision"] != revision:
                raise ValueError("饮食记录版本已变化，请刷新后核对")
            body = {key: record[key] for key in ("document", "nutrition", "source_image_ids")}
            body["deleted_from"] = record["status"]
            db.execute("UPDATE personal_food_logs SET status='deleted',revision=revision+1,updated_at=?,body=? WHERE id=?",
                       (self._now().isoformat(), _encoded(body), identity))
        return self.get(identity, include_deleted=True)

    def restore(self, identity, revision):
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            record = self._public(db.execute("SELECT * FROM personal_food_logs WHERE id=?", (identity,)).fetchone())
            if not record:
                raise KeyError(identity)
            if type(revision) is not int or record["status"] != "deleted" or record["revision"] != revision:
                raise ValueError("饮食记录版本已变化，请刷新后核对")
            body = {key: record[key] for key in ("document", "nutrition", "source_image_ids")}
            db.execute("UPDATE personal_food_logs SET status=?,revision=revision+1,updated_at=?,body=? WHERE id=?",
                       (record["deleted_from"], self._now().isoformat(), _encoded(body), identity))
        return self.get(identity)

    def list(self, *, status="saved", query="", days=30, limit=100, offset=0):
        if (status not in {"draft", "saved", "all", "deleted"} or type(days) is not int or not 1 <= days <= 366
                or type(limit) is not int or not 1 <= limit <= 1000 or type(offset) is not int or not 0 <= offset <= 100000
                or not isinstance(query, str) or len(query) > 200):
            raise ValueError("饮食记录查询条件无效")
        today = self._now().astimezone(LOCAL_ZONE).date()
        start = (today - timedelta(days=days - 1)).isoformat()
        with self._connect() as db:
            rows = [self._public(row) for row in db.execute("SELECT * FROM personal_food_logs WHERE day>=? AND day<=? "
                "ORDER BY day DESC,created_at DESC,id", (start, today.isoformat()))]
        records = [record for record in rows if (record["status"] in {"draft", "saved"} if status == "all" else record["status"] == status)
                   and (not query.strip() or query.strip().casefold() in _encoded(record["document"]).casefold())]
        return {"items": records[offset:offset + limit], "total": len(records), "offset": offset, "limit": limit}

    def summary(self, days=7):
        if type(days) is not int or not 1 <= days <= 366:
            raise ValueError("饮食汇总日期范围无效")
        today = self._now().astimezone(LOCAL_ZONE).date()
        start = today - timedelta(days=days - 1)
        with self._connect() as db:
            records = [self._public(row) for row in db.execute("SELECT * FROM personal_food_logs "
                "WHERE status='saved' AND day>=? AND day<=? ORDER BY day,id", (start.isoformat(), today.isoformat()))]
        daily = [{"date": (start + timedelta(days=offset)).isoformat(),
                  **intake_summary([record for record in records if record["date"] == (start + timedelta(days=offset)).isoformat()])}
                 for offset in range(days)]
        return {**intake_summary(records), "period": {"from": start.isoformat(), "to": today.isoformat(), "days": days},
                "days": daily, "today": daily[-1]}


def _date_matches_source(day, evidence, source, today):
    if not evidence or evidence not in source:
        return False
    if day in evidence or f"{Day.fromisoformat(day).year}年{Day.fromisoformat(day).month}月{Day.fromisoformat(day).day}日" in evidence:
        return True
    for phrase, offset in (("今天", 0), ("今日", 0), ("昨天", 1), ("昨日", 1), ("前天", 2)):
        if phrase in evidence and day == (today - timedelta(days=offset)).isoformat():
            return True
    return False


def _provided_nutrient(key, value, evidence):
    number = rf"(?<![\d.]){re.escape(f'{value:g}')}(?![\d.])"
    if key == "calories_kcal":
        return bool(re.search(number + r"\s*(?:kcal|千卡|大卡)", evidence, re.IGNORECASE))
    labels = {"protein_g": r"蛋白(?:质)?", "fat_g": "脂肪", "carbohydrate_g": r"碳水(?:化合物)?",
              "sugar_g": r"(?:糖|sugar)", "sodium_mg": r"(?:钠|sodium)"}
    units = r"(?:毫克|mg)" if key == "sodium_mg" else r"(?:克|g)"
    return bool(re.search(labels[key] + r"\s*[:：]?\s*" + number + r"\s*" + units, evidence, re.IGNORECASE))


def _validate_item_sources(items, user_text, baseline_items=()):
    remaining = list(baseline_items)
    for item in items:
        original = next((old for old in remaining if old["name"] == item.name), None)
        if original is not None:
            remaining.remove(original)  # An old verified item cannot authorize multiple new copies.
        quantity = (item.portion, item.amount_g, item.portion_basis)
        old_quantity = tuple(original[key] for key in ("portion", "amount_g", "portion_basis")) if original else None
        quantity_changed = quantity != old_quantity
        evidence_changed = bool(original and item.evidence != original["evidence"])
        if evidence_changed and (item.nutrition_basis == "user_provided"
                                 or item.portion_basis == "user_provided" and item.nutrition_basis == "unknown"):
            if not item.evidence or item.evidence not in user_text:
                raise ValueError("已核实的用户来源说明只能沿用原文或替换为本轮实际提供的原文")
        if item.portion_basis == "user_provided" and quantity_changed:
            if item.portion not in user_text:
                raise ValueError("用户提供的份量需引用本轮原话；视觉份量请标记为估算并说明假设")
            if item.amount_g is not None:
                grams = re.escape(f"{item.amount_g:g}")
                kilograms = re.escape(f"{item.amount_g / 1000:g}")
                if not re.search(rf"(?<![\d.])(?:{grams}\s*(?:克|g)|{kilograms}\s*(?:千克|kg))(?![a-z])", item.portion, re.IGNORECASE):
                    raise ValueError("用户未提供这项克重；从碗、盘推算的克重请标为估算")
        if item.nutrition_basis == "user_provided":
            numbers = item.nutrition.model_dump()
            changed = {key: number for key, number in numbers.items() if number is not None and (
                not original or quantity_changed or evidence_changed or original["nutrition_basis"] != "user_provided"
                or original["nutrition"].get(key) != number)}
            if changed:
                if not item.evidence or item.evidence not in user_text:
                    raise ValueError("营养evidence必须是本轮用户消息中逐字连续的一段原文；不要加外层引号、总结语或改动标点。按份量换算请标为估算")
                if any(not _provided_nutrient(key, number, item.evidence) for key, number in changed.items()):
                    raise ValueError("营养数值需与用户原文一致；估算请明确标记")


def _draft_receipt(record):
    receipt = {key: record[key] for key in ("id", "status", "revision", "date", "record_kind", "nutrition", "source_image_ids")}
    receipt.update(title=record["document"]["title"], food_count=len(record["document"]["items"]),
                   source_kind=record["document"]["source_kind"])
    return receipt


def _read_baseline(messages, identity, revision):
    for message in reversed(messages):
        if message.get("role") != "tool" or message.get("name") != "get_food_log":
            continue
        try:
            output = json.loads(message.get("content", ""))
        except (TypeError, ValueError):
            continue
        if not isinstance(output, dict):
            continue
        record = output.get("record")
        if (output.get("status") == "ok" and isinstance(record, dict) and record.get("id") == identity
                and record.get("revision") == revision and record.get("status") == "draft"):
            return record
    return None


def _latest_user_index(messages):
    return next((index for index in range(len(messages) - 1, -1, -1)
                 if messages[index].get("role") == "user"), -1)


def _previous_draft_receipt(messages, identity, revision):
    # A newly inferred or changed draft needs another human turn before it can
    # become intake. The caller cannot turn its own current-round tool result
    # into evidence that the user reviewed the inferred food and portions.
    latest = _latest_user_index(messages)
    user_text = messages[latest].get("content", "") if latest >= 0 else ""
    explicit_identity = identity in user_text
    for message in reversed(messages[:latest]):
        if message.get("role") != "tool" or message.get("name") not in {
                "create_food_log_draft", "get_food_log", "revise_food_log_draft"}:
            continue
        try:
            output = json.loads(message.get("content", ""))
        except (TypeError, ValueError):
            continue
        if not isinstance(output, dict) or output.get("status") != "ok":
            continue
        record = output.get("record") or output.get("draft")
        if not isinstance(record, dict) or record.get("status") != "draft":
            continue
        if record.get("id") == identity and record.get("revision") == revision:
            return record
        if not explicit_identity:
            return None  # A vague "this one" cannot approve an older, different draft.
    return None


def _explicit_confirmation(evidence, user_text, identity):
    if not evidence or evidence not in user_text:
        return False
    # Refuse negation, questions and quotes rather than finding an affirmative
    # substring inside "不要确认保存" or a question about how confirmation works.
    if re.search(r"(?:不|别|勿|先别|暂不|还没|尚未|取消|拒绝|不能|不要|不用)[^\n，,。；;]{0,8}(?:确认|保存|记录|入账)"
                 r"|(?:确认|保存|记录|入账).{0,5}(?:吗|么|能否|是否|要不要|[?？])"
                 r"|(?:如何|怎么|能否|是否|要不要).{0,12}(?:确认|保存|记录|入账)", user_text):
        return False
    if any(re.search(r"确认|保存|记录|入账", clause) and re.search(r"[?？]|吗|么", clause)
           for clause in re.split(r"[，,。；;\n]", user_text)):
        return False
    if re.search(r"(?:引用|原话|示例|举例|假设|如果)", user_text):
        return False
    if re.search(r"[\"“‘「『`][^\"”’」』`]{0,120}(?:确认|保存|记录|入账)[^\"”’」』`]{0,120}[\"”’」』`]", user_text):
        return False
    mentioned = set(re.findall(r"food-[a-f0-9-]{36}", user_text))
    if mentioned and identity not in mentioned:
        return False
    return (user_text.strip("。！! \n\t") in {"确认", "确定"}
            or bool(re.search(r"(?:确认|确定)(?:保存|记录|入账)|(?:保存|记录|入账)(?:这|刚才|当前|它|吧)|就按.{0,20}(?:保存|记录|入账)", evidence)))


def _turn_guard(host):
    def guard():
        if getattr(host, "cancelled", None) is not None and host.cancelled.is_set():
            raise ValueError("本轮已经停止，没有保存饮食草稿")
        binding = getattr(host, "binding", None)
        if binding is not None and not hasattr(binding.repository, "path"):
            state = binding.repository.get(host.session_id)
            if not state or not state.get("running") or (state.get("active_turn") or {}).get("id") != host.turn_id:
                raise ValueError("本轮已经结束，没有保存饮食草稿")
    return guard


def make_meal_logging_tools(journal):
    from chefmate_agent import InjectedToolSpec

    def create(values, context):
        host = context.host_context
        if host is None or not getattr(host, "session_id", None) or not getattr(host, "turn_id", None):
            return {"status": "session_required", "message": "需要在当前对话中记录这顿饭。"}
        latest = next((message for message in reversed(context.messages) if message.get("role") == "user"), {})
        user_text = latest.get("content", "")
        images = [image["id"] for image in latest.get("images", []) if isinstance(image, dict) and image.get("id")]
        binding = getattr(host, "binding", None)

        try:
            request = FoodLogDraftRequest.model_validate(values)
            if request.source_kind == "text":
                normalized = re.sub(r"\s+", "", request.source_text)
                if not normalized or normalized not in re.sub(r"\s+", "", user_text):
                    raise ValueError("文字来源需引用用户本轮实际提供的内容")
            if request.date is not None and not _date_matches_source(request.date, request.date_evidence, user_text,
                                                                     journal.food_logs._now().astimezone(LOCAL_ZONE).date()):
                raise ValueError("日期依据date_evidence必须是本轮原文中的连续片段，如今天；不要加解释或外层引号。不确定时date留null按今天记录")
            _validate_item_sources(request.items, user_text)
            record = journal.food_logs.create_draft(request.model_dump(mode="json"), session_id=host.session_id,
                image_ids=images, operation_id=f"{host.session_id}:{host.turn_id}:{context.call_id}", guard=_turn_guard(host),
                active_turn_id=host.turn_id if binding is not None and hasattr(binding.repository, "path") else None)
            return {"status": "ok", "draft": _draft_receipt(record),
                    "message": "草稿已整理，当前聊天组件会展示食物、份量与营养。等待用户核对或继续修改；明确确认保存后才计入个人记录。"}
        except ValueError as error:
            return {"status": "invalid_food_log", "message": str(error)}

    def read(values, context):
        request = FoodLogIdentityRequest.model_validate(values)
        record = journal.food_logs.get(request.id)
        if not record:
            return {"status": "not_found", "message": "饮食记录不存在或已经移入回收站。"}
        if len(_encoded(record)) > 40000:
            return {"status": "record_too_large", "id": record["id"], "revision": record["revision"],
                    "message": "这份记录较长，完整内容请在食光手记核对，未将截断内容作为修改依据。"}
        return {"status": "ok", "record": record,
                "message": "当前聊天组件会展示已读取的食物、份量与营养。draft可按此真实版本修改或等用户确认保存，saved已确认不可用草稿工具改写。"}

    def revise(values, context):
        host = context.host_context
        if host is None or not getattr(host, "session_id", None) or not getattr(host, "turn_id", None):
            return {"status": "session_required", "message": "需要在当前对话中修改这份饮食草稿。"}
        request = ReviseFoodLogDraftRequest.model_validate(values)
        baseline = _read_baseline(context.messages, request.id, request.revision)
        if baseline is None:
            return {"status": "read_required", "message": "请先调用get_food_log读取这份草稿的真实内容和revision，再修改同一个id；不要新建重复草稿。"}
        latest = next((message for message in reversed(context.messages) if message.get("role") == "user"), {})
        user_text = latest.get("content", "")
        original = baseline["document"]
        # Top-level provenance omitted by the model retains the actual read baseline.
        document = {**original, **request.record.model_dump(mode="json", exclude_unset=True)}
        images = list(baseline["source_image_ids"])
        current_images = [image["id"] for image in latest.get("images", []) if isinstance(image, dict) and image.get("id")]
        try:
            if document["source_kind"] != original["source_kind"]:
                if not re.search(r"(?:来源|记录).{0,8}(?:改|换|更正).{0,8}(?:照片|图片|文字)|(?:改为|改成)(?:照片|图片|文字)", user_text):
                    raise ValueError("没有本轮明确更改来源的要求，请保留草稿原始来源类型")
                if document["source_kind"] == "image":
                    if baseline["source_session_id"] != host.session_id:
                        raise ValueError("跨对话修改请保留原始来源；补充照片请回到原对话上传，避免图片来源失效")
                    if not current_images:
                        raise ValueError("改为照片来源需要本轮真实图片")
                    images = list(dict.fromkeys([*images, *current_images]))
                elif not document["source_text"] or document["source_text"] not in user_text:
                    raise ValueError("改为文字来源需要本轮实际提供的原文")
            if document["source_text"] != original["source_text"]:
                if not document["source_text"] or document["source_text"] not in user_text:
                    raise ValueError("来源原文只能保持原基线或改为本轮用户明确提供的文字")
            if document["date"] is None:
                document["date"] = original["date"]
            if document["date"] != original["date"] or document["date_evidence"] != original["date_evidence"]:
                if not _date_matches_source(document["date"], document["date_evidence"], user_text,
                                            journal.food_logs._now().astimezone(LOCAL_ZONE).date()):
                    raise ValueError("记录日期只能保持原基线或根据本轮明确日期更改")
            parsed = FoodLogDraftRequest.model_validate(document)
            _validate_item_sources(parsed.items, user_text, original["items"])
            binding = getattr(host, "binding", None)
            record = journal.food_logs.update_draft(request.id, request.revision, parsed.model_dump(mode="json"),
                guard=_turn_guard(host), session_id=host.session_id,
                active_turn_id=host.turn_id if binding is not None and hasattr(binding.repository, "path") else None,
                source_image_ids=images, allow_source_change=True)
            return {"status": "ok", "draft": _draft_receipt(record),
                    "message": "已修改原来的同一份饮食草稿，没有重复新建或入账。当前聊天组件会展示更新后的份量与营养，等待用户下一条明确确认。"}
        except KeyError:
            return {"status": "not_found", "message": "饮食草稿不存在或已经移除。"}
        except ValueError as error:
            return {"status": "invalid_food_log_revision", "message": str(error)}

    def confirm(values, context):
        host = context.host_context
        if host is None or not getattr(host, "session_id", None) or not getattr(host, "turn_id", None):
            return {"status": "session_required", "message": "需要在当前对话中确认饮食记录。"}
        request = ConfirmFoodLogRequest.model_validate(values)
        latest = _latest_user_index(context.messages)
        user_text = context.messages[latest].get("content", "") if latest >= 0 else ""
        if not _explicit_confirmation(request.confirmation_evidence, user_text, request.id):
            return {"status": "confirmation_required", "message": "请等待用户在本轮明确说确认保存这份饮食记录；问题、否定、引用和助手文字不能作为确认。"}
        if _previous_draft_receipt(context.messages, request.id, request.revision) is None:
            return {"status": "review_required", "message": "请先在对话展示这份草稿当前版本的食物、份量和营养，再等用户下一条消息确认。不能刚创建或修改就自动入账。"}
        if _read_baseline(context.messages[latest + 1:], request.id, request.revision) is None:
            return {"status": "read_required", "message": "请先在本轮get_food_log读取同一份草稿与revision，核对最新版本后再确认。"}
        try:
            binding = getattr(host, "binding", None)
            record = journal.food_logs.publish(request.id, request.revision, guard=_turn_guard(host),
                session_id=host.session_id,
                active_turn_id=host.turn_id if binding is not None and hasattr(binding.repository, "path") else None)
            summary = {key: value for key, value in journal.food_logs.summary(7).items() if key != "days"}
            return {"status": "ok", "record": _draft_receipt(record), "summary": summary,
                    "message": "这份饮食已按用户确认保存，现已计入个人营养统计，当前聊天组件会展示结果。估算和未知值保留，不代表当天完整摄入。"}
        except KeyError:
            return {"status": "not_found", "message": "饮食草稿不存在或已经移除。"}
        except ValueError as error:
            return {"status": "invalid_food_log_confirmation", "message": str(error)}

    def search(values, context):
        request = FoodLogSearchRequest.model_validate(values)
        records = journal.food_logs.list(**request.model_dump())
        items = [{"id": record["id"], "revision": record["revision"], "status": record["status"], "date": record["date"],
                  "title": record["document"]["title"], "meal_type": record["document"]["meal_type"],
                  "source_kind": record["document"]["source_kind"], "nutrition": record["nutrition"],
                  "food_count": len(record["document"]["items"]), "notes": record["document"]["notes"][:300],
                  "foods": [{"name": item["name"][:80], "portion": item["portion"][:100],
                             "portion_basis": item["portion_basis"], "nutrition_basis": item["nutrition_basis"]}
                            for item in record["document"]["items"][:4]]} for record in records["items"]]
        summary = {key: value for key, value in journal.food_logs.summary(request.days).items() if key != "days"}
        return {"status": "ok", **records, "items": items, "summary": summary,
                "message": "当前聊天组件会展示营养汇总与饮食列表。只有saved计入个人摄入，draft仍待用户核对；每条最多摘要4种食物，详情可用get_food_log读取。"
                           "汇总覆盖所选日期范围，制作量和个人摄入分开保存。"}

    return [InjectedToolSpec("create_food_log_draft", CHAT_COMPONENT_PRESENTATION + "把用户本轮的饭菜照片或文字整理为个人饮食草稿，用户明确确认后才计入统计。"
                "每项营养是用户自己吃的这一份的总量；份量未知时克重和营养留null。视觉/描述估算必须标estimated并写assumptions，不能说精确识别。"
                "source_kind=image仅可用本轮真实图片。date不确定留null按今天；历史日期需引用本轮date_evidence。不能把多人制作量当个人摄入。",
                FoodLogDraftRequest, create, effect="write", required_skill="food-journal", side_effects=("保存待用户核对的个人饮食草稿",)),
            InjectedToolSpec("search_food_logs", CHAT_COMPONENT_PRESENTATION + "按关键词和日期范围读取已确认的个人饮食记录、草稿及营养统计。默认saved，只读；"
                "宏量总数仅代表已确认记录，未知保留null；制作量和个人摄入分别记录。", FoodLogSearchRequest, search,
                required_skill="food-journal"),
            InjectedToolSpec("get_food_log", CHAT_COMPONENT_PRESENTATION + "按search_food_logs返回的真实id读取完整饮食记录、document与revision，修改草稿前必须调用。"
                "过长记录不会截断冒充完整内容。只有draft能修订，saved只读。", FoodLogIdentityRequest, read,
                required_skill="food-journal"),
            InjectedToolSpec("revise_food_log_draft", CHAT_COMPONENT_PRESENTATION + "用户说去掉酸奶、米饭减半等时，先get_food_log读取，再传同一个id、revision及完整record修改原草稿，"
                "不要create重复草稿。来源和日期沿用读取的基线，除非用户本轮明确更改。未改的旧用户数值可保留，新增/改动的用户数值须引用本轮原话；"
                "按原份量缩放的营养标estimated并写换算假设。工具不会发布或计入摄入；在对话展示结果，等用户下一条明确确认。",
                ReviseFoodLogDraftRequest, revise, effect="write", required_skill="food-journal",
                side_effects=("修改同一份待核对饮食草稿，未确认入账",)),
            InjectedToolSpec("confirm_food_log", CHAT_COMPONENT_PRESENTATION + "用户在已展示饮食草稿后明确说确认保存/记录/入账时，先在本轮get_food_log读取最新版本，"
                "再以真实id、revision及本轮逐字确认原话调用。新创建/修改的草稿需下一条用户消息确认；否定、问题、引用、旧确认不成立。"
                "成功后直接在对话展示已记录与最新个人营养，不要求切换食光手记。",
                ConfirmFoodLogRequest, confirm, effect="write", required_skill="food-journal",
                side_effects=("用户明确确认后将该饮食草稿计入个人营养统计",))]
