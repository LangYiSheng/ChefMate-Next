"""Completed cooking becomes personal intake only with a known personal portion.

The single-diner completion policy is explicit application behavior. Shared meals
remain pending until the user supplies their own share. Nutrition always comes
from the frozen cooking snapshot, never numbers copied back by the model.
"""

import hashlib
import json
import math
import re
from typing import Annotated
from uuid import NAMESPACE_URL, uuid5

from pydantic import Field

from .meal_logging import CHAT_COMPONENT_PRESENTATION, FoodLogModel, MACROS, _encoded, _turn_guard


class CookedMealIdentity(FoodLogModel):
    entry_id: Annotated[str, Field(pattern=r"^(?:[a-f0-9]{32})?$")] = ""


class CookedPortion(FoodLogModel):
    recipe_id: Annotated[str, Field(min_length=1, max_length=100)]
    fraction: Annotated[float, Field(ge=0, le=1,
        description="本人吃掉这整道菜的比例，0表示没吃、0.5表示半盘、1表示整盘。不是每道菜的配方份数。")]
    evidence: Annotated[str, Field(min_length=1, max_length=300,
        description="逐字引用本轮本人食用比例的原话，如我吃了一半、每道菜按五人均分吃了一份；一句只表达一个比例。")]


class RecordCookedMealIntake(FoodLogModel):
    entry_id: Annotated[str, Field(pattern=r"^[a-f0-9]{32}$")]
    source_revision: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    expected_revision: Annotated[int, Field(ge=0)]
    portions: Annotated[list[CookedPortion], Field(min_length=1, max_length=30,
        description="逐道菜给出本人实际比例；必须包含本次所有菜，没有吃的明确传0。不要按人数擅自均分。")]


def entry_identity(session_id, meal_id):
    return hashlib.sha256(f"{session_id}:{meal_id}".encode()).hexdigest()[:32]


def _revision(entry):
    return hashlib.sha256(_encoded({key: entry[key] for key in (
        "id", "meal_id", "completed_at", "diners", "recipes")} ).encode()).hexdigest()


def _numeric(value):
    return type(value) in {int, float} and math.isfinite(value) and value >= 0


def _chinese_number(text):
    if re.fullmatch(r"\d+(?:\.\d+)?", text):
        return float(text)
    digits = dict(zip("零一二两三四五六七八九", (0, 1, 2, 2, 3, 4, 5, 6, 7, 8, 9)))
    if text in digits:
        return float(digits[text])
    if text == "百":
        return 100.0
    if "十" in text:
        left, right = text.split("十", 1)
        if (not left or left in digits) and (not right or right in digits):
            return float((digits[left] if left else 1) * 10 + (digits[right] if right else 0))
    return None


def explicit_fractions(evidence, diners):
    """Recognize common portion expressions; ambiguous weights stay a question."""
    values = set()
    number = r"[零一二两三四五六七八九十百\d.]+"
    for denominator, numerator in re.findall(fr"({number})分之({number})", evidence):
        divisor, part = _chinese_number(denominator), _chinese_number(numerator)
        if divisor and part is not None:
            values.add(round(part / divisor, 8))
    for numerator, denominator in re.findall(r"(\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)", evidence):
        if float(denominator):
            values.add(round(float(numerator) / float(denominator), 8))
    for percent in re.findall(r"(\d+(?:\.\d+)?)\s*[%％]", evidence):
        values.add(round(float(percent) / 100, 8))
    for percent in re.findall(fr"百分之({number})", evidence):
        value = _chinese_number(percent)
        if value is not None:
            values.add(round(value / 100, 8))
    # Remove fractions before treating 半 or 全 as standalone portion evidence.
    if re.search(r"一半|半盘|半份|半碗|半锅|半道", evidence):
        values.add(0.5)
    if re.search(r"没(?:有)?(?:吃|喝)|未(?:吃|喝)|一口没|完全没吃|没有食用", evidence):
        values.add(0.0)
    if re.search(r"整(?:盘|锅|道)(?![^，,。；;\n]{0,24}(?:分之|[%％]|一半|半盘|半份|半碗))"
                 r"|全(?:部)?(?:吃|喝)(?:完|了)|(?:吃|喝)了?全部|全吃了", evidence):
        values.add(1.0)
    if re.search(r"均分|平分|平均分", evidence):
        explicit_diners = [_chinese_number(count) for count in re.findall(fr"({number})(?:个人|人|位).{{0,8}}?(?:均分|平分|平均分)", evidence)]
        counts = explicit_diners or [diners]
        for count in counts:
            if _numeric(count) and count >= 1:
                values.add(round(1 / count, 8))
    return values


