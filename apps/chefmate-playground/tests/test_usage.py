"""Durable usage is separate from chat history and preserves incomplete reports."""

import asyncio
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from chefmate_playground.runtime import TurnManager
from chefmate_playground.sessions import SessionRepository
from chefmate_playground.usage import UsageRepository


def reported(round_number=1, **patch):
    return {"round": round_number, "model": "fixture", "input_tokens": 100, "output_tokens": 10,
            "total_tokens": 110, "cached_tokens": None, "reasoning_tokens": None, **patch}


def test_idempotent_updates_keep_known_usage_and_survive_chat_deletion(tmp_path):
    sessions = SessionRepository(tmp_path / "sessions.db")
    session = sessions.create()
    ledger = UsageRepository(sessions.path)
    usage = {"model_calls": 2, "calls": [reported()]}
    for status in ("running", "completed", "running"):
        ledger.record(session["id"], "turn", usage, model="fixture", status=status)
    ledger.record(session["id"], "turn", {"model_calls": 1, "calls": [reported(input_tokens=None)]})
    summary = ledger.summary(days=None)
    assert summary["summary"]["turns"] == 1 and summary["summary"]["model_calls"] == 2
    assert summary["summary"]["input_tokens"] is None
    assert summary["summary"]["known_totals"]["input_tokens"] == 100
    assert summary["summary"]["coverage"]["cached_tokens"] == {"reported": 0, "calls": 2}
    assert summary["summary"]["statuses"]["completed"] == 1
    sessions.delete(session["id"])
    assert UsageRepository(sessions.path).summary(days=None) == summary


def test_period_and_models_use_local_days_and_never_attribute_old_calls_to_current_model(tmp_path):
    def clock():
        return datetime(2026, 10, 21, 17, tzinfo=timezone.utc)
    ledger = UsageRepository(tmp_path / "usage.db", now=clock)
    ledger.record("a", "old", {"model_calls": 1, "calls": [reported(model="old-model")]},
                  created_at="2026-10-20T15:30:00+00:00", status="completed")
    ledger.record("a", "today", {"model_calls": 1, "calls": [reported(model="")]},
                  created_at="2026-10-21T16:01:00+00:00", status="cancelled")
    summary = ledger.summary(days=1)
    assert summary["period"]["to"] == "2026-10-22" and summary["summary"]["turns"] == 1
    assert summary["all_time"]["turns"] == 2 and summary["today"]["turns"] == 1
    assert summary["models"][0]["model"] == "unknown"
    assert summary["summary"]["statuses"]["cancelled"] == 1
    with pytest.raises(ValueError):
        ledger.summary(days=0)
    with pytest.raises(ValueError):
        ledger.summary(days=True)


def test_backfill_uses_existing_completed_and_failed_observations_once(tmp_path):
    sessions = SessionRepository(tmp_path / "sessions.db")
    session = sessions.create()
    usage = {"model_calls": 1, "calls": [reported(model="")]}
    session["messages"].append({"role": "assistant", "turn_id": "done", "usage": usage,
                                "created_at": session["created_at"]})
    sessions.save(session)
    sessions.save_debug_run(session["id"], "failed", {"turn_id": "failed", "status": "error",
                             "created_at": session["created_at"], "usage": usage, "debug_trace": [{}, {}]})
    ledger = UsageRepository(sessions.path)
    ledger.backfill(sessions)
    ledger.backfill(sessions)
    summary = ledger.summary(days=None)
    assert summary["summary"]["turns"] == 2 and summary["summary"]["model_calls"] == 3
    assert summary["summary"]["input_tokens"] is None
    assert summary["summary"]["known_totals"]["input_tokens"] == 200
    assert {item["model"] for item in summary["models"]} == {"unknown"}


def test_restart_reconciles_usage_written_before_debug_persistence(tmp_path):
    sessions = SessionRepository(tmp_path / "sessions.db")
    session = sessions.create()
    ledger = UsageRepository(sessions.path)
    ledger.record(session["id"], "crashed", {"model_calls": 2, "calls": [reported()]}, model="fixture")
    ledger.backfill(sessions)
    result = ledger.summary(days=None)
    assert result["summary"]["statuses"]["interrupted"] == 1
    assert result["summary"]["known_totals"]["input_tokens"] == 100
    assert result["models"][0]["model"] == "fixture" and result["models"][0]["model_calls"] == 2


def test_cancellation_keeps_observed_usage_and_latest_context_without_raw_data(tmp_path):
    started = asyncio.Event()
    measurement = {"round": 2, "chars": 200, "estimated_tokens": 80, "estimated": True,
                   "categories": [{"key": "conversation", "label": "对话", "chars": 200, "estimated_tokens": 80}]}

    class Paused:
        config = SimpleNamespace(model="fixture")

        async def run_turn(self, messages, on_event=None, **kwargs):
            await on_event({"type": "context_usage", "context_usage": measurement})
            await on_event({"type": "model_usage", "usage": {"model_calls": 2, "calls": [reported()]}})
            started.set()
            await asyncio.Event().wait()

    async def exercise():
        repository = SessionRepository(tmp_path / "sessions.db")
        session = repository.create()
        manager = TurnManager(repository, Paused())
        turn, _ = await manager.begin(session["id"], "不应该出现在用量统计中的用户原文")
        await started.wait()
        assert repository.get(session["id"])["active_turn"]["context_usage"] == measurement
        await manager.cancel(session["id"])
        await turn.task
        saved = repository.get(session["id"])
        assert saved["last_run"]["result"]["context_usage"] == measurement
        assert saved["last_run"]["result"]["usage"]["calls"][0]["input_tokens"] == 100
        summary = manager.usage.summary(days=None)
        assert summary["summary"]["statuses"]["cancelled"] == 1
        assert summary["summary"]["known_totals"]["input_tokens"] == 100
        assert "用户原文" not in json.dumps(summary, ensure_ascii=False)
        assert "不应该" not in json.dumps(summary, ensure_ascii=False)
        repository.delete(session["id"])
        assert manager.usage.summary(days=None)["summary"]["turns"] == 1
        await manager.close()

    asyncio.run(exercise())


