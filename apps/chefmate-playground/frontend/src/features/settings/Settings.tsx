import {Fragment, useCallback, useEffect, useRef, useState} from 'react';
import {api} from '../../runtime/http';
import {Icon} from '../../components/Icon';
import {readUiPreferences, saveUiPreferences, type UiPreferencesSnapshot} from '../preferences/client';
import {AppearanceSettings} from './AppearanceSettings';
import {MemorySettings} from './MemorySettings';
import {AccountPanel, type AccountSection} from '../account/AccountPanel';
import type {AccountUser} from '../account/types';
import {SettingsInsights, UsageTimeRange} from './SettingsInsights';
import {KeyboardSettings} from './KeyboardSettings';
import {ComponentSettings} from './ComponentSettings';
import {saveKeyboardPreferences, type KeyboardPreferences} from './keyboard-preferences';
import {Field, SettingsGroup, SettingsTabs} from './SettingsFields';
import {SettingsIndex} from './SettingsIndex';
import {usePageArrival} from './page-arrival';
import {advanceRevisions, createDrafts, editableSections, isDirty, type Drafts, type SettingsRevision, type Values} from './settings-state';
import './settings-react.css';

const sections = [
  ['account-profile', '个人资料', 'user-round', '让小锅搭子用你喜欢的方式称呼你。'],
  ['account-quota', '我的额度', 'coins', '查看当前可用比例，以及会员窗口的恢复时间。'],
  ['account-security', '账号安全', 'shield-check', '管理登录密码，保护自己的厨房资料。'],
  ['display', '外观与对话', 'sun-moon', '按你的习惯使用小锅搭子。'],
  ['keyboard', '快捷键', 'corner-down-left', '把常用操作放在顺手的按键上。'],
  ['cooking', '烹饪与计时', 'timer', '让计时和步骤切换更符合你的做饭习惯。'],
  ['memory', '个人偏好', 'sliders-horizontal', '整理厨房习惯、口味和长期记忆，让每一次建议更懂你。'],
  ['model', '模型连接', 'plug', '选择模型、端点和连接方式。保存后用于接下来的对话。'],
  ['components', '系统组件', 'chef-hat', '统一管理指南、工具和交互界面。'],
  ['usage', 'Token 用量', 'layers', '查看模型服务已报告的使用量。'],
  ['context', '上下文预算', 'layers', '给查询结果和连续对话留出足够空间。'],
  ['runtime', '执行设置', 'sliders-horizontal', '设置一次任务的等待时间和操作上限。'],
  ['developer', '开发者', 'code-xml', '按需查看公开执行记录、工具参数和模型实际用量。'],
  ['data', '数据与状态', 'database', '查看当前菜谱库和本地工作台状态。'],
  ['changelog', '版本更新日志', 'list-checks', '看看小锅搭子最近多了哪些本领。'],
  ['about', '关于 ChefMate', 'info', ''],
];
const navigationGroups: Record<string, string> = {'account-profile': '我的账号', display: '偏好', model: '智能体', context: '高级设置', changelog: '产品信息'};
const sectionTabs: Record<string, readonly string[]> = {display: ['配色与字体', '对话显示', '对话建议'], keyboard: ['发送消息', '浏览与返回'], cooking: ['计时规则', '声音提醒'], model: ['连接信息', '连接方式'], components: ['组件管理'], usage: ['用量概览', '按模型统计'], context: ['信息容量'], runtime: ['等待与重试', '操作预算'], developer: ['调试信息'], changelog: ['更新记录']};
export type SettingsProps = {onClose: () => void; onSaved?: () => void; onAccount?: () => void; notify?: (message: string) => void; scope?: 'personal' | 'admin'; initialSection?: string; onSectionChange?: (section: string) => void; apiBase?: string; developerAllowed?: boolean; account?: AccountUser; refreshAccount?: () => Promise<void>};
const noNotify = () => {};
const noRefresh = async () => {};

