import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { api, HttpError } from '../runtime/http';
import { chefStore } from '../runtime/store';
import { workspaceStore } from '../features/workspace/store';
import { RecipeReviews, collectRecipeReviews } from './RecipeReview';

vi.mock('../runtime/http', async importOriginal => ({ ...await importOriginal<typeof import('../runtime/http')>(), api: vi.fn() }));
const request = vi.mocked(api), sessions: string[] = [];
const draft = { recipe_id: 'custom-eggs', revision: 1, status: 'draft', source_kind: 'text', source_text: '鸡蛋2个，锅热后倒入蛋液。', document: { name: '家常炒蛋', description: '用户提供的做法', servings: null, total_time_min: null, difficulty: null, ingredients: [{ canonical_name: '鸡蛋', raw_amount: '2', raw_unit: '个', grams: null }], steps: ['锅热后倒入蛋液，轻轻推动。'], warnings: ['原文没有写明整道菜总耗时。'], references: [{ title: '不安全来源', url: 'javascript:alert(1)' }] } };
const message = (record: any = draft) => ({ role: 'assistant', content: '请核对菜谱', tool_calls: [{ name: 'create_recipe_draft', arguments: { reasoning_content: '不显示的过程思维' }, result: { status: 'ok', draft: record, receipt: { reasoning: '也不应显示' } } }] });
function props(record: any = draft) { const sessionId = `recipe-review-${crypto.randomUUID()}`; sessions.push(sessionId); workspaceStore.receive(sessionId, { workspace: { revision: 1, pending_question: null }, views: [] }); return { message: message(record), sessionId, locked: false, onAsk: vi.fn(), ownedIds: new Set([record.recipe_id]) }; }
const loaded = () => waitFor(() => expect(screen.queryByText('正在读取菜谱当前版本…')).not.toBeInTheDocument());
beforeEach(() => { request.mockReset(); });
afterEach(() => { sessions.forEach(id => workspaceStore.remove(id)); sessions.length = 0; });

