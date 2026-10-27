import {useEffect, useState} from 'react';
import {Dialog} from '../../components/Dialog';

export function useAccountLeaveGuard(dirty: boolean, busy: boolean, blocked: (message: string) => void, currentPage?: string) {
  const [pendingLeave, setPendingLeave] = useState<(() => void) | null>(null);
  useEffect(() => {
    const unload = (event: BeforeUnloadEvent) => {if (dirty || busy) {event.preventDefault(); event.returnValue = '';}};
    const navigate = (event: Event) => {
      const navigation = event as CustomEvent<{page: string; resume?: () => void}>;
      if ((!dirty && !busy) || currentPage && navigation.detail?.page === currentPage) return;
      event.preventDefault();
      if (busy) blocked('正在保存，完成后就可以离开。');
      else if (navigation.detail?.resume) setPendingLeave(() => navigation.detail.resume!);
    };
    window.addEventListener('beforeunload', unload); window.addEventListener('chefmate:before-page-change', navigate);
    return () => {window.removeEventListener('beforeunload', unload); window.removeEventListener('chefmate:before-page-change', navigate);};
  }, [dirty, busy, blocked, currentPage]);
  return {pendingLeave, setPendingLeave};
}

export function AccountLeavePrompt({action, onCancel}: {action: (() => void) | null; onCancel: () => void}) {
  if (!action) return null;
  return <Dialog title="保留未保存的修改？" onClose={onCancel}><div className="account-leave-prompt"><p>离开会放弃尚未保存的修改。可以继续编辑，或者放弃后离开。</p><div className="account-form-actions"><button className="secondary-button" type="button" onClick={onCancel}>继续编辑</button><button className="primary-button" type="button" onClick={() => {onCancel(); action();}}>放弃修改</button></div></div></Dialog>;
}
