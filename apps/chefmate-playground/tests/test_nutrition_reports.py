from datetime import datetime, timezone

import pytest

from chefmate_playground.meal_logging import FoodLogRepository
from chefmate_playground.nutrition_reports import NutritionReports

NOW = datetime(2026, 11, 4, 8, tzinfo=timezone.utc)


def add(repo, day, calories=100, saved=True):
    draft = repo.create_draft({"title": "午饭", "date": day, "source_kind": "text",
        "source_text": "我吃了100克米饭", "meal_type": "lunch", "items": [{"name": "米饭", "portion": "100克",
        "amount_g": 100, "portion_basis": "user_provided", "nutrition": {"calories_kcal": calories,
            "protein_g": 3, "fat_g": 1, "carbohydrate_g": 20}, "nutrition_basis": "user_provided",
        "evidence": "用户提供的标签数据"}]})
    return repo.publish(draft["id"], draft["revision"]) if saved else draft


@pytest.fixture
def reports(tmp_path):
    repo = FoodLogRepository(tmp_path / "test.db", now=lambda: NOW)
    return NutritionReports(repo)


def test_calendar_week_keeps_empty_and_future_days_unknown(reports):
    add(reports.logs, '2026-11-02', 100)
    add(reports.logs, '2026-11-03', 300)
    add(reports.logs, '2026-11-04', 900, saved=False)
    result = reports.build('weekly', '2026-11-04')
    assert result['period'] == {'from': '2026-11-02', 'to': '2026-11-08'}
    assert result['summary']['total']['calories_kcal'] == 400
    assert result['average']['calories_kcal'] == {'value': 200, 'days': 2, 'partial': False}
    assert result['days'][2]['total']['calories_kcal'] is None
    assert result['days'][3]['future'] and not result['days'][2]['future']
    assert result['draft_count'] == 1 and not result['comparison']


def test_completion_and_cached_review_invalidate_after_record_change(reports):
    record = add(reports.logs, '2026-11-03')
    result = reports.build('daily', '2026-11-03')
    reports.mark_day('2026-11-03', True, result['days'][0]['revision'])
    result = reports.build('daily', '2026-11-03')
    assert result['complete_days'] == 1
    reports.save_review(result, {'text': '这几餐有米饭。'})
    assert reports.build('daily', '2026-11-03')['review']
    reports.logs.delete(record['id'], record['revision'])
    result = reports.build('daily', '2026-11-03')
    assert result['complete_days'] == 0 and result['summary']['record_count'] == 0
    assert result['review'] is None and result['review_stale']
    with pytest.raises(ValueError):
        reports.mark_day('2026-11-03', True, result['days'][0]['revision'])


def test_partial_unknown_and_no_zero_average(reports):
    add(reports.logs, '2026-11-02', 100)
    add(reports.logs, '2026-11-03', None)
    result = reports.build('weekly', '2026-11-03')
    assert result['summary']['total']['calories_kcal'] is None
    assert result['summary']['known_total']['calories_kcal'] == 100
    assert result['average']['calories_kcal']['days'] == 1
    assert result['average']['calories_kcal']['value'] == 100


def test_week_comparison_requires_both_complete_weeks(reports):
    from datetime import date, timedelta
    for i in range(14):
        day = (date(2026, 10, 19) + timedelta(days=i)).isoformat()
        add(reports.logs, day, 100 if i < 7 else 200)
        current = reports.build('daily', day)
        reports.mark_day(day, True, current['days'][0]['revision'])
    result = reports.build('weekly', '2026-10-28')
    assert result['comparison']['calories_kcal'] == {'previous': 100, 'current': 200, 'change_percent': 100}
    reports.mark_day('2026-10-19', False, reports.build('daily', '2026-10-19')['days'][0]['revision'])
    assert reports.build('weekly', '2026-10-28')['comparison'] == {}


def test_date_boundary_and_future_rejection(reports):
    reports.logs._now = lambda: datetime(2026, 11, 3, 16, 5, tzinfo=timezone.utc)
    assert reports.build()['period']['from'] == '2026-11-04'
    with pytest.raises(ValueError):
        reports.build('daily', '2026-11-05')
    with pytest.raises(ValueError):
        reports.build('monthly', '2026-11-04')


