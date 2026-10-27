import {useCallback, useEffect, useRef, useState} from 'react';
import {api} from '../../runtime/http';
import {Icon} from '../../components/Icon';
import {Dialog} from '../../components/Dialog';
import {Select} from '../../components/ui/Select';
import {AccountLeavePrompt, useAccountLeaveGuard} from './leave-guard';
import './model-manager.css';

type Tariff = {currency: string; input_per_million: string; cached_input_per_million: string; output_per_million: string; configured: boolean};
export type ModelProfile = {id: string; revision: number; name: string; provider: string; model: string; base_url: string; api_format: string; stream_tool_calls: boolean; supports_images: boolean; max_images: number; api_key_set: boolean; pricing: Tariff; active: boolean; last_test: {ok: boolean; message?: string; error?: string; outdated: boolean; tested_at: string} | null};
type Registry = {revision: number; active_id: string; items: ModelProfile[]};
type Draft = Omit<ModelProfile, 'id' | 'revision' | 'api_key_set' | 'active' | 'last_test'> & {api_key: string; clear_api_key: boolean};
const empty: Draft = {name: '', provider: 'openai', model: '', base_url: '', api_format: 'chat_completions', stream_tool_calls: false, supports_images: false, max_images: 3, api_key: '', clear_api_key: false, pricing: {currency: 'CNY', input_per_million: '0', cached_input_per_million: '0', output_per_million: '0', configured: false}};
function draftOf(profile?: ModelProfile): Draft {
  if (!profile) return structuredClone(empty);
  const {name, provider, model, base_url, api_format, stream_tool_calls, supports_images, max_images, pricing} = profile;
  return {name, provider, model, base_url, api_format, stream_tool_calls, supports_images, max_images, pricing: {...pricing}, api_key: '', clear_api_key: false};
}
export function ModelManager({notify}: {notify: (message: string) => void}) {
  const [data, setData] = useState<Registry | null>(null), [error, setError] = useState(''), [editor, setEditor] = useState<ModelProfile | 'new' | null>(null), [busy, setBusy] = useState(''), [remove, setRemove] = useState<ModelProfile | null>(null);
  const alive = useRef(true), working = useRef(false);
  const load = useCallback(async (signal?: AbortSignal) => {
    const value = await api<Registry>('/api/admin/models', {signal});
    if (alive.current && !signal?.aborted) setData(value);
  }, []);
  useEffect(() => {alive.current = true; const controller = new AbortController(); void load(controller.signal).catch(problem => {if (!controller.signal.aborted) setError(problem.message);}); return () => {alive.current = false; controller.abort();};}, [load]);
  const {pendingLeave, setPendingLeave} = useAccountLeaveGuard(false, Boolean(busy), setError);
  const perform = async (profile: ModelProfile, operation: 'activate' | 'test' | 'delete') => {
    if (working.current || !data) return;
    working.current = true; setBusy(`${profile.id}:${operation}`); setError('');
    try {
      const path = `/api/admin/models/${encodeURIComponent(profile.id)}`;
      const result = await api<{ok?: boolean; message?: string}>(operation === 'delete' ? `${path}?revision=${data.revision}` : `${path}/${operation}`, {method: operation === 'delete' ? 'DELETE' : 'POST', ...(operation !== 'delete' && {body: JSON.stringify({revision: data.revision})})});
      if (alive.current) {setRemove(null); await load(); notify(operation === 'test' ? result.ok ? '模型连接与原生工具调用测试通过。' : result.message || '模型测试未通过，请查看测试结果。' : operation === 'activate' ? '已切换当前使用的模型。' : '模型配置已删除。');}
    } catch (problem) {if (alive.current) setError((problem as Error).message);}
    finally {working.current = false; if (alive.current) setBusy('');}
  };
  return <div className="model-manager">
    <div className="admin-section-heading"><div><h2>模型连接</h2><p className="account-fineprint">每个连接有独立的服务商、密钥与单价。当前只启用一个模型。</p></div><div className="account-form-actions"><button type="button" className="secondary-button" disabled={Boolean(busy)} onClick={() => {const resume = () => {setError(''); void load().catch(problem => setError(problem.message));}; if (window.dispatchEvent(new CustomEvent('chefmate:before-page-change', {cancelable: true, detail: {page: 'admin-model-refresh', resume}}))) resume();}}><Icon name="refresh-cw"/>重新读取模型</button><button type="button" className="primary-button" disabled={Boolean(busy) || !data} onClick={() => setEditor('new')}><Icon name="plus"/>添加模型</button></div></div>
    {error && <p className="error-box" role="alert">{error}<button type="button" onClick={() => {setError(''); void load().catch(problem => setError(problem.message));}} disabled={Boolean(busy)}>重新读取</button></p>}
    {!data && !error && <p role="status">正在读取模型连接…</p>}
    {data && <div className="model-profile-grid">{data.items.map(profile => <article className="model-profile-card" key={profile.id} data-active={profile.active}>
      <header><span className="model-profile-icon"><Icon name="plug"/></span><div><h3>{profile.name}</h3><p>{profile.provider} · {profile.model}</p></div><span className="ui-badge">{profile.active ? '正在使用' : '备用'}</span></header>
      <p className="model-endpoint" title={profile.base_url}>{profile.base_url || '默认服务端点'}</p>
      <dl><div><dt>协议</dt><dd>{profile.api_format === 'responses' ? 'Responses' : 'Chat Completions'}</dd></div><div><dt>密钥</dt><dd>{profile.api_key_set ? '已设置' : '未设置'}</dd></div><div><dt>输入 / 输出</dt><dd>¥{profile.pricing.input_per_million} / ¥{profile.pricing.output_per_million}<small>每百万 Token</small></dd></div></dl>
      {!profile.pricing.configured && <p className="account-notice">单价待确认，金额受限账号暂不能使用此模型。</p>}
      {profile.last_test && <p className="model-test-result" role="status" data-ok={profile.last_test.ok}>{profile.last_test.outdated ? '配置已修改，请重新测试。' : profile.last_test.ok ? '连接与工具测试通过' : profile.last_test.message || profile.last_test.error || '测试未通过'}<small>{new Date(profile.last_test.tested_at).toLocaleString('zh-CN')}</small></p>}
      <footer><button type="button" className="secondary-button" disabled={Boolean(busy)} onClick={() => setEditor(profile)}>编辑连接与单价</button><button type="button" className="text-button" disabled={Boolean(busy)} onClick={() => void perform(profile, 'test')}>{busy === `${profile.id}:test` ? '测试中…' : '测试'}</button>{!profile.active && <><button type="button" className="text-button" disabled={Boolean(busy)} onClick={() => void perform(profile, 'activate')}>启用</button><button type="button" className="icon-button" aria-label={`删除模型 ${profile.name}`} disabled={Boolean(busy)} onClick={() => setRemove(profile)}><Icon name="trash-2"/></button></>}</footer>
    </article>)}</div>}
    <p className="account-fineprint">测试会向该连接发送简短的合成测试内容，检查响应和原生工具调用，按该模型保存的价格计入你的用量。</p>
    {editor && data && <ModelEditor profile={editor === 'new' ? undefined : editor} revision={data.revision} onClose={() => setEditor(null)} onSaved={next => {setData(next); setEditor(null); notify('模型连接与单价已保存。');}}/>}
    {remove && <Dialog title="删除这个备用模型？" onClose={() => {if (!busy) setRemove(null);}}><div className="account-leave-prompt"><p>将删除“{remove.name}”的连接配置，历史请求的模型与价格快照会保留。</p><div className="account-form-actions"><button type="button" className="secondary-button" disabled={Boolean(busy)} onClick={() => setRemove(null)}>取消</button><button type="button" className="primary-button" disabled={Boolean(busy)} onClick={() => void perform(remove, 'delete')}>确认删除</button></div></div></Dialog>}
    <AccountLeavePrompt action={pendingLeave} onCancel={() => setPendingLeave(null)}/>
  </div>;
}

