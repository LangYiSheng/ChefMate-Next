import { useEffect, useId, useState } from 'react';
import { api, safeText } from '../../runtime/http';
import { Icon } from '../Icon';
import { Select } from '../ui/Select';
import { ComponentsCatalog, ToolsCatalog } from './Catalog';
import { Badge, Empty } from './Primitives';
import { ExecutionLog, TraceExplorer } from './Trace';
import { type Data, formatDate, stageLabels, statusLabels } from './model';
import './developer.css';

type Props = { sessionId: string | null; status: Data | null; developer: boolean };
type Mode = 'connection' | 'tools' | 'components' | 'logs' | 'context';
const tabs: [Mode, string, string][] = [['connection', '连接', 'plug'], ['tools', '工具', 'code-xml'], ['components', '组件', 'layers'], ['logs', '执行记录', 'list-checks'], ['context', '请求轨迹', 'message-circle']];

function Connection({ status }: { status: Data | null }) {
  return <section className="dev-connection"><header className="dev-section-title"><Icon name="plug" /><h3>运行连接</h3><Badge tone={status?.ready ? 'success' : 'warning'}>{status?.ready ? '服务就绪' : '等待就绪'}</Badge></header><dl>{[['服务', status?.ready ? '已连接' : '待连接'], ['模型', status?.model || '尚未配置'], ['菜谱库', status?.database?.ready ? '已连接' : '待连接'], ['菜谱数量', status?.database?.recipe_count ?? '未报告'], ['数据版本', status?.database?.data_version || '未报告']].map(([label, value]) => <div key={String(label)}><dt>{label}</dt><dd>{value}</dd></div>)}</dl><p className="dev-muted">调试面板只读取已留存数据，不发起模型请求。</p></section>;
}

function DeveloperDiagnostics({ sessionId, status }: Omit<Props, 'developer'>) {
  const tabId = useId();
  const [mode, setMode] = useState<Mode>('connection'), [selection, setSelection] = useState<{ session: string | null; turn: string }>({ session: sessionId, turn: '' });
  const [reload, setReload] = useState(0), [request, setRequest] = useState<{ key: string; data: Data | null; error: string; loading: boolean }>({ key: '', data: null, error: '', loading: false });
  const turn = selection.session === sessionId ? selection.turn : '', key = `${sessionId || 'catalog'}:${turn}`, needsData = mode !== 'connection';
  useEffect(() => {
    if (!needsData) return;
    const controller = new AbortController();
    setRequest({ key, data: null, error: '', loading: true });
    const path = sessionId ? `/api/sessions/${encodeURIComponent(sessionId)}/debug${turn ? `?turn_id=${encodeURIComponent(turn)}` : ''}` : '/api/debug/catalog';
    api(path, { signal: controller.signal, headers: { 'X-ChefMate-Developer': '1' } }).then(data => {
      if (!controller.signal.aborted) setRequest({ key, data, error: '', loading: false });
    }).catch(error => { if (!controller.signal.aborted) setRequest({ key, data: null, error: safeText(error.message), loading: false }); });
    return () => controller.abort();
  }, [sessionId, turn, key, needsData, reload]);
  const data = request.key === key ? request.data : null, error = request.key === key ? request.error : '';
  const loading = needsData && (request.key !== key || request.loading), capture = data?.capture || null;
  const runs: Data[] = data?.runs || [], historical = mode === 'logs' || mode === 'context';
  return <div className="developer-panel dev-inspector"><div className="dev-tabs" role="tablist" aria-label="调试内容">{tabs.map(([id, label, icon], index) => <button type="button" role="tab" id={`${tabId}-${id}`} aria-controls={`${tabId}-panel`} aria-selected={mode === id} tabIndex={mode === id ? 0 : -1} key={id} onClick={() => setMode(id)} onKeyDown={event => {
      if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
      event.preventDefault(); const next = event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 : (index + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length;
      setMode(tabs[next][0]); document.getElementById(`${tabId}-${tabs[next][0]}`)?.focus();
    }}><Icon name={icon} />{label}</button>)}</div>
    <div role="tabpanel" id={`${tabId}-panel`} aria-labelledby={`${tabId}-${mode}`}>{mode === 'connection' ? <Connection status={status} /> : <>
      <header className="dev-data-toolbar"><p>{historical ? '已留存的执行与请求' : `当前目录 · ${stageLabels[data?.stage] || data?.stage || '读取中'}`}</p><button type="button" className="dev-icon-action" aria-label="刷新调试记录" disabled={loading} onClick={() => setReload(value => value + 1)}><Icon name="refresh-cw" />刷新</button></header>
      {error && <div className="error-box" role="alert"><p>{error}</p><button type="button" onClick={() => setReload(value => value + 1)}>重新读取</button>{turn && <button type="button" onClick={() => setSelection({ session: sessionId, turn: '' })}>返回最近留存</button>}</div>}
      {loading && <p className="dev-loading" role="status"><Icon name="loader-circle" className="spin" />正在读取调试记录…</p>}
      {data && <>
        {historical && runs.length > 0 && <label className="dev-field dev-turn-picker"><span>会话轮次 <small>最近保留 {data.retained_turns || 20} 轮</small></span><Select aria-label="查看调试轮次" value={turn || capture?.turn_id || runs[0].turn_id} onValueChange={value => setSelection({ session: sessionId, turn: value })} options={runs.map(run => ({ value: run.turn_id, label: `${formatDate(run.created_at)}${run.created_at ? ' · ' : ''}${(run.message || run.turn_id).slice(0, 48)} · ${statusLabels[run.status] || run.status || '状态未报告'}` }))} /></label>}
        {mode === 'tools' && <ToolsCatalog tools={data.tools} />}
        {mode === 'components' && <ComponentsCatalog components={data.components} />}
        {mode === 'logs' && (sessionId ? <ExecutionLog key={`${sessionId}:${capture?.turn_id || 'legacy'}`} capture={capture} logs={data.logs} /> : <Empty>选择一个会话后，查看它的执行记录。</Empty>)}
        {mode === 'context' && <TraceExplorer key={`${sessionId}:${capture?.turn_id || 'none'}`} capture={capture} />}
      </>}
    </>}</div>
  </div>;
}

export function Diagnostics({ developer, ...props }: Props) { return developer ? <DeveloperDiagnostics {...props} /> : null; }