export function Settings({onClose, onSaved, scope, initialSection = 'display', onSectionChange, apiBase = '/api/settings', developerAllowed = true, account, refreshAccount = noRefresh, notify = noNotify}: SettingsProps) {
  const globalSections = ['model', 'components', 'context', 'runtime', 'data'];
  const visibleSections = sections.filter(([id]) => (!id.startsWith('account-') || Boolean(account)) && (!scope || (scope === 'admin' ? globalSections.includes(id) : !globalSections.includes(id))) && (id !== 'developer' || developerAllowed));
  const [sectionChoice, setCurrent] = useState(initialSection), [loaded, setLoaded] = useState<Values | null>(null), [status, setStatus] = useState<Values>({});
  const current = visibleSections.some(([id]) => id === sectionChoice) ? sectionChoice : visibleSections[0][0];
  const currentRef = useRef(current); currentRef.current = current;
  useEffect(() => {setCurrent(initialSection);}, [initialSection]);
  const [drafts, setDrafts] = useState<Drafts>({}), [baselines, setBaselines] = useState<Drafts>({}), [revisions, setRevisions] = useState<Record<string, SettingsRevision>>({});
  const [displayRevision, setDisplayRevision] = useState('');
  const [usageDays, setUsageDays] = useState('365');
  const [busy, setBusy] = useState(false), [testing, setTesting] = useState(false), [memoryDirty, setMemoryDirty] = useState(false), [memoryBusy, setMemoryBusy] = useState(false);
  const [accountDirty, setAccountDirty] = useState(false), [accountBusy, setAccountBusy] = useState(false);
  const [accountDirtySections, setAccountDirtySections] = useState<Record<string, boolean>>({});
  const [notice, setNotice] = useState(''), [error, setError] = useState(false), [confirmation, setConfirmation] = useState<null | {text: string; action: () => void}>(null), [insightRefresh, setInsightRefresh] = useState(0);
  const form = useRef<HTMLFormElement>(null), content = useRef<HTMLDivElement>(null), alive = useRef(true), working = useRef(false), allowedNavigation = useRef(false), loadAbort = useRef<AbortController | null>(null), loadSequence = useRef(0), testAbort = useRef<AbortController | null>(null);
  const pageContent = useRef<HTMLDivElement>(null);
  usePageArrival(pageContent, current);
  const pendingPreferences = useRef<UiPreferencesSnapshot | null>(null), displayRevisionRef = useRef(displayRevision); displayRevisionRef.current = displayRevision;
  const draftsRef = useRef(drafts), baselineRef = useRef(baselines); draftsRef.current = drafts; baselineRef.current = baselines;
  const dirtySections = Object.keys(drafts).filter(key => isDirty(drafts[key], baselines[key]));
  const dirty = accountDirty || memoryDirty || dirtySections.length > 0, locked = busy || testing || memoryBusy || accountBusy;
  const say = (text: string, failed = false) => {setNotice(text); setError(failed);};
  const adoptDisplay = useCallback((snapshot: UiPreferencesSnapshot) => {
    const accepted = {...snapshot.values};
    draftsRef.current = {...draftsRef.current, display: accepted}; baselineRef.current = {...baselineRef.current, display: structuredClone(accepted)}; displayRevisionRef.current = snapshot.revision;
    setDrafts(previous => ({...previous, display: accepted})); setBaselines(previous => ({...previous, display: structuredClone(accepted)})); setDisplayRevision(snapshot.revision);
  }, []);
  const receivePreferences = useCallback((snapshot: UiPreferencesSnapshot) => {
    if (!alive.current || !snapshot?.values || typeof snapshot.revision !== 'string' || snapshot.revision === displayRevisionRef.current) return;
    if (isDirty(draftsRef.current.display, baselineRef.current.display)) {setNotice('外观与对话偏好已在其他位置更新。当前草稿已保留；保存前请重新读取并核对。'); setError(false); return;}
    adoptDisplay(snapshot);
  }, [adoptDisplay]);
  const flushPreferences = useCallback(() => {const pending = pendingPreferences.current; pendingPreferences.current = null; if (pending) receivePreferences(pending);}, [receivePreferences]);
  const load = useCallback(async (resetSection?: string) => {
    if (working.current && !loadAbort.current) return;
    loadAbort.current?.abort();
    working.current = true; setBusy(true);
    const controller = new AbortController(), epoch = ++loadSequence.current; loadAbort.current = controller;
    try {
      const [settings, service, initialPreferences] = await Promise.all([api(apiBase, {signal: controller.signal}), api('/api/status', {signal: controller.signal}), readUiPreferences({signal: controller.signal})]);
      if (!alive.current || controller.signal.aborted || epoch !== loadSequence.current) return;
      let preferences = initialPreferences;
      // Revisions are opaque hashes: an event can arrive after a newer GET result.
      // Treat queued differences as invalidation, then read the authoritative state.
      while (pendingPreferences.current && pendingPreferences.current.revision !== preferences.revision) {
        pendingPreferences.current = null;
        preferences = await readUiPreferences({signal: controller.signal});
        if (!alive.current || controller.signal.aborted || epoch !== loadSequence.current) return;
      }
      pendingPreferences.current = null;
      const fresh = createDrafts(settings, preferences.values);
      setLoaded(settings); setStatus(service);
      const nextDrafts = {...draftsRef.current}, nextBaselines = {...baselineRef.current};
      const accepted: string[] = [];
      for (const section of editableSections) if (section === resetSection || !isDirty(nextDrafts[section], nextBaselines[section])) {nextDrafts[section] = fresh[section]; nextBaselines[section] = structuredClone(fresh[section]); accepted.push(section);}
      draftsRef.current = nextDrafts; baselineRef.current = nextBaselines;
      setDrafts(nextDrafts); setBaselines(nextBaselines);
      setRevisions(previous => ({...previous, ...Object.fromEntries(accepted.filter(section => section !== 'display').map(section => [section, settings.revision]))}));
      if (accepted.includes('display')) {displayRevisionRef.current = preferences.revision; setDisplayRevision(preferences.revision);}
      say(!accepted.includes('display') && preferences.revision !== displayRevisionRef.current ? '已读取最新设置。外观与对话偏好在其他位置更新，当前草稿已保留；请重新读取外观页并核对。' : '');
    } catch (reason) {if (alive.current && !controller.signal.aborted && epoch === loadSequence.current) say((reason as Error).message, true);}
    finally {if (epoch === loadSequence.current) {working.current = false; loadAbort.current = null; pendingPreferences.current = null; if (alive.current) setBusy(false);}}
  }, [apiBase]);
  useEffect(() => {alive.current = true; void load(); return () => {alive.current = false; loadSequence.current++; loadAbort.current?.abort(); testAbort.current?.abort(); working.current = false;};}, [load]);
  useEffect(() => {
    const receive = (event: Event) => {
      const snapshot = (event as CustomEvent<UiPreferencesSnapshot>).detail;
      if (!alive.current) return;
      if (working.current) pendingPreferences.current = snapshot;
      else receivePreferences(snapshot);
    };
    window.addEventListener('chefmate:ui-preferences', receive); return () => window.removeEventListener('chefmate:ui-preferences', receive);
  }, [receivePreferences]);
  const requestClose = () => {
    if (locked) {say(memoryBusy ? '正在保存记忆，完成后就可以返回。' : testing ? '正在测试连接，完成后就可以返回。' : '正在处理设置，完成后就可以返回。', true); return;}
    if (dirty) setConfirmation({text: '返回对话会放弃尚未保存的设置。可以先保存，或者继续返回。', action: () => {allowedNavigation.current = true; onClose(); queueMicrotask(() => {allowedNavigation.current = false;});}});
    else onClose();
  };
  useEffect(() => {
    const beforeUnload = (event: BeforeUnloadEvent) => {if (dirty || locked) {event.preventDefault(); event.returnValue = '';}};
    window.addEventListener('beforeunload', beforeUnload); return () => window.removeEventListener('beforeunload', beforeUnload);
  }, [dirty, locked]);
  useEffect(() => {
    const navigate = (event: Event) => {
      const navigation = event as CustomEvent<{page: string; resume?: () => void}>;
      if (allowedNavigation.current) return;
      if (navigation.detail?.page === 'settings') {if (locked) {event.preventDefault(); say('正在处理设置，完成后就可以切换。', true);} return;}
      if (!dirty && !locked) return;
      event.preventDefault();
      if (locked) say('正在处理设置，完成后就可以离开。', true);
      else if (navigation.detail?.resume) setConfirmation({text: '离开会放弃尚未保存的设置。可以先保存，或者继续离开。', action: navigation.detail.resume});
    };
    window.addEventListener('chefmate:before-page-change', navigate);
    return () => window.removeEventListener('chefmate:before-page-change', navigate);
  }, [dirty, locked]);
  useEffect(() => {content.current?.scrollTo?.({top: 0}); setNotice(''); setError(false);}, [current]);
  const change = (key: string, value: any) => {setDrafts(previous => ({...previous, [current]: {...previous[current], [key]: value}})); say('');};
  const save = async () => {
    if (locked || working.current || !loaded || !form.current?.reportValidity()) return;
    working.current = true; setBusy(true); say('正在保存…');
    const section = current, values = {...drafts[section]}, previousRevision = revisions[section];
    let remoteSaved = false;
    const acceptRemote = (next: Values) => {
      remoteSaved = true;
      if (!alive.current) return;
      setLoaded(next); setRevisions(previous => advanceRevisions(previous, previousRevision, next.revision));
    };
    try {
      let result = loaded;
      if (section === 'display') {
        const changes = Object.fromEntries(Object.entries(values).filter(([key, value]) => !Object.is(value, baselines.display?.[key])));
        const accepted = await saveUiPreferences(changes, displayRevision);
        if (!alive.current) return;
        adoptDisplay(accepted);
      } else {
      if (section === 'keyboard') saveKeyboardPreferences(values as KeyboardPreferences);
      else if (section === 'developer') localStorage.setItem('chefmate:developer-settings', JSON.stringify(values));
      else {
        const payload: Values = section === 'components' ? {enabled_components: Object.keys(values).filter(key => values[key])} : {...values};
        if (section === 'cooking') delete payload.timer_sound;
        if (section !== 'cooking' || isDirty(payload, loaded.cooking)) {result = await api(apiBase, {method: 'PATCH', body: JSON.stringify({revision: previousRevision, section, values: payload})}); acceptRemote(result);}
        if (section === 'cooking') localStorage.setItem('chefmate:kitchen-preferences', JSON.stringify({timer_sound: values.timer_sound !== false}));
      }
      if (!alive.current) return;
      const accepted = section === 'model' ? {...result.model, api_key: ''} : values;
      setLoaded(result); setDrafts(previous => ({...previous, [section]: accepted})); setBaselines(previous => ({...previous, [section]: structuredClone(accepted)}));
      setRevisions(previous => ({...previous, [section]: result.revision}));
      }
      window.dispatchEvent(new CustomEvent('chefmate:settings-saved')); onSaved?.();
      say(section === 'keyboard' ? '快捷键已保存，当前浏览器立即生效。' : section === 'cooking' ? '计时设置已保存，对当前和新会话立即生效。' : section === 'display' ? '外观与对话偏好已保存。' : '设置已保存。接下来的对话使用新设置。');
    } catch (reason) {if (alive.current) {if (remoteSaved) {window.dispatchEvent(new CustomEvent('chefmate:settings-saved')); onSaved?.();} say(`${remoteSaved ? '服务器设置已保存，本机偏好未保存；草稿已保留，可以重试。' : ''}${(reason as Error).message}`, true);}}
    finally {working.current = false; if (alive.current) {setBusy(false); flushPreferences();}}
  };
  const test = async () => {
    if (locked || working.current || !form.current?.reportValidity()) return;
    const payload = JSON.stringify(drafts.model), controller = new AbortController(); testAbort.current = controller; working.current = true; setTesting(true); say('正在验证原生工具调用…');
    try {const result = await api(`${apiBase}/test-model`, {method: 'POST', body: payload, signal: controller.signal}); if (alive.current && !controller.signal.aborted) say(result.message, !result.ok);}
    catch (reason) {if (alive.current && !controller.signal.aborted) say((reason as Error).message, true);}
    finally {working.current = false; testAbort.current = null; if (alive.current) {setTesting(false); flushPreferences();}}
  };
  const previewChime = async () => {
    const startedPage = current;
    try {const Audio = window.AudioContext || (window as any).webkitAudioContext; if (!Audio) throw new Error('浏览器暂不支持声音提醒。'); const audio = new Audio(); await audio.resume(); if (currentRef.current !== startedPage || !alive.current) {void audio.close(); return;} [660, 880].forEach((frequency, index) => {const sound = audio.createOscillator(), gain = audio.createGain(), at = audio.currentTime + index * .22; sound.frequency.value = frequency; gain.gain.setValueAtTime(.12, at); gain.gain.exponentialRampToValueAtTime(.001, at + .4); sound.connect(gain); gain.connect(audio.destination); sound.start(at); sound.stop(at + .42); sound.onended = () => {sound.disconnect(); gain.disconnect();};}); setTimeout(() => {void audio.close();}, 1200); say('已播放提醒声。');}
    catch (reason) {if (currentRef.current === startedPage && alive.current) say((reason as Error).message, true);}
  };
  const values = drafts[current] || {}, selected = sections.find(section => section[0] === current)!;
  const field = (label: string, key: string, props: any = {}) => <Field key={key} label={label} value={values[key]} onChange={value => change(key, value)} disabled={locked} {...props}/>;
  const readOnly = !editableSections.includes(current);
  return <div className="settings-page react-settings" data-settings-section={current} aria-label="ChefMate 设置">
    <nav className="settings-nav" aria-label="设置分类" onKeyDown={event => {if (!['ArrowRight', 'ArrowLeft', 'ArrowUp', 'ArrowDown', 'Home', 'End'].includes(event.key)) return; const buttons = Array.from(event.currentTarget.querySelectorAll<HTMLButtonElement>('[data-section]')); const index = buttons.indexOf(event.target as HTMLButtonElement); if (index < 0) return; event.preventDefault(); const next = event.key === 'Home' ? 0 : event.key === 'End' ? buttons.length - 1 : (index + (['ArrowRight', 'ArrowDown'].includes(event.key) ? 1 : -1) + buttons.length) % buttons.length; buttons[next].focus(); buttons[next].click();}}>
      {visibleSections.map(([id, title, icon]) => <Fragment key={id}>{navigationGroups[id] && <span className="settings-nav-label">{navigationGroups[id]}</span>}<button type="button" data-section={id} aria-current={current === id ? 'page' : undefined} onClick={() => {if (!locked) {setCurrent(id); onSectionChange?.(id);}}} disabled={locked}><Icon name={icon}/><span>{title}</span>{dirtySections.includes(id) || id === 'memory' && memoryDirty || accountDirtySections[id.slice(8)] ? <span aria-label="有未保存修改">•</span> : null}</button></Fragment>)}
    </nav>
    <section className="settings-workspace"><header className="settings-header" key={`heading-${current}`}><div className="settings-heading-inner"><div><h1>{selected[1]}</h1><p>{selected[3]}</p></div><button type="button" className="icon-button" aria-label="返回工作台" onClick={requestClose}><Icon name="x"/></button></div></header><div className="settings-content" ref={content}><div className="settings-reading-layout"><SettingsIndex container={content} section={current}/><div className="settings-reading-main" ref={pageContent} data-settings-document>
      {notice && <p role="status" className={error ? 'settings-error' : 'settings-success'}>{notice}</p>}
      {!loaded ? <p className="settings-note">{busy ? '正在读取配置…' : '配置读取失败。'}{!busy && <button type="button" onClick={() => load()}>重试</button>}</p> : <>
      <form id="settings-form" className={current === 'display' ? 'has-appearance' : current === 'components' ? 'has-components' : ''} onSubmit={event => {event.preventDefault(); void save();}} ref={form}>{current === 'usage' && <UsageTimeRange value={usageDays} onChange={setUsageDays}/>}<SettingsTabs key={current} items={sectionTabs[current] || []} label={`${selected[1]}内容`}>
        {current === 'display' && <><SettingsGroup title="配色与字体">{field('显示模式', 'theme', {options: [['light', '浅色'], ['dark', '深色'], ['system', '跟随系统']]})}<AppearanceSettings values={values} change={change} disabled={locked}/></SettingsGroup><SettingsGroup title="对话显示">{field('每次回复展开几项内容', 'inline_limit', {options: [[1, '只展开一项'], [2, '最多两项']]})}{field('内容间距', 'density', {options: [['comfortable', '宽松 · 内容之间留白更多'], ['compact', '紧凑 · 同一屏显示更多内容']]})}{field('显示估算标记', 'show_estimates', {type: 'checkbox'})}</SettingsGroup><SettingsGroup title="对话建议">{field('显示推荐回复', 'reply_suggestions', {type: 'checkbox', hint: '点击只替换输入框里的文字，你可以修改后再发送。'})}{field('推荐回复数量', 'reply_suggestion_count', {options: [1, 2, 3, 4, 5, 6].map(count => [count, `${count} 条`])})}</SettingsGroup></>}
        {current === 'keyboard' && <KeyboardSettings values={values as KeyboardPreferences} change={change} disabled={locked}/>}
        {current === 'model' && <><SettingsGroup title="连接信息">{field('API 端点', 'base_url', {required: true, hint: 'OpenAI 兼容端点通常以 /v1 结尾。'})}{field('模型名称', 'model', {required: true})}{field('API Key', 'api_key', {type: 'password', hint: loaded.api_key_set ? '已保存密钥。留空沿用同一端点的密钥；更换供应商需填写专用密钥。' : '尚未保存密钥。'})}</SettingsGroup><SettingsGroup title="连接方式">{field('支持图片输入', 'supports_images', {type: 'checkbox', hint: '按模型实际能力开启，用于识图与拍照记餐。'})}{field('同时接收的图片上限', 'max_images', {type: 'number', min: 1, max: 12})}{field('请求协议', 'api_format', {options: [['chat_completions', 'OpenAI · Chat Completions'], ['responses', 'OpenAI · Responses']]})}{field('流式工具调用', 'stream_tool_calls', {type: 'checkbox'})}<button type="button" className="secondary-button" disabled={locked} onClick={test}>{testing ? '正在验证连接…' : '测试连接与工具调用'}</button></SettingsGroup></>}
        {current === 'cooking' && <><SettingsGroup title="计时规则">{field('同时进行的计时器', 'timer_limit', {options: [[1, '单计时器'], [3, '最多 3 个']], hint: '运行和暂停计入上限；已有计时会保留。'})}{field('切换步骤时清除上一步计时', 'clear_timer_on_step_change', {type: 'checkbox', hint: '仅在进入下一实际步骤时清除，浏览步骤不影响计时。'})}</SettingsGroup><SettingsGroup title="声音提醒">{field('到时声音提醒', 'timer_sound', {type: 'checkbox', hint: '页面打开时播放提示音，音量跟随设备。', action: <button type="button" className="secondary-button" disabled={locked} onClick={previewChime}><Icon name="play"/>试听提醒声</button>})}</SettingsGroup></>}
        {current === 'components' && <ComponentSettings components={loaded.components || []} values={values} change={change} disabled={locked}/>}
        {current === 'context' && <SettingsGroup title="信息容量">{field('会话上下文上限', 'max_history_chars', {type: 'number', min: 4000, max: 2000000, hint: '字符，包含对话历史与本轮工具信息。默认 400,000。'})}{field('单次工具结果上限', 'max_tool_result_chars', {type: 'number', min: 2000, max: 200000, hint: '字符，控制每次查询送给模型的信息量。默认 60,000。'})}<p className="settings-note">这里按字符计量，和模型的 token 上限不同。提高预算可以容纳更多结果，也会增加每轮发送的信息量。</p></SettingsGroup>}
        {current === 'runtime' && <><SettingsGroup title="等待与重试">{field('单轮任务时限', 'turn_timeout_s', {type: 'number', min: 10, max: 300, hint: '秒，10–300'})}{field('单次模型等待', 'model_timeout_s', {type: 'number', min: 10, max: 300, hint: '秒，10–300'})}{field('模型请求重试', 'model_max_retries', {type: 'number', min: 0, max: 2})}</SettingsGroup><SettingsGroup title="操作预算">{field('模型调用上限', 'max_model_rounds', {type: 'number', min: 2, max: 30})}{field('工具调用上限', 'max_tool_calls', {type: 'number', min: 2, max: 50})}</SettingsGroup></>}
        {current === 'developer' && <SettingsGroup title="调试信息">{field('开发者模式', 'enabled', {type: 'checkbox', hint: '显示上下文占用、系统组件、工具和脱敏请求记录。'})}{field('记录展示方式', 'trace_mode', {options: [['compact', '收起记录 · 按需展开'], ['detailed', '展开公开执行轨迹']]})}<p className="settings-note">只展示公开操作与工具状态。Token 分解为估算；实际用量以服务端报告为准。</p></SettingsGroup>}
        {current === 'data' && <><SettingsGroup title="当前连接"><dl className="settings-status-grid">
          <div><dt><Icon name="plug"/>模型</dt><dd><span>{status.model || '尚未提供'}</span><small className="settings-status-tag" data-state={status.configured === true ? 'ready' : 'unknown'}>{status.configured === true ? '已配置' : status.configured === false ? '待配置' : '状态未报告'}</small></dd></div>
          <div><dt><Icon name="database"/>菜谱库</dt><dd><small className="settings-status-tag" data-state={status.database?.ready === true ? 'ready' : 'unknown'}>{status.database?.ready === true ? '已连接' : status.database?.ready === false ? '暂未连接' : '状态未报告'}</small></dd></div>
          <div><dt>菜谱数量</dt><dd>{Number.isFinite(status.database?.recipe_count) ? `${status.database.recipe_count} 道` : '尚未报告'}</dd></div>
          <div><dt>数据版本</dt><dd>{status.database?.data_version || '尚未报告'}</dd></div>
          <div><dt>进行中的对话</dt><dd>{Number.isFinite(status.active_sessions) ? `${status.active_sessions} 个` : '尚未报告'}</dd></div>
        </dl></SettingsGroup><SettingsGroup title="保存与隔离"><p className="settings-note">会话、厨房进度和计时器随账号保存。模型与系统组件由管理员统一设置，外观与对话偏好在当前账号内同步。</p></SettingsGroup></>}
        {['usage', 'changelog', 'about'].includes(current) && <SettingsInsights section={current} refresh={insightRefresh} usageDays={current === 'usage' ? usageDays : undefined}/>}
      </SettingsTabs></form>
      <MemorySettings active={current === 'memory'} onDirty={setMemoryDirty} onBusy={setMemoryBusy}/>
      {account && <AccountPanel user={account} refresh={refreshAccount} notify={notify} active={current.startsWith('account-')} embedded section={(current.startsWith('account-') ? current.slice(8) : 'profile') as AccountSection} onDirty={setAccountDirty} onBusy={setAccountBusy} onDirtySections={setAccountDirtySections}/>}
      </>}
    </div></div></div>{current !== 'memory' && !current.startsWith('account-') && <footer className="settings-footer"><span id="save-state" hidden={!locked && !dirtySections.includes(current) && !error} role="status" data-state={locked ? 'busy' : dirtySections.includes(current) ? 'dirty' : error ? 'error' : 'saved'}><Icon name={locked ? 'loader-circle' : dirtySections.includes(current) ? 'circle-alert' : error ? 'circle-x' : 'circle-check'}/><span>{locked ? testing ? '正在验证原生工具调用…' : '正在处理设置…' : dirtySections.includes(current) ? '当前页有未保存修改' : notice || '设置已同步'}</span></span><div><button type="button" className="text-button" disabled={locked} onClick={() => {if (['usage', 'changelog', 'about'].includes(current)) setInsightRefresh(value => value + 1); else if (dirtySections.includes(current)) setConfirmation({text: '放弃当前页未保存的修改，读取最新配置？其他分类的草稿会保留。', action: () => {void load(current);}}); else void load(current);}}>重新读取</button>{!readOnly && <button type="button" className="primary-button" disabled={!loaded || locked || !dirtySections.includes(current)} onClick={save}>保存修改</button>}</div></footer>}</section>
    {confirmation && <div className="react-confirm-backdrop" onKeyDown={event => {if (event.key === 'Escape') setConfirmation(null); if (event.key === 'Tab') {const targets = Array.from(event.currentTarget.querySelectorAll<HTMLButtonElement>('button:not(:disabled)')); const first = targets[0], last = targets.at(-1); if (event.shiftKey && document.activeElement === first || !event.shiftKey && document.activeElement === last) {event.preventDefault(); (event.shiftKey ? last : first)?.focus();}}}}><div className="react-confirm" role="alertdialog" aria-modal="true" aria-labelledby="settings-confirm-title"><h2 id="settings-confirm-title">保留未保存的修改？</h2><p>{confirmation.text}</p><div><button autoFocus type="button" className="secondary-button" onClick={() => setConfirmation(null)}>继续编辑</button><button type="button" className="primary-button" onClick={() => {const action = confirmation.action; setConfirmation(null); action();}}>放弃修改</button></div></div></div>}
  </div>;
}
export default Settings;
