import { useId, useState } from 'react';
import { Icon } from '../Icon';
import { Badge, Content, Disclosure, Empty } from './Primitives';
import { type Data, componentIcons, dependencyLabels, kindLabels, stageLabels, summary, toolInfo } from './model';

function Conditions({ label, items }: { label: string; items?: any[] }) {
  return items?.length ? <div className="dev-conditions"><h5>{label}</h5><ul>{items.map((item, index) => <li key={index}>{typeof item === 'string' ? item : summary(item)}</li>)}</ul></div> : null;
}
function Parameters({ schema }: { schema?: Data }) {
  const properties = Object.entries(schema?.properties || {}) as [string, Data][];
  if (!properties.length) return <p className="dev-muted">无需参数，或参数定义未提供。</p>;
  return <div className="dev-parameters">{properties.map(([name, property]) => <article key={name}><header><code>{name}</code><span>{property.type || (property.anyOf ? '联合类型' : '对象')}</span><Badge tone={schema?.required?.includes(name) ? 'warning' : 'muted'}>{schema?.required?.includes(name) ? '必填' : '可选'}</Badge></header>{property.description && <p>{property.description}</p>}{property.enum && <p className="dev-muted">可选：{property.enum.join('、')}</p>}</article>)}</div>;
}
function ToolCard({ tool }: { tool: Data }) {
  const info = toolInfo(tool.name), available = tool.available === true, known = typeof tool.available === 'boolean';
  return <article className="dev-catalog-card"><header className="dev-catalog-heading"><span className="dev-card-icon"><Icon name={info.icon} /></span><div><h4>{info.label}</h4><code>{tool.name}</code></div><Badge tone={available ? 'success' : known ? 'warning' : 'muted'}>{available ? '当前可用' : known ? '当前不可用' : '状态未提供'}</Badge></header>
    <p className="dev-card-description">{tool.description || '工具说明未提供。'}</p>
    <div className="dev-tags"><Badge>{({ read: '只读查询', write: '状态写入', mutation: '状态写入', confirm: '用户确认' } as Data)[tool.effect] || tool.effect || '作用未提供'}</Badge>{tool.auto_display && <Badge>自动展示结果</Badge>}{Array.isArray(tool.allowed_stages) ? tool.allowed_stages.map((stage: string) => <Badge key={stage}>{stageLabels[stage] || stage}</Badge>) : <Badge>不限阶段</Badge>}</div>
    <Conditions label="当前阻碍" items={tool.blockers} />
    <Disclosure title={`参数与约束 · ${Object.keys(tool.parameters?.properties || {}).length} 个参数`}>
      <Parameters schema={tool.parameters} /><Conditions label="前置条件" items={tool.preconditions} /><Conditions label="产生的变化" items={tool.side_effects} /><Disclosure title="查看原始参数定义"><Content value={tool.parameters} label="展开 JSON" /></Disclosure>
    </Disclosure>
  </article>;
}

export function ToolsCatalog({ tools = [] }: { tools?: Data[] }) {
  const id = useId(), [search, setSearch] = useState(''), [availability, setAvailability] = useState('all');
  const query = search.trim().toLocaleLowerCase(), filtered = tools.filter(tool => {
    const info = toolInfo(tool.name);
    return (availability === 'all' || (availability === 'available' ? tool.available === true : tool.available === false)) && `${info.label} ${tool.name} ${tool.description || ''} ${info.group}`.toLocaleLowerCase().includes(query);
  });
  const groups = new Map<string, Data[]>();
  for (const tool of filtered) { const group = toolInfo(tool.name).group; groups.set(group, [...(groups.get(group) || []), tool]); }
  return <div className="dev-catalog"><div className="dev-search"><Icon name="search" /><label className="visually-hidden" htmlFor={id}>搜索工具</label><input id={id} type="search" value={search} placeholder="搜索工具名称、功能…" onChange={event => setSearch(event.target.value)} /></div>
    <div className="dev-filter-row" aria-label="工具可用状态">{[['all', '全部'], ['available', '可用'], ['blocked', '不可用']].map(([value, label]) => <button key={value} type="button" aria-pressed={availability === value} onClick={() => setAvailability(value)}>{label}{value === 'all' ? ` ${tools.length}` : ''}</button>)}<span>{filtered.length} 项</span></div>
    {!filtered.length && <Empty>{tools.length ? '没有匹配的工具，试试其他关键词。' : '当前没有提供工具目录。'}</Empty>}
    {[...groups].map(([group, items]) => <section className="dev-catalog-group" key={group}><h3>{group}<small>{items.length}</small></h3><div className="dev-catalog-grid">{items.map(tool => <ToolCard key={tool.name} tool={tool} />)}</div></section>)}
  </div>;
}

export function ComponentsCatalog({ components = [] }: { components?: Data[] }) {
  const [search, setSearch] = useState(''), id = useId();
  const filtered = components.filter(component => `${component.name} ${component.id} ${component.description}`.toLowerCase().includes(search.trim().toLowerCase()));
  return <div className="dev-catalog"><div className="dev-search"><Icon name="search" /><label className="visually-hidden" htmlFor={id}>搜索组件</label><input id={id} type="search" value={search} placeholder="搜索组件…" onChange={event => setSearch(event.target.value)} /></div>
    {!filtered.length && <Empty>没有匹配的系统组件。</Empty>}
    <div className="dev-catalog-grid">{filtered.map(component => <article className="dev-catalog-card" key={component.id}>
      <header className="dev-catalog-heading"><span className="dev-card-icon"><Icon name={componentIcons[component.id] || 'layers'} /></span><div><h4>{component.name || component.id}</h4><code>{component.id}{component.version ? ` · v${component.version}` : ''}</code></div><Badge tone={component.enabled === true ? 'success' : 'muted'}>{component.enabled === true ? '已启用' : component.enabled === false ? '未启用' : '启用状态未知'}</Badge></header>
      <p className="dev-card-description">{component.description}</p>
      <div className="dev-dependencies"><h5><Icon name="plug" />服务依赖</h5><Badge tone={component.available === true ? 'success' : component.available === false ? 'warning' : 'muted'}>{component.available === true ? '依赖就绪' : component.available === false ? '依赖不可用' : '依赖状态未提供'}</Badge><div className="dev-tags">{(component.dependencies || []).map((name: string) => <span key={name} title={name}>{dependencyLabels[name] || name}</span>)}{!component.dependencies?.length && <span>无额外依赖</span>}</div></div>
      <div className="dev-tags">{(component.ui?.kinds || []).map((kind: string) => <Badge key={kind}>{kindLabels[kind] || kind}</Badge>)}{(component.stage_targets || []).map((stage: string) => <Badge key={stage}>{stageLabels[stage] || stage}</Badge>)}</div>
      <Disclosure title={`提供 ${component.tools?.length || 0} 个工具`}><ul className="dev-component-tools">{(component.tools || []).map((name: string) => <li key={name}><Icon name={toolInfo(name).icon} /><span>{toolInfo(name).label}<code>{name}</code></span></li>)}</ul>{component.instructions?.entrypoint && <p className="dev-muted">指南按需加载 · <code>{component.instructions.entrypoint}</code></p>}</Disclosure>
    </article>)}</div>
  </div>;
}
