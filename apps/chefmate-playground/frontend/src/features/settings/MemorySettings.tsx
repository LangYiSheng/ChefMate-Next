import {useEffect, useId, useRef, useState} from 'react';
import {api} from '../../runtime/http';
import {Field} from './SettingsFields';
import {Icon} from '../../components/Icon';
import {PROFILE_PREFERENCES} from '../account/types';

type PreferencesRecord = {revision: string; enabled: boolean; user_notes: string; summary: string; cooking_level: string; household_size: number; preferences: string[]};
const snapshot = (record: PreferencesRecord) => JSON.stringify([record.enabled, record.user_notes, record.cooking_level, record.household_size, record.preferences]);
export function MemorySettings({active, onDirty, onBusy}: {active: boolean; onDirty: (dirty: boolean) => void; onBusy: (busy: boolean) => void; onAccount?: () => void}) {
  const [record, setRecord] = useState<PreferencesRecord | null>(null), [notes, setNotes] = useState(''), [enabled, setEnabled] = useState(true);
  const [level, setLevel] = useState('beginner'), [household, setHousehold] = useState(1), [choices, setChoices] = useState<string[]>([]);
  const [busy, setBusy] = useState(false), [tab, setTab] = useState('preferences'), [message, setMessage] = useState(''), [failed, setFailed] = useState(false);
  const [confirmation, setConfirmation] = useState<'clear-summary' | 'reload' | null>(null), [retry, setRetry] = useState(0);
  const alive = useRef(true), working = useRef(false), indexId = useId();
  const adopt = (value: PreferencesRecord) => {setRecord(value); setNotes(value.user_notes); setEnabled(value.enabled); setLevel(value.cooking_level); setHousehold(value.household_size); setChoices([...value.preferences]);};
  useEffect(() => {alive.current = true; return () => {alive.current = false;};}, []);
  useEffect(() => {
    if (!active || record) return;
    const controller = new AbortController(); setMessage('正在读取个人偏好…'); setFailed(false);
    api<PreferencesRecord>('/api/personal-preferences', {signal: controller.signal}).then(value => {if (!controller.signal.aborted) {adopt(value); setMessage('');}}).catch(error => {if (!controller.signal.aborted) {setMessage(error.message); setFailed(true);}});
    return () => controller.abort();
  }, [active, record, retry]);
  const dirty = Boolean(record && snapshot({revision: record.revision, summary: record.summary, enabled, user_notes: notes, cooking_level: level, household_size: household, preferences: choices}) !== snapshot(record));
  useEffect(() => {onDirty(dirty);}, [dirty, onDirty]);
  const run = async (operation: () => Promise<PreferencesRecord>, success: string, preserveDraft = false) => {
    if (working.current) return;
    working.current = true; setBusy(true); onBusy(true); setMessage('正在保存个人偏好…'); setFailed(false);
    try {const accepted = await operation(); if (!alive.current) return; if (preserveDraft) setRecord(accepted); else adopt(accepted); setConfirmation(null); setMessage(success);}
    catch (error) {if (alive.current) {setMessage((error as Error).message); setFailed(true);}}
    finally {working.current = false; if (alive.current) {setBusy(false); onBusy(false);}}
  };
  const availableChoices = [...new Set([...PROFILE_PREFERENCES, ...(record?.preferences || []), ...choices])];
  return <section hidden={!active} className="memory-settings personal-preferences-settings">
    {!record ? <div className="settings-load-state"><p role="status" className={failed ? 'settings-error' : 'settings-note'}>{message || '正在读取个人偏好…'}</p>{failed && <button type="button" className="secondary-button" onClick={() => setRetry(value => value + 1)}>重新读取个人偏好</button>}</div> : <>
      <div role="tablist" aria-label="个人偏好内容" className="settings-tabs memory-tabs" onKeyDown={event => {if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return; event.preventDefault(); const next = event.key === 'Home' ? 'preferences' : event.key === 'End' ? 'summary' : tab === 'preferences' ? 'summary' : 'preferences'; setTab(next); event.currentTarget.querySelector<HTMLButtonElement>('#memory-tab-' + next)?.focus();}}>{[['preferences', '个人偏好'], ['summary', '长期记忆']].map(([id, title]) => <button type="button" key={id} className="settings-tab" role="tab" aria-selected={id === tab} aria-controls={'memory-' + id} id={'memory-tab-' + id} tabIndex={id === tab ? 0 : -1} onClick={() => setTab(id)}>{title}</button>)}</div>
      <div role="tabpanel" id="memory-preferences" aria-labelledby="memory-tab-preferences" hidden={tab !== 'preferences'}>
        <div id={indexId + '-enabled'} data-settings-index="对话中的使用"><Field label="在对话中使用个人偏好与长期记忆" type="checkbox" value={enabled} onChange={setEnabled} disabled={busy} hint="统一控制厨房档案、你写的偏好和助手整理的长期记忆。关闭后仍会告诉模型你的称呼。"/></div>
        <section className="personal-preferences-kitchen" id={indexId + '-kitchen'} data-settings-index="厨房偏好"><div className="account-section-heading"><div><h2><Icon name="chef-hat"/>厨房偏好</h2><p>这是平时的习惯，具体一顿饭的要求仍以当前对话为准。</p></div></div>
          <div className="personal-preferences-grid"><Field label="平时用餐人数" value={String(household)} onChange={value => setHousehold(Number(value))} disabled={busy} options={Array.from({length: 20}, (_, index) => [String(index + 1), (index + 1) + ' 人'])}/><Field label="下厨经验" value={level} onChange={setLevel} disabled={busy} options={[['beginner', '刚刚开始'], ['comfortable', '日常下厨'], ['confident', '厨房熟手']]}/></div>
          <fieldset className="account-profile-preferences"><legend>口味与做饭习惯 <small>可多选</small></legend><div>{availableChoices.map(choice => <button type="button" key={choice} disabled={busy} aria-pressed={choices.includes(choice)} onClick={() => setChoices(current => current.includes(choice) ? current.filter(item => item !== choice) : [...current, choice])}>{choices.includes(choice) && <Icon name="check"/>}{choice}</button>)}</div></fieldset>
        </section>
        <div id={indexId + '-notes'} data-settings-index="补充偏好"><Field label="你希望记住的事" type="textarea" value={notes} onChange={setNotes} disabled={busy} maxLength={6000} hint="可以补充忌口、常用厨具和做饭习惯。"/></div>
        <p className="memory-count">{notes.length} / 6000 字</p>
      </div>
      <div role="tabpanel" id="memory-summary" aria-labelledby="memory-tab-summary" hidden={tab !== 'summary'}><div id={indexId + '-summary'} data-settings-index="长期记忆"><h2>长期记忆</h2><p className="setting-help">由助手从已授权的对话中整理，和你手动填写的偏好分开保存。</p><p className="memory-summary">{record.summary || '还没有长期记忆。聊过几次后，你的口味、忌口和做饭习惯会慢慢整理在这里。'}</p><button type="button" className="text-button memory-clear" disabled={busy} onClick={() => setConfirmation('clear-summary')}>清空长期记忆</button></div></div>
      <div className="memory-actions" id={indexId + '-actions'} data-settings-index="偏好管理"><p className="setting-help">称呼请在个人资料中修改。</p><div className="memory-primary-actions"><button type="button" className="text-button" disabled={busy} onClick={() => dirty ? setConfirmation('reload') : run(() => api('/api/personal-preferences'), '已读取最新个人偏好。')}>读取最新</button><button type="button" className="primary-button" disabled={busy || !dirty} onClick={() => run(() => api('/api/personal-preferences', {method: 'PATCH', body: JSON.stringify({revision: record.revision, user_notes: notes, enabled, cooking_level: level, household_size: household, preferences: choices})}), '个人偏好已保存。')}>保存个人偏好</button></div></div>
      {confirmation && <div className="memory-confirm" role="alertdialog" aria-label={confirmation === 'clear-summary' ? '清空长期记忆' : '重新读取个人偏好'}><p>{confirmation === 'clear-summary' ? '只会清空助手整理的长期记忆，手写偏好、厨房偏好和历史对话仍保留。' : '重新读取会放弃当前未保存的个人偏好和开关修改。'}</p><div><button type="button" className="text-button" disabled={busy} onClick={() => setConfirmation(null)}>保留草稿</button><button type="button" className="secondary-button" disabled={busy} onClick={() => confirmation === 'clear-summary' ? run(() => api('/api/personal-preferences/summary?revision=' + encodeURIComponent(record.revision), {method: 'DELETE'}), '长期记忆已清空。', true) : run(() => api('/api/personal-preferences'), '已读取最新个人偏好。')}>{confirmation === 'clear-summary' ? '确认清空长期记忆' : '放弃修改并读取'}</button></div></div>}
      <p className={'memory-status ' + (failed ? 'settings-error' : 'setting-help')} role="status">{message || '个人偏好按账号保存，可随时修改。'}</p>
    </>}
  </section>;
}
