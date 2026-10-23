import { useEffect, useRef, type ReactNode } from 'react';

export function Dialog({ title, children, onClose, className = '' }: {
  title: string; children: ReactNode; onClose: () => void; className?: string;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const dialog = ref.current!;
    const trigger = document.activeElement as HTMLElement | null;
    dialog.showModal();
    return () => { dialog.close(); trigger?.isConnected && trigger.focus(); };
  }, []);
  return <dialog ref={ref} className={`session-dialog ${className}`} aria-label={title}
    onCancel={event => { event.preventDefault(); onClose(); }}>
    <h2>{title}</h2>{children}
  </dialog>;
}
