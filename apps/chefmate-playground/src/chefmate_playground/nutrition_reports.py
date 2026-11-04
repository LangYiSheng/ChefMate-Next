"""Calendar reports built exclusively from confirmed personal intake."""
import hashlib
import json
from datetime import date, timedelta

from .food_log_models import LOCAL_ZONE, intake_summary

CORE = ("calories_kcal", "protein_g", "fat_g", "carbohydrate_g")
LABELS = dict(zip(CORE, ("热量", "蛋白质", "脂肪", "碳水")))
MEALS = {"breakfast": "早餐", "lunch": "午餐", "dinner": "晚餐", "snack": "加餐", "unspecified": "未分类"}


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


class NutritionReports:
    def __init__(self, food_logs):
        self.logs = food_logs
        with self.logs._connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS nutrition_day_checks (day TEXT PRIMARY KEY, fingerprint TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS nutrition_report_reviews ("
                       "id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, body TEXT NOT NULL)")

    def records(self, start, end):
        with self.logs._connect() as db:
            return [self.logs._public(row) for row in db.execute(
                "SELECT * FROM personal_food_logs WHERE day>=? AND day<=? AND status IN ('saved','draft') ORDER BY day,id",
                (start.isoformat(), end.isoformat()))]

    @staticmethod
    def day_revision(records):
        return fingerprint([(r["id"], r["revision"], r["status"]) for r in records])

    def mark_day(self, day, complete, revision):
        selected = date.fromisoformat(day)
        if selected > self.logs._now().astimezone(LOCAL_ZONE).date():
            raise ValueError("不能确认未来日期")
        records = self.records(selected, selected)
        actual = self.day_revision(records)
        if revision != actual:
            raise ValueError("记录已变化，请刷新后确认")
        if complete and (not records or any(r["status"] == "draft" for r in records)):
            raise ValueError("请先核对当天的饮食记录")
        with self.logs._connect() as db:
            if complete:
                db.execute("INSERT INTO nutrition_day_checks(day,fingerprint) VALUES(?,?) "
                           "ON CONFLICT(day) DO UPDATE SET fingerprint=excluded.fingerprint", (day, actual))
            else:
                db.execute("DELETE FROM nutrition_day_checks WHERE day=?", (day,))

    def build(self, kind="daily", selected=None):
        today = self.logs._now().astimezone(LOCAL_ZONE).date()
        anchor = date.fromisoformat(selected) if selected else today
        if anchor > today or kind not in ("daily", "weekly"):
            raise ValueError("请选择有效的日期和报告类型")
        start = anchor if kind == "daily" else anchor - timedelta(days=anchor.weekday())
        end = start if kind == "daily" else start + timedelta(days=6)
        records = self.records(start - timedelta(days=7), min(end, today))
        with self.logs._connect() as db:
            checks = {r["day"]: r["fingerprint"] for r in db.execute(
                "SELECT * FROM nutrition_day_checks WHERE day>=? AND day<=?", ((start - timedelta(days=7)).isoformat(), end.isoformat()))}

        def one_day(day):
            all_rows = [r for r in records if r["date"] == day.isoformat()]
            rows = [r for r in all_rows if r["status"] == "saved"]
            rev = self.day_revision(all_rows)
            return {"date": day.isoformat(), **intake_summary(rows), "revision": rev,
                    "complete": bool(rows) and checks.get(day.isoformat()) == rev,
                    "future": day > today, "draft_count": sum(r["status"] == "draft" for r in all_rows),
                    "meals": [{"type": key, "label": label, **intake_summary(
                        [r for r in rows if r["document"]["meal_type"] == key]),
                        "record_ids": [r["id"] for r in rows if r["document"]["meal_type"] == key]}
                        for key, label in MEALS.items()]}

        days = [one_day(start + timedelta(days=i)) for i in range((end - start).days + 1)]
        saved = [r for r in records if start.isoformat() <= r["date"] <= end.isoformat() and r["status"] == "saved"]
        summary = intake_summary(saved)
        recorded = [d for d in days if d["record_count"]]
        complete = [d for d in days if d["complete"]]
        average = {key: {"value": round(sum(d["known_total"][key] for d in known) / len(known), 1) if known else None,
                         "days": len(known), "partial": any(d["total"][key] is None for d in known)}
                   for key in CORE for known in [[d for d in recorded if d["known_total"][key] is not None]]}
        comparison = {}
        if kind == "weekly" and end < today and len(complete) == 7:
            previous = [one_day(start - timedelta(days=7-i)) for i in range(7)]
            for key in CORE:
                if all(d["complete"] and d["total"][key] is not None for d in days + previous):
                    old = sum(d["total"][key] for d in previous) / 7
                    new = sum(d["total"][key] for d in days) / 7
                    comparison[key] = {"previous": round(old, 1), "current": round(new, 1),
                                       "change_percent": round((new-old)/old*100, 1) if old else None}
        facts = [{"id": "coverage", "text": f"这段时间已确认 {len(saved)} 条饮食记录，覆盖 {len(recorded)} 天。",
                  "dates": [d["date"] for d in recorded]}]
        if len(complete) < len([d for d in days if not d["future"]]):
            facts.append({"id": "incomplete_days", "text": "部分日期尚未确认已记完，目前的数字仅反映已记录的饮食，不能代表全天实际摄入。",
                          "dates": [d["date"] for d in days if not d["complete"] and not d["future"]]})
        if summary["estimated"]:
            facts.append({"id": "estimates", "text": f"有 {summary['estimated_records']} 条记录含营养估算，回顾时请一并考虑份量和估算依据。",
                          "dates": sorted({r["date"] for r in saved if r["nutrition"]["estimated"]})})
        missing = [LABELS[k] for k in CORE if saved and summary["total"][k] is None]
        if missing:
            facts.append({"id": "missing", "text": f"{'、'.join(missing)}仍有未知值，展示的是已知部分，没有将未知值计为零。",
                          "dates": [d["date"] for d in recorded if any(d["total"][k] is None for k in CORE)]})
        for key, label in MEALS.items():
            dates = [d["date"] for d in days if any(m["type"] == key and m["record_count"] for m in d["meals"])]
            if dates:
                facts.append({"id": "meal_" + key, "text": f"{len(dates)} 天留下了{label}记录，可以沿着这些餐次回看当时吃的份量。", "dates": dates})
        if kind == "weekly":
            for key in CORE:
                value = average[key]
                if value["value"] is not None:
                    unit = "kcal" if key == "calories_kcal" else "g"
                    facts.append({"id": "average_" + key,
                        "text": f"在有已知{LABELS[key]}值的 {value['days']} 天中，已记录部分日均为 {value['value']} {unit}。这不是全天摄入目标，也不用于判断是否充足。",
                        "dates": [d["date"] for d in recorded if d["known_total"][key] is not None]})
        if kind == "weekly" and len(recorded) >= 2:
            known = [d for d in recorded if d["total"]["calories_kcal"] is not None]
            if len(known) >= 2:
                peak = max(known, key=lambda d: d["total"]["calories_kcal"])
                facts.append({"id": "peak", "text": f"在热量字段完整的记录日中，{peak['date']} 的已记录热量最高。记录量不同也会造成差异，不据此判断饮食好坏。", "dates": [peak["date"]]})
        result = {"kind": kind, "period": {"from": start.isoformat(), "to": end.isoformat()},
                  "today": today.isoformat(), "timezone": "Asia/Shanghai", "ongoing": end >= today,
                  "summary": summary, "days": days, "recorded_days": len(recorded), "complete_days": len(complete),
                  "average": average, "comparison": comparison, "facts": facts,
                  "records": [{"id": r["id"], "revision": r["revision"], "date": r["date"],
                               "title": r["document"]["title"], "meal_type": r["document"]["meal_type"],
                               "nutrition": r["nutrition"]} for r in saved],
                  "draft_count": sum(d["draft_count"] for d in days)}
        result["drafts"] = [{"id": r["id"], "title": r["document"]["title"], "date": r["date"]}
                            for r in records if r["status"] == "draft" and start.isoformat() <= r["date"] <= end.isoformat()]
        with self.logs._connect() as db:
            available = [r["day"] for r in db.execute(
                "SELECT DISTINCT day FROM personal_food_logs WHERE status='saved' AND day<=? ORDER BY day DESC",
                (today.isoformat(),))]
        result["revision"] = fingerprint(result)
        result["available_days"] = available
        result["available_weeks"] = sorted({(date.fromisoformat(day) - timedelta(days=date.fromisoformat(day).weekday())).isoformat()
                                            for day in available}, reverse=True)
        with self.logs._connect() as db:
            cached = db.execute("SELECT * FROM nutrition_report_reviews WHERE id=?", (kind + ':' + start.isoformat(),)).fetchone()
        result["review"] = json.loads(cached["body"]) if cached and cached["fingerprint"] == result["revision"] else None
        if result["review"] and not result["review"].get("text"):
            result["review"] = None
        result["review_stale"] = bool(cached and not result["review"])
        return result

    def save_review(self, report, body):
        with self.logs._connect() as db:
            db.execute("INSERT INTO nutrition_report_reviews(id,fingerprint,body) VALUES(?,?,?) "
                       "ON CONFLICT(id) DO UPDATE SET fingerprint=excluded.fingerprint,body=excluded.body",
                       (report["kind"] + ':' + report["period"]["from"], report["revision"], json.dumps(body, ensure_ascii=False)))