def test_http_review_writes_text_from_meals_and_caches_it(reports, monkeypatch):
    from types import SimpleNamespace
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from langchain_core.messages import AIMessage
    from chefmate_playground.routes.nutrition_reports import build_router
    from chefmate_playground.background_jobs import BackgroundJobs
    import time
    calls = []

    class Model:
        def __init__(self, **kwargs):
            pass

        async def ainvoke(self, messages):
            calls.append(messages)
            return AIMessage(content='这餐记录了米饭，可以搭配喜欢的蔬菜。')

    monkeypatch.setattr('chefmate_agent.provider.IntegrityChatOpenAI', Model)
    add(reports.logs, '2026-11-03')
    context = SimpleNamespace(journal=SimpleNamespace(food_logs=reports.logs), manager=SimpleNamespace(jobs=BackgroundJobs(reports.logs.database)),
        config=SimpleNamespace(enabled_components=('food-journal',), api_key='test', model='test',
                               base_url='https://example.invalid', api_format='chat_completions'))
    app = FastAPI()
    app.include_router(build_router(context))
    with TestClient(app) as client:
        assert client.get('/api/nutrition-reports?kind=daily&date=2026-11-05').status_code == 422
        for _ in range(2):
            response = client.post('/api/nutrition-reports/review?kind=daily&date=2026-11-03')
            assert response.status_code == 202
            for attempt in range(100):
                response = client.get('/api/nutrition-reports?kind=daily&date=2026-11-03')
                if response.json()['review']:
                    break
                time.sleep(0.01)
            assert response.json()['review']['text'] == '这餐记录了米饭，可以搭配喜欢的蔬菜。'
        assert len(calls) == 1
        assert '"meals"' in calls[0][0].content
        assert '"calories_kcal": 100' in calls[0][0].content


def test_report_data_is_isolated_by_repository(reports, tmp_path):
    add(reports.logs, '2026-11-03')
    other = NutritionReports(FoodLogRepository(tmp_path / 'other.db', now=lambda: NOW))
    assert other.build('daily', '2026-11-03')['summary']['record_count'] == 0
    assert reports.build('daily', '2026-11-03')['summary']['record_count'] == 1

def test_available_weeks_only_include_saved_records(reports):
    add(reports.logs, '2026-10-04')
    add(reports.logs, '2026-11-02')
    add(reports.logs, '2026-10-20', saved=False)
    record = add(reports.logs, '2026-10-13')
    reports.logs.delete(record['id'], record['revision'])
    assert reports.build('weekly')['available_weeks'] == ['2026-11-02', '2026-09-28']


def test_review_accepts_immediately_and_other_requests_work_while_model_waits(reports, monkeypatch):
    import asyncio
    import threading
    import time
    from types import SimpleNamespace
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from langchain_core.messages import AIMessage
    from chefmate_playground.background_jobs import BackgroundJobs
    from chefmate_playground.routes.nutrition_reports import build_router
    release, started = threading.Event(), threading.Event()
    calls = []

    class Model:
        def __init__(self, **kwargs):
            pass

        async def ainvoke(self, messages):
            calls.append(messages)
            started.set()
            await asyncio.to_thread(release.wait, 5)
            return AIMessage(content='这几餐值得回味。')

    monkeypatch.setattr('chefmate_agent.provider.IntegrityChatOpenAI', Model)
    add(reports.logs, '2026-11-03')
    jobs = BackgroundJobs(reports.logs.database)
    context = SimpleNamespace(journal=SimpleNamespace(food_logs=reports.logs), manager=SimpleNamespace(jobs=jobs),
        config=SimpleNamespace(enabled_components=('food-journal',), api_key='test', model='test',
                              base_url='https://example.invalid', api_format='chat_completions'))
    app = FastAPI()
    app.include_router(build_router(context))
    uri = '/api/nutrition-reports/review?kind=daily&date=2026-11-03'
    with TestClient(app) as client:
        try:
            response = client.post(uri)
            assert response.status_code == 202
            assert started.wait(2)
            duplicate = client.post(uri).json()
            assert duplicate['review_job']['id'] == response.json()['review_job']['id']
            assert client.get('/api/nutrition-reports?kind=weekly&date=2026-11-03').status_code == 200
            assert len(calls) == 1
        finally:
            release.set()
        for _ in range(200):
            result = client.get('/api/nutrition-reports?kind=daily&date=2026-11-03').json()
            if result['review'] and result['review_job']['status'] == 'completed':
                break
            time.sleep(.01)
        assert result['review']['text'] == '这几餐值得回味。'
        assert result['review_job']['status'] == 'completed'
