import asyncio
import json
from types import SimpleNamespace

from chefmate_playground.background_jobs import BackgroundJobs
from chefmate_playground.continuation import continuation_history
from chefmate_playground.runtime import TurnManager
from chefmate_playground.sessions import SessionRepository, public_session


CALL = {'id': 'write-1', 'type': 'function', 'function': {'name': 'save_food', 'arguments': '{"title":"午饭"}'}}


def test_background_job_survives_observer_and_deduplicates(tmp_path):
    async def run():
        repo = SessionRepository(tmp_path / 'jobs.db')
        jobs = BackgroundJobs(repo.database)
        release = asyncio.Event()
        calls = []

        async def operation():
            calls.append(1)
            await release.wait()

        first = await jobs.submit('daily:today', 'r1', operation)
        second = await jobs.submit('daily:today', 'r1', operation)
        assert first['id'] == second['id']
        assert jobs.active
        release.set()
        await asyncio.gather(*jobs.active.values())
        assert calls == [1]
        assert jobs.get('daily:today')['status'] == 'completed'
        jobs.save('daily:tomorrow', dict(id='crashed', status='running'))
        restarted = BackgroundJobs(repo.database)
        assert restarted.get('daily:tomorrow')['status'] == 'interrupted'
        assert not restarted.active
    asyncio.run(run())


def test_stop_and_continue_reuses_tool_result_and_keeps_partial_text(tmp_path):
    async def run():
        repo = SessionRepository(tmp_path / 'sessions.db')
        ready = asyncio.Event()
        received = []

        class Agent:
            async def run_turn(self, messages, on_event, **kwargs):
                received.append(messages)
                if len(received) == 1:
                    await on_event({'type': 'execution_checkpoint', 'messages': [*messages,
                        {'role': 'assistant', 'content': '准备记午饭', 'tool_calls': [CALL]}]})
                    await on_event({'type': 'tool_start', 'call_id': 'write-1', 'name': 'save_food', 'arguments': {'title': '午饭'}})
                    await on_event({'type': 'execution_tool_result', 'message': {'role': 'tool', 'name': 'save_food',
                        'tool_call_id': 'write-1', 'content': '{"status":"ok","id":"food-1"}'}})
                    await on_event({'type': 'tool_end', 'call_id': 'write-1', 'name': 'save_food', 'result': {'status': 'ok'}})
                    await on_event({'type': 'assistant_delta', 'text': '已记下，接下来'})
                    ready.set()
                    await asyncio.Event().wait()
                return {'status': 'completed', 'assistant_text': '继续完成了',
                        'messages': [*messages, {'role': 'assistant', 'content': '继续完成了'}]}

        manager = TurnManager(repo, Agent())
        session = repo.create()['id']
        turn, _ = await manager.begin(session, '记午饭')
        await ready.wait()
        await manager.cancel(session)
        await turn.task
        saved = repo.get(session)
        assert saved['last_run']['result']['text'] == '已记下，接下来'
        assert 'execution_checkpoint' not in public_session(saved)
        restarted = TurnManager(SessionRepository(repo.path), manager.agent)
        continued, _ = await restarted.begin(session, '继续')
        await continued.task
        assert received[1][-1]['content'] == '继续'
        assert sum(m.get('tool_call_id') == 'write-1' for m in received[1]) == 1
        assert json.loads(next(m['content'] for m in received[1] if m.get('role') == 'tool'))['id'] == 'food-1'
        assert any(m.get('content') == '已记下，接下来' for m in repo.get(session)['messages'])
        assert not repo.get(session).get('execution_checkpoint')
    asyncio.run(run())


def test_restart_retains_checkpoint_and_unknown_write_is_not_replayed(tmp_path):
    async def run():
        repo = SessionRepository(tmp_path / 'crash.db')
        state = repo.create()
        state.update(running=True, messages=[{'role': 'user', 'content': '记午饭'}],
            execution_checkpoint={'messages': [{'role': 'user', 'content': '记午饭'},
                {'role': 'assistant', 'content': '', 'tool_calls': [CALL]}]},
            active_turn={'id': 'old', 'text': '正在保存', 'tool_calls': [{'call_id': 'write-1', 'name': 'save_food'}]})
        repo.save(state)
        repo = SessionRepository(repo.path)
        recovered = repo.get(state['id'])
        assert recovered['last_status'] == 'interrupted'
        assert recovered['last_run']['result']['text'] == '正在保存'
        messages, uncertain = continuation_history(recovered['execution_checkpoint'], recovered['interrupted_progress'])
        assert json.loads(next(m['content'] for m in messages if m['role'] == 'tool'))['status'] == 'execution_unknown'
        assert uncertain[0]['name'] == 'save_food'

        class Agent:
            _extra_tools = {'save_food': SimpleNamespace(effect='write')}

            async def run_turn(self, messages, on_event, **kwargs):
                await on_event({'type': 'tool_start', 'call_id': 'new', 'name': 'save_food', 'arguments': {'title': '午饭'}})
                raise AssertionError('Unknown write must not execute')

        manager = TurnManager(repo, Agent())
        turn, _ = await manager.begin(state['id'], '继续')
        await turn.task
        assert repo.get(state['id'])['last_status'] == 'error'
    asyncio.run(run())