describe('chat recipe review', () => {
  it('folds at the header without discarding the current tab, loaded draft revision or confirmation controls', async () => {
    request.mockResolvedValue({ ...draft, revision: 3 });
    const view = render(<RecipeReviews {...props()} />); await loaded();
    fireEvent.click(screen.getByRole('tab', { name: '做法' }));
    const panel = view.container.querySelector('.recipe-review-body')!;
    const collapse = screen.getByRole('button', { name: '收起个人菜谱：家常炒蛋' });
    expect(collapse).toHaveAttribute('aria-expanded', 'true');
    fireEvent.click(collapse);
    expect(panel).not.toBeVisible();
    expect(screen.getByRole('heading', { name: '家常炒蛋' })).toBeVisible();
    expect(screen.queryByRole('button', { name: '确认保存到我的菜谱' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '展开个人菜谱：家常炒蛋' }));
    expect(panel).toBeVisible(); expect(view.container.querySelector('.recipe-review-body')).toBe(panel);
    expect(screen.getByRole('tab', { name: '做法' })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByText('个人菜谱 · 第 3 版')).toBeVisible();
    expect(screen.getByRole('button', { name: '确认保存到我的菜谱' })).toBeEnabled();
    expect(request).toHaveBeenCalledOnce();
  });

  it('keeps a late read failure visible even after the recipe card was folded', async () => {
    let reject!: (reason: Error) => void;
    request.mockImplementation(() => new Promise((_yes, no) => { reject = no; }));
    render(<RecipeReviews {...props()} />);
    fireEvent.click(screen.getByRole('button', { name: '收起个人菜谱：家常炒蛋' }));
    await act(async () => reject(new Error('暂时无法读取这份菜谱')));
    expect(screen.getByRole('alert')).toBeVisible();
    expect(screen.getByRole('alert')).toHaveTextContent('暂时无法读取这份菜谱');
    expect(screen.getByRole('button', { name: '展开个人菜谱：家常炒蛋' })).toHaveAttribute('aria-expanded', 'false');
  });

  it('collects highest revisions from successful recipe records and renders only the latest message owner', async () => {
    const second = { ...draft, recipe_id: 'custom-second' }, latest = { ...draft, revision: 4 };
    const source = { tool_calls: [
      { name: 'create_recipe_draft', result: { status: 'ok', draft } },
      { name: 'revise_recipe_draft', result: { status: 'ok', draft: latest } },
      { name: 'get_recipe_draft', result: { status: 'ok', draft: { ...draft, revision: 2 } } },
      { name: 'request_recipe_publication', result: { status: 'wait', receipt: { recipe_id: draft.recipe_id } } },
      { name: 'unrelated', result: { status: 'ok', draft: { ...draft, revision: 99 } } },
      { name: 'create_recipe_draft', result: { status: 'error', draft: { ...draft, revision: 100 } } },
      { name: 'get_recipe_draft', result: { status: 'ok', draft: second } },
    ] };
    expect(collectRecipeReviews(source).map(record => [record.id, record.revision])).toEqual([[draft.recipe_id, 4], [second.recipe_id, 1]]);
    request.mockResolvedValue(second); render(<RecipeReviews {...props(second)} message={source} />); await loaded();
    expect(screen.getAllByRole('region', { name: /个人菜谱审核/ })).toHaveLength(1); expect(request).toHaveBeenCalledExactlyOnceWith('/api/cookbook/custom-second', expect.anything());
  });

  it('reads authority before allowing confirmation, preserves unknown facts and raw units, and only drafts a modification request', async () => {
    let resolveRead!: (value: any) => void; request.mockImplementation(() => new Promise(resolve => { resolveRead = resolve; }) as any);
    const configuration = props(); const view = render(<RecipeReviews {...configuration} />);
    expect(screen.getByRole('button', { name: '确认保存到我的菜谱' })).toBeDisabled(); expect(screen.getAllByText('未知')).toHaveLength(3); expect(screen.getByText('2个')).toBeInTheDocument();
    await act(async () => resolveRead({ ...draft, revision: 3 }));
    expect(screen.getByRole('button', { name: '确认保存到我的菜谱' })).toBeEnabled();
    fireEvent.click(screen.getByRole('button', { name: '告诉助手怎么改' })); expect(configuration.onAsk).toHaveBeenCalledOnce(); expect(configuration.onAsk.mock.calls[0][0]).toContain('custom-eggs'); expect(configuration.onAsk.mock.calls[0][0]).toContain('第 3 版'); expect(request).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole('tab', { name: '做法' })); expect(screen.getByText('锅热后倒入蛋液，轻轻推动。')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('tab', { name: '来源与依据' })); expect(screen.getByText(draft.source_text)).toBeInTheDocument(); expect(screen.queryByRole('link', { name: /不安全来源/ })).not.toBeInTheDocument();
    expect(view.container).not.toHaveTextContent('不显示的过程思维'); expect(view.container).not.toHaveTextContent('也不应显示');
  });

  it('publishes exactly the reviewed authoritative revision and then replaces draft actions with cooking follow-up', async () => {
    request.mockResolvedValueOnce({ ...draft, revision: 5 }).mockResolvedValueOnce({ ...draft, revision: 6, status: 'saved' });
    const configuration = props(); render(<RecipeReviews {...configuration} />); await loaded(); fireEvent.click(screen.getByRole('button', { name: '确认保存到我的菜谱' }));
    await screen.findByText('已保存到我的菜谱。');
    expect(request).toHaveBeenNthCalledWith(2, '/api/cookbook/custom-eggs/publish', expect.objectContaining({ method: 'POST', body: JSON.stringify({ revision: 5 }) }));
    expect(screen.queryByRole('button', { name: '确认保存到我的菜谱' })).not.toBeInTheDocument(); expect(screen.queryByRole('button', { name: '告诉助手怎么改' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '聊聊怎么做' })); expect(configuration.onAsk.mock.calls[0][0]).toContain('custom-eggs'); expect(request).toHaveBeenCalledTimes(2);
  });

  it.each([403, 409])('keeps the reviewed record after HTTP %i and requires an explicit reread before another save', async status => {
    request.mockResolvedValueOnce(draft).mockRejectedValueOnce(new HttpError(status === 403 ? '请求被拒绝。' : '草稿已更新，请重新核对。', status, {})).mockResolvedValueOnce({ ...draft, revision: 2, document: { ...draft.document, name: '服务端新版本' } });
    render(<RecipeReviews {...props()} />); await loaded(); fireEvent.click(screen.getByRole('button', { name: '确认保存到我的菜谱' }));
    await screen.findByRole('alert'); expect(screen.getByRole('heading', { name: '家常炒蛋' })).toBeInTheDocument(); expect(screen.getByText('2个')).toBeInTheDocument(); expect(screen.getByRole('button', { name: '确认保存到我的菜谱' })).toBeDisabled(); expect(request).toHaveBeenCalledTimes(2);
    fireEvent.click(screen.getByRole('button', { name: '重新读取菜谱' })); await screen.findByRole('heading', { name: '服务端新版本' });
    expect(screen.getByRole('button', { name: '确认保存到我的菜谱' })).toBeEnabled(); expect(request).toHaveBeenCalledTimes(3); expect(request.mock.calls.filter(([, options]) => options?.method === 'POST')).toHaveLength(1);
  });

  it('retains receipt content after a read failure and supports explicit reading recovery without posting', async () => {
    request.mockRejectedValueOnce(new Error('离线了')).mockResolvedValueOnce(draft);
    render(<RecipeReviews {...props()} />); await screen.findByRole('alert'); expect(screen.getByRole('heading', { name: '家常炒蛋' })).toBeInTheDocument(); expect(screen.getByRole('button', { name: '确认保存到我的菜谱' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: '重新读取菜谱' })); await loaded(); expect(screen.getByRole('button', { name: '确认保存到我的菜谱' })).toBeEnabled(); expect(request.mock.calls.every(([, options]) => !options?.method)).toBe(true);
  });

  it('blocks writes while locked, hides competing publication during a pending question, and reports deleted records', async () => {
    request.mockResolvedValue(draft); const configuration = props(); const view = render(<RecipeReviews {...configuration} locked />); await loaded();
    expect(screen.getByRole('button', { name: '确认保存到我的菜谱' })).toBeDisabled(); expect(screen.getByRole('button', { name: '告诉助手怎么改' })).toBeDisabled();
    act(() => workspaceStore.receive(configuration.sessionId, { workspace: { revision: 1, pending_question: { id: 'publish-question' } }, views: [] }));
    view.rerender(<RecipeReviews {...configuration} />); expect(screen.queryByRole('button', { name: '确认保存到我的菜谱' })).not.toBeInTheDocument(); expect(screen.getByText(/请先完成下方的确认问题/)).toBeInTheDocument();
    const deleted = { ...draft, revision: 2, status: 'deleted' }; request.mockResolvedValue(deleted); view.rerender(<RecipeReviews {...configuration} message={message(deleted)} />); await loaded();
    expect(screen.getByText('这份个人菜谱已删除，不再可用。')).toBeInTheDocument(); expect(screen.queryByRole('button', { name: '告诉助手怎么改' })).not.toBeInTheDocument(); expect(request.mock.calls.every(([, options]) => !options?.method)).toBe(true);
  });

  it('isolates late GET and publication responses after changing sessions', async () => {
    let resolveOldRead!: (value: any) => void, resolveOldPublish!: (value: any) => void;
    const configuration = props(), nextSession = props().sessionId;
    request.mockImplementationOnce(() => new Promise(resolve => { resolveOldRead = resolve; }) as any).mockResolvedValueOnce({ ...draft, document: { ...draft.document, name: '新会话菜谱' } });
    const view = render(<RecipeReviews {...configuration} />), readSignal = request.mock.calls[0][1]?.signal;
    view.rerender(<RecipeReviews {...configuration} sessionId={nextSession} />); await loaded(); expect(readSignal?.aborted).toBe(true);
    await act(async () => resolveOldRead({ ...draft, document: { ...draft.document, name: '过期读取' } })); expect(screen.queryByRole('heading', { name: '过期读取' })).not.toBeInTheDocument();
    request.mockImplementationOnce(() => new Promise(resolve => { resolveOldPublish = resolve; }) as any).mockResolvedValueOnce(draft);
    fireEvent.click(screen.getByRole('button', { name: '确认保存到我的菜谱' })); const postSignal = request.mock.calls.at(-1)?.[1]?.signal;
    view.rerender(<RecipeReviews {...configuration} />); await loaded(); expect(postSignal?.aborted).toBe(true);
    await act(async () => resolveOldPublish({ ...draft, revision: 2, status: 'saved' }));
    expect(screen.queryByText('已保存到我的菜谱。')).not.toBeInTheDocument(); expect(screen.getByRole('button', { name: '确认保存到我的菜谱' })).toBeEnabled();
  });

  it('hides direct saving for a canonical question before workspace refresh and rechecks it at click time', async () => {
    request.mockResolvedValue(draft); const configuration = props(), initial = chefStore.getSnapshot();
    let runtime = initial; vi.spyOn(chefStore, 'getSnapshot').mockImplementation(() => runtime);
    const view = render(<RecipeReviews {...configuration} />); await loaded();
    const save = screen.getByRole('button', { name: '确认保存到我的菜谱' }); expect(save).toBeEnabled();
    // Simulate the canonical GET completing before workspace and before React has committed a rerender.
    runtime = { ...initial, currentId: configuration.sessionId, session: { id: configuration.sessionId, title: '本轮', messages: [], pending_question: { id: 'canonical-question' }, awaiting_user: true }, cache: { ...initial.cache, [configuration.sessionId]: { id: configuration.sessionId, title: '本轮', messages: [], pending_question: { id: 'canonical-question' }, awaiting_user: true } } };
    fireEvent.click(save); expect(request).toHaveBeenCalledTimes(1);
    view.rerender(<RecipeReviews {...configuration} />);
    expect(screen.queryByRole('button', { name: '确认保存到我的菜谱' })).not.toBeInTheDocument(); expect(screen.getByText(/请先完成下方的确认问题/)).toBeInTheDocument();
  });

  it('ignores an answered canonical question but keeps queued continuation and loading states locked', async () => {
    request.mockResolvedValue(draft); const configuration = props(), initial = chefStore.getSnapshot();
    workspaceStore.receive(configuration.sessionId, { workspace: { revision: 2, pending_question: null, last_answer: { question_id: 'answered-question' } }, views: [] });
    let runtime = { ...initial, cache: { ...initial.cache, [configuration.sessionId]: { id: configuration.sessionId, title: '旧确认', messages: [], pending_question: { id: 'answered-question' }, awaiting_user: true } } };
    vi.spyOn(chefStore, 'getSnapshot').mockImplementation(() => runtime);
    const view = render(<RecipeReviews {...configuration} />); await loaded(); expect(screen.getByRole('button', { name: '确认保存到我的菜谱' })).toBeEnabled();
    runtime = { ...runtime, continuations: { ...runtime.continuations, [configuration.sessionId]: { stage: 'queued', requestId: 'answer-question', message: '继续修改草稿' } } };
    view.rerender(<RecipeReviews {...configuration} />); expect(screen.getByRole('button', { name: '确认保存到我的菜谱' })).toBeDisabled(); expect(screen.getByText('会话确认状态尚未就绪，完成确认或重新读取后再保存。')).toBeInTheDocument();
    runtime = { ...runtime, continuations: {} }; workspaceStore.entry(configuration.sessionId).loading = true;
    view.rerender(<RecipeReviews {...configuration} />); expect(screen.getByRole('button', { name: '确认保存到我的菜谱' })).toBeDisabled();
    workspaceStore.entry(configuration.sessionId).loading = false; view.rerender(<RecipeReviews {...configuration} />);
    expect(screen.getByRole('button', { name: '确认保存到我的菜谱' })).toBeEnabled(); expect(request).toHaveBeenCalledTimes(1);
  });

  it('keeps an uninitialized workspace locked and explicitly rereads confirmation state without sending a task', async () => {
    request.mockResolvedValue(draft); const configuration = props(); configuration.sessionId = `unloaded-${crypto.randomUUID()}`; sessions.push(configuration.sessionId);
    const loadWorkspace = vi.spyOn(workspaceStore, 'load').mockImplementation(async id => { workspaceStore.receive(id, { workspace: { revision: 1, pending_question: null }, views: [] }); return workspaceStore.entry(id).snapshot; });
    render(<RecipeReviews {...configuration} />); await loaded(); expect(screen.getByRole('button', { name: '确认保存到我的菜谱' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: '重新读取菜谱' })); await loaded();
    expect(loadWorkspace).toHaveBeenCalledWith(configuration.sessionId, true); expect(screen.getByRole('button', { name: '确认保存到我的菜谱' })).toBeEnabled(); expect(request.mock.calls.every(([, options]) => !options?.method)).toBe(true);
  });
});
