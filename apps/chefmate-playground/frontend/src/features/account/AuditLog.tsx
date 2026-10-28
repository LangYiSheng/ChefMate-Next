import {useEffect, useState, type FormEvent} from 'react';
import {Icon} from '../../components/Icon';
import {Select, type SelectOption} from '../../components/ui/Select';
import {api} from '../../runtime/http';
import './audit-log.css';
import {AdminListControls, AdminPagination} from './AdminListControls';

type Filters = {q: string; action: string; actor: string; target: string; date_from: string; date_to: string};
type AuditRow = {id: number; created_at: string; actor_label: string; target_label: string; action: string; action_label: string; field_labels: string[]};
export type AuditReport = {items: AuditRow[]; total: number; page: number; page_size: number; total_pages: number; actions: SelectOption[]; actors: SelectOption[]; targets: SelectOption[]; timezone: string};
const emptyFilters: Filters = {q: '', action: '', actor: '', target: '', date_from: '', date_to: ''};
const dateFormat = new Intl.DateTimeFormat('zh-CN', {timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false});
const formatDate = (value: string) => {const date = new Date(value); return Number.isNaN(date.getTime()) ? '时间未知' : dateFormat.format(date);};

export function AuditLog() {
  const [draft, setDraft] = useState<Filters>(emptyFilters), [query, setQuery] = useState({filters: emptyFilters, page: 1, pageSize: 20});
  const [report, setReport] = useState<AuditReport | null>(null), [loading, setLoading] = useState(true), [error, setError] = useState(''), [refresh, setRefresh] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    const parameters = new URLSearchParams({page: String(query.page), page_size: String(query.pageSize)});
    for (const [key, value] of Object.entries(query.filters)) if (value.trim()) parameters.set(key, value.trim());
    setLoading(true); setError('');
    void api<AuditReport>(`/api/admin/audit?${parameters}`, {signal: controller.signal}).then(value => {
      if (!controller.signal.aborted) setReport(value);
    }).catch(reason => {
      if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : '操作记录读取失败，请重试。');
    }).finally(() => {if (!controller.signal.aborted) setLoading(false);});
    return () => controller.abort();
  }, [query, refresh]);
  const change = (key: keyof Filters, value: string) => setDraft(previous => ({...previous, [key]: value}));
  const apply = (event: FormEvent) => {event.preventDefault(); setQuery(previous => ({...previous, filters: {...draft}, page: 1}));};
  const clear = () => {setDraft(emptyFilters); setQuery(previous => ({...previous, filters: emptyFilters, page: 1}));};
  const filtered = Object.values(query.filters).some(Boolean), page = report?.page || 1, pages = report?.total_pages || 1;
  const options = (label: string, values: SelectOption[] | undefined) => [{value: '', label}, ...values || []];
  return <section className="admin-section audit-log" aria-label="操作记录" aria-busy={loading}>
    <AdminListControls label="操作记录" query={draft.q} onQueryChange={value => change('q', value)} placeholder="用户名、操作或改动内容" onApply={apply} onClear={clear}>
      <label>操作类型<Select value={draft.action} onValueChange={value => change('action', value)} options={options('全部操作', report?.actions)} aria-label="操作类型"/></label>
      <label>操作者<Select value={draft.actor} onValueChange={value => change('actor', value)} options={options('全部操作者', report?.actors)} aria-label="操作者"/></label>
      <label>操作对象<Select value={draft.target} onValueChange={value => change('target', value)} options={options('全部对象', report?.targets)} aria-label="操作对象"/></label>
      <label>开始日期<input type="date" value={draft.date_from} max={draft.date_to || undefined} onChange={event => change('date_from', event.target.value)}/></label>
      <label>结束日期<input type="date" value={draft.date_to} min={draft.date_from || undefined} onChange={event => change('date_to', event.target.value)}/></label>
    </AdminListControls>
    <div className="audit-overview"><p role="status" aria-live="polite">{loading ? '正在读取操作记录…' : error ? '操作记录暂时无法读取。' : `${filtered ? '筛选结果' : '全部记录'} · 共 ${report?.total || 0} 条`}</p><span>北京时间 UTC+8</span><button type="button" className="text-button" disabled={loading} onClick={() => setRefresh(value => value + 1)} aria-label="刷新操作记录"><Icon name="refresh-cw"/>刷新</button></div>
    {error && <div className="error-box" role="alert">{error}<button type="button" onClick={() => setRefresh(value => value + 1)}>重新读取</button></div>}
    {report && <><div className="admin-table-scroll audit-table-scroll" tabIndex={0} role="region" aria-label="操作记录表格"><table className="admin-table audit-table"><thead><tr><th scope="col">时间</th><th scope="col">操作者</th><th scope="col">操作</th><th scope="col">对象</th><th scope="col">改动内容</th></tr></thead><tbody>{report.items.map(item => <tr key={item.id}><td><time dateTime={item.created_at}>{formatDate(item.created_at)}</time></td><td>{item.actor_label}</td><td>{item.action_label}</td><td>{item.target_label}</td><td>{item.field_labels.length ? item.field_labels.join('、') : '—'}</td></tr>)}{!report.items.length && <tr><td colSpan={5} className="admin-table-empty">{filtered ? '没有符合筛选条件的记录，试试调整日期或关键词。' : '还没有操作记录。'}</td></tr>}</tbody></table></div>
      <AdminPagination label="操作记录" page={page} pages={pages} pageSize={query.pageSize} disabled={loading} onPage={next => setQuery(previous => ({...previous, page: next}))} onPageSize={size => setQuery(previous => ({...previous, page: 1, pageSize: size}))}/>

    </>}
  </section>;
}
