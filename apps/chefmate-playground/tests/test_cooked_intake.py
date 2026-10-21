"""Completed meals bind one personal nutrition record to an immutable source."""

import asyncio
import copy
import json
from types import SimpleNamespace

import pytest

from chefmate_agent import ToolExecutionContext
from chefmate_playground.cooked_intake import CookedPortion, entry_identity, explicit_fractions, validate_portion_evidence
from chefmate_playground.interactions import WorkspaceBinding
from chefmate_playground.journal import JournalRepository
from chefmate_playground.meal_logging import make_meal_logging_tools
from chefmate_playground.sessions import SessionRepository
from test_journal import NOW, cooking_state


def setup(tmp_path, *, diners=5, unknown=True, two_dishes=False, complete=True):
    journal = JournalRepository(tmp_path / 'intake.db', now=lambda: NOW)
    state = cooking_state(completed=complete, unknown=unknown)
    state['workspace']['meal_diners'] = diners
    if two_dishes:
        recipe = copy.deepcopy(state['workspace']['meal_plan'][0])
        recipe['recipe_id'] = recipe['recipe']['recipe_id'] = 'r2'
        recipe['recipe']['name'] = '测试青菜'
        state['workspace']['meal_plan'].append(recipe)
    journal.sync_session(state, register_intake=True)
    identity = entry_identity(state['id'], state['workspace']['meal_id'])
    return journal, state, identity


def execute(text, receipt=None, *, binding=None, cancelled=None, session_id='chat', turn_id='turn'):
    messages = [{'role': 'user', 'content': text}]
    if receipt:
        messages.append({'role': 'tool', 'name': 'get_cooked_meal_intake', 'content': json.dumps(receipt, ensure_ascii=False)})
    host = SimpleNamespace(session_id=session_id, turn_id=turn_id, binding=binding, cancelled=cancelled)
    return ToolExecutionContext({}, host, messages)


def tools(journal):
    return {tool.name: tool for tool in make_meal_logging_tools(journal)}


def request(receipt, fraction=.5, evidence='我吃了一半'):
    return {key: receipt[key] for key in ('entry_id', 'source_revision', 'expected_revision')} | {
        'portions': [{'recipe_id': recipe['recipe_id'], 'fraction': fraction, 'evidence': evidence} for recipe in receipt['recipes']]}


def test_single_diner_completion_saves_intake_once_and_preserves_unknowns(tmp_path):
    journal, state, identity = setup(tmp_path, diners=1)
    result = journal.cooked_intake.get(identity)
    assert result['intake_status'] == 'saved'
    record = result['record']
    assert record['status'] == 'saved' and record['document']['items'][0]['portion'] == '整道菜的 100%'
    assert record['nutrition']['total']['calories_kcal'] == 12.5
    assert record['nutrition']['total']['sodium_mg'] is None
    assert record['nutrition']['estimated']
    assert record['cooked_source']['mode'] == 'single_diner'
    assert journal.food_logs.summary()['record_count'] == 1
    assert not journal.sync_session(state, register_intake=True)
    assert journal.cooked_intake.completed(identity)['record'] == record
    journal.sync_session(state)
    assert journal.food_logs.summary()['record_count'] == 1


def test_history_reads_never_backfill_intake_and_shared_unknown_diners_wait(tmp_path):
    for index, diners in enumerate((1, 5, None)):
        journal = JournalRepository(tmp_path / f'read-{index}.db', now=lambda: NOW)
        state = cooking_state(completed=True)
        state['workspace']['meal_diners'] = diners
        journal.sync_session(state)
        journal.summary()
        identity = entry_identity(state['id'], state['workspace']['meal_id'])
        assert journal.cooked_intake.get(identity)['intake_status'] == 'awaiting_portions'
        assert journal.food_logs.summary()['record_count'] == 0
        if diners != 1:
            journal.sync_session(state, register_intake=True)
            assert journal.cooked_intake.get(identity)['record'] is None
            assert journal.food_logs.summary()['record_count'] == 0