def _dish_aliases(name):
    name = re.sub(r"\s+", "", name)
    aliases = {name}
    shorter = re.sub(r"^(?:测试|验收|家常|清炒|清蒸|香煎|红烧|炒|蒸|煎|炖)", "", name)
    if len(shorter) >= 2:
        aliases.add(shorter)
    return {alias for alias in aliases if alias}


def validate_portion_evidence(portions, user_text, diners, recipes=()):
    if re.search(r"(?:假设|如果|要是|假如|假若|倘若|若是|举例|示例|引用|原话)", user_text):
        raise ValueError("请先询问用户本人实际吃了多少；疑问、假设或引用不能当作食用份额")
    # A request to correct the existing meal without creating a duplicate is
    # permission to revise, not a refusal to record the reported portion.
    registration_text = re.sub(r"(?:不要|不用|先别|暂不|别|不)(?:再)?(?:重复|新增|另建|新建)"
                               r"(?:一[条份餐])?(?:饮食|个人|营养)?(?:登记|记录|保存|入账)", "", user_text)
    if re.search(r"(?:不|别|暂不|先别|不要|不用).{0,6}(?:登记|记录|保存|入账)", registration_text):
        raise ValueError("用户尚未允许登记这次个人份额")
    for portion in portions:
        if portion.evidence not in user_text:
            raise ValueError("食用比例依据必须逐字引用本轮用户原话")
        clauses = [clause for clause in re.split(r"[，,。；;\n]", user_text) if portion.evidence in clause] or [portion.evidence]
        def named_in(text):
            normalized = re.sub(r"\s+", "", text)
            return {recipe["recipe_id"] for recipe in recipes
                    if any(alias in normalized for alias in _dish_aliases(recipe["name"]))}
        evidence_names = re.sub(r"\s+", "", portion.evidence)
        named_dishes = named_in(evidence_names)
        if not named_dishes:
            named_dishes = set().union(*(named_in(clause) for clause in clauses))
            shared = bool(re.search(r"每道|每盘|所有菜|全部菜|这些菜|均分|平分|平均分|都吃|各吃", portion.evidence))
            if len(named_dishes) > 1 and not shared:
                raise ValueError("同句包含多道菜，份额引用需带上对应菜名，不能只截取比例")
            if len(recipes) > 1 and not named_dishes and not shared and portion.fraction != 0:
                raise ValueError("请先明确这是每道菜的共同份额，还是分别说明菜名和比例")
        if named_dishes and portion.recipe_id not in named_dishes:
            raise ValueError("这段份额依据说的是另一道菜，请逐道核对真实菜名与recipe_id，不能调换营养")
        if any(re.search(r"[?？]|吗|么|如何|怎么", clause) for clause in clauses):
            raise ValueError("食用比例仍是问题，请先询问用户本人实际吃了多少")
        if any(re.search(r"原本|本来|计划|打算|准备吃|想吃|想喝", clause) for clause in clauses):
            raise ValueError("计划或原本想吃的份额不是实际食用量，请引用用户实际吃的比例")
        if portion.fraction > 0 and any(re.search(r"(?:没(?:有)?|不是|并非|并未|尚未|未|不).{0,12}(?:吃|喝|食用|均分|平分)", clause)
                                        for clause in clauses):
            raise ValueError("被否定的食用份额不能作为实际摄入，请引用后来实际吃的比例")
        for clause in clauses:
            other_diner = re.search(r"(?:妈妈|爸爸|孩子|家人|朋友|室友|同事|老公|老婆|儿子|女儿|别人|客人|他们|她们|大家|[他她]).{0,5}(?:吃|喝|食用)", clause)
            personal = re.search(r"我(?:自己|本人)?(?:大概|差不多|约|实际|后来|最后|总共|一共|只|也|都|各|才|还|已经)?(?:吃|喝|食用|按|分到)"
                                 r"|我(?:和|跟|与).{1,10}(?:各|都|一起)(?:吃|喝)", clause)
            if other_diner and not personal:
                raise ValueError("这里说的是其他人的份额，请先询问用户本人实际吃了多少")
        if re.search(r"[\"“‘「『`][^\"”’」』`]{0,300}" + re.escape(portion.evidence)
                     + r"[^\"”’」』`]{0,300}[\"”’」』`]", user_text):
            raise ValueError("引用里的食用份额不能当作用户实际摄入")
        fractions = explicit_fractions(portion.evidence, diners)
        if len(fractions) != 1 or not math.isclose(next(iter(fractions)), portion.fraction, rel_tol=0, abs_tol=1e-7):
            raise ValueError("比例与原话不一致或还不明确；请询问本人吃了整道菜的几分之几、百分比或是否按人数均分")