function ModelEditor({profile, revision, onClose, onSaved}: {profile?: ModelProfile; revision: number; onClose: () => void; onSaved: (data: Registry) => void}) {
  const baseline = useRef(draftOf(profile)), [draft, setDraft] = useState<Draft>(() => draftOf(profile)), [error, setError] = useState(''), [busy, setBusy] = useState(false), [conflict, setConflict] = useState(false);
  const alive = useRef(true), working = useRef(false);
  useEffect(() => {alive.current = true; return () => {alive.current = false;};}, []);
  const dirty = JSON.stringify(draft) !== JSON.stringify(baseline.current);
  const {pendingLeave, setPendingLeave} = useAccountLeaveGuard(dirty, busy, setError);
  const close = () => {if (!busy) {if (dirty) setPendingLeave(() => onClose); else onClose();}};
  const patch = <K extends keyof Draft>(key: K, value: Draft[K]) => setDraft(previous => ({...previous, [key]: value}));
  async function save() {
    if (working.current || conflict) return;
    working.current = true; setBusy(true); setError('');
    try {
      const values = {...draft};
      const payload: Record<string, unknown> = {...values};
      if (!values.api_key.trim()) delete payload.api_key;
      if (!profile) delete payload.clear_api_key;
      const next = await api<Registry>(profile ? `/api/admin/models/${encodeURIComponent(profile.id)}` : '/api/admin/models', {method: profile ? 'PATCH' : 'POST', body: JSON.stringify(profile ? {revision, values: payload} : {revision, ...payload})});
      if (alive.current) onSaved(next);
    } catch (problem) {if (alive.current) {setError((problem as Error).message); if ((problem as {status?: number}).status === 409) setConflict(true);}}
    finally {working.current = false; if (alive.current) setBusy(false);}
  }
  return <><Dialog className="model-connection-dialog" title={profile ? `编辑 ${profile.name}` : '添加模型连接'} onClose={close}><form className="model-editor" onSubmit={event => {event.preventDefault(); void save();}}>
    <div className="model-form-grid">{([['name', '连接名称', '例如：日常厨房助手'], ['provider', '提供商', 'openai'], ['model', '模型名称', '服务商提供的 MODELNAME'], ['base_url', 'Base URL', 'https://example.com/v1']] as const).map(([key, label, placeholder]) => <label className="account-field" key={key}><span>{label}</span><input required maxLength={key === 'base_url' ? 500 : key === 'model' ? 200 : 80} value={draft[key]} disabled={busy} onChange={event => patch(key, event.target.value)} placeholder={placeholder}/></label>)}</div>
    <div className="account-field"><label htmlFor="model-api-format">协议</label><Select id="model-api-format" value={draft.api_format} disabled={busy} onValueChange={value => patch('api_format', value)} options={[{value: 'chat_completions', label: 'Chat Completions'}, {value: 'responses', label: 'Responses'}]}/></div>
    <label className="account-field"><span>API 密钥</span><input type="password" autoComplete="new-password" maxLength={2000} disabled={busy || draft.clear_api_key} value={draft.api_key} onChange={event => patch('api_key', event.target.value)} placeholder={profile?.api_key_set ? '已设置，留空保留' : '输入这个连接的专用密钥'}/><small>更换服务商或端点来源时，请提供对应密钥或明确清除旧密钥。</small></label>
    {profile?.api_key_set && <label className="admin-check"><span>清除已保存的密钥</span><input type="checkbox" checked={draft.clear_api_key} disabled={busy} onChange={event => setDraft(previous => ({...previous, clear_api_key: event.target.checked, api_key: ''}))}/></label>}
    <div className="model-form-grid"><label className="admin-check"><span>流式工具调用</span><input type="checkbox" checked={draft.stream_tool_calls} disabled={busy} onChange={event => patch('stream_tool_calls', event.target.checked)}/></label><label className="admin-check"><span>支持图片输入</span><input type="checkbox" checked={draft.supports_images} disabled={busy} onChange={event => patch('supports_images', event.target.checked)}/></label></div>
    {draft.supports_images && <label className="account-field"><span>单轮图片上限</span><input type="number" required min={1} max={12} value={draft.max_images} disabled={busy} onChange={event => patch('max_images', Number(event.target.value))}/></label>}
    <fieldset className="model-price-fields"><legend>这个模型的价格</legend><p className="account-fineprint">人民币 / 百万 Token。每轮请求保存价格快照。</p>{([['input_per_million', '输入单价'], ['output_per_million', '输出单价'], ['cached_input_per_million', '缓存命中输入单价']] as const).map(([key, label]) => <label className="account-field" key={key}><span>{label}</span><input type="number" min="0" max="1000000" step="0.000001" required value={draft.pricing[key]} disabled={busy} onChange={event => setDraft(previous => ({...previous, pricing: {...previous.pricing, [key]: event.target.value}}))}/></label>)}<label className="admin-check"><span><strong>已核对并确认此模型计价</strong><small>确认后，有金额额度的账号才能使用这个连接。</small></span><input type="checkbox" disabled={busy} checked={draft.pricing.configured} onChange={event => setDraft(previous => ({...previous, pricing: {...previous.pricing, configured: event.target.checked}}))}/></label></fieldset>
    {error && <p className="error-box" role="alert">{error}{conflict && <span>草稿已保留，请关闭后重新读取配置再核对。</span>}</p>}
    <div className="account-form-actions"><button type="button" className="text-button" disabled={busy} onClick={close}>取消</button><button type="submit" className="primary-button" disabled={busy || conflict || Boolean(profile && !dirty)}>{busy ? '正在保存…' : '保存连接与价格'}</button></div>
  </form></Dialog><AccountLeavePrompt action={pendingLeave} onCancel={() => setPendingLeave(null)}/></>;
}