def test_shared_portions_use_frozen_recipe_nutrition_and_corrections_update_same_record(tmp_path):
    journal, state, identity = setup(tmp_path, two_dishes=True)
    specs = tools(journal)
    text = '我吃了番茄汤整盘的三分之一，青菜整盘的一半。'
    receipt = specs['get_cooked_meal_intake'].handler({'entry_id': identity}, execute(text))
    values = request(receipt)
    values['portions'] = [{'recipe_id': 'r1', 'fraction': 1 / 3, 'evidence': '番茄汤整盘的三分之一'},
                          {'recipe_id': 'r2', 'fraction': .5, 'evidence': '青菜整盘的一半'}]
    saved = specs['record_cooked_meal_intake'].handler(values, execute(text, receipt))
    assert saved['intake_status'] == 'saved', saved
    record = saved['record']
    assert record['nutrition']['total']['calories_kcal'] == 10.417
    assert record['nutrition']['total']['sodium_mg'] is None
    assert record['source_session_id'] == state['id']
    assert journal.food_logs.summary()['record_count'] == 1
    assert journal.get(identity)['nutrition']['meal_total']['calories_kcal'] == 25
    # A replay of the same reply does not add or revise the saved meal again.
    replay = specs['record_cooked_meal_intake'].handler(values, execute(text, receipt))
    assert replay['record'] == record
    corrected = specs['record_cooked_meal_intake'].handler(request(saved, .25, '每道菜我只吃了四分之一'),
        execute('更正一下，每道菜我只吃了四分之一。', saved))
    assert corrected['record']['id'] == record['id']
    assert corrected['record']['revision'] == record['revision'] + 1
    assert corrected['record']['nutrition']['total']['calories_kcal'] == 6.25
    assert journal.food_logs.summary()['record_count'] == 1


def test_single_automatic_record_can_be_corrected_and_zero_intake_excluded(tmp_path):
    journal, _, identity = setup(tmp_path, diners=1)
    specs = tools(journal)
    automatic = journal.cooked_intake.get(identity)
    result = specs['record_cooked_meal_intake'].handler(request(automatic), execute('其实我吃了一半。', automatic))
    assert result['record']['id'] == automatic['record']['id']
    assert result['record']['nutrition']['total']['calories_kcal'] == 6.25
    skipped = specs['record_cooked_meal_intake'].handler(request(result, 0, '我没吃'), execute('更正，我没吃。', result))
    assert skipped['intake_status'] == 'not_eaten' and skipped['record'] is None
    assert journal.food_logs.summary()['record_count'] == 0
    assert journal.get(identity)['status'] == 'completed'
    # A repeated completion cannot resurrect an explicitly removed personal meal.
    assert journal.cooked_intake.completed(identity)['intake_status'] == 'not_eaten'
    assert journal.food_logs.summary()['record_count'] == 0
    restored = specs['record_cooked_meal_intake'].handler(request(skipped), execute('刚才说错，我吃了一半。', skipped))
    assert restored['record']['id'] == automatic['record']['id']
    assert journal.food_logs.summary()['record_count'] == 1


def test_food_log_delete_restore_preserves_cooking_provenance_and_never_auto_restores(tmp_path):
    journal, state, identity = setup(tmp_path, diners=1)
    record = journal.cooked_intake.get(identity)['record']
    deleted = journal.food_logs.delete(record['id'], record['revision'])
    journal.sync_session(state, register_intake=True)
    assert journal.cooked_intake.get(identity)['intake_status'] == 'deleted'
    assert journal.food_logs.summary()['record_count'] == 0
    restored = journal.food_logs.restore(record['id'], deleted['revision'])
    assert restored['cooked_source'] == record['cooked_source']
    assert journal.cooked_intake.get(identity)['record']['id'] == record['id']


def test_actual_food_log_state_controls_zero_portion_and_restore_idempotency(tmp_path):
    journal, _, identity = setup(tmp_path, diners=1)
    specs = tools(journal)
    current = journal.cooked_intake.get(identity)
    zero = specs['record_cooked_meal_intake'].handler(request(current, 0, '我没吃'), execute('我没吃', current))
    original = journal.food_logs.get(current['record']['id'], include_deleted=True)
    journal.food_logs.restore(original['id'], original['revision'])
    restored = journal.cooked_intake.get(identity)
    assert restored['intake_status'] == 'saved'
    again = specs['record_cooked_meal_intake'].handler(request(restored, 0, '我没吃'), execute('我没吃', restored))
    assert again['intake_status'] == zero['intake_status'] == 'not_eaten'
    assert journal.food_logs.summary()['record_count'] == 0
    half = specs['record_cooked_meal_intake'].handler(request(again), execute('我吃了一半', again))
    journal.food_logs.delete(half['record']['id'], half['record']['revision'])
    deleted = journal.cooked_intake.get(identity)
    same_half = specs['record_cooked_meal_intake'].handler(request(deleted), execute('我吃了一半', deleted))
    assert same_half['intake_status'] == 'saved'
    assert same_half['record']['id'] == half['record']['id']
    assert journal.food_logs.summary()['record_count'] == 1


