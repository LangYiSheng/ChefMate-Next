import {useEffect, useId, useRef, useState} from 'react';
import {api} from '../../runtime/http';
import {Field} from './SettingsFields';
import {Icon} from '../../components/Icon';

export function MemorySettings({active, onDirty, onBusy, onAccount}: {active: boolean; onDirty: (dirty: boolean) => void; onBusy: (busy: boolean) => void; onAccount?: () => void}) {
  const [record, setRecord] = useState<any>(null), [notes, setNotes] = useState(''), [enabled, setEnabled] = useState(true);
  const [busy, setBusy] = useState(false), [tab, setTab] = useState('preferences'), [message, setMessage] = useState(''), [failed, setFailed] = useState(false);
  const [confirmation, setConfirmation] = useState<'clear' | 'reload' | null>(null), [retry, setRetry] = useState(0);
  const alive = useRef(true), working = useRef(false), indexId = useId();
  const adopt = (value: any) => {setRecord(value); setNotes(value.user_notes); setEnabled(value.enabled);};
  useEffect(() => {alive.current = true; return () => {alive.current = false;};}, []);
  useEffect(() => {
    if (!active || record) return;
    const controller = new AbortController(); setMessage('正在读取记忆…'); setFailed(false);
    api('/api/memory', {signal: controller.signal}).then(value => {if (!controller.signal.aborted) {adopt(value); setMessage('');}}).catch(error => {if (!controller.signal.aborted) {setMessage(error.message); setFailed(true);}});
    return () => controller.abort();
  }, [active, record, retry]);
  const dirty = Boolean(record && (notes !== record.user_notes || enabled !== record.enabled));
  useEffect(() => {onDirty(dirty);}, [dirty, onDirty]);
  const run = async (operation: () => Promise<any>, success: string) => {
    if (working.current) return;
    working.current = true; setBusy(true); onBusy(true); setMessage('正在处理记忆…'); setFailed(false);
    try {const accepted = await operation(); if (!alive.current) return; adopt(accepted); setConfirmation(null); setMessage(success);}
    catch (error) {if (alive.current) {setMessage((error as Error).message); setFailed(true);}}
    finally {working.current = false; if (alive.current) {setBusy(false); onBusy(false);}}
  };
  return <section hidden={!active} className="memory-settings">
    {!record ? <div className="settings-load-state"><p role="status" className={failed ? 'settings-error' : 'settings-note'}>{message || '正在读取记忆…'}</p>{failed && <button type="button" className="secondary-button" onClick={() => setRetry(value => value + 1)}>重新读取记忆</button>}</div> : <>
      <div role="tablist" aria-label="长期记忆内容" className="settings-tabs memory-tabs" onKeyDown={event => {if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return; event.preventDefault(); const next = event.key === 'Home' ? 'preferences' : event.key === 'End' ? 'summary' : tab === 'preferences' ? 'summary' : 'preferences'; setTab(next); event.currentTarget.querySelector<HTMLButtonElement>(`#memory-tab-${next}`)?.focus();}}>{[['preferences', '我的偏好'], ['summary', '助手总结']].map(([id, title]) => <button type="button" key={id} className="settings-tab" role="tab" aria-selected={id === tab} aria-controls={`memory-${id}`} id={`memory-tab-${id}`} tabIndex={id === tab ? 0 : -1} onClick={() => setTab(id)}>{title}</button>)}</div>
      <div role="tabpanel" id="memory-preferences" aria-labelledby="memory-tab-preferences" hidden={tab !== 'preferences'}>
        <div id={`${indexId}-enabled`} data-settings-index="记忆的使用"><Field label="在对话中使用长期记忆" type="checkbox" value={enabled} onChange={setEnabled} disabled={busy} hint="保存口味与习惯，让后续对话更合心意。"/></div>
        {record.kitchen_profile && Object.keys(record.kitchen_profile).length > 0 && <section id={`${indexId}-kitchen-profile-section`} className="memory-kitchen-profile" aria-labelledby={`${indexId}-kitchen-profile`} data-settings-index="厨房档案"><div><h2 id={`${indexId}-kitchen-profile`}><Icon name="chef-hat"/>厨房档案</h2><a href="/account" onClick={event => {if (onAccount) {event.preventDefault(); onAccount();}}}>到账号页修改<Icon name="chevron-right"/></a></div><p>{enabled ? '来自你在账号中开启的档案同步，用于后续对话建议。' : '当前已停用长期记忆，这份档案暂不用于对话建议。'}</p><dl>{record.kitchen_profile.display_name && <div><dt>称呼</dt><dd>{record.kitchen_profile.display_name}</dd></div>}{record.kitchen_profile.household_size && <div><dt>平时用餐</dt><dd>{record.kitchen_profile.household_size} 人</dd></div>}{record.kitchen_profile.cooking_level && <div><dt>下厨经验</dt><dd>{({beginner: '刚刚开始', comfortable: '日常下厨', confident: '厨房熟手'} as Record<string, string>)[record.kitchen_profile.cooking_level] || record.kitchen_profile.cooking_level}</dd></div>}{record.kitchen_profile.preferences?.length > 0 && <div><dt>厨房偏好</dt><dd>{record.kitchen_profile.preferences.join('、')}</dd></div>}</dl></section>}
        <div id={`${indexId}-notes`} data-settings-index="我的偏好"><Field label="你希望记住的事" type="textarea" value={notes} onChange={setNotes} disabled={busy} maxLength={6000} hint="可以写口味、忌口、常用厨具和做饭习惯。"/></div>
        <p className="memory-count">{notes.length} / 6000 字</p>
      </div>
      <div role="tabpanel" id="memory-summary" aria-labelledby="memory-tab-summary" hidden={tab !== 'summary'}><div id={`${indexId}-summary`} data-settings-index="助手总结"><h2>助手整理的记忆</h2><p className="memory-summary">{record.summary || '还没有总结。聊过几次后，你的口味、忌口和做饭习惯会慢慢整理在这里。'}</p></div></div>
      <div className="memory-actions" id={`${indexId}-actions`} data-settings-index="记忆管理"><button type="button" className="text-button memory-clear" disabled={busy} onClick={() => setConfirmation('clear')}>清空记忆</button><div className="memory-primary-actions"><button type="button" className="text-button" disabled={busy} onClick={() => dirty ? setConfirmation('reload') : run(() => api('/api/memory'), '已读取最新记忆。')}>读取最新</button><button type="button" className="primary-button" disabled={busy || !dirty} onClick={() => run(() => api('/api/memory', {method: 'PATCH', body: JSON.stringify({revision: record.revision, user_notes: notes, enabled})}), '记忆已保存。')}>保存记忆</button></div></div>
      {confirmation && <div className="memory-confirm" role="alertdialog" aria-label={confirmation === 'clear' ? '清空长期记忆' : '重新读取长期记忆'}><p>{confirmation === 'clear' ? '会清空你写的偏好、助手总结和用于对话的厨房档案。历史对话、饮食记录和账号资料仍保留；修改账号档案或重新开启档案使用后，才会恢复这份档案。' : '重新读取会放弃当前未保存的偏好和开关修改，改用服务器里的最新记忆。'}</p><div><button type="button" className="text-button" disabled={busy} onClick={() => setConfirmation(null)}>保留草稿</button><button type="button" className="secondary-button" disabled={busy} onClick={() => confirmation === 'clear' ? run(() => api(`/api/memory?revision=${record.revision}`, {method: 'DELETE'}), '记忆已清空。') : run(() => api('/api/memory'), '已读取最新记忆。')}>{confirmation === 'clear' ? '确认清空' : '放弃修改并读取'}</button></div></div>}
      <p className={`memory-status ${failed ? 'settings-error' : 'setting-help'}`} role="status">{message || '记忆保存在当前账号，可随时修改。'}</p>
    </>}
  </section>;
}
