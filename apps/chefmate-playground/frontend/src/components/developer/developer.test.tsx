import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { api } from '../../runtime/http';
import { Diagnostics } from './Diagnostics';
import { ExecutionTrace } from './ExecutionTrace';
import { TraceExplorer } from './Trace';
import { ComponentsCatalog, ToolsCatalog } from './Catalog';
import { UsageSummary } from './Primitives';
import { serialized, summary } from './model';

vi.mock('../../runtime/http', async importOriginal => ({ ...await importOriginal<typeof import('../../runtime/http')>(), api: vi.fn() }));
const request = vi.mocked(api);
const catalog = { tools: [{ name: 'search_recipes', description: '按食材和时间筛选真实菜谱', available: true, effect: 'read', parameters: { properties: { query: { type: 'string', description: '食材名称' } }, required: ['query'] } }, { name: 'set_meal_plan', description: '安排已选菜品', available: false, blockers: ['请先选择菜品'], allowed_stages: ['planning'] }], components: [{ id: 'meal-planning', name: '选菜与餐单', enabled: true, available: false, description: '用食材安排整餐', dependencies: ['retrieval'], tools: ['search_recipes'], ui: { kinds: ['meal_plan'] } }], stage: 'planning' };
const trace = { round: 1, stage: 'planning', system: '你是厨房助手，按服务器状态完成任务。', messages: [{ role: 'user', content: '生菜怎么做\n服务器状态', user_text: '生菜怎么做', ui_context: { stage: 'planning' } }, { role: 'assistant', content: '', tool_calls: [{ id: 'call-1', name: 'search_recipes', arguments: { ingredients: ['生菜'] } }] }, { role: 'tool', name: 'search_recipes', tool_call_id: 'call-1', result: { status: 'ok', recipes: [{ name: '蚝油生菜' }] }, content: '' }], tools: catalog.tools, native_images_count: 0 };
const capture = { turn_id: 'latest', status: 'completed', message: '生菜怎么做', debug_trace: [trace, { ...trace, round: 2, system: '第二次真实系统提示' }], tool_calls: [{ name: 'search_recipes', arguments: { ingredients: ['生菜'] }, result: { status: 'ok', recipes: [{ name: '蚝油生菜' }] } }], usage: { model_calls: 2, input_tokens: null, output_tokens: 12, cached_tokens: null, total_tokens: null, known_totals: { input_tokens: 120 }, coverage: { input_tokens: { reported: 1, calls: 2 } }, calls: [{ round: 1, input_tokens: 120, output_tokens: 8, total_tokens: 128, total_tokens_source: 'input_output_sum' }] }, context_usage: { round: 2, estimated_tokens: 200, categories: [{ key: 'system', label: '系统提示词', estimated_tokens: 30 }] } };
const response = { ...catalog, capture, runs: [{ turn_id: 'latest', message: '最近一轮', status: 'completed' }, { turn_id: 'older', message: '较早一轮', status: 'error' }], logs: [], retained_turns: 20 };
beforeEach(() => { request.mockReset(); });

