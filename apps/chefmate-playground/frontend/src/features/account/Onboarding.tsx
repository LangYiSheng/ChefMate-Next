import {useEffect, useRef, useState} from 'react';
import {api} from '../../runtime/http';
import {Brand, Icon} from '../../components/Icon';
import {Select} from '../../components/ui/Select';
import {applyAppearance} from '../settings/appearance';
import type {AccountSession, AccountUser} from './types';
import {PROFILE_PREFERENCES} from './types';

const levels = [
  {value: 'beginner', name: '刚刚开始', text: '步骤再细一点，慢慢来就好。', icon: 'sprout'},
  {value: 'comfortable', name: '日常下厨', text: '熟悉锅铲，想多一点新灵感。', icon: 'utensils'},
  {value: 'confident', name: '厨房熟手', text: '能张罗一桌，也愿意试试新花样。', icon: 'chef-hat'},
];
export function Onboarding({user, onComplete, onLogout}: {user: AccountUser; onComplete: (user: AccountUser) => void; onLogout: () => Promise<void>}) {
  const [step, setStep] = useState(0), [name, setName] = useState(user.display_name || user.username), [level, setLevel] = useState(user.profile.cooking_level || 'beginner');
  const [household, setHousehold] = useState(user.profile.household_size || 1), [choices, setChoices] = useState<string[]>(user.profile.preferences || []), [theme, setTheme] = useState(user.profile.theme || 'system');
  const [useForAi, setUseForAi] = useState(user.profile.use_for_ai === true);
  const [busy, setBusy] = useState(false), [error, setError] = useState('');
  const heading = useRef<HTMLHeadingElement>(null), shell = useRef<HTMLElement>(null), working = useRef(false), alive = useRef(true);
  useEffect(() => {alive.current = true; return () => {alive.current = false;};}, []);
  useEffect(() => {if (shell.current) shell.current.scrollTop = 0; heading.current?.focus({preventScroll: true});}, [step]);
  async function finish() {
    if (working.current) return;
    working.current = true; setBusy(true); setError('');
    try {
      const result = await api<AccountSession>('/api/auth/onboarding', {method: 'PATCH', body: JSON.stringify({display_name: name.trim() || user.username, cooking_level: level, household_size: household, preferences: choices, theme, use_for_ai: useForAi})});
      if (!result.user) throw new Error('初始配置尚未完成，请再试一次。');
      try {localStorage.setItem('chefmate:ui-settings', JSON.stringify({theme}));} catch { /* Appearance remains active in memory. */ }
      applyAppearance({theme});
      if (alive.current) onComplete(result.user);
    } catch (reason) {if (alive.current) setError((reason as Error).message);}
    finally {working.current = false; if (alive.current) setBusy(false);}
  }
  return <main className="onboarding-shell" ref={shell}>
    <header className="onboarding-header"><span className="account-wordmark"><Brand/><strong>ChefMate</strong></span><button type="button" className="text-button" disabled={busy} onClick={() => {setBusy(true); void onLogout().catch(reason => {setError(reason.message); setBusy(false);});}}>退出账号</button></header>
    <div className="onboarding-main"><div className="onboarding-progress" aria-label={`初始配置，第 ${step + 1} 步，共 3 步`}>{['认识小锅搭子', '你的日常', '准备开饭'].map((label, index) => <span key={label} className={index <= step ? 'is-complete' : ''} aria-current={index === step ? 'step' : undefined}><span>{index < step ? <Icon name="check"/> : index + 1}</span><small>{label}</small></span>)}</div>
    <section className="onboarding-card" data-step={step} key={step} aria-labelledby="onboarding-heading">
      {step === 0 && <><div className="onboarding-hello" aria-hidden="true"><span className="onboarding-steam steam-one"/><span className="onboarding-steam steam-two"/><Brand className="onboarding-chef"/><span className="onboarding-spark"><Icon name="sparkles"/></span></div><p className="account-eyebrow">很高兴认识你</p><h1 id="onboarding-heading" tabIndex={-1} ref={heading}>欢迎来到你的厨房</h1><p className="onboarding-intro">我是小锅搭子。今晚不知道吃什么，<br/>或是锅里那道菜拿不准，都可以来聊聊。</p>
        <div className="onboarding-capabilities">{[['book-open', '一起挑菜', '从食材和时间出发，找到合适的真实菜谱。'], ['timer', '一步步做', '备料、步骤与计时，都放在同一顿饭里。'], ['notebook-pen', '慢慢积累', '留下拿手菜和食光手记，下次接着来。']].map(([icon, title, text]) => <div key={title}><Icon name={icon}/><div><h2>{title}</h2><p>{text}</p></div></div>)}</div></>}
      {step === 1 && <><p className="account-eyebrow">记录你的厨房日常</p><h1 id="onboarding-heading" tabIndex={-1} ref={heading}>先认识一下你</h1><p className="onboarding-intro">先选个大概就好，资料保存在当前账号，之后随时都能调整。</p>
        <div className="onboarding-fields"><label className="account-field"><span>怎么称呼你</span><input value={name} maxLength={40} disabled={busy} onChange={event => setName(event.target.value)} placeholder={user.username}/></label><div className="account-field"><label htmlFor="onboarding-household">平时几个人吃饭</label><Select id="onboarding-household" value={String(household)} onValueChange={value => setHousehold(Number(value))} options={Array.from({length: 20}, (_, index) => ({value: String(index + 1), label: `${index + 1} 人`}))} disabled={busy}/></div></div>
        <fieldset className="onboarding-levels"><legend>你和厨房的熟悉程度</legend>{levels.map(item => <label className="onboarding-choice" key={item.value} data-selected={level === item.value}><input type="radio" name="cooking-level" value={item.value} checked={level === item.value} onChange={() => setLevel(item.value)} disabled={busy}/><Icon name={item.icon}/><span><strong>{item.name}</strong><small>{item.text}</small></span></label>)}</fieldset>
        <fieldset className="onboarding-preferences"><legend>哪些比较像你 <small>可多选，也可以跳过</small></legend><div>{PROFILE_PREFERENCES.map(choice => <button key={choice} type="button" aria-pressed={choices.includes(choice)} disabled={busy} onClick={() => setChoices(current => current.includes(choice) ? current.filter(item => item !== choice) : [...current, choice])}>{choices.includes(choice) && <Icon name="check"/>}{choice}</button>)}</div></fieldset><label className="account-consent"><span><strong>在对话中使用个人偏好与长期记忆</strong><small>开启后，厨房习惯、口味与长期记忆会发送给所配置的模型，可在个人偏好中统一关闭。称呼始终用于 ChefMate 对你的称呼。</small></span><input type="checkbox" role="switch" aria-label="在对话中使用个人偏好与长期记忆" checked={useForAi} disabled={busy} onChange={event => setUseForAi(event.target.checked)}/></label></>}
      {step === 2 && <><div className="onboarding-ready-art" aria-hidden="true"><Icon name="utensils"/><span/><Icon name="leaf"/></div><p className="account-eyebrow">最后一个小选择</p><h1 id="onboarding-heading" tabIndex={-1} ref={heading}>把厨房调成喜欢的样子</h1><p className="onboarding-intro">选一个舒服的明暗，准备开始第一顿。</p><fieldset className="onboarding-themes"><legend className="visually-hidden">显示模式</legend>{[['light', '暖暖的白天', 'sun'], ['dark', '安静的夜晚', 'moon'], ['system', '跟随我的设备', 'monitor']].map(([value, label, icon]) => <label key={value} data-selected={theme === value}><input type="radio" name="onboarding-theme" value={value} checked={theme === value} onChange={() => {setTheme(value); applyAppearance({theme: value});}} disabled={busy}/><Icon name={icon}/><span>{label}</span></label>)}</fieldset><div className="onboarding-ready-note"><Icon name="message-circle"/><p>第一句话，可以从<br/><strong>“我想做一道简单的番茄炒蛋。”</strong>开始。</p></div><p className="account-footnote">资料会保存在你的账号里。需要忌口或过敏提醒时，<br/>请在对话中明确告诉小锅搭子，再核对食材。</p></>}
      {error && <p className="error-box" role="alert">{error}</p>}
      <footer className="onboarding-actions"><button className="text-button" type="button" disabled={busy} onClick={() => step ? setStep(step - 1) : void finish()}>{step ? '上一步' : '稍后再了解'}</button><button type="button" className="primary-button" disabled={busy} onClick={() => step === 2 ? void finish() : setStep(step + 1)}>{busy ? '正在准备…' : step === 2 ? '进入我的厨房' : step === 0 ? '一起开始吧' : '下一步'}{!busy && <Icon name="arrow-right"/>}</button></footer>
    </section><p className="onboarding-footer">一顿饭，一个新开始。</p></div>
  </main>;
}
