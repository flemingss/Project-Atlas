/**
 * PageShell — Standard page wrapper providing consistent header area + scrollable content.
 *
 * Usage:
 *   <PageShell title="Documents" subtitle="Manage your corpus" actions={<Button>New</Button>}>
 *     <CardGrid>…</CardGrid>
 *   </PageShell>
 */
import * as React from 'react';
import { cn } from '@/lib/utils';
import { ScrollArea } from '@/components/ui/scroll-area';

export interface PageShellProps {
  /** Page title displayed in the header area */
  title?: string;
  /** Optional subtitle or description */
  subtitle?: string;
  /** Slot for action buttons rendered at the right of the header */
  actions?: React.ReactNode;
  /** Extra classes applied to the outer container */
  className?: string;
  /** Page content */
  children: React.ReactNode;
  /** If true, content scrolls within a ScrollArea; otherwise it fills and manages its own overflow */
  scrollable?: boolean;
}

export function PageShell({
  title,
  subtitle,
  actions,
  className,
  children,
  scrollable = true,
}: PageShellProps) {
  return (
    <div className="flex flex-1 flex-col overflow-hidden">
      {/* ── Page header (only if title provided) ── */}
      {title && (
        <div className="flex shrink-0 items-center justify-between border-b border-border px-6 py-4">
          <div>
            <h1 className="text-lg font-semibold text-text-primary">{title}</h1>
            {subtitle && <p className="mt-0.5 text-sm text-text-secondary">{subtitle}</p>}
          </div>
          {actions && <div className="flex items-center gap-2">{actions}</div>}
        </div>
      )}

      {/* ── Content ── */}
      {scrollable ? (
        <ScrollArea className="flex-1">
          <div className={cn('px-6 py-5', className)}>{children}</div>
        </ScrollArea>
      ) : (
        <div className={cn('flex flex-1 flex-col overflow-hidden', className)}>{children}</div>
      )}
    </div>
  );
}
