import {useEffect, useState} from 'react';
import {api} from '../../runtime/http';
import {Field} from './SettingsFields';

export function MemorySettings({active, onDirty, onBusy}: {active: boolean; onDirty: (dirty: boolean) => void; onBusy: (busy: boolean) => void}) {
  const [record, setRecord] = useState<any>(null);
  const [notes, setNotes] = useState('');
  const [enabled, setEnabled] = useState(true);
  const [busy, setBusy] = useState(false);
  const [tab, setTab] = useState('preferences');
  const [message, setMessage] = useState('');
  const [confirmClear, setConfirmClear] = useState(false);
  const adopt = (value: any) => {setRecord(value); setNotes(value.user_notes); setEnabled(value.enabled);};
  useEffect(() => {
    if (!active || record) return;
    const controller = new AbortController();
    api('/api/memory', {signal: controller.signal}).then(adopt).catch(error => {if (error.name !== 'AbortError') setMessage(error.message);});
    return () => controller.abort();
  }, [active, record]);
  const dirty = record && (notes !== record.user_notes || enabled !== record.enabled);
  useEffect(() => {onDirty(!!dirty);}, [dirty, onDirty]);
  const run = async (operation: () => Promise<any>, success: string) => {
    if (busy) return;
    setBusy(true); onBusy(true); setMessage('正在处理记忆…');
    try {adopt(await operation()); setConfirmClear(false); setMessage(success);}
    catch (error) {setMessage((error as Error).message);}
    finally {setBusy(false); onBusy(false);}
  };
  return <section hidden={!active} className="memory-settings">
    {!record ? <p role="status">{message || '正在读取记忆…'}</p> : <>
      <div role="tablist" aria-label="长期记忆内容" className="settings-tabs memory-tabs" onKeyDown={event => {if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return; event.preventDefault(); const next = event.key === 'Home' ? 'preferences' : event.key === 'End' ? 'summary' : tab === 'preferences' ? 'summary' : 'preferences'; setTab(next); event.currentTarget.querySelector<HTMLButtonElement>(`#memory-tab-${next}`)?.focus();}}>{[['preferences', '我的偏好'], ['summary', '助手总结']].map(([id, title]) => <button type="button" key={id} className="settings-tab" role="tab" aria-selected={id === tab} aria-controls={`memory-${id}`} id={`memory-tab-${id}`} tabIndex={id === tab ? 0 : -1} onClick={() => setTab(id)}>{title}</button>)}</div>
      <div role="tabpanel" id="memory-preferences" aria-labelledby="memory-tab-preferences" hidden={tab !== 'preferences'}>
        <Field label="在对话中使用长期记忆" type="checkbox" value={enabled} onChange={setEnabled} disabled={busy}/>
        <Field label="你希望记住的事" type="textarea" value={notes} onChange={setNotes} disabled={busy} maxLength={6000} hint="可以写口味、忌口、常用厨具和做饭习惯。"/>
        <p className="settings-note">{notes.length} / 6000 字</p>
      </div>
      <div role="tabpanel" id="memory-summary" aria-labelledby="memory-tab-summary" hidden={tab !== 'summary'}><h2>助手整理的记忆</h2><p className="memory-summary">{record.summary || '还没有总结。聊过几次后，你的口味、忌口和做饭习惯会慢慢整理在这里。'}</p></div>
      <div className="memory-actions"><button type="button" className="text-button memory-clear" disabled={busy} onClick={() => setConfirmClear(true)}>清空记忆</button><div className="memory-primary-actions"><button type="button" className="text-button" disabled={busy} onClick={() => dirty ? setMessage('当前还有未保存修改，请先保存记忆。') : run(() => api('/api/memory'), '已读取最新记忆。')}>读取最新</button><button type="button" className="primary-button" disabled={busy || !dirty} onClick={() => run(() => api('/api/memory', {method: 'PATCH', body: JSON.stringify({revision: record.revision, user_notes: notes, enabled})}), '记忆已保存。')}>保存记忆</button></div></div>
      {confirmClear && <div className="memory-clear-confirm" role="alertdialog" aria-label="清空长期记忆"><p>会清空你写的偏好和助手总结，历史对话和饮食记录仍保留。</p><button type="button" className="text-button" disabled={busy} onClick={() => setConfirmClear(false)}>保留记忆</button><button type="button" className="secondary-button" disabled={busy} onClick={() => run(() => api(`/api/memory?revision=${record.revision}`, {method: 'DELETE'}), '记忆已清空。')}>确认清空</button></div>}
      <p className="setting-help memory-status" role="status">{message || '记忆保存在本机，可随时修改。'}</p>
    </>}
  </section>;
}
