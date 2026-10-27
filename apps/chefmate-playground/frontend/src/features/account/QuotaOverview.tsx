import {useEffect, useState} from 'react';
import {Icon} from '../../components/Icon';
import {money, quotaAmounts, type AccountQuota, type FundWindow} from './types';
import './quota.css';

const dateText = (value: string) => new Date(value).toLocaleString('zh-CN', {month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit'});
function WindowMeter({label, window, active}: {label: string; window: FundWindow; active: boolean}) {
  return <div className="quota-window"><div><strong>{label}</strong><span>{active ? '剩余' : '窗口未用'} {money(window.remaining_micros)}</span></div><progress aria-label={`${label}用量`} value={Math.min(window.limit_micros, window.used_micros + window.reserved_micros)} max={Math.max(1, window.limit_micros)}/><div className="quota-window-detail"><span>已用 {money(window.used_micros)} / {money(window.limit_micros)}{window.reserved_micros > 0 && ` · 预留 ${money(window.reserved_micros)}`}</span><small>{window.resets_at ? active ? `${dateText(window.resets_at)} 重置` : `历史窗口截至 ${dateText(window.resets_at)}` : active ? '首次使用时开始计时' : '此窗口尚未启用'}</small></div>{window.estimated_micros > 0 && <small>其中 {money(window.estimated_micros)} 为待核对预估</small>}</div>;
}

export function QuotaOverview({quota, compact = false, onRefresh}: {quota: AccountQuota; compact?: boolean; onRefresh?: () => Promise<unknown>}) {
  const [refreshError, setRefreshError] = useState('');
  const funds = quota.funds, amounts = quotaAmounts(quota);
  const active = funds?.memberships.filter(member => member.status === 'active') || [];
  const priority = active.find(member => member.available_micros > 0) || active[0];
  const nextReset = Math.min(Infinity, ...active.flatMap(member => [member.expires_at, member.week.resets_at, member.five_hour.resets_at]).filter((value): value is string => Boolean(value)).map(value => new Date(value).getTime()).filter(value => value > Date.now()));
  useEffect(() => {
    if (!onRefresh || !Number.isFinite(nextReset)) return;
    let alive = true;
    const timer = setTimeout(() => {void onRefresh().then(() => {if (alive) setRefreshError('');}).catch(() => {if (alive) setRefreshError('未能读取新窗口额度，暂显示上次记录。');});}, Math.min(2_147_000_000, Math.max(0, nextReset - Date.now()) + 1000));
    return () => {alive = false; clearTimeout(timer);};
  }, [nextReset, onRefresh]);
  if (compact) return <section className="quota-mini" aria-label="额度概览"><div className="quota-mini-balance"><span><Icon name="coins"/>当前可用</span><strong>{money(amounts.remaining)}</strong></div>{priority && <div className="quota-mini-member"><span>{priority.label}</span><span>本窗口可用 {money(priority.available_micros)}</span><progress aria-label="会员本窗口额度占用" value={Math.max(0, priority.five_hour.limit_micros - priority.available_micros)} max={Math.max(1, priority.five_hour.limit_micros)}/></div>}<div className="quota-mini-sources"><span>{active.length ? `会员 ${active.length} 份` : '永久额度'}{funds && ` · ${money(funds.permanent.remaining_micros)}`}</span><span>预留 {money(amounts.reserved)}</span></div></section>;
  return <section className="quota-overview" aria-label="额度与用量"><div className="quota-wallet"><div><p className="account-eyebrow">此刻可用的厨房额度</p><h2>{money(amounts.remaining)}</h2><p>优先使用限时会员额度，再使用基础与充值额度。</p></div><dl><div><dt>累计计入金额</dt><dd>{money(amounts.charged)}</dd></div><div><dt>正在预留</dt><dd>{money(amounts.reserved)}</dd></div></dl></div>
    {refreshError && <p className="error-box" role="alert">{refreshError}</p>}
    {funds ? <><div className="quota-section-title"><Icon name="sparkles"/><h3>限时会员</h3><span>先用限时，再用永久</span></div>{funds.memberships.length ? <div className="quota-memberships">{funds.memberships.map(member => <article key={member.id} className={`quota-membership is-${member.status}`}><header><div><h4>{member.label}</h4><p>{member.status === 'active' ? `有效至 ${dateText(member.expires_at)}` : member.status === 'expired' ? `已于 ${dateText(member.expires_at)} 到期` : '已停用'}</p></div><span className="ui-badge">{member.status === 'active' ? member.available_micros > 0 ? '优先使用中' : '本窗口已用完' : member.status === 'expired' ? '已到期' : '已停用'}</span></header><div className="quota-window-grid"><WindowMeter label="周额度" window={member.week} active={member.status === 'active'}/><WindowMeter label="5 小时额度" window={member.five_hour} active={member.status === 'active'}/></div><p className="account-fineprint">5 小时额度同时受本周剩余额度限制。窗口未开始时，首次使用才计时。</p></article>)}</div> : <p className="quota-empty">暂未开通会员，当前使用永久额度。</p>}
      <div className="quota-section-title"><Icon name="coins"/><h3>永久额度</h3><span>不会随会员窗口失效</span></div><div className="quota-permanent-list">{funds.permanent.sources.map(source => <article key={source.id} className="quota-permanent"><div><span className="quota-source-icon"><Icon name={source.kind === 'base' ? 'sparkles' : 'coins'}/></span><div><h4>{source.label || (source.kind === 'base' ? '新用户基础额度' : '额外充值额度')}</h4><small>{source.kind === 'base' ? '基础' : '充值'} · 总额 {money(source.amount_micros)}</small></div></div><dl><div><dt>已用</dt><dd>{money(source.used_micros)}</dd></div><div><dt>预留</dt><dd>{money(source.reserved_micros)}</dd></div><div><dt>剩余</dt><dd>{money(source.remaining_micros)}</dd></div></dl>{source.estimated_micros > 0 && <p>已用中含 {money(source.estimated_micros)} 待核对预估。</p>}{source.overdrawn_micros > 0 && <p className="error-box">待补足 {money(source.overdrawn_micros)}</p>}</article>)}</div></> : <div className="quota-permanent-list"><article className="quota-permanent"><div><h3>基础金额额度</h3><span>总额 {money(quota.cost_limit_micros)}</span></div><dl><div><dt>已用</dt><dd>{money(amounts.charged)}</dd></div><div><dt>剩余</dt><dd>{money(amounts.remaining)}</dd></div></dl></article></div>}
    <p className="account-fineprint">以上额度已扣除正在执行的预留。未知用量的预估占用单独标记，管理员核对后会按原来源调整。Token 仅作统计。</p>
  </section>;
}
