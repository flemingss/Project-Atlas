/**
 * Status bar displayed at the bottom of the editor page.
 * Shows connection state, document stats, and model info.
 */
import { useEditorStore } from '@/stores/editor-store';
import { cn } from '@/lib/utils';

interface StatusBarProps {
  charCount: number;
  lineCount: number;
}

export function StatusBar({ charCount, lineCount }: StatusBarProps) {
  const { status, statusText, lastModel } = useEditorStore();

  return (
    <div className="flex shrink-0 items-center gap-4 border-t border-border bg-bg-secondary px-3 py-1 text-[11px] text-text-muted">
      <div className="flex items-center gap-1.5">
        <div
          className={cn(
            'size-1.5 rounded-full',
            status === 'idle' && 'bg-success',
            status === 'busy' && 'animate-pulse bg-warning',
            status === 'error' && 'bg-error',
          )}
        />
        <span>{statusText}</span>
      </div>
      <span>Lines: {lineCount}</span>
      <span>Chars: {charCount}</span>
      {lastModel && <span className="ml-auto">Model: {lastModel}</span>}
    </div>
  );
}
