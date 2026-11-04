"""Close interrupted tool batches without pretending unknown calls succeeded."""
import copy
import json


def continuation_history(checkpoint, progress):
    messages = copy.deepcopy(checkpoint['messages'])
    answered = {m.get('tool_call_id') for m in messages if m.get('role') == 'tool'}
    started = {c.get('call_id'): c for c in (progress or {}).get('tool_calls', [])}
    pending = []
    for message in messages:
        for call in message.get('tool_calls', []):
            if call['id'] not in answered:
                pending.append(call)
    uncertain = []
    for call in pending:
        function = call['function']
        entered = call['id'] in started
        result = {'status': 'execution_unknown' if entered else 'not_executed',
                  'message': '执行中断，结果未确认；先读取当前业务状态，不要重复写入。' if entered
                  else '调用尚未开始，用户停止了本轮。'}
        messages.append({'role': 'tool', 'name': function['name'], 'tool_call_id': call['id'],
                         'content': json.dumps(result, ensure_ascii=False)})
        if entered:
            uncertain.append({'name': function['name'], 'arguments': json.loads(function['arguments'])})
    partial = (progress or {}).get("text", "")
    if partial and (not messages or messages[-1].get("content") != partial):
        messages.append({"role": "assistant", "content": partial})
    return messages, uncertain


def preserve_progress(state):
    progress = state.get('active_turn') or {}
    if progress:
        state['interrupted_progress'] = copy.deepcopy(progress)
        state['last_run'] = {'id': progress.get('id'), 'request_id': progress.get('request_id'),
                             'status': 'interrupted', 'message': progress.get('message'),
                             'result': {'text': progress.get('text', ''), 'process': progress.get('process', []),
                                        'tool_calls': progress.get('tool_calls', []), 'actions': progress.get('actions', [])}}