def test_stale_source_missing_dishes_duplicate_ids_and_cas_do_not_count(tmp_path):
    journal, _, identity = setup(tmp_path, two_dishes=True)
    receipt = journal.cooked_intake.get(identity)
    values = request(receipt)
    for bad in ({'source_revision': '0' * 64}, {'portions': values['portions'][:1]},
                {'portions': [values['portions'][0], values['portions'][0]]}, {'expected_revision': 99}):
        data = values | bad
        with pytest.raises(ValueError):
            journal.cooked_intake.record(data['entry_id'], data['source_revision'], data['expected_revision'], data['portions'], session_id='chat')
    saved = journal.cooked_intake.record(identity, receipt['source_revision'], 0, values['portions'], session_id='chat')
    with pytest.raises(ValueError, match='已变化'):
        journal.cooked_intake.record(identity, receipt['source_revision'], 0, request(receipt, .25)['portions'], session_id='chat')
    assert journal.food_logs.summary()['record_count'] == 1
    assert journal.cooked_intake.get(identity)['record'] == saved['record']


@pytest.mark.parametrize('text,evidence,fraction', [
    ('我吃了一半吗？', '我吃了一半', .5), ('假设我吃了一半', '我吃了一半', .5),
    ('他说“我吃了一半”', '我吃了一半', .5), ('原本想吃一半，实际没吃', '一半', .5),
    ('我吃了一半', '我吃了一半', .2), ('吃了100克', '100克', .5),
    ('大家吃了一半，我没吃。', '我没吃', .5), ('不要登记，我吃了一半', '我吃了一半', .5),
    ('我没吃，妈妈吃了一半。', '妈妈吃了一半', .5),
    ('我没有按五人均分吃一份，后来只吃了四分之一。', '五人均分吃一份', .2),
    ('要是我吃了一半，就给我记录吧。', '我吃了一半', .5),
])
def test_portion_evidence_rejects_questions_quotes_plans_unmatched_values(text, evidence, fraction):
    with pytest.raises(ValueError):
        validate_portion_evidence([CookedPortion(recipe_id='r1', fraction=fraction, evidence=evidence)], text, 5)


@pytest.mark.parametrize('text,fraction,diners', [
    ('我吃了一半', .5, 5), ('我吃了三分之一', 1 / 3, 5), ('每道菜吃了20%', .2, 5),
    ('我按五人均分吃了一份', .2, 5), ('我按四人平分吃了一份', .25, None),
    ('原本想吃一半，实际没吃', 0, 5), ('我把整盘都吃了', 1, 1),
])
def test_explicit_personal_fraction_expressions(text, fraction, diners):
    evidence = '实际没吃' if '原本' in text else text
    validate_portion_evidence([CookedPortion(recipe_id='r1', fraction=fraction, evidence=evidence)], text, diners)
    assert explicit_fractions('整盘的三分之一', 5) == {round(1 / 3, 8)}
    assert explicit_fractions('整盘的一半', 5) == {.5}


def test_structured_answer_prefix_does_not_turn_the_actual_answer_into_a_question():
    text = '关于“你自己吃了多少？”，我的回答是：我按五人均分吃了一份。请按这个答案继续。'
    validate_portion_evidence([CookedPortion(recipe_id='r1', fraction=.2, evidence='我按五人均分吃了一份')], text, 5)


def test_correcting_a_portion_without_duplicate_records_is_not_a_refusal_to_record():
    portion = CookedPortion(recipe_id='r1', fraction=.5, evidence='我只吃了一半')
    for instruction in ('不要重复记录', '不要新增记录', '别再另建一条饮食记录'):
        validate_portion_evidence([portion], f'我只吃了一半，请更正这条，{instruction}。', 1)
    for instruction in ('不要记录', '不要重复记录，也不要记录这一餐'):
        with pytest.raises(ValueError, match='尚未允许登记'):
            validate_portion_evidence([portion], f'我只吃了一半，{instruction}。', 1)