describe('developer diagnostics', () => {
  it('does not render or request diagnostics when developer mode is off', () => {
    const view = render(<Diagnostics developer={false} sessionId="one" status={null} />);
    expect(view.container).toBeEmptyDOMElement(); expect(request).not.toHaveBeenCalled();
  });

  it('supports keyboard tab navigation with one tab stop and a named content panel', async () => {
    request.mockResolvedValue(response); render(<Diagnostics developer sessionId="keyboard" status={null} />);
    const connection = screen.getByRole('tab', { name: '连接' }); connection.focus(); fireEvent.keyDown(connection, { key: 'ArrowRight' });
    const tools = screen.getByRole('tab', { name: '工具' }); expect(document.activeElement).toBe(tools); expect(tools).toHaveAttribute('tabindex', '0'); expect(connection).toHaveAttribute('tabindex', '-1');
    expect(screen.getByRole('tabpanel', { name: '工具' })).toBeInTheDocument(); await screen.findByText('搜索菜谱');
  });

  it('searches Chinese tool cards and distinguishes component enablement from unavailable dependencies', () => {
    const view = render(<ToolsCatalog tools={catalog.tools} />);
    expect(screen.getByText('搜索菜谱')).toBeInTheDocument(); expect(screen.getByText('当前不可用')).toBeInTheDocument();
    fireEvent.change(screen.getByRole('searchbox', { name: '搜索工具' }), { target: { value: '生菜不存在' } });
    expect(screen.queryByText('搜索菜谱')).not.toBeInTheDocument(); expect(screen.getByText('没有匹配的工具，试试其他关键词。')).toBeInTheDocument();
    fireEvent.change(screen.getByRole('searchbox', { name: '搜索工具' }), { target: { value: '' } }); fireEvent.click(screen.getByRole('button', { name: '不可用' }));
    expect(screen.getByText('安排餐单')).toBeInTheDocument(); expect(screen.queryByText('搜索菜谱')).not.toBeInTheDocument();
    view.rerender(<ComponentsCatalog components={catalog.components} />);
    expect(screen.getByText('已启用')).toBeInTheDocument(); expect(screen.getByText('依赖不可用')).toBeInTheDocument(); expect(screen.getByText('菜谱检索服务')).toBeInTheDocument();
  });

  it('uses the shared turn selector and rejects late responses from the previously selected session', async () => {
    let resolveOld!: (value: any) => void;
    request.mockImplementation(path => path.includes('/first/') ? new Promise(resolve => { resolveOld = resolve; }) : Promise.resolve(response) as any);
    const view = render(<Diagnostics developer sessionId="first" status={null} />);
    fireEvent.click(screen.getByRole('tab', { name: '请求轨迹' })); expect(request).toHaveBeenCalledWith('/api/sessions/first/debug', expect.objectContaining({ headers: { 'X-ChefMate-Developer': '1' } }));
    const oldSignal = request.mock.calls[0][1]?.signal;
    view.rerender(<Diagnostics developer sessionId="second" status={null} />);
    await screen.findByRole('combobox', { name: '查看调试轮次' }); expect(oldSignal?.aborted).toBe(true);
    await act(async () => resolveOld({ ...response, capture: { ...capture, debug_trace: [{ ...trace, system: '过期的第一会话系统提示' }] } }));
    expect(view.container).not.toHaveTextContent('过期的第一会话系统提示');
    fireEvent.click(screen.getByRole('combobox', { name: '查看调试轮次' })); fireEvent.click(screen.getByRole('option', { name: /较早一轮/ }));
    await waitFor(() => expect(request).toHaveBeenCalledWith('/api/sessions/second/debug?turn_id=older', expect.anything()));
  });

  it('renders captured roles, request rounds and call links, without inventing missing estimates', async () => {
    render(<TraceExplorer capture={capture} />);
    expect(screen.getByText('用户输入')).toBeInTheDocument(); expect(screen.getByText('助手消息')).toBeInTheDocument(); expect(screen.getByText('工具结果')).toBeInTheDocument();
    expect(screen.getByText('生菜怎么做')).toBeInTheDocument(); expect(screen.getByText(/对应调用/)).toHaveTextContent('call-1');
    expect(screen.getByText('按输入与输出相加', { exact: false })).toBeInTheDocument(); expect(screen.queryByText('≈ 200 tokens')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('combobox', { name: '选择模型请求' })); fireEvent.click(screen.getByRole('option', { name: /第 2 次请求/ }));
    expect(screen.getByText('第二次真实系统提示')).toBeInTheDocument(); expect(screen.getByText('≈ 200 tokens · 第 2 次请求')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '树视图' })); expect(screen.getByRole('button', { name: '树视图' })).toHaveAttribute('aria-pressed', 'true');
    const usage = screen.getByRole('region', { name: '这次请求用量' }); expect(within(usage).getByText('请求大小粗估')).toBeInTheDocument();
  });

  it('keeps legacy logs readable while stating that complete model inputs were not retained', async () => {
    request.mockResolvedValue({ ...catalog, runs: [], capture: null, logs: [{ id: 'legacy', text: '旧的公开回复', tool_calls: [] }] });
    render(<Diagnostics developer sessionId="legacy" status={null} />);
    fireEvent.click(screen.getByRole('tab', { name: '执行记录' })); await screen.findByText('旧的公开回复');
    expect(screen.getByText('仅有历史公开记录；完整模型输入未留存。')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('tab', { name: '请求轨迹' })); expect(screen.getByText(/这轮没有留存完整模型请求/)).toBeInTheDocument();
  });

  it('never exposes private reasoning and distinguishes incomplete reported usage from request estimates', () => {
    expect(serialized({ reasoning_content: '隐藏推理', nested: '{"reasoning":"秘密思考","message":"可显示"}', api_key: 'secret' })).not.toMatch(/隐藏推理|秘密思考|secret/);
    expect(summary({ ingredients: ['生菜'], servings: 2 })).toBe('食材：生菜 · 份量：2');
    render(<UsageSummary usage={capture.usage} contextUsage={capture.context_usage} />);
    expect(screen.getByText('已知 120，非完整统计')).toBeInTheDocument(); expect(screen.getByText('估算值')).toBeInTheDocument(); expect(screen.getByText('报告用量')).toBeInTheDocument();
  });

  it('shows an inline public tool timeline only to developers and defers raw input details', async () => {
    const message = { role: 'assistant' as const, content: '可以做生菜', tool_calls: [{ name: 'search_recipes', arguments: { private_fixture_field: '完整参数标记' }, result: { status: 'ok', recipes: [{ name: '生菜' }] } }], process: ['绝不能显示的思维'], usage: { input_tokens: 33 } };
    const view = render(<ExecutionTrace message={message} developer={false} />); expect(view.container).toBeEmptyDOMElement();
    view.rerender(<ExecutionTrace message={message} developer />);
    expect(screen.getByText('搜索菜谱')).toBeInTheDocument(); expect(view.container).not.toHaveTextContent('绝不能显示的思维');
    expect(view.container.querySelector('.dev-content pre')).toBeNull();
    fireEvent.click(screen.getByText('展开完整参数'));
    await waitFor(() => expect(view.container.querySelector('.dev-content pre')).toHaveTextContent('完整参数标记'));
  });
});
