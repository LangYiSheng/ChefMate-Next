import type { SVGProps } from 'react';

type Props = SVGProps<SVGSVGElement> & { name: string; size?: number };
export function Icon({ name, size = 20, className = '', ...props }: Props) {
  return <svg className={`icon ${className}`} width={size} height={size} viewBox="0 0 24 24"
    fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round"
    aria-hidden="true" focusable="false" {...props}>
    <use href={`/static/vendor/lucide/sprite.svg#${name}`} />
  </svg>;
}

export function Brand({ className = 'brand-icon' }: { className?: string }) {
  return <span className={`chefmate-mark ${className}`} aria-hidden="true" />;
}