def test_portion_evidence_cannot_swap_named_dishes():
    recipes = [{'recipe_id': 'meat', 'name': '肉'}, {'recipe_id': 'greens', 'name': '炒青菜'}]
    text = '我吃了半盘青菜，肉我没吃。'
    with pytest.raises(ValueError, match='另一道菜'):
        validate_portion_evidence([CookedPortion(recipe_id='meat', fraction=.5, evidence='我吃了半盘青菜')], text, 2, recipes)
    with pytest.raises(ValueError, match='另一道菜'):
        validate_portion_evidence([CookedPortion(recipe_id='greens', fraction=0, evidence='肉我没吃')], text, 2, recipes)
    with pytest.raises(ValueError, match='另一道菜'):
        validate_portion_evidence([CookedPortion(recipe_id='meat', fraction=.5, evidence='半盘')], text, 2, recipes)
    validate_portion_evidence([CookedPortion(recipe_id='greens', fraction=.5, evidence='我吃了半盘青菜'),
                              CookedPortion(recipe_id='meat', fraction=0, evidence='肉我没吃')], text, 2, recipes)
    validate_portion_evidence([CookedPortion(recipe_id=recipe['recipe_id'], fraction=.5, evidence='我每道菜都吃了一半')
                              for recipe in recipes], '我每道菜都吃了一半', 2, recipes)
    with pytest.raises(ValueError, match='带上对应菜名'):
        validate_portion_evidence([CookedPortion(recipe_id='greens', fraction=1 / 3, evidence='三分之一')],
            '我吃了肉整盘的三分之一、炒青菜整盘的一半', 2, recipes)


def test_tool_requires_fresh_read_and_current_human_evidence(tmp_path):
    journal, _, identity = setup(tmp_path)
    receipt = journal.cooked_intake.get(identity)
    spec = tools(journal)['record_cooked_meal_intake']
    assert spec.required_skill == 'food-journal' and spec.effect == 'write'
    assert spec.handler(request(receipt), execute('我吃了一半'))['status'] == 'read_required'
    wrong = spec.handler(request(receipt), execute('大家都吃完了，我还没有吃。', receipt))
    assert wrong['status'] == 'invalid_cooked_portions'
    assert journal.food_logs.summary()['record_count'] == 0


def test_closed_turn_cancellation_and_guard_failure_roll_back(tmp_path):
    journal, _, identity = setup(tmp_path)
    sessions = SessionRepository(journal.path)
    chat = sessions.create()
    chat.update(running=True, active_turn={'id': 'active'})
    sessions.save(chat)
    binding = WorkspaceBinding(sessions, None, journal=journal)
    receipt = journal.cooked_intake.get(identity)
    spec = tools(journal)['record_cooked_meal_intake']
    closed = spec.handler(request(receipt), execute('我吃了一半', receipt, binding=binding,
        session_id=chat['id'], turn_id='closed'))
    assert closed['status'] == 'invalid_cooked_portions'
    cancelled = asyncio.Event()
    cancelled.set()
    assert spec.handler(request(receipt), execute('我吃了一半', receipt, binding=binding,
        cancelled=cancelled, session_id=chat['id'], turn_id='active'))['status'] == 'invalid_cooked_portions'
    calls = []
    def guard():
        calls.append(True)
        if len(calls) == 2:
            raise ValueError('cancelled during write')
    with pytest.raises(ValueError):
        journal.cooked_intake.record(identity, receipt['source_revision'], 0, request(receipt)['portions'],
            session_id=chat['id'], turn_id='active', guard=guard)
    assert journal.food_logs.summary()['record_count'] == 0
    assert journal.cooked_intake.get(identity)['expected_revision'] == 0


def test_disabled_food_component_keeps_only_cooking_production(tmp_path):
    journal, state, identity = setup(tmp_path, diners=1, complete=False)
    sessions = SessionRepository(journal.path)
    state.update(updated_at=NOW.isoformat())
    state['workspace']['stage'] = 'completed'
    state['workspace']['completed_at'] = NOW.isoformat()
    sessions.save(state)
    binding = WorkspaceBinding(sessions, None, journal=journal, intake_enabled=lambda: False)
    assert binding.record_journal(state['id'], register_intake=True)
    assert binding.intake_status(state['id'])['intake_status'] == 'disabled'
    assert journal.get(identity)['status'] == 'completed'
    assert journal.food_logs.summary()['record_count'] == 0