def test_same_call_updates_derive_once_and_later_provider_total_has_priority(tmp_path):
    ledger = UsageRepository(tmp_path / "usage.db")
    ledger.record("chat", "turn", {"model_calls": 1, "calls": [reported(output_tokens=None, total_tokens=None)]})
    assert ledger.summary(days=None)["summary"]["total_tokens"] is None
    update = {"model_calls": 1, "calls": [reported(input_tokens=None, output_tokens=20, total_tokens=None)]}
    ledger.record("chat", "turn", update)
    ledger.record("chat", "turn", update)
    summary = ledger.summary(days=None)["summary"]
    assert summary["total_tokens"] == 120 and summary["total_derived_calls"] == 1
    assert summary["model_calls"] == 1
    ledger.record("chat", "turn", {"model_calls": 1, "calls": [reported(output_tokens=20, total_tokens=125)]})
    # A late incomplete receipt cannot replace a direct provider report with a sum.
    ledger.record("chat", "turn", update)
    summary = ledger.summary(days=None)["summary"]
    assert summary["total_tokens"] == 125 and summary["total_reported_calls"] == 1
    assert summary["total_derived_calls"] == 0


def test_old_ledger_and_retained_chat_calls_derive_per_call_without_aggregate_fill(tmp_path):
    now = datetime(2026, 10, 21, 10, tzinfo=timezone.utc)
    sessions = SessionRepository(tmp_path / "sessions.db")
    ledger = UsageRepository(sessions.path, now=lambda: now)
    old_calls = [reported(1, input_tokens=1590589, output_tokens=102465, total_tokens=None),
                 reported(2, input_tokens=10, output_tokens=None, total_tokens=None),
                 reported(3, input_tokens=None, output_tokens=20, total_tokens=None)]
    old_body = {"model": "fixture", "attempted_calls": 3, "calls": old_calls}
    with ledger._connect() as connection:
        connection.execute("INSERT INTO model_usage_ledger VALUES(?,?,?,?,?,?)",
                           ("old-chat", "old-turn", "2026-10-21", now.isoformat(), "completed", json.dumps(old_body)))
    legacy = ledger.summary(days=None)
    assert legacy["summary"]["total_tokens"] is None
    assert legacy["summary"]["known_totals"]["total_tokens"] == 1693054
    assert legacy["summary"]["coverage"]["total_tokens"] == {"reported": 0, "derived": 1, "known": 1, "calls": 3}
    assert legacy["models"][0]["total_derived_calls"] == 1
    assert legacy["daily"][0]["total_derived_calls"] == 1
    session = sessions.create()
    session["messages"].append({"role": "assistant", "turn_id": "retained", "created_at": now.isoformat(),
                                "usage": {"model_calls": 1, "calls": [reported(total_tokens=None)]}})
    sessions.save(session)
    ledger.backfill(sessions)
    ledger.backfill(sessions)
    result = ledger.summary(days=None)["summary"]
    assert result["model_calls"] == 4 and result["total_derived_calls"] == 2
    assert result["known_totals"]["total_tokens"] == 1693164
    assert result["coverage"]["total_tokens"]["known"] == 2
    # Neither the ledger's nor the historical turn's aggregate is used to fill
    # another call's missing input/output operand.
    assert result["total_tokens"] is None and result["input_tokens"] is None


def test_persisted_derived_call_source_survives_a_fresh_repository(tmp_path):
    path = tmp_path / "usage.db"
    ledger = UsageRepository(path)
    ledger.record("chat", "turn", {"model_calls": 1, "calls": [reported(total_tokens=None)]})
    reloaded = UsageRepository(path).summary(days=None)["summary"]
    assert reloaded["total_tokens"] == 110
    assert reloaded["total_derived_calls"] == 1 and reloaded["total_reported_calls"] == 0
    with ledger._connect() as connection:
        stored = json.loads(connection.execute("SELECT body FROM model_usage_ledger").fetchone()[0])
    assert stored["calls"][0]["total_tokens_source"] == "input_output_sum"


def test_turn_level_totals_cannot_fill_different_calls_missing_operands(tmp_path):
    ledger = UsageRepository(tmp_path / "usage.db")
    ledger.record("chat", "turn", {"model_calls": 2, "input_tokens": 100, "output_tokens": 20, "total_tokens": 120,
        "calls": [reported(1, output_tokens=None, total_tokens=None),
                  reported(2, input_tokens=None, output_tokens=20, total_tokens=None)]})
    summary = ledger.summary(days=None)["summary"]
    assert summary["known_totals"]["input_tokens"] == 100
    assert summary["known_totals"]["output_tokens"] == 20
    assert summary["known_totals"]["total_tokens"] is None and summary["total_derived_calls"] == 0
