import {useEffect, useId, useRef, useState, type CSSProperties} from 'react';
import {api} from '../../runtime/http';
import {Select} from '../../components/ui/Select';
import {SettingsGroup} from './SettingsFields';
import './usage-insights.css';

export function UsageTimeRange({value, onChange}: {value: string; onChange: (value: string) => void}) {
  const id = useId();
  return <div className="usage-insights-toolbar"><label htmlFor={id}>统计时间</label><Select id={id} aria-label="Token统计时间范围" value={value} onValueChange={onChange} options={[['7', '最近 7 天'], ['30', '最近 30 天'], ['365', '最近一年'], ['all', '全部记录']].map(([option, label]) => ({value: option, label}))}/></div>;
}

export const tokenText = (value: unknown) => typeof value === 'number' && Number.isFinite(value) ? Math.round(value).toLocaleString('zh-CN') : '未报告';
const dateNumber = (value: string) => typeof value === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(value) ? Date.parse(`${value}T00:00:00Z`) : NaN;
export function usageCalendar(data: any) {
  const end = dateNumber(data.period?.to);
  if (!Number.isFinite(end)) return {days: [], offset: 0, columns: 0, clipped: false};
  const given = dateNumber(data.period?.from), requested = Number.isFinite(given) ? given : end - 364 * 86400000;
  const start = Math.max(requested, end - 365 * 86400000), byDate = new Map<string, any>((data.daily || []).map((day: any) => [day.date, day]));
  const days: any[] = [];
  for (let time = start; time <= end; time += 86400000) {
    const date = new Date(time).toISOString().slice(0, 10), day = byDate.get(date), calls = Number.isFinite(day?.model_calls) ? day.model_calls : 0;
    const total = day?.known_totals?.total_tokens ?? day?.total_tokens, report = day?.coverage?.total_tokens;
    const known = report?.known ?? report?.reported ?? (Number.isFinite(total) ? calls : 0);
    days.push({date, calls, value: Number.isFinite(total) ? total : null, status: calls === 0 ? 'empty' : !Number.isFinite(total) ? 'unknown' : known < calls ? 'partial' : 'complete'});
  }
  const maximum = Math.max(1, ...days.map(day => day.value || 0));
  days.forEach(day => {day.level = day.value > 0 ? Math.min(4, Math.max(1, Math.ceil(Math.sqrt(day.value / maximum) * 4))) : 0;});
  const offset = (new Date(start).getUTCDay() + 6) % 7;
  return {days, offset, columns: Math.ceil((offset + days.length) / 7), clipped: start > requested};
}
export const usageDayLabel = (day: any) => day.status === 'empty' ? `${day.date} · 没有模型请求` : day.status === 'unknown' ? `${day.date} · ${day.calls} 次请求，Token未报告` : `${day.date} · ${tokenText(day.value)} Token${day.status === 'partial' ? '（已知小计）' : ''} · ${day.calls} 次请求`;
function UsageCalendar({data}: {data: any}) {
  const calendar = usageCalendar(data), [selected, setSelected] = useState(calendar.days.length - 1), refs = useRef<(HTMLButtonElement | null)[]>([]), scroller = useRef<HTMLDivElement>(null);
  useEffect(() => {setSelected(calendar.days.length - 1); if (scroller.current) scroller.current.scrollLeft = scroller.current.scrollWidth;}, [data]);
  const months: {column: number; label: string}[] = []; let previousMonth = '';
  calendar.days.forEach((day, index) => {const month = day.date.slice(0, 7); if (month === previousMonth) return; previousMonth = month; const column = Math.floor((calendar.offset + index) / 7) + 1; if (months.length && column - months.at(-1)!.column < 3) months.pop(); months.push({column, label: `${Number(day.date.slice(5, 7))}月`});});
  return <section className="usage-calendar"><div className="usage-calendar-heading"><h2>每日用量</h2><span>{calendar.days[0]?.date} — {calendar.days.at(-1)?.date}</span></div><div className="usage-calendar-scroll" ref={scroller}><div className="usage-calendar-plot" style={{'--heatmap-columns': calendar.columns} as CSSProperties}><div className="usage-calendar-months" aria-hidden="true">{months.map(month => <span key={month.column} style={{gridColumn: month.column}}>{month.label}</span>)}</div><div className="usage-calendar-weekdays" aria-hidden="true">{[['一', 1], ['三', 3], ['五', 5]].map(([label, row]) => <span key={label} style={{gridRow: row}}>{label}</span>)}</div><div className="usage-calendar-grid" role="group" aria-label="每日Token用量，方向键切换日期">{Array.from({length: calendar.offset}, (_, index) => <span key={`blank-${index}`} aria-hidden="true"/>)}{calendar.days.map((day, index) => <button type="button" key={day.date} ref={node => {refs.current[index] = node;}} className={`usage-calendar-cell level-${day.level} is-${day.status}`} title={usageDayLabel(day)} aria-label={usageDayLabel(day)} aria-pressed={selected === index} tabIndex={selected === index ? 0 : -1} onFocus={() => setSelected(index)} onClick={() => setSelected(index)} onKeyDown={event => {const delta: Record<string, number> = {ArrowLeft: -7, ArrowRight: 7, ArrowUp: -1, ArrowDown: 1}; const next = event.key === 'Home' ? 0 : event.key === 'End' ? calendar.days.length - 1 : delta[event.key] ? Math.max(0, Math.min(calendar.days.length - 1, index + delta[event.key])) : null; if (next !== null) {event.preventDefault(); refs.current[next]?.focus();}}}/>)}</div></div></div><p className="usage-calendar-day" role="status">{calendar.days[selected] ? usageDayLabel(calendar.days[selected]) : '还没有模型请求记录。'}</p><div className="usage-calendar-legend"><span>少</span>{[0, 1, 2, 3, 4].map(level => <i key={level} className={`usage-calendar-cell level-${level}`}/>)}<span>多</span><i className="usage-calendar-cell is-unknown" aria-hidden="true"/><span>未报告</span><i className="usage-calendar-cell level-2 is-partial" aria-hidden="true"/><span>部分报告</span></div>{calendar.clipped && <p className="settings-note">热力图显示最近 366 天，汇总仍包含所选范围的全部记录。</p>}</section>;
}
function Row({label, value}: {label: string; value: any}) { return <div className="status-row"><span>{label}</span><span>{value}</span></div>; }
export function SettingsInsights({section, refresh, usageDays}: {section: string; refresh: number; usageDays?: string}) {
  const [localDays, setLocalDays] = useState('365'), [data, setData] = useState<any>(null), [error, setError] = useState('');
  const days = usageDays ?? localDays;
  const range = section === 'usage' && usageDays === undefined ? <UsageTimeRange value={localDays} onChange={setLocalDays}/> : null;
  useEffect(() => {const controller = new AbortController(); setData(null); setError(''); api(section === 'usage' ? `/api/usage?days=${days}` : section === 'changelog' ? '/api/changelog' : '/api/about', {signal: controller.signal}).then(value => {if (!controller.signal.aborted) setData(value);}).catch(reason => {if (!controller.signal.aborted) setError(reason.message);}); return () => controller.abort();}, [section, days, refresh]);
  if (!data) return <>{range}<p role="status" className={error ? 'settings-error' : 'settings-note'}>{error || '正在读取…'}</p></>;
  if (section === 'changelog') return <SettingsGroup title="更新记录">{(data.releases || []).map((release: any, index: number) => <article className="changelog-release" id={`settings-release-${index}`} data-settings-index={`${release.version} · ${release.title}`} key={`${release.version}-${index}`} aria-label={`${release.version} 版本更新`}><header className="changelog-header"><h2 className="changelog-version">{release.version}</h2>{release.version === data.current_version && <span className="changelog-current">当前版本</span>}<time className="changelog-date" dateTime={release.date}>{release.date}</time></header>{release.source?.branch && <p className="changelog-source">分支 {release.source.branch} · 提交 <code title={release.source.commit}>{release.source.commit?.slice(0, 7)}</code></p>}<p className="changelog-title">{release.title}</p><p className="changelog-summary">{release.summary}</p>{(release.sections || []).map((group: any, groupIndex: number) => <section className="changelog-group" id={index === 0 ? `settings-release-${index}-group-${groupIndex}` : undefined} data-settings-index={index === 0 ? group.title : undefined} key={group.title}><h3>{group.title}</h3><ul>{group.items.map((item: string, index: number) => <li key={index}>{item}</li>)}</ul></section>)}</article>)}{!data.releases?.length && <p>还没有发布更新记录。</p>}</SettingsGroup>;
  if (section === 'about') return <>
    <SettingsGroup title="产品信息"><div className="about-intro"><span className="settings-brand-mark about-brand-icon" aria-hidden="true"/><h2>{data.name || 'ChefMate'}</h2><p>{data.subtitle || '您的个性化烹饪助手'}</p></div><Row label="版本" value={`${data.version || '未提供'}${data.commit ? ` (${data.commit})` : ''}`}/><Row label="运行方式" value={data.runtime || '本地工作台'}/><Row label="基础菜谱" value={`${data.recipe_count ?? '—'} 道`}/><Row label="系统组件" value={`${data.component_count ?? '—'} 个`}/></SettingsGroup>
    <SettingsGroup title="数据与隐私"><p className="settings-note">聊天、个人菜谱、饮食记录和长期记忆保存在本机。调用模型时，会发送本次对话需要的内容与图片。API Key 不回显。</p></SettingsGroup>
    <SettingsGroup title="营养数据"><p className="settings-note">制作量与个人摄入分别记录。单人餐完成后按配方自动登记，多人餐先确认本人份额；照片记餐需核对后保存，未知营养留空。</p></SettingsGroup>
  </>;
  const summary = data.summary || {}, totals = summary.known_totals || summary;
  const caption = (key: string) => {const report = summary.coverage?.[key], known = report?.known ?? report?.reported; return `${report && known < report.calls ? `已知小计 · ${known}/${report.calls} 次请求` : ''}${key === 'cached_tokens' ? ' · 包含在输入中' : ''}`;};
  return <>{range}
    <SettingsGroup title="用量概览"><dl className="insight-metrics">{[['input_tokens', '输入 Token'], ['output_tokens', '输出 Token'], ['cached_tokens', '缓存读取'], ['total_tokens', '总 Token']].map(([key, title]) => <div className="insight-metric" key={key}><dt>{title}</dt><dd>{tokenText(totals[key])}</dd><small>{caption(key)}</small></div>)}</dl><p className="settings-note">{summary.model_calls ?? 0} 次模型请求。同次输入与输出完整时相加得到总量；其余缺失项保留未知。{summary.total_derived_calls ? ` ${summary.total_derived_calls} 次按输入 + 输出计算。` : ''}</p><UsageCalendar data={data}/><h2>使用概况</h2><Row label="今日已知 Token" value={tokenText((data.today?.known_totals || data.today?.summary?.known_totals || data.today)?.total_tokens)}/><Row label="累计已知 Token" value={tokenText((data.all_time?.known_totals || data.all_time?.summary?.known_totals || data.all_time)?.total_tokens)}/></SettingsGroup>
    <SettingsGroup title="按模型统计">{(data.models || []).map((model: any, index: number) => {const usage = model.known_totals || model.summary?.known_totals || model; return <section key={index} id={`settings-model-usage-${index}`} data-settings-index={model.model && model.model !== 'unknown' ? model.model : '历史模型未记录'}><h3>{model.model && model.model !== 'unknown' ? model.model : '历史模型未记录'}</h3><Row label="输入 / 输出" value={`${tokenText(usage.input_tokens)} / ${tokenText(usage.output_tokens)}`}/><Row label="缓存读取" value={tokenText(usage.cached_tokens)}/></section>})}{!data.models?.length && <p className="settings-note">所选时间范围还没有模型用量记录。</p>}<p className="settings-note">统计保存在本机。删除聊天会保留用量账本；历史中未记录的模型身份不会推断。</p></SettingsGroup>
  </>;
}
