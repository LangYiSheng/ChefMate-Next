import {useEffect, useRef, useState} from 'react';
import {Select} from '../../components/ui/Select';
import {api} from '../../runtime/http';
import {CenterButton, CenterTabs} from './CenterControls';
import {intakeMetric, macros, numberText, type Data} from './center-model';
import './nutrition-report.css';

export function localReportDate() {
  return new Intl.DateTimeFormat('en-CA', {timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit'}).format(new Date());
}
export function shiftReportDate(day: string, offset: number) {
  const date = new Date(`${day}T12:00:00Z`); date.setUTCDate(date.getUTCDate() + offset); return date.toISOString().slice(0, 10);
}
export function reportWeekStart(day: string) {
  const weekday = new Date(day + 'T12:00:00Z').getUTCDay();
  return shiftReportDate(day, -((weekday + 6) % 7));
}

type Props = {kind: 'daily' | 'weekly'; date: string; onDate: (date: string) => void;
  onDaily: (date: string) => void; onFood: (record: Data) => void;
  refreshKey: unknown; onDrafts: () => void};

export function NutritionReport({kind, date, onDate, onDaily, onFood, refreshKey, onDrafts}: Props) {
  const [report, setReport] = useState<Data | null>(null), [error, setError] = useState('');
  const [loading, setLoading] = useState(true), [busy, setBusy] = useState(false), [reload, setReload] = useState(0);
  const [metric, setMetric] = useState('calories_kcal');
  const epoch = useRef(0), working = useRef(false);
  const query = `kind=${kind}&date=${date}`;
  useEffect(() => {
    const controller = new AbortController(); const version = ++epoch.current;
    working.current = false; setBusy(false); setLoading(true); setError(''); setReport(null);
    api(`/api/nutrition-reports?${query}`, {signal: controller.signal}).then(value => {
      if (epoch.current === version) setReport(value);
    }).catch(reason => {if (!controller.signal.aborted) setError(reason.message);})
      .finally(() => {if (!controller.signal.aborted) setLoading(false);});
    return () => {controller.abort(); epoch.current++;};
  }, [query, refreshKey, reload]);
  const generating = ['queued', 'running'].includes(report?.review_job?.status);
  useEffect(() => {
    if (!generating) return;
    const controller = new AbortController(); const version = epoch.current;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const value = await api(`/api/nutrition-reports?${query}`, {signal: controller.signal});
        if (epoch.current === version) {setReport(value); setError('');}
      } catch { /* A disconnected observer must not cancel the server's job. */ }
      if (!controller.signal.aborted) timer = setTimeout(poll, 1500);
    };
    timer = setTimeout(poll, 1000);
    return () => {controller.abort(); clearTimeout(timer);};
  }, [query, generating, refreshKey]);
  async function mutate(path: string, body?: Data) {
    if (working.current) return;
    working.current = true; setBusy(true); setError(''); const version = epoch.current;
    try {
      const value = await api(path, {method: body ? 'PUT' : 'POST', ...(body ? {body: JSON.stringify(body)} : {})});
      if (epoch.current === version) setReport(value);
    } catch (reason) {if (epoch.current === version) setError((reason as Error).message);}
    finally {if (epoch.current === version) {working.current = false; setBusy(false);}}
  }
  const today = report?.today || localReportDate();
  const next = report ? shiftReportDate(report.period.from, kind === 'daily' ? 1 : 7) : today;
  const chosen = macros.find(([key]) => key === metric)!;
  const day = report?.days[0];
  const selectedWeek = reportWeekStart(date);
  const weeks: string[] = report?.available_weeks || [];
  const previousWeek = weeks.find(week => week < selectedWeek);
  const nextWeek = [...weeks].reverse().find(week => week > selectedWeek);
  const changes = macros.filter(([key]) => report?.comparison[key] && report.comparison[key].current !== report.comparison[key].previous);
  return <section className="nutrition-report" aria-label={kind === 'daily' ? '每日饮食回顾' : '每周饮食回顾'}>
    <header className="report-heading"><div><h2>{kind === 'daily' ? '好好看看这一天吃了什么' : '回看这一周的餐桌'}</h2><p className="center-muted">{report ? `${report.period.from}${kind === 'weekly' ? ' — ' + report.period.to : ''}` : '按用餐日期整理'}</p></div>
      <div className="report-date-controls"><CenterButton disabled={loading || (kind === 'weekly' && !previousWeek)} onClick={() => onDate(kind === 'weekly' ? previousWeek! : shiftReportDate(date, -1))}>上一{kind === 'daily' ? '天' : '周'}</CenterButton>{kind === 'weekly' ? <Select className="report-week-select" aria-label="选择回顾周" value={weeks.includes(selectedWeek) ? selectedWeek : ''} placeholder="暂无本周记录" disabled={loading || !weeks.length} onValueChange={onDate} options={weeks.map(start => ({value: start, label: start + '（周一）— ' + shiftReportDate(start, 6) + '（周日）'}))}/> : <input className="center-input" type="date" aria-label="选择回顾日期" value={date} max={today} onChange={e => {if (/^\d{4}-\d{2}-\d{2}$/.test(e.target.value) && e.target.value <= today) onDate(e.target.value);}}/>}<CenterButton disabled={loading || (kind === 'weekly' ? !nextWeek : next > today)} onClick={() => onDate(kind === 'weekly' ? nextWeek! : next)}>下一{kind === 'daily' ? '天' : '周'}</CenterButton><CenterButton onClick={() => {onDate(kind === 'weekly' ? reportWeekStart(today) : today);}}>回到本{kind === 'daily' ? '日' : '周'}</CenterButton></div>
    </header>
    {error && <div className="report-error" role="alert">{error}<CenterButton onClick={() => setReload(n => n + 1)}>重试</CenterButton></div>}
    {loading ? <p className="report-loading" role="status">正在整理这段食光…</p> : report && <>
      <section className="intake-hero report-nutrition-hero"><header className="intake-hero-heading"><h2>{kind === 'daily' ? '这一天的营养' : '这一周的营养'}</h2></header><div className="report-coverage"><span>共 <strong>{report.summary.record_count}</strong> 条记录</span><span>有记录 <strong>{report.recorded_days}</strong> 天</span><span>已记完 <strong>{report.complete_days}</strong> 天</span>{report.summary.estimated && <span className="estimate-marker">含估算</span>}</div>

      <dl className="intake-metrics report-metrics">{macros.map(([key, label, unit]) => {const value = intakeMetric(report.summary, key), average = report.average[key]; return <div className={key === 'calories_kcal' ? 'intake-energy' : ''} key={key}><dt>{label}<small>{value.partial ? '已知部分' : '合计'}</small></dt><dd>{value.value === null ? '—' : numberText(value.value)} <small>{unit}</small></dd>{kind === 'weekly' && <p>{average.value === null ? '暂无可计算日均的数据' : `${average.days} 个记录日日均 ${numberText(average.value)} ${unit}${average.partial ? ' · 含部分值' : ''}`}</p>}</div>;})}</dl></section>
      {kind === 'weekly' && <section className="report-panel"><div className="report-section-heading"><h3>七天的饮食轨迹</h3><CenterTabs label="营养趋势指标" items={macros.map(([key, label]) => [key, label])} selected={metric} onSelect={setMetric}/></div><div className="report-chart">{report.days.map((d: Data) => {const value = intakeMetric(d, metric); const max = Math.max(1, ...report.days.map((x: Data) => intakeMetric(x, metric).value || 0)); return <button key={d.date} disabled={d.future} onClick={() => onDaily(d.date)} aria-label={`${d.date}，${d.future ? '尚未到来' : value.value === null ? '暂无已知营养' : numberText(value.value) + chosen[2]}，查看日报`}><span>{d.future ? '·' : value.value === null ? '—' : numberText(value.value)}</span><div className="report-bar-track"><i className={value.partial ? 'partial' : ''} style={{height: `${value.value === null ? 0 : Math.max(2, value.value / max * 100)}%`}}/></div><time>{d.date.slice(5)}</time><small>{d.future ? '待到来' : d.complete ? '已记完' : d.record_count ? '有记录' : '未记录'}</small></button>;})}</div><p className="center-footnote">{chosen[1]} · {chosen[2]} · 点击日期回看当天</p>
        {changes.length > 0 && <div className="report-comparison"><h3>与上一周比较</h3>{changes.map(([key, label, unit]) => <p key={key}>{label}日均：{numberText(report.comparison[key].previous)} → <strong>{numberText(report.comparison[key].current)} {unit}</strong>{report.comparison[key].change_percent !== null ? '（' + (report.comparison[key].change_percent > 0 ? '+' : '') + report.comparison[key].change_percent + '%）' : ''}</p>)}</div>}</section>}
      <section className="report-panel report-reflection"><div className="report-section-heading"><div><span className="center-eyebrow">{report.review ? 'AI 回顾' : '记录回顾'}</span><h3>{report.summary.record_count ? '留给自己的几个观察' : '从记下一餐开始'}</h3></div><CenterButton disabled={busy || generating || !report.summary.record_count || Boolean(report.review)} onClick={() => {void mutate(`/api/nutrition-reports/review?${query}`);}}>{busy || generating ? '正在整理…' : report.review_job?.status === 'interrupted' ? '继续生成' : report.review ? '回顾已更新' : report.review_stale ? '更新 AI 回顾' : '整理 AI 回顾'}</CenterButton></div>{report.review_stale && <p className="center-muted">有了新记录，可以更新回顾了。</p>}
        {generating && <p className="center-muted" role="status">正在后台整理，可以先去看看其他页面。</p>}
        {!generating && report.review_job?.error && <p className="center-muted" role="status">{report.review_job.error}</p>}
        {report.review?.text ? <p className="report-review-text" style={{whiteSpace: 'pre-wrap', lineHeight: 1.9}}>{report.review.text}</p> : <p className="center-muted">{report.summary.record_count ? '让小锅搭子结合这几餐，写一段饮食回顾。' : '记下一餐后，就能生成回顾。'}</p>}
      </section>
      {kind === 'daily' && <section className="report-panel"><div className="report-section-heading"><h3>这一天的餐次</h3><CenterButton disabled={busy || !day.record_count || day.draft_count > 0} onClick={() => {void mutate('/api/nutrition-reports/day-complete', {date, complete: !day.complete, revision: day.revision});}}>{day.complete ? '撤销已记完' : '这一天已记完'}</CenterButton></div><div className="report-meals">{day.meals.map((meal: Data) => <div key={meal.type}><h4>{meal.label}</h4>{meal.record_count ? report.records.filter((r: Data) => r.meal_type === meal.type).map((r: Data) => <button className="report-source" key={r.id} onClick={() => onFood({...r, document: {title: r.title}})}>{r.title}<span>{numberText(intakeMetric(r.nutrition, 'calories_kcal').value, 'kcal')}{r.nutrition.estimated ? ' · 估算' : ''}</span></button>) : <p className="center-muted">尚无记录</p>}</div>)}</div></section>}
      {report.draft_count > 0 && <div className="intake-pending"><span>所选范围内有 {report.draft_count} 份草稿待核对，尚未计入统计。</span>{report.drafts?.length ? report.drafts.map((draft: Data) => <CenterButton key={draft.id} onClick={() => onFood({...draft, document: {title: draft.title}})}>核对：{draft.title}</CenterButton>) : <CenterButton onClick={onDrafts}>去核对</CenterButton>}</div>}
    </>}
  </section>;
}
