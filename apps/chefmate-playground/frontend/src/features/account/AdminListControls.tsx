import type {FormEvent, ReactNode} from 'react';
import {Icon} from '../../components/Icon';
import {Select} from '../../components/ui/Select';
import './admin-list.css';

/** Shared list controls; callers own local or server-side filtering. */
export function AdminListControls({label, query, onQueryChange, placeholder = '搜索账号或称呼', children, onApply, onClear}: {
  label: string; query: string; onQueryChange: (value: string) => void; placeholder?: string;
  children?: ReactNode; onApply?: (event: FormEvent) => void; onClear: () => void;
}) {
  return <form className="admin-list-controls" aria-label={`筛选${label}`} onSubmit={event => {event.preventDefault(); onApply?.(event);}}>
    <label className="admin-list-search">搜索{label}<span><Icon name="search"/><input type="search" value={query} maxLength={200} placeholder={placeholder} onChange={event => onQueryChange(event.target.value)}/></span></label>
    {children}
    <div className="admin-list-actions">{onApply && <button type="submit" className="primary-button"><Icon name="search"/>筛选</button>}<button type="button" className="secondary-button" onClick={onClear}>清空</button></div>
  </form>;
}

export function AdminPagination({label, page, pages, pageSize, onPage, onPageSize, disabled = false}: {
  label: string; page: number; pages: number; pageSize: number; onPage: (page: number) => void;
  onPageSize: (size: number) => void; disabled?: boolean;
}) {
  return <nav className="admin-list-pagination" aria-label={`${label}分页`}><label>每页<Select disabled={disabled} value={String(pageSize)} onValueChange={value => onPageSize(Number(value))} options={[20, 50, 100].map(value => ({value: String(value), label: `${value} 条`}))} aria-label="每页条数"/></label><div><button type="button" className="secondary-button" disabled={disabled || page <= 1} onClick={() => onPage(page - 1)}>上一页</button><span>第 {page} / {pages} 页</span><button type="button" className="secondary-button" disabled={disabled || page >= pages} onClick={() => onPage(page + 1)}>下一页</button></div></nav>;
}

export function paginateAdminRows<T>(items: T[], requestedPage: number, pageSize: number) {
  const pages = Math.max(1, Math.ceil(items.length / pageSize));
  const page = Math.min(Math.max(1, requestedPage), pages);
  return {items: items.slice((page - 1) * pageSize, page * pageSize), page, pages};
}
