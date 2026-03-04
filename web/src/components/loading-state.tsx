/**
 * LoadingState — Consistent loading indicator for pages and panels.
 *
 * Usage:
 *   <LoadingState />                        // Full centered spinner
 *   <LoadingState label="Loading docs…" />  // With label
 *   <LoadingState inline />                 // Inline small spinner
 */
import { Loader2 } from 'lucide-react';
import { cn } from '@/lib/utils';

interface LoadingStateProps {
  /** Optional label displayed below the spinner */
  label?: string;
  /** If true, renders inline instead of centered */
  inline?: boolean;
  /** Extra className */
  className?: string;
}

export function LoadingState({ label, inline, className }: LoadingStateProps) {
  if (inline) {
    return (
      <span className={cn('inline-flex items-center gap-1.5 text-text-secondary', className)}>
        <Loader2 className="size-3.5 animate-spin" />
        {label && <span className="text-xs">{label}</span>}
      </span>
    );
  }

  return (
    <div className={cn('flex flex-1 flex-col items-center justify-center gap-3 py-12', className)}>
      <Loader2 className="size-8 animate-spin text-accent" />
      {label && <p className="text-sm text-text-secondary">{label}</p>}
    </div>
  );
}