class CookedMealIntakeRepository:
    def __init__(self, food_logs):
        self.food_logs = food_logs
        with food_logs._connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS cooked_meal_intake ("
                       "entry_id TEXT PRIMARY KEY,source_revision TEXT NOT NULL,revision INTEGER NOT NULL,"
                       "state TEXT NOT NULL,food_log_id TEXT,portions TEXT NOT NULL)")

    @staticmethod
    def _source(db, identity):
        row = db.execute("SELECT * FROM food_journal WHERE id=?", (identity,)).fetchone()
        if not row or row["status"] != "completed":
            raise ValueError("这顿饭尚未实际完成，不能登记完成餐食的个人营养")
        entry = {**json.loads(row["snapshot"]), "id": row["id"], "session_id": row["session_id"], "date": row["day"]}
        if not entry.get("completed_at") or not entry.get("recipes"):
            raise ValueError("本次完成餐食缺少可核对的来源")
        return entry

    def _receipt(self, db, entry):
        ledger = db.execute("SELECT * FROM cooked_meal_intake WHERE entry_id=?", (entry["id"],)).fetchone()
        record = (self.food_logs._public(db.execute("SELECT * FROM personal_food_logs WHERE id=?", (ledger["food_log_id"],)).fetchone())
                  if ledger and ledger["food_log_id"] else None)
        state = ledger["state"] if ledger else "awaiting_portions"
        if record and record["status"] == "deleted" and state != "not_eaten":
            state = "deleted"
        elif record and record["status"] == "saved":
            state = "saved"
        revision = max(ledger["revision"] if ledger else 0, record["revision"] if record else 0)
        messages = {
            "awaiting_portions": "制作记录已保存，个人营养等待份额。请先问用户本人吃了哪些菜、各占整道菜多少；不要把多人份擅自均分或整桌入账。",
            "saved": "本次餐食已计入个人营养，按冻结配方与已记录份额估算，未知仍留空。用户更正份额时先重新读取，再修改同一记录，不另建草稿。",
            "not_eaten": "用户本次未食用，制作历史保留，未计入个人摄入。",
            "deleted": "这顿饭的个人记录已被移除，不会因刷新或重试自动恢复；如需重新登记请先确认本人食用份额。",
        }
        return {"status": "ok", "intake_status": state, "entry_id": entry["id"],
                "source_revision": _revision(entry), "expected_revision": revision, "diners": entry.get("diners"),
                "record": record if state == "saved" else None,
                "recipes": [{"recipe_id": recipe["recipe_id"], "name": recipe["name"],
                             "portion_reference": f"整道菜，配方等效 {recipe.get('target_servings') or recipe.get('servings') or '未知'} 份",
                             "nutrition": recipe.get("nutrition") or {}} for recipe in entry["recipes"]],
                "message": messages[state]}

    def get(self, identity):
        with self.food_logs._connect() as db:
            return self._receipt(db, self._source(db, identity))

    def completed(self, identity):
        """A real completion event may register a single diner once, never a read."""
        with self.food_logs._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            entry = self._source(db, identity)
            if db.execute("SELECT 1 FROM cooked_meal_intake WHERE entry_id=?", (identity,)).fetchone():
                return self._receipt(db, entry)
            if entry.get("diners") == 1:
                portions = [{"recipe_id": recipe["recipe_id"], "fraction": 1.0,
                             "evidence": "完成后按单人餐单自动登记"} for recipe in entry["recipes"]]
                self._save(db, entry, portions, "single_diner", 0)
            else:
                db.execute("INSERT INTO cooked_meal_intake VALUES(?,?,?,?,?,?)",
                           (identity, _revision(entry), 0, "awaiting_portions", None, "[]"))
            return self._receipt(db, entry)

    def record(self, identity, source_revision, expected_revision, portions, *, session_id, turn_id=None, guard=None):
        with self.food_logs._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            entry = self._source(db, identity)
            if _revision(entry) != source_revision:
                raise ValueError("餐食来源已变化，请重新读取后核对")
            current = self._receipt(db, entry)
            ids = [portion["recipe_id"] for portion in portions]
            if len(set(ids)) != len(ids) or set(ids) != {recipe["recipe_id"] for recipe in entry["recipes"]}:
                raise ValueError("必须逐道使用真实餐单ID提供本人份额；没吃的菜明确填0，不能遗漏或重复")
            if turn_id is not None:
                row = db.execute("SELECT state FROM sessions WHERE id=?", (session_id,)).fetchone()
                state = json.loads(row[0]) if row else {}
                if not state.get("running") or (state.get("active_turn") or {}).get("id") != turn_id:
                    raise ValueError("本轮已结束，没有登记个人份额")
            if guard:
                guard()
            # A repeated response with the same evidence is a no-op, including
            # native retries, while a genuinely new correction advances CAS.
            ledger = db.execute("SELECT * FROM cooked_meal_intake WHERE entry_id=?", (identity,)).fetchone()
            desired_state = "saved" if any(portion["fraction"] > 0 for portion in portions) else "not_eaten"
            if ledger and json.loads(ledger["portions"]) == portions and current["intake_status"] == desired_state:
                return current
            if current["expected_revision"] != expected_revision:
                raise ValueError("个人份额已变化，请重新读取最新版本后再保存")
            self._save(db, entry, portions, "reported_portions", expected_revision)
            if guard:
                guard()
            return self._receipt(db, entry)

    def _save(self, db, entry, portions, mode, revision):
        selected = {portion["recipe_id"]: portion for portion in portions}
        items = []
        for recipe in entry["recipes"]:
            portion = selected[recipe["recipe_id"]]
            fraction = portion["fraction"]
            if fraction == 0:
                continue
            totals = (recipe.get("nutrition") or {}).get("total", {})
            nutrition = {key: round(totals[key] * fraction, 3) if _numeric(totals.get(key)) else None for key in MACROS}
            items.append({"name": recipe["name"], "portion": f"整道菜的 {fraction * 100:.3f}%".replace(".000%", "%"),
                "portion_basis": "estimated" if mode == "single_diner" else "user_provided", "amount_g": None,
                "nutrition": nutrition, "nutrition_basis": "estimated" if any(value is not None for value in nutrition.values()) else "unknown",
                "evidence": f"已完成餐食的冻结配方 {recipe['recipe_id']}；份额依据：{portion['evidence']}",
                "assumptions": ["营养按制作时冻结配方和本人比例换算，实际吸油、烹调损耗与食用量可能不同。"]
                    + (["单人完成后按本次整份餐单自动登记，可告诉助手更正实际吃的比例。"] if mode == "single_diner" else [])})
        identity = "food-" + str(uuid5(NAMESPACE_URL, "chefmate:cooked:" + entry["id"]))
        stamp = self.food_logs._now().isoformat()
        if items:
            body = self.food_logs._body({"title": ("本次烹饪 · " + "、".join(recipe["name"] for recipe in entry["recipes"]))[:120],
                "source_kind": "text", "source_text": "已完成餐单，使用制作时冻结的菜谱与营养来源。", "date": entry["date"],
                "items": items, "notes": "完成后自动关联营养系统；个人份额可通过对话更正，制作历史独立保留。"})
            body["cooked_source"] = {"entry_id": entry["id"], "source_revision": _revision(entry), "mode": mode, "portions": portions}
            fingerprint = hashlib.sha256(_encoded(body).encode()).hexdigest()
            existing = db.execute("SELECT * FROM personal_food_logs WHERE id=?", (identity,)).fetchone()
            if existing:
                db.execute("UPDATE personal_food_logs SET status='saved',revision=?,updated_at=?,body=?,fingerprint=? WHERE id=?",
                           (revision + 1, stamp, _encoded(body), fingerprint, identity))
            else:
                db.execute("INSERT INTO personal_food_logs VALUES(?,?,?,?,?,?,?,?,?,?)", (
                    identity, "saved", revision + 1, entry["date"], stamp, stamp, entry["session_id"],
                    "cooked:" + entry["id"], fingerprint, _encoded(body)))
            status = "saved"
        else:
            existing = db.execute("SELECT * FROM personal_food_logs WHERE id=?", (identity,)).fetchone()
            if existing:
                body = json.loads(existing["body"])
                body["deleted_from"] = "saved"
                db.execute("UPDATE personal_food_logs SET status='deleted',revision=?,updated_at=?,body=? WHERE id=?",
                           (revision + 1, stamp, _encoded(body), identity))
            status = "not_eaten"
        db.execute("INSERT INTO cooked_meal_intake VALUES(?,?,?,?,?,?) ON CONFLICT(entry_id) DO UPDATE SET "
                   "revision=excluded.revision,state=excluded.state,food_log_id=excluded.food_log_id,portions=excluded.portions",
                   (entry["id"], _revision(entry), revision + 1, status, identity if items or existing else None, _encoded(portions)))


