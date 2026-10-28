import {Icon} from '../../components/Icon';
import {componentExamples, dependencyLabels, interfaceLabels, toolLabels} from './component-labels';
import {Field, SettingsGroup} from './SettingsFields';
import type {Values} from './settings-state';

const symbols: Record<string, string> = {
  'meal-planning': 'utensils', 'pantry-preparation': 'list-checks', 'cooking-guide': 'timer',
  'recipe-authoring': 'chef-hat', 'personal-memory': 'book-open', 'food-journal': 'utensils', 'app-preferences': 'sun-moon',
};
const capabilitySymbol = (tool: string) => /timer/.test(tool) ? 'timer' : /memory|history/.test(tool) ? 'book-open' : /recipe/.test(tool) ? 'chef-hat' : /prep|shopping/.test(tool) ? 'list-checks' : /food|intake/.test(tool) ? 'utensils' : 'layers';
function Capabilities({tools, label, offset = 0}: {tools: string[]; label: string; offset?: number}) {
  return <div className="component-capabilities" aria-label={label}>{tools.map((tool, index) => <div className="component-capability" key={tool}><Icon name={capabilitySymbol(tool)}/><span>{toolLabels[tool] || `扩展工具 ${index + offset + 1}`}</span></div>)}</div>;
}

export function ComponentSettings({components, values, change, disabled}: {
  components: Values[]; values: Values; change: (key: string, value: any) => void; disabled: boolean;
}) {
  return <SettingsGroup title="组件管理">
    <p className="components-intro">决定小锅搭子能帮你做什么。关闭后保留已有记录。</p>
    <div className="component-flow" aria-label="系统组件组成">
      <div><Icon name="book-open"/><strong>组件指南</strong><span>组织对应任务的做法</span></div><span className="component-flow-arrow" aria-hidden="true">›</span>
      <div><Icon name="layers"/><strong>工具能力</strong><span>查询、核对与保存</span></div><span className="component-flow-arrow" aria-hidden="true">›</span>
      <div><Icon name="list-checks"/><strong>操作界面</strong><span>把结果变成可操作的内容</span></div>
    </div>
    <div className="settings-component-grid">{components.map(component => {
      const tools: string[] = component.tools || [], interfaces: string[] = [...new Set<string>([...(component.ui?.kinds || []), ...(component.ui?.pages || [])])];
      const example = componentExamples[component.id];
      const availability = component.available === true ? '已就绪' : component.available === false ? '依赖尚未就绪' : '尚未报告';
      return <section className={`settings-component ${values[component.id] ? 'is-enabled' : ''}`} id={`settings-component-${component.id}`} data-settings-index={component.name} key={component.id}>
        <header className="component-card-heading"><span className="component-symbol"><Icon name={symbols[component.id] || 'layers'}/></span><Field label={component.name} value={values[component.id]} type="checkbox" hint={component.description} onChange={value => change(component.id, value)} disabled={disabled}/></header>
        {component.available === false && <p className="component-unavailable"><Icon name="plug"/>依赖尚未就绪</p>}
        {example && <p className="component-example"><Icon name="message-circle"/><span>{example}</span></p>}
        {tools.length > 0 && <Capabilities tools={tools.slice(0, 6)} label={`${component.name}工具能力`}/>}
        {tools.length > 6 && <details className="component-more-capabilities"><summary>查看其余 {tools.length - 6} 项能力</summary><Capabilities tools={tools.slice(6)} label={`${component.name}更多工具能力`} offset={6}/></details>}
        {interfaces.length > 0 && <div className="component-interfaces" aria-label={`${component.name}界面`}>{interfaces.map((id, index) => <span key={id}><Icon name="check"/>{interfaceLabels[id] || `扩展界面 ${index + 1}`}</span>)}</div>}
        <details className="component-technical"><summary>技术信息 <span>{tools.length} 项工具</span></summary><dl className="component-manifest"><dt>组件 ID</dt><dd><code>{component.id}</code></dd><dt>指南</dt><dd><code>{component.instructions?.entrypoint || '未提供'}</code></dd><dt>加载方式</dt><dd>{component.instructions?.loading === 'on_demand' ? '按需读取' : component.instructions?.loading || '未提供'}</dd><dt>依赖</dt><dd>{(component.dependencies || []).map((id: string) => <span key={id}>{dependencyLabels[id] || '扩展依赖'} <code>{id}</code> </span>)}{!component.dependencies?.length && '无额外依赖'}</dd><dt>状态</dt><dd>{availability}</dd><dt>工具 ID</dt><dd className="component-tool-ids">{tools.map(tool => <code key={tool}>{tool}</code>)}{!tools.length && '无工具'}</dd><dt>界面 ID</dt><dd>{interfaces.length ? interfaces.map(id => <code key={id}>{id} </code>) : '当前对话'}</dd></dl></details>
      </section>;
    })}</div>
    <p className="settings-note">开关同时控制组件指南与工具权限。</p>
  </SettingsGroup>;
}
