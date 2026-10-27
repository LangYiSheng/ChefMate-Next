import {useEffect, useState, type CSSProperties} from 'react';
import {Icon} from '../../components/Icon';
import {quotaAmounts, type AccountQuota, type FundWindow} from './types';
import {availableQuotaPercent, personalQuotaLabel, quotaPercent, quotaPercentText} from './quota-display';
import './personal-quota.css';

const dateText = (value: string) => new Date(value).toLocaleString('zh-CN', {month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit'});
function PercentMeter({label, available, reserved = 0}: {label: string; available: number | null; reserved?: number}) {
  return <div className="pquota-meter"><div><span>{label}</span><strong>{quotaPercentText(available)}{available != null && ' 可用'}</strong></div>
    {available != null && <progress aria-label={`${label}可用比例`} value={available} max={100} aria-valuetext={`${quotaPercentText(available)}可用`}/>}
    {reserved > 0 && <small>处理中占用 {quotaPercentText(reserved)}</small>}
  </div>;
}
function WindowMeter({label, window, active}: {label: string; window: FundWindow; active: boolean}) {
  return <div className="pquota-window"><PercentMeter label={label} available={quotaPercent(window.remaining_micros, window.limit_micros)} reserved={quotaPercent(window.reserved_micros, window.limit_micros) || 0}/><small className="pquota-reset"><Icon name="timer"/>{window.resets_at ? `${dateText(window.resets_at)} ${active ? '恢复' : '窗口结束'}` : active ? '首次使用时开始计时' : '此窗口尚未启用'}</small></div>;
}
export function PersonalQuotaOverview({quota, compact = false, onRefresh}: {quota: AccountQuota; compact?: boolean; onRefresh?: () => Promise<unknown>}) {
  const [refreshError, setRefreshError] = useState('');
  const funds = quota.funds, amounts = quotaAmounts(quota), percent = availableQuotaPercent(quota);
  const active = funds?.memberships.filter(member => member.status === 'active') || [];
  const priority = active.find(member => member.available_micros > 0) || active[0];
  const nextReset = Math.min(Infinity, ...active.flatMap(member => [member.expires_at, member.week.resets_at, member.five_hour.resets_at]).filter((value): value is string => Boolean(value)).map(value => new Date(value).getTime()).filter(value => value > Date.now()));
  useEffect(() => {
    if (!onRefresh || !Number.isFinite(nextReset)) return;
    let alive = true;
    const timer = setTimeout(() => {void onRefresh().then(() => {if (alive) setRefreshError('');}).catch(() => {if (alive) setRefreshError('未能读取新窗口额度，暂显示上次记录。');});}, Math.min(2_147_000_000, Math.max(0, nextReset - Date.now()) + 1000));
    return () => {alive = false; clearTimeout(timer);};
  }, [nextReset, onRefresh]);
  if (compact) return <section className="pquota-mini" aria-label="额度概览"><div className="pquota-mini-heading"><span><Icon name="coins"/>当前可用</span><strong>{quotaPercentText(percent)}</strong></div>{priority && <div className="pquota-mini-member"><span>{personalQuotaLabel(priority.label, 'ChefMate 会员')}</span><span>本窗口可用 {quotaPercentText(quotaPercent(priority.available_micros, Math.min(priority.weekly_limit_micros, priority.five_hour_limit_micros)))}</span><progress aria-label="会员本窗口可用比例" value={quotaPercent(priority.available_micros, Math.min(priority.weekly_limit_micros, priority.five_hour_limit_micros)) || 0} max={100}/></div>}<div className="pquota-mini-state"><span>{active.length ? '会员优先使用' : '永久额度'}{funds && ` · ${quotaPercentText(quotaPercent(funds.permanent.remaining_micros, funds.permanent.total_micros))}可用`}</span><span>{amounts.reserved > 0 ? '有请求处理中' : '已同步'}</span></div>{refreshError && <small role="status">{refreshError}</small>}</section>;
  return <section className="personal-quota" aria-label="额度与用量"><div className="pquota-hero"><div className="pquota-ring" style={{'--quota-share': `${percent ?? 100}%`} as CSSProperties}><div><span>当前可用</span><strong role="heading" aria-level={2}>{percent == null ? '∞' : quotaPercentText(percent)}</strong></div></div><div className="pquota-hero-copy"><p className="account-eyebrow">你的厨房额度</p><h3>{percent == null ? '自由使用，安心下厨' : percent === 0 ? '当前额度已用完' : '下一顿，继续一起做'}</h3><p>{percent == null ? '当前账号没有额度上限。' : '按当前有效会员与永久额度计算可用比例。'}{active.length > 0 ? '会员额度优先使用，窗口结束后恢复。' : '永久额度会一直保留。'}</p><span className="pquota-state"><Icon name={amounts.reserved > 0 ? 'loader-circle' : 'circle-check'}/>{amounts.reserved > 0 ? '有请求处理中，可用比例已扣除占用' : '额度状态已同步'}</span></div></div>
    {refreshError && <p className="error-box" role="alert">{refreshError}</p>}
    <section className="pquota-section"><div className="pquota-section-title"><Icon name="sparkles"/><h3>限时会员</h3><span>优先使用</span></div>{funds?.memberships.length ? <div className="pquota-memberships">{funds.memberships.map(member => <article key={member.id} className={`pquota-membership is-${member.status}`}><header><div><h4>{personalQuotaLabel(member.label, 'ChefMate 会员')}</h4><p>{member.status === 'active' ? `有效至 ${dateText(member.expires_at)}` : member.status === 'expired' ? `已于 ${dateText(member.expires_at)} 到期` : '已停用'}</p></div><span className="ui-badge">{member.status === 'active' ? member.available_micros > 0 ? '优先使用中' : '等待窗口恢复' : member.status === 'expired' ? '已到期' : '已停用'}</span></header><div className="pquota-window-grid"><WindowMeter label="周额度" window={member.week} active={member.status === 'active'}/><WindowMeter label="5 小时额度" window={member.five_hour} active={member.status === 'active'}/></div><p className="account-fineprint">5 小时额度同时受周额度限制，首次使用时开始计时。</p></article>)}</div> : <div className="pquota-empty"><Icon name="sparkles"/><div><h4>还未开通会员</h4><p>当前使用永久额度，开通后会显示窗口状态。</p></div></div>}</section>
    <section className="pquota-section"><div className="pquota-section-title"><Icon name="coins"/><h3>永久额度</h3><span>长期保留</span></div><div className="pquota-permanent-list">{funds ? funds.permanent.sources.map(source => <article key={source.id} className="pquota-permanent"><div className="pquota-source-heading"><span className="pquota-source-icon"><Icon name={source.kind === 'base' ? 'sparkles' : 'coins'}/></span><div><h4>{source.kind === 'base' ? '新用户基础额度' : personalQuotaLabel(source.label, '额外充值额度')}</h4><small>{source.kind === 'base' ? '基础额度' : '追加额度'}</small></div></div><PercentMeter label="剩余额度" available={quotaPercent(source.remaining_micros, source.amount_micros)} reserved={quotaPercent(source.reserved_micros, source.amount_micros) || 0}/>{source.estimated_micros > 0 && <p>部分用量正在核对，已计入占用。</p>}{source.overdrawn_micros > 0 && <p className="error-box">额度暂不可用，请联系管理员。</p>}</article>) : <article className="pquota-permanent"><PercentMeter label="基础额度" available={percent}/></article>}</div></section>
    <p className="account-fineprint">可用状态已扣除正在执行的占用。各来源按自身额度计算比例，待核对用量暂计入使用。</p>
  </section>;
}