def make_cooked_intake_tools(journal):
    from chefmate_agent import InjectedToolSpec

    def read(values, context):
        request = CookedMealIdentity.model_validate(values)
        host = context.host_context
        identity = request.entry_id
        if not identity:
            binding = getattr(host, "binding", None)
            if binding is None:
                return {"status": "session_required", "message": "请提供真实已完成餐食ID，或在本次做菜对话里读取。"}
            record = binding.service(host.session_id).meal_record()
            if not record or record.get("status") != "completed":
                return {"status": "meal_not_completed", "message": "本次餐食尚未完成，不能登记完成餐食营养。"}
            identity = entry_identity(host.session_id, record["meal_id"])
        try:
            return journal.cooked_intake.get(identity)
        except ValueError as error:
            return {"status": "cooked_meal_unavailable", "message": str(error)}

    def record(values, context):
        request = RecordCookedMealIntake.model_validate(values)
        host = context.host_context
        if not host or not getattr(host, "session_id", None) or not getattr(host, "turn_id", None):
            return {"status": "session_required", "message": "需要在当前对话中登记本人份额。"}
        latest = next((index for index in range(len(context.messages) - 1, -1, -1)
                       if context.messages[index].get("role") == "user"), -1)
        user_text = context.messages[latest].get("content", "") if latest >= 0 else ""
        read_receipt = None
        for message in reversed(context.messages[latest + 1:]):
            if message.get("role") != "tool" or message.get("name") != "get_cooked_meal_intake":
                continue
            try:
                receipt = json.loads(message.get("content", ""))
            except (TypeError, ValueError):
                continue
            if (isinstance(receipt, dict) and receipt.get("status") == "ok" and receipt.get("entry_id") == request.entry_id
                    and receipt.get("source_revision") == request.source_revision
                    and receipt.get("expected_revision") == request.expected_revision):
                read_receipt = receipt
                break
        if read_receipt is None:
            return {"status": "read_required", "message": "请先在本轮get_cooked_meal_intake读取真实餐单、source_revision和expected_revision，再登记或修正同一餐。"}
        try:
            validate_portion_evidence(request.portions, user_text, read_receipt.get("diners"), read_receipt.get("recipes", []))
            binding = getattr(host, "binding", None)
            return journal.cooked_intake.record(request.entry_id, request.source_revision, request.expected_revision,
                [portion.model_dump(mode="json") for portion in request.portions], session_id=host.session_id,
                turn_id=host.turn_id if binding is not None and hasattr(binding.repository, "path") else None,
                guard=_turn_guard(host))
        except ValueError as error:
            return {"status": "invalid_cooked_portions", "message": str(error)}

    return [InjectedToolSpec("get_cooked_meal_intake", CHAT_COMPONENT_PRESENTATION
        + "读取已完成餐食的个人营养登记状态与冻结菜谱。entry_id省略时读取当前对话这次完成餐食；其他餐必须使用真实历史餐食ID。"
          "单人完成会自动登记；多人或人数未知需先问本人吃哪些菜、各占整道菜多少，未回答不能按人数擅自均分。",
        CookedMealIdentity, read, required_skill="food-journal"),
        InjectedToolSpec("record_cooked_meal_intake", CHAT_COMPONENT_PRESENTATION
        + "用户明确给出本人实际食用比例后，先本轮get_cooked_meal_intake读取，再传原样entry_id、source_revision、expected_revision和每道真实菜的比例及本轮原话。"
          "营养由服务端按冻结配方计算，直接计入个人营养，无需再新建草稿；更正已自动记录的份额也更新同一条。"
          "每道菜必须有比例，没吃填0。支持一半、几分之几、百分比、明确说均分一份；仅克重或碗数不够判断整盘比例时先询问。",
        RecordCookedMealIntake, record, effect="write", required_skill="food-journal",
        side_effects=("按用户实际份额登记或修正同一餐个人营养",))]
