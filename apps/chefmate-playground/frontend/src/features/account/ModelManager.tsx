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
const priceFormat = new Intl.NumberFormat('zh-CN', {maximumFractionDigits: 6});
const shownPrice = (value: string) => Number.isFinite(Number(value)) ? priceFormat.format(Number(value)) : value;
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
  return <div className="model-manager" data-count={data?.items.length}>
    <div className="model-manager-toolbar"><p>{data ? <><strong>{data.items.length}</strong> 个已保存的连接</> : '模型连接'}<span>每次使用一个模型</span></p><div className="model-manager-actions"><button type="button" className="secondary-button" aria-label="重新读取模型" disabled={Boolean(busy)} onClick={() => {const resume = () => {setError(''); void load().catch(problem => setError(problem.message));}; if (window.dispatchEvent(new CustomEvent('chefmate:before-page-change', {cancelable: true, detail: {page: 'admin-model-refresh', resume}}))) resume();}}><Icon name="refresh-cw"/>刷新</button><button type="button" className="primary-button" disabled={Boolean(busy) || !data} onClick={() => setEditor('new')}><Icon name="plus"/>添加模型</button></div></div>
    {error && <p className="error-box" role="alert">{error}<button type="button" onClick={() => {setError(''); void load().catch(problem => setError(problem.message));}} disabled={Boolean(busy)}>重新读取</button></p>}
    {!data && !error && <p role="status">正在读取模型连接…</p>}
    {data && <div className="model-profile-grid">{data.items.map(profile => <article className="model-profile-card" key={profile.id} data-active={profile.active} aria-label={profile.name}>
      <header><span className="model-profile-icon"><Icon name="plug"/></span><span className="model-profile-provider">{profile.provider}</span><span className="model-profile-state">{profile.active && <Icon name="check"/>}{profile.active ? '正在使用' : '备用'}</span></header>
      <div className="model-profile-title"><h3>{profile.name}</h3><p>{profile.model || '尚未填写模型名称'}</p></div>
      <div className="model-connection-details"><p className="model-endpoint" title={profile.base_url}><Icon name="external-link"/><span>{profile.base_url || '默认服务端点'}</span></p><dl className="model-connection-meta"><div><dt>协议</dt><dd>{profile.api_format === 'responses' ? 'Responses' : 'Chat Completions'}</dd></div><div><dt>密钥</dt><dd>{profile.api_key_set ? '已设置' : '未设置'}</dd></div></dl></div>
      <div className="model-price-summary"><div><span>每百万 Token · 人民币</span>{!profile.pricing.configured && <span className="model-price-pending">单价待确认</span>}</div><dl>{([['input_per_million', '输入'], ['cached_input_per_million', '缓存输入'], ['output_per_million', '输出']] as const).map(([key, label]) => <div key={key}><dt>{label}</dt><dd>¥{shownPrice(profile.pricing[key])}</dd></div>)}</dl></div>
      <div className="model-card-status">{profile.last_test ? <p className="model-test-result" role="status" data-state={profile.last_test.outdated ? 'outdated' : profile.last_test.ok ? 'success' : 'error'}><Icon name={profile.last_test.outdated ? 'refresh-cw' : profile.last_test.ok ? 'circle-check' : 'circle-alert'}/><span>{profile.last_test.outdated ? '配置已修改，请重新测试' : profile.last_test.ok ? '连接与工具测试通过' : profile.last_test.message || profile.last_test.error || '测试未通过'}<small>{new Date(profile.last_test.tested_at).toLocaleString('zh-CN')}</small></span></p> : <p className="model-test-empty"><Icon name="circle-help"/>尚未测试</p>}</div>
      <footer><button type="button" className="secondary-button" aria-label="编辑连接与单价" disabled={Boolean(busy)} onClick={() => setEditor(profile)}><Icon name="notebook-pen"/>编辑</button><button type="button" className="text-button" title="测试连接及原生工具调用，会计入用量" disabled={Boolean(busy)} onClick={() => void perform(profile, 'test')}>{busy === `${profile.id}:test` ? '测试中…' : '测试'}</button>{!profile.active && <><button type="button" className="model-activate text-button" disabled={Boolean(busy)} onClick={() => void perform(profile, 'activate')}>启用</button><button type="button" className="icon-button" aria-label={`删除模型 ${profile.name}`} disabled={Boolean(busy)} onClick={() => setRemove(profile)}><Icon name="trash-2"/></button></>}</footer>
    </article>)}</div>}
    <p className="model-manager-note"><Icon name="plug"/>连接测试会调用对应模型，并按其单价计入你的用量。</p>
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
    <section className="model-editor-section" aria-labelledby="model-connection-heading"><h3 id="model-connection-heading">连接信息</h3><div className="model-form-grid">{([['name', '连接名称', '例如：日常厨房助手'], ['provider', '提供商', '例如：OpenAI']] as const).map(([key, label, placeholder]) => <label className="account-field" key={key}><span>{label}</span><input required maxLength={80} value={draft[key]} disabled={busy} onChange={event => patch(key, event.target.value)} placeholder={placeholder}/></label>)}<label className="account-field"><span>模型名称</span><input required maxLength={200} value={draft.model} disabled={busy} onChange={event => patch('model', event.target.value)} placeholder="服务商提供的模型名称"/></label><div className="account-field"><label htmlFor="model-api-format">协议</label><Select id="model-api-format" value={draft.api_format} disabled={busy} onValueChange={value => patch('api_format', value)} options={[{value: 'chat_completions', label: 'Chat Completions'}, {value: 'responses', label: 'Responses'}]}/></div></div>
    <label className="account-field"><span>Base URL</span><input required maxLength={500} value={draft.base_url} disabled={busy} onChange={event => patch('base_url', event.target.value)} placeholder="https://example.com/v1"/></label>
    <div className="account-field"><label htmlFor="model-api-key">API 密钥</label><input id="model-api-key" aria-describedby="model-api-key-hint" type="password" autoComplete="new-password" maxLength={2000} disabled={busy || draft.clear_api_key} value={draft.api_key} onChange={event => patch('api_key', event.target.value)} placeholder={profile?.api_key_set ? '已设置，留空保留' : '输入这个连接的专用密钥'}/><small id="model-api-key-hint">更换服务商或端点来源时，请提供对应密钥或明确清除旧密钥。</small></div>
    {profile?.api_key_set && <label className="admin-check model-clear-key"><span>清除已保存的密钥</span><input type="checkbox" checked={draft.clear_api_key} disabled={busy} onChange={event => setDraft(previous => ({...previous, clear_api_key: event.target.checked, api_key: ''}))}/></label>}</section>
    <section className="model-editor-section" aria-labelledby="model-capabilities-heading"><h3 id="model-capabilities-heading">调用能力</h3><div className="model-form-grid model-capabilities"><label className="admin-check"><span>流式工具调用</span><input type="checkbox" checked={draft.stream_tool_calls} disabled={busy} onChange={event => patch('stream_tool_calls', event.target.checked)}/></label><label className="admin-check"><span>支持图片输入</span><input type="checkbox" checked={draft.supports_images} disabled={busy} onChange={event => patch('supports_images', event.target.checked)}/></label></div>
    {draft.supports_images && <label className="account-field model-image-limit"><span>单轮图片上限</span><input type="number" required min={1} max={12} value={draft.max_images} disabled={busy} onChange={event => patch('max_images', Number(event.target.value))}/></label>}</section>
    <section className="model-price-fields" aria-labelledby="model-price-heading"><header><h3 id="model-price-heading">模型计价</h3><span>人民币 / 百万 Token</span></header><div className="model-price-inputs">{([['input_per_million', '输入单价'], ['cached_input_per_million', '缓存命中输入单价'], ['output_per_million', '输出单价']] as const).map(([key, label]) => <label className="account-field" key={key}><span>{label}</span><input type="number" min="0" max="1000000" step="0.000001" required value={draft.pricing[key]} disabled={busy} onChange={event => setDraft(previous => ({...previous, pricing: {...previous.pricing, [key]: event.target.value}}))}/></label>)}</div><label className="admin-check model-price-confirm"><span><strong>已核对并确认此模型计价</strong><small>用于用户扣费与成本统计。</small></span><input type="checkbox" disabled={busy} checked={draft.pricing.configured} onChange={event => setDraft(previous => ({...previous, pricing: {...previous.pricing, configured: event.target.checked}}))}/></label></section>
    {error && <p className="error-box" role="alert">{error}{conflict && <span>草稿已保留，请关闭后重新读取配置再核对。</span>}</p>}
    <div className="model-editor-actions"><button type="button" className="text-button" disabled={busy} onClick={close}>取消</button><button type="submit" className="primary-button" disabled={busy || conflict || Boolean(profile && !dirty)}>{busy ? '正在保存…' : '保存连接与价格'}</button></div>
  </form></Dialog><AccountLeavePrompt action={pendingLeave} onCancel={() => setPendingLeave(null)}/></>;
}
