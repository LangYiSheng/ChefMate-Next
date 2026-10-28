import { useEffect, useRef, type ReactNode } from 'react';

export function Dialog({ title, children, onClose, className = '', role = 'dialog' }: {
  title: string; children: ReactNode; onClose: () => void; className?: string; role?: 'dialog' | 'alertdialog';
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const dialog = ref.current!;
    const trigger = document.activeElement as HTMLElement | null;
    dialog.showModal();
    return () => { dialog.close(); trigger?.isConnected && trigger.focus(); };
  }, []);
  return <dialog ref={ref} className={`session-dialog ${className}`} role={role} aria-label={title}
    onCancel={event => { event.preventDefault(); event.stopPropagation(); onClose(); }}>
    <h2>{title}</h2>{children}
  </dialog>;
}
